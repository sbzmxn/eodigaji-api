"""일출·일몰 시각 로컬 계산 (NOAA 근사식, 외부 API 없음). 오차는 보통 ±1~2분."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, tzinfo


def sunset(day: date, lat: float, lng: float, tz: tzinfo) -> datetime | None:
    """해당 날짜·좌표의 일몰 시각 (tz 기준 aware datetime). 백야/극야면 None."""
    return _sun_event(day, lat, lng, tz, rising=False)


def sunrise(day: date, lat: float, lng: float, tz: tzinfo) -> datetime | None:
    """해당 날짜·좌표의 일출 시각 (tz 기준 aware datetime). 백야/극야면 None."""
    return _sun_event(day, lat, lng, tz, rising=True)


def _sun_event(day: date, lat: float, lng: float, tz: tzinfo, rising: bool) -> datetime | None:
    n = day.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1 + 0.5)  # 정오 기준
    eqtime = 229.18 * (
        0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    )
    lat_r = math.radians(lat)
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(lat_r) * math.cos(decl))) - math.tan(lat_r) * math.tan(decl)
    if not -1 <= cos_ha <= 1:
        return None
    ha = math.degrees(math.acos(cos_ha))
    utc_minutes = 720 - 4 * (lng + ha if rising else lng - ha) - eqtime  # 일출은 -ha, 일몰은 +ha
    base = datetime.combine(day, time(0, 0), tzinfo=tz)
    offset = base.utcoffset() or timedelta(0)
    local_minutes = utc_minutes + offset.total_seconds() / 60
    return base + timedelta(minutes=local_minutes)
