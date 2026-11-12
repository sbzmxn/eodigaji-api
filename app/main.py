"""어디가지? 추천 서버 (FastAPI).

실행: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from datetime import datetime
from typing import Callable, Optional

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .alias import AliasPool, build_region_words, build_type_words, build_unit_words
from .audit import AuditSink, LogSink, RequestMeta
from .cache import DiskCache
from .config import Settings, get_settings
from .recommend import ApiError, Conditions, Recommender, Retry, new_request_id
from .schemas import (ERROR_CODES, ErrorResponse, EtaCheckRequest, EtaCheckResponse, KeywordsResponse,
                      RecommendRequest, RecommendResponse, SearchFields)
from .spots import DetailService, IndexManager
from .tour_client import TourClient

log = logging.getLogger("eodigaji")
RENAMED_MSG = "available_minutes는 drive_minutes(편도 운전 시간, 분)로 변경됐어요"


# ---- 로깅: 좌표·쿼리·키가 남지 않게 ----------------------------------------
class AccessLogSanitizer(logging.Filter):
    """uvicorn access log 에서 쿼리 문자열과 클라이언트 주소를 지운다."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            client, method, path, ver, status = args
            record.args = ("-", method, str(path).split("?", 1)[0], ver, status)
        return True


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("eodigaji").setLevel(level)
    # httpx/httpcore 는 INFO 에서 전체 URL(serviceKey 포함)을 찍으므로 올려 둔다
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    acc = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessLogSanitizer) for f in acc.filters):
        acc.addFilter(AccessLogSanitizer())


def _error(code: str, message: str, rid: str) -> JSONResponse:
    return JSONResponse(status_code=ERROR_CODES.get(code, 400),
                        content={"error": {"code": code, "message": message, "request_id": rid}})


