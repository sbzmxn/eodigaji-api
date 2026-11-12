"""추천 흐름: 반경 → 로컬 후보 → 1차 점수(목록 필드) → 상위 3곳 상세조회 → 최종 점수 → 가중 랜덤 → 가명."""
from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from . import sun
from .alias import AliasPool
from .config import Settings
from .keywords import (combined_keyword_score, is_excluded, keyword_score, landmass_of, match_keywords,
                       theme_of, time_status)
from .schemas import ERROR_CODES
from .scoring import (distance_score, eta_minutes, info_score, radius_km, season_bonus,
                      weighted_pick)
from .spots import Detail, DetailService, IndexManager, Spot, closed_weekdays, haversine_km, normalize_parking, \
    open_at, overview_short
from .tour_client import TourApiError


class ApiError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = ERROR_CODES.get(code, 400)


_UNSET = object()


@dataclass
class Cand:
    spot: Spot
    dist_km: float
    eta: float
    dist_score: float
    matches: dict[str, float]
    pre: float
    detail: Optional[Detail] = None
    open_now: Optional[bool] = None
    final: float = 0.0
    fee: object = _UNSET                    # 조건 검사 때 받은 요금 (_UNSET 이면 아직 모름)


@dataclass
class Outcome:
    body: dict
    n_candidates: int = 0
    alias_source: Optional[str] = None
    speed_factor: Optional[float] = None    # 재추천 보정 배율 (보정 안 했으면 None)


@dataclass
class Conditions:
    """추천 조건. require_parking: 주차 '가능' 확인된 곳만 / exclude_paid: '유료' 제외(무료·정보 없음 포함)."""
    require_parking: bool = False
    exclude_paid: bool = False

    @property
    def any(self) -> bool:
        return self.require_parking or self.exclude_paid

    def not_met_message(self) -> str:
        if self.require_parking and self.exclude_paid:
            return "주차할 수 있고 유료가 아닌 곳을 찾지 못했어요"
        if self.require_parking:
            return "주차할 수 있는 곳을 찾지 못했어요"
        return "유료가 아닌 곳을 찾지 못했어요"


def passes(cond: Conditions, parking: Optional[str], fee: Optional[str]) -> bool:
    """상세 정보로 조건 통과 여부. parking 은 normalize_parking 결과."""
    if cond.require_parking and parking != "가능":
        return False
    if cond.exclude_paid and fee == "유료":
        return False
    return True


@dataclass
class Retry:
    previous_content_ids: list[str]
    observed_eta_seconds: Optional[int] = None


