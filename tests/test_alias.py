import asyncio
import json

import httpx
import pytest

from app.alias import Lexicon, place_words, validate
from conftest import NOW, base_words, make_alias, make_spot

SPOT = make_spot("126132", "대구앞산공원", addr1="대구광역시 남구 대명동 산227-1")


def lex(settings, spot=SPOT):
    return Lexicon(base_words(settings) | place_words(spot), settings.alias.allow_words)


def ok(settings, name, spot=SPOT):
    return validate(name, lex(settings, spot), settings.alias)


def run(coro):
    return asyncio.run(coro)


# ---- 검증 ----------------------------------------------------------------------
@pytest.mark.parametrize("hour", [9, 14, 18, 22])
def test_all_templates_pass(settings, hour):
    """고정 템플릿 + 시간대별 조합 템플릿 전부가 금지어·길이·반복 검증을 통과한다."""
    a = make_alias(settings, [])
    pool = a.template_pool(NOW.replace(hour=hour))
    assert len(pool) >= 300                                  # 반복을 줄이기 위한 풀 크기
    bad = [(t, ok(settings, t)[1]) for t in pool if ok(settings, t)[0] != t]
    assert not bad, bad


def test_template_uses_arrival_time_bucket(settings):
    a = make_alias(settings, [], seed=1)
    night = set(a.template_pool(NOW.replace(hour=22)))
    morning = set(a.template_pool(NOW.replace(hour=9)))
    assert "늦은 밤의 쉼표" in night and "늦은 밤의 쉼표" not in morning
    assert "아침을 여는 선물" in morning and "아침을 여는 선물" not in night


@pytest.mark.parametrize("name", ["비밀의 공간", "오늘의 힐링 공간", "아직 이름 없는 쉼표", "계절이 머무는 쉼표",
                                  "산들거리는 마음 쉼표", "기다리던 작은 선물"])
def test_abstract_names_allowed(settings, name):
    assert ok(settings, name)[0] == name


@pytest.mark.parametrize("name,hit", [
    ("앞산의 작은 비밀", "앞산"),        # 실제 장소명 조각
    ("대구의 숨은 쉼표", "대구"),        # 지명 / 장소명
    ("수성 어딘가의 비밀", "수성"),      # 시군구 어간
    ("대명 골목의 비밀", "대명"),        # 주소의 동
    ("고요한 바다 끝", "바다"),          # 유형어
    ("숲속의 비밀 이야기", "숲"),        # 한 글자 유형어(단어 앞)
    ("비밀스러운 작은섬", "섬"),         # 한 글자 유형어(단어 뒤)
    ("노을빛 쉼표 하나", "노을"),
    ("오늘 밤 야경 산책", "야경"),
    ("포근한 찜질방 하루", "찜질방"),    # 신분류 이름에만 있는 유형어
])
def test_forbidden_words(settings, name, hit):
    got, why = ok(settings, name)
    assert got is None and why.startswith("forbidden"), why
    assert hit in why or why.split(":")[1] in name


@pytest.mark.parametrize("name,why", [("쉼표", "length"), ("가" * 21, "length"), ("Secret 공간", "charset"),
                                      ("비밀의 공간 1", "charset"), ('"비밀의 공간"', "charset"), (None, "not_string"),
                                      ("밤의 위로 안들기 위로", "repeat")])
def test_format_rules(settings, name, why):
    assert ok(settings, name) == (None, why)


def test_place_words_bigrams():
    w = place_words(make_spot("1", "이월드 83타워", addr1="대구광역시 달서구 두류공원로 200 (두류동)"))
    assert {"이월드", "이월", "월드", "타워", "두류동", "두류"} <= w


# ---- 생성 ----------------------------------------------------------------------
def test_llm_success(settings):
    a = make_alias(settings, ["설렘이 머무는 자리"])
    r = run(a.generate(SPOT, NOW))
    assert (r.name, r.source, r.attempts) == ("설렘이 머무는 자리", "llm", 1)


def test_retry_then_success(settings):
    a = make_alias(settings, ["RAW:not json", "앞산의 비밀", "느긋한 오후의 선물"])
    r = run(a.generate(SPOT, NOW))
    assert r.source == "llm" and r.attempts == 3 and r.name == "느긋한 오후의 선물"
    assert r.reasons[0].startswith("bad_response") and r.reasons[1].startswith("forbidden")


def test_fallback_after_retries(settings):
    a = make_alias(settings, ["바다 끝", "대구 쉼표", "공원 산책길"])
    r = run(a.generate(SPOT, NOW))
    assert r.source == "template" and r.attempts == 3
    assert r.name in a.template_pool(NOW)


@pytest.mark.parametrize("exc,reason", [(httpx.ConnectError, "unavailable"), (httpx.ReadTimeout, "timeout")])
def test_fallback_when_ollama_down(settings, exc, reason):
    a = make_alias(settings, [exc])
    r = run(a.generate(SPOT, NOW))
    assert r.source == "template" and r.reasons == [reason] and r.attempts == 1


def test_recent_names_avoided(settings):
    a = make_alias(settings, ["설렘이 머무는 자리", "설렘이 머무는 자리", "설렘이 머무는 자리", "설렘이 머무는 자리"])
    assert run(a.generate(SPOT, NOW)).source == "llm"
    r2 = run(a.generate(SPOT, NOW))
    assert r2.source == "template" and "recent" in r2.reasons


def test_template_avoids_recent(settings):
    a = make_alias(settings, [], seed=3)
    lx = a.lexicon_for(SPOT)
    names = [a.template(lx) for _ in range(settings.alias.recent_size)]
    assert len(set(names)) == len(names)          # 최근 N개 안에서는 반복 없음


def test_prompt_has_no_place_info(settings):
    captured = []
    a = make_alias(settings, ["설렘이 머무는 자리"], captured)
    run(a.generate(SPOT, NOW))
    payload = json.dumps(captured[0], ensure_ascii=False)
    for leak in ("대구앞산공원", "앞산", "대명동", "126132", "근린공원"):
        assert leak not in payload
    assert "시간대: 오후" in captured[0]["prompt"] and "힐링" in captured[0]["prompt"]
    assert captured[0]["format"]["required"] == ["blind_name"]      # JSON 출력 강제
