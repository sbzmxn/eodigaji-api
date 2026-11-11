"""좌표·키·쿼리가 로그와 캐시에 남지 않는지."""
import asyncio
import logging
import random

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.cache import DiskCache
from app.main import AccessLogSanitizer, create_app
from app.spots import DetailService, IndexManager
from app.tour_client import TourApiError, TourClient, mask_url
from conftest import LOC_IDS, LOCATIONS, NOW, make_alias, make_spot

KEY = "SECRET-KEY-123"


def test_access_log_strips_query_and_client():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5555", "GET", "/api/v1/keywords?lat=35.84&lng=128.58&drive_minutes=30", "1.1", 200),
                            None)
    AccessLogSanitizer().filter(rec)
    msg = rec.getMessage()
    assert "35.84" not in msg and "1.2.3.4" not in msg and "/api/v1/keywords" in msg


def test_mask_url():
    u = mask_url(f"https://x/anyOperation?serviceKey={KEY}&mapX=128.5&mapY=35.8&radius=5000&pageNo=1")
    assert KEY not in u and "128.5" not in u and "35.8" not in u and "pageNo=1" in u


def test_upstream_error_message_has_no_key(settings):
    settings.tour_kor_key = SecretStr(KEY)

    def handler(request):
        return httpx.Response(200, json={"resultCode": "30", "resultMsg": f"SERVICE KEY IS NOT REGISTERED {KEY}"})

    tc = TourClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(TourApiError) as ei:
        asyncio.run(tc.call("detailCommon2", contentId="1"))
    assert KEY not in str(ei.value)


class FakeTour:
    """외부로 나가는 파라미터를 기록한다."""

    def __init__(self, spots):
        self.items = [s.to_item() for s in spots]
        self.params = []

    async def list_all(self, page_size, **filters):
        self.params.append(filters)
        return self.items

    async def detail_common(self, cid):
        return {"overview": "조용한 곳이다."}

    async def detail_intro(self, cid):
        return {"parking": "가능", "restdate": "연중무휴", "usetime": "상시"}

    async def detail_info(self, cid):
        return [{"infoname": "입장료", "infotext": "무료"}]

    async def region_names(self):
        return [{"lDongRegnNm": "가나도", "lDongSignguNm": "다라시"}]

    async def lcls_names(self):
        return [{"lclsSystm3Cd": "VE030400", "lclsSystm3Nm": "근린공원"}]


@pytest.mark.parametrize("loc", LOCATIONS, ids=LOC_IDS)
def test_coordinates_never_cached_or_logged_or_sent(settings, tmp_path, caplog, loc):
    o = (loc["lat"], loc["lng"])
    spots = [make_spot(f"p{i}", f"쉼터{i}", north_km=3.0 + i * 0.4, east_km=1.3, lcls3="VE030400", base=o)
             for i in range(4)]
    tour = FakeTour(spots)
    cache = DiskCache(settings.cache_dir)
    app = create_app(settings, index=IndexManager(settings, tour, cache), details=DetailService(settings, tour, cache),
                     alias=make_alias(settings, ["설렘이 머무는 자리"] * 5), now_fn=lambda: NOW,
                     rng=random.Random(0), load_on_startup=False)
    caplog.set_level(logging.DEBUG)
    with TestClient(app) as c:
        r = c.post("/api/v1/recommend", json={"latitude": o[0], "longitude": o[1], "drive_minutes": 15,
                                              "keyword_ids": ["walk"], "exclude_content_ids": []})
        assert r.status_code == 200, r.text
        c.get("/api/v1/keywords", params={"lat": o[0], "lng": o[1], "drive_minutes": 15})

    lat_s, lng_s = str(o[0]), str(o[1])
    # 1) 외부 API 로 사용자 좌표를 보내지 않는다
    assert tour.params == [{}]
    # 2) 캐시 파일 어디에도 사용자 좌표가 없다
    files = list(settings.cache_dir.rglob("*.json"))
    assert files
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert lat_s not in text and lng_s not in text, f
    # 3) 로그에 좌표·키워드 조합이 없다
    logs = caplog.text
    assert lat_s not in logs and lng_s not in logs and "walk" not in logs
    req_lines = [r.getMessage() for r in caplog.records if r.name == "eodigaji.request"]
    assert len(req_lines) == 2 and all("rid=" in l and "code=" in l for l in req_lines)
