"""대시보드 — 파이썬 내장 웹서버 (http://127.0.0.1:8765). 설치할 것 없음."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, accounts, comments, config, db, scheduler, shopconnect, style, trends, updater
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
    trends.tidy_lists()
    return {
        "version": __version__,
        "auto": scheduler.get(),
        "counts": scheduler.counts(1),
        "accounts": [dict(a, login_bad=scheduler.login_bad(a["id"]), counts=scheduler.counts(a["id"]))
                     for a in accounts.all()],
        "batch": scheduler.state.get("batch"),
        "current": scheduler.state["current"],
        "next_check": scheduler.state["next_check"],
        "logged_in": bool(scheduler.usable_accounts()),
        "settings": {k: (_mask(cfg.get(k, "")) if k.endswith("_API_KEY") else cfg.get(k, "")) for k in config.EDITABLE},
        "labels": config.EDITABLE,
        "posts": db.q("SELECT id, kind, status, topic, title, scheduled_at, error, note, mode, post_url, auto, account, "
                      "created_at FROM posts ORDER BY id DESC LIMIT 80"),
        # 링크 + 그 링크로 쓴 글 (사용 완료 표시용)
        "links": db.q("SELECT l.id, l.url, l.memo, l.status, l.product_name, l.account, l.keyword, l.created_at, l.used_at, "
                      "p.id AS post_id, p.title AS post_title, p.status AS post_status, p.scheduled_at AS post_at "
                      "FROM links l LEFT JOIN posts p ON p.id = l.used_post "
                      "ORDER BY CASE l.status WHEN 'WAITING' THEN 0 ELSE 1 END, l.id DESC LIMIT 100"),
        # 최근 30일 안에 링크를 넣은 키워드 (키워드 줄에서 '다 쓴 키워드'로 빼고 결과만 보여줌)
        "kw_links": db.q("SELECT l.id, l.status, l.account, l.keyword, l.created_at, "
                         "p.title AS post_title, p.status AS post_status, p.scheduled_at AS post_at "
                         "FROM links l LEFT JOIN posts p ON p.id = l.used_post "
                         "WHERE l.keyword IS NOT NULL AND l.created_at >= ? ORDER BY l.id DESC",
                         ((dt.date.today() - dt.timedelta(days=trends.USED_DAYS)).isoformat(),)),
        "logs": _recent(),
        "style": style.get(),
        "update": updater.get(),
        "sc": shopconnect.get(), "sc_ready": bool(accounts.get(1)["sc_base"]),
        "trends": trends.get(), "trend_cats": trends.CATS, "recon": scheduler.state.get("recon"), "editorcheck": scheduler.state.get("editorcheck"),
        "comments": dict(comments.get(), back=comments.back_list(), max_reply=comments.REPLY_MAX,
                         max_visit=comments.VISIT_MAX,
                         today={a["id"]: comments.today_counts(a["id"]) for a in accounts.all()}),
        "comment_rows": comments.recent(),
        "old_project": str(old_project() or ""),
        "home": str(config.HOME),
    }


def add_links(text: str, memo: str, account: int = 1) -> int:
    urls = re.findall(r"https?://[^\s<>\"']+", text or "")
    n = 0
    for u in dict.fromkeys(urls):
        if db.one("SELECT id FROM links WHERE url=? AND status='WAITING'", (u,)):
            continue
        db.run("INSERT INTO links(url, memo, status, account, created_at) VALUES(?,?, 'WAITING', ?, ?)",
               (u, memo or None, account, db.now()))
        n += 1
    return n


def handle(path: str, body: dict) -> dict:
    if path == "/api/auto":
        patch = {k: body[k] for k in ("enabled", "per_day", "days_ahead", "ratio", "mode") if k in body}
        if patch.get("enabled") and not config.get("GEMINI_API_KEY"):
            return {"ok": False, "msg": "먼저 설정에서 Gemini API 키를 넣어주세요."}
        if patch.get("enabled") and not scheduler.usable_accounts():
            return {"ok": False, "msg": "먼저 설정 › 블로그 계정에서 블로그 아이디와 네이버 로그인을 해주세요."}
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
                                                      body.get("mode"), int(body.get("account") or 1))}
    if path == "/api/comments":
        patch = {k: body[k] for k in ("reply_on", "visit_on", "practice", "reply_per_day", "visit_per_day",
                                      "visit_ids", "visit_back") if k in body}
        if (patch.get("reply_on") or patch.get("visit_on")) and not config.get("GEMINI_API_KEY"):
            return {"ok": False, "msg": "먼저 설정에서 Gemini API 키를 넣어주세요."}
        if (patch.get("reply_on") or patch.get("visit_on")) and not scheduler.usable_accounts():
            return {"ok": False, "msg": "먼저 설정 › 블로그 계정에서 네이버 로그인을 해주세요."}
        before = comments.get()
        s = comments.save(**patch)
        for k, name in (("reply_on", "내 글 답글"), ("visit_on", "이웃 댓글"), ("practice", "연습 모드")):
            if k in patch and bool(before[k]) != bool(s[k]):
                log(f"💬 {name} {'켜짐' if s[k] else '꺼짐'}")
        if "visit_ids" in patch:
            return {"ok": True, "msg": f"이웃 {len(comments.visit_ids())}곳을 저장했어요."}
        return {"ok": True}
    if path == "/api/comments/run":
        if not config.get("GEMINI_API_KEY"):
            return {"ok": False, "msg": "먼저 설정에서 Gemini API 키를 넣어주세요."}
        return {"ok": True, "msg": scheduler.run_comments(force=True)}
    if path == "/api/comments/back_remove":
        db.set_setting("comment_back", [b for b in comments.back_list() if b["id"] != body.get("id")])
        return {"ok": True}
    if path == "/api/login":
        return {"ok": True, "msg": scheduler.run_login(int(body.get("account") or 1))}
    if path == "/api/batch/start":
        if not config.get("GEMINI_API_KEY"):
            return {"ok": False, "msg": "먼저 설정에서 Gemini API 키를 넣어주세요."}
        return {"ok": True, "msg": scheduler.start_batch()}
    if path == "/api/batch/stop":
        return {"ok": True, "msg": scheduler.stop_batch()}
    if path == "/api/accounts/save":
        n = int(body["id"])
        accounts.save(n, label=body.get("label"), blog_id=body.get("blog_id"), sc_url=body.get("sc_url"),
                      enabled=body.get("enabled"))
        log(f"👤 계정 저장: {accounts.name(n)}")
        return {"ok": True, "msg": "저장했어요."}
    if path == "/api/accounts/add":
        try:
            n = accounts.add()
        except ValueError as e:
            return {"ok": False, "msg": str(e)}
        return {"ok": True, "msg": f"{n}번 계정 칸을 만들었어요. 블로그 아이디를 넣고 로그인해 주세요."}
    if path == "/api/accounts/remove":
        try:
            accounts.remove(int(body["id"]))
        except ValueError as e:
            return {"ok": False, "msg": str(e)}
        return {"ok": True, "msg": "계정을 뺐어요. (그 계정으로 쓴 글 기록은 남아 있어요)"}
    if path == "/api/kw/link":
        # 인기 키워드 줄에서: 직접 발급한 링크 넣기 (+ 바로 쇼핑글 쓰기)
        url = (body.get("url") or "").strip()
        if not re.match(r"https?://\S+$", url):
            return {"ok": False, "msg": "쇼핑커넥트에서 발급한 링크(https://…)를 붙여넣어 주세요."}
        acc_n = int(body.get("account") or 1)
        kw = (body.get("keyword") or "").strip()[:40] or None
        lid = db.run("INSERT INTO links(url, status, account, keyword, created_at) VALUES(?, 'WAITING', ?, ?, ?)",
                     (url, acc_n, kw, db.now()))
        log(f"🔗 링크 넣음{f' ({kw})' if kw else ''} → {accounts.get(acc_n)['label']} 대기열")
        if body.get("write"):
            if not config.get("GEMINI_API_KEY"):
                return {"ok": False, "msg": "링크는 넣었어요. 글을 쓰려면 설정에서 Gemini API 키를 넣어주세요."}
            msg = scheduler.request("shop", None, "schedule", acc_n, lid)
            if msg.startswith("이미") or msg.startswith("다른"):
                return {"ok": True, "msg": "링크는 대기열에 넣었어요. 지금 다른 글을 쓰는 중이라, 끝나면 [쇼핑글 쓰기]를 눌러주세요."}
            return {"ok": True, "msg": "링크를 넣고 쇼핑글을 쓰기 시작했어요. 빈 시간에 예약돼요."}
        return {"ok": True, "msg": "링크를 대기열에 넣었어요. 쇼핑글 차례(또는 [시작])에 써요."}
    if path == "/api/links":
        n = add_links(body.get("text", ""), body.get("memo", ""), int(body.get("account") or 1))
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
        return {"ok": True, "msg": ""}
    if path == "/api/kw/skip":
        kw = (body.get("keyword") or "").strip()
        if kw:
            trends.skip(kw)
        return {"ok": True, "msg": f"'{kw}'는 2주 동안 안 보여줄게요."}
    if path == "/api/kw/unskip":
        n = trends.unskip_all()
        return {"ok": True, "msg": f"뺀 키워드 {n}개를 다시 보여줘요."}
    if path == "/api/kw/add":
        kws = [k.strip() for k in re.split(r"[,\n]", body.get("keyword") or "") if k.strip()]
        if not kws:
            return {"ok": False, "msg": "키워드를 적어주세요."}
        n = trends.add_extra([{"kw": k} for k in kws], "me")
        return {"ok": True, "msg": f"키워드 {n}개를 맨 위에 넣었어요." if n else "이미 있는 키워드예요."}
    if path == "/api/kw/suggest":
        if not config.get("GEMINI_API_KEY"):
            return {"ok": False, "msg": "설정에서 Gemini API 키를 먼저 넣어주세요."}
        if trends.get().get("suggesting"):
            return {"ok": True, "msg": "이미 고르는 중이에요."}
        threading.Thread(target=trends.suggest, args=(int(body.get("account") or 1),), daemon=True).start()
        return {"ok": True, "msg": "AI가 다른 키워드를 고르고 있어요 (10~30초)."}
    if path == "/api/trends/refresh":
        return {"ok": True, "msg": scheduler.run_trends()}
    if path == "/api/trends/cats":
        cats = [c for c in body.get("cats", []) if c in trends.CATS] or trends.DEFAULT_CATS
        trends.save(cats=cats, date=None, tried_at=None)        # 분야 바꾸면 다시 모으기
        return {"ok": True, "msg": scheduler.run_trends()}
    if path == "/api/sc/settings":
        shopconnect.save(**{k: body[k] for k in ("min_reviews", "min_commission") if k in body})
        return {"ok": True, "msg": "저장했어요."}
    if path == "/api/sc/recommend":
        if not accounts.get(1)["sc_base"]:
            return {"ok": False, "msg": "설정에 '쇼핑커넥트 상품 목록 주소'를 먼저 넣어주세요."}
        return {"ok": True, "msg": scheduler.run_recommend((body.get("keyword") or "").strip() or None)}
    if path == "/api/sc/add":
        url = (body.get("url") or "").strip()
        if not re.match(r"https?://\S+$", url):
            return {"ok": False, "msg": "발급받은 링크(https://…)를 붙여넣어 주세요."}
        shopconnect.add_link(str(body.get("id")), url, body.get("memo") or "", int(body.get("account") or 1))
        return {"ok": True, "msg": "링크 대기열에 넣었어요. 쇼핑글 차례에 이 상품으로 써요."}
    if path == "/api/sc/clear":
        shopconnect.save(items=[])
        return {"ok": True, "msg": "추천 목록을 비웠어요."}
    if path == "/api/sc/skip":
        shopconnect.skip(str(body.get("id")))
        return {"ok": True, "msg": "이 상품은 다시 추천하지 않을게요."}
    if path == "/api/recon":
        kw = (body.get("keyword") or "").strip() or (trends.hints(1) or ["물티슈"])[0]
        return {"ok": True, "msg": scheduler.run_recon(kw)}
    if path == "/api/editor_check":
        return {"ok": True, "msg": scheduler.run_editor_check(int(body.get("account") or 1))}
    if path == "/api/open_recon":
        os.system(f'open "{config.HOME / "recon"}" >/dev/null 2>&1 &')
        return {"ok": True}
    if path == "/api/posts/clear_failed":
        n = db.run("DELETE FROM posts WHERE status='FAILED'")
        return {"ok": True, "msg": "실패한 글 기록을 지웠어요."}
    if path == "/api/settings":
        upd = {k: str(v).strip() for k, v in body.items() if k in config.EDITABLE and str(v).strip()}
        for k in [k for k in upd if k.endswith("_API_KEY") and "…" in upd[k]]:
            upd.pop(k)                         # 가려진 값 그대로면 안 바꿈
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
