"""대시보드 — 파이썬 내장 웹서버 (http://127.0.0.1:8765). 설치할 것 없음."""
from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, config, db, scheduler, style, updater
from . import log as _logmod
from .log import RECENT, log

PORT = 8765
WEB = Path(__file__).parent / "web" / "index.html"
def old_project() -> Path | None:
    """예전 프로그램 파일이 있는 곳: ~/BlogAuto/old (복사본) 먼저, 없으면 다운로드 폴더."""
    for p in (config.OLD_COPY, config.OLD_DOWNLOADS):
        if config.can_see(p / ".env") or config.can_see(p / "naver-session.json"):
            return p
    return None
_server: ThreadingHTTPServer | None = None


def _mask(v: str) -> str:
    return (v[:4] + "…" + v[-4:]) if len(v) > 10 else ("입력됨" if v else "")


def _recent() -> list:
    with _logmod._lock:          # 다른 스레드가 기록 중일 때 복사하다 깨지지 않게
        return list(RECENT)[-200:]


def state() -> dict:
    cfg = config.load()
    return {
        "version": __version__,
        "auto": scheduler.get(),
        "counts": scheduler.counts(),
        "current": scheduler.state["current"],
        "next_check": scheduler.state["next_check"],
        "logged_in": config.SESSION_FILE.exists(),
        "settings": {k: (_mask(cfg.get(k, "")) if k == "GEMINI_API_KEY" else cfg.get(k, "")) for k in config.EDITABLE},
        "labels": config.EDITABLE,
        "posts": db.q("SELECT id, kind, status, topic, title, scheduled_at, error, note, mode, post_url, auto, created_at FROM posts "
                      "ORDER BY id DESC LIMIT 60"),
        "links": db.q("SELECT id, url, memo, status, product_name, created_at FROM links "
                      "ORDER BY CASE status WHEN 'WAITING' THEN 0 ELSE 1 END, id DESC LIMIT 100"),
        "logs": _recent(),
        "style": style.get(),
        "update": updater.get(),
        "old_project": str(old_project() or ""),
        "home": str(config.HOME),
    }


def add_links(text: str, memo: str) -> int:
    urls = re.findall(r"https?://[^\s<>\"']+", text or "")
    n = 0
    for u in dict.fromkeys(urls):
        if db.one("SELECT id FROM links WHERE url=? AND status='WAITING'", (u,)):
            continue
        db.run("INSERT INTO links(url, memo, status, created_at) VALUES(?,?, 'WAITING', ?)", (u, memo or None, db.now()))
        n += 1
    return n


