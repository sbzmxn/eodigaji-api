from datetime import datetime, timedelta

import pytest

from app.keywords import combined_keyword_score, is_excluded, keyword_score, match_keywords, theme_of, time_status
from app.sun import sunset
from app.spots import mark_island_coast
from conftest import KST, LCLS_NAMES, make_spot


def ks(kid, spot, s, ov=""):
    return keyword_score(kid, spot, s.keywords, s.scoring, ov)


def test_class_and_title_match(settings):
    beach = make_spot("1", "어느 해수욕장", lcls3="NA020900", regn="47")
    assert ks("sea", beach, settings) == 1.0
    title_only = make_spot("2", "바다마을 카페거리", lcls3="VE040100", regn="47")
    assert ks("sea", title_only, settings) == 0.5
    assert ks("sea", make_spot("3", "근린공원", lcls3="VE030400"), settings) == 0
    # 소개글 단어도 0.5
    assert ks("quiet", make_spot("4", "어느 건물", lcls3="VE010100"), settings, "오래된 한옥이 남아 있다") == 0.5


def test_river_island_goes_to_water(settings):
    """같은 시·군·구에 해변·항구 등이 없으면 강·호수의 섬(→water), 있으면 바다의 섬(→sea)."""
    spots = mark_island_coast([
        make_spot("1", "강가운데섬", lcls3="NA020500", regn="11", signgu="650"),   # 해안 분류 없는 시군구
        make_spot("2", "먼바다섬", lcls3="NA020500", regn="12", signgu="870"),
        make_spot("3", "어느 해수욕장", lcls3="NA020900", regn="12", signgu="870"),  # 같은 시군구에 해변
    ], settings.keywords.coastal_lcls)
    river, sea_island = spots[0], spots[1]
    assert ks("sea", river, settings) == 0 and ks("water", river, settings) == 1.0
    assert ks("sea", sea_island, settings) == 1.0 and ks("water", sea_island, settings) == 0


def test_time_keywords_require_base_class(settings):
    walk_park = make_spot("1", "노을공원", lcls3="VE030400")            # 산책(walk)은 노을의 base 아님
    assert ks("sunset", walk_park, settings) == 0
    tower = make_spot("2", "어느 타워", lcls3="VE010200")
    assert ks("sunset", tower, settings) == 0.5                          # base 만
    assert ks("sunset", tower, settings, "붉은 노을이 아름답다") == 1.0   # + 소개글 텍스트 가산
    assert ks("night", tower, settings) == 0.5
    assert ks("night", walk_park, settings, "야경 명소") == 1.0
    bridge = make_spot("3", "어느 대교", lcls3="VE010300")               # 야경 extra_lcls
    assert ks("night", bridge, settings) == 0.5
    assert ks("night", make_spot("4", "야경 맛집 골목", lcls3="VE040100"), settings) == 0  # 텍스트만으론 안 됨


def test_multi_match_bonus(settings):
    m = match_keywords(make_spot("1", "해넘이 해변", lcls3="NA020900", regn="47"), ["sea", "sunset"],
                       settings.keywords, settings.scoring)
    assert set(m) == {"sea", "sunset"}
    assert combined_keyword_score(m, settings.scoring) == pytest.approx(1.0 + 0.3)


def test_exclusions(settings):
    p = settings.candidates.exclude_lcls_prefixes
    assert is_excluded(make_spot("1", "어느역", lcls3="VE110200"), p)      # 교통
    assert is_excluded(make_spot("2", "도서관", lcls3="VE090300"), p)      # 교육
    assert is_excluded(make_spot("3", "공장", lcls3="EX061000"), p)        # 산업관광
    assert is_excluded(make_spot("4", "동상", lcls3="VE010500"), p)        # 동상
    assert not is_excluded(make_spot("5", "공원", lcls3="VE030400"), p)


def test_theme(settings):
    spot = make_spot("1", "어느 공원", lcls3="VE030400")
    assert theme_of(spot, ["walk"], settings.keywords, LCLS_NAMES) == "산책"
    assert theme_of(spot, [], settings.keywords, LCLS_NAMES) == "근린공원"


# ---- 일몰 / 시간 판정 ----------------------------------------------------------
@pytest.mark.parametrize("day,hh,mm", [(datetime(2024, 6, 21), 19, 57), (datetime(2024, 12, 21), 17, 17)])
def test_sunset_seoul(day, hh, mm):
    s = sunset(day.date(), 37.5665, 126.9780, KST)
    target = s.replace(hour=hh, minute=mm, second=0, microsecond=0)
    assert abs((s - target).total_seconds()) <= 180


def _at(h, m):
    return datetime(2026, 11, 11, h, m, tzinfo=KST)


def test_sunset_window(settings):
    kw, tw = settings.keywords.keywords["sunset"], settings.time_windows
    sd = _at(17, 20)
    assert time_status(kw, _at(16, 20), sd, tw).active                  # 60분 전
    assert time_status(kw, _at(17, 40), sd, tw).active                  # 20분 후
    off = time_status(kw, _at(16, 19), sd, tw)
    assert not off.active and off.reason == "일몰은 17:20이에요"
    assert not time_status(kw, _at(17, 41), sd, tw).active


def test_night_window(settings):
    kw, tw = settings.keywords.keywords["night"], settings.time_windows
    sd = _at(17, 20)
    early = time_status(kw, _at(17, 49), sd, tw)
    assert not early.active and "17:50" in early.reason
    assert time_status(kw, _at(17, 50), sd, tw).active
    assert time_status(kw, _at(23, 0), sd, tw).active
    late = time_status(kw, _at(23, 1), sd, tw)
    assert not late.active and "23:00" in late.reason


def test_non_time_keyword_always_active(settings):
    assert time_status(settings.keywords.keywords["sea"], _at(3, 0), _at(17, 20), settings.time_windows).active


def test_arrival_based_activation(settings):
    """같은 출발 시각이라도 drive_minutes 로 도착 시각이 바뀌면 노을 활성 여부가 바뀐다."""
    kw, tw = settings.keywords.keywords["sunset"], settings.time_windows
    sd = _at(17, 20)
    now = _at(15, 50)
    assert not time_status(kw, now + timedelta(minutes=15), sd, tw).active   # 16:05 도착
    assert time_status(kw, now + timedelta(minutes=60), sd, tw).active       # 16:50 도착
