"""블라인드 가명 생성 (Ollama) + 코드 검증 + 템플릿 폴백.

원칙: 가명으로 목적지를 예측할 수 없어야 한다.
- LLM 입력: 시간대(오전/오후/저녁/밤), '힐링' 분위기, 무작위 느낌 단어. 장소명·소개·분류·키워드는 넣지 않는다.
- 금지어: 실제 장소명과 그 구성(2글자 조각), 지명(시·도·시·군·구 이름과 어간, 주소의 동·읍·면·리),
  장소 유형어(키워드 표시어·매칭 단어, 신분류 이름 전체, config/alias.yaml extra_forbidden).
- 실패 시 최대 max_retries 회 재시도, 전체 시간 예산 timeout_s. 그래도 실패하면 템플릿 풀에서 무작위.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

import httpx

from .config import AliasCfg, KeywordsCfg
from .spots import Spot

log = logging.getLogger("eodigaji.alias")

_HANGUL_RUN = re.compile(r"[가-힣]+")
_SPLIT = re.compile(r"[/,·‧ㆍ.()\[\]\s]+")
_REGION_SUFFIX = re.compile(r"(통합특별시|특별자치시|특별자치도|특별시|광역시|자치시|자치도|도|시|군|구)$")
_ADDR_UNIT = re.compile(r"^[가-힣0-9]+(동|읍|면|리|가)$")
_JOSA = sorted(["의", "에", "에서", "로", "으로", "은", "는", "이", "가", "을", "를", "과", "와", "도", "만", "까지", "처럼"],
               key=len, reverse=True)

SCHEMA = {
    "type": "object",
    "properties": {"blind_name": {"type": "string"}},
    "required": ["blind_name"],
}

SYSTEM_PROMPT = (
    "너는 '어디가지?' 드라이브 앱의 작명가다. 목적지를 숨긴 채 떠나는 드라이브에 붙일 짧은 한국어 이름을 하나 짓는다.\n"
    "규칙:\n"
    "1) 장소의 종류(바다, 강, 호수, 숲, 산, 공원, 절, 전망대, 계곡, 마을 등), 지명, 풍경, 날씨를 떠올리게 하는 단어는 절대 쓰지 않는다.\n"
    "2) '비밀의 공간', '오늘의 힐링 공간', '아직 이름 없는 쉼표'처럼 추상적이고 따뜻한 표현만 쓴다.\n"
    "3) 한글만 쓰고 5~20자로 짓는다. 숫자, 영어, 이모지, 따옴표는 쓰지 않는다.\n"
    '4) {"blind_name": "..."} 형식의 JSON 하나만 출력한다.'
)


def time_of_day(dt: datetime) -> str:
    h = dt.hour
    if 5 <= h < 11:
        return "오전"
    if 11 <= h < 17:
        return "오후"
    if 17 <= h < 20:
        return "저녁"
    return "밤"


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
    words: set[str]
    allow: list[str] = field(default_factory=list)

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


# ---- 생성기 ---------------------------------------------------------------
@dataclass
class AliasResult:
    name: str
    source: str                      # "llm" | "template"
    attempts: int = 0
    reasons: list[str] = field(default_factory=list)


class AliasGenerator:
    def __init__(self, cfg: AliasCfg, base_words: set[str], client: Optional[httpx.AsyncClient] = None,
                 rng: Optional[random.Random] = None, mode: str = "llm"):
        self.cfg = cfg
        self.mode = mode                 # "template" 이면 LLM 을 부르지 않는다
        self.base_words = base_words
        self._client = client or httpx.AsyncClient()
        self.rng = rng or random.Random()
        self.recent: deque[str] = deque(maxlen=cfg.recent_size)

    def set_base_words(self, words: set[str]) -> None:
        self.base_words = words

    async def aclose(self) -> None:
        await self._client.aclose()

    async def warm(self) -> bool:
        """모델을 GPU 메모리에 미리 올린다 (첫 요청이 로딩 시간 때문에 템플릿으로 떨어지지 않게)."""
        if self.mode != "llm":
            return False
        try:
            r = await self._client.post(f"{self.cfg.ollama_url}/api/generate",
                                        json={"model": self.cfg.model, "keep_alive": "30m"}, timeout=120)
            r.raise_for_status()
            return True
        except httpx.HTTPError as e:
            log.warning("ollama warm-up failed: %s", type(e).__name__)
            return False

    def lexicon_for(self, spot: Spot) -> Lexicon:
        return Lexicon(self.base_words | place_words(spot), self.cfg.allow_words)

    def _prompt(self, arrival: datetime) -> str:
        mood = self.rng.choice(self.cfg.moods) if self.cfg.moods else "쉼"
        avoid = ", ".join(list(self.recent)[-3:])
        p = f"시간대: {time_of_day(arrival)}\n분위기: 힐링\n느낌 단어: {mood}\n"
        if avoid:
            p += f"이미 쓴 이름(다시 쓰지 말 것): {avoid}\n"
        return p + "새 이름을 지어줘."

    async def _ask(self, prompt: str, timeout: float) -> object:
        r = await self._client.post(
            f"{self.cfg.ollama_url}/api/generate",
            json={
                "model": self.cfg.model,
                "system": SYSTEM_PROMPT,
                "prompt": prompt,
                "format": SCHEMA,
                "stream": False,
                "keep_alive": "30m",
                "options": {"temperature": self.cfg.temperature, "num_predict": 48},
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return json.loads(r.json().get("response", "")).get("blind_name")

    async def generate(self, spot: Spot, arrival: datetime) -> AliasResult:
        deadline = time.monotonic() + self.cfg.timeout_s
        lex = self.lexicon_for(spot)
        if self.mode != "llm":
            return AliasResult(self.template(lex, arrival), "template", 0, ["mode:template"])
        reasons: list[str] = []
        attempts = 0
        for _ in range(1 + self.cfg.max_retries):
            remaining = deadline - time.monotonic()
            if remaining < 0.3:
                reasons.append("budget")
                break
            attempts += 1
            try:
                raw = await self._ask(self._prompt(arrival), remaining)
            except httpx.TimeoutException:
                reasons.append("timeout")
                break
            except httpx.TransportError:
                reasons.append("unavailable")
                break
            except (httpx.HTTPStatusError, ValueError, AttributeError) as e:
                reasons.append(f"bad_response:{type(e).__name__}")
                continue
            name, why = validate(raw, lex, self.cfg, self.recent)
            if name:
                self.recent.append(name)
                return AliasResult(name, "llm", attempts, reasons)
            reasons.append(why or "invalid")
        name = self.template(lex, arrival)
        return AliasResult(name, "template", attempts, reasons)

    def template_pool(self, arrival: Optional[datetime] = None) -> list[str]:
        """고정 템플릿 + (공통·시간대 수식어 × 감성 명사) 조합."""
        mods = list(self.cfg.template_modifiers.get("any", []))
        if arrival is not None:
            mods += self.cfg.template_modifiers.get(time_of_day(arrival), [])
        combos = [f"{m} {n}" for m in mods for n in self.cfg.template_nouns]
        return list(dict.fromkeys(self.cfg.templates + combos))

    def template(self, lex: Lexicon, arrival: Optional[datetime] = None) -> str:
        """금지어 검증을 통과하고 최근에 안 쓴 템플릿을 무작위로 고른다."""
        pool = [t for t in self.template_pool(arrival) if validate(t, lex, self.cfg)[0]]
        fresh = [t for t in pool if t not in self.recent] or pool or ["비밀의 공간"]
        name = self.rng.choice(fresh)
        self.recent.append(name)
        return name
