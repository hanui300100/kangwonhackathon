# 모아봄 통합 — 강대 모음 (강원대 공고 모아보기)

HTML·CSS·JavaScript 화면(프론트엔드 2050 버전 기반) + Python 백엔드.
Python 서버 하나로 **일반 사이트**, **관리자 사이트**, API를 같이 제공합니다.

```
모아봄 통합/
  backend/          Python (FastAPI + SQLite)
    app/crawler.py    수집 흐름 (강원대 6개 게시판 + 시청 4곳)
    app/parser.py     강원대 게시판 파싱
    app/samcheok.py   삼척시청 공지사항 파싱
    app/cities.py     춘천(시정소식)·강릉(공지사항)·원주(새소식) 시청 파싱
    app/classifier.py 분류 키워드, 시청 공지 중 공모·행사만 고르는 규칙
    app/main.py       공개 API + 화면 제공
    app/admin.py      관리자 API
    app/db.py         DB 구조
  web/
    index.html        일반 사이트  → http://localhost:8000
    admin.html        관리자 사이트 → http://localhost:8000/admin.html
    app.js / api.js / style.css  (두 사이트가 같이 씀)
```

## 실행
```powershell
cd "C:\Users\의성\Desktop\해커톤\모아봄 통합\backend"
$env:ADMIN_TOKEN="1234"     # 관리자 사이트에서 '최종 관리자'로 로그인할 키
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```
- 일반 사이트: **http://localhost:8000** · 관리자 사이트: **http://localhost:8000/admin.html** (일반 사이트 맨 아래 '관리자 사이트' 링크)
- HTML 파일을 더블클릭해서 열면 동작하지 않습니다 (자동으로 위 주소로 이동).
- 화면 파일(web/)을 고치면 브라우저 새로고침만 하면 됩니다. 백엔드(.py)를 고치면 서버를 다시 켜야 합니다.

## 출처와 분류
| 출처 | 분류 | 캠퍼스 |
|---|---|---|
| 강원대 학사공지·장학공지·행사안내·공모안내·일반공지·채용안내 | 학사/장학금/행사/공모/(자동)/채용 | 공고에 표시된 캠퍼스 |
| 춘천시청 시정소식 | 춘천시 | 춘천 |
| 삼척시청 공지사항 | 삼척시 | 삼척 |
| 강릉시청 공지사항 | 강릉시 | 강릉 |
| 원주시청 새소식 | 원주시 | 원주 |
| 관리자가 올린 학과 행사 | 학과행사 | 학과 캠퍼스 |

- 시청 공지는 **시민·대학생 대상 공모·행사만** 저장합니다 (채용·입찰·결과 발표·회의결과 등 행정 공지 제외). 규칙: `classifier.py`의 `SAMCHEOK_INCLUDE` / `SAMCHEOK_EXCLUDE`.
- 화면에서 캠퍼스를 고르면 **그 지역 시청 탭만** 보입니다 (전체를 고르면 4개 모두).

## 일반 사이트
| 기능 | 내용 |
|---|---|
| 목록 | 캠퍼스·분류·진행 상태 필터, 검색, D-day, **정렬(최신순·조회순·오래된 순)**, 더 보기. 중요 표시 글은 어떤 정렬이든 맨 위 |
| 새로고침 | 접속할 때·새로고침 버튼: 학교·시청 사이트에서 새 공고를 뒤에서 가져옴 (10분에 한 번만 실제 수집) |
| 좌측 배너 | 지금 많이 보는 중 · 좋아요 많은 공고 · 곧 마감돼요 |
| 상세 | 원문 서식 본문, 사진, 첨부파일, 좋아요, 원문 바로가기 |
| 접속 팝업 | 관리자가 건 대표 이미지. 닫기 / 앞으로 보지 않기(브라우저에 기억) |
| 내 학과 소식 | 학과를 고르면 그 학과 행사만 |

## 관리자 사이트 (admin.html)
- 관리자 키로 로그인하면 일반 사이트와 똑같이 공고를 둘러볼 수 있고, 공고 상세 상단에 관리자 버튼이 나옵니다:
  **📌 중요 표시 · 팝업으로 띄우기 · ✏️ 수정 · 🗑 삭제**
