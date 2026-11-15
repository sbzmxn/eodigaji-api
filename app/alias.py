"""블라인드 가명: 사람이 검수한 100개 목록(config/alias_pool.yaml)에서 무작위로 고른다. 실행 중 LLM 은 쓰지 않는다.

원칙: 가명으로 목적지를 예측할 수 없어야 한다.
- 금지어 검증: 장소 유형어(키워드 표시어·매칭 단어, 신분류 이름 전체, config/alias.yaml extra_forbidden)와
  시·도·시·군·구 이름(부분 문자열), 읍·면·동·리 이름과 어간(어절 단위), 한글·공백 5~20자, 같은 어절 반복 금지.
- 서버 시작 시 목록 전체를 검증하고 하나라도 걸리면 시작하지 않는다.
- 고를 때는 선택된 장소의 실제 이름 조각·주소의 동 이름과 겹치는 가명, 최근 N개(메모리에만)를 피한다.
- 목록 후보는 scripts/generate_alias_pool.py 로 한 번 생성해 팀이 검수했다. 앱도 같은 목록을 앱 안에 두고 쓴다.
"""
from __future__ import annotations

import logging
import random
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .config import AliasCfg, KeywordsCfg
from .spots import Spot

log = logging.getLogger("eodigaji.alias")

_HANGUL_RUN = re.compile(r"[가-힣]+")
_SPLIT = re.compile(r"[/,·‧ㆍ.()\[\]\s]+")
_REGION_SUFFIX = re.compile(r"(통합특별시|특별자치시|특별자치도|특별시|광역시|자치시|자치도|도|시|군|구)$")
_ADDR_UNIT = re.compile(r"^[가-힣0-9]+(동|읍|면|리|가)$")
_UNIT_NAME = re.compile(r"^([가-힣]{1,8})(동|읍|면|리|가)$")
_JOSA = sorted(["의", "에", "에서", "로", "으로", "은", "는", "이", "가", "을", "를", "과", "와", "도", "만", "까지", "처럼"],
               key=len, reverse=True)


# ---- 금지어 사전 ----------------------------------------------------------
def build_type_words(cfg: AliasCfg, kw: KeywordsCfg, lcls_names: dict[str, str]) -> set[str]:
    words: set[str] = set(cfg.extra_forbidden)
    for k in kw.keywords.values():
        words.update(_SPLIT.split(k.label))
        words.update(k.title)
        words.update(k.text)
    for name in lcls_names.values():
        words.update(_SPLIT.split(name))
    return {w for w in words if w and _HANGUL_RUN.fullmatch(w)}


