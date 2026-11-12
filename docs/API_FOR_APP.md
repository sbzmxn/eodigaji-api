# 어디가지? 추천 서버 API — 앱 팀용

> 기준: 서버 `app/schemas.py`(Pydantic) v0.1.0, 2026-11-13 (재추천 `retry_context`, `straight_km`, `estimated_drive_seconds` 추가). 아래 JSON 예시는 실제 서버 응답을 그대로 옮긴 것이다(request_id 등 값만 다름).
> 시연 서버 주소는 ngrok 주소로 바뀐다. 아래 `BASE` 를 바꿔 쓰면 된다.

## 0. 한눈에 보기

| 순서 | 호출 | 언제 |
|---|---|---|
| 1 | `GET /api/v1/keywords` | 운전 시간 슬라이더가 바뀔 때(디바운스 300ms 권장). 키워드 칩의 활성/비활성과 안내 문구 |
| 2 | `POST /api/v1/recommend` | [뽑기] / [다시 뽑기] |
| 3 | (앱) NaviHelper `requestTrip` | 실제 ETA(초) 확인. `drive_minutes × 60` 을 넘으면 `retry_context` 를 담아 2번을 다시 호출(최대 3회, [2-3](#2-3-자동-재추천-retry_context)) |
| 4 | (앱) NaviHelper `requestRoute` | 응답의 `destination` 좌표 + 앱이 `BlindNames.pick()` 으로 고른 가명 |
| 5 | (앱) 도착·주차 후 | `reveal` 표시, 도감 저장 |

- 시간 입력은 **목적지까지 편도 운전 시간** `drive_minutes` (15~120분). 예전 `available_minutes` 는 폐기됐다(보내면 400).
- **가명은 앱 `BlindNames`([`BlindNames.kt`](BlindNames.kt))에서 고른다. 서버 응답의 `blind_name` 은 쓰지 않아도 된다.** (호환을 위해 당분간 같은 100개 목록에서 고른 값을 내려준다.)
- 서버는 좌표를 저장·로그하지 않는다. 사용자 식별자는 보내지 않는다.
- 타임아웃 권장: `/keywords` 5초, `/recommend` 15초(호출 1회). 재추천을 포함한 로딩 화면 전체는 25초 ([2-4](#2-4-로딩-시간과-타임아웃)).

## 1. 엔드포인트와 curl

```bash
BASE=http://localhost:8000          # 시연: https://xxxx.ngrok-free.app

# 키워드 상태
curl "$BASE/api/v1/keywords?lat=37.4979&lng=127.0276&drive_minutes=30"

# 추천
curl -X POST "$BASE/api/v1/recommend" -H 'Content-Type: application/json' \
  -d '{"latitude":37.4979,"longitude":127.0276,"drive_minutes":30,"keyword_ids":["forest","walk"],"exclude_content_ids":[]}'

# 서버 상태 (관광지 인덱스 적재 여부)
curl "$BASE/healthz"
```

## 2. 요청·응답

### 2-1. `GET /api/v1/keywords`

| 쿼리 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `lat` | Double | ✔ | 현재 위도 (WGS84 십진 도) |
| `lng` | Double | ✔ | 현재 경도 |
| `drive_minutes` | Int | ✔ | 편도 운전 시간 15~120 |

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
    {"id": "sunset", "label": "노을", "enabled": false, "reason": "일몰은 17:25이에요"},
    {"id": "night", "label": "야경", "enabled": true, "reason": null}
  ]
}
```

- `keywords` 는 항상 8개, 항상 이 순서.
- `enabled=false` 면 칩을 비활성으로 그리고 `reason` 을 그대로 보여 준다. `reason` 은 `enabled=true` 일 때 `null`.
- 비활성 사유 문구는 서버가 정한다:
  - `지금 시간으론 멀어요` — 이 운전 시간 안에 해당 장소가 3곳 미만
  - `일몰은 HH:MM이에요` — 노을: 도착 예상이 일몰 60분 전 ~ 20분 후가 아님
  - `HH:MM 이후 도착부터 볼 수 있어요` / `야경 안내는 23:00까지예요` — 야경: 일몰 30분 후 ~ 23:00
- `arrival_time` = 현재 + drive_minutes, `sunset_time` = 도착일 일몰(로컬 계산). 화면 표시용으로 써도 된다.

### 2-2. `POST /api/v1/recommend`

요청

```json
{
  "latitude": 37.4979,
  "longitude": 127.0276,
  "drive_minutes": 30,
  "keyword_ids": ["forest", "walk"],
  "exclude_content_ids": []
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `latitude`, `longitude` | Double | ✔ | 현재 위치 |
| `drive_minutes` | Int | ✔ | 15~120 |
| `keyword_ids` | List<String> | | 0~3개, 중복 없이. 0개 = "키워드 없이 뽑기". `/keywords` 에서 `enabled=true` 인 것만 보내기 |
| `exclude_content_ids` | List<String> | | [다시 뽑기] 때 이 세션에서 이미 나온 `reveal.content_id` 들 (최대 200) |
| `retry_context` | Object | | 자동 재추천 때만. 아래 [2-3](#2-3-자동-재추천-retry_context) |

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

힌트 문구 규칙(TEAM_HANDOFF.md '앱 화면(UI)'과 같음): `parking` 이 `"가능"` 이면 "주차 가능", 아니면(`"불가"`·`null`) "주차 불가 또는 알 수 없음". `fee` 가 `"유료"` 면 "유료 체험", 아니면(`"무료"`·`null`) "무료 체험 또는 알 수 없음". `open_now` 가 `null` 이면 숨긴다.

### 2-3. 자동 재추천 (`retry_context`)

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

앱 흐름 (최대 3회)

```kotlin
val limitSec = driveMinutes * 60
val previous = mutableListOf<String>()
var observed: Int? = null
var res: RecommendResponse? = null
for (attempt in 1..3) {
    val retry = if (previous.isEmpty()) null else RetryContext(previous.toList(), observed)
    val r = api.recommend(baseRequest.copy(retryContext = retry)).body() ?: break   // 에러(404 등) → 직전 결과 사용
    res = r
    previous += r.reveal.contentId
    val eta = requestTripEtaSeconds(r.destination)          // NaviHelper requestTrip, 초 단위
    observed = eta
    if (eta <= limitSec) break                              // 설정 시간 이내 → 확정
}
// 3회 모두 초과면 마지막(가장 가까워진) 결과로 안내한다. NO_CANDIDATES 면 직전 결과를 쓴다.
```

- 자동 재추천의 `previous_content_ids` 와 사용자의 [다시 뽑기] `exclude_content_ids` 는 따로 보내도 되고(둘 다 제외됨), 같은 목록을 써도 된다.
- 재추천 중 `NO_CANDIDATES`(404)가 오면 더 가까운 곳이 없다는 뜻이다. 직전 결과로 안내하는 것을 권장한다.

### 2-4. 로딩 시간과 타임아웃

**앱 총 대기 시간 = Σ(서버 시간 + 네트워크 왕복) + `requestTrip` 시간 × 횟수**. 추천 n회(1~3)면 `requestTrip` 도 n회다.

서버 시간(2026-11-13 실측, 7곳 × 30·60분, 프로세스 내, 네트워크 제외):

| 상세 캐시 | 1회 p50 / p95 / 최대 | 연속 2회 p50 / p95 / 최대 | 연속 3회 p50 / p95 / 최대 |
|---|---|---|---|
| 없음 | 0.34 / 1.6 / 3.8초 | 0.63 / 1.9 / 4.0초 | 0.90 / 2.3 / 4.2초 |
| 있음 | 0.007 / 0.013 / 0.04초 | 0.012 / 0.14 / 0.18초 | 0.016 / 0.17 / 0.38초 |

계산 예: 3회 모두 돌고 서버 p95 2.3초, ngrok 왕복 0.5초 × 3, `requestTrip` 2초 × 3 → 2.3 + 1.5 + 6 = **약 10초**. `requestTrip` 시간은 에뮬레이터에서 재서 넣는다.

권장
- `/recommend` 호출 1회 타임아웃 15초 (관광 API 가 느릴 때 상세 조회 재시도까지 포함).
- 로딩 화면 전체 타임아웃 **25초**. 넘으면 그때까지 받은 마지막 추천으로 안내하고, 하나도 없으면 "잠시 후 다시 시도해 주세요".
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
    ): Response<KeywordsResponse>

    @POST("api/v1/recommend")
    suspend fun recommend(@Body body: RecommendRequest): Response<RecommendResponse>
}

// 에러 본문 파싱
fun <T> Response<T>.apiError(gson: com.google.gson.Gson): ErrorBody? =
    errorBody()?.charStream()?.use { runCatching { gson.fromJson(it, ErrorResponse::class.java).error }.getOrNull() }
```

```kotlin
// Retrofit 생성 예
val api = retrofit2.Retrofit.Builder()
    .baseUrl("https://xxxx.ngrok-free.app/")      // 끝에 / 필수
    .addConverterFactory(retrofit2.converter.gson.GsonConverterFactory.create())
    .client(okhttp3.OkHttpClient.Builder()
        .readTimeout(15, java.util.concurrent.TimeUnit.SECONDS)
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
| `INVALID_REQUEST` | 400 | 형식 오류, `drive_minutes` 범위 밖, `available_minutes` 사용, `retry_context` 값 오류(`observed_eta_seconds` ≤ 0 등) | `drive_minutes 는 15~120 사이여야 합니다` / `available_minutes는 drive_minutes(편도 운전 시간, 분)로 변경됐어요` | "요청을 처리하지 못했어요. 앱을 최신 버전으로 업데이트해 주세요." (앱 버그이므로 로그 수집) |
| `INVALID_LOCATION` | 400 | 좌표 형식 오류, 대한민국 범위 밖 | `서비스 지역(대한민국) 밖의 좌표입니다` | "현재 위치를 확인할 수 없어요. 잠시 후 다시 시도해 주세요." (FusedLocation 재조회) |
| `INVALID_KEYWORD` | 400 | 정의 안 된 키워드, 4개 이상, 중복 | `keyword_ids 는 정의된 키워드 중 중복 없이 0~3개여야 합니다` | "키워드를 다시 골라 주세요." |
| `KEYWORD_UNAVAILABLE` | 400 | 고른 키워드가 **모두** 도착 시각에 쓸 수 없음(노을·야경) | `고른 키워드를 도착 예상 시각에는 쓸 수 없어요` | "지금 출발하면 볼 수 없는 키워드예요. 다른 키워드를 골라 주세요." → `/keywords` 다시 호출해 칩 갱신 |
| `NO_CANDIDATES` | 404 | 조건에 맞는 장소 없음, 또는 모두 도착일 휴무, 또는 `exclude_content_ids` 로 다 빠짐 | `조건에 맞는 장소가 없어요` / `도착 예상 시각에 문을 여는 장소가 없어요` | "조건에 맞는 곳이 없어요. 운전 시간을 늘리거나 키워드를 바꿔 보세요." |
| `UPSTREAM_TOUR_API_ERROR` | 502 | 관광 정보 서버 장애 + 서버 캐시 없음 | `관광 정보 서버에서 목록을 가져오지 못했습니다` | "장소 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요." |
| `INTERNAL_ERROR` | 500 | 서버 내부 오류 | `서버 내부 오류` | "잠시 후 다시 시도해 주세요." |
| (네트워크 실패/타임아웃) | - | ngrok 끊김 등 | - | "서버에 연결할 수 없어요. 네트워크를 확인해 주세요." |

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
    const val SUNSET = "sunset"  // 노을 (도착 시각이 일몰 60분 전 ~ 20분 후)
    const val NIGHT = "night"    // 야경 (도착 시각이 일몰 30분 후 ~ 23:00)

    /** 서버 응답 순서와 같다. 표시명은 가능하면 /keywords 응답의 label 을 쓴다. */
    val LABELS: Map<String, String> = linkedMapOf(
        SEA to "바다", WATER to "호수·강", FOREST to "숲", WALK to "산책",
        VIEW to "전망", QUIET to "고즈넉", SUNSET to "노을", NIGHT to "야경",
    )
    const val MAX_SELECT = 3
}
```

## 6. 주의사항

1. **NaviHelper `RouteInfo` 는 위도(`latitude`)가 먼저다. 반드시 이름 붙은 인자로 넘긴다.**
   ```kotlin
   val blindName = BlindNames.pick(RecentBlindNames.load(prefs))   // RecentBlindNames: TEAM_HANDOFF.md 참고
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
   위치 인자(순서)로 넘기지 않는다. Pleos 문서의 Java 예시는 첫 인자에 경도 값(127.x)을 넣고 있어 문서끼리 순서가 다르게 보이므로, 이름 붙은 인자로 넘기면 순서와 무관하게 안전하다.
   좌표계: 서버 좌표는 WGS84 십진 도로 보인다(경복궁 좌표 대조). NaviHelper 문서에는 좌표계가 명시돼 있지 않으니 에뮬레이터에서 한 번 경로를 띄워 확인할 것.
2. **이미지(`reveal.image_url`)는 출처 "한국관광공사"를 표시하고, 자르거나 덮지 않는다.** 일부 이미지는 공공누리 제3유형(변경 금지)이다. 썸네일이 필요하면 비율을 유지한 채 축소(`fitCenter`)만 한다. `centerCrop`, 텍스트·그라데이션 오버레이는 쓰지 않는다.
3. **`reveal` 은 도착·주차 전에는 화면에 표시하지 않는다.** 메모리에만 들고 있다가 도착 + 주차 조건이 모두 충족되면 보여 준다. NaviHelper 에도 넘기지 않는다(`address` 는 빈 값). 로그·크래시 리포트에도 찍지 않는다.
4. [다시 뽑기] 는 직전까지 나온 `reveal.content_id` 를 `exclude_content_ids` 에 누적해서 보낸다(세션 단위). 실제 ETA 초과에 따른 자동 재추천은 `retry_context` 를 쓴다([2-3](#2-3-자동-재추천-retry_context)).
5. 섬 출발: 제주·울릉도·옹진 섬처럼 다리로 이어지지 않은 곳에서 출발하면 같은 섬 안의 장소만 나오고, 육지에서 출발하면 그런 섬은 나오지 않는다(서버 처리).
6. 서버 기동 직후 몇 초간은 관광지 목록을 받는 중일 수 있다. `/healthz` 의 `index_spots` 가 0 이면 잠시 후 재시도.

## 7. 가명 (`BlindNames.kt`)

- 파일: [`docs/BlindNames.kt`](BlindNames.kt) — `object BlindNames { val LIST: List<String>; fun pick(recent: Collection<String> = emptyList(), random: Random = Random.Default): String }`.
  패키지명만 앱에 맞게 바꿔 복사한다.
- `pick(recent)` 은 `recent` 에 없는 이름 중 하나를 무작위로 고른다(모두 최근이면 전체에서). 최근 10개 저장은 `TEAM_HANDOFF.md` 의 `RecentBlindNames`.
- 목록 100개는 팀이 검수했고 서버 `config/alias_pool.yaml` 과 같다. 서버 테스트가 두 목록의 일치와 금지어(장소 유형어·지명·읍면동, 5~20자) 검증을 매번 검사한다. 목록을 바꿀 때는 두 파일을 함께 바꾼다.
- 가명은 출발 전·주행 중 화면과 NaviHelper `poiName` 에 쓰고, 도감에는 그날 고른 가명을 저장한다.
