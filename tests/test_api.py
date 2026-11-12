import logging
import random

import pytest
from fastapi.testclient import TestClient

from app.alias import AliasPoolError
from app.main import create_app
from app.scoring import eta_minutes
from app.spots import Detail, haversine_km
from conftest import LOC_IDS, LOCATIONS, NOW, FakeDetails, FakeIndex, make_pool, make_spot


@pytest.fixture(params=LOCATIONS, ids=LOC_IDS)
def origin(request):
    """모든 API 테스트를 통합 확인용 좌표 7곳에서 각각 돌린다."""
    return (request.param["lat"], request.param["lng"])


def spots_around(o):
    """출발지 기준 가상 장소. drive_minutes=15 → 반경 5.77km."""
    return [
        make_spot("p1", "가람근린공원", north_km=4.0, lcls3="VE030400", base=o),
        make_spot("p2", "나래근린공원", east_km=5.0, lcls3="VE030400", base=o),
        make_spot("p3", "다솜시민공원", north_km=-4.5, lcls3="VE030100", base=o),
        make_spot("w1", "라온저수지", east_km=-5.0, lcls3="NA020300", base=o),
        make_spot("v1", "바람전망대", north_km=3.5, east_km=3.5, lcls3="VE010200", base=o),
        make_spot("x1", "어느 기차역", north_km=4.0, east_km=1.0, lcls3="VE110200", base=o),   # 제외 분류
        make_spot("far", "먼호수", north_km=20.0, lcls3="NA020200", base=o),                   # 반경 밖
        make_spot("edge", "경계근린공원", north_km=5.7, lcls3="VE030400", base=o),              # 운전시간 초과
    ]


def body(o, **over):
    return {"latitude": o[0], "longitude": o[1], "drive_minutes": 15, "keyword_ids": [], "exclude_content_ids": [],
            **over}


def client(settings, o, details=None, spots=None, now=NOW, seed=1, load_on_startup=False):
    app = create_app(settings, index=FakeIndex(spots if spots is not None else spots_around(o)),
                     details=details or FakeDetails(), alias=make_pool(settings, seed),
                     now_fn=lambda: now, rng=random.Random(seed), load_on_startup=load_on_startup)
    return TestClient(app)


def test_recommend_contract(settings, origin):
    with client(settings, origin) as c:
        r = c.post("/api/v1/recommend", json=body(origin))
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j) == {"request_id", "blind_name", "destination", "hint", "reveal", "alias_source",
                      "straight_km", "estimated_drive_seconds"}
    assert set(j["hint"]) == {"parking", "fee", "open_now"}                       # theme 없음
    assert set(j["reveal"]) == {"content_id", "real_name", "address", "overview_short", "image_url",
                                "theme", "matched_keywords"}
    assert j["alias_source"] == "pool" and j["blind_name"] in settings.alias.pool
    assert j["hint"] == {"parking": "가능", "fee": "무료", "open_now": True}
    spot = {s.content_id: s for s in spots_around(origin)}[j["reveal"]["content_id"]]
    assert j["destination"] == {"lat": spot.lat, "lng": spot.lng}
    assert j["reveal"]["content_id"] not in {"x1", "far", "edge"}
    assert j["straight_km"] == pytest.approx(haversine_km(*origin, spot.lat, spot.lng), abs=0.01)
    assert j["estimated_drive_seconds"] == round(eta_minutes(haversine_km(*origin, spot.lat, spot.lng),
                                                             settings.radius) * 60)
    assert 0 < j["estimated_drive_seconds"] <= 15 * 60


def test_keyword_filter_and_reveal(settings, origin):
    with client(settings, origin) as c:
        j = c.post("/api/v1/recommend", json=body(origin, keyword_ids=["water"])).json()
    assert j["reveal"]["content_id"] == "w1"
    assert j["reveal"]["matched_keywords"] == ["water"] and j["reveal"]["theme"] == "호수·강"


def test_exclude_ids(settings, origin):
    with client(settings, origin) as c:
        j = c.post("/api/v1/recommend", json=body(origin, exclude_content_ids=["p1", "p2", "p3", "v1"])).json()
    assert j["reveal"]["content_id"] == "w1"


def test_only_top3_get_detail_lookup(settings, origin):
    d = FakeDetails()
    with client(settings, origin, details=d) as c:
        c.post("/api/v1/recommend", json=body(origin))
    assert len(d.calls) == 3


def test_closed_on_arrival_day_excluded(settings, origin):
    # 2026-11-11(수) 도착. w1 은 매주 수요일 휴무 → 절대 선택되지 않는다
    d = FakeDetails({"w1": Detail(overview="물가", parking="가능", restdate="매주 수요일", usetime="09:00~18:00")})
    for seed in range(5):
        with client(settings, origin, details=d, seed=seed) as c:
            r = c.post("/api/v1/recommend", json=body(origin, keyword_ids=["water"]))
        assert r.status_code == 404 and r.json()["error"]["code"] == "NO_CANDIDATES"