def build_region_words(region_names: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for n in region_names:
        n = n.strip()
        if not n:
            continue
        out.add(n)
        stem = _REGION_SUFFIX.sub("", n)
        if len(stem) >= 2:
            out.add(stem)
    return out


def build_unit_words(spots: Iterable[Spot]) -> set[str]:
    """관광지 주소에서 읍·면·동·리 이름과 어간(2글자 이상). 어절 단위로만 검사한다."""
    out: set[str] = set()
    for s in spots:
        for tok in re.split(r"[\s()\[\],]+", f"{s.addr1} {s.addr2}"):
            m = _UNIT_NAME.match(tok)
            if m and len(tok) >= 2:
                out.add(tok)
                if len(m.group(1)) >= 2:
                    out.add(m.group(1))
    return out


def place_words(spot: Spot) -> set[str]:
    """실제 장소명 조각(2글자 이상 연속 한글, 그 2-gram)과 주소의 동·읍·면·리."""
    out: set[str] = set()
    for run in _HANGUL_RUN.findall(spot.title):
        if len(run) >= 2:
            out.add(run)
            out.update(run[i:i + 2] for i in range(len(run) - 1))
    for tok in re.split(r"[\s()\[\],]+", f"{spot.addr1} {spot.addr2}"):
        if _ADDR_UNIT.match(tok) and _HANGUL_RUN.search(tok):
            out.add(tok)
            stem = tok[:-1]
            if len(stem) >= 2 and _HANGUL_RUN.fullmatch(stem):
                out.add(stem)
    return out


def _strip_josa(tok: str) -> str:
    for j in _JOSA:
        if len(tok) > len(j) and tok.endswith(j):
            return tok[: -len(j)]
    return tok


@dataclass
class Lexicon:
    words: set[str]                                          # 부분 문자열로 검사 (장소 유형어, 시·도·시·군·구)
    allow: list[str] = field(default_factory=list)
    token_words: set[str] = field(default_factory=set)       # 어절 단위로 검사 (읍·면·동·리 이름과 어간)

    def violation(self, name: str) -> Optional[str]:
        masked = name
        for a in sorted(self.allow, key=len, reverse=True):
            masked = masked.replace(a, " ")
        squeezed = masked.replace(" ", "")
        singles = {w for w in self.words if len(w) == 1}
        for w in self.words:
            if len(w) >= 2 and (w in masked or w in squeezed):
                return w
        for tok in masked.split():
            core = _strip_josa(tok)
            for c in {tok[0], core[0], core[-1]}:
                if c in singles:
                    return c
            plural = core[:-1] if len(core) > 1 and core.endswith("들") else core
            if tok in self.token_words or core in self.token_words or plural in self.token_words:
                return core
            if len(plural) < len(core) and (plural[0] in singles or plural[-1] in singles):
                return plural
        return None


def validate(name: object, lex: Lexicon, cfg: AliasCfg, recent: Iterable[str] = ()) -> tuple[Optional[str], Optional[str]]:
    """(정규화된 이름, 실패 사유). 성공이면 사유 None."""
    if not isinstance(name, str):
        return None, "not_string"
    n = re.sub(r"\s+", " ", name).strip()
    if not n or not re.fullmatch(r"[가-힣 ]+", n):
        return None, "charset"
    if not cfg.min_len <= len(n) <= cfg.max_len:
        return None, "length"
    cores = [_strip_josa(t) for t in n.split()]
    if len(set(cores)) != len(cores):
        return None, "repeat"                       # 예: '밤의 위로 안들기 위로'
    w = lex.violation(n)
    if w:
        return None, f"forbidden:{w}"
    if n in set(recent):
        return None, "recent"
    return n, None


# ---- 목록에서 고르기 --------------------------------------------------------
class AliasPoolError(ValueError):
    """가명 목록이 검증을 통과하지 못함 → 서버를 시작하지 않는다."""


class AliasPool:
    def __init__(self, cfg: AliasCfg, base_words: set[str], rng: Optional[random.Random] = None):
        self.cfg = cfg
        self.base_words = base_words
        self.unit_words: set[str] = set()
        self.rng = rng or random.Random()
        self.recent: deque[str] = deque(maxlen=cfg.recent_size)   # 메모리에만, 저장하지 않음

    def lexicon(self) -> Lexicon:
        return Lexicon(self.base_words, self.cfg.allow_words, self.unit_words)

    def check(self) -> None:
        """목록 전체를 금지어 검증. 걸리는 이름이 있으면 로그를 남기고 AliasPoolError."""
        names = self.cfg.pool
        lex = self.lexicon()
        bad = [(n, validate(n, lex, self.cfg)[1]) for n in names]
        bad = [(n, why) for n, why in bad if why]
        dup = sorted({n for n in names if names.count(n) > 1})
        if not names:
            raise AliasPoolError("가명 목록(config/alias_pool.yaml)이 비어 있습니다")
        if bad or dup:
            for n, why in bad:
                log.error("alias pool rejected: %s (%s)", n, why)
            for n in dup:
                log.error("alias pool duplicate: %s", n)
            raise AliasPoolError(f"가명 목록 검증 실패 {len(bad) + len(dup)}건: "
                                 + ", ".join(f"{n}({w})" for n, w in bad) + (f" 중복 {dup}" if dup else ""))

    def update_lexicon(self, base_words: set[str], unit_words: set[str]) -> None:
        """참조 데이터(신분류·시군구·읍면동)를 받은 뒤 사전을 넓히고 다시 검증한다."""
        self.base_words = base_words
        self.unit_words = unit_words
        self.check()

    def pick(self, spot: Spot) -> str:
        """선택된 장소의 이름·동 이름과 겹치지 않고 최근 N개에 없는 이름을 무작위로 고른다."""
        lex = Lexicon(place_words(spot), self.cfg.allow_words)
        ok = [n for n in self.cfg.pool if lex.violation(n) is None]
        fresh = [n for n in ok if n not in self.recent] or ok or list(self.cfg.pool)
        name = self.rng.choice(fresh)
        self.recent.append(name)
        return name
