"""반경 환산, 예상 운전시간, 장소 점수, 가중 랜덤 선택 (규칙 기반, LLM 미사용)."""
from __future__ import annotations

import random
from typing import Optional, Sequence, TypeVar

from .config import RadiusCfg, ScoringCfg, SeasonRule

T = TypeVar("T")


def road_km_for_minutes(minutes: float, cfg: RadiusCfg) -> float:
    """구간별 속도로 minutes 동안 가는 도로 거리."""
    km, prev = 0.0, 0.0
    for seg in cfg.speed_segments:
        end = minutes if seg.until_min is None else min(minutes, seg.until_min)
        if end > prev:
            km += (end - prev) / 60 * seg.kmh
            prev = end
        if seg.until_min is None or minutes <= seg.until_min:
            break
    return km


def radius_km(drive_minutes: float, cfg: RadiusCfg) -> float:
    return max(cfg.min_radius_km, road_km_for_minutes(drive_minutes, cfg) / cfg.detour_factor)


def eta_minutes(dist_km: float, cfg: RadiusCfg) -> float:
    """직선거리 → 예상 운전시간 (radius_km 의 역함수)."""
    road = dist_km * cfg.detour_factor
    minutes, prev = 0.0, 0.0
    for seg in cfg.speed_segments:
        if seg.until_min is None:
            return minutes + road / seg.kmh * 60
        cap = (seg.until_min - prev) / 60 * seg.kmh
        if road <= cap:
            return minutes + road / seg.kmh * 60
        road -= cap
        minutes += seg.until_min - prev
        prev = seg.until_min
    return minutes + road / cfg.speed_segments[-1].kmh * 60


def distance_score(eta: float, drive_minutes: float, sc: ScoringCfg) -> Optional[float]:
    """예상 운전시간이 drive_minutes 의 band_low~band_high 이면 1.0, 가까우면 선형 감점, 초과면 None(제외)."""
    ratio = eta / drive_minutes if drive_minutes > 0 else 99
    if ratio > sc.band_high + 1e-9:
        return None
    if ratio >= sc.band_low:
        return 1.0
    return sc.near_floor + (1.0 - sc.near_floor) * (ratio / sc.band_low)


def info_score(has_image: bool, has_overview: bool, sc: ScoringCfg) -> float:
    return (sc.info_image if has_image else 0.0) + (sc.info_overview if has_overview else 0.0)


def season_bonus(month: int, spot_keywords: set[str], overview: str, rules: list[SeasonRule]) -> float:
    bonus = 0.0
    for r in rules:
        if month in r.months and spot_keywords & set(r.keywords) and any(w in overview for w in r.text):
            bonus += r.bonus
    return bonus


def weighted_pick(items: Sequence[T], scores: Sequence[float], rng: random.Random, min_weight: float) -> T:
    if not items:
        raise ValueError("빈 후보")
    weights = [max(s, min_weight) for s in scores]
    return rng.choices(list(items), weights=weights, k=1)[0]