def test_closed_by_hours_excluded_when_others_open(settings, origin):
    # 14:15 도착. p1 은 14:10 에 문을 닫음 → 다른 산책 후보가 열려 있으면 p1 은 빠진다
    d = FakeDetails({"p1": Detail(overview="쉼", parking="가능", restdate="연중무휴", usetime="09:00~14:10")})
    for seed in range(8):
        with client(settings, origin, details=d, seed=seed) as c:
            j = c.post("/api/v1/recommend", json=body(origin, keyword_ids=["walk"])).json()
        assert j["reveal"]["content_id"] != "p1" and j["hint"]["open_now"] is True


def test_all_closed_by_hours_revived_with_open_now_false(settings, origin):
    d = FakeDetails({"w1": Detail(overview="물가", parking="불가", restdate="매주 월요일", usetime="09:00~14:10")})
    with client(settings, origin, details=d) as c:
        j = c.post("/api/v1/recommend", json=body(origin, keyword_ids=["water"])).json()
    assert j["reveal"]["content_id"] == "w1"
    assert j["hint"]["open_now"] is False and j["hint"]["parking"] == "불가"


def test_no_candidates(settings, origin):
    with client(settings, origin, spots=[spots_around(origin)[6]]) as c:
        r = c.post("/api/v1/recommend", json=body(origin))
    assert r.status_code == 404 and r.json()["error"]["code"] == "NO_CANDIDATES"


@pytest.mark.parametrize("over,code", [
    ({"keyword_ids": ["mountain"]}, "INVALID_KEYWORD"),
    ({"keyword_ids": ["sea", "water", "forest", "walk"]}, "INVALID_KEYWORD"),
    ({"keyword_ids": ["sea", "sea"]}, "INVALID_KEYWORD"),
    ({"latitude": 0.0}, "INVALID_LOCATION"),
    ({"latitude": "abc"}, "INVALID_LOCATION"),
    ({"drive_minutes": 10}, "INVALID_REQUEST"),
    ({"drive_minutes": 121}, "INVALID_REQUEST"),
    ({"keyword_ids": ["night"]}, "KEYWORD_UNAVAILABLE"),  # 14:15 도착
])
def test_errors(settings, over, code):
    o = (LOCATIONS[0]["lat"], LOCATIONS[0]["lng"])
    with client(settings, o) as c:
        r = c.post("/api/v1/recommend", json=body(o, **over))
    j = r.json()
    assert r.status_code == 400 and j["error"]["code"] == code
    assert j["error"]["request_id"]
    echoed = {k: v for k, v in j["error"].items() if k != "request_id"}   # request_id 는 무작위 16진수
    assert "abc" not in str(echoed) and str(o[0]) not in str(echoed)    # 입력값을 되돌려 주지 않는다


@pytest.mark.parametrize("payload", [
    {"available_minutes": 120},                                   # drive_minutes 와 함께
    {"available_minutes": 120, "drive_minutes": None},            # drive_minutes 없이
])
def test_available_minutes_renamed(settings, payload):
    o = (LOCATIONS[0]["lat"], LOCATIONS[0]["lng"])
    b = body(o, **payload)
    if b.get("drive_minutes") is None:
        b.pop("drive_minutes")
    with client(settings, o) as c:
        r = c.post("/api/v1/recommend", json=b)
    e = r.json()["error"]
    assert r.status_code == 400 and e["code"] == "INVALID_REQUEST" and "drive_minutes" in e["message"]


def test_unknown_fields_ignored_with_warning(settings, caplog):
    o = (LOCATIONS[0]["lat"], LOCATIONS[0]["lng"])
    caplog.set_level(logging.WARNING)
    with client(settings, o) as c:
        r = c.post("/api/v1/recommend", json=body(o, companion="family", app_version="1.0"))
    assert r.status_code == 200
    warn = [x.getMessage() for x in caplog.records if "ignored fields" in x.getMessage()]
    assert warn and "companion" in warn[0] and "family" not in warn[0]   # 이름만, 값은 남기지 않음


def test_blind_names_vary_and_come_from_pool(settings, origin):
    with client(settings, origin) as c:
        names = [c.post("/api/v1/recommend", json=body(origin)).json()["blind_name"] for _ in range(12)]
    assert set(names) <= set(settings.alias.pool)
    assert len(set(names[:10])) == 10                    # 최근 10개는 겹치지 않는다


