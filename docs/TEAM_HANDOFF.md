# 영역별 연동 안내

추천 서버를 앱에 붙일 때 영역마다 필요한 것만 모았습니다. API 전체 명세는 [`API_FOR_APP.md`](API_FOR_APP.md)를 보세요.

## 공통 규칙

- 시간 입력은 **목적지까지 편도 운전 시간** `drive_minutes`(15~120분)입니다.
- 가명(블라인드 이름)은 **앱이 [`BlindNames.kt`](BlindNames.kt)에서 직접 고릅니다.** 서버 응답의 `blind_name`은 쓰지 않아도 됩니다.
- `reveal`(실제 장소명·주소·사진)은 **도착과 주차가 모두 확인되기 전에는** 화면에도, NaviHelper에도, 로그에도 넣지 않습니다.
- 도감은 사용자가 **[도감에 저장]을 눌렀을 때만** 저장합니다.

---

## 앱 화면(UI)

화면 규격·흐름·문구는 **UI 인계서(`UI design/HANDOFF.md`)를 따릅니다.** 여기에는 서버 연동에 필요한 규칙만 둡니다.

1. [`BlindNames.kt`](BlindNames.kt)를 프로젝트에 복사합니다. 패키지명만 맞추면 됩니다.
2. 가명 고르기: `BlindNames.pick(recent)` — 최근 사용 이름(아래 '앱 저장소·서버 연결'의 `RecentBlindNames`)을 넘기면 같은 이름이 연달아 나오지 않습니다.
3. 운전 시간 프리셋이나 조건 선택 카드가 바뀌면 `/api/v1/keywords`를 다시 부릅니다(300ms 디바운스). `enabled=false`인 키워드는 선택을 해제하고 `reason`을 그대로 보여 줍니다.
4. 조건 선택 카드 값: "주차 가능한 곳만" → `require_parking`, "유료 체험 제외" → `exclude_paid`(기본 둘 다 `false`). `/keywords`·`/recommend`·`/eta-check`에 같은 값을 보냅니다.
5. 힌트 문구 규칙 (`null`도 아래 규칙으로 표시합니다)

   | 서버 값 | 표시 문구 |
   |---|---|
   | `hint.parking == "가능"` | 주차 가능 |
   | 그 외 (`"불가"`, `null`) | 주차 불가 또는 알 수 없음 |
   | `hint.fee == "유료"` | 유료 체험 |
   | 그 외 (`"무료"`, `null`) | 무료 체험 또는 알 수 없음 |

   ```kotlin
   fun parkingText(p: String?) = if (p == "가능") "주차 가능" else "주차 불가 또는 알 수 없음"
   fun feeText(f: String?) = if (f == "유료") "유료 체험" else "무료 체험 또는 알 수 없음"
   ```
   `hint.open_now`는 `null`이면 숨깁니다.
6. 로딩: 최소 **1.5초** 보여 줍니다. 자동 재추천(아래 'Pleos SDK·내비 연동') 때문에 최대 3번 추천을 받으며, 전체 제한 시간은 **12초**(넘으면 마지막으로 받은 추천으로 안내)입니다. 시도마다 단계(n/3)를 갱신합니다.
7. 언박싱 사진은 출처 "한국관광공사"를 표시하고, 자르거나(`centerCrop`) 글자를 덮지 않습니다(`fitCenter`만).

---

## Pleos SDK·내비 연동

NaviHelper에 넘기는 부분입니다. **위도가 먼저**이고, 이름 붙은 인자로 넘기면 순서 실수가 없습니다.

```kotlin
val blindName = BlindNames.pick(RecentBlindNames.load(prefs))

val routeInfo = RouteInfo(
    latitude = res.destination.lat,
    longitude = res.destination.lng,
    displayLatitude = null,  // Pleos 문서 예시와 같이 명시 (기본값 여부 미확인)
    displayLongitude = null,
    poiName = blindName,     // 실제 장소명 금지
    poiId = "",              // 에뮬레이터에서 확인한 값으로 교체
    address = "",            // 실주소 금지 (블라인드 유지)
    poiSubId = "",           // 에뮬레이터에서 확인한 값으로 교체
)
// 안내를 시작할 때 RecentBlindNames.push(prefs, blindName)
```

