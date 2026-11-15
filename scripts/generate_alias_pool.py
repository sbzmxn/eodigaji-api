#!/usr/bin/env python3
"""
가명 후보 생성 (한 번만 실행). 서버는 실행 중 LLM 을 쓰지 않고, 사람이 검수한 목록(config/alias_pool.yaml)만 쓴다.

    python scripts/generate_alias_pool.py [--model qwen2.5:7b] [--n 2000] [--exclude reports/alias_confirmed54.json]
결과: reports/alias_candidates.md (번호 붙은 전체 후보, 검토용, git 제외), reports/alias_candidates.json
검수를 마친 이름은 config/alias_pool.yaml 에 반영한다(테스트가 금지어 검증을 검사).
의존성: requirements.txt 의 httpx (서버와 공용) + 로컬 Ollama 와 생성 모델.
필요: Ollama(로컬), data/cache 의 관광지 인덱스·법정동 이름(서버를 한 번 띄우면 생긴다). 관광 API 는 부르지 않는다.

LLM 입력은 장소 정보 없이 '힐링'·무작위 느낌 단어뿐이다(시간대어는 금지어라 넣지 않는다).
검증: 한글·공백만, 5~20자, 같은 어절 반복 금지, 금지어(장소 유형어·신분류 이름·시·도·시·군·구는 부분 문자열,
      읍·면·동·리 이름과 어간은 어절 단위).
중복 제거: 완전 중복, 마지막 어절만 다른 이름, 글자 유사도 0.75 이상.
"""
import asyncio
import json
import random
import sys
import time
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.alias import (Lexicon, _strip_josa, build_region_words, build_type_words,  # noqa: E402
                       build_unit_words, validate)
from app.cache import DiskCache  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.spots import Spot  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
# 생성 전용 금지어: 도착 시각·날씨·풍경·장소를 떠올리게 하는 말 ('오늘'은 허용)
STRICT_SUBSTR = {"아침", "오전", "오후", "저녁", "새벽", "야간", "하늘", "햇살", "햇빛", "달빛", "별빛", "구름", "바람",
                 "커피", "음식", "그늘", "둥지", "나무", "오아시스", "모래", "텐트", "카페", "식당", "정류장", "골목",
                 "거리", "도시", "창가", "지붕", "텃밭", "여관", "호텔", "숙소", "캠핑", "놀이터", "학교", "시장", "극장",
                 "광장", "공항", "기차", "버스", "자정", "노을빛", "석양빛"}
STRICT_AFFIX = {"밤", "별"}                     # 한 글자: 어절 앞·뒤에 붙어도 금지 (오늘밤, 별들)
STRICT_TOKEN = {"비", "눈", "차", "낮", "잎", "집", "방", "길", "역"}   # 한 글자: 어절 전체가 같을 때만 (비밀·차분 허용)
EXTRA_SYSTEM = ("\n5) 시간대(아침·오전·낮·오후·저녁·밤·새벽·야간), 하늘·날씨(별·하늘·햇살·햇빛·달빛·구름·바람·비·눈), "
                "음식·음료(커피·차), 자연물(그늘·둥지·나무·잎·꽃·모래), 장소(카페·길·집·방·골목·도시 등)를 "
                "떠올리게 하는 단어도 쓰지 않는다. '오늘'은 써도 된다.")
OLLAMA_URL = "http://localhost:11434"
TEMPERATURE = 1.0
MOODS = ["설렘", "쉼", "고요", "다정함", "기대", "느긋함", "포근함", "여유", "위로", "두근거림", "새로움", "평온"]
SCHEMA = {"type": "object", "properties": {"blind_name": {"type": "string"}}, "required": ["blind_name"]}
SYSTEM_PROMPT = (
    "너는 '어디가지?' 드라이브 앱의 작명가다. 목적지를 숨긴 채 떠나는 드라이브에 붙일 짧은 한국어 이름을 하나 짓는다.\n"
    "규칙:\n"
    "1) 장소의 종류(바다, 강, 호수, 숲, 산, 공원, 절, 전망대, 계곡, 마을 등), 지명, 풍경, 날씨를 떠올리게 하는 단어는 절대 쓰지 않는다.\n"
    "2) '비밀의 공간', '오늘의 힐링 공간', '아직 이름 없는 쉼표'처럼 추상적이고 따뜻한 표현만 쓴다.\n"
    "3) 한글만 쓰고 5~20자로 짓는다. 숫자, 영어, 이모지, 따옴표는 쓰지 않는다.\n"
    '4) {"blind_name": "..."} 형식의 JSON 하나만 출력한다.'
)
SIMILARITY = 0.75


def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def admin_unit_words(cache: DiskCache) -> set[str]:
    """전국 관광지 주소에서 읍·면·동·리 이름과 어간 (서버와 같은 규칙)."""
    entry = cache.get_entry("index", "bulk")
    if not entry:
        sys.exit("data/cache 에 관광지 인덱스가 없습니다. 서버를 한 번 띄운 뒤 다시 실행하세요.")
    return build_unit_words(s for s in (Spot.from_item(i) for i in entry["value"]) if s)