class Recommender:
    def __init__(self, settings: Settings, index: IndexManager, details: DetailService, alias: AliasPool,
                 now_fn: Optional[Callable[[], datetime]] = None, rng: Optional[random.Random] = None):
        self.s = settings
        self.index = index
        self.details = details
        self.alias = alias
        self.tz = ZoneInfo(settings.timezone)
        self.now_fn = now_fn or (lambda: datetime.now(self.tz))
        self.rng = rng or random.Random(settings.random_seed)

    # ---- 검증 ---------------------------------------------------------------
    def _check_location(self, lat: float, lng: float) -> None:
        b = self.s.bounds
        if not (b.lat_min <= lat <= b.lat_max and b.lng_min <= lng <= b.lng_max):
            raise ApiError("INVALID_LOCATION", "서비스 지역(대한민국) 밖의 좌표입니다")

    def _check_drive(self, m: int) -> None:
        d = self.s.drive_minutes
        if not d.min <= m <= d.max:
            raise ApiError("INVALID_REQUEST", f"drive_minutes 는 {d.min}~{d.max} 사이여야 합니다")

    def _check_keywords(self, kids: list[str]) -> None:
        known = set(self.s.keywords.ids)
        if len(kids) > 3 or len(set(kids)) != len(kids) or any(k not in known for k in kids):
            raise ApiError("INVALID_KEYWORD", "keyword_ids 는 정의된 키워드 중 중복 없이 0~3개여야 합니다")

    def _times(self, lat: float, lng: float, drive: int):
        """도착 예상 시각과 도착일의 일몰·일출 시각."""
        arrival = self.now_fn().astimezone(self.tz) + timedelta(minutes=drive)
        day = arrival.date()
        return arrival, sun.sunset(day, lat, lng, self.tz), sun.sunrise(day, lat, lng, self.tz)

    async def _near(self, lat: float, lng: float, r: float):
        try:
            return await self.index.near(lat, lng, r)
        except TourApiError as e:
            raise ApiError("UPSTREAM_TOUR_API_ERROR", "관광 정보 서버에서 목록을 가져오지 못했습니다") from e

    def _origin_landmass(self, near) -> Optional[str]:
        """출발지의 땅 = 가장 가까운 관광지(origin_landmass_max_km 이내)의 땅. 없으면 육지로 본다."""
        close = [(d, s) for s, d in near if d <= self.s.origin_landmass_max_km]
        if not close:
            return None
        return landmass_of(min(close, key=lambda x: x[0])[1], self.s.landmasses)

    def _base_pool(self, near, drive: int, factor: float = 1.0):
        """factor: 재추천 속도 배율. 예상 운전시간 = 기본 모델 시간 / factor."""
        prefixes = self.s.candidates.exclude_lcls_prefixes
        origin = self._origin_landmass(near)
        for spot, d in near:
            if is_excluded(spot, prefixes):
                continue
            if landmass_of(spot, self.s.landmasses) != origin:
                continue                            # 배를 타야 하는 곳
            eta = eta_minutes(d, self.s.radius) / factor
            ds = distance_score(eta, drive, self.s.scoring)
            if ds is None:
                continue
            yield spot, d, eta, ds

    # ---- 조건: 캐시만 보는 판정 --------------------------------------------------
    def _cached_parts(self, spot: Spot, cond: Conditions) -> list[Optional[bool]]:
        """조건별 캐시 판정. True=통과 확인, False=탈락 확인, None=캐시에 없음 (API 호출 없음)."""
        parts: list[Optional[bool]] = []
        if cond.require_parking:
            d = self.details.peek(spot)
            parts.append(None if d is None else normalize_parking(d.parking) == "가능")
        if cond.exclude_paid:
            known, fee = self.details.peek_fee(spot)
            parts.append(None if not known else fee != "유료")
        return parts

    def _cached_fail(self, spot: Spot, cond: Conditions) -> bool:
        return False in self._cached_parts(spot, cond)

    def _pass_weight(self, spot: Spot, cond: Conditions) -> float:
        """/keywords 추정: 확인된 통과 1, 확인된 탈락 0, 모르면 설정 통과율."""
        if not cond.any:
            return 1.0
        cc = self.s.conditions
        rates = ([cc.parking_pass_rate] if cond.require_parking else []) + \
                ([cc.fee_pass_rate] if cond.exclude_paid else [])
        w = 1.0
        for part, rate in zip(self._cached_parts(spot, cond), rates):
            if part is False:
                return 0.0
            if part is None:
                w *= rate
        return w

    # ---- /keywords ----------------------------------------------------------
    async def keywords_status(self, lat: float, lng: float, drive: int,
                              cond: Optional[Conditions] = None) -> dict:
        """cond 를 켜면 키워드별 후보 수를 캐시 기반으로 추정해(_pass_weight 합) 3 미만이면 끈다."""
        cond = cond or Conditions()
        self._check_location(lat, lng)
        self._check_drive(drive)
        arrival, sunset_dt, sunrise_dt = self._times(lat, lng, drive)
        near = await self._near(lat, lng, radius_km(drive, self.s.radius))
        kcfg, sc = self.s.keywords, self.s.scoring
        counts = {k: 0 for k in kcfg.ids}
        est = {k: 0.0 for k in kcfg.ids}
        n = 0
        for spot, *_ in self._base_pool(near, drive):
            n += 1
            ks = [k for k in kcfg.ids if keyword_score(k, spot, kcfg, sc) > 0]
            w = self._pass_weight(spot, cond) if ks and cond.any else 1.0
            for k in ks:
                counts[k] += 1
                est[k] += w
        out = []
        for k, kw in kcfg.keywords.items():
            ts = time_status(kw, arrival, sunset_dt, self.s.time_windows, sunrise_dt)
            if not ts.active:
                out.append({"id": k, "label": kw.label, "enabled": False, "reason": ts.reason})
            elif counts[k] < self.s.candidates.min_for_keyword:
                out.append({"id": k, "label": kw.label, "enabled": False, "reason": "지금 시간으론 멀어요"})
            elif cond.any and est[k] < self.s.candidates.min_for_keyword - 1e-9:
                out.append({"id": k, "label": kw.label, "enabled": False, "reason": "조건에 맞는 곳이 적어요"})
            else:
                out.append({"id": k, "label": kw.label, "enabled": True, "reason": None})
        return {
            "arrival_time": arrival.strftime("%H:%M"),
            "sunset_time": sunset_dt.strftime("%H:%M") if sunset_dt else None,
            "keywords": out,
            "_n": n,
            "_counts": counts,
            "_estimated": est,
        }

    # ---- 재추천 속도 보정 -----------------------------------------------------
    async def speed_factor(self, lat: float, lng: float, retry: Optional[Retry]) -> Optional[float]:
        """직전 후보(previous_content_ids 마지막)의 직선거리로 서버가 추정한 운전시간 / 실제 ETA.
        직전 후보를 인덱스에서 못 찾거나, 너무 가까워 추정이 불안정하면 None (보정 안 함)."""
        if not retry or not retry.observed_eta_seconds or not retry.previous_content_ids:
            return None
        idx = await self.index.ensure_index()
        prev = idx.by_id.get(retry.previous_content_ids[-1])
        if prev is None:
            return None
        est_s = eta_minutes(haversine_km(lat, lng, prev.lat, prev.lng), self.s.radius) * 60
        rc = self.s.retry
        if est_s < rc.min_estimated_seconds:
            return None
        return min(rc.max_speed_factor, max(rc.min_speed_factor, est_s / retry.observed_eta_seconds))

    # ---- /recommend ---------------------------------------------------------
    async def recommend(self, lat: float, lng: float, drive: int, kids: list[str], exclude: list[str],
                        retry: Optional[Retry] = None, cond: Optional[Conditions] = None) -> Outcome:
        cond = cond or Conditions()
        self._check_location(lat, lng)
        self._check_drive(drive)
        self._check_keywords(kids)
        kcfg, sc = self.s.keywords, self.s.scoring
        arrival, sunset_dt, sunrise_dt = self._times(lat, lng, drive)
        active = [k for k in kids
                  if time_status(kcfg.keywords[k], arrival, sunset_dt, self.s.time_windows, sunrise_dt).active]
        if kids and not active:
            raise ApiError("KEYWORD_UNAVAILABLE", "고른 키워드를 도착 예상 시각에는 쓸 수 없어요")

        try:
            factor = await self.speed_factor(lat, lng, retry)
        except TourApiError as e:
            raise ApiError("UPSTREAM_TOUR_API_ERROR", "관광 정보 서버에서 목록을 가져오지 못했습니다") from e
        # 속도 f배 = 같은 drive 분 동안 기본 모델로 drive*f 분 가는 거리. 직전 후보의 보정 시간 = 실제 ETA
        f = factor or 1.0
        near = await self._near(lat, lng, radius_km(drive * f, self.s.radius))
        excl = set(exclude) | set(retry.previous_content_ids if retry else [])
        pool: list[Cand] = []
        cond_dropped = 0
        for spot, d, eta, ds in self._base_pool(near, drive, f):
            if spot.content_id in excl:
                continue
            if cond.any and self._cached_fail(spot, cond):
                cond_dropped += 1                   # 캐시로 불가·정보 없음·유료가 확인된 곳은 조회 없이 제외
                continue
            cached = self.details.peek(spot)
            ov = cached.overview if cached else ""
            m = match_keywords(spot, active, kcfg, sc, ov) if active else {}
            if active and not m:
                continue
            pre = (sc.w_keyword * combined_keyword_score(m, sc) + sc.w_distance * ds
                   + info_score(spot.image_url is not None, bool(ov), sc))
            pool.append(Cand(spot, d, eta, ds, m, pre))
        if not pool:
            if cond_dropped:
                raise ApiError("CONDITION_NOT_MET", cond.not_met_message())
            raise ApiError("NO_CANDIDATES", "조건에 맞는 장소가 없어요")

        self.rng.shuffle(pool)                      # 동점일 때 매번 같은 곳이 나오지 않게
        pool.sort(key=lambda c: c.pre, reverse=True)
        top = pool[: self.s.candidates.max_candidates]
        finalists, rejected = await self._finalists(top, arrival, active, cond)
        if not finalists:
            if rejected:
                raise ApiError("CONDITION_NOT_MET", cond.not_met_message())
            raise ApiError("NO_CANDIDATES", "도착 예상 시각에 문을 여는 장소가 없어요")

        chosen = weighted_pick(finalists, [c.final for c in finalists], self.rng, sc.min_weight)
        spot = chosen.spot
        if chosen.fee is not _UNSET:
            fee = chosen.fee
        else:
            try:
                fee = await self.details.fee(spot)
            except TourApiError:
                fee = None
        blind_name = self.alias.pick(spot)
        matched = list(chosen.matches)
        det = chosen.detail or Detail()
        body = {
            "blind_name": blind_name,
            "destination": {"lat": spot.lat, "lng": spot.lng},
            "hint": {"parking": normalize_parking(det.parking), "fee": fee, "open_now": chosen.open_now},
            "reveal": {
                "content_id": spot.content_id,
                "real_name": spot.title,
                "address": spot.address,
                "overview_short": overview_short(det.overview),
                "image_url": spot.image_url,
                "theme": theme_of(spot, matched, kcfg, self.index.ref.lcls_names),
                "matched_keywords": matched,
            },
            "alias_source": "pool",
            "straight_km": round(chosen.dist_km, 2),
            "estimated_drive_seconds": int(round(chosen.eta * 60)),
        }
        return Outcome(body, len(pool), "pool", factor)

    async def _lookup(self, c: Cand, cond: Conditions) -> None:
        """상세(+조건에 요금이 필요하면 요금)를 병렬 조회. 실패는 정보 없음으로 둔다."""
        async def fee():
            try:
                return await self.details.fee(c.spot)
            except TourApiError:
                return None
        results = await asyncio.gather(self.details.get(c.spot), *( [fee()] if cond.exclude_paid else []),
                                       return_exceptions=True)
        det = results[0]
        if isinstance(det, TourApiError):
            det = None                              # 상세 실패는 정보 없음으로 진행
        elif isinstance(det, BaseException):
            raise det
        c.detail = det
        c.fee = results[1] if cond.exclude_paid else _UNSET

    async def _finalists(self, top: list[Cand], arrival: datetime, active: list[str],
                         cond: Optional[Conditions] = None) -> tuple[list[Cand], int]:
        """상위 후보부터 상세조회해 finalists 개를 채운다. (통과 후보, 조건 탈락 수)
        - 도착 요일 휴무가 확실하면 제외.
        - 도착 시각에 운영 종료가 확실하면(open_now=False) 제외. 단 모든 후보가 그렇다면 감점해서 살린다.
        - 조건(cond)을 켜면 batch_size 곳씩 병렬 조회, 요청당 max_lookups 곳까지. 조건 탈락은 제외."""
        cond = cond or Conditions()
        cc = self.s.candidates
        cap = self.s.conditions.max_lookups if cond.any else cc.max_detail_lookups
        out: list[Cand] = []
        closed_at_arrival: list[Cand] = []
        i = lookups = rejected = 0
        while len(out) < cc.finalists and i < len(top) and lookups < cap:
            want = self.s.conditions.batch_size if cond.any else cc.finalists - len(out)
            batch = top[i:i + min(want, cap - lookups)]
            i += len(batch)
            lookups += len(batch)
            await asyncio.gather(*(self._lookup(c, cond) for c in batch))
            for c in batch:
                det = c.detail
                if cond.any and not passes(cond, normalize_parking(det.parking) if det else None,
                                           None if c.fee is _UNSET else c.fee):
                    rejected += 1                   # 주차 불가·정보 없음 / 유료
                    continue
                if det and arrival.weekday() in closed_weekdays(det.restdate):
                    continue                        # 휴무가 확실한 곳 제외
                c.open_now = open_at(arrival, det.restdate, det.usetime) if det else None
                self._final_score(c, arrival, active)
                (closed_at_arrival if c.open_now is False else out).append(c)
                if len(out) >= cc.finalists:
                    break
        if not out and closed_at_arrival:
            closed_at_arrival.sort(key=lambda c: c.final, reverse=True)
            out = closed_at_arrival[: cc.finalists]
        return out, rejected

    def _final_score(self, c: Cand, arrival: datetime, active: list[str]) -> None:
        kcfg, sc = self.s.keywords, self.s.scoring
        base_kws = [k for k, kw in kcfg.keywords.items() if not kw.time_window]
        ov = c.detail.overview if c.detail else ""
        if active:
            c.matches = match_keywords(c.spot, active, kcfg, sc, ov) or c.matches
        spot_kws = set(match_keywords(c.spot, base_kws, kcfg, sc, ov))
        c.final = (sc.w_keyword * combined_keyword_score(c.matches, sc)
                   + season_bonus(arrival.month, spot_kws, ov, self.s.season_rules)
                   + sc.w_distance * c.dist_score
                   + info_score(c.spot.image_url is not None, bool(ov), sc)
                   - (sc.closed_at_arrival_penalty if c.open_now is False else 0.0))


def new_request_id() -> str:
    return uuid.uuid4().hex