- 언박싱 조건: NaviHelper `onDestinationArrived` **그리고** 주차(기어 P) 상태. 기어를 못 읽으면 CarUxRestrictions 해제로 대체합니다(UI 인계서 9.5).

### 재추천 흐름 (`eta-check`)

서버의 운전시간은 직선거리로 추정한 값이라 실제와 다를 수 있습니다. 안내를 시작하기 전에 `requestTrip`으로 실제 ETA를 재서 **서버에 보내기만 하면**, 서버가 시간 안인지 판단하고 필요하면 재추천까지 해서 돌려줍니다. 앱은 시간 비교와 횟수 세기를 하지 않고 `decision`만 따릅니다. 자세한 필드는 [`API_FOR_APP.md` 2-3](API_FOR_APP.md#2-3-eta-판단-post-apiv1eta-check).

1. `/api/v1/recommend`로 첫 추천을 받습니다.
2. 그 `destination`으로 `requestTrip` → ETA(초).
3. `/api/v1/eta-check`에 검색 조건 전부 + `content_id`(방금 후보) + ETA + `previous_content_ids`(지금까지 받은 후보 전부, 마지막=방금 후보)를 보냅니다.
4. `decision`
   - `retry`: 같이 온 `recommendation`이 다음 후보입니다. 2번으로 돌아갑니다. 로딩 화면에 `attempt`/`max_attempts`를 보여 줍니다.
   - `accept`·`fallback`: `route_content_id`의 추천으로 `requestRoute` 안내를 시작합니다(앱이 들고 있는 추천 중에서 찾습니다).
5. 전체 **12초 제한은 앱이 겁니다.** 넘으면 마지막으로 받은 추천으로 안내합니다.
6. 재추천이 실패하는 경우(후보 없음, 관광 API 장애, 서버 오류 등)는 서버가 `fallback`으로 돌려주므로 앱이 따로 처리하지 않습니다. 앱이 처리할 것은 두 가지뿐입니다.
   - `INVALID_*` 에러(400): 앱 버그입니다. 로그를 남기고 지금 후보로 안내합니다.
   - 네트워크 실패(연결 실패·타임아웃): 지금 후보로 안내합니다.

```kotlin
// 앱은 ETA 비교·횟수 세기를 하지 않는다. decision 만 따른다.
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
            .body() ?: break                              // INVALID_*(앱 버그, 로그) → 지금 후보로 안내
        if (res.decision == "retry") {
            val next = res.recommendation!!
            seen[next.reveal.contentId] = next
            current = next
            showLoadingStep(res.attempt, res.maxAttempts) // "가까운 곳을 다시 찾는 중…(2/3)"
        } else {                                          // accept · fallback
            current = seen[res.routeContentId] ?: c
            break
        }
    }
}
// 네트워크 실패(IOException)는 runCatching 등으로 잡아 break 와 같이 처리 → 지금 후보로 안내
// current 로 requestRoute (12초를 넘겨도 마지막으로 받은 추천). null 이면 에러 화면.
```

- `baseRequest`에는 사용자가 켠 조건(`requireParking`, `excludePaid`)이 들어 있어야 합니다. `toEtaCheck`([`API_FOR_APP.md` 3장](API_FOR_APP.md#3-kotlin-gson--retrofit))가 같은 조건을 그대로 옮깁니다.
- 호환용: 예전 방식(앱이 직접 비교하고 `/recommend`에 `retry_context`를 담아 재요청)도 그대로 동작합니다. 새 코드에서는 쓰지 않습니다.
- `requestTrip`에도 `RouteInfo`를 넘긴다면 위와 같이 `poiName`은 가명, `address`는 빈 값으로 넘깁니다.
- 앱 총 대기 시간 = 서버 시간(합계) + `requestTrip` 시간(최대 약 1초) × 횟수. 여기에 앱 ↔ 서버 네트워크 왕복 시간이 더해집니다.
  3회 모두 돌 때: 상세 캐시 없음 p95 2.3 + 3 = 5.3초, 최악 4.2 + 3 = 7.2초 / 캐시 있음 약 3.2초. **전체 제한 12초**, 넘으면 마지막으로 받은 추천으로 안내합니다.

---

## 앱 저장소·서버 연결

바로 쓸 수 있는 코드: [`docs/VisitedSpotsDb.kt`](VisitedSpotsDb.kt)(도감 저장·목록·좋아요/별로·개별/전체 삭제), [`docs/RecentBlindNames.kt`](RecentBlindNames.kt)(최근 가명 10개). 앱 프로젝트에 복사하고 맨 윗줄 패키지명만 바꾸면 됩니다. 아래는 구조 설명입니다.

### 도감 테이블

```sql
-- 사용자가 [도감에 저장]을 눌렀을 때만 INSERT
CREATE TABLE IF NOT EXISTS visited_spots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id   TEXT    NOT NULL,   -- reveal.content_id
    blind_name   TEXT    NOT NULL,   -- 그날 앱이 고른 가명
    real_name    TEXT    NOT NULL,
    address      TEXT,
    image_url    TEXT,
    theme        TEXT,
    keyword_ids  TEXT,               -- reveal.matched_keywords 를 쉼표로 이어서 (예: water,sunset)
    feedback     INTEGER,            -- 1=좋아요, -1=별로, NULL=미응답
    visited_at   INTEGER NOT NULL    -- epoch millis
);
CREATE INDEX IF NOT EXISTS idx_visited_at ON visited_spots(visited_at DESC);
```

자동 삭제 규칙은 없습니다. 사용자가 개별 또는 전체 삭제할 수 있게 해 주세요.

### 최근 사용 가명 (같은 이름 연속 방지)

```kotlin
object RecentBlindNames {
    private const val KEY = "recent_blind_names"
    private const val MAX = 10

    fun load(prefs: android.content.SharedPreferences): List<String> =
        prefs.getString(KEY, "")!!.split('\n').filter { it.isNotBlank() }

    fun push(prefs: android.content.SharedPreferences, name: String) {
        val updated = (listOf(name) + load(prefs).filterNot { it == name }).take(MAX)
        prefs.edit().putString(KEY, updated.joinToString("\n")).apply()
    }
}
```

### 서버 연결

- 서버 주소는 각자 환경의 서버 주소를 씁니다(예: `BuildConfig.API_BASE_URL`). Retrofit `baseUrl` 끝에 `/`를 붙이고, 운영은 HTTPS 로 접속합니다.
- 타임아웃: `/keywords` 5초, `/recommend` 10초(1회). 재추천 포함 로딩 화면 전체 12초.
- 에러는 `{"error": {"code", "message", "request_id"}}` 형식입니다. 코드별 문구는 `API_FOR_APP.md` 4장을 보세요.
- **API 키(`X-API-Key`)**: `/api/v1/` 아래 모든 요청에 헤더를 붙입니다(`/healthz` 는 필요 없음). 자세한 코드는 `API_FOR_APP.md` [3-1](API_FOR_APP.md#3-1-api-키-넣기-localproperties--buildconfig--okhttp-인터셉터).
  1. 서버 담당에게 키를 개별로 받아 `local.properties` 에 `app_api_key=...` 로 넣습니다(**git 에 올리지 않음**, 단톡에 올리지 않음).
  2. `build.gradle` 에서 `BuildConfig.APP_API_KEY` 로 읽고, OkHttp 인터셉터 하나로 모든 요청에 `X-API-Key` 를 붙입니다. 로그 인터셉터를 쓰면 `redactHeader("X-API-Key")`.
  3. 401 `UNAUTHORIZED` 는 앱 버그로 보고 로그를 모으고, 사용자에게는 "서버 인증에 실패했어요. 앱을 최신 버전으로 업데이트해 주세요."를 보여 줍니다.
  - 한계: 앱에 넣은 키는 APK 에서 추출될 수 있어 '아무나 호출하지 못하게 하는 장치'이지 강한 인증이 아닙니다. 출시 시 HTTPS·요청 횟수 제한과 함께 씁니다.
