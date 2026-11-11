"""한국관광공사 KorService2 클라이언트 (httpx async).
- serviceKey 와 좌표 파라미터는 로그·예외 메시지에서 마스킹한다.
- 사용자 좌표는 외부로 보내지 않는다 (목록은 전국 일괄 수집 후 로컬 거리 계산)."""
from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger("eodigaji.tour")

_MASK_PARAMS = {"serviceKey", "mapX", "mapY", "radius"}


class TourApiError(Exception):
    """업스트림 오류. 메시지에 키/좌표가 들어가지 않도록 생성 지점에서 정리한다."""


def mask_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    q = [(k, "***" if k in _MASK_PARAMS else v) for k, v in q]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q, safe="*")))


class TourClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.cfg = settings.tour_api
        self._key = settings.tour_kor_key.get_secret_value()
        self._client = client or httpx.AsyncClient(timeout=self.cfg.timeout_s)
        self.calls = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    def _scrub(self, text: str) -> str:
        if self._key:
            text = text.replace(self._key, "***").replace(urllib.parse.quote(self._key, safe=""), "***")
        return re.sub(r"(mapX|mapY)=[0-9.]+", r"\1=***", text)

    async def call(self, op: str, **params: Any) -> tuple[list[dict], int]:
        if not self._key:
            raise TourApiError("TOURAPI_KOR_KEY 가 설정되지 않았습니다")
        q = {"serviceKey": self._key, "MobileOS": "ETC", "MobileApp": self.cfg.mobile_app, "_type": "json"}
        q.update({k: str(v) for k, v in params.items()})
        url = f"{self.cfg.base_url}/{op}"
        last: Exception | None = None
        for _attempt in range(2):
            try:
                self.calls += 1
                r = await self._client.get(url, params=q)
                log.debug("tour call %s", mask_url(str(r.request.url)))
                r.raise_for_status()
                data = r.json()
                break
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as e:
                last = e
        else:
            raise TourApiError(f"{op} 호출 실패: {self._scrub(type(last).__name__ + ': ' + str(last))}")

        resp = data.get("response", data)
        header = resp.get("header", {}) if isinstance(resp, dict) else {}
        code = header.get("resultCode", data.get("resultCode"))
        if code not in ("0000", "00", None):
            msg = header.get("resultMsg", data.get("resultMsg", ""))
            raise TourApiError(f"{op} resultCode={code} {self._scrub(str(msg))}")
        body = resp.get("body") or {}
        items = body.get("items") or {}
        items = items.get("item", []) if isinstance(items, dict) else []
        if isinstance(items, dict):
            items = [items]
        return items, int(body.get("totalCount") or 0)

    async def list_all(self, page_size: int) -> list[dict]:
        """전국 관광지(contentTypeId) 목록 전체."""
        out: list[dict] = []
        page = 1
        while True:
            items, total = await self.call(
                "areaBasedList2", contentTypeId=self.cfg.content_type_id, numOfRows=page_size, pageNo=page
            )
            out.extend(items)
            if not items or len(out) >= total:
                return out
            page += 1

    async def detail_common(self, content_id: str) -> dict:
        items, _ = await self.call("detailCommon2", contentId=content_id)
        return items[0] if items else {}

    async def detail_intro(self, content_id: str) -> dict:
        items, _ = await self.call("detailIntro2", contentId=content_id, contentTypeId=self.cfg.content_type_id)
        return items[0] if items else {}

    async def detail_info(self, content_id: str) -> list[dict]:
        items, _ = await self.call("detailInfo2", contentId=content_id, contentTypeId=self.cfg.content_type_id)
        return items

    async def region_names(self) -> list[dict]:
        items, _ = await self.call("ldongCode2", lDongListYn="Y", numOfRows=1000, pageNo=1)
        return items

    async def lcls_names(self) -> list[dict]:
        items, _ = await self.call("lclsSystmCode2", lclsSystmListYn="Y", numOfRows=2000, pageNo=1)
        return items
