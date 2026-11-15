# 어디가지? 추천 서버 API 명세

> 버전 v0.1.0 (2026-11-15 기준). 아래 JSON 예시는 실제 서버 응답을 그대로 옮긴 것이다(request_id 등 값만 다름).

## 0. 한눈에 보기

| 순서 | 호출 | 언제 |
|---|---|---|
| 1 | `GET /api/v1/keywords` | 운전 시간 프리셋이나 조건 선택 카드(주차 가능한 곳만·유료 체험 제외)가 바뀔 때(디바운스 300ms 권장). 키워드 타일의 활성/비활성과 안내 문구 |
| 2 | `POST /api/v1/recommend` | [비밀 장소 뽑기] / [다시 뽑기] |
| 3 | (앱) NaviHelper `requestTrip` → `POST /api/v1/eta-check` | 실제 ETA(초)를 서버에 보낸다. 서버가 `decision` 으로 답한다: `accept`·`fallback` 이면 `route_content_id` 로 안내, `retry` 면 같이 온 새 추천으로 3번을 반복([2-3](#2-3-eta-판단-post-apiv1eta-check)). 앱은 시간 비교·횟수 세기를 하지 않는다 |
| 4 | (앱) NaviHelper `requestRoute` | 응답의 `destination` 좌표 + 앱이 `BlindNames.pick()` 으로 고른 가명 |
| 5 | (앱) 도착·주차 후 | `reveal` 표시, 도감 저장 |

- 시간 입력은 **목적지까지 편도 운전 시간** `drive_minutes` (15~120분). 예전 `available_minutes` 는 폐기됐다(보내면 400).
- **가명은 앱이 서버의 `config/alias_pool.yaml` 과 같은 100개 목록을 앱 안에 두고 고른다([7](#7-가명)). 서버 응답의 `blind_name` 은 쓰지 않아도 된다.** (호환을 위해 당분간 같은 목록에서 고른 값을 내려준다.)
- 서버는 좌표를 저장·로그하지 않는다. 사용자 식별자는 보내지 않는다.
- **모든 `/api/v1/` 요청에 헤더 `X-API-Key: <앱 API 키>` 를 붙인다**(OkHttp 인터셉터로 한 곳에서, [3](#3-kotlin-gson--retrofit)). 없거나 틀리면 401 `UNAUTHORIZED`. `/healthz` 는 키 없이 된다.
- 타임아웃 권장: `/keywords` 5초, `/recommend` 10초(호출 1회). 재추천을 포함한 로딩 화면 전체는 **12초** ([2-5](#2-5-로딩-시간과-타임아웃)).

## 1. 엔드포인트와 curl

### 공통 헤더 `X-API-Key`

| 헤더 | 값 | 대상 |
|---|---|---|
| `X-API-Key` | 서버 `.env` 의 `APP_API_KEYS` 에 등록된 키 중 하나 (개별로 전달받음) | `/api/v1/` 아래 모든 요청 (`keywords`·`recommend`·`eta-check`). `/healthz` 는 필요 없음 |

- 키가 없거나 틀리면 **401 `UNAUTHORIZED`** ([4. 에러](#4-에러)). 없는 경우와 틀린 경우를 구분해 알려 주지 않는다.
- 서버에 키가 등록돼 있지 않으면(로컬 개발) 검사하지 않는다. 이때만 `/docs`(Swagger)가 열린다. 키 검사가 켜진 서버에서는 `/docs`·`/openapi.json` 이 꺼져 있으니 이 문서를 기준으로 한다.
- 키는 교체할 때 서버가 옛 키·새 키를 잠시 함께 받는다. 앱은 키 하나만 넣으면 된다.
- **한계: 앱에 넣은 키는 APK 에서 추출될 수 있어 '아무나 호출하지 못하게 하는 장치'이지 강한 인증이 아니다. 출시 시 HTTPS·요청 횟수 제한과 함께 쓴다.**

```bash
BASE=http://localhost:8000
KEY=<앱 API 키>                      # 개별로 전달받은 키. 문서·채팅에 붙여 넣지 않는다

# 키워드 상태
curl -H "X-API-Key: $KEY" "$BASE/api/v1/keywords?lat=37.4979&lng=127.0276&drive_minutes=30"

# 추천
curl -X POST "$BASE/api/v1/recommend" -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"latitude":37.4979,"longitude":127.0276,"drive_minutes":30,"keyword_ids":["forest","walk"],"exclude_content_ids":[]}'

# ETA 판단 (requestTrip 으로 잰 ETA 를 보냄)
curl -X POST "$BASE/api/v1/eta-check" -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"latitude":37.4979,"longitude":127.0276,"drive_minutes":30,"keyword_ids":["forest","walk"],"content_id":"3352052","observed_eta_seconds":2160,"previous_content_ids":["3352052"]}'

# 서버 상태 (관광지 인덱스 적재 여부). 키 필요 없음
curl "$BASE/healthz"
```

## 2. 요청·응답

### 2-1. `GET /api/v1/keywords`

| 쿼리 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `lat` | Double | ✔ | 현재 위도 (WGS84 십진 도) |
| `lng` | Double | ✔ | 현재 경도 |
| `drive_minutes` | Int | ✔ | 편도 운전 시간 15~120 |
| `require_parking` | Boolean | | 기본 `false`. [주차 가능한 곳만] 조건 선택 카드. `/recommend` 와 같은 값을 보낸다 |
| `exclude_paid` | Boolean | | 기본 `false`. [유료 체험 제외] 조건 선택 카드 |

응답 200 (서울 강남, 30분, 20:59 호출 → 도착 21:29)

```json
{
  "request_id": "97923e715def4283a67ce57045d370fd",
  "arrival_time": "21:29",
  "sunset_time": "17:25",
  "keywords": [
    {"id": "sea", "label": "바다", "enabled": false, "reason": "지금 시간으론 멀어요"},
    {"id": "water", "label": "호수·강", "enabled": true, "reason": null},
    {"id": "forest", "label": "숲", "enabled": true, "reason": null},
    {"id": "walk", "label": "산책", "enabled": true, "reason": null},
    {"id": "view", "label": "전망", "enabled": true, "reason": null},
    {"id": "quiet", "label": "고즈넉", "enabled": true, "reason": null},
    {"id": "sunrise", "label": "일출", "enabled": false, "reason": "일출은 07:07이에요"},
    {"id": "sunset", "label": "노을", "enabled": false, "reason": "일몰은 17:25이에요"},
    {"id": "night", "label": "야경", "enabled": true, "reason": null}
  ]
}
```

- `keywords` 는 항상 9개, 항상 이 순서: `sea, water, forest, walk, view, quiet, sunrise, sunset, night`.
- `enabled=false` 면 키워드 타일을 꺼짐 상태로 그리고 `reason` 을 그대로 보여 준다. `reason` 은 `enabled=true` 일 때 `null`.
- 비활성 사유 문구는 서버가 정한다:
  - `지금 시간으론 멀어요` — 이 운전 시간 안에 해당 장소가 3곳 미만
  - `조건에 맞는 곳이 적어요` — 조건 카드를 켰을 때, 조건에 맞을 것으로 **추정**되는 곳이 3곳 미만. 추정은 서버 캐시에 있는 주차·요금 정보로 하고, 모르는 곳은 통과율(주차 0.7, 요금 0.95)로 센다. 추정이라 켜져 있어도 `/recommend` 가 `CONDITION_NOT_MET` 을 줄 수 있다
  - `일출은 HH:MM이에요` — 일출: 도착 예상이 일출 30분 전 ~ 일출 시각이 아님 (도착일의 일출, 좌표로 로컬 계산)
  - `일몰은 HH:MM이에요` — 노을: 도착 예상이 일몰 60분 전 ~ 20분 후가 아님
  - `HH:MM 이후 도착부터 볼 수 있어요` / `야경 안내는 23:00까지예요` — 야경: 일몰 30분 후 ~ 23:00
- `arrival_time` = 현재 + drive_minutes, `sunset_time` = 도착일 일몰(로컬 계산). 화면 표시용으로 써도 된다. 일출 시각은 별도 필드 없이 `reason` 문구에만 들어간다.

### 2-2. `POST /api/v1/recommend`

요청

```json
{
  "latitude": 37.4979,
  "longitude": 127.0276,
  "drive_minutes": 30,
  "keyword_ids": ["forest", "walk"],
  "exclude_content_ids": [],
  "require_parking": false,
  "exclude_paid": false
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `latitude`, `longitude` | Double | ✔ | 현재 위치 |
| `drive_minutes` | Int | ✔ | 15~120 |
| `keyword_ids` | List<String> | | 0~3개, 중복 없이. 0개 = "키워드 없이 뽑기". `/keywords` 에서 `enabled=true` 인 것만 보내기 |
| `exclude_content_ids` | List<String> | | [다시 뽑기] 때 이 세션에서 이미 나온 `reveal.content_id` 들 (최대 200) |
| `retry_context` | Object | | 자동 재추천 때만. 아래 [2-4](#2-4-자동-재추천-retry_context-호환용) |
| `require_parking` | Boolean | | 기본 `false`. `true` 면 주차가 `"가능"` 으로 확인된 곳만 추천(불가·정보 없음 제외) |
| `exclude_paid` | Boolean | | 기본 `false`. `true` 면 기본 입장료·이용료가 `"유료"` 인 곳 제외(무료·정보 없음은 포함) |

조건(`require_parking`, `exclude_paid`) 처리
- 점수 상위 후보부터 5곳씩 병렬로 상세 정보를 조회해, 조건을 통과한 곳이 3곳이 되면 멈춘다. 요청당 최대 10곳까지만 조회한다.
- 서버 캐시에 이미 불가·정보 없음(주차)·유료로 확인된 곳은 조회 없이 뺀다.
- 10곳 안에서 한 곳도 통과하지 못하면 `CONDITION_NOT_MET`(404). 1곳이라도 통과하면 그중에서 고른다.
- 자동 재추천(`retry_context`)과 함께 보내면 속도 보정·제외 후의 후보에 조건을 적용한다. **재추천 때도 같은 조건 값을 그대로 보낸다.**

정의되지 않은 필드는 서버가 무시한다(경고 로그만). 단 `available_minutes` 는 400.

응답 200

```json
{
  "request_id": "f031e8c12cc94bacaec3ea0852ea66ac",
  "blind_name": "숨겨진 평온",
  "destination": {"lat": 37.556468, "lng": 127.020801},
  "hint": {"parking": "불가", "fee": "무료", "open_now": true},
  "reveal": {
    "content_id": "3352052",
    "real_name": "대현산배수지공원·대현산배수지공원 모노레일",
    "address": "서울특별시 성동구 금호동1가 172",
    "overview_short": "응봉은 산 모양이 매처럼 보이고 조선시대 왕들이 매사냥을 하였다 하여 한자어로 붙여진 이름이다. 옛날에는 하나의 줄기였으나 도시개발로 인해 응봉산, 대현산, 대현배수지, 금호산, 매봉산 5개 지역으로 나뉘어 각 자치…",
    "image_url": "https://tong.visitkorea.or.kr/cms/resource/05/3351805_image2_1.jpg",
    "theme": "산책",
    "matched_keywords": ["walk"]
  },
  "alias_source": "pool",
  "straight_km": 6.54,
  "estimated_drive_seconds": 1020
}
```

| 필드 | 언제 보여 주나 | 값 |
|---|---|---|
| `blind_name` | **쓰지 않아도 됨** | 호환용. 앱은 `BlindNames.pick()` 으로 직접 고른다(같은 100개 목록) |
| `destination` | NaviHelper 로만 전달 | WGS84 십진 도 |
| `hint.parking` | 출발 전 힌트 카드 | `"가능"` / `"불가"` / `null` |
| `hint.fee` | 출발 전 힌트 카드 | 기본 입장료 기준 `"무료"` / `"유료"` / `null` |
| `hint.open_now` | 출발 전 힌트 카드 | **도착 예상 시각** 기준 운영 여부 `true` / `false` / `null`. `false` 는 다른 후보가 모두 닫혀 있을 때만 나온다 |
| `reveal.*` | **도착·주차 후에만** | 실제 이름, 주소, 소개(최대 약 120자), 사진, 테마(예: `숲·산책`), 매칭된 키워드 |
| `reveal.image_url` | 언박싱 | `null` 일 수 있음(이용 조건 불명 이미지는 서버가 뺌). 항상 https |
| `alias_source` | 표시하지 않음 | 항상 `"pool"` (검수된 가명 목록) |
| `straight_km` | 표시하지 않음(디버그용) | 출발지→목적지 직선거리 km (소수 둘째 자리) |
| `estimated_drive_seconds` | 표시하지 않음(재추천 판단 참고) | 서버 추정 편도 운전시간(초). 도로 경로가 아닌 직선거리 기반 추정이므로 **재추천 여부는 `requestTrip` 의 실제 ETA 로 판단**한다. `retry_context` 로 보정했으면 보정 속도 기준 |

힌트 값 표시 규칙

| 서버 값 | 표시 문구 | 조건을 켰을 때 |
|---|---|---|
| `hint.parking == "가능"` | 주차 가능 | `require_parking=true` 면 항상 이 문구 |
| 그 외(`"불가"`, `null`) | 주차 불가 또는 알 수 없음 | `require_parking=true` 면 나오지 않음 |
| `hint.fee == "유료"` | 유료 체험 | `exclude_paid=true` 면 나오지 않음 |
| 그 외(`"무료"`, `null`) | 무료 체험 또는 알 수 없음 | `exclude_paid=true` 면 항상 이 문구 |

`open_now` 가 `null` 이면 숨긴다.

### 2-3. ETA 판단 (`POST /api/v1/eta-check`)

앱이 `requestTrip` 으로 잰 실제 ETA 를 보내면 서버가 **시간 안인지 판단하고, 필요하면 재추천까지 해서** 돌려준다. ETA 는 앱만 잴 수 있으므로(Pleos `requestTrip`) 서버는 내비를 부르지 않는다. 서버는 상태를 저장하지 않으므로 **앱이 매번 전부 보낸다.**

요청 — `/recommend` 와 같은 검색 필드(같은 검증) + 세 필드

```json
{
  "latitude": 37.4979,
  "longitude": 127.0276,
  "drive_minutes": 30,
  "keyword_ids": ["forest", "walk"],
  "exclude_content_ids": [],
  "require_parking": false,
  "exclude_paid": false,
  "content_id": "3352052",
  "observed_eta_seconds": 2160,
  "previous_content_ids": ["3352052"]
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `latitude` … `exclude_paid` | | | `/recommend` 와 같다. **첫 추천 때와 같은 값**을 보낸다(조건 카드 포함) |
| `content_id` | String | ✔ | 방금 ETA 를 잰 후보 (`reveal.content_id`) |
| `observed_eta_seconds` | Int | ✔ | 그 후보까지 `requestTrip` ETA(초), 1~21600 |
| `previous_content_ids` | List<String> | ✔ | 지금까지 나온 후보 전부(오래된 → 최근, 1~200개). **마지막 원소가 `content_id`** 여야 한다 |

판단 규칙 (`settings.yaml` 의 `eta_check`: 허용 오차 0초, 최대 3회)

| 조건 | `decision` | `reason` | `route_content_id` | `recommendation` |
|---|---|---|---|---|
| ETA ≤ `drive_minutes × 60` (+ 오차) | `accept` | `within_limit` | `content_id` | `null` |
| 넘음, `previous_content_ids` 가 3개 미만 | `retry` | `over_limit` | `null` | 새 추천 (`/recommend` 응답과 같은 형식) |
| 넘음, 이미 3번째 후보 | `fallback` | `max_attempts` | `content_id` | `null` |
| 넘음, 재추천할 곳이 없음 | `fallback` | `no_candidates` | `content_id` | `null` |
| 넘음, 조건 카드에 맞는 곳이 없음 | `fallback` | `condition_not_met` | `content_id` | `null` |
| 넘음, 고른 키워드를 이제 쓸 수 없음(예: 노을 시간이 지남) | `fallback` | `keyword_unavailable` | `content_id` | `null` |
| 넘음, 재추천 중 관광 API 장애 | `fallback` | `upstream_error` | `content_id` | `null` |
| 넘음, 재추천 중 서버 내부 오류 | `fallback` | `internal_error` | `content_id` | `null` |

- 재추천은 `/recommend` 의 `retry_context` 경로를 서버 안에서 그대로 쓴다: 방금 후보의 실제 ETA 로 속도를 보정하고, `previous_content_ids`·`exclude_content_ids` 를 모두 빼고, 조건 카드를 그대로 적용한다.
- `attempt` = 안내할 후보(`accept`·`fallback`) 또는 새 추천(`retry`)이 몇 번째 추천인지. 로딩 화면의 "(2/3)" 표시에 그대로 쓴다.
- 재추천이 실패해도(후보 없음, 조건 불충족, 키워드 시간 지남, 관광 API 장애, 내부 오류) 에러가 아니라 `fallback` 200 으로 온다. 서버 로그에는 원래 에러 코드가 남는다.
- **에러로 오는 것은 요청 검증 오류(`INVALID_REQUEST`·`INVALID_LOCATION`·`INVALID_KEYWORD`, 400)와 API 키 오류(`UNAUTHORIZED`, 401)뿐이다.** 둘 다 앱 버그이므로 로그를 남기고 방금 후보로 안내한다. 그 밖에 앱이 따로 처리할 것은 네트워크 실패(연결 실패·타임아웃)뿐이다.
- 입력 오류(`INVALID_REQUEST`): `content_id` 가 `previous_content_ids` 의 마지막이 아님, `observed_eta_seconds` ≤ 0, 필드 누락. 위치·운전시간·키워드 검증은 `/recommend` 와 같다(`accept` 일 때도 검사).
- 서버 로그에는 request_id, content_id, ETA, 판단만 남고 좌표는 남지 않는다.

응답 200 — accept (실제 응답, 30분 = 1800초 안)

```json
{"request_id": "dfda9c75b8824038b8f651f7fba3fcbd", "decision": "accept", "attempt": 1, "max_attempts": 3,
 "reason": "within_limit", "route_content_id": "3352052", "recommendation": null}
```

응답 200 — retry (위 요청: ETA 2160초 > 1800초 → 속도 보정 후 더 가까운 곳)

```json
{
  "request_id": "089ed9ea165b44a18e9b9bf5a8881bfa",
  "decision": "retry",
  "attempt": 2,
  "max_attempts": 3,
  "reason": "over_limit",
  "route_content_id": null,
  "recommendation": {
    "request_id": "089ed9ea165b44a18e9b9bf5a8881bfa",
    "blind_name": "어린이의 웃음",
    "destination": {"lat": 37.4692, "lng": 127.0171},
    "hint": {"parking": "가능", "fee": "무료", "open_now": true},
    "reveal": {
      "content_id": "2757831",
      "real_name": "우면산자연생태공원",
      "address": "서울특별시 서초구 우면동 산 34-1",
      "overview_short": "우면산은 높이 293m로 서초구 우면동과 서초동, 양재동 등지의 도심에서 쉽게 오를 수 있는 산이다. 산모양이 소가 졸고 있는 모양이라 하여 우면산이라 이름이 붙여졌다.",
      "image_url": "https://tong.visitkorea.or.kr/cms/resource/96/3540696_image2_1.jpg",
      "theme": "숲·산책",
      "matched_keywords": ["forest", "walk"]
    },
    "alias_source": "pool",
    "straight_km": 3.32,
    "estimated_drive_seconds": 1098
  }
}
```

응답 200 — fallback (세 번째 후보도 넘음: `content_id: "1602451"`, `previous_content_ids: ["3352052", "2757831", "1602451"]`, ETA 1980초)

```json
{"request_id": "9f5e98dc4ed440c0a53db9029a28552f", "decision": "fallback", "attempt": 3, "max_attempts": 3,
 "reason": "max_attempts", "route_content_id": "1602451", "recommendation": null}
```

`route_content_id` 는 앱이 들고 있는 추천 응답 중 그 `content_id` 의 것으로 안내하라는 뜻이다(`destination`·`reveal` 은 다시 보내지 않는다).

앱 쪽 호출 흐름 (앱은 시간 비교·횟수 세기를 하지 않고 `decision` 만 따른다)

1. `/api/v1/recommend` 로 첫 추천을 받는다.
2. 그 `destination` 으로 `requestTrip` → ETA(초).
3. `/api/v1/eta-check` 에 검색 조건 전부 + `content_id`(방금 후보) + ETA + `previous_content_ids`(지금까지 받은 후보 전부, 마지막 = 방금 후보)를 보낸다.
4. `retry` 면 같이 온 `recommendation` 이 다음 후보다. 2번으로 돌아간다. `accept`·`fallback` 이면 `route_content_id` 의 추천으로 `requestRoute` 안내를 시작한다.
5. 전체 **12초 제한은 앱이 건다**([2-5](#2-5-로딩-시간과-타임아웃)). 넘으면 마지막으로 받은 추천으로 안내한다.

```kotlin
val seen = linkedMapOf<String, RecommendResponse>()      // content_id → 추천 (받은 순서 유지)
var current: RecommendResponse? = null

withTimeoutOrNull(12_000) {                               // 전체 제한 12초 (앱)
    val first = api.recommend(baseRequest).body() ?: return@withTimeoutOrNull
    seen[first.reveal.contentId] = first
    current = first
    while (true) {
        val c = current!!
        val eta = requestTripEtaSeconds(c.destination)   // NaviHelper requestTrip, 초
        val res = api.etaCheck(baseRequest.toEtaCheck(c.reveal.contentId, eta, seen.keys.toList()))
            .body() ?: break                              // INVALID_*·UNAUTHORIZED(앱 버그, 로그) → 지금 후보로 안내
        if (res.decision == "retry") {
            val next = res.recommendation!!
            seen[next.reveal.contentId] = next
            current = next
        } else {                                          // accept · fallback
            current = seen[res.routeContentId] ?: c
            break
        }
    }
}
// 네트워크 실패(IOException)는 runCatching 등으로 잡아 break 와 같이 처리 → 지금 후보로 안내
// current 로 requestRoute (12초를 넘겨도 마지막으로 받은 추천). null 이면 에러 처리.
```

- `baseRequest` 에는 사용자가 켠 조건(`requireParking`, `excludePaid`)이 들어 있어야 한다. `toEtaCheck`([3장](#3-kotlin-gson--retrofit))가 같은 조건을 그대로 옮긴다.

### 2-4. 자동 재추천 (`retry_context`, 호환용)

서버의 운전시간은 직선거리 × 우회계수 ÷ 구간 속도로 추정한 값이라 실제 도로·교통과 다를 수 있다. 앱이 `requestTrip` 으로 받은 **실제 ETA** 를 보내면, 서버는 그 요청에 한해 평균 속도를 보정해 반경과 거리 구간을 다시 계산한다.

```json
{
  "latitude": 37.4979,
  "longitude": 127.0276,
  "drive_minutes": 30,
  "keyword_ids": ["forest", "walk"],
  "exclude_content_ids": [],
  "retry_context": {
    "previous_content_ids": ["3352052"],
    "observed_eta_seconds": 1530
  }
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `retry_context.previous_content_ids` | List<String> | | 이번 자동 재추천 흐름에서 이미 나온 `reveal.content_id` (오래된 것 → 최근 순, 최대 200). **마지막 원소가 직전 후보**이고, 모두 결과에서 제외된다 |
| `retry_context.observed_eta_seconds` | Int | | 직전 후보까지 `requestTrip` 이 준 ETA(초), 1~21600. 없으면 제외만 하고 보정하지 않는다 |

서버 처리
- 속도 배율 `f = 직전 후보의 서버 추정 운전시간 / observed_eta_seconds` (0.3~2.0으로 제한). 실제가 더 오래 걸렸으면 f < 1.
- 반경 = `drive_minutes × f` 분 기준 반경, 각 후보의 예상 운전시간 = 기본 추정 ÷ f. 그래서 직전 후보의 보정된 예상시간은 실제 ETA 와 같아진다.
- 직전 후보가 서버 추정 2분 미만으로 가깝거나 목록에 없으면 보정하지 않는다(제외만).
- 보정값은 그 요청에만 쓰고 저장하지 않는다. 다음 요청에 `retry_context` 가 없으면 기본 속도로 돌아간다.

위 요청의 실제 응답(발췌): 직선 6.54km(서버 추정 1020초) 후보의 실제 ETA 가 1530초 → f = 0.67 → 더 가까운 곳이 나온다.

```json
{
  "destination": {"lat": 37.48, "lng": 127.081},
  "hint": {"parking": "가능", "fee": "무료", "open_now": true},
  "reveal": {"content_id": "1602451", "real_name": "대모산도시자연공원", "theme": "숲·산책", "matched_keywords": ["forest", "walk"], "...": "..."},
  "straight_km": 5.11,
  "estimated_drive_seconds": 1197
}
```

호환용 앱 흐름: 앱이 `requestTrip` ETA 와 `drive_minutes × 60` 을 직접 비교하고, 넘으면 `retry_context` 를 담아 `/recommend` 를 다시 부른다(최대 3회, 횟수도 앱이 센다). **새 코드는 [2-3 eta-check](#2-3-eta-판단-post-apiv1eta-check) 를 쓴다.** `eta-check` 의 재추천은 서버 안에서 이 경로를 그대로 호출하므로 결과는 같다.

- 자동 재추천의 `previous_content_ids` 와 사용자의 [다시 뽑기] `exclude_content_ids` 는 따로 보내도 되고(둘 다 제외됨), 같은 목록을 써도 된다.
- 재추천 중 `NO_CANDIDATES`(404)가 오면 더 가까운 곳이 없다는 뜻이다. 직전 결과로 안내하는 것을 권장한다.

### 2-5. 로딩 시간과 타임아웃

**앱 총 대기 시간 = 서버 시간(n회 합계) + `requestTrip` 시간 × n**. 추천 n회(1~3)면 `requestTrip` 도 n회다. `eta-check` 를 쓰면 서버 호출은 `/recommend` 1회 + `eta-check` n회인데, `accept`·`fallback` 은 수 ms, `retry` 는 `/recommend` 1회와 같은 시간이라 아래 표가 그대로 맞다. 아래 시간에는 앱 ↔ 서버 네트워크 왕복이 들어 있지 않으니, 각자 환경의 서버 주소까지 왕복 시간을 더해서 본다.

서버 시간(2026-11-13 실측, 7곳 × 30·60분, 프로세스 내, 네트워크 제외):

| 상세 캐시 | 1회 p50 / p95 / 최대 | 연속 2회 p50 / p95 / 최대 | 연속 3회 p50 / p95 / 최대 |
|---|---|---|---|
| 없음 | 0.34 / 1.6 / 3.8초 | 0.63 / 1.9 / 4.0초 | 0.90 / 2.3 / 4.2초 |
| 있음 | 0.007 / 0.013 / 0.04초 | 0.012 / 0.14 / 0.18초 | 0.016 / 0.17 / 0.38초 |

`requestTrip` 은 실측 최대 약 1초(에뮬레이터 실측).

| 추천 횟수 | 상세 캐시 없음 p95 / 최대 | 상세 캐시 있음 p95 / 최대 |
|---|---|---|
| 1회 | 1.6 + 1 = 2.6초 / 3.8 + 1 = 4.8초 | 0.01 + 1 ≈ 1.0초 / 0.04 + 1 ≈ 1.0초 |
| 2회 | 1.9 + 2 = 3.9초 / 4.0 + 2 = 6.0초 | 0.14 + 2 ≈ 2.1초 / 0.18 + 2 ≈ 2.2초 |
| 3회 | 2.3 + 3 = 5.3초 / 4.2 + 3 = **7.2초** | 0.17 + 3 ≈ 3.2초 / 0.38 + 3 ≈ 3.4초 |

권장
- 로딩 화면 전체 제한 시간 **12초** (실측 최악 7.2초 + 여유). 넘으면 그때까지 받은 마지막 추천으로 안내하고, 하나도 없으면 "잠시 후 다시 시도해 주세요".
- `/recommend` 호출 1회 타임아웃 10초. 단 전체 12초 안에서 남은 시간을 넘지 않게 한다.
- 로딩 연출은 캐시가 있어 금방 끝나도 **최소 1.5초** 보여 준다(뽑기 느낌 유지, 깜빡임 방지).
- 조건 카드를 켜면 요청 1회에 상세조회가 최대 10곳(5곳씩 2번 병렬)으로 늘어난다. 2026-11-13 프로토타입 측정(7곳, 30분, 두 조건 모두)에서 캐시 없음 p50 0.20초 / 최대 0.49초, 캐시 있음 수 ms였다. 위 제한 시간 12초는 그대로 둔다.
- 로딩 화면에는 "가까운 곳을 다시 찾는 중…"처럼 재추천 단계(1/3, 2/3…)를 보여 주면 대기가 덜 길게 느껴진다.

## 3. Kotlin (Gson + Retrofit)

```kotlin
// build.gradle: com.squareup.retrofit2:retrofit, com.squareup.retrofit2:converter-gson
import com.google.gson.annotations.SerializedName
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Query

// ---- /keywords ----
data class KeywordsResponse(
    @SerializedName("request_id") val requestId: String,
    @SerializedName("arrival_time") val arrivalTime: String,     // "HH:mm"
    @SerializedName("sunset_time") val sunsetTime: String?,      // "HH:mm"
    @SerializedName("keywords") val keywords: List<KeywordStatus>,
)

data class KeywordStatus(
    @SerializedName("id") val id: String,
    @SerializedName("label") val label: String,
    @SerializedName("enabled") val enabled: Boolean,
    @SerializedName("reason") val reason: String?,
)

// ---- /recommend ----
data class RecommendRequest(
    @SerializedName("latitude") val latitude: Double,
    @SerializedName("longitude") val longitude: Double,
    @SerializedName("drive_minutes") val driveMinutes: Int,
    @SerializedName("keyword_ids") val keywordIds: List<String> = emptyList(),
    @SerializedName("exclude_content_ids") val excludeContentIds: List<String> = emptyList(),
    @SerializedName("retry_context") val retryContext: RetryContext? = null,   // 자동 재추천 때만
    @SerializedName("require_parking") val requireParking: Boolean = false,    // [주차 가능한 곳만]
    @SerializedName("exclude_paid") val excludePaid: Boolean = false,          // [유료 체험 제외]
)

data class RetryContext(
    @SerializedName("previous_content_ids") val previousContentIds: List<String>,  // 마지막 = 직전 후보
    @SerializedName("observed_eta_seconds") val observedEtaSeconds: Int?,          // requestTrip ETA(초)
)

data class RecommendResponse(
    @SerializedName("request_id") val requestId: String,
    @SerializedName("blind_name") val blindName: String,      // 호환용 — 앱은 BlindNames.pick() 사용
    @SerializedName("destination") val destination: Destination,
    @SerializedName("hint") val hint: Hint,
    @SerializedName("reveal") val reveal: Reveal,
    @SerializedName("alias_source") val aliasSource: String,  // 항상 "pool"
    @SerializedName("straight_km") val straightKm: Double,
    @SerializedName("estimated_drive_seconds") val estimatedDriveSeconds: Int,  // 서버 추정(직선 기반)
)

data class Destination(
    @SerializedName("lat") val lat: Double,
    @SerializedName("lng") val lng: Double,
)

data class Hint(
    @SerializedName("parking") val parking: String?,     // "가능" | "불가" | null
    @SerializedName("fee") val fee: String?,             // "무료" | "유료" | null
    @SerializedName("open_now") val openNow: Boolean?,   // 도착 예상 시각 기준
)

data class Reveal(
    @SerializedName("content_id") val contentId: String,
    @SerializedName("real_name") val realName: String,
    @SerializedName("address") val address: String,
    @SerializedName("overview_short") val overviewShort: String?,
    @SerializedName("image_url") val imageUrl: String?,
    @SerializedName("theme") val theme: String?,
    @SerializedName("matched_keywords") val matchedKeywords: List<String>,
)

// ---- /eta-check ----
data class EtaCheckRequest(
    @SerializedName("latitude") val latitude: Double,
    @SerializedName("longitude") val longitude: Double,
    @SerializedName("drive_minutes") val driveMinutes: Int,
    @SerializedName("keyword_ids") val keywordIds: List<String> = emptyList(),
    @SerializedName("exclude_content_ids") val excludeContentIds: List<String> = emptyList(),
    @SerializedName("require_parking") val requireParking: Boolean = false,
    @SerializedName("exclude_paid") val excludePaid: Boolean = false,
    @SerializedName("content_id") val contentId: String,                    // 방금 ETA 를 잰 후보
    @SerializedName("observed_eta_seconds") val observedEtaSeconds: Int,    // requestTrip ETA(초)
    @SerializedName("previous_content_ids") val previousContentIds: List<String>,  // 마지막 = contentId
)

data class EtaCheckResponse(
    @SerializedName("request_id") val requestId: String,
    @SerializedName("decision") val decision: String,          // "accept" | "retry" | "fallback"
    @SerializedName("attempt") val attempt: Int,               // 1~3
    @SerializedName("max_attempts") val maxAttempts: Int,
    @SerializedName("reason") val reason: String,              // within_limit | over_limit | max_attempts | no_candidates | condition_not_met | keyword_unavailable | upstream_error | internal_error (표시용 아님)
    @SerializedName("route_content_id") val routeContentId: String?,   // accept·fallback
    @SerializedName("recommendation") val recommendation: RecommendResponse?,  // retry
)

// RecommendRequest 에서 같은 검색 조건으로 만들기
fun RecommendRequest.toEtaCheck(contentId: String, etaSec: Int, previous: List<String>) = EtaCheckRequest(
    latitude, longitude, driveMinutes, keywordIds, excludeContentIds, requireParking, excludePaid,
    contentId, etaSec, previous,
)

// ---- 에러 ----
data class ErrorResponse(@SerializedName("error") val error: ErrorBody)

data class ErrorBody(
    @SerializedName("code") val code: String,
    @SerializedName("message") val message: String,
    @SerializedName("request_id") val requestId: String,
)

interface EodigajiApi {
    @GET("api/v1/keywords")
    suspend fun keywords(
        @Query("lat") lat: Double,
        @Query("lng") lng: Double,
        @Query("drive_minutes") driveMinutes: Int,
        @Query("require_parking") requireParking: Boolean = false,
        @Query("exclude_paid") excludePaid: Boolean = false,
    ): Response<KeywordsResponse>

    @POST("api/v1/recommend")
    suspend fun recommend(@Body body: RecommendRequest): Response<RecommendResponse>

    @POST("api/v1/eta-check")
    suspend fun etaCheck(@Body body: EtaCheckRequest): Response<EtaCheckResponse>
}

// 에러 본문 파싱
fun <T> Response<T>.apiError(gson: com.google.gson.Gson): ErrorBody? =
    errorBody()?.charStream()?.use { runCatching { gson.fromJson(it, ErrorResponse::class.java).error }.getOrNull() }
```

### 3-1. API 키 넣기 (`local.properties` → `BuildConfig` → OkHttp 인터셉터)

키는 코드·`build.gradle` 에 직접 쓰지 않고 `local.properties` 에 둔다. **`local.properties` 는 git 에 올리지 않는다**(Android 기본 `.gitignore` 에 들어 있는지 확인). 키는 단톡방이 아니라 개별로 전달받는다.

```properties
# local.properties (git 제외)
app_api_key=<개별로 전달받은 키>
api_base_url=<각자 환경의 서버 주소, 끝에 />
```

```kotlin
// app/build.gradle.kts
import java.util.Properties

val localProps = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    buildFeatures { buildConfig = true }
    defaultConfig {
        buildConfigField("String", "APP_API_KEY", "\"${localProps.getProperty("app_api_key", "")}\"")
        buildConfigField("String", "API_BASE_URL", "\"${localProps.getProperty("api_base_url", "")}\"")
    }
}
```

```kotlin
// 모든 요청에 X-API-Key 를 붙이는 인터셉터 (라우트마다 넣지 않는다)
class ApiKeyInterceptor(private val key: String) : okhttp3.Interceptor {
    override fun intercept(chain: okhttp3.Interceptor.Chain): okhttp3.Response =
        chain.proceed(chain.request().newBuilder().header("X-API-Key", key).build())
}
```

- 키를 로그·크래시 리포트에 찍지 않는다. `HttpLoggingInterceptor` 를 쓰면 `redactHeader("X-API-Key")` 를 건다.
- `BuildConfig.APP_API_KEY` 가 빈 값이면 빌드 설정 누락이다(서버가 검사를 켜 두었다면 모든 요청이 401).

```kotlin
// Retrofit 생성 예
val api = retrofit2.Retrofit.Builder()
    .baseUrl(BuildConfig.API_BASE_URL)            // 서버 주소. 끝에 / 필수. 운영은 HTTPS
    .addConverterFactory(retrofit2.converter.gson.GsonConverterFactory.create())
    .client(okhttp3.OkHttpClient.Builder()
        .addInterceptor(ApiKeyInterceptor(BuildConfig.APP_API_KEY))
        .readTimeout(10, java.util.concurrent.TimeUnit.SECONDS)
        .build())
    .build()
    .create(EodigajiApi::class.java)
```

## 4. 에러

모든 에러는 같은 형식이다. `message` 는 개발자용(한국어)이고, 사용자에게는 아래 "앱 문구 제안"을 보여 주는 것을 권장한다.

```json
{"error": {"code": "NO_CANDIDATES", "message": "조건에 맞는 장소가 없어요", "request_id": "9d9c4739be654afd9a1c4a112ec5dca5"}}
```

| code | HTTP | 언제 | 서버 message (실제) | 앱 문구 제안 / 처리 |
|---|---|---|---|---|
| `INVALID_REQUEST` | 400 | 형식 오류, `drive_minutes` 범위 밖, `available_minutes` 사용, `retry_context` 값 오류(`observed_eta_seconds` ≤ 0 등), `eta-check` 의 `content_id` 가 `previous_content_ids` 마지막이 아님 | `drive_minutes 는 15~120 사이여야 합니다` / `available_minutes는 drive_minutes(편도 운전 시간, 분)로 변경됐어요` | "요청을 처리하지 못했어요. 앱을 최신 버전으로 업데이트해 주세요." (앱 버그이므로 로그 수집) |
| `UNAUTHORIZED` | 401 | `X-API-Key` 헤더가 없거나 등록된 키가 아님 (모든 `/api/v1/` 엔드포인트) | `API 키가 없거나 올바르지 않아요` | "서버 인증에 실패했어요. 앱을 최신 버전으로 업데이트해 주세요." (앱 버그로 로그 수집. 키 값은 로그에 넣지 않는다) |
| `INVALID_LOCATION` | 400 | 좌표 형식 오류, 대한민국 범위 밖 | `서비스 지역(대한민국) 밖의 좌표입니다` | "현재 위치를 확인할 수 없어요. 잠시 후 다시 시도해 주세요." (FusedLocation 재조회) |
| `INVALID_KEYWORD` | 400 | 정의 안 된 키워드, 4개 이상, 중복 | `keyword_ids 는 정의된 키워드 중 중복 없이 0~3개여야 합니다` | "키워드를 다시 골라 주세요." |
| `KEYWORD_UNAVAILABLE` | 400 | 고른 키워드가 **모두** 도착 시각에 쓸 수 없음(일출·노을·야경) | `고른 키워드를 도착 예상 시각에는 쓸 수 없어요` | "지금 출발하면 볼 수 없는 키워드예요. 다른 키워드를 골라 주세요." → `/keywords` 다시 호출해 키워드 타일 갱신 |
| `NO_CANDIDATES` | 404 | 조건에 맞는 장소 없음, 또는 모두 도착일 휴무, 또는 `exclude_content_ids` 로 다 빠짐 | `조건에 맞는 장소가 없어요` / `도착 예상 시각에 문을 여는 장소가 없어요` | "조건에 맞는 곳이 없어요. 운전 시간을 늘리거나 키워드를 바꿔 보세요." |
| `CONDITION_NOT_MET` | 404 | (`/recommend`) `require_parking`·`exclude_paid` 를 켰는데 상세조회 상한(10곳) 안에서 통과한 곳이 없음 | `주차할 수 있는 곳을 찾지 못했어요` / `유료가 아닌 곳을 찾지 못했어요` / `주차할 수 있고 유료가 아닌 곳을 찾지 못했어요` (켠 조건에 따라) | "조건을 끄거나 운전 시간을 늘려 보세요." 조건 카드를 바로 끌 수 있게 풍경 화면으로 돌아간다. 자동 재추천 중이면 직전 결과로 안내 |
| `UPSTREAM_TOUR_API_ERROR` | 502 | 관광 정보 서버 장애 + 서버 캐시 없음 | `관광 정보 서버에서 목록을 가져오지 못했습니다` | "장소 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요." |
| `INTERNAL_ERROR` | 500 | 서버 내부 오류 | `서버 내부 오류` | "잠시 후 다시 시도해 주세요." |
| (네트워크 실패/타임아웃) | - | 서버 꺼짐, 주소 오류 등 | - | "서버에 연결할 수 없어요. 네트워크를 확인해 주세요." |

참고: 일부 키워드만 시간상 못 쓰는 경우(예: `["walk","sunset"]` 인데 노을 시간이 아님)는 에러가 아니다. 서버가 쓸 수 없는 키워드만 빼고 추천한다.

## 5. 키워드 상수

```kotlin
object Keyword {
    const val SEA = "sea"        // 바다
    const val WATER = "water"    // 호수·강
    const val FOREST = "forest"  // 숲
    const val WALK = "walk"      // 산책
    const val VIEW = "view"      // 전망
    const val QUIET = "quiet"    // 고즈넉
    const val SUNRISE = "sunrise" // 일출 (도착 시각이 일출 30분 전 ~ 일출)
    const val SUNSET = "sunset"  // 노을 (도착 시각이 일몰 60분 전 ~ 20분 후)
    const val NIGHT = "night"    // 야경 (도착 시각이 일몰 30분 후 ~ 23:00)

    /** 서버 응답 순서와 같다. 표시명은 가능하면 /keywords 응답의 label 을 쓴다. */
    val LABELS: Map<String, String> = linkedMapOf(
        SEA to "바다", WATER to "호수·강", FOREST to "숲", WALK to "산책",
        VIEW to "전망", QUIET to "고즈넉", SUNRISE to "일출", SUNSET to "노을", NIGHT to "야경",
    )
    const val MAX_SELECT = 3
}
```

## 6. 주의사항

1. **NaviHelper `RouteInfo` 는 위도(`latitude`)가 먼저다. 반드시 이름 붙은 인자로 넘긴다.**
   ```kotlin
   val blindName = BlindNames.pick(recentNames)   // 앱이 들고 있는 100개 목록에서, 최근 쓴 이름은 피해서
   val routeInfo = RouteInfo(
       latitude = res.destination.lat,
       longitude = res.destination.lng,
       displayLatitude = null,
       displayLongitude = null,
       poiName = blindName,         // 실제 장소명 금지
       poiId = "",                  // 필수 여부는 문서에서 확인하지 못함 — 에뮬레이터로 확인한 값으로 교체
       address = "",                // 실주소를 넣으면 블라인드가 깨진다
       poiSubId = "",               // 위와 같음
   )
   ```
   `requestTrip` 에도 `RouteInfo` 를 넘긴다면 같은 규칙으로 `poiName` 은 가명, `address` 는 빈 값으로 넘긴다.
   위치 인자(순서)로 넘기지 않는다. Pleos 문서의 Java 예시는 첫 인자에 경도 값(127.x)을 넣고 있어 문서끼리 순서가 다르게 보이므로, 이름 붙은 인자로 넘기면 순서와 무관하게 안전하다.
   좌표계: 서버 좌표는 WGS84 십진 도로 보인다(경복궁 좌표 대조). NaviHelper 문서에는 좌표계가 명시돼 있지 않으니 에뮬레이터에서 한 번 경로를 띄워 확인할 것.
2. **이미지(`reveal.image_url`)는 출처 "한국관광공사"를 표시하고, 자르거나 덮지 않는다.** 일부 이미지는 공공누리 제3유형(변경 금지)이다. 썸네일이 필요하면 비율을 유지한 채 축소(`fitCenter`)만 한다. `centerCrop`, 텍스트·그라데이션 오버레이는 쓰지 않는다.
3. **`reveal` 은 도착·주차 전에는 화면에 표시하지 않는다.** 메모리에만 들고 있다가 도착 + 주차 조건이 모두 충족되면 보여 준다. NaviHelper 에도 넘기지 않는다(`address` 는 빈 값). 로그·크래시 리포트에도 찍지 않는다.
4. [다시 뽑기] 는 직전까지 나온 `reveal.content_id` 를 `exclude_content_ids` 에 누적해서 보낸다(세션 단위). 실제 ETA 확인·자동 재추천은 `eta-check` 를 쓴다([2-3](#2-3-eta-판단-post-apiv1eta-check)). `retry_context` 는 호환용.
5. 섬 출발: 제주·울릉도·옹진 섬처럼 다리로 이어지지 않은 곳에서 출발하면 같은 섬 안의 장소만 나오고, 육지에서 출발하면 그런 섬은 나오지 않는다(서버 처리).
6. 서버 기동 직후 몇 초간은 관광지 목록을 받는 중일 수 있다. `/healthz` 의 `index_spots` 가 0 이면 잠시 후 재시도.

## 7. 가명

- 기준 목록은 서버의 `config/alias_pool.yaml`(검수한 100개)이다. 서버는 시작할 때와 테스트에서 이 목록을 금지어(장소 유형어·지명·읍면동, 5~20자) 검증으로 검사한다.
- 앱은 같은 100개 목록을 앱 안에 두고 직접 고른다(예: `BlindNames.pick(recent)`). 최근에 쓴 이름(예: 10개)은 피해서 같은 이름이 연달아 나오지 않게 한다. 목록을 바꾸면 서버 `alias_pool.yaml` 과 함께 바꾼다.
- 가명은 블라인드 티켓·도착 알림과 NaviHelper `poiName` 에 쓰고, 도감에는 그날 고른 가명을 저장한다.
