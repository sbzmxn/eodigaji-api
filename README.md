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
| `scripts/` | `demo_call.sh`(시연 호출), `check_locations.py`(7곳 점검), `generate_alias_pool.py`(가명 후보 일회성 생성, Ollama 필요) |
| `docs/` | `API_FOR_APP.md`(앱 팀용 API 명세), `BlindNames.kt`(앱용 가명 100개), `TEAM_HANDOFF.md`(팀원별 연동), `GIT_GUIDE.md`(저장소 사용법), `EVALUATION.md`(평가 결과) |

## 빠른 시작

서버는 실행 중 LLM 을 쓰지 않으므로 **GPU·Ollama 없이** 실행됩니다. Python 3.10 기준입니다.

```bash
python3.10 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # 키 입력 (아래)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

처음 기동할 때 전국 관광지 목록을 받아(관광 API 3회, 수 초) `data/cache/` 에 하루 동안 캐시합니다. `GET /healthz` 의 `index_spots` 가 0보다 크면 준비된 것입니다.

```bash
# 좌표는 항상 인자로: <위도> <경도> <drive_minutes 15~120> [keyword_ids]
scripts/demo_call.sh 37.4979 127.0276 30 forest,walk
BASE=https://xxxx.ngrok-free.app scripts/demo_call.sh 33.4996 126.5312 45
```

가명은 팀이 검수한 100개 목록(`config/alias_pool.yaml`)에서 고릅니다. 앱은 같은 목록을 [`docs/BlindNames.kt`](docs/BlindNames.kt)로 직접 쓰며, 서버 응답의 `blind_name` 은 호환용입니다.
서버는 시작할 때 목록 전체를 금지어(장소 유형어·지명·읍면동 등)로 검사하고, 하나라도 걸리면 그 이름을 로그에 남기고 시작하지 않습니다.

## .env 설정

```
TOURAPI_KOR_KEY=   # 공공데이터포털 '한국관광공사_국문 관광정보 서비스_GW' Decoding 키
```

키는 로그·에러 메시지에서 마스킹되며 `.env` 는 git 에서 제외됩니다.

## API 요약

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/api/v1/keywords?lat=&lng=&drive_minutes=` | 키워드 9개의 활성 여부와 비활성 사유 (예: `"지금 시간으론 멀어요"`, `"일몰은 17:25이에요"`) |
| POST | `/api/v1/recommend` | 장소 1곳 추천: 목적지 좌표, 힌트(주차·요금·도착 시 운영), 도착 후 공개 정보, 직선거리·서버 추정 운전시간 (+ 호환용 가명). `retry_context` 로 실제 ETA 를 보내면 그 요청만 속도를 보정해 재추천 |
| GET | `/healthz` | 관광지 인덱스 적재 상태 |

```json
// POST /api/v1/recommend
{"latitude": 37.4979, "longitude": 127.0276, "drive_minutes": 30, "keyword_ids": ["forest", "walk"], "exclude_content_ids": []}
```

- 키워드 id: `sea` 바다, `water` 호수·강, `forest` 숲, `walk` 산책, `view` 전망, `quiet` 고즈넉, `sunrise` 일출, `sunset` 노을, `night` 야경 (0~3개).
- 에러는 `{"error": {"code", "message", "request_id"}}` 형식입니다.
  코드: `INVALID_REQUEST`, `INVALID_LOCATION`, `INVALID_KEYWORD`, `KEYWORD_UNAVAILABLE`, `NO_CANDIDATES`, `UPSTREAM_TOUR_API_ERROR`, `INTERNAL_ERROR`.
- 자동 재추천: 앱이 `requestTrip` 실제 ETA 가 설정 시간을 넘으면 `"retry_context": {"previous_content_ids": [...], "observed_eta_seconds": 1530}` 을 담아 다시 요청한다(최대 3회). 보정값은 저장하지 않는다.
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

## 평가 결과

7곳 × 4개 운전 시간 점검(56/56 정상), 섬 규칙, 가명 목록 생성·검수, 개인정보 검증: **[docs/EVALUATION.md](docs/EVALUATION.md)**
