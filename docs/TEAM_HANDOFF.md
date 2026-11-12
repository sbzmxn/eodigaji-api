# 팀원별 연동 가이드

추천 서버를 앱에 붙일 때 각자 필요한 것만 모았습니다. API 전체 명세는 [`API_FOR_APP.md`](API_FOR_APP.md)를 보세요.

## 공통 규칙

- 시간 입력은 **목적지까지 편도 운전 시간** `drive_minutes`(15~120분)입니다.
- 가명(블라인드 이름)은 **앱이 [`BlindNames.kt`](BlindNames.kt)에서 직접 고릅니다.** 서버 응답의 `blind_name`은 쓰지 않아도 됩니다.
- `reveal`(실제 장소명·주소·사진)은 **도착과 주차가 모두 확인되기 전에는** 화면에도, NaviHelper에도, 로그에도 넣지 않습니다.
- 도감은 사용자가 **[도감에 저장]을 눌렀을 때만** 저장합니다.

---

## 앱 화면(UI)

1. [`BlindNames.kt`](BlindNames.kt)를 프로젝트에 복사합니다. 패키지명만 맞추면 됩니다.
2. 가명 고르기: `BlindNames.pick(recent)` — 최근 사용 이름(아래 '앱 저장소·서버 연결'의 `RecentBlindNames`)을 넘기면 같은 이름이 연달아 나오지 않습니다.
3. 화면 규칙
   - 운전 시간 → 키워드 순서로 입력받습니다. 시간이 바뀌면 `/api/v1/keywords`를 다시 불러 칩 상태를 갱신하고, 꺼진 칩은 선택을 자동 해제합니다.
   - 꺼진 칩은 회색으로 그리고 `reason` 문구를 그대로 보여 줍니다.
   - 가명은 추천 결과·주행 중 화면에만 보여 줍니다. 한 줄로 표시합니다.
   - 힌트 카드 표시 규칙 (`null`도 아래 규칙으로 표시합니다)

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
   - 로딩 화면: 자동 재추천(아래 'Pleos SDK·내비 연동') 때문에 최대 3번 추천을 받습니다. 전체 제한 시간은 **12초**로 두고(넘으면 마지막으로 받은 추천으로 안내), "가까운 곳을 다시 찾는 중…(2/3)"처럼 단계를 보여 주세요.
   - (권장) 로딩 연출은 캐시가 있어 응답이 금방 와도 **최소 1.5초** 보여 주세요. 캐시가 있으면 서버는 수십 ms 안에 답해서 화면이 깜빡이듯 지나갑니다.
   - 키워드 칩은 9개입니다: 바다, 호수·강, 숲, 산책, 전망, 고즈넉, **일출**, 노을, 야경(서버 응답 순서 그대로). 일출은 도착 예상이 일출 30분 전 ~ 일출 시각일 때만 켜지고, 꺼지면 "일출은 07:07이에요" 같은 `reason`이 옵니다.
   - 언박싱 사진은 출처 "한국관광공사"를 표시하고, 자르거나(`centerCrop`) 글자를 덮지 않습니다(`fitCenter`만).
   - 도감 카드는 가명을 크게, 실제 장소명을 작게 보여 줍니다.
   - 주행 중(`CarUxRestrictions`)에는 입력 화면을 막고 "정차 후 선택해 주세요"를 보여 줍니다.

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

- 언박싱 조건: 도착 이벤트(또는 FusedLocation 근접 알림) **그리고** 주차(기어 P) 상태.
- 저장소 관리자에게 알려 줄 것: 도착 이벤트 이름, poiId·poiSubId에 넣은 값, 기어 P 속성 이름. (`requestTrip` 1회 최대 약 1초 — 확인 완료)

### 재추천 흐름 (실제 ETA 확인)

서버의 운전시간은 직선거리로 추정한 값이라 실제와 다를 수 있습니다. 안내를 시작하기 전에 `requestTrip`으로 실제 ETA를 확인하고, 설정 시간을 넘으면 서버에 다시 요청합니다. 자세한 필드는 [`API_FOR_APP.md` 2-3](API_FOR_APP.md#2-3-자동-재추천-retry_context).

1. `/api/v1/recommend` 호출 (첫 회는 `retry_context` 없이).
2. 응답 `destination`으로 NaviHelper `requestTrip` → 실제 ETA(초).
3. ETA ≤ `drive_minutes × 60` 이면 확정 → `requestRoute`로 안내 시작.
4. 초과면 `retry_context = {previous_content_ids: 지금까지 나온 content_id 전부(마지막=직전 후보), observed_eta_seconds: 방금 ETA}`를 담아 1번으로. **추천은 최대 3회**.
5. 3회 모두 초과하거나 재요청이 `NO_CANDIDATES`면 마지막으로 받은 결과로 안내합니다.

```kotlin
val previous = mutableListOf<String>()
var observed: Int? = null
var res: RecommendResponse? = null
for (attempt in 1..3) {
    val retry = if (previous.isEmpty()) null else RetryContext(previous.toList(), observed)
    val r = api.recommend(baseRequest.copy(retryContext = retry)).body() ?: break
    res = r
    previous += r.reveal.contentId
    val eta = requestTripEtaSeconds(r.destination)   // requestTrip 결과를 초로
    observed = eta
    if (eta <= driveMinutes * 60) break
}
// res 로 requestRoute (res 가 null 이면 에러 화면)
```

- `requestTrip`에도 `RouteInfo`를 넘긴다면 위와 같이 `poiName`은 가명, `address`는 빈 값으로 넘깁니다.
- 앱 총 대기 시간 = 서버 시간(합계) + `requestTrip` 시간(최대 약 1초) × 횟수. 시연은 서버가 노트북 안(`10.0.2.2`)이라 네트워크 왕복은 0으로 봅니다.
  3회 모두 돌 때: 상세 캐시 없음 p95 2.3 + 3 = 5.3초, 최악 4.2 + 3 = 7.2초 / 캐시 있음 약 3.2초. **전체 제한 12초**, 넘으면 마지막으로 받은 추천으로 안내합니다.

---

## 앱 저장소·서버 연결

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

- 시연 때 서버는 노트북 안에서 돌고, 에뮬레이터는 `http://10.0.2.2:8000/` 으로 붙습니다(다른 환경이면 팀에서 공유한 주소). Retrofit `baseUrl` 끝에 `/`를 붙입니다.
- 타임아웃: `/keywords` 5초, `/recommend` 10초(1회). 재추천 포함 로딩 화면 전체 12초.
- 에러는 `{"error": {"code", "message", "request_id"}}` 형식입니다. 코드별 문구는 `API_FOR_APP.md` 4장을 보세요.