- 관리자 패널에서 볼 수 있는 목록: 중요 표시 공고, 팝업 표시 공고, 학과 행사, 학과, 발급된 관리자 키, **삭제한 공고(복원 가능)**
- 수정: 학과 행사는 내용 전체, 학교·시청에서 수집한 공고는 **제목·분류·마감일**만 (본문은 원문 사이트 것). 다시 수집돼도 고친 값이 유지되고, '원래 값으로'로 되돌릴 수 있음
- 삭제: 학과 행사는 완전 삭제, 수집한 공고는 **숨김** (다시 수집돼도 안 보이고, '삭제한 공고'에서 복원)

| 등급 | 로그인 키 | 할 수 있는 일 |
|---|---|---|
| 최종 관리자 | 서버의 `ADMIN_TOKEN` 값 | 전부 + 일반 관리자 키 발급 |
| 일반 관리자 | 최종 관리자가 발급 | 모든 공고 중요·팝업·수정·삭제, 학과·학과 관리자 키 관리, 크롤링 |
| 학과 관리자 | 최종·일반 관리자가 발급 | 자기 학과 행사만 작성·수정·삭제·중요·팝업 |

## API (추가·변경된 것)
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/notices?sort=newest\|views\|oldest&flag=important\|popup` | 정렬, 중요·팝업 글만 |
| GET | `/api/notices?hidden=true` | 삭제(숨김)한 공고 목록 — 관리자 키 필요 |
| GET | `/api/meta` | `city_category_by_campus` (캠퍼스 → 시청 분류), `sorts` 포함 |
| PUT | `/api/admin/notices/{id}` | 수집 공고 제목·분류·마감일 수정 (null이면 원래 값) |
| DELETE | `/api/admin/notices/{id}` | 삭제 (수집 공고는 숨김, 학과 행사는 완전 삭제) |
| POST | `/api/admin/notices/{id}/restore` | 숨긴 공고 복원 |
| PUT | `/api/admin/notices/{id}/flags` | 중요·팝업 켜고 끄기 |

전체 API 문서: 서버 실행 후 http://localhost:8000/docs

## 참고
- 조회수·좋아요·중요·팝업·관리자 수정·숨김은 공고 표와 따로 저장해서 크롤러가 공고를 다시 받아도 유지됩니다.
- 좋아요는 로그인 없이 브라우저 기준이라 반복하면 부풀릴 수 있습니다 (시연용).
- 로컬 시연용입니다. 공개 배포 전에는 HTTPS, 로그인 시도 제한, CORS 제한이 필요합니다.

## 앱 (Capacitor · 안드로이드)
화면은 `web/`을 그대로 씁니다. `app/`이 그걸 감싸서 안드로이드 앱으로 만듭니다.

설치된 도구 (이 PC): Node.js `C:\nodejs`, JDK 21 `C:\jdk21`, Android Studio `C:\android-studio`,
Android SDK `C:\Android\Sdk`, 가상 휴대폰(AVD) `C:\Android\avd\Moabom` — 경로에 한글이 있으면 빌드·에뮬레이터가 실패해서 영문 경로에 둠.

```powershell
cd "C:\Users\의성\Desktop\해커톤\모아봄 통합\app"
$env:JAVA_HOME="C:\jdk21"; $env:GRADLE_USER_HOME="C:\gradle-home"

# 1) 앱이 접속할 서버 주소를 넣고 화면 복사 (주소 없이 실행하면 app/api-url.txt 사용)
node scripts/sync-web.mjs http://10.0.2.2:8000      # 에뮬레이터용 (10.0.2.2 = 이 PC)
# node scripts/sync-web.mjs http://192.168.1.163:8000  # 실제 휴대폰용 (같은 와이파이, 아래 방화벽 참고)
npx cap sync android

# 2) APK 빌드 → app\android\app\build\outputs\apk\debug\app-debug.apk
cd android; .\gradlew.bat assembleDebug

