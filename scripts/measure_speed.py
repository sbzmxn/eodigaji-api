#!/usr/bin/env python3
"""
/recommend 서버 소요 시간 측정 (앱 로딩 화면 기준).

출발 좌표 7곳 × drive_minutes(기본 30·60) 마다 '연속 3회 체인'을 돌린다:
  1회차 추천 → 재추천(retry_context: 직전 후보, 실제 ETA = 서버 추정 × ETA_RATIO) → 한 번 더 재추천.
체인의 앞 1·2·3회 합계가 각각 "/recommend 1회", "연속 2회", "연속 3회"의 서버 총 소요 시간이다.

- 캐시 없음: 체인마다 새 캐시 폴더(관광지 목록·참조 데이터만 복사, 상세·요금 캐시 없음).
- 캐시 있음: 같은 캐시 폴더·같은 시드로 체인을 WARM_REPEATS 번 다시 돌린다.
서버를 프로세스 안에서 띄워 호출하므로 네트워크 왕복은 포함하지 않는다.

    python scripts/measure_speed.py [--drives 30,60] [--warm-repeats 3] [--max-api-calls 450]
결과: reports/speed.md (git 제외). 관광 API 호출 수를 함께 기록하고, 상한을 넘기 전에 멈춘다.
"""
import argparse
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from tests.conftest import LOCATIONS  # noqa: E402

ETA_RATIO = 1.5      # 재추천을 일으키는 '실제 ETA 가 추정보다 느린' 상황


def seed_cache(src: Path, dst: Path) -> None:
    for ns in ("index", "ref"):
        if (src / ns).exists():
            shutil.copytree(src / ns, dst / ns)


def run_chain(c: TestClient, loc: dict, drive: int) -> tuple[list[float], list[str]]:
    base = {"latitude": loc["lat"], "longitude": loc["lng"], "drive_minutes": drive, "keyword_ids": []}
    times, codes, prev, last = [], [], [], None
    for _ in range(3):
        body = dict(base)
        if last is not None:
            body["retry_context"] = {"previous_content_ids": prev,
                                     "observed_eta_seconds": max(1, round(last["estimated_drive_seconds"] * ETA_RATIO))}
        t = time.perf_counter()
        r = c.post("/api/v1/recommend", json=body)
        times.append((time.perf_counter() - t) * 1000)
        if r.status_code != 200:
            codes.append(r.json().get("error", {}).get("code", str(r.status_code)))
            break
        last = r.json()
        codes.append("OK")
        prev = prev + [last["reveal"]["content_id"]]
    return times, codes


def pct(vals: list[float], p: float) -> float:
    v = sorted(vals)
    k = (len(v) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drives", default="30,60")
    ap.add_argument("--warm-repeats", type=int, default=3)
    ap.add_argument("--max-api-calls", type=int, default=450)
    a = ap.parse_args()
    drives = [int(x) for x in a.drives.split(",")]
    base_settings = load_settings()
    src = base_settings.cache_dir
    tmp_root = Path(tempfile.mkdtemp(prefix="speed_"))
    samples = {"cold": {1: [], 2: [], 3: []}, "warm": {1: [], 2: [], 3: []}}
    per_call = {"cold": [], "warm": []}
    api = {"cold": 0, "warm": 0}
    fails = []
    stopped = None
    try:
        for i, loc in enumerate(LOCATIONS):
            for drive in drives:
                if api["cold"] + api["warm"] + 13 * 3 > a.max_api_calls:
                    stopped = f"{loc['name']} {drive}분 전에 상한({a.max_api_calls}회) 근접으로 중단"
                    break
                d = tmp_root / f"{i}_{drive}"
                seed_cache(src, d)
                for phase, reps in (("cold", 1), ("warm", a.warm_repeats)):
                    for _ in range(reps):
                        s = load_settings()
                        s.cache.dir = str(d)
                        s.random_seed = i * 1000 + drive
                        app = create_app(s)
                        hdr = {"X-API-Key": s.app_api_keys[0].get_secret_value()} if s.app_api_keys else {}
                        with TestClient(app, headers=hdr) as c:
                            times, codes = run_chain(c, loc, drive)
                        api[phase] += app.state.index.tour.calls      # 시작 시 목록 갱신 호출까지 포함
                        per_call[phase] += times
                        if codes[-1] != "OK":
                            fails.append(f"{phase} {loc['name']} {drive}분: {codes}")
                        for n in (1, 2, 3):
                            if len(times) >= n and all(x == "OK" for x in codes[:n]):
                                samples[phase][n].append(sum(times[:n]))
            if stopped:
                break
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    L = ["# /recommend 서버 소요 시간", "",
         f"_측정 {datetime.now():%Y-%m-%d %H:%M} · 출발 {len(LOCATIONS)}곳 × {drives}분 · 재추천은 실제 ETA = 추정 × {ETA_RATIO} 가정 · "
         f"프로세스 내 호출(네트워크 왕복 제외)_", "",
         "| 캐시 | 호출 | n | p50 (ms) | p95 (ms) | 최대 (ms) |", "|---|---|---|---|---|---|"]
    label = {1: "/recommend 1회", 2: "연속 2회 (추천+재추천)", 3: "연속 3회 (추천+재추천 2번)"}
    for phase, name in (("cold", "없음"), ("warm", "있음")):
        for n in (1, 2, 3):
            v = samples[phase][n]
            if v:
                L.append(f"| {name} | {label[n]} | {len(v)} | {pct(v, .5):.0f} | {pct(v, .95):.0f} | {max(v):.0f} |")
    L += ["", "1회 호출 단위 분포:", ""]
    for phase, name in (("cold", "없음"), ("warm", "있음")):
        v = per_call[phase]
        if v:
            L.append(f"- 캐시 {name}: n={len(v)}, p50 {pct(v, .5):.0f} ms, p95 {pct(v, .95):.0f} ms, "
                     f"최대 {max(v):.0f} ms, 평균 {statistics.mean(v):.0f} ms")
    L += ["", f"관광 API 호출: 캐시 없음 {api['cold']}회, 캐시 있음 {api['warm']}회, 합계 {api['cold'] + api['warm']}회", ""]
    if stopped:
        L.append(f"중단: {stopped}")
    L += [f"- 실패: {f}" for f in fails] or ["실패 없음"]
    out = ROOT / "reports" / "speed.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
