# 어디가지? — 블라인드 스팟 추천 서버

운전자가 고른 **힐링 키워드**와 **편도 운전 시간**에 맞는 장소 1곳을 골라, 실제 이름 대신 감성 가명으로 알려 주는 FastAPI 서버입니다.
장소는 한국관광공사 관광정보를 규칙 기반 점수로 고르고, 실제 이름·주소는 앱이 도착·주차 후에만 공개합니다.
현대자동차그룹 Pleos App Market 공모전 출품작(팀 "이번엔")의 백엔드입니다.

## 폴더 구조

| 경로 | 내용 |
|---|---|
| `app/` | 서버 코드 (API, 추천 흐름, 관광 API 클라이언트, 점수·일몰·가명·캐시) |
| `config/` | 튜닝 값: `settings.yaml`(반경·점수·시간 구간·섬 규칙), `keywords.yaml`(키워드↔관광 분류), `alias_pool.yaml`(검수된 가명 100개), `alias.yaml`(가명 금지어 규칙) |
| `tests/` | 단위 테스트(mock), 실제 API 통합 테스트(`-m integration`), 테스트 데이터(출발 좌표 7곳 등) |
| `scripts/` | `check_locations.py`(7곳 점검), `warmup.py`(사용 전 캐시 예열), `measure_speed.py`(속도 측정), `generate_alias_pool.py`(가명 후보 일회성 생성, Ollama 필요) |
| `docs/` | `API_FOR_APP.md`(앱용 API 명세), `EVALUATION.md`(평가 결과) |

## 빠른 시작

서버는 실행 중 LLM 을 쓰지 않으므로 **GPU·Ollama 없이** 실행됩니다. Python 3.10 기준입니다.

```bash
python3.10 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # 키 입력 (아래)
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

외부에서 접속해야 하면 서버를 직접 열지 말고 HTTPS 리버스 프록시 뒤에 둡니다.

처음 기동할 때 전국 관광지 목록을 받아(관광 API 3회, 수 초) `data/cache/` 에 하루 동안 캐시합니다. `GET /healthz` 의 `index_spots` 가 0보다 크면 준비된 것입니다.

```bash
# 호출 예시. APP_API_KEYS 를 설정했으면 그중 하나를 KEY 에 (비워 두면 헤더 없이도 됨)
KEY=<앱 API 키>
curl -H "X-API-Key: $KEY" "http://127.0.0.1:8000/api/v1/keywords?lat=37.4979&lng=127.0276&drive_minutes=30"
curl -X POST "http://127.0.0.1:8000/api/v1/recommend" -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"latitude":37.4979,"longitude":127.0276,"drive_minutes":30,"keyword_ids":["forest","walk"],"exclude_content_ids":[]}'
```

가명은 팀이 검수한 100개 목록(`config/alias_pool.yaml`)에서 고릅니다. 앱은 같은 목록을 앱 안에 두고 직접 고르며, 서버 응답의 `blind_name` 은 호환용입니다.
서버는 시작할 때 목록 전체를 금지어(장소 유형어·지명·읍면동 등)로 검사하고, 하나라도 걸리면 그 이름을 로그에 남기고 시작하지 않습니다.

## .env 설정

```
TOURAPI_KOR_KEY=   # 공공데이터포털 '한국관광공사_국문 관광정보 서비스_GW' Decoding 키
APP_API_KEYS=      # 앱 → 서버 API 키(요청 헤더 X-API-Key). 쉼표로 여러 개
```

키는 로그·에러 메시지에서 마스킹되며 `.env` 는 git 에서 제외됩니다.

### 앱 API 키 (`APP_API_KEYS`)

- 키 만들기(32자 이상): `python -c "import secrets; print(secrets.token_urlsafe(32))"`
- **값이 있으면 검사 켜짐**: `/api/v1/` 아래 모든 요청에 `X-API-Key` 가 등록된 키 중 하나와 같아야 합니다(없거나 틀리면 401 `UNAUTHORIZED`). `/healthz` 는 키 없이 되고, `/docs`·`/redoc`·`/openapi.json` 은 꺼집니다.
- **비어 있으면 검사 꺼짐**(로컬 개발용): 시작할 때 `APP_API_KEYS 가 비어 있어 API 키 검사를 하지 않습니다` 경고를 한 번 남기고, `/docs` 가 열립니다.
- 키가 하나라도 32자 미만이면 서버가 시작하지 않고 몇 번째 키인지 로그에 남깁니다(키 값은 남기지 않음).
- 키 교체: `APP_API_KEYS=<새 키>,<옛 키>` 로 둘 다 받다가 앱이 모두 새 키로 바뀌면 옛 키를 지웁니다.
- 환경변수 `APP_API_KEYS` 가 있으면 `.env` 보다 우선합니다. `scripts/check_locations.py`·`measure_speed.py` 는 첫 번째 키를 헤더에 붙입니다.

## API 요약

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/api/v1/keywords?lat=&lng=&drive_minutes=` | 키워드 9개의 활성 여부와 비활성 사유 (예: `"지금 시간으론 멀어요"`, `"일몰은 17:25이에요"`) |
| POST | `/api/v1/recommend` | 장소 1곳 추천: 목적지 좌표, 힌트(주차·요금·도착 시 운영), 도착 후 공개 정보, 직선거리·서버 추정 운전시간 (+ 호환용 가명). `retry_context` 로 실제 ETA 를 보내면 그 요청만 속도를 보정해 재추천. `require_parking`·`exclude_paid` 조건 |
| POST | `/api/v1/eta-check` | 앱이 `requestTrip` 으로 잰 ETA 를 보내면 서버가 `accept` / `retry`(새 추천 포함) / `fallback` 을 판단. 상태 저장 없음, 최대 3회 |
| GET | `/healthz` | 관광지 인덱스 적재 상태 |

