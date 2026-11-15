#!/usr/bin/env python3
"""
출발 좌표 7곳(tests/data/locations.json) × drive_minutes(15·30·60·120)에서
/keywords 와 /recommend(키워드 없이)가 정상 응답하는지 확인하고, 키워드별 후보 수 표를 만든다.
실제 서버 구성(create_app)을 프로세스 안에서 띄워 HTTP 로 호출한다. 가명은 settings 의 alias_mode 를 따른다.

    python scripts/check_locations.py
결과: reports/locations_check.md (폴더는 자동 생성, git 제외)
관광 API: 인덱스 캐시가 있으면 목록 0회, 추천 1회당 상세 최대 7회(content_id 캐시).
"""
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

LOCATIONS = json.loads((ROOT / "tests" / "data" / "locations.json").read_text(encoding="utf-8"))
DRIVES = [15, 30, 60, 120]


def main():
    app = create_app()
    rec = app.state.recommender
    rows, fails = [], []
    keys = rec.s.app_api_keys   # 검사가 켜져 있으면 첫 번째 키를 붙인다 (프로세스 안 호출이어도 미들웨어를 지남)
    headers = {"X-API-Key": keys[0].get_secret_value()} if keys else {}
    with TestClient(app, headers=headers) as c:
        kw_ids = rec.s.keywords.ids
        calls0 = rec.index.tour.calls if getattr(rec.index, "tour", None) else 0
        for loc in LOCATIONS:
            for m in DRIVES:
                q = {"lat": loc["lat"], "lng": loc["lng"], "drive_minutes": m}
                rk = c.get("/api/v1/keywords", params=q)
                counts = asyncio.run(rec.keywords_status(loc["lat"], loc["lng"], m))
                t = time.perf_counter()
                rr = c.post("/api/v1/recommend", json={"latitude": loc["lat"], "longitude": loc["lng"],
                                                       "drive_minutes": m, "keyword_ids": [], "exclude_content_ids": []})
                ms = int((time.perf_counter() - t) * 1000)
                ok = rk.status_code == 200 and rr.status_code == 200
                if not ok:
                    fails.append(f"{loc['name']} {m}분: keywords {rk.status_code}, recommend {rr.status_code} {rr.text[:120]}")
                enabled = {k["id"]: k["enabled"] for k in rk.json().get("keywords", [])}
                rj = rr.json()
                pick = rj.get("reveal", {}).get("real_name") or rj.get("error", {}).get("code")
                rows.append((loc["name"], m, counts["_n"], counts["_counts"], enabled, rk.status_code, rr.status_code,
                             pick, rj.get("blind_name", "-"), ms, counts["arrival_time"]))
        calls = (rec.index.tour.calls if getattr(rec.index, "tour", None) else 0) - calls0

    L = ["# 출발 좌표 7곳 × 운전 시간 확인", "",
         f"_측정 {datetime.now():%Y-%m-%d %H:%M} · 후보 수 = 반경 내·운전시간 이내·제외분류/섬 규칙 적용 후. "
         f"⚠ = 3개 미만(→ /keywords 비활성). 노을·야경 수는 분류 기준이고, 활성 여부는 도착 시각으로 따로 정해진다._", "",
         "| 출발 | 분 | 전체 | " + " | ".join(kw_ids) + " | /keywords | /recommend | 추천 결과(실명) | 가명 | ms |",
         "|" + "---|" * (len(kw_ids) + 8)]
    for name, m, n, cnt, en, sk, sr, pick, alias, ms, arr in rows:
        cells = []
        for k in kw_ids:
            v = cnt[k]
            mark = f"**⚠{v}**" if v < 3 else str(v)
            if k in ("sunset", "night") and not en.get(k):
                mark += "·off" if v >= 3 else ""
            cells.append(mark)
        L.append(f"| {name} | {m} | {n} | " + " | ".join(cells) + f" | {sk} | {sr} | {pick} | {alias} | {ms} |")
    L += ["", f"실패: **{len(fails)}건**", ""]
    L += [f"- {f}" for f in fails]
    L += ["", f"이번 실행의 관광 API 호출: {calls}회", ""]
    out = ROOT / "reports" / "locations_check.md"
    out.parent.mkdir(parents=True, exist_ok=True)   # reports/ 는 .gitignore 대상
    out.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
