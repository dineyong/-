#!/bin/bash
# 블로그 자동화 — 맥 앱 실행기 (더블클릭하면 이게 실행됨, 터미널 필요 없음)
# 1) 처음이면: 파이썬 가상환경 + playwright + 크롬 설치 (3~5분, 한 번만)
# 2) 프로그램 실행 → 대시보드가 브라우저로 열림
# 3) 프로그램이 오류로 꺼지면 10초 뒤 다시 켬 (대시보드 '종료' 버튼으로 끄면 그대로 끝)

APP_RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
HOME_DIR="$HOME/BlogAuto"
VENV="$HOME_DIR/.venv"
LOGS="$HOME_DIR/logs"
SETUP_LOG="$LOGS/setup.log"
PW_VERSION="$(cat "$APP_RES/playwright-version.txt" 2>/dev/null || echo 1.56.0)"
MARK="$VENV/.ready-$PW_VERSION"
mkdir -p "$LOGS"
exec 2>>"$LOGS/launcher.log"                      # 실행기 자체 오류도 기록
echo "[$(date '+%m/%d %H:%M:%S')] 앱 실행 (mark: $([ -f "$MARK" ] && echo 있음 || echo 없음))" >>"$LOGS/launcher.log"
export PATH="/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/Current/bin:$PATH"

say_dialog() {   # 제목 메시지 [버튼들]  — 'tell me to activate': 안내창이 다른 창 뒤에 숨지 않게 맨 앞으로
  /usr/bin/osascript -e 'tell me to activate' -e "display dialog \"$2\" with title \"$1\" buttons {$3} default button 1 with icon note" 2>>"$LOGS/launcher.log"
}
notify() { /usr/bin/osascript -e "display notification \"$1\" with title \"블로그 자동화\"" 2>/dev/null; }

# ── 파이썬 찾기 (3.9 이상) ──────────────────────
find_python() {
  for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.9/bin/python3; do
    if [ -x "$p" ] && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      echo "$p"; return 0
    fi
  done
  # 맥 기본 파이썬 (개발자 도구가 깔려 있을 때만 — 아니면 설치 창이 뜸)
  if /usr/bin/xcode-select -p >/dev/null 2>&1 && /usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    echo /usr/bin/python3; return 0
  fi
  return 1
}

# ── 처음 한 번 설치 ─────────────────────────────
if [ ! -f "$MARK" ]; then
  PY="$(find_python)"
  if [ -z "$PY" ]; then
    r=$(say_dialog "블로그 자동화" "처음 실행하려면 파이썬이 필요해요.\n\n[설치 페이지 열기]를 누르고 'Download Python' 버튼으로 받아서 설치한 뒤, 이 앱을 다시 열어주세요." "\"설치 페이지 열기\", \"닫기\"")
    case "$r" in *설치*) open "https://www.python.org/downloads/macos/";; esac
    exit 1
  fi
  say_dialog "블로그 자동화 — 처음 준비" "처음 한 번만 필요한 프로그램을 설치할게요.\n\n3~5분 걸려요. 끝나면 알려드릴게요.\n(인터넷이 연결되어 있어야 해요)" "\"시작\"" >/dev/null
  notify "설치 중이에요… (3~5분)"
  {
    echo "=== $(date) 설치 시작 — $PY ($("$PY" --version 2>&1))"
    rm -rf "$VENV"
    "$PY" -m venv "$VENV" &&
    "$VENV/bin/python" -m pip install --upgrade pip &&
    "$VENV/bin/python" -m pip install "playwright==$PW_VERSION" certifi &&
    "$VENV/bin/python" -m playwright install chromium &&
    touch "$MARK"
    echo "=== $(date) 끝 (성공: $([ -f "$MARK" ] && echo 예 || echo 아니오))"
  } >>"$SETUP_LOG" 2>&1
  if [ ! -f "$MARK" ]; then
    r=$(say_dialog "블로그 자동화" "설치 중 문제가 생겼어요.\n인터넷 연결을 확인하고 앱을 다시 열어주세요.\n\n(자세한 기록: 홈 폴더 > BlogAuto > logs > setup.log)" "\"기록 보기\", \"닫기\"")
    case "$r" in *기록*) open -e "$SETUP_LOG";; esac
    exit 1
  fi
  notify "설치 완료! 대시보드를 열게요."
fi

# ── 실행 (오류로 꺼지면 다시 켜기) ────────────────
# 코드 위치: 자동 업데이트로 받은 ~/BlogAuto/app 이 있으면 그것, 없으면 앱 안에 든 것
code_dir() {
  if [ -f "$HOME_DIR/app/blogbot/__init__.py" ]; then echo "$HOME_DIR/app"; else echo "$APP_RES/app"; fi
}
export PYTHONUNBUFFERED=1
child=""
trap '[ -n "$child" ] && kill "$child" 2>/dev/null; exit 0' TERM INT HUP   # 앱을 끄면 프로그램도 같이 끔
first=1
fast=0
while true; do
  started=$(date +%s)
  export PYTHONPATH="$(code_dir)"
  [ "$first" = "1" ] || export BLOGBOT_NO_BROWSER=1   # 다시 켤 때는 브라우저 창을 또 열지 않음
  "$VENV/bin/python" -m blogbot >>"$LOGS/app-console.log" 2>&1 &
  child=$!
  wait "$child"
  code=$?
  [ "$code" = "0" ] && exit 0                         # '종료' 버튼 → 정상 종료
  first=0
  if [ "$code" = "3" ]; then                          # 자동 업데이트 → 바로 다시 켬
    echo "[$(date '+%m/%d %H:%M:%S')] 업데이트 적용 — 다시 켬" >>"$LOGS/app-console.log"
    fast=0; sleep 1; continue
  fi
  echo "[$(date '+%m/%d %H:%M:%S')] 프로그램이 꺼짐(코드 $code) — 10초 뒤 다시 켬" >>"$LOGS/app-console.log"
  if [ $(( $(date +%s) - started )) -lt 60 ]; then fast=$((fast + 1)); else fast=0; fi
  # 업데이트 받은 코드가 켜자마자 계속 꺼지면 → 예전 버전으로 되돌림
  if [ "$fast" -ge 3 ] && [ "$PYTHONPATH" = "$HOME_DIR/app" ]; then
    echo "[$(date '+%m/%d %H:%M:%S')] 새 버전이 계속 꺼져서 예전 버전으로 되돌림" >>"$LOGS/app-console.log"
    rm -rf "$HOME_DIR/app.bad"; mv "$HOME_DIR/app" "$HOME_DIR/app.bad"
    [ -d "$HOME_DIR/app.prev" ] && mv "$HOME_DIR/app.prev" "$HOME_DIR/app"
    fast=0; continue
  fi
  # 켜자마자 3번 연속 꺼지면 (조용히 반복하지 않고) 사용자에게 알림
  if [ "$fast" -ge 3 ]; then
    r=$(say_dialog "블로그 자동화" "프로그램이 켜지자마자 꺼지고 있어요.\n\n[기록 보기]를 눌러 나온 내용을 Claude에게 보여주세요." "\"기록 보기\", \"닫기\"")
    case "$r" in *기록*) open -e "$LOGS/app-console.log";; esac
    exit 1
  fi
  sleep 10
done
