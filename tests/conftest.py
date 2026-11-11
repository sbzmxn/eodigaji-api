import json
import math
import random
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.alias import AliasGenerator, build_region_words, build_type_words
from app.config import load_settings
from app.spots import Detail, RefData, Spot, SpotIndex

KST = ZoneInfo("Asia/Seoul")
DATA = Path(__file__).parent / "data"
# 통합 확인용 출발 좌표 7곳 (특정 지역을 기본값으로 두지 않는다)
LOCATIONS = json.loads((DATA / "locations.json").read_text(encoding="utf-8"))
LOC_IDS = [l["name"] for l in LOCATIONS]
# 좌표가 결과에 영향을 주지 않는 단위 테스트용 임의 점
ANY_POINT = (36.0, 128.0)
LCLS_NAMES = json.loads((DATA / "lcls_names.json").read_text(encoding="utf-8"))
REGIONS = ["대구광역시", "남구", "수성구", "중구", "달서구", "서울특별시", "종로구", "안동시", "영광군", "경상북도"]
# 2026-11-11 은 수요일
NOW = datetime(2026, 11, 11, 14, 0, tzinfo=KST)


@pytest.fixture
def settings(tmp_path):
    s = load_settings(env_file=None)
    s.cache.dir = str(tmp_path / "cache")
    return s


def make_spot(cid, title, north_km=0.0, east_km=0.0, lcls3="VE030400", regn="", image="https://img/x.jpg",
              cpyrht="Type1", addr1="가나시 다라구 마바동 1", base=ANY_POINT, modified="20260101000000", signgu="200"):
    lat = base[0] + north_km / 111.0
    lng = base[1] + east_km / (111.0 * math.cos(math.radians(base[0])))
    return Spot(content_id=cid, title=title, lat=lat, lng=lng, addr1=addr1, lcls1=lcls3[:2], lcls2=lcls3[:4],
                lcls3=lcls3, image=image, cpyrht=cpyrht, regn=regn, signgu=signgu, modified=modified)


class FakeIndex:
    def __init__(self, spots):
        self.index = SpotIndex(spots)
        self.ref = RefData(lcls_names=LCLS_NAMES, region_names=REGIONS)

    async def near(self, lat, lng, r):
        return self.index.within(lat, lng, r)

    async def ensure_index(self):
        return self.index

    async def load_ref(self):
        return self.ref


DEFAULT_DETAIL = Detail(overview="조용히 쉬기 좋은 곳이다.", parking="가능", restdate="연중무휴", usetime="상시 개방")


class FakeDetails:
    def __init__(self, details=None, fee="무료"):
        self.d = details or {}
        self.fee_val = fee
        self.calls = []

    def peek(self, spot):
        return None

    async def get(self, spot):
        self.calls.append(spot.content_id)
        return self.d.get(spot.content_id, DEFAULT_DETAIL)

    async def fee(self, spot):
        return self.fee_val


def ollama_transport(answers, captured=None):
    """answers: 문자열(blind_name), dict(원시 응답 JSON), 'RAW:<text>', 또는 예외 클래스."""
    it = iter(answers)

    def handler(request: httpx.Request):
        if captured is not None:
            captured.append(json.loads(request.content))
        a = next(it)
        if isinstance(a, type) and issubclass(a, Exception):
            raise a("mock", request=request)
        if isinstance(a, str) and a.startswith("RAW:"):
            return httpx.Response(200, json={"response": a[4:]})
        return httpx.Response(200, json={"response": json.dumps({"blind_name": a}, ensure_ascii=False)})

    return httpx.MockTransport(handler)


def base_words(settings):
    return build_type_words(settings.alias, settings.keywords, LCLS_NAMES) | build_region_words(REGIONS)


def make_alias(settings, answers, captured=None, seed=0, mode="llm"):
    client = httpx.AsyncClient(transport=ollama_transport(answers, captured))
    return AliasGenerator(settings.alias, base_words(settings), client=client, rng=random.Random(seed), mode=mode)
