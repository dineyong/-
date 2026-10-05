# 파이썬 블로그 자동화 앱 — 설계 & 작업 기록

> 다음에 다른 블로그 프로그램을 만들 때도 참고하려고 남기는 문서.
> 작업 폴더(클라우드): /home/claude/blogbot (git으로 단계별 기록)
> 원본(Node) 엔진: /home/claude/daewooki/naver-bc-automation — 여기서 검증된 로직을 파이썬으로 옮김

## 사용자 요구 (2026-10-05)
- 전부 파이썬으로 새로 작성 (Node/Next.js 엔진 대체)
- 터미널 없이 실행: 맥에서 더블클릭으로 켜는 앱
- 켜두면 알아서 정해진 시간에 정보글 자동 작성·예약
- 쇼핑커넥트 글은 정보글 3개당 1개 비율 (링크는 사용자가 앱에 넣어둔 대기열에서)
- 작업 내용을 전부 기록 / 사용량 소진 시 자동 이어하기(예약 메시지)

## 설계
| 부분 | 방식 | 이유 |
|---|---|---|
| 실행 | `블로그자동화.app` (셸 런처가 든 맥 앱 묶음) → 처음 실행 시 가상환경 만들고 설치, 이후 바로 실행 | 터미널 없이 더블클릭. 설치 진행은 맥 알림/대화상자로 |
| 화면 | 파이썬 내장 웹서버 + 브라우저 대시보드(localhost:8765) 자동으로 열림 | tkinter는 맥 기본 파이썬에서 깨지는 경우가 많아 피함. 의존성 0 |
| 저장 | SQLite (표준 라이브러리) `data/blog.db` | 설치 불필요 |
| AI | Gemini REST(urllib) — 3.8 → 3.7 → 3.6 → 3.5 → 3.5-lite, 붐빔 재시도, 인터넷 끊김 대기 | 의존성 없이 HTTP |
| 네이버 | playwright(파이썬) — 기존 TS 로직 그대로 이식 | 같은 라이브러리라 동작 동일 |
| 그림 | illustrations.ts를 그대로 이식(SVG 문자열 + 같은 난수) → 크롬으로 PNG | TS 결과와 같은 그림인지 SVG 비교로 검증 |
| 예약 | 하루 3칸(9–11·13–16·19–22시), N일치 미리 채움 | 기존과 동일 |
| 비율 | 정보 3 : 쇼핑 1 (마지막 쇼핑글 이후 정보글이 3개 이상이고 대기열에 링크가 있으면 쇼핑글) | 사용자 요구 |

## 기존 엔진에서 배운 함정 (그대로 지킬 것)
- 네이버 에디터는 iframe 안에 있을 수 있음 → 모든 frame에서 `.se-documentTitle` 찾기, 못 찾으면 중단
- 좌표 클릭 금지 (프로필 편집 창 30개 사고)
- 제목→본문은 Tab이 아니라 본문 클릭/Enter, 이후 제목 바뀌었으면 중단
- 이모지는 grapheme 단위로 insertText 1개씩, 글자는 keyboard.type (줄 통째 insertText는 글자 증발)
- 그림 렌더링은 별도 browser context (새 창 감시에 걸려 닫힘)
- 예약 버튼 누른 뒤 창이 닫히면 성공
- 'ㅎㅎ/ㅋㅋ/ㅠㅠ', 인사 이모지 금지, 경험 지어내기 금지, 공정위 문구 맨 위(쇼핑만)
- 인터넷 끊김은 실패로 세지 않음, 실패 횟수는 켠 이후만

## 진행 상황
- [x] 1. 기본 뼈대 — blogbot/config.py(~/BlogAuto/settings.env, 예전 .env·세션 가져오기), db.py(SQLite: posts·links·settings), log.py
- [x] 2. AI 호출 — blogbot/ai.py (urllib, 404→다음 모델, 429/5xx→5·15초, 오프라인→1분×5, OfflineError는 실패로 안 셈). 모의 테스트 통과
- [x] 3. 프롬프트 — blogbot/prompts.py (PLANNER 마케팅 기획을 정보·쇼핑 둘 다), text.py (clean_for_editor, blog_lines)
  - 발견: Node 버전은 마케팅 기획 헤더가 쇼핑 프롬프트에 들어가 있었음(교체 대상 오인). 파이썬판은 둘 다 넣음
