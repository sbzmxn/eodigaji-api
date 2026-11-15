"""앱 → 서버 API 키 검사 (X-API-Key). 관광 API 는 부르지 않는다 (FakeIndex/FakeDetails)."""
import logging
import random

import pytest
from fastapi.testclient import TestClient

from app.config import parse_api_keys
from app.main import ApiKeyConfigError, create_app
from conftest import ANY_POINT, NOW, FakeDetails, FakeIndex, make_pool, make_spot

# 테스트 전용 가짜 키 (32자 이상)
KEY1 = "test-key-one-0123456789abcdefghijklmnop"
KEY2 = "test-key-two-0123456789abcdefghijklmnop"
WRONG = "test-key-bad-0123456789abcdefghijklmnop"
O = ANY_POINT
SPOTS = [make_spot("p1", "가람근린공원", north_km=4.0, base=O), make_spot("p2", "나래근린공원", east_km=4.0, base=O)]
BODY = {"latitude": O[0], "longitude": O[1], "drive_minutes": 15, "keyword_ids": [], "exclude_content_ids": []}
ETA_BODY = {**BODY, "content_id": "p1", "observed_eta_seconds": 600, "previous_content_ids": ["p1"]}


def make_client(settings, keys=""):
    settings.app_api_keys = parse_api_keys(keys)
    app = create_app(settings, index=FakeIndex(SPOTS), details=FakeDetails(), alias=make_pool(settings),
                     now_fn=lambda: NOW, rng=random.Random(1), load_on_startup=False)
    return TestClient(app)


def call(c, name, headers=None):
    if name == "keywords":
        return c.get("/api/v1/keywords", params={"lat": O[0], "lng": O[1], "drive_minutes": 15}, headers=headers)
    if name == "recommend":
        return c.post("/api/v1/recommend", json=BODY, headers=headers)
    return c.post("/api/v1/eta-check", json=ETA_BODY, headers=headers)


ENDPOINTS = ["keywords", "recommend", "eta-check"]


def assert_401(r):
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["code"] == "UNAUTHORIZED" and err["message"] == "API 키가 없거나 올바르지 않아요"
    assert err["request_id"]
    return err


@pytest.mark.parametrize("ep", ENDPOINTS)
def test_missing_key_401(settings, ep):
    with make_client(settings, f"{KEY1},{KEY2}") as c:
        assert_401(call(c, ep))


@pytest.mark.parametrize("ep", ENDPOINTS)
def test_wrong_key_401_same_message_as_missing(settings, ep):
    with make_client(settings, KEY1) as c:
        wrong = assert_401(call(c, ep, {"X-API-Key": WRONG}))
        missing = assert_401(call(c, ep))
        empty = assert_401(call(c, ep, {"X-API-Key": ""}))
        prefix = assert_401(call(c, ep, {"X-API-Key": KEY1[:-1]}))
    assert {e["message"] for e in (wrong, missing, empty, prefix)} == {"API 키가 없거나 올바르지 않아요"}


@pytest.mark.parametrize("ep", ENDPOINTS)
def test_correct_key_200(settings, ep):
    with make_client(settings, f"{KEY1},{KEY2}") as c:
        r = call(c, ep, {"X-API-Key": KEY1})
    assert r.status_code == 200, r.text


def test_second_key_also_passes(settings):
    with make_client(settings, f" {KEY1} , {KEY2} ") as c:   # 공백은 무시
        for ep in ENDPOINTS:
            assert call(c, ep, {"X-API-Key": KEY2}).status_code == 200


def test_unknown_api_path_needs_key(settings):
    """없는 /api 경로도 키가 없으면 401 (어떤 경로가 있는지 알려 주지 않기)."""
    with make_client(settings, KEY1) as c:
        assert_401(c.get("/api/v1/nope"))
        assert c.get("/api/v1/nope", headers={"X-API-Key": KEY1}).status_code == 404


def test_disabled_allows_without_key_and_warns_once(settings, caplog):
    caplog.set_level(logging.WARNING)
    with make_client(settings, "") as c:
        for ep in ENDPOINTS:
            assert call(c, ep).status_code == 200
        assert c.get("/docs").status_code == 200 and c.get("/openapi.json").status_code == 200
    warns = [x for x in caplog.records if "APP_API_KEYS 가 비어 있어 API 키 검사를 하지 않습니다" in x.getMessage()]
    assert len(warns) == 1 and warns[0].levelno == logging.WARNING


def test_blank_entries_mean_disabled(settings):
    with make_client(settings, " , ,") as c:
        assert call(c, "recommend").status_code == 200


def test_healthz_without_key(settings):
    with make_client(settings, KEY1) as c:
        r = c.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_docs_disabled_when_check_on(settings):
    with make_client(settings, KEY1) as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert c.get(path).status_code == 404
            assert c.get(path, headers={"X-API-Key": KEY1}).status_code == 404


@pytest.mark.parametrize("keys", ["short-key", f"{KEY1},abc", "a" * 31])
def test_short_key_refuses_start(settings, caplog, keys):
    caplog.set_level(logging.ERROR)
    with pytest.raises(ApiKeyConfigError) as ei:
        make_client(settings, keys)
    logs = "\n".join(x.getMessage() for x in caplog.records)
    assert "32자 미만" in logs and "32자 미만" in str(ei.value)
    for k in keys.split(","):   # 거부 사유에도 키 값은 없다
        assert k not in logs and k not in str(ei.value)


def test_exactly_32_chars_ok(settings):
    k = "k" * 32
    with make_client(settings, k) as c:
        assert call(c, "recommend", {"X-API-Key": k}).status_code == 200


def test_key_values_never_logged(settings, caplog):
    caplog.set_level(logging.DEBUG)
    with make_client(settings, f"{KEY1},{KEY2}") as c:
        for ep in ENDPOINTS:
            call(c, ep)
            call(c, ep, {"X-API-Key": WRONG})
            call(c, ep, {"X-API-Key": KEY1})
            call(c, ep, {"X-API-Key": KEY2})
        c.post("/api/v1/recommend", json={"bad": 1}, headers={"X-API-Key": KEY1})   # 검증 오류 로그
    logs = "\n".join(x.getMessage() for x in caplog.records)
    for k in (KEY1, KEY2, WRONG):
        assert k not in logs
    unauth = [x.getMessage() for x in caplog.records if x.getMessage().startswith("unauthorized")]
    assert unauth and all("rid=" in m and "path=/api/v1/" in m for m in unauth)
    assert "code=UNAUTHORIZED" in logs   # 요청 메타 로그에도 결과 코드만


def test_settings_repr_hides_keys(settings):
    settings.app_api_keys = parse_api_keys(f"{KEY1},{KEY2}")
    assert KEY1 not in repr(settings) and KEY2 not in str(settings)
