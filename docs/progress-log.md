# 진행 기록

## 2026-10-05 — 파이썬 앱으로 새로 만듦 (v1.0.0)
- 사용자 요청: 파이썬, 터미널 없이, 켜두면 알아서 정보글 자동 작성·예약, **쇼핑글은 정보글 3편당 1편**. "전부 파이썬으로 새로 만들기" 선택
- 결과: `블로그자동화-파이썬-v1.0.0.zip` (맥 앱 + 처음 읽어주세요.txt). 데이터는 ~/BlogAuto
- 설계·함정·검증 전체: **claude/python-app-build-log.md**, 사용법: **claude/user-guide-python.md**
- 바뀐 점: 쇼핑글도 자동(링크 대기열에서), 여러 링크 한꺼번에 넣기 구현됨, 오류 시 자동 재시작, 예전 프로그램 설정·로그인·예약글·링크 자동 가져오기
- 이전 결정 "정보 2 : 쇼핑 1" → **정보 3 : 쇼핑 1**로 변경 (사용자 요청)
- 아직 맥 실기 확인 전: 첫 설치(pip/chromium), 실제 예약 발행 1회, 쇼핑글

## 2026-10-02
- 회사 PC(Windows): 설치 1~4단계 완료.
- 집 맥북(macOS, 사용자 폴더 ~/Downloads/naver-bc-automation-main): 설치·로그인·첫 발행(20:07) → 정보글+일러스트+예약 성공(22:02) → **자동 정보글(하루 2개·3일치) 가동, 예약 잘 올라옴(23:16)**
- 사용자 3일간 외출 예정 — 출발 전 3일치 예약 채워두는 방식
- 블로그: https://blog.naver.com/everysales_ (NAVER_BLOG_ID="everysales_", 밑줄 포함)
- Pexels: 신규 API 키 발급 불가 → 사용 안 함 (정보글은 일러스트만)

### 고친 것 / 추가한 것 (scripts/simple-agent.ts 외)
1. 인플루언서 톤 9섹션 프롬프트, 공정위 고지 최상단, 대시보드 메모 = 실사용 메모
2. Gemini 모델: 기본 gemini-3.8-flash, maxOutputTokens 16384
3. 과부하 시 5·15초 재시도 후 3.8 → 3.7 → 3.6 → 3.5 Flash → 3.5 Flash-Lite. 404 모델은 건너뜀
4. 안전장치: 에디터 모든 frame 탐색·못 찾으면 중단 / 좌표 클릭 제거 / 줄마다 에디터 확인 / 새 창 감시(최종 클릭 후 무시) / 실제 주소 확인 시에만 PUBLISHED
5. 제목→본문: 본문 클릭/Enter, 제목 변경 감지 시 중단
6. 공정위 문구(사용자 지정, 본문 맨 위) / 해시태그 유지
7. 스타일: claude/writing-style-guide.md. 'ㅎㅎ/ㅋㅋ/ㅠㅠ'·인사 이모지 금지
8. **예약 발행 (기본)**: 하루 3칸(9–11, 13–16, 19–22시), 10분 단위, 최소 1시간 뒤. 실제 동작 확인됨
9. publish route: 동시 작성 1개, 재발행 차단, 작업 로그 logs/{shop|info|auto-info}-시각.log
10. 대시보드: 🛒 / 📝 탭, "예약됨" 배지
11. **손그림 일러스트** (scripts/illustrations.ts): SVG → Playwright PNG, 별도 browser context
    - 장면 9종: desk·kitchen·living·vanity·closet·bedroom·bathroom·travel·veranda
    - 카드: 썸네일 / 장면 / 체크리스트 / 추천(fit) / **①②③ 단계 카드 / 💡 팁 메모지 / 자주 묻는 질문(Q 분홍·A 민트 말풍선) / 마무리 카드**
    - **크기**: IMAGE_WIDTH 480px, THUMB_WIDTH 600px (사용자: 1080px은 블로그에서 너무 큼). 쇼핑 상품사진도 resizePhotos로 480px
12. **정보글 모드**: 마케팅 담당자 관점 기획(독자·검색의도·메인/연관 키워드 배치·클릭·체류·반응·그림), 주제는 "생활 꿀팁" 소분류를 돌아가며 + 롱테일·계절성 + 일러스트로 표현 좋은 주제
    - 7섹션 + 준비물·💡팁. **자주 묻는 질문은 본문 글자 대신 카드 이미지로** (사용자: 글자 Q&A가 밋밋함)
    - **정보글 그림 10장 배치**: 썸네일 → 도입 → 장면 → 결론·준비물 → 한눈에 정리+STEP1 → ① → STEP2 → ② → STEP3 → ③ → 팁 → 주의 → FAQ+기억 카드 → 마무리 → 마무리 카드
    - 한 자리에 여러 장: slots 타입 (string | string[] | null)[]
13. 타이핑: 글자 keyboard.type, 이모지 grapheme 단위 insertText
14. 블로그식 줄바꿈 blogLines()
15. 사진 업로드: 여러 셀렉터 + input[type=file], 이미지 수 증가로 확인
16. **자동 정보글** (src/lib/autoInfo.ts + src/instrumentation.ts + /api/auto): 10분마다, 미래 예약 정보글 < 하루N×며칠 이면 1개 작성(--info --auto, 끝나면 브라우저 닫힘), 멈춘 작업 정리, 연속 3번 실패 시 꺼짐. 정보글 하루 상한 = 자동 설정 perDay. 실행: `caffeinate -i npm run dev`

### 결정 사항
- 발행 전 미리보기 불필요 (예약 후 폰에서 수정)
- 상품 정보 정확도 개선 보류
- 사진 원칙: 제품은 공식 이미지만, 가짜 사용 사진 금지, 나머지는 손그림 일러스트
- 블로그 운영: "생활 꿀팁" 우산 아래 소분류 다양화, 정보 2 : 쇼핑 1 (→ 10/05 정보 3 : 쇼핑 1로 변경)
- 쇼핑커넥트 링크 자동 수집 비추천 (대안: 여러 링크 한꺼번에 — 10/05 파이썬 앱에 구현)

### 열린 이슈
- 이미지가 작게(480px) 보이는지 확인 필요 — 네이버 에디터가 문서 너비로 늘리면 에디터의 사진 크기 버튼을 써야 할 수 있음
- "알리익스프레스 새 창" 제보 — 출처 확인 대기
- 쇼핑커넥트 글 새 코드 검증 안 됨

### 겪은 설치 이슈
- Windows: cmd를 scripts 폴더에서 열어 prisma schema 못 찾음 → cd ..
- .env 없이 prisma db push → cp .env.example .env 먼저
- 맥: npx playwright install chromium
- 맥 텍스트 편집기 스마트 따옴표 → 따옴표 안쪽만 수정
- Prisma 업데이트 알림 무시
- 업데이트 배포: `ditto ~/Downloads/<폴더>/files .` (끝의 점 필수! 빠뜨리면 "No destination")
- 프로그램 중복 실행(Port 3000 in use / lock) → `pkill -f "next dev"` 후 다시 켜기
- 전체 묶음: 블로그자동화_전체.zip + claude/user-guide.md

### 다음 할 일
- 파이썬 앱 맥 첫 실행 결과 확인
- 그림 10장·크기 결과 확인
- 쇼핑커넥트 글 검증
