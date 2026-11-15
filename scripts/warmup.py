#!/usr/bin/env python3
"""
출발지 주변 상세·요금 캐시 예열 (사용 전).

출발지 × 운전시간 × 키워드 조합마다 1차 점수 상위 --top 곳(기본 10)을 골라 상세(2회)·요금(1회)을 미리 받아 둔다.
이미 캐시에 있는 곳은 건너뛴다. 캐시에 소개글이 있는 곳은 1차 점수에서 가산(info_overview)을 받으므로,
예열한 곳이 먼저 뽑혀 관광 API 호출이 거의 없어진다. 상세 캐시는 7일 유지되므로 서비스 시작 1~2일 전에 나눠 돌린다.

    python scripts/warmup.py --origin 37.4979,127.0276 --drives 30,60 --max-calls 300
    python scripts/warmup.py --origin 37.4979,127.0276 --max-calls 300 --dry-run   # 호출 없이 계획만

--max-calls 는 필수다. 관광 API 하루 한도(1,000회)에서 그날 이미 쓴 양을 빼고 정한다.
목록 인덱스가 하루 지나 갱신되면 그 호출(약 3회)도 상한에 포함된다.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.alias import AliasPool  # noqa: E402
from app.cache import DiskCache  # noqa: E402
from app.config import Settings, load_settings  # noqa: E402
from app.keywords import combined_keyword_score, match_keywords  # noqa: E402
from app.recommend import Recommender  # noqa: E402
from app.scoring import info_score, radius_km  # noqa: E402
from app.spots import DetailService, IndexManager, Spot  # noqa: E402
from app.tour_client import TourApiError, TourClient  # noqa: E402

DETAIL_CALLS, FEE_CALLS = 2, 1


def keyword_sets(s: Settings, spec: str) -> list[list[str]]:
    """'all' = 키워드 없음 + 키워드 1개씩. 또는 'none,forest,walk' 처럼."""
    if spec == "all":
        return [[]] + [[k] for k in s.keywords.ids]
    out = []
    for part in spec.split(","):
        part = part.strip()
        if part == "none":
            out.append([])
        elif part in s.keywords.ids:
            out.append([part])
        else:
            raise SystemExit(f"알 수 없는 키워드: {part}")
    return out


async def plan(rec: Recommender, origins: list[tuple[float, float]], drives: list[int],
               kw_sets: list[list[str]], top: int, rng: random.Random) -> list[Spot]:
    """조합별 1차 점수 상위 top 곳의 합집합 (처음 나온 순서). 시간대 키워드도 분류 기준으로 고른다."""
    s, sc = rec.s, rec.s.scoring
    seen: dict[str, Spot] = {}
    for lat, lng in origins:
        for m in drives:
            near = await rec.index.near(lat, lng, radius_km(m, s.radius))
            base = list(rec._base_pool(near, m))
            for kids in kw_sets:
                scored = []
                for spot, _d, _eta, ds in base:
                    cached = rec.details.peek(spot)
                    ov = cached.overview if cached else ""
                    mk = match_keywords(spot, kids, s.keywords, sc, ov) if kids else {}
                    if kids and not mk:
                        continue
                    pre = (sc.w_keyword * combined_keyword_score(mk, sc) + sc.w_distance * ds
                           + info_score(spot.image_url is not None, bool(ov), sc))
                    scored.append((pre, spot))
                rng.shuffle(scored)                       # 동점 순서는 서버처럼 섞는다
                scored.sort(key=lambda x: x[0], reverse=True)
                for _pre, spot in scored[:top]:
                    seen.setdefault(spot.content_id, spot)
    return list(seen.values())


def cost(details: DetailService, spot: Spot) -> int:
    """이 곳을 예열하는 데 필요한 호출 수 (캐시에 있으면 0)."""
    need = 0 if details.peek(spot) is not None else DETAIL_CALLS
    return need + (0 if details.peek_fee(spot)[0] else FEE_CALLS)


async def warm(details: DetailService, spots: list[Spot], max_calls: int, used: Callable[[], int]) -> dict:
    """상한(max_calls)을 넘기 전에 멈춘다. used() 는 지금까지 쓴 호출 수."""
    r = {"warmed": 0, "cached": 0, "failed": 0, "stopped_at": None}
    for i, spot in enumerate(spots):
        c = cost(details, spot)
        if c == 0:
            r["cached"] += 1
            continue
        if used() + c > max_calls:
            r["stopped_at"] = i
            break
        try:
            if details.peek(spot) is None:
                await details.get(spot)
            if not details.peek_fee(spot)[0]:
                await details.fee(spot)
            r["warmed"] += 1
        except TourApiError:
            r["failed"] += 1
    return r


def parse_origin(text: str) -> tuple[float, float]:
    try:
        lat, lng = (float(x) for x in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("--origin 은 '위도,경도' 형식") from None
    return lat, lng


async def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--origin", type=parse_origin, action="append", required=True, help="위도,경도 (여러 번 가능)")
    ap.add_argument("--drives", default="30,60", help="운전 시간(분) 목록, 기본 30,60")
    ap.add_argument("--keywords", default="all", help="all(기본: 키워드 없음 + 9개 각각) 또는 none,forest,...")
    ap.add_argument("--top", type=int, default=10, help="조합별 상위 몇 곳 (기본 10)")
    ap.add_argument("--max-calls", type=int, required=True, help="이번 실행의 관광 API 호출 상한 (필수)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="호출 없이 대상 수와 예상 호출 수만 출력")
    a = ap.parse_args(argv)

    s = load_settings()
    cache = DiskCache(s.cache_dir)
    tour = TourClient(s)
    try:
        index = IndexManager(s, tour, cache)
        details = DetailService(s, tour, cache)
        await index.ensure_index()                    # 하루 지난 인덱스면 갱신 호출이 생긴다 (상한에 포함)
        rec = Recommender(s, index, details, AliasPool(s.alias, set()))
        drives = [int(x) for x in a.drives.split(",")]
        targets = await plan(rec, a.origin, drives, keyword_sets(s, a.keywords), a.top, random.Random(a.seed))
        need = sum(cost(details, t) for t in targets)
        print(f"대상 {len(targets)}곳, 이미 캐시 {sum(cost(details, t) == 0 for t in targets)}곳, "
              f"예상 호출 {need}회 (상한 {a.max_calls}회, 인덱스 갱신 {tour.calls}회 사용)")
        if a.dry_run:
            return
        r = await warm(details, targets, a.max_calls, lambda: tour.calls)
        print(f"예열 {r['warmed']}곳, 캐시 있음 {r['cached']}곳, 실패 {r['failed']}곳, 관광 API {tour.calls}회")
        if r["stopped_at"] is not None:
            print(f"상한에 닿아 {len(targets) - r['stopped_at']}곳은 남김 — 다음 날 같은 명령으로 이어서 실행")
    finally:
        await tour.aclose()


if __name__ == "__main__":
    asyncio.run(main())
