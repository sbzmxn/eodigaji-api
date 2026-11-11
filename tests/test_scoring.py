import random

import pytest

from app.scoring import distance_score, eta_minutes, radius_km, season_bonus, weighted_pick


# ---- 반경 환산 (처음 30분 30km/h, 이후 60km/h, 우회계수 1.3) -------------------
@pytest.mark.parametrize("m,expected", [(15, 7.5 / 1.3), (30, 15 / 1.3), (45, 30 / 1.3), (60, 45 / 1.3), (120, 105 / 1.3)])
def test_radius(settings, m, expected):
    assert radius_km(m, settings.radius) == pytest.approx(expected)


def test_radius_min_and_continuity(settings):
    assert radius_km(5, settings.radius) == settings.radius.min_radius_km
    # 30분 → 31분에서 반경이 튀지 않는다 (연속 공식)
    assert 0 < radius_km(31, settings.radius) - radius_km(30, settings.radius) < 1.0


@pytest.mark.parametrize("m", [15, 30, 31, 45, 60, 90, 120])
def test_eta_is_inverse_of_radius(settings, m):
    assert eta_minutes(radius_km(m, settings.radius), settings.radius) == pytest.approx(m)


# ---- 거리 점수 ---------------------------------------------------------------
def test_distance_score_band(settings):
    sc = settings.scoring
    assert distance_score(36, 60, sc) == 1.0          # 60%
    assert distance_score(60, 60, sc) == 1.0          # 100%
    assert distance_score(60.1, 60, sc) is None       # 초과 → 제외
    assert distance_score(0, 60, sc) == pytest.approx(sc.near_floor)
    assert distance_score(18, 60, sc) == pytest.approx(0.65)  # 30% → floor 와 1.0 의 중간
    assert distance_score(10, 60, sc) < distance_score(30, 60, sc) < 1.0


# ---- 계절 가산 ---------------------------------------------------------------
def test_season_bonus(settings):
    rules = settings.season_rules
    assert season_bonus(11, {"forest"}, "가을 단풍이 아름답다", rules) == pytest.approx(0.3)
    assert season_bonus(10, {"forest"}, "가을 단풍이 아름답다", rules) == 0
    assert season_bonus(11, {"forest"}, "소나무 숲", rules) == 0
    assert season_bonus(11, {"sea"}, "단풍", rules) == 0


# ---- 가중 랜덤 ---------------------------------------------------------------
def test_weighted_pick_seeded_and_weighted():
    items = ["a", "b", "c"]
    picks1 = [weighted_pick(items, [3, 1, 0], random.Random(7), 0.05) for _ in range(5)]
    picks2 = [weighted_pick(items, [3, 1, 0], random.Random(7), 0.05) for _ in range(5)]
    assert picks1 == picks2                                    # 시드 고정 재현
    rng = random.Random(1)
    counts = {k: 0 for k in items}
    for _ in range(4000):
        counts[weighted_pick(items, [3, 1, 0], rng, 0.05)] += 1
    assert counts["a"] > counts["b"] > counts["c"] > 0          # 0점도 min_weight 로 아주 가끔


def test_weighted_pick_empty():
    with pytest.raises(ValueError):
        weighted_pick([], [], random.Random(), 0.05)
