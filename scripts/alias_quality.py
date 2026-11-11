#!/usr/bin/env python3
"""
단계 4: llama3.2:1b 가명 품질 측정 (판단 자료용, 모델 교체 안 함).
관광 API 는 부르지 않는다: 서버가 만든 data/cache 의 인덱스·참조 데이터만 쓴다.

    python scripts/alias_quality.py [샘플 수=20] [--model gemma3:4b]
결과: reports/alias_quality.md (기본 모델) / reports/alias_quality_<모델>.md (폴더는 자동 생성, git 제외)
"""
import asyncio
import httpx
import json
import random
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.alias import (SCHEMA, SYSTEM_PROMPT, AliasGenerator, build_region_words,  # noqa: E402
                       build_type_words, place_words, validate)
from app.cache import DiskCache  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.keywords import match_keywords  # noqa: E402
from app.scoring import radius_km  # noqa: E402
from app.spots import IndexManager, Spot, SpotIndex  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
COORDS = {l["name"]: (l["lat"], l["lng"]) for l in json.loads((ROOT / "tests" / "data" / "locations.json").read_text(encoding="utf-8"))}
COMBOS = [[], ["sea"], ["water"], ["forest"], ["walk"], ["view"], ["quiet"], ["forest", "walk"],
          ["water", "view"], ["quiet", "walk"], ["sea", "sunset"], ["view", "night"]]
HOURS = [9, 14, 18, 21]


