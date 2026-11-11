"""요청 단위 메타데이터 로그.

지금 남기는 것: request_id, 엔드포인트, 처리 시간, 후보 수, 결과 코드, alias_source.
남기지 않는 것: 좌표, 키워드 조합, 사용자 식별자, 쿼리/바디.

출시 대비 확장 지점 (위치정보법 '위치정보 이용·제공사실 확인자료'):
- 누가(requester: 서비스 가입자 식별자, 앱이 보내기 시작하면), 언제(started_at), 어떤 방법(method),
  제3자 제공 여부(provided_to: 지금은 좌표를 외부로 보내지 않으므로 해당 없음)를 남기되 위치정보 자체는 제외한다.
- AuditSink 구현을 바꿔 끼우면(파일/DB, 보존기간 관리) 라우트 코드는 그대로 둔다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol

log = logging.getLogger("eodigaji.request")


@dataclass
class RequestMeta:
    request_id: str
    endpoint: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    elapsed_ms: int = 0
    result_code: str = "OK"
    n_candidates: Optional[int] = None
    alias_source: Optional[str] = None
    # --- 출시 시 확장 (지금은 비워 둔다) ---
    requester: Optional[str] = None
    method: Optional[str] = None
    provided_to: Optional[str] = None


class AuditSink(Protocol):
    def record(self, meta: RequestMeta) -> None: ...


class LogSink:
    def record(self, meta: RequestMeta) -> None:
        log.info(
            "rid=%s endpoint=%s ms=%d candidates=%s code=%s alias=%s",
            meta.request_id, meta.endpoint, meta.elapsed_ms,
            "-" if meta.n_candidates is None else meta.n_candidates,
            meta.result_code, meta.alias_source or "-",
        )