def handle(path: str, body: dict) -> dict:
    if path == "/api/auto":
        patch = {k: body[k] for k in ("enabled", "per_day", "days_ahead", "ratio", "mode") if k in body}
        if patch.get("enabled") and not config.get("GEMINI_API_KEY"):
            return {"ok": False, "msg": "먼저 설정에서 Gemini API 키를 넣어주세요."}
        if patch.get("enabled") and not config.SESSION_FILE.exists():
            return {"ok": False, "msg": "먼저 '네이버 로그인'을 해주세요."}
        s = scheduler.save(**patch)
        if "enabled" in patch:
            log("▶️ 자동 작성 켜짐" if s["enabled"] else "⏸️ 자동 작성 꺼짐")
            if s["enabled"]:
                scheduler._wake.set()
        return {"ok": True}
    if path == "/api/write":
        if not config.get("GEMINI_API_KEY"):
            return {"ok": False, "msg": "먼저 설정에서 Gemini API 키를 넣어주세요."}
        return {"ok": True, "msg": scheduler.request(body.get("kind"), (body.get("topic") or "").strip() or None,
                                                      body.get("mode"))}
    if path == "/api/login":
        return {"ok": True, "msg": scheduler.run_login()}
    if path == "/api/links":
        n = add_links(body.get("text", ""), body.get("memo", ""))
        return {"ok": n > 0, "msg": f"링크 {n}개를 대기열에 넣었어요." if n else "새 링크(https://…)를 찾지 못했어요."}
    if path == "/api/links/delete":
        db.run("DELETE FROM links WHERE id=?", (int(body["id"]),))
        return {"ok": True}
    if path == "/api/links/retry":
        db.run("UPDATE links SET status='WAITING' WHERE id=?", (int(body["id"]),))
        return {"ok": True}
    if path == "/api/posts/delete":
        db.run("DELETE FROM posts WHERE id=? AND status != 'WRITING'", (int(body["id"]),))
        return {"ok": True}
    if path == "/api/posts/mark_done":
        # 실제로는 예약·발행됐는데 실패로 남은 글을 사용자가 직접 '완료'로
        row = db.one("SELECT scheduled_at, mode FROM posts WHERE id=? AND status='FAILED'", (int(body["id"]),))
        if not row:
            return {"ok": False, "msg": "실패 상태인 글만 바꿀 수 있어요."}
        at = row["scheduled_at"] or db.now()[:16]
        st = "SCHEDULED" if at > db.now() or row["mode"] == "schedule" else "PUBLISHED"
        db.update_post(int(body["id"]), status=st, scheduled_at=at, error=None, note="직접 완료로 표시함")
        return {"ok": True, "msg": "완료로 바꿨어요."}
    if path == "/api/style":
        notes = str(body.get("notes", "")).strip()
        style.save(notes=notes, updated_at=db.now()[:16])
        log("🗣️ 말투 메모를 직접 고쳤어요")
        return {"ok": True, "msg": "말투 메모를 저장했어요. 다음 글부터 반영돼요."}
    if path == "/api/style/check":
        def job():
            try:
                r = style.check(force=True)
                log(f"🗣️ 말투 학습(직접 실행): {r or '확인함'}")
            except Exception as e:  # noqa: BLE001
                log(f"🗣️ 말투 학습 오류: {e}")
        threading.Thread(target=job, daemon=True).start()
        return {"ok": True, "msg": "공개된 글을 확인하고 있어요. 잠시 뒤 작업 기록을 봐주세요."}
    if path == "/api/update/check":
        def job():
            r = updater.check(busy=scheduler.busy)
            log(f"⬇️ 업데이트 확인: {updater.get()['last_result'] or r}")
        threading.Thread(target=job, daemon=True).start()
        return {"ok": True, "msg": "새 버전을 확인하고 있어요."}
    if path == "/api/posts/clear_failed":
        n = db.run("DELETE FROM posts WHERE status='FAILED'")
        return {"ok": True, "msg": "실패한 글 기록을 지웠어요."}
    if path == "/api/settings":
        upd = {k: str(v).strip() for k, v in body.items() if k in config.EDITABLE and str(v).strip()}
        if "GEMINI_API_KEY" in upd and "…" in upd["GEMINI_API_KEY"]:
            upd.pop("GEMINI_API_KEY")          # 가려진 값 그대로면 안 바꿈
        if "NAVER_BLOG_ID" in upd:
            m = re.search(r"blog\.naver\.com/([A-Za-z0-9_\-]+)", upd["NAVER_BLOG_ID"])
            upd["NAVER_BLOG_ID"] = m.group(1) if m else upd["NAVER_BLOG_ID"]
        config.save(upd)
        log("⚙️ 설정 저장: " + ", ".join(config.EDITABLE[k].split(" (")[0] for k in upd))
        return {"ok": True, "msg": "저장했어요."}
    if path == "/api/import":
        folder = Path(os.path.expanduser(body.get("folder") or str(old_project() or config.OLD_COPY)))
        if not config.can_see(folder):
            return {"ok": False, "msg": f"폴더를 찾지 못했어요: {folder}"}
        done = config.import_old_project(folder)
        log("📥 예전 프로그램에서 가져옴: " + (", ".join(done) or "없음"))
        return {"ok": bool(done), "msg": ("가져왔어요: " + ", ".join(done)) if done else "가져올 것이 없었어요."}
    if path == "/api/open_folder":
        os.system(f'open "{config.HOME}" >/dev/null 2>&1 &')
        return {"ok": True}
    if path == "/api/quit":
        log("👋 프로그램을 종료해요.")
        threading.Timer(0.5, lambda: os._exit(0)).start()
        return {"ok": True, "msg": "종료했어요. 이 창은 닫아도 돼요."}
    return {"ok": False, "msg": "알 수 없는 요청"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):   # 접속 기록은 안 남김
        pass

    def _send(self, code: int, data: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

    def _local(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self._local():
            return self._send(403, b"forbidden", "text/plain")
        if self.path in ("/", "/index.html"):
            return self._send(200, WEB.read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            return self._json(state())
        if self.path == "/api/ping":
            return self._json({"app": "blogbot", "version": __version__, "pid": os.getpid()})
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        # 다른 사이트에서 몰래 보내는 요청 막기 (직접 만든 헤더가 있어야 함)
        if not self._local() or self.headers.get("X-Blogbot") != "1":
            return self._send(403, b"forbidden", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            self._json(handle(self.path, body))
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ 화면 요청 오류: {e}")
            self._json({"ok": False, "msg": str(e)}, 500)


def serve(port: int = PORT) -> ThreadingHTTPServer:
    global _server
    _server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=_server.serve_forever, daemon=True, name="web").start()
    return _server