def test_bad_pool_blocks_app_creation(settings):
    settings.alias.pool = ["바다가 보이는 쉼터"] + settings.alias.pool[1:]
    with pytest.raises(AliasPoolError):
        client(settings, (LOCATIONS[0]["lat"], LOCATIONS[0]["lng"]))


def test_startup_rechecks_pool_with_admin_unit_names(settings):
    """서버 시작 시 인덱스의 읍·면·동 이름으로 다시 검사하고, 걸리면 시작하지 않는다."""
    o = (LOCATIONS[0]["lat"], LOCATIONS[0]["lng"])
    spots = spots_around(o) + [make_spot("u", "어느곳", base=o, addr1="가나도 다라시 지금리 1")]   # '지금 여기'
    with pytest.raises(AliasPoolError):
        with client(settings, o, spots=spots, load_on_startup=True):
            pass
    with client(settings, o, load_on_startup=True) as c:       # 걸리는 동 이름이 없으면 정상 시작
        assert c.get("/healthz").status_code == 200


def test_keywords_endpoint(settings, origin):
    with client(settings, origin) as c:
        r = c.get("/api/v1/keywords", params={"lat": origin[0], "lng": origin[1], "drive_minutes": 15})
    assert r.status_code == 200, r.text
    j = r.json()
    ks = {k["id"]: k for k in j["keywords"]}
    assert list(ks) == ["sea", "water", "forest", "walk", "view", "quiet", "sunset", "night"]
    assert j["arrival_time"] == "14:15" and j["sunset_time"].startswith("17:")
    assert ks["walk"]["enabled"] is True and ks["walk"]["reason"] is None
    assert ks["sea"] == {"id": "sea", "label": "바다", "enabled": False, "reason": "지금 시간으론 멀어요"}
    assert ks["water"]["enabled"] is False                    # 1곳뿐 (<3)
    assert ks["sunset"]["reason"] == f"일몰은 {j['sunset_time']}이에요"
    assert "이후 도착부터" in ks["night"]["reason"]


def test_keywords_sunset_enabled_near_sunset(settings, origin):
    spots = [make_spot(f"v{i}", f"높은곳{i}", north_km=4 + i * 0.3, lcls3="VE010200", base=origin) for i in range(3)]
    late = NOW.replace(hour=16, minute=50)                     # +15분 = 17:05 도착
    with client(settings, origin, spots=spots, now=late) as c:
        j = c.get("/api/v1/keywords", params={"lat": origin[0], "lng": origin[1], "drive_minutes": 15}).json()
    ks = {k["id"]: k for k in j["keywords"]}
    assert ks["sunset"]["enabled"] and ks["view"]["enabled"]


# ---- 섬 규칙 -----------------------------------------------------------------
JEJU = next((l["lat"], l["lng"]) for l in LOCATIONS if l["name"] == "제주시")


def _island_spots():
    return [
        make_spot("j1", "가나오름", north_km=2.0, lcls3="NA010100", regn="50", base=JEJU, addr1="제주특별자치도 제주시 가나동"),
        make_spot("j2", "다라해변", north_km=-3.0, lcls3="NA020900", regn="50", base=JEJU, addr1="제주특별자치도 제주시 다라동"),
        make_spot("u1", "작은섬해변", north_km=5.0, lcls3="NA020900", regn="50", base=JEJU, addr1="제주특별자치도 제주시 우도면"),
        make_spot("m1", "건너편포구", north_km=5.2, lcls3="NA020700", regn="12", signgu="850", base=JEJU,
                  addr1="전남 완도군 노화읍"),
    ]


def test_island_origin_keeps_same_landmass(settings):
    for seed in range(10):
        with client(settings, JEJU, spots=_island_spots(), seed=seed) as c:
            j = c.post("/api/v1/recommend", json=body(JEJU, keyword_ids=["sea"])).json()
        assert j["reveal"]["content_id"] == "j2"                 # 우도(배)·완도(배) 후보 제외


def test_mainland_origin_excludes_island(settings):
    o = (LOCATIONS[0]["lat"], LOCATIONS[0]["lng"])
    spots = [make_spot("a1", "가까운공원", north_km=4.0, base=o, regn="11"),
             make_spot("i1", "섬해변", east_km=4.5, lcls3="NA020900", regn="28", signgu="720", base=o,
                       addr1="인천광역시 옹진군 덕적면"),
             make_spot("i2", "다리건너해변", east_km=-4.5, lcls3="NA020900", regn="28", signgu="720", base=o,
                       addr1="인천광역시 옹진군 영흥면")]
    with client(settings, o, spots=spots) as c:
        r = c.post("/api/v1/recommend", json=body(o, keyword_ids=["sea"]))
    assert r.json()["reveal"]["content_id"] == "i2"              # 덕적면 제외, 영흥면(다리 연결)은 허용


