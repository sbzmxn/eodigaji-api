"""실제 관광 API 통합 테스트 (호출 약 4회). 실행: pytest -m integration"""
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
    with TestClient(create_app(real_settings)) as c:
        yield c


@pytest.mark.parametrize("loc", LOCATIONS, ids=[l["name"] for l in LOCATIONS])
def test_live_keywords_and_recommend(live_client, loc):
    """실제 인덱스로 7곳 × 30분. 상세는 content_id 캐시라 재실행 시 호출이 거의 없다."""
    q = {"lat": loc["lat"], "lng": loc["lng"], "drive_minutes": 30}
    rk = live_client.get("/api/v1/keywords", params=q)
    assert rk.status_code == 200 and len(rk.json()["keywords"]) == 8
    rr = live_client.post("/api/v1/recommend", json={"latitude": loc["lat"], "longitude": loc["lng"],
                                                     "drive_minutes": 30, "keyword_ids": []})
    assert rr.status_code == 200, rr.text
    j = rr.json()
    assert j["blind_name"] and j["reveal"]["real_name"] not in j["blind_name"]
