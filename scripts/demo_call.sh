#!/usr/bin/env bash
# /keywords → /recommend 를 호출한다. 좌표는 기본값 없이 항상 인자로 받는다.
#   scripts/demo_call.sh <위도> <경도> <drive_minutes> [keyword_ids(쉼표구분)]
#   BASE=<외부 접속 주소> scripts/demo_call.sh 37.4979 127.0276 30 forest,walk
# API 키: 환경변수 APP_API_KEYS 또는 .env 의 APP_API_KEYS 첫 번째 키를 X-API-Key 헤더로 붙인다(비어 있으면 생략).
set -euo pipefail
if [ $# -lt 3 ]; then
  echo "사용법: $0 <위도> <경도> <drive_minutes 15~120> [keyword_ids 예: forest,walk]" >&2
  exit 2
fi
BASE="${BASE:-http://localhost:8000}"
LAT="$1"
LNG="$2"
DRIVE="$3"
KW="${4:-}"

ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
KEYS="${APP_API_KEYS:-}"
if [ -z "$KEYS" ] && [ -f "$ENV_FILE" ]; then
  KEYS="$(sed -n 's/^[[:space:]]*APP_API_KEYS[[:space:]]*=[[:space:]]*//p' "$ENV_FILE" | tail -n 1 | tr -d "\"'\r")"
fi
KEY="$(printf '%s' "${KEYS%%,*}" | tr -d '[:space:]')"
AUTH=()
if [ -n "$KEY" ]; then
  AUTH=(-H "X-API-Key: $KEY")
fi

pretty() { local s; s="$(cat)"; printf '%s' "$s" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || printf '%s\n' "$s"; }

echo "== GET /api/v1/keywords (drive_minutes=$DRIVE)"
curl -sS ${AUTH[@]+"${AUTH[@]}"} "$BASE/api/v1/keywords?lat=$LAT&lng=$LNG&drive_minutes=$DRIVE" | pretty

KW_JSON="[]"
if [ -n "$KW" ]; then
  KW_JSON="[$(echo "$KW" | sed 's/[^,][^,]*/"&"/g')]"
fi
echo
echo "== POST /api/v1/recommend (keyword_ids=$KW_JSON)"
RESP="$(curl -sS ${AUTH[@]+"${AUTH[@]}"} -X POST "$BASE/api/v1/recommend" -H 'Content-Type: application/json' \
  -d "{\"latitude\": $LAT, \"longitude\": $LNG, \"drive_minutes\": $DRIVE, \"keyword_ids\": $KW_JSON, \"exclude_content_ids\": []}" \
  -w '\n%{http_code} %{time_total}')"
printf '%s' "${RESP%$'\n'*}" | pretty
echo "(HTTP ${RESP##*$'\n'}s)"
