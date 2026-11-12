"""실제 관광 API 통합 테스트. 실행: pytest -m integration
호출 수: 상세 캐시(data/cache)가 있으면 수십 회 이내. 끝나면 이번 실행의 관광 API 호출 수를 출력한다."""
import asyncio
import json
from pathlib import Path

import pytest

from app.config import load_settings
from app.spots import normalize_fee, normalize_parking
from app.tour_client import TourClient

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def real_settings():
    s = load_settings()
    if not s.tour_kor_key.get_secret_value():
        pytest.skip("TOURAPI_KOR_KEY 없음")
    return s


def test_detail_and_reference_live(real_settings):
    async def go():
        tc = TourClient(real_settings)
        try:
            common = await tc.detail_common("126132")
            intro = await tc.detail_intro("126132")
            info = await tc.detail_info("126132")
            regions = await tc.region_names()
            return common, intro, info, regions
        finally:
            await tc.aclose()

    common, intro, info, regions = asyncio.run(go())
    assert common["title"] == "대구앞산공원" and common["overview"]
    assert float(common["mapy"]) == pytest.approx(35.82, abs=0.05)
    assert normalize_parking(intro.get("parking", "")) in ("가능", "불가", None)
    assert normalize_fee(info) in ("무료", "유료", None)
    assert len(regions) > 200 and {"lDongRegnNm", "lDongSignguNm"} <= set(regions[0])