# ---- 재추천 (retry_context) ------------------------------------------------------
def _retry_spots(o):
    """drive_minutes=15. p1(4km) 추정 624초. near(2km)는 속도가 반으로 줄어도 15분 안."""
    return spots_around(o) + [
        make_spot("near", "가까운쉼터", north_km=2.0, lcls3="VE030400", base=o),
        make_spot("w10", "먼저수지", north_km=-10.0, lcls3="NA020300", base=o),   # 기본 반경(5.77km) 밖
    ]


def test_retry_slow_eta_shrinks_radius(settings, origin):
    # 실제 ETA 가 서버 추정(624초)의 2배 → 속도 0.5배 → 15분 안 = 직선 약 2.9km 이내만
    rc = {"previous_content_ids": ["p1"], "observed_eta_seconds": 1248}
    for seed in range(5):
        with client(settings, origin, spots=_retry_spots(origin), seed=seed) as c:
            j = c.post("/api/v1/recommend", json=body(origin, retry_context=rc)).json()
        assert j["reveal"]["content_id"] == "near"
        assert j["straight_km"] == pytest.approx(2.0, abs=0.01)
        assert j["estimated_drive_seconds"] == pytest.approx(2.0 * 1.3 / 30 * 3600 * 2, abs=2)   # 5.2분 × 2


def test_retry_fast_eta_widens_radius(settings, origin):
    # 실제 ETA 가 추정보다 훨씬 짧음 → 배율 상한 2.0 → 10km 저수지가 15분 안으로 들어온다
    rc = {"previous_content_ids": ["w1", "p1"], "observed_eta_seconds": 200}
    with client(settings, origin, spots=_retry_spots(origin)) as c:
        j = c.post("/api/v1/recommend", json=body(origin, keyword_ids=["water"], retry_context=rc)).json()
        no_retry = c.post("/api/v1/recommend", json=body(origin, keyword_ids=["water"],
                                                         exclude_content_ids=["w1", "p1"]))
    assert j["reveal"]["content_id"] == "w10"
    assert j["estimated_drive_seconds"] == pytest.approx(10 * 1.3 / 60 * 3600, abs=2)        # 26분 / 2
    assert no_retry.status_code == 404                                                     # 보정 없으면 반경 밖


def test_retry_excludes_previous_ids_and_is_not_stored(settings, origin):
    rc = {"previous_content_ids": ["p1", "p2", "p3", "v1"], "observed_eta_seconds": 600}
    with client(settings, origin) as c:
        j = c.post("/api/v1/recommend", json=body(origin, retry_context=rc)).json()
        assert j["reveal"]["content_id"] == "w1"
        assert c.app.state.recommender.s.radius.speed_segments[0].kmh == 30   # 설정은 그대로
        again = c.post("/api/v1/recommend", json=body(origin)).json()     # 다음 요청은 보정 없이
    assert again["estimated_drive_seconds"] == pytest.approx(
        eta_minutes(again["straight_km"], settings.radius) * 60, abs=2)


def test_speed_factor_rules(settings, origin):
    import asyncio
    from app.recommend import Retry
    with client(settings, origin, spots=_retry_spots(origin)) as c:
        rec = c.app.state.recommender
        f = lambda r: asyncio.run(rec.speed_factor(*origin, r))
        assert f(None) is None
        assert f(Retry(["p1"], None)) is None                        # ETA 없으면 제외만
        assert f(Retry(["unknown"], 600)) is None                    # 인덱스에 없는 후보
        assert f(Retry(["p1"], 624)) == pytest.approx(1.0, abs=0.01)
        assert f(Retry(["p1"], 1248)) == pytest.approx(0.5, abs=0.01)
        assert f(Retry(["p1"], 100000)) == settings.retry.min_speed_factor
        assert f(Retry(["p1"], 10)) == settings.retry.max_speed_factor
        settings.retry.min_estimated_seconds = 700                   # p1(624초)은 너무 가까워 보정 안 함
        assert f(Retry(["p1"], 1248)) is None


@pytest.mark.parametrize("rc", [{"previous_content_ids": ["p1"], "observed_eta_seconds": 0},
                                {"previous_content_ids": ["p1"], "observed_eta_seconds": -5},
                                {"previous_content_ids": ["p1"], "observed_eta_seconds": "abc"},
                                {"previous_content_ids": ["x"] * 201}])
def test_retry_context_validation(settings, rc):
    o = (LOCATIONS[0]["lat"], LOCATIONS[0]["lng"])
    with client(settings, o) as c:
        r = c.post("/api/v1/recommend", json=body(o, retry_context=rc))
    e = r.json()["error"]
    assert r.status_code == 400 and e["code"] == "INVALID_REQUEST" and "abc" not in e["message"]