- [x] 4. 그림 — blogbot/illustrations.py. mulberry32·hashSeed·toFixed까지 JS와 동일하게 구현 → TS와 SVG 17종 **바이트 동일**. 파이썬 playwright로 10장 렌더링 확인
- [x] 5. 네이버 자동화 — blogbot/naver.py (login, Writer: product/open_editor/title/body/_upload/_reserve/publish/confirm). 이모지 묶음 나누기는 표준 라이브러리로 직접 구현(ZWJ·피부색·키캡·국기 확인)
- [x] 6. 자동 작성 — blogbot/scheduler.py + writer.py
  - 일꾼 스레드 1개가 글쓰기·로그인을 순서대로 처리 (동시에 두 편 안 씀). 10분마다 tick, 예약이 모자라면 바로 다음 편
  - 비율: 마지막 쇼핑글 이후 성공한 정보글 ≥ 3 이고 대기 링크 있으면 쇼핑. 모의 실행 결과 정보·정보·정보·쇼핑 반복 확인
  - 실패 판정: 켠 시각 이후(>=, 같은 초 포함 — '>'로 했다가 안 꺼지는 버그 발견) 인터넷 실패 제외 연속 3번 → 꺼짐. 로그인 풀림은 즉시 꺼짐
  - 쇼핑 실패 시 링크: 인터넷 문제면 WAITING으로 되돌림, 그 외 FAILED
  - 가짜 AI·가짜 에디터로 정보/쇼핑 흐름 끝까지 실행: 그림 슬롯·공정위 문구·[구매링크] 치환·메모 없을 때 '내돈내산' 태그 제거·임시파일 정리 확인
- [x] 7. 대시보드 — blogbot/server.py + web/index.html (내장 http.server, 127.0.0.1만, Host 검사, POST는 X-Blogbot 헤더 필수 → 다른 사이트에서 몰래 요청 못 함)
  - 자동 스위치·하루 편수·며칠치, 정보3:쇼핑1 진행 막대, 지금 1편 쓰기, 네이버 로그인 창, 링크 대기열(여러 줄 붙여넣기+메모), 글 목록, 설정, 작업 기록. 4초마다 새로고침, 휴대폰 폭에서 글 목록 카드형
  - 설정 저장: 가려진 API 키(AIza…1234)는 안 덮어씀, 블로그 주소 통째로 넣어도 아이디만 뽑음, 여러 줄 공정위 문구 \n으로 저장
- [x] 8. 맥 앱 — mac/launcher.sh + mac/build_app.py → dist/블로그자동화.app (+zip)
  - 첫 실행: 파이썬(3.9+) 찾기(homebrew → /usr/local → python.org → 개발자도구 있을 때만 /usr/bin) → ~/BlogAuto/.venv + playwright==1.56.0 + certifi + chromium, osascript 안내창, 실패 시 setup.log
  - 실행: 오류로 꺼지면 10초 뒤 재시작(브라우저 재오픈 없이), '종료' 버튼(exit 0)이면 끝, 앱 종료 신호 받으면 자식도 끔. 맥에서는 caffeinate -w로 켜져 있는 동안 잠자기 방지
  - 이미 켜져 있으면(/api/ping) 화면만 다시 엶
  - zip은 ZipInfo.external_attr로 launcher 755 기록 (안 하면 맥에서 실행 안 됨)
  - 아이콘: SVG를 크롬으로 찍어 PNG 4장(ic07~ic10)을 icns에 직접 담음 (iconutil 없이 리눅스에서)
  - 함정: python.org 맥 파이썬은 인증서가 비어 https 전부 실패 → online()이 영원히 False(오프라인으로 오해) → certifi를 시스템 인증서에 추가해 모든 urlopen에 context 전달
  - 예전 Node 폴더(~/Downloads/naver-bc-automation-main) 자동 가져오기: .env 키·아이디·주제, 세션, prisma dev.db의 앞으로 예약된 글(칸 겹침 방지)·READY 링크 (한 번만)
  - 검증: 3.9로 전체 import, 가짜 홈에서 실행기 → 실행/강제종료 후 재시작/종료 버튼 확인. (샌드박스에서 PyPI 접근이 막혀 pip 설치 단계는 맥에서 처음 확인 필요)