class Recording(AliasGenerator):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.log = []
        self.errors = []

    async def _ask(self, prompt, timeout):
        t = time.monotonic()
        try:
            r = await self._client.post(
                f"{self.cfg.ollama_url}/api/generate",
                json={"model": self.cfg.model, "system": SYSTEM_PROMPT,
                      "prompt": prompt, "format": SCHEMA, "stream": False,
                      "keep_alive": "30m", "options": {"temperature": self.cfg.temperature, "num_predict": 48}},
                timeout=timeout)
            if r.status_code >= 400:
                self.errors.append(f"HTTP {r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            raw = r.json().get("response", "")
        except httpx.HTTPError as e:
            self.errors.append(f"{type(e).__name__}: {str(e)[:200]}")
            raise
        finally:
            self.log.append({"ms": int((time.monotonic() - t) * 1000)})
        self.log[-1]["raw"] = raw
        try:
            val = json.loads(raw).get("blind_name")
            self.log[-1]["json_ok"] = isinstance(val, str)
        except (ValueError, AttributeError):
            self.log[-1]["json_ok"] = False
            raise ValueError("bad json")
        self.log[-1]["value"] = val
        return val


def pick_spots(s, index: SpotIndex, n, rng):
    out = []
    i = 0
    while len(out) < n:
        place = list(COORDS)[i % 3]
        combo = COMBOS[i % len(COMBOS)]
        lat, lng = COORDS[place]
        near = index.within(lat, lng, radius_km(60, s.radius))
        base = [k for k in combo if not s.keywords.keywords[k].time_window] or combo
        cands = [sp for sp, _ in near if not combo or match_keywords(sp, base, s.keywords, s.scoring)]
        if cands:
            out.append((place, combo, rng.choice(cands)))
        i += 1
    return out


async def vram_mb(model):
    import httpx
    async with httpx.AsyncClient() as c:
        ps = (await c.get("http://localhost:11434/api/ps")).json().get("models", [])
    for m in ps:
        if m.get("name") == model:
            return int(m.get("size_vram", 0) / 2**20), int(m.get("size", 0) / 2**20)
    return None, None


async def main(n, model=None):
    s = load_settings()
    if model:
        s.alias.model = model
    cache = DiskCache(s.cache_dir)
    entry = cache.get_entry(IndexManager.INDEX_NS, IndexManager.INDEX_KEY)
    if not entry:
        sys.exit("data/cache 에 인덱스가 없습니다. 서버를 한 번 띄워 주세요.")
    index = SpotIndex([sp for sp in (Spot.from_item(i) for i in entry["value"]) if sp])
    lcls = cache.get("ref", "lcls", None) or []
    regions = cache.get("ref", "ldong", None) or []
    lcls_names = {it.get(f"lclsSystm{lv}Cd"): it.get(f"lclsSystm{lv}Nm") for it in lcls for lv in (1, 2, 3)
                  if it.get(f"lclsSystm{lv}Cd")}
    region_names = {it[k] for it in regions for k in ("lDongRegnNm", "lDongSignguNm") if it.get(k)}
    region_words = build_region_words(region_names)
    gen = Recording(s.alias, build_type_words(s.alias, s.keywords, lcls_names) | region_words, rng=random.Random(42))
    await gen.warm()

    rows, attempts = [], []
    for k, (place, combo, spot) in enumerate(pick_spots(s, index, n, random.Random(7))):
        arrival = datetime(2026, 11, 14, HOURS[k % 4], 10, tzinfo=KST)
        gen.log = []
        t = time.monotonic()
        res = await gen.generate(spot, arrival)
        ms = int((time.monotonic() - t) * 1000)
        pw = place_words(spot)
        for a in gen.log:
            v = a.get("value")
            a["leak_place"] = isinstance(v, str) and any(w in v.replace(" ", "") for w in pw)
            a["leak_region"] = isinstance(v, str) and any(w in v for w in region_words)
            a["verdict"] = validate(v, gen.lexicon_for(spot), s.alias)[1] if a.get("json_ok") else "bad_json"
        attempts += gen.log
        rows.append({"place": place, "combo": "+".join(combo) or "(없음)", "spot": spot.title,
                     "hour": HOURS[k % 4], "tries": [a.get("value") or a.get("raw", "")[:30] for a in gen.log],
                     "verdicts": [a["verdict"] or "ok" for a in gen.log], "final": res.name, "source": res.source,
                     "ms": ms})

    n_att = len(attempts)
    json_ok = sum(1 for a in attempts if a.get("json_ok"))
    leak_place = sum(1 for a in attempts if a.get("leak_place"))
    leak_region = sum(1 for a in attempts if a.get("leak_region"))
    passed = sum(1 for a in attempts if a.get("json_ok") and a["verdict"] is None)
    reasons = {}
    for a in attempts:
        if a["verdict"]:
            key = a["verdict"].split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
    llm_final = sum(1 for r in rows if r["source"] == "llm")
    req_ms = [r["ms"] for r in rows]
    att_ms = [a["ms"] for a in attempts]
    final_leak = sum(1 for r, (_, _, sp) in zip(rows, pick_spots(s, index, n, random.Random(7)))
                     if any(w in r["final"].replace(" ", "") for w in place_words(sp)))

    vram, total = await vram_mb(s.alias.model)
    natural = [r["final"] for r in rows if r["source"] == "llm"]
    L = [f"# 가명 품질 측정 ({s.alias.model})", "",
         f"_측정 {datetime.now(KST):%Y-%m-%d %H:%M} · 샘플 {len(rows)}건 · LLM 입력은 시간대+힐링+느낌 단어뿐(장소 정보 없음)_", "",
         "## 요약", "",
         f"| 항목 | 값 |", "|---|---|",
         f"| LLM 시도 수 | {n_att} (요청당 평균 {n_att / len(rows):.1f}) |",
         f"| JSON 유효율 (시도 기준) | {json_ok}/{n_att} = {100 * json_ok / n_att:.0f}% |",
         f"| 검증 통과율 (시도 기준) | {passed}/{n_att} = {100 * passed / n_att:.0f}% |",
         f"| LLM 원문 실명 조각 포함률 (시도 기준) | {leak_place}/{n_att} = {100 * leak_place / n_att:.0f}% |",
         f"| LLM 원문 지명 포함률 (시도 기준) | {leak_region}/{n_att} = {100 * leak_region / n_att:.0f}% |",
         f"| **최종 응답 실명 누출** | **{final_leak}/{len(rows)}** |",
         f"| 최종 alias_source=llm 비율 | {llm_final}/{len(rows)} = {100 * llm_final / len(rows):.0f}% |",
         f"| 가명 생성 지연 (요청당) | 평균 {statistics.mean(req_ms):.0f}ms · 최대 {max(req_ms)}ms |",
         f"| LLM 1회 호출 지연 | 평균 {statistics.mean(att_ms):.0f}ms · 중앙 {statistics.median(att_ms):.0f}ms · 최대 {max(att_ms)}ms |",
         f"| VRAM (ollama ps) | {vram} MiB (모델 전체 {total} MiB) |",
         "", "검증 실패 사유(시도 기준): " + (", ".join(f"`{k}` {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])) or "없음"),
         "", "## 샘플", "", "| # | 지역 | 키워드 | 장소(실명) | 도착시 | LLM 시도 → 판정 | 최종 | 출처 | ms |",
         "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        tries = "<br>".join(f"{t} → {v}" for t, v in zip(r["tries"], r["verdicts"]))
        L.append(f"| {i} | {r['place']} | {r['combo']} | {r['spot']} | {r['hour']}시 | {tries} | **{r['final']}** | "
                 f"{r['source']} | {r['ms']} |")
    L += ["", "## 채택된 LLM 가명", "", ", ".join(natural) or "-"]
    if gen.errors:
        L += ["", "## 호출 오류 (상위 3)", ""] + [f"- {e}" for e in gen.errors[:3]]
    tag = "" if not model else "_" + model.replace(":", "_").replace("/", "_")
    out = ROOT / "reports" / f"alias_quality{tag}.md"
    out.parent.mkdir(parents=True, exist_ok=True)   # reports/ 는 .gitignore 대상
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    await gen.aclose()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else None
    if model in args:
        args.remove(model)
    asyncio.run(main(int(args[0]) if args else 20, model))
