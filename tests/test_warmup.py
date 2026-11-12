"""scripts/warmup.py: 계획·상한·필수 옵션 (실제 관광 API 호출 없음)."""
import asyncio
import random

import pytest

from app.recommend import Recommender
from conftest import FakeDetails, FakeIndex, make_pool, make_spot
from scripts import warmup

O = (36.0, 128.0)


class CountingDetails(FakeDetails):
    """조회하면 캐시에 남는 가짜 상세 서비스. calls() = 지금까지의 '관광 API 호출 수'."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.n = 0

    async def get(self, spot):
        self.n += 2
        self.cached.add(spot.content_id)
        return await super().get(spot)

    async def fee(self, spot):
        self.n += 1
        self.cached_fee.add(spot.content_id)
        return await super().fee(spot)


def _rec(settings, spots, details):
    return Recommender(settings, FakeIndex(spots), details, make_pool(settings))


def _spots():
    parks = [make_spot(f"k{i:02d}", f"공원{i}", north_km=3.6 + 0.1 * i, lcls3="VE030400", base=O) for i in range(14)]
    views = [make_spot(f"v{i}", f"높은곳{i}", east_km=4 + 0.2 * i, lcls3="VE010200", base=O) for i in range(4)]
    return parks + views


def test_plan_top_per_combination(settings):
    rec = _rec(settings, _spots(), FakeDetails())
    sets = warmup.keyword_sets(settings, "none,walk,view")
    t = asyncio.run(warmup.plan(rec, [O], [15], sets, 10, random.Random(0)))
    ids = [s.content_id for s in t]
    assert len(ids) == len(set(ids))                                     # 합집합, 중복 없음
    assert sum(i.startswith("v") for i in ids) == 4                      # view 조합은 4곳뿐
    assert sum(i.startswith("k") for i in ids) >= 10                     # walk 조합 상위 10
    assert len(ids) <= 10 * len(sets)


def test_keyword_sets(settings):
    assert warmup.keyword_sets(settings, "all") == [[]] + [[k] for k in settings.keywords.ids]
    assert len(warmup.keyword_sets(settings, "all")) == 10
    with pytest.raises(SystemExit):
        warmup.keyword_sets(settings, "mountain")


def test_warm_respects_max_calls_and_skips_cached(settings):
    spots = _spots()[:6]
    d = CountingDetails(cached={spots[0].content_id}, cached_fee={spots[0].content_id, spots[1].content_id})
    assert warmup.cost(d, spots[0]) == 0 and warmup.cost(d, spots[1]) == 2 and warmup.cost(d, spots[2]) == 3
    r = asyncio.run(warmup.warm(d, spots, 8, lambda: d.n))
    assert d.n <= 8                                                      # 상한을 넘지 않음
    assert r == {"warmed": 3, "cached": 1, "failed": 0, "stopped_at": 4}  # 2 + 3 + 3 = 8 → 네 번째에서 멈춤
    r2 = asyncio.run(warmup.warm(d, spots, 100, lambda: d.n))            # 이어서 실행하면 남은 곳만
    assert r2["warmed"] == 2 and r2["cached"] == 4


def test_max_calls_required(capsys):
    with pytest.raises(SystemExit) as e:
        asyncio.run(warmup.main(["--origin", "37.5,127.0"]))
    assert e.value.code == 2 and "--max-calls" in capsys.readouterr().err