- [x] 9. 전달 — /home/claude/post/블로그자동화-파이썬-v1.0.0.zip, 소스 git bundle /home/claude/post/blogbot-source.bundle

## 맥 첫 실행에서 겪은 일 (2026-10-05, v1.0.1)
- Gatekeeper "악성 코드가 없음을 확인할 수 없습니다" → 시스템 설정 > 개인정보 보호 및 보안 > "그래도 열기". 안 보이면 `xattr -dr com.apple.quarantine /Applications/블로그자동화.app`
- 설치(맥 기본 /usr/bin/python3 3.9.6 + playwright 1.56 + chromium-1194)는 5분 만에 성공
- **켜지자마자 꺼짐 반복**: 서명 안 된 앱(셸 실행기)에서 띄운 파이썬은 맥 개인정보 보호(TCC)로 ~/Downloads 읽기가 묻지도 않고 거부됨 → `PermissionError: [Errno 1] Operation not permitted: .../Downloads/naver-bc-automation-main/.env` → 예외로 프로그램 종료 → 실행기가 10초마다 재시작(사용자 눈엔 무반응)
  - 고침: 가져오기의 OSError는 잡고 계속 실행, Path.exists()도 안전 함수(can_see)로 (3.9는 EPERM에서 예외)
  - 가져오기 경로: `~/BlogAuto/old/`에 터미널로 .env·naver-session.json·dev.db를 복사해 두면 거기서 가져옴 (터미널은 다운로드 접근 권한이 있음)
  - 실행기: 60초 안에 3번 연속 꺼지면 안내창 + 기록 보기, 안내창은 'tell me to activate'로 맨 앞에, launcher.log 기록
- 교훈: 서명 없는 맥 앱은 Documents/Downloads/Desktop을 건드리지 말고 자기 폴더(~/BlogAuto)만 쓸 것. 실패가 조용히 반복되지 않게 꼭 사용자에게 보이게
- 예전 Node 프로그램이 닫지 않은 로봇 크롬(chromium-1208)이 10개 가까이 남아 있었음 → `pkill -f "chromium-1208"`

## v1.1.0 (2026-10-05) — 사용자 요청 반영
- 첫 실사용: 글 작성은 잘 됨. 그러나 **예약은 실제로 됐는데 상태가 '실패'**로 뜸
  - 원인: confirm()이 20초 안에 에디터가 사라지는지만 봤음 → 네이버가 예약 후 화면을 늦게 바꾸면 실패 판정. 실패가 3번 쌓이면 자동이 꺼지는 위험까지 있었음
  - 고침: 45초, 판정 3단계 — ok(창 닫힘/글쓰기 주소 벗어남/에디터 사라짐/글 주소) · fail(네이버 알림창(dialog)·오류 레이어 문구가 있을 때만, 그 문구를 사유로 기록) · unsure(오류 없이 늦음 → **완료로 처리 + 안내 메모**)
  - 실패로 남은 글은 대시보드 [완료로 표시] (직접 완료 처리)
  - 계획한 공개 시각을 글 쓰기 전에 저장 → 실패해도 언제로 하려 했는지 남음
- **예약 발행 / 즉시 발행** 선택 (자동 설정 + '지금 1편 쓰기'마다)
  - 즉시 모드 자동: 하루 3시간대마다, 날짜·칸 기준 crc32로 정한 시각(칸 끝나기 40분 전까지)에 써서 바로 발행, 하루 N편까지. 맥이 그 시간에 켜져 있어야 함
- 상태 표시: 성공은 모두 '완료', 옆 칸에 "예약글 · 10/6(화) 14:20" / "즉시 발행 · 날짜" + 글 주소 있으면 [보기]. 오류는 ❌ 사유, 참고는 ℹ️ 메모
- **말투 자동 학습** (blogbot/style.py): 사용자가 예시를 가져올 필요 없게
  - 글마다 AI 초안(body) 저장 → 공개 6시간 뒤 RSS(rss.blog.naver.com/{id}.xml)에서 제목 또는 공개 시각(±10분)으로 짝 맞춤 → 모바일 글(m.blog.naver.com/PostView.naver)의 se-text-paragraph 본문 읽기(실패 시 RSS 요약)
  - 빈 줄 무시 unified diff → 고친 글이 있으면 Gemini가 "주인이 고치는 습관" 최대 12줄 말투 메모로 정리(기존 메모와 병합, 공정위·링크·해시태그 차이 무시)
  - 말투 메모는 정보·쇼핑 프롬프트 맨 끝에 "다른 규칙보다 우선"으로 붙음. 대시보드에서 보기·직접 고치기·[지금 확인하기]
  - 클라우드(Claude 쪽)에서는 네이버 접속이 막혀 있어 직접 못 읽음 → 사용자 맥에서 앱이 읽는 방식으로 설계