```json
// POST /api/v1/recommend
{"latitude": 37.4979, "longitude": 127.0276, "drive_minutes": 30, "keyword_ids": ["forest", "walk"], "exclude_content_ids": []}
```

- 키워드 id: `sea` 바다, `water` 호수·강, `forest` 숲, `walk` 산책, `view` 전망, `quiet` 고즈넉, `sunrise` 일출, `sunset` 노을, `night` 야경 (0~3개).
- 에러는 `{"error": {"code", "message", "request_id"}}` 형식입니다.
  코드: `UNAUTHORIZED`(401, `X-API-Key` 없음·틀림), `INVALID_REQUEST`, `INVALID_LOCATION`, `INVALID_KEYWORD`, `KEYWORD_UNAVAILABLE`, `NO_CANDIDATES`, `CONDITION_NOT_MET`, `UPSTREAM_TOUR_API_ERROR`, `INTERNAL_ERROR`.
- 조건: `"require_parking": true` 면 주차 '가능'으로 확인된 곳만, `"exclude_paid": true` 면 '유료' 제외(무료·정보 없음 포함). 점수 상위부터 5곳씩, 요청당 최대 10곳 상세조회. 못 찾으면 `CONDITION_NOT_MET`(404). `/keywords` 도 같은 쿼리를 받아 캐시 기반 추정으로 키워드 타일을 끈다.
- 자동 재추천: 앱은 `requestTrip` ETA 를 `POST /api/v1/eta-check` 에 보내고 `decision` 만 따른다. 서버가 시간 비교·횟수(최대 3회)·재추천(속도 보정 + 이전 후보 제외)을 맡는다. 예전 `/recommend` 의 `retry_context` 방식도 호환용으로 동작한다. 보정값은 저장하지 않는다.
- 서버는 사용자 좌표를 저장·로그하지 않고, 관광 API 로도 보내지 않습니다.

요청·응답 전체 예시, Kotlin 데이터 클래스·Retrofit 인터페이스, 에러별 앱 문구, NaviHelper 주의사항은 **[docs/API_FOR_APP.md](docs/API_FOR_APP.md)** 에 있습니다.
FastAPI 자동 문서: 서버 실행 후 `/docs`.

## 테스트

```bash
pytest                               # 단위 테스트 (네트워크·GPU 불필요). API 테스트는 출발 좌표 7곳 각각에서 실행
pytest -m integration                # 실제 관광 API 통합 테스트 (.env 키 필요)
python scripts/check_locations.py    # 7곳 × 15·30·60·120분 점검 → reports/locations_check.md
python scripts/measure_speed.py      # /recommend 1·2·3회 연속 소요 시간(캐시 있음/없음) → reports/speed.md, 관광 API 약 370회
python scripts/generate_alias_pool.py --model qwen2.5:7b   # 가명 후보 생성(일회성, Ollama 필요) → reports/
```

`reports/` 와 `data/` 는 실행 시 자동으로 만들어지며 git 에서 제외됩니다.

## 서비스 시작 전 캐시 예열 (`scripts/warmup.py`)

자주 쓰는 출발지 주변의 상세·요금 정보를 미리 받아 두면 사용 중 관광 API 호출과 대기 시간이 거의 없어집니다.
출발지 × 운전시간 × 키워드(없음 + 9개) 조합마다 1차 점수 상위 10곳을 조회합니다(곳당 최대 3회, 이미 캐시된 곳은 건너뜀).
캐시에 소개글이 있는 곳은 1차 점수 가산을 받으므로 예열한 곳이 먼저 뽑힙니다.

```bash
# 1) 호출 없이 대상 수·예상 호출 수 확인
python scripts/warmup.py --origin 37.4979,127.0276 --drives 30,60 --max-calls 300 --dry-run
# 2) 실제 예열. --max-calls 는 필수 (하루 한도 1,000회 - 그날 이미 쓴 양)
python scripts/warmup.py --origin 37.4979,127.0276 --drives 30,60 --max-calls 300
```

- 출발지 1곳 × 운전시간 1개에 보통 300회 이하입니다. 상한에 닿으면 멈추고, 다음 날 같은 명령으로 이어서 실행하면 남은 곳만 받습니다.
- 상세 캐시는 7일 유지되므로 **서비스 시작 1~2일 전**에 나눠 돌립니다. 서비스 당일은 한도를 비워 둡니다.
- `--origin` 은 여러 번 줄 수 있고, `--keywords none,forest,walk` 로 조합을 줄일 수 있습니다.

## 평가 결과

7곳 × 4개 운전 시간 점검(56/56 정상), 섬 규칙, 가명 목록 생성·검수, 개인정보 검증: **[docs/EVALUATION.md](docs/EVALUATION.md)**
