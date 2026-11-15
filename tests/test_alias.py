"""가명: 검수된 100개 목록(config/alias_pool.yaml) + 금지어 검증."""
import logging
from pathlib import Path

import pytest

from app.alias import AliasPoolError, Lexicon, build_unit_words, place_words, validate
from conftest import base_words, make_pool, make_spot

ROOT = Path(__file__).resolve().parent.parent
SPOT = make_spot("126132", "대구앞산공원", addr1="대구광역시 남구 대명동 산227-1")


def lex(settings, spot=SPOT):
    return Lexicon(base_words(settings) | place_words(spot), settings.alias.allow_words)


def ok(settings, name, spot=SPOT):
    return validate(name, lex(settings, spot), settings.alias)


# ---- 목록 자체 -------------------------------------------------------------------
def test_pool_is_100_unique(settings):
    pool = settings.alias.pool
    assert len(pool) == 100 and len(set(pool)) == 100


def test_pool_passes_server_validation(settings):
    """100개 전체를 서버 금지어 검증(장소 유형어·신분류 이름·시군구, 형식·길이·반복)으로 다시 검사한다."""
    lx = Lexicon(base_words(settings), settings.alias.allow_words)
    bad = [(n, validate(n, lx, settings.alias)[1]) for n in settings.alias.pool]
    assert not [b for b in bad if b[1]], [b for b in bad if b[1]]
    make_pool(settings).check()                       # 서버 시작 검사와 같은 경로


# ---- 서버 시작 검사 ----------------------------------------------------------------
def test_bad_pool_is_rejected_with_names_logged(settings, caplog):
    settings.alias.pool = ["비밀의 공간", "바다가 보이는 쉼터", "비밀의 공간"]
    caplog.set_level(logging.ERROR)
    with pytest.raises(AliasPoolError):
        make_pool(settings).check()
    assert "바다가 보이는 쉼터" in caplog.text and "duplicate: 비밀의 공간" in caplog.text


def test_unit_words_checked_per_token(settings):
    """읍·면·동 이름은 어절 단위로만 검사한다 ('평온의'는 '온의동' 어간에 걸리지 않음). '지도'는 예외 단어."""
    pool = make_pool(settings)
    settings.alias.pool = ["평온의 작은 쉼", "지도에 없는 쉼표"]
    pool.update_lexicon(base_words(settings), {"온의", "온의동", "지도", "숨은"})
    settings.alias.pool = ["숨은 웃음"]                 # '숨은'이 동 이름(어간)과 같은 어절
    with pytest.raises(AliasPoolError):
        pool.check()


def test_build_unit_words():
    spots = [make_spot("1", "가", addr1="강원특별자치도 춘천시 온의동 1"), make_spot("2", "나", addr1="전남 신안군 지도읍 읍내리")]
    w = build_unit_words(spots)
    assert {"온의동", "온의", "지도읍", "지도", "읍내리", "읍내"} <= w


# ---- 고르기 ---------------------------------------------------------------------
def test_pick_avoids_recent_10(settings):
    pool = make_pool(settings, seed=3)
    names = [pool.pick(SPOT) for _ in range(200)]
    for i in range(len(names)):
        assert names[i] not in names[max(0, i - settings.alias.recent_size):i]
    assert set(names) <= set(settings.alias.pool)


def test_pick_avoids_spot_name_pieces(settings):
    spot = make_spot("x", "비밀숲 오늘정원", addr1="가나시 다라구 마바동 1")
    pool = make_pool(settings, seed=1)
    for _ in range(300):
        n = pool.pick(spot)
        assert "비밀" not in n and "오늘" not in n


# ---- 검증 규칙 -------------------------------------------------------------------
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


def test_no_runtime_llm_code():
    """실행 중 LLM 호출 코드가 서버에 남아 있지 않다."""
    src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "app").glob("*.py"))
    for word in ("ollama", "11434", "/api/generate", "AliasGenerator", "alias_mode"):
        assert word not in src.lower() if word.islower() else word not in src, word
