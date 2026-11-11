"""힐링 키워드 매칭과 노을·야경 시간 판정.

점수 규칙 (config/keywords.yaml, config/settings.yaml scoring):
- 일반 키워드: 신분류 일치 kw_class(1.0) / 제목·소개글 단어 일치 kw_text(0.5)
- 노을·야경: base 키워드의 '분류' 일치가 필수(time_base 0.5), text 일치 시 time_text(0.5) 가산.
  소개글은 상세 캐시가 있을 때만 쓰인다 (overview 인자).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .config import KeywordDef, KeywordsCfg, Landmass, ScoringCfg, TimeWindowCfg
from .spots import Spot


def is_excluded(spot: Spot, prefixes: list[str]) -> bool:
    return any(spot.lcls3.startswith(p) for p in prefixes)


def landmass_of(spot: Spot, groups: list[Landmass]) -> Optional[str]:
    """다리로 이어지지 않은 땅 이름. 육지(또는 연결된 섬)면 None."""
    for g in groups:
        if g.regn and spot.regn not in g.regn:
            continue
        if g.regn_signgu and f"{spot.regn}_{spot.signgu}" not in g.regn_signgu:
            continue
        addr = f"{spot.addr1} {spot.addr2}"
        if g.addr_contains and not any(w in addr for w in g.addr_contains):
            continue
        if any(w in addr for w in g.addr_exclude):
            continue
        return g.name
    return None


def class_match(kw: KeywordDef, spot: Spot, cfg: KeywordsCfg) -> bool:
    inland = not spot.coastal          # 강·호수의 섬 여부 (spots.mark_island_coast 에서 계산)
    if spot.lcls3 in kw.inland_exclude_lcls and inland:
        return False
    if spot.lcls3 in kw.inland_only_lcls:
        return inland
    return spot.lcls3 in kw.lcls


def keyword_score(kid: str, spot: Spot, cfg: KeywordsCfg, sc: ScoringCfg, overview: str = "") -> float:
    kw = cfg.keywords[kid]
    if kw.time_window:
        base = any(class_match(cfg.keywords[b], spot, cfg) for b in kw.base) or spot.lcls3 in kw.extra_lcls
        if not base:
            return 0.0
        text = f"{spot.title} {overview}"
        return sc.time_base + (sc.time_text if any(w in text for w in kw.text) else 0.0)
    if class_match(kw, spot, cfg):
        return sc.kw_class
    text = f"{spot.title} {overview}"
    if any(w in text for w in kw.title):
        return sc.kw_text
    return 0.0


def match_keywords(spot: Spot, kids: list[str], cfg: KeywordsCfg, sc: ScoringCfg, overview: str = "") -> dict[str, float]:
    out = {}
    for k in kids:
        v = keyword_score(k, spot, cfg, sc, overview)
        if v > 0:
            out[k] = v
    return out


def combined_keyword_score(matches: dict[str, float], sc: ScoringCfg) -> float:
    if not matches:
        return 0.0
    return max(matches.values()) + sc.multi_bonus * (len(matches) - 1)


def theme_of(spot: Spot, matched: list[str], cfg: KeywordsCfg, lcls_names: dict[str, str]) -> Optional[str]:
    """언박싱 때 공개하는 테마. 매칭 키워드 표시어 우선, 없으면 신분류 이름."""
    if matched:
        return "·".join(cfg.keywords[k].label for k in matched)
    for code in (spot.lcls3, spot.lcls2):
        if code and code in lcls_names:
            return lcls_names[code]
    for kid, kw in cfg.keywords.items():  # 키워드 없이 뽑았을 때도 해당 분류면 표시
        if not kw.time_window and class_match(kw, spot, cfg):
            return kw.label
    return None


# ---- 시간 판정 -----------------------------------------------------------
@dataclass
class TimeStatus:
    active: bool
    reason: Optional[str] = None


def _hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def time_status(kw: KeywordDef, arrival: datetime, sunset_dt: Optional[datetime], tw: TimeWindowCfg) -> TimeStatus:
    if not kw.time_window:
        return TimeStatus(True)
    if sunset_dt is None:
        return TimeStatus(False, "일몰 시각을 계산할 수 없어요")
    if kw.time_window == "sunset":
        lo = sunset_dt - timedelta(minutes=tw.sunset_before_min)
        hi = sunset_dt + timedelta(minutes=tw.sunset_after_min)
        if lo <= arrival <= hi:
            return TimeStatus(True)
        return TimeStatus(False, f"일몰은 {_hhmm(sunset_dt)}이에요")
    # night: sunset_dt 는 도착 날짜 기준으로 계산되어 있어야 한다
    start = sunset_dt + timedelta(minutes=tw.night_after_sunset_min)
    h, m = (int(x) for x in tw.night_until.split(":"))
    until = arrival.replace(hour=h, minute=m, second=0, microsecond=0)
    if arrival < start:
        return TimeStatus(False, f"{_hhmm(start)} 이후 도착부터 볼 수 있어요")
    if arrival > until:
        return TimeStatus(False, f"야경 안내는 {tw.night_until}까지예요")
    return TimeStatus(True)
