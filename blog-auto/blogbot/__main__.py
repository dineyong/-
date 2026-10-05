"""실행: python -m blogbot  (맥 앱이 이걸 대신 실행해 줌)"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

from . import __version__, config


def already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as r:
            return json.loads(r.read()).get("app") == "blogbot"
    except Exception:
        return False


def main():
    from .server import PORT, old_project
    url = f"http://127.0.0.1:{PORT}/"
    if already_running(PORT):            # 이미 켜져 있으면 화면만 다시 열기
        webbrowser.open(url)
        return

    from . import scheduler, server
    from .log import cleanup_old, log

    log(f"🚀 블로그 자동화 {__version__} 시작 — 데이터 폴더: {config.HOME}")
    cleanup_old()
    # 처음 켰을 때 예전 프로그램 폴더가 있으면 설정·로그인을 자동으로 가져옴
    old = None if config.get("GEMINI_API_KEY") else old_project()
    if old:
        try:
            done = config.import_old_project(old)
        except Exception as e:  # noqa: BLE001 — 가져오기 실패로 프로그램이 안 켜지면 안 됨
            done = []
            log(f"⚠️ 예전 프로그램에서 가져오기 실패: {e}")
        if done:
            log("📥 예전 프로그램에서 자동으로 가져옴: " + ", ".join(done))
    # 맥이 잠들지 않게 (프로그램이 켜져 있는 동안만)
    if sys.platform == "darwin":
        try:
            subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        except Exception:
            pass
    server.serve(PORT)
    scheduler.start()
    from . import updater
    updater.start(scheduler.busy)          # 1시간마다 새 버전 확인 → 있으면 받아서 다시 켜기
    if os.environ.get("BLOGBOT_NO_BROWSER") != "1":
        webbrowser.open(url)
    log(f"🖥️ 대시보드: {url}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