def create_app(
    settings: Optional[Settings] = None,
    *,
    index: Optional[IndexManager] = None,
    details: Optional[DetailService] = None,
    alias: Optional[AliasPool] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
    rng: Optional[random.Random] = None,
    sink: Optional[AuditSink] = None,
    load_on_startup: bool = True,
) -> FastAPI:
    s = settings or get_settings()
    configure_logging(s.log_level)
    cache = DiskCache(s.cache_dir)
    tour = TourClient(s) if (index is None or details is None) else None
    index = index or IndexManager(s, tour, cache)
    details = details or DetailService(s, tour, cache)
    rng = rng or random.Random(s.random_seed)
    alias = alias or AliasPool(s.alias, build_type_words(s.alias, s.keywords, {}), rng=rng)
    alias.check()   # 기본 금지어로 가명 목록 검사. 걸리면 AliasPoolError (앱을 만들지 않음)
    rec = Recommender(s, index, details, alias, now_fn=now_fn, rng=rng)
    sink = sink or LogSink()

    async def warm_up() -> None:
        """참조 데이터·인덱스를 받고, 넓힌 금지어(신분류·시군구·읍면동)로 가명 목록을 다시 검사한다.
        데이터를 못 받으면 경고만 남기고 계속하지만, 가명 목록 검증 실패(AliasPoolError)는 시작을 중단시킨다."""
        ref = idx = None
        try:
            ref = await index.load_ref()
        except Exception as e:  # noqa: BLE001
            log.warning("reference data load failed: %s", type(e).__name__)
        try:
            idx = await index.ensure_index()
        except Exception as e:  # noqa: BLE001  요청 시 재시도
            log.warning("index load failed: %s", type(e).__name__)
        alias.update_lexicon(
            build_type_words(s.alias, s.keywords, ref.lcls_names if ref else {})
            | build_region_words(ref.region_names if ref else []),
            build_unit_words(idx.spots) if idx else set(),
        )

    async def refresher() -> None:
        while True:
            await asyncio.sleep(3600)
            with contextlib.suppress(Exception):
                await index.ensure_index()   # TTL(하루) 지났을 때만 실제로 수집

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = None
        if load_on_startup:
            await warm_up()
            task = asyncio.create_task(refresher())
        yield
        if task:
            task.cancel()
        if tour:
            await tour.aclose()

    app = FastAPI(title="어디가지? 추천 서버", version="0.1.0", lifespan=lifespan)
    app.state.recommender = rec
    app.state.index = index

    @app.middleware("http")
    async def request_meta(request: Request, call_next):
        rid = new_request_id()
        request.state.meta = RequestMeta(request_id=rid, endpoint=request.url.path)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001  내부 오류도 같은 에러 스키마 + 요청 로그로
            log.error("unhandled %s rid=%s", type(exc).__name__, rid)
            request.state.meta.result_code = "INTERNAL_ERROR"
            response = _error("INTERNAL_ERROR", "서버 내부 오류", rid)
        meta: RequestMeta = request.state.meta
        meta.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if response.status_code >= 400 and meta.result_code == "OK":
            meta.result_code = f"HTTP_{response.status_code}"
        if request.url.path.startswith("/api/"):
            sink.record(meta)
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        request.state.meta.result_code = exc.code
        return _error(exc.code, exc.message, request.state.meta.request_id)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # 입력값을 응답·로그에 되돌려 주지 않는다 (pydantic 기본 응답에는 input 이 들어 있음)
        locs = {str(p) for e in exc.errors() for p in e.get("loc", ())}
        if isinstance(exc.body, dict) and "available_minutes" in exc.body:
            code, msg = "INVALID_REQUEST", RENAMED_MSG
        elif locs & {"keyword_ids"}:
            code, msg = "INVALID_KEYWORD", "keyword_ids 는 정의된 키워드 중 0~3개여야 합니다"
        elif locs & {"latitude", "longitude", "lat", "lng"}:
            code, msg = "INVALID_LOCATION", "위도·경도가 올바르지 않습니다"
        else:
            fields = sorted(locs - {"body", "query"})
            code, msg = "INVALID_REQUEST", f"요청 형식이 올바르지 않습니다: {', '.join(fields)}"
        request.state.meta.result_code = code
        return _error(code, msg, request.state.meta.request_id)

    errors = {s: {"model": ErrorResponse} for s in sorted(set(ERROR_CODES.values()))}

    @app.get("/api/v1/keywords", response_model=KeywordsResponse, responses=errors)
    async def keywords(request: Request, lat: float = Query(...), lng: float = Query(...),
                       drive_minutes: int = Query(...), require_parking: bool = Query(False),
                       exclude_paid: bool = Query(False)):
        res = await rec.keywords_status(lat, lng, drive_minutes, Conditions(require_parking, exclude_paid))
        request.state.meta.n_candidates = res.pop("_n")
        res.pop("_counts", None)
        res.pop("_estimated", None)
        return {"request_id": request.state.meta.request_id, **res}

    def check_extras(request: Request, body: SearchFields) -> None:
        extras = sorted(body.model_extra or {})
        if "available_minutes" in extras:
            raise ApiError("INVALID_REQUEST", RENAMED_MSG)
        if extras:  # 정의 안 된 필드는 무시. 필드 '이름'만 남기고 값은 남기지 않는다
            log.warning("ignored fields rid=%s: %s", request.state.meta.request_id,
                        ",".join(e[:30] for e in extras[:5]))

    @app.post("/api/v1/recommend", response_model=RecommendResponse, responses=errors)
    async def recommend(request: Request, body: RecommendRequest):
        check_extras(request, body)
        rc = body.retry_context
        retry = Retry(rc.previous_content_ids, rc.observed_eta_seconds) if rc else None
        out = await rec.recommend(body.latitude, body.longitude, body.drive_minutes,
                                  body.keyword_ids, body.exclude_content_ids, retry,
                                  Conditions(body.require_parking, body.exclude_paid))
        meta: RequestMeta = request.state.meta
        meta.n_candidates = out.n_candidates
        meta.alias_source = out.alias_source
        return {"request_id": meta.request_id, **out.body}

    @app.post("/api/v1/eta-check", response_model=EtaCheckResponse, responses=errors)
    async def eta_check(request: Request, body: EtaCheckRequest):
        check_extras(request, body)
        meta: RequestMeta = request.state.meta
        res = await rec.eta_check(body.latitude, body.longitude, body.drive_minutes, body.keyword_ids,
                                  body.exclude_content_ids, Conditions(body.require_parking, body.exclude_paid),
                                  body.content_id, body.observed_eta_seconds, body.previous_content_ids)
        out = res.pop("recommendation")
        if out is not None:
            meta.n_candidates = out.n_candidates
            meta.alias_source = out.alias_source
        # ETA·content_id·판단만 남긴다 (좌표는 남기지 않음)
        log.info("eta-check rid=%s content_id=%s eta_s=%d limit_s=%d attempt=%d decision=%s reason=%s next=%s",
                 meta.request_id, body.content_id[:20], body.observed_eta_seconds, body.drive_minutes * 60,
                 res["attempt"], res["decision"], res["reason"],
                 out.body["reveal"]["content_id"] if out else "-")
        return {"request_id": meta.request_id, **res,
                "recommendation": {"request_id": meta.request_id, **out.body} if out else None}

    @app.get("/healthz")
    async def healthz():
        idx = index.index
        return {"status": "ok", "index_spots": len(idx.spots) if idx else 0,
                "index_age_min": int((time.time() - idx.loaded_at) / 60) if idx else None}

    return app


def __getattr__(name: str):
    # `uvicorn app.main:app` 에서만 실제 앱을 만든다 (테스트 import 시 부작용 방지)
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
