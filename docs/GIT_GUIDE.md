# 깃 처음 쓰는 사람을 위한 가이드

저장소: https://github.com/sbzmxn/eodigaji-api (비공개)

대부분은 **1번과 2번만** 하면 됩니다. 파일을 보고 복사하는 데는 설치할 게 없습니다.

---

## 1. 초대 수락하기 (처음 한 번)

1. GitHub 계정으로 로그인합니다. 계정이 없으면 https://github.com 에서 가입하고, 아이디를 저장소 관리자에게 알려 줍니다.
2. 가입한 이메일로 온 **"invited you to collaborate"** 메일에서 **View invitation → Accept invitation**을 누릅니다.
   - 메일이 안 보이면 https://github.com/sbzmxn/eodigaji-api 에 들어가면 수락 버튼이 뜹니다.
3. 수락하지 않으면 비공개 저장소라서 **404(페이지 없음)** 가 뜹니다. 그게 정상입니다.

---

## 2. 웹에서 파일 보고 복사하기 (설치 필요 없음, 가장 쉬움)

1. https://github.com/sbzmxn/eodigaji-api 에 들어갑니다.
2. `docs` 폴더를 누릅니다. 우리가 볼 파일은 여기 있습니다.
   - `TEAM_HANDOFF.md`: 영역별 할 일과 코드
   - `BlindNames.kt`: 가명 100개 + 고르는 함수
   - `API_FOR_APP.md`: API 전체 명세
3. 파일을 누르면 내용이 보입니다.
   - **전체 복사:** 파일 위쪽 오른쪽의 복사 아이콘(겹친 네모 모양, "Copy raw file")을 누르면 전체가 클립보드에 복사됩니다.
   - **파일로 받기:** 같은 줄의 다운로드 아이콘("Download raw file")을 누릅니다.
4. 코드 블록 하나만 필요하면, 코드 블록 오른쪽 위에 마우스를 올리면 나오는 복사 버튼을 누르면 됩니다.

> 파일이 바뀌었는지 보려면 같은 페이지를 새로고침하면 됩니다. 페이지 위쪽에 마지막으로 바뀐 시간이 나옵니다.

---

## 3. 내 PC로 통째로 받기 (필요할 때만)

서버를 직접 돌려 보거나 파일이 여러 개 필요할 때만 하면 됩니다.

### 방법 A: Android Studio에서 받기 (앱 개발하는 사람 추천)

1. Android Studio 첫 화면에서 **Get from VCS**(또는 메뉴 **File → New → Project from Version Control**)를 누릅니다.
2. URL에 `https://github.com/sbzmxn/eodigaji-api.git`을 넣습니다.
3. 저장할 폴더를 고르고 **Clone**을 누릅니다.
4. GitHub 로그인 창이 뜨면 로그인합니다(**Log in via GitHub** 추천).

> 이 저장소는 Python 서버 코드라서, 앱 프로젝트 안에 넣지 말고 **별도 폴더**에 받으세요. 앱에는 `BlindNames.kt` 같은 필요한 파일만 복사해 넣습니다.

### 방법 B: GitHub Desktop (명령어 없이)

1. https://desktop.github.com 에서 설치하고 GitHub 계정으로 로그인합니다.
2. **File → Clone repository → GitHub.com 탭**에서 `sbzmxn/eodigaji-api`를 고르고 **Clone**을 누릅니다.

### 방법 C: 터미널

```bash
git clone https://github.com/sbzmxn/eodigaji-api.git
```

---

## 4. 최신 내용으로 업데이트하기

한 번 받아 둔 뒤에 관리자가 파일을 고치면, 내 PC에는 자동으로 반영되지 않습니다. 작업 전에 한 번씩 업데이트하세요.

- **Android Studio:** 메뉴 **Git → Pull**(또는 위쪽의 파란 화살표 ↓) → **Pull**
- **GitHub Desktop:** 위쪽의 **Fetch origin** → **Pull origin**
- **터미널:** 받은 폴더 안에서 `git pull`

---

## 5. 꼭 지킬 것

- **이 저장소에 직접 올리지(push) 마세요.** 서버 코드라서 바꿀 게 있으면 저장소 관리자에게 말해 주세요. 꼭 직접 고쳐야 하면 관리자와 먼저 상의합니다.
- **API 키, 비밀번호, 외부 접속 주소를 깃에 올리지 마세요.** 비공개 저장소라도 나중에 공개로 바뀌면 기록까지 전부 보입니다.
- **`.env` 파일은 저장소에 없습니다.** 서버를 직접 돌려야 하면 관광 API 키를 각자 발급받거나, 저장소 관리자에게 따로 받으세요(단톡에 키를 올리지 않기).

---

## 자주 막히는 것

| 증상 | 해결 |
|---|---|
| 저장소 주소에 들어가면 404 | 초대를 아직 수락하지 않았거나, 다른 계정으로 로그인됨 |
| Clone할 때 로그인 창이 계속 뜸 | 비밀번호 대신 GitHub 로그인(브라우저 인증)으로 진행. 터미널이면 GitHub Desktop이나 Android Studio로 받는 게 편함 |
| Pull할 때 "conflict" 오류 | 받은 파일을 내가 고쳐서 생긴 충돌. 고친 내용이 필요 없으면 폴더를 지우고 다시 Clone하는 게 가장 간단함 |
| `BlindNames.kt`가 안 보임 | 아직 올라가기 전일 수 있음. 저장소 관리자에게 확인 |