def build_lexicon(s, cache: DiskCache) -> tuple[Lexicon, dict]:
    lcls = cache.get("ref", "lcls", None) or []
    regions = cache.get("ref", "ldong", None) or []
    lcls_names = {it.get(f"lclsSystm{lv}Cd"): it.get(f"lclsSystm{lv}Nm")
                  for it in lcls for lv in (1, 2, 3) if it.get(f"lclsSystm{lv}Cd")}
    region_names = {it[k] for it in regions for k in ("lDongRegnNm", "lDongSignguNm") if it.get(k)}
    type_words = build_type_words(s.alias, s.keywords, lcls_names)
    region_words = build_region_words(region_names)
    unit_words = admin_unit_words(cache)
    stats = {"장소 유형어": len(type_words), "시·도·시·군·구": len(region_words), "읍·면·동·리": len(unit_words)}
    stats["생성 전용"] = len(STRICT_SUBSTR) + len(STRICT_AFFIX) + len(STRICT_TOKEN)
    # 읍·면·동·리와 한 글자 생성 금지어는 어절 단위로만 검사 ('평온의'가 '온의동' 어간에 걸리는 오탐 방지)
    return Lexicon(type_words | region_words | STRICT_SUBSTR | STRICT_AFFIX, s.alias.allow_words,
                   unit_words | STRICT_TOKEN), stats


def prompt(mood: str, recent: list[str]) -> str:
    p = f"분위기: 힐링\n느낌 단어: {mood}\n"
    if recent:
        p += f"이미 쓴 이름(다시 쓰지 말 것): {', '.join(recent[-3:])}\n"
    return p + "새 이름을 지어줘."


async def ask(client, s, model, text) -> tuple[object, str]:
    r = await client.post(f"{OLLAMA_URL}/api/generate", timeout=60, json={
        "model": model, "system": SYSTEM_PROMPT + EXTRA_SYSTEM, "prompt": text, "format": SCHEMA, "stream": False,
        "keep_alive": "10m", "options": {"temperature": TEMPERATURE, "num_predict": 48}})
    r.raise_for_status()
    raw = r.json().get("response", "")
    return json.loads(raw).get("blind_name"), raw


def tokens(name: str) -> list[str]:
    return [_strip_josa(t) for t in name.split()]


def near_duplicate(name: str, kept: list[str]) -> str | None:
    tk = tokens(name)
    for k in kept:
        kt = tokens(k)
        if len(tk) >= 2 and len(tk) == len(kt) and tk[:-1] == kt[:-1]:
            return k                                   # 마지막 어절만 다름
        if SequenceMatcher(None, name.replace(" ", ""), k.replace(" ", "")).ratio() >= SIMILARITY:
            return k
    return None


async def main():
    s = load_settings()
    model = arg("--model", "qwen2.5:7b")
    n = int(arg("--n", "2000"))
    exclude = json.loads(Path(arg("--exclude", "")).read_text(encoding="utf-8")) if "--exclude" in sys.argv else []
    rng = random.Random(int(arg("--seed", "20261111")))
    cache = DiskCache(s.cache_dir)
    lex, lex_stats = build_lexicon(s, cache)
    moods = MOODS


    rows, fails, reasons = [], [], Counter()
    seen: set[str] = set(exclude)          # 이미 확정된 이름과 같거나 비슷하면 제외
    kept: list[str] = []
    reference = list(exclude)
    t0 = time.monotonic()
    async with httpx.AsyncClient() as client:
        await client.post(f"{OLLAMA_URL}/api/generate", json={"model": model, "keep_alive": "10m"}, timeout=120)
        t0 = time.monotonic()   # 모델 로딩 시간 제외
        for i in range(1, n + 1):
            mood = rng.choice(moods)
            try:
                value, raw = await ask(client, s, model, prompt(mood, kept))
            except (httpx.HTTPError, ValueError, AttributeError) as e:
                reasons["bad_json"] += 1
                fails.append((mood, f"<{type(e).__name__}>", "bad_json"))
                continue
            name, why = validate(value, lex, s.alias)
            if why:
                key = why.split(":")[0]
                reasons[key] += 1
                fails.append((mood, str(value)[:30], why))
                continue
            if name in seen:
                reasons["duplicate"] += 1
                fails.append((mood, name, "duplicate"))
                continue
            seen.add(name)
            similar = near_duplicate(name, reference + kept)
            if similar:
                reasons["similar"] += 1
                fails.append((mood, name, f"similar:{similar}"))
                continue
            kept.append(name)
            rows.append({"name": name, "mood": mood})
            if i % 50 == 0:
                print(f"  {i}/{n} 통과 {len(rows)}", flush=True)
    elapsed = time.monotonic() - t0

    out_dir = ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "alias_candidates.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    L = [f"# 가명 후보 ({model})", "",
         f"_생성 {datetime.now(KST):%Y-%m-%d %H:%M} · 시도 {n}회 · 제외 기준 {len(exclude)}개 · 소요 {elapsed:.0f}초 · "
         f"금지어 사전 {', '.join(f'{k} {v}' for k, v in lex_stats.items())}_", "",
         f"- 통과(중복·유사 제거 후): **{len(rows)}개**",
         "- 탈락 사유: " + ", ".join(f"`{k}` {v}" for k, v in reasons.most_common()), "",
         "## 통과 후보", "", "| # | 가명 | 느낌 단어 |", "|---|---|---|"]
    L += [f"| {i} | {r['name']} | {r['mood']} |" for i, r in enumerate(rows, 1)]
    L += ["", "## 탈락 (참고)", "", "| 느낌 단어 | 출력 | 사유 |", "|---|---|---|"]
    L += [f"| {b} | {v} | {w} |" for b, v, w in fails]
    (out_dir / "alias_candidates.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:6]))
    print(f"\n저장: {out_dir / 'alias_candidates.md'}")


if __name__ == "__main__":
    asyncio.run(main())
