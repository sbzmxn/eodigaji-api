"""API 요청/응답 스키마와 공통 에러 스키마."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class RecommendRequest(BaseModel):
    model_config = ConfigDict(extra="allow")   # 정의 안 된 필드는 무시(경고 로그). available_minutes 만 거부

    latitude: float
    longitude: float
    drive_minutes: int = Field(description="목적지까지 편도 운전 시간(분), 15~120")
    keyword_ids: list[str] = Field(default_factory=list, max_length=3)
    exclude_content_ids: list[str] = Field(default_factory=list, max_length=200)


class Destination(BaseModel):
    lat: float
    lng: float


class Hint(BaseModel):
    parking: Optional[str] = Field(None, description="'가능' | '불가' | null")
    fee: Optional[str] = Field(None, description="기본 입장료 기준 '무료' | '유료' | null")
    open_now: Optional[bool] = Field(None, description="도착 예상 시각 기준 운영 여부, 근거 없으면 null")


class Reveal(BaseModel):
    content_id: str
    real_name: str
    address: str
    overview_short: Optional[str] = None
    image_url: Optional[str] = None
    theme: Optional[str] = None
    matched_keywords: list[str] = Field(default_factory=list)


class RecommendResponse(BaseModel):
    request_id: str
    blind_name: str = Field(description="호환용. 앱은 BlindNames(docs/BlindNames.kt)에서 직접 고른다. 같은 100개 목록에서 고른 값")
    destination: Destination
    hint: Hint
    reveal: Reveal
    alias_source: str = Field(description='항상 "pool" (검수된 가명 목록)')


class KeywordStatus(BaseModel):
    id: str
    label: str
    enabled: bool
    reason: Optional[str] = None


class KeywordsResponse(BaseModel):
    request_id: str
    arrival_time: str = Field(description="도착 예상 시각 HH:MM (현재 + drive_minutes)")
    sunset_time: Optional[str] = Field(None, description="도착일 일몰 시각 HH:MM")
    keywords: list[KeywordStatus]


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


ERROR_CODES = {
    "INVALID_REQUEST": 400,
    "INVALID_LOCATION": 400,
    "INVALID_KEYWORD": 400,
    "KEYWORD_UNAVAILABLE": 400,
    "NO_CANDIDATES": 404,
    "UPSTREAM_TOUR_API_ERROR": 502,
    "INTERNAL_ERROR": 500,
}