LOCATIONS = json.loads((Path(__file__).parent / "data" / "locations.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def live_client(real_settings):
    from fastapi.testclient import TestClient
    from app.main import create_app
    app = create_app(real_settings)
    with TestClient(app) as c:
        yield c
    print(f"\n[integration] 관광 API 호출 {app.state.index.tour.calls}회")


@pytest.mark.parametrize("loc", LOCATIONS, ids=[l["name"] for l in LOCATIONS])
def test_live_keywords_and_recommend(live_client, loc):
    """실제 인덱스로 7곳 × 30분. 상세는 content_id 캐시라 재실행 시 호출이 거의 없다."""
    q = {"lat": loc["lat"], "lng": loc["lng"], "drive_minutes": 30}
    rk = live_client.get("/api/v1/keywords", params=q)
    assert rk.status_code == 200 and len(rk.json()["keywords"]) == 9
    rr = live_client.post("/api/v1/recommend", json={"latitude": loc["lat"], "longitude": loc["lng"],
                                                     "drive_minutes": 30, "keyword_ids": []})
    assert rr.status_code == 200, rr.text
    j = rr.json()
    assert j["blind_name"] and j["reveal"]["real_name"] not in j["blind_name"]


@pytest.mark.parametrize("loc", LOCATIONS, ids=[l["name"] for l in LOCATIONS])
def test_live_retry_with_slow_eta(live_client, loc):
    """7곳 × 30분: 1차 추천 → '실제 ETA 가 서버 추정의 1.5배' 로 재추천.
    직전 후보는 제외되고, 보정 속도(1/1.5) 기준 예상 시간이 30분 이내인 더 가까운 곳이 나와야 한다."""
    from app.config import load_settings
    from app.scoring import radius_km
    s = load_settings(env_file=None)
    base = {"latitude": loc["lat"], "longitude": loc["lng"], "drive_minutes": 30, "keyword_ids": []}
    r1 = live_client.post("/api/v1/recommend", json=base)
    assert r1.status_code == 200, r1.text
    j1 = r1.json()
    assert 0 < j1["estimated_drive_seconds"] <= 30 * 60 and j1["straight_km"] >= 0
    observed = round(j1["estimated_drive_seconds"] * 1.5)
    rc = {"previous_content_ids": [j1["reveal"]["content_id"]], "observed_eta_seconds": observed}
    r2 = live_client.post("/api/v1/recommend", json={**base, "retry_context": rc})
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["reveal"]["content_id"] != j1["reveal"]["content_id"]
    assert 0 < j2["estimated_drive_seconds"] <= 30 * 60
    factor = j1["estimated_drive_seconds"] / observed
    if j1["estimated_drive_seconds"] >= s.retry.min_estimated_seconds:
        assert j2["straight_km"] <= radius_km(30 * factor, s.radius) + 0.01


# ---- 추천 조건 (require_parking, exclude_paid) --------------------------------------
# 조건을 켜면 요청당 최대 30회(10곳 × 상세 2 + 요금 1)까지 호출할 수 있어, 이 앱의 누적 호출이 상한에 가까우면 건너뛴다.
COND_CALL_CAP = int(__import__("os").environ.get("INTEGRATION_COND_MAX_CALLS", "70"))


@pytest.mark.parametrize("loc", LOCATIONS, ids=[l["name"] for l in LOCATIONS])
def test_live_conditions(live_client, loc):
    q = {"lat": loc["lat"], "lng": loc["lng"], "drive_minutes": 30}
    plain = {k["id"]: k for k in live_client.get("/api/v1/keywords", params=q).json()["keywords"]}
    rk = live_client.get("/api/v1/keywords", params={**q, "require_parking": "true", "exclude_paid": "true"})
    assert rk.status_code == 200
    cond = {k["id"]: k for k in rk.json()["keywords"]}
    assert list(cond) == list(plain)
    for k, v in cond.items():                                   # 조건은 칩을 끄기만 한다
        assert not v["enabled"] or plain[k]["enabled"]
        if plain[k]["enabled"] and not v["enabled"]:
            assert v["reason"] == "조건에 맞는 곳이 적어요"
    tour = live_client.app.state.index.tour
    if tour.calls + 30 > COND_CALL_CAP:
        pytest.skip(f"관광 API 상한 보호 (이 앱 누적 {tour.calls}회)")
    rr = live_client.post("/api/v1/recommend", json={"latitude": loc["lat"], "longitude": loc["lng"],
                                                     "drive_minutes": 30, "keyword_ids": [],
                                                     "require_parking": True, "exclude_paid": True})
    if rr.status_code == 404:
        assert rr.json()["error"]["code"] == "CONDITION_NOT_MET"
        return
    assert rr.status_code == 200, rr.text
    j = rr.json()
    assert j["hint"]["parking"] == "가능" and j["hint"]["fee"] != "유료"


# ---- POST /api/v1/eta-check (2곳) ----------------------------------------------------
ETA_LOCS = [l for l in LOCATIONS if l["name"] in ("서울 강남", "대구")]


@pytest.mark.parametrize("loc", ETA_LOCS, ids=[l["name"] for l in ETA_LOCS])
def test_live_eta_check_flow(live_client, loc):
    """추천 → 시간 안 ETA 면 accept, 넘으면 retry(새 추천) → 3번째 초과면 fallback."""
    base = {"latitude": loc["lat"], "longitude": loc["lng"], "drive_minutes": 30, "keyword_ids": [],
            "require_parking": True}
    r1 = live_client.post("/api/v1/recommend", json=base).json()
    first = r1["reveal"]["content_id"]
    ok = live_client.post("/api/v1/eta-check", json={**base, "content_id": first, "previous_content_ids": [first],
                                                     "observed_eta_seconds": 1700}).json()
    assert (ok["decision"], ok["reason"], ok["route_content_id"], ok["attempt"]) == ("accept", "within_limit", first, 1)
    prev, j = [first], None
    for attempt in (2, 3):
        j = live_client.post("/api/v1/eta-check", json={**base, "content_id": prev[-1], "previous_content_ids": prev,
                                                        "observed_eta_seconds": 2400}).json()
        if j["decision"] == "fallback":                     # 더 가까운 곳이 없으면 정상적인 fallback
            assert j["reason"] in ("no_candidates", "condition_not_met") and j["route_content_id"] == prev[-1]
            return
        assert j["decision"] == "retry" and j["attempt"] == attempt
        new = j["recommendation"]
        assert new["reveal"]["content_id"] not in prev and new["hint"]["parking"] == "가능"   # 조건 유지
        prev.append(new["reveal"]["content_id"])
    last = live_client.post("/api/v1/eta-check", json={**base, "content_id": prev[-1], "previous_content_ids": prev,
                                                       "observed_eta_seconds": 2400}).json()
    assert (last["decision"], last["reason"], last["route_content_id"]) == ("fallback", "max_attempts", prev[-1])


# ---- 새벽(일출) 시간대 ------------------------------------------------------------
@pytest.fixture(scope="module")
def dawn_client(real_settings):
    """now_fn 을 바꿀 수 있는 실제 서버. 7곳 각각 '다음 날 일출 15분 전 도착'으로 맞춘다."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    clock = {}
    app = create_app(real_settings, now_fn=lambda: clock["now"])
    with TestClient(app) as c:
        yield c, clock
    print(f"\n[integration dawn] 관광 API 호출 {app.state.index.tour.calls}회")


@pytest.mark.parametrize("loc", LOCATIONS, ids=[l["name"] for l in LOCATIONS])
def test_live_sunrise_at_dawn(dawn_client, loc):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from app.sun import sunrise
    c, clock = dawn_client
    kst = ZoneInfo("Asia/Seoul")
    sr = sunrise((datetime.now(kst) + timedelta(days=1)).date(), loc["lat"], loc["lng"], kst)
    # 60분: 안동은 30분 안에 바다·물가·전망 후보가 1곳뿐이라 일출(노을과 같음)이 꺼진다 (2026-11-13 실측)
    clock["now"] = sr - timedelta(minutes=60 + 15)
    rk = c.get("/api/v1/keywords", params={"lat": loc["lat"], "lng": loc["lng"], "drive_minutes": 60})
    assert rk.status_code == 200
    ks = {k["id"]: k for k in rk.json()["keywords"]}
    assert list(ks)[6] == "sunrise" and len(ks) == 9
    assert ks["sunrise"]["enabled"], ks["sunrise"]
    assert not ks["sunset"]["enabled"] and not ks["night"]["enabled"]
    rr = c.post("/api/v1/recommend", json={"latitude": loc["lat"], "longitude": loc["lng"], "drive_minutes": 60,
                                           "keyword_ids": ["sunrise"]})
    assert rr.status_code == 200, rr.text
    j = rr.json()
    assert j["reveal"]["matched_keywords"] == ["sunrise"]
    assert 0 < j["estimated_drive_seconds"] <= 60 * 60
    clock["now"] = sr + timedelta(hours=3)                     # 낮 → 꺼짐, 사유에 일출 시각
    off = {k["id"]: k for k in c.get("/api/v1/keywords", params={"lat": loc["lat"], "lng": loc["lng"],
                                                                 "drive_minutes": 60}).json()["keywords"]}
    assert not off["sunrise"]["enabled"] and off["sunrise"]["reason"].startswith("일출은 ")
