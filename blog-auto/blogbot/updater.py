"""자동 업데이트 — GitHub에 올라간 새 버전을 받아서 스스로 바꾸고 다시 켜기.

- 업데이트 창고: UPDATE_URL/manifest.json  ({"version": "1.2.0", "files": {"blogbot/x.py": "sha256…"}, "notes": "…"})
- 새 코드는 앱 안이 아니라 ~/BlogAuto/app 에 받음 → 맥의 "악성 코드" 경고 없음
- 파일마다 sha256 확인, 하나라도 틀리면 적용 안 함
- 적용: app.new 에 다 받은 뒤 app → app.prev (예전 것 보관), app.new → app 로 바꿈
- 글 쓰는 중에는 바꾸지 않음. 바꾼 뒤 종료코드 3으로 끝내면 실행기가 바로 다시 켬
- 새 버전이 켜지자마자 계속 꺼지면 실행기가 app.prev 로 되돌림
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import urllib.request

from . import __version__, config, db
from .log import log

KEY = "update"
CHECK_EVERY_S = 60 * 60
RESTART_CODE = 3
APP_DIR = config.HOME / "app"
_lock = threading.Lock()


def _vt(v: str) -> tuple:
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except ValueError:
        return (0,)


def get() -> dict:
    s = {"latest": None, "last_check": None, "last_result": "", "notes": ""}
    s.update(db.setting(KEY, {}) or {})
    s["current"] = __version__
    s["checking"] = _lock.locked()
    s["available"] = bool(s.get("latest")) and _vt(s["latest"]) > _vt(__version__)
    return s


def _save(**patch):
    s = get()
    s.update(patch)
    s.pop("current", None)
    db.set_setting(KEY, s)


def _fetch(url: str, timeout: int = 30) -> bytes:
    if url.startswith("http"):
        url += ("&" if "?" in url else "?") + f"t={int(time.time())}"     # 캐시 피하기
    req = urllib.request.Request(url,
                                 headers={"User-Agent": f"blogbot/{__version__}", "Cache-Control": "no-cache"})
    ctx = config.SSL if url.startswith("https") else None
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


def check(apply: bool = True, busy=None) -> str:
    """새 버전 확인 → 있으면 받아서 적용(글 쓰는 중이 아니면). 돌려주는 값은 화면에 보여줄 한 줄."""
    if not _lock.acquire(blocking=False):
        return "이미 확인 중이에요"
    try:
        base = config.get("UPDATE_URL").rstrip("/")
        if not base:
            return ""
        now = time.strftime("%Y-%m-%dT%H:%M")
        try:
            man = json.loads(_fetch(base + "/manifest.json"))
        except Exception as e:  # noqa: BLE001
            _save(last_check=now, last_result=f"업데이트 확인 실패 (인터넷?): {e}")
            return "확인 실패"
        latest = str(man.get("version", ""))
        _save(last_check=now, latest=latest, notes=str(man.get("notes", ""))[:500])
        if _vt(latest) <= _vt(__version__):
            _save(last_result=f"최신 버전이에요 ({__version__})")
            return "최신"
        bad = config.HOME / "app.bad" / "blogbot" / "__init__.py"
        try:
            if bad.exists() and f'"{latest}"' in bad.read_text("utf-8"):
                _save(last_result=f"새 버전 {latest} 이 이 맥에서 켜지지 않아 예전 버전을 쓰는 중 — 고친 버전을 기다려요")
                return "문제 버전 건너뜀"
        except OSError:
            pass
        if not apply:
            _save(last_result=f"새 버전 {latest} 이 있어요")
            return "새 버전 있음"
        files = man.get("files") or {}
        if not files or "blogbot/__init__.py" not in files:
            _save(last_result="업데이트 목록이 이상해서 건너뛰었어요")
            return "목록 이상"
        new_dir = config.HOME / "app.new"
        shutil.rmtree(new_dir, ignore_errors=True)
        try:
            for rel, sha in files.items():
                if rel.startswith(("/", "..")) or ".." in rel.split("/"):
                    raise ValueError(f"이상한 경로: {rel}")
                data = _fetch(f"{base}/{rel}")
                if hashlib.sha256(data).hexdigest() != sha:
                    raise ValueError(f"{rel} 내용이 목록과 달라요 (아직 올라가는 중일 수 있어요)")
                out = new_dir / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
        except Exception as e:  # noqa: BLE001
            shutil.rmtree(new_dir, ignore_errors=True)
            _save(last_result=f"새 버전 {latest} 받기 실패 — 다음에 다시: {e}")
            log(f"⬇️ 업데이트 받기 실패: {e}")
            return "받기 실패"
        # 글 쓰는 중이면 기다렸다가 다음 확인 때 적용
        if busy is not None and not busy.acquire(blocking=False):
            shutil.rmtree(new_dir, ignore_errors=True)
            _save(last_result=f"새 버전 {latest} — 글 작성이 끝나면 적용해요")
            return "작성 중이라 대기"
        done = False
        try:
            prev = config.HOME / "app.prev"
            shutil.rmtree(prev, ignore_errors=True)
            if APP_DIR.exists():
                APP_DIR.rename(prev)
            new_dir.rename(APP_DIR)
            _save(last_result=f"{__version__} → {latest} 업데이트 완료, 다시 켜는 중")
            log(f"⬆️ 새 버전 {latest} 으로 업데이트했어요. 잠깐 다시 켤게요." +
                (f" ({man.get('notes')})" if man.get("notes") else ""))
            done = True
            threading.Timer(1.0, lambda: os._exit(RESTART_CODE)).start()   # 끝날 때까지 작업 잠금 유지
            return "업데이트함"
        finally:
            if busy is not None and not done:
                busy.release()
    finally:
        _lock.release()


def start(busy):
    def loop():
        time.sleep(90)
        while True:
            try:
                check(busy=busy)
            except Exception as e:  # noqa: BLE001
                log(f"⬇️ 업데이트 확인 오류: {e}")
            time.sleep(CHECK_EVERY_S)
    threading.Thread(target=loop, daemon=True, name="updater").start()
