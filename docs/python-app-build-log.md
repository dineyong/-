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

## 아직 실제 네이버에서 확인 못 한 것
- 파이썬판으로 실제 예약 발행 1회 (에디터 셀렉터는 Node판과 동일하므로 같은 결과 예상)
- 첫 실행 pip/chromium 설치 (맥 실제 환경)
- 쇼핑글 실사용, 에디터 안 사진 크기, 알리 팝업 건(Node 때부터 미해결)

## 다음에 블로그 프로그램 만들 때 재사용할 것
- 맥 앱 묶음 방식(launcher.sh + build_app.py)은 그대로 재사용 가능 — 파이썬 패키지 이름과 PW 버전만 바꾸면 됨
- 대시보드는 의존성 0 (http.server + 한 파일 HTML)
- 이모지 입력·에디터 iframe·좌표 클릭 금지 등 네이버 에디터 규칙은 naver.py 위쪽 주석에 정리

## 다음 할 일
- 사용자가 맥에서 첫 실행 → 결과(설치 성공/오류 화면) 받아서 대응
