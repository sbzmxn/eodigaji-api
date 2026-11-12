"""관광지 로컬 인덱스, 상세 정보 캐시, 힌트 정규화.

- 목록: 전국 관광지를 하루 1회 일괄 수집. 사용자 좌표는 외부로 보내지 않고 로컬 하버사인으로 거른다.
- 상세(소개·주차·휴무·운영시간·요금): content_id 기준 디스크 캐시 (TTL + modifiedtime 무효화).
"""
from __future__ import annotations

import asyncio
import html
import logging
import math
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Optional

from .cache import DiskCache
from .config import Settings
from .tour_client import TourClient, TourApiError

log = logging.getLogger("eodigaji.spots")

EARTH_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def _f(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v and math.isfinite(v) else None


@dataclass(frozen=True)
class Spot:
    content_id: str
    title: str
    lat: float
    lng: float
    addr1: str = ""
    addr2: str = ""
    lcls1: str = ""
    lcls2: str = ""
    lcls3: str = ""
    image: str = ""
    cpyrht: str = ""
    regn: str = ""
    signgu: str = ""
    modified: str = ""
    coastal: bool = True    # 섬일 때만 의미: 같은 시·군·구에 해안 분류가 있으면 True

    @staticmethod
    def from_item(it: dict) -> Optional["Spot"]:
        lat, lng = _f(it.get("mapy")), _f(it.get("mapx"))
        title = (it.get("title") or "").strip()
        cid = str(it.get("contentid") or "").strip()
        if not (lat and lng and title and cid):
            return None
        return Spot(
            content_id=cid, title=title, lat=lat, lng=lng,
            addr1=(it.get("addr1") or "").strip(), addr2=(it.get("addr2") or "").strip(),
            lcls1=it.get("lclsSystm1") or "", lcls2=it.get("lclsSystm2") or "", lcls3=it.get("lclsSystm3") or "",
            image=(it.get("firstimage") or "").strip(), cpyrht=(it.get("cpyrhtDivCd") or "").strip(),
            regn=it.get("lDongRegnCd") or "", signgu=it.get("lDongSignguCd") or "",
            modified=it.get("modifiedtime") or "",
        )

    def to_item(self) -> dict:
        return {
            "contentid": self.content_id, "title": self.title, "mapy": self.lat, "mapx": self.lng,
            "addr1": self.addr1, "addr2": self.addr2, "lclsSystm1": self.lcls1, "lclsSystm2": self.lcls2,
            "lclsSystm3": self.lcls3, "firstimage": self.image, "cpyrhtDivCd": self.cpyrht,
            "lDongRegnCd": self.regn, "lDongSignguCd": self.signgu, "modifiedtime": self.modified,
        }

    @property
    def image_url(self) -> Optional[str]:
        """이용 조건(cpyrhtDivCd)이 없는 이미지는 쓰지 않는다. http 는 https 로 바꾼다."""
        if not self.image or not self.cpyrht:
            return None
        if self.image.startswith("http://"):
            return "https://" + self.image[len("http://"):]
        return self.image

    @property
    def address(self) -> str:
        if self.addr2 and self.addr2 not in self.addr1:
            return f"{self.addr1} {self.addr2}".strip()
        return self.addr1


ISLAND_LCLS = "NA020500"


def mark_island_coast(spots: list[Spot], coastal_lcls: list[str]) -> list[Spot]:
    """섬(NA020500)마다 같은 시·군·구에 해안 분류가 있는지 표시한다 (강·호수의 섬 구분)."""
    if not coastal_lcls:
        return spots
    coastal = {(s.regn, s.signgu) for s in spots if s.lcls3 in coastal_lcls}
    return [replace(s, coastal=(s.regn, s.signgu) in coastal) if s.lcls3 == ISLAND_LCLS else s for s in spots]


class SpotIndex:
    def __init__(self, spots: list[Spot], loaded_at: float | None = None):
        self.spots = spots
        self.loaded_at = loaded_at or time.time()
        self.by_id = {s.content_id: s for s in spots}

    def within(self, lat: float, lng: float, radius_km: float) -> list[tuple[Spot, float]]:
        dlat = radius_km / 111.0
        dlng = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
        out = []
        for s in self.spots:
            if abs(s.lat - lat) > dlat or abs(s.lng - lng) > dlng:
                continue
            d = haversine_km(lat, lng, s.lat, s.lng)
            if d <= radius_km:
                out.append((s, d))
        return out


@dataclass
class RefData:
    lcls_names: dict[str, str] = field(default_factory=dict)      # 코드 -> 이름
    region_names: list[str] = field(default_factory=list)         # 시·도, 시·군·구 이름


class IndexManager:
    """목록 인덱스와 참조 데이터(신분류 이름, 법정동 이름)를 관리한다."""

    INDEX_NS, INDEX_KEY = "index", "bulk"

    def __init__(self, settings: Settings, tour: TourClient, cache: DiskCache):
        self.s = settings
        self.tour = tour
        self.cache = cache
        self.index: Optional[SpotIndex] = None
        self.ref = RefData()
        self._lock = asyncio.Lock()

    # ---- 목록 -------------------------------------------------------------
    async def ensure_index(self) -> SpotIndex:
        ttl = self.s.source.refresh_hours * 3600
        if self.index and time.time() - self.index.loaded_at < ttl:
            return self.index
        async with self._lock:
            if self.index and time.time() - self.index.loaded_at < ttl:
                return self.index
            entry = self.cache.get_entry(self.INDEX_NS, self.INDEX_KEY)
            if entry and time.time() - entry["saved_at"] < ttl:
                self.index = self._build(entry["value"], entry["saved_at"])
                return self.index
            try:
                items = await self.tour.list_all(self.s.source.bulk_page_size)
                slim = [s.to_item() for s in (Spot.from_item(i) for i in items) if s]
                self.cache.set(self.INDEX_NS, self.INDEX_KEY, slim, {"count": len(slim)})
                self.index = self._build(slim, time.time())
                log.info("index refreshed spots=%d", len(self.index.spots))
            except TourApiError:
                if entry:  # 오래된 캐시라도 쓴다
                    log.warning("index refresh failed; using stale cache")
                    self.index = self._build(entry["value"], time.time() - ttl + 600)  # 10분 뒤 재시도
                elif self.index is None:
                    raise
            return self.index

    def _build(self, items: list[dict], loaded_at: float) -> SpotIndex:
        spots = [s for s in (Spot.from_item(i) for i in items) if s]
        return SpotIndex(mark_island_coast(spots, self.s.keywords.coastal_lcls), loaded_at)

    async def near(self, lat: float, lng: float, radius_km: float) -> list[tuple[Spot, float]]:
        return (await self.ensure_index()).within(lat, lng, radius_km)

    # ---- 참조 데이터 ------------------------------------------------------
    async def load_ref(self) -> RefData:
        ttl = self.s.cache.ref_ttl_days * 86400
        lcls = await self._cached_ref("lcls", ttl, self.tour.lcls_names)
        regions = await self._cached_ref("ldong", ttl, self.tour.region_names)
        names: dict[str, str] = {}
        for it in lcls or []:
            for lv in (1, 2, 3):
                cd, nm = it.get(f"lclsSystm{lv}Cd"), it.get(f"lclsSystm{lv}Nm")
                if cd and nm:
                    names[cd] = nm
        rn: set[str] = set()
        for it in regions or []:
            for k in ("lDongRegnNm", "lDongSignguNm"):
                if it.get(k):
                    rn.add(it[k])
        self.ref = RefData(lcls_names=names, region_names=sorted(rn))
        return self.ref

    async def _cached_ref(self, key: str, ttl: float, fetch):
        val = self.cache.get("ref", key, ttl)
        if val is not None:
            return val
        try:
            val = await fetch()
            self.cache.set("ref", key, val)
            return val
        except TourApiError as e:
            log.warning("ref %s fetch failed: %s", key, e)
            return self.cache.get("ref", key, None)  # 만료된 것이라도


# ---- 상세 ---------------------------------------------------------------
@dataclass
class Detail:
    overview: str = ""
    parking: str = ""
    restdate: str = ""
    usetime: str = ""


class DetailService:
    def __init__(self, settings: Settings, tour: TourClient, cache: DiskCache):
        self.tour = tour
        self.cache = cache
        self.ttl = settings.cache.detail_ttl_days * 86400

    def _valid(self, entry: Optional[dict], spot: Spot) -> bool:
        if not entry or time.time() - entry.get("saved_at", 0) > self.ttl:
            return False
        return (entry.get("meta") or {}).get("modified", "") >= spot.modified

    def peek(self, spot: Spot) -> Optional[Detail]:
        """API 를 부르지 않고 캐시에 있을 때만 돌려준다 (1차 점수의 텍스트 가산용)."""
        if spot.content_id not in self.cache.keys("detail"):
            return None
        entry = self.cache.get_entry("detail", spot.content_id)
        return Detail(**entry["value"]) if self._valid(entry, spot) else None

    def peek_fee(self, spot: Spot) -> tuple[bool, Optional[str]]:
        """API 를 부르지 않고 (캐시에 있는지, 요금 값)."""
        if spot.content_id not in self.cache.keys("fee"):
            return False, None
        entry = self.cache.get_entry("fee", spot.content_id)
        return (True, entry["value"]) if self._valid(entry, spot) else (False, None)

    async def get(self, spot: Spot) -> Detail:
        entry = self.cache.get_entry("detail", spot.content_id)
        if self._valid(entry, spot):
            return Detail(**entry["value"])
        common, intro = await asyncio.gather(
            self.tour.detail_common(spot.content_id), self.tour.detail_intro(spot.content_id)
        )
        d = Detail(
            overview=common.get("overview") or "",
            parking=intro.get("parking") or "",
            restdate=intro.get("restdate") or "",
            usetime=intro.get("usetime") or "",
        )
        self.cache.set("detail", spot.content_id, d.__dict__, {"modified": spot.modified})
        return d

    async def fee(self, spot: Spot) -> Optional[str]:
        entry = self.cache.get_entry("fee", spot.content_id)
        if self._valid(entry, spot):
            return entry["value"]
        info = await self.tour.detail_info(spot.content_id)
        val = normalize_fee(info)
        self.cache.set("fee", spot.content_id, val, {"modified": spot.modified})
        return val


# ---- 정규화 -------------------------------------------------------------
def clean_text(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def overview_short(text: str, limit: int = 120) -> Optional[str]:
    t = re.sub(r"\s+", " ", clean_text(text))
    if not t:
        return None
    if len(t) <= limit:
        return t
    cut = t[:limit]
    end = max(cut.rfind(". "), cut.rfind("다."))
    if end >= limit * 0.5:
        return cut[: end + (2 if cut[end:end + 2] == "다." else 1)].strip()
    return cut.rstrip() + "…"


def normalize_parking(raw: str) -> Optional[str]:
    t = clean_text(raw)
    if not t:
        return None
    if "불가" in t or "없음" in t:
        return "불가"
    if t.startswith("가능") or "가능" in t or "있음" in t:
        return "가능"
    return None


_FEE_NAME = re.compile(r"입\s*장\s*료|관\s*람\s*료|이\s*용\s*요\s*금")


def normalize_fee(info: list[dict]) -> Optional[str]:
    """기본 입장료 기준. 기본이 무료면 '무료'."""
    rows = [r for r in info if _FEE_NAME.search(r.get("infoname", ""))]
    rows.sort(key=lambda r: 0 if "입장" in re.sub(r"\s", "", r.get("infoname", "")) else 1)
    if not rows:
        return None
    t = clean_text(rows[0].get("infotext", ""))
    head = re.sub(r"^[\s\-\[\]·※]+", "", t)
    if head.startswith("무료") or head.startswith("없음"):
        return "무료"
    if re.search(r"\d[\d,]*\s*원", t) or "유료" in t:
        return "유료"
    if "무료" in t:
        return "무료"
    return None


_WEEKDAYS = "월화수목금토일"
_QUALIFIER = re.compile(r"째|격주|둘째|넷째|첫째|셋째|마지막")


def closed_weekdays(restdate: str) -> set[int]:
    """휴무가 '확실한' 요일 (0=월). 매월 n째 주 같은 조건부 휴무는 확실하지 않으므로 넣지 않는다."""
    t = clean_text(restdate)
    if not t or "연중무휴" in t or "없음" in t.replace(" ", "")[:4]:
        return set()
    days: set[int] = set()
    for line in re.split(r"[\n,/]", t):
        if _QUALIFIER.search(line):
            continue
        m = re.search(r"([월화수목금토일])요일\s*[~\-]\s*([월화수목금토일])요일", line)
        if m:
            a, b = _WEEKDAYS.index(m.group(1)), _WEEKDAYS.index(m.group(2))
            days.update(range(a, b + 1) if a <= b else list(range(a, 7)) + list(range(0, b + 1)))
        for d in re.findall(r"([월화수목금토일])요일", line):
            days.add(_WEEKDAYS.index(d))
        if re.search(r"(^|\s)주말", line):
            days.update({5, 6})
    return days


_RANGE = re.compile(r"(\d{1,2})\s*[:시]\s*(\d{2})?\s*분?\s*[~\-]\s*(\d{1,2})\s*[:시]\s*(\d{2})?")


def open_at(arrival: datetime, restdate: str, usetime: str) -> Optional[bool]:
    """도착 예상 시각에 운영 중인지. 판단 근거가 없으면 None."""
    if arrival.weekday() in closed_weekdays(restdate):
        return False
    u = clean_text(usetime)
    if not u:
        return True if "연중무휴" in (restdate or "") else None
    if re.search(r"상시|24\s*시간|연중\s*개방|항시", u):
        return True
    m = _RANGE.search(u)
    if not m:
        return None
    start = int(m.group(1)) * 60 + int(m.group(2) or 0)
    end = int(m.group(3)) * 60 + int(m.group(4) or 0)
    now = arrival.hour * 60 + arrival.minute
    if end <= start:
        end += 24 * 60
        if now < start:
            now += 24 * 60
    return start <= now < end
