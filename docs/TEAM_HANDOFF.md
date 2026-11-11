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
   - 힌트 카드: `hint.parking`, `hint.fee`, `hint.open_now` 중 `null`인 항목은 숨깁니다.
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
- 저장소 관리자에게 알려 줄 것: ETA를 받는 함수·필드 이름과 단위, 도착 이벤트 이름, poiId·poiSubId에 넣은 값, 기어 P 속성 이름. 이걸 받으면 서버에 "실제 소요 시간 확인 후 목적지 재선정"을 붙입니다.

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

- 시연 주소는 ngrok 주소(팀에서 공유)입니다. Retrofit `baseUrl` 끝에 `/`를 붙입니다.
- 타임아웃: `/keywords` 5초, `/recommend` 15초.
- 에러는 `{"error": {"code", "message", "request_id"}}` 형식입니다. 코드별 문구는 `API_FOR_APP.md` 4장을 보세요.