# 3) 에뮬레이터 켜고 설치
C:\Android\Sdk\emulator\emulator.exe -avd Moabom
C:\Android\Sdk\platform-tools\adb.exe install -r app\build\outputs\apk\debug\app-debug.apk
```
- 서버는 `--host 0.0.0.0`으로 켜야 에뮬레이터·휴대폰에서 접속됩니다.
- **실제 휴대폰**에서 쓰려면 Windows 방화벽에서 Python(포트 8000) 들어오는 연결을 허용해야 합니다. 시연 후 배포 서버(https)를 쓰면 이 설정과 `usesCleartextTraffic`(http 허용)은 필요 없습니다.
- 화면(web/)을 고친 뒤에는 1)~3)을 다시 하면 앱에 반영됩니다.

## 앱 전용 기능: 사용자 정보 · 나에게 맞는 공고 · 알림
- **최초 실행**: 사용자 정보 입력 (관리자/일반 → 학생/주민 → 학생: 캠퍼스·학과·학년 / 주민: 지역·성별·나이 → 관심 주제 여러 개). 단계마다 '돌아가기'. 이후 ⚙ 설정에서 언제든 수정.
  관리자를 고르면 관리자 키를 확인하고, 앱의 공고 상세에 중요·팝업·수정·삭제 버튼이 보임 (키는 서버에 저장하지 않음).
- **화면**: 양옆 배너 없음, 위쪽에 소분류 칩(캠퍼스·분류·상태, 가로 스크롤), 아래쪽에 대분류 탭(전체 공고 · 나에게 맞는 공고 · 내 학과 · 설정). 사진 있는 공고는 왼쪽 사진 + 오른쪽 제목·(작은 글씨) 내용.
- 브라우저에서 앱 화면 확인: `http://localhost:8000/?app=1`

### 적합도 (backend/app/matching.py — 외부 AI 없이 서버 자체 모델)
1. 공고를 **수집할 때 한 번만** 분석해 작은 태그로 저장 (`notice_tags`): 대상(학생/주민), 캠퍼스, 관심 주제 10개 점수, 학년·성별·나이 조건.
   주제 점수 = 키워드 규칙 + **나이브 베이즈 분류기**(글자 2-gram). 분류기는 수집된 공고로 스스로 학습 (공고 221건 기준 학습+태깅 0.4초).
2. 사용자 적합도는 태그와 프로필만 비교 (본문을 다시 읽지 않음, 진행 중 공고 전체 계산 약 1ms) → 0~100점, **60점 이상**이 '나에게 맞는 공고'.
3. 새 공고가 수집되면(또는 관리자가 학과 행사를 올리면) 모든 프로필과 비교해 60점 이상인 사람의 **알림 대기열**(`notifications`)에 넣음.
4. 앱은 켜질 때·화면으로 돌아올 때·켜져 있는 동안 5분마다 대기열을 가져와 **휴대폰 알림**을 띄움 (앱을 완전히 종료하면 다음 실행 때 받음).
   안드로이드 14+는 '알람 및 리마인더' 허용이 필요해서 처음 한 번 설정 화면으로 안내함.

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/profile-options` | 입력 화면 선택지 (캠퍼스, 지역, 학년, 성별, 주제, 기준 점수) |
| POST | `/api/profiles` | 프로필 등록·수정 → `device_id` 발급 |
| GET / DELETE | `/api/profiles/{device_id}` | 내 프로필 / 삭제 |
| GET | `/api/me/matches?device_id=` | 나에게 맞는 공고 (적합도 높은 순, `match_score`) |
| GET | `/api/me/notifications?device_id=` | 새로 맞는 공고 알림 (가져가면 다시 안 줌) |

## 모아봄 완성 (모아봄 통합 + 해커톤 2355)
모아봄 통합(앱·맞춤 공고 AI·알림·배포 설정)에 해커톤 2355의 기능을 합친 최종본입니다.

| 2355에서 가져온 기능 | 내용 |
|---|---|
| 새 화면 디자인 | 머리글 검색창, 다크 모드(브라우저에 기억), 새 소개 영역 |
| 봉사 분류 | 제목에 '봉사'가 있으면 '봉사'로 분류 (관리자가 고친 분류가 우선) |
| 업로드 기간 필터 | 최근 1일·1주·1개월·1년 (`/api/notices?period=1d\|1w\|1m\|1y`) |
| 내 소식 | 학과 소식 + ♥ 좋아요 탭. 캠퍼스 → 계열 → 학과 순서로 선택 |
| 학과 목록 | 학교 공식 학과 180개(`backend/app/departments.json`)를 서버 시작 때 한 번 넣음. 기존 학과·관리자 수정은 유지 |
| 공모 참가 대상 | 본문·첨부파일(hwp·hwpx·pdf)에서 참가 대상 학과를 찾아 `contest_eligibility`로 제공 (확실할 때만) |
| 좋아요 목록 API | `/api/notices/saved?ids=...` (앱이 쓰던 `/api/notices/by-ids`도 그대로) |

- 패키지 추가: `pypdf`, `olefile` (requirements.txt에 반영)
- 테스트 47개 통과
- 로컬 실행: `cd backend` → `.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000`