- DB: posts에 mode·note·body·learned 칸 추가 (예전 DB는 켤 때 ALTER TABLE로 자동 추가)
- 검증: 3.9 컴파일, 옛 DB 이전, 완료로 표시, 즉시 모드 시간대 판정, 두 모드 글쓰기 흐름, 가짜 RSS/본문으로 학습, 대시보드 화면(어두운 모드) 확인. 화면 JS에서 변수 이름 겹침(st) 오류를 스크린샷으로 발견해 고침

## v1.2.0 (2026-10-05) — 자동 업데이트 (zip 다운로드 졸업)
- 창고: GitHub 공개 저장소 **dineyong/-** 의 `blog-auto/` (main). 앱은 `https://raw.githubusercontent.com/dineyong/-/main/blog-auto/manifest.json`을 1시간마다(켜고 90초 뒤 첫 확인) 확인
- manifest.json = {version, notes, files: {경로: sha256}} → 파일마다 받아 sha256 확인 → `~/BlogAuto/app.new` → `app`→`app.prev`, `app.new`→`app` → 종료코드 3
- 실행기: `~/BlogAuto/app` 이 있으면 그 코드로 실행(PYTHONPATH), 코드 3이면 1초 뒤 바로 재시작. 새 코드가 60초 안에 3번 꺼지면 `app.bad`로 빼고 `app.prev`(없으면 앱 안 코드)로 되돌림. updater는 app.bad 와 같은 버전은 다시 안 받음
- 글 쓰는 중이면 적용 안 하고 다음 확인 때. 적용 순간엔 작업 잠금(busy)을 쥔 채 종료 → 그 사이 새 글 시작 안 됨
- 새 코드는 앱 묶음 밖(~/BlogAuto)에 받으므로 맥 Gatekeeper 경고 없음. 대시보드 상단 버전 글자를 누르면 즉시 확인, 버전이 바뀌면 화면 자동 새로고침
- 한계: 실행기(launcher.sh)·파이썬 패키지(pip) 변경은 자동 업데이트로 못 함 → 그때만 zip
- **배포 방법 (다음 작업자용)**: ① blogbot/__init__.py 버전 올리기 ② `python3 -B mac/release.py /home/claude/- "바뀐 점"` (저장소 클론 경로) ③ 저장소 commit & push. 버전이 같거나 낮으면 앱이 안 받음. pycache가 남아 있으면 버전이 옛날로 읽힐 수 있어 `-B`·pycache 삭제
- 검증: 로컬 file:// 창고로 적용·해시 위조 거부·작성 중 대기, 가짜 홈에서 실행기까지 1.2.0→1.2.1 자동 교체 후 재시작 확인. GitHub 푸시는 사용자가 Claude GitHub 앱 설치 후 성공(저장소 공개, 키·로그인은 올라가지 않음)

## 아직 실제 네이버에서 확인 못 한 것
- 즉시 발행 실제 1회, 말투 학습의 RSS·모바일 본문 읽기 (맥에서)
- 첫 실행 pip/chromium 설치 (맥 실제 환경)
- 쇼핑글 실사용, 에디터 안 사진 크기, 알리 팝업 건(Node 때부터 미해결)

## 다음에 블로그 프로그램 만들 때 재사용할 것
- 맥 앱 묶음 방식(launcher.sh + build_app.py)은 그대로 재사용 가능 — 파이썬 패키지 이름과 PW 버전만 바꾸면 됨
- 대시보드는 의존성 0 (http.server + 한 파일 HTML)
- 이모지 입력·에디터 iframe·좌표 클릭 금지 등 네이버 에디터 규칙은 naver.py 위쪽 주석에 정리

## 다음 할 일
- 사용자가 맥에서 첫 실행 → 결과(설치 성공/오류 화면) 받아서 대응
