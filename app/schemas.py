"""API 요청/응답 스키마와 공통 에러 스키마."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class RetryContext(BaseModel):
    """재추천: 직전 추천의 실제 ETA 로 이번 요청의 평균 속도를 보정한다 (저장하지 않음)."""
    previous_content_ids: list[str] = Field(
        default_factory=list, max_length=200,
        description="이 세션에서 이미 나온 reveal.content_id (마지막 원소 = 직전 후보). 모두 제외된다")
    observed_eta_seconds: Optional[int] = Field(
        None, gt=0, le=6 * 3600, description="직전 후보까지 NaviHelper requestTrip 이 준 실제 ETA(초)")


class SearchFields(BaseModel):
    """/recommend 와 /eta-check 가 함께 쓰는 검색 조건."""
    model_config = ConfigDict(extra="allow")   # 정의 안 된 필드는 무시(경고 로그). available_minutes 만 거부

    latitude: float
    longitude: float
    drive_minutes: int = Field(description="목적지까지 편도 운전 시간(분), 15~120")
    keyword_ids: list[str] = Field(default_factory=list, max_length=3)
    exclude_content_ids: list[str] = Field(default_factory=list, max_length=200)
    require_parking: bool = Field(False, description="true 면 주차 '가능'으로 확인된 곳만 (불가·정보 없음 제외)")
    exclude_paid: bool = Field(False, description="true 면 기본 입장료·이용료 '유료'인 곳 제외 (무료·정보 없음 포함)")


class RecommendRequest(SearchFields):
    retry_context: Optional[RetryContext] = None


class EtaCheckRequest(SearchFields):
    """앱이 requestTrip 으로 잰 ETA 를 보내면 서버가 수락·재추천·포기를 정한다. 서버는 상태를 저장하지 않는다."""
    content_id: str = Field(min_length=1, max_length=64, description="방금 ETA 를 잰 후보")
    observed_eta_seconds: int = Field(gt=0, le=6 * 3600, description="그 후보까지 requestTrip ETA(초)")
    previous_content_ids: list[str] = Field(
        min_length=1, max_length=200, description="지금까지 나온 후보 전부(오래된 → 최근). 마지막 = content_id")


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
    straight_km: float = Field(description="출발지→목적지 직선거리(km, 소수 둘째 자리)")
    estimated_drive_seconds: int = Field(
        description="서버 추정 편도 운전시간(초). retry_context 로 속도를 보정했으면 보정 속도 기준")


class EtaCheckResponse(BaseModel):
    request_id: str
    decision: Literal["accept", "retry", "fallback"]
    attempt: int = Field(description="안내할 후보(accept·fallback) 또는 새 추천(retry)이 몇 번째 추천인지")
    max_attempts: int
    reason: Literal["within_limit", "over_limit", "max_attempts", "no_candidates", "condition_not_met",
                    "keyword_unavailable", "upstream_error", "internal_error"]
    route_content_id: Optional[str] = Field(None, description="accept·fallback 일 때 안내할 content_id")
    recommendation: Optional[RecommendResponse] = Field(None, description="retry 일 때만 새 추천")


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
    "UNAUTHORIZED": 401,
    "KEYWORD_UNAVAILABLE": 400,
    "NO_CANDIDATES": 404,
    "CONDITION_NOT_MET": 404,
    "UPSTREAM_TOUR_API_ERROR": 502,
    "INTERNAL_ERROR": 500,
}
