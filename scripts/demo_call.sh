#!/usr/bin/env bash
# /keywords → /recommend 를 호출한다. 좌표는 기본값 없이 항상 인자로 받는다.
#   scripts/demo_call.sh <위도> <경도> <drive_minutes> [keyword_ids(쉼표구분)]
#   BASE=https://xxxx.ngrok-free.app scripts/demo_call.sh 37.4979 127.0276 30 forest,walk
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

pretty() { local s; s="$(cat)"; printf '%s' "$s" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || printf '%s\n' "$s"; }

echo "== GET /api/v1/keywords (drive_minutes=$DRIVE)"
curl -sS "$BASE/api/v1/keywords?lat=$LAT&lng=$LNG&drive_minutes=$DRIVE" | pretty

KW_JSON="[]"
if [ -n "$KW" ]; then
  KW_JSON="[$(echo "$KW" | sed 's/[^,][^,]*/"&"/g')]"
fi
echo
echo "== POST /api/v1/recommend (keyword_ids=$KW_JSON)"
RESP="$(curl -sS -X POST "$BASE/api/v1/recommend" -H 'Content-Type: application/json' \
  -d "{\"latitude\": $LAT, \"longitude\": $LNG, \"drive_minutes\": $DRIVE, \"keyword_ids\": $KW_JSON, \"exclude_content_ids\": []}" \
  -w '\n%{http_code} %{time_total}')"
printf '%s' "${RESP%$'\n'*}" | pretty
echo "(HTTP ${RESP##*$'\n'}s)"
