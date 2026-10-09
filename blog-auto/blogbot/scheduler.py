"""자동 작성 — 프로그램이 켜져 있는 동안 10분마다 확인.

- 앞으로 예약된 글이 (하루 개수 × 며칠치)보다 적으면 1편 써서 예약
- 비율: 정보글 3편마다 쇼핑글 1편 (쇼핑 링크 대기열에 링크가 있을 때만, 없으면 정보글)
- 인터넷 끊김은 실패로 세지 않고 다음 확인 때 다시
- 켠 뒤로 연속 3번 실패하면 스스로 꺼짐 (로그인 풀림 등)
- 대시보드의 "지금 1편 쓰기"도 같은 일꾼 스레드가 처리 (동시에 두 편 안 씀)
- 블로그 계정 여러 개(최대 3): 계정마다 따로 목표·비율·링크 대기열, 1번 → 2번 → 3번 순서로 채움
- [시작]: 켜진 계정마다 정보글 3편 + 쇼핑글 1편을 차례로 써서 예약 (한 세트)
"""
from __future__ import annotations

import datetime as dt
import queue
import threading
import time
import zlib

from . import accounts, ai, comments, db, naver, shopconnect, style, trends, writer
from .log import log

KEY = "auto"
DEFAULT = {"enabled": False, "per_day": 2, "days_ahead": 2, "ratio": 3, "message": "", "enabled_at": None,
           "mode": "schedule"}   # schedule = 네이버 예약 발행 / now = 시간 칸이 오면 바로 발행
TICK_S = 10 * 60
STALE_MIN = 30

_jobs: "queue.Queue[tuple]" = queue.Queue()
busy = threading.Lock()          # 글쓰기·로그인이 겹치지 않게
state = {"current": None, "next_check": None, "recon": None,
         "batch": None}          # [시작] 진행: {"items": [...], "done": n, "stopped": bool}


def get() -> dict:
    s = dict(DEFAULT)
    s.update(db.setting(KEY, {}) or {})
    return s


def save(**patch) -> dict:
    s = get()
    if patch.get("enabled") and not s["enabled"]:
        patch["enabled_at"] = db.now()          # 실패 횟수는 켠 뒤부터만 셈
    s.update(patch)
    s["per_day"] = min(max(int(s["per_day"]), 1), 3)
    s["days_ahead"] = min(max(int(s["days_ahead"]), 1), 7)
    s["ratio"] = min(max(int(s["ratio"]), 1), 10)
    s["mode"] = "now" if s.get("mode") == "now" else "schedule"
    db.set_setting(KEY, s)
    return s


def _msg(text: str):
    save(message=f"{text} ({dt.datetime.now():%m/%d %H:%M})")


# ─────────────────────────────────────────────
# 무엇을 쓸지
# ─────────────────────────────────────────────
def _infos_since_shop(account: int) -> int:
    last_shop = db.one("SELECT id FROM posts WHERE kind='shop' AND status IN ('SCHEDULED','PUBLISHED') AND account=? "
                       "ORDER BY id DESC LIMIT 1", (account,))
    return db.one("SELECT COUNT(*) n FROM posts WHERE kind='info' AND status IN ('SCHEDULED','PUBLISHED') "
                  "AND account=? AND id > ?", (account, last_shop["id"] if last_shop else 0))["n"]


def waiting_link(account: int):
    row = db.one("SELECT id FROM links WHERE status='WAITING' AND account=? ORDER BY id LIMIT 1", (account,))
    return row["id"] if row else None


def next_kind(ratio: int, account: int = 1) -> tuple[str, int | None]:
    """이 계정에서 마지막 쇼핑글 이후 정보글이 ratio편 이상이고 대기 링크가 있으면 쇼핑글."""
    link = waiting_link(account)
    if _infos_since_shop(account) >= ratio and link:
        return "shop", link
    return "info", None


def counts(account: int = 1) -> dict:
    s = get()
    up = db.one("SELECT COUNT(*) n FROM posts WHERE status='SCHEDULED' AND scheduled_at > ? AND account=?",
                (db.now(), account))["n"]
    return {"account": account, "upcoming": up, "target": s["per_day"] * s["days_ahead"], "mode": s["mode"],
            "today": len(_today_done(account)), "per_day": s["per_day"],
            "info_since_shop": _infos_since_shop(account), "ratio": s["ratio"],
            "links_waiting": db.one("SELECT COUNT(*) n FROM links WHERE status='WAITING' AND account=?",
                                    (account,))["n"]}


# ── 로그인이 풀린 계정은 다시 로그인할 때까지 건너뜀 (다른 계정은 계속) ──
def _session_mtime(account: int) -> float:
    f = accounts.session_file(account)
    return f.stat().st_mtime if f.exists() else 0.0


def login_bad(account: int) -> bool:
    bad = (db.setting("login_bad", {}) or {}).get(str(account))
    return bad is not None and bad == _session_mtime(account)


def _mark_login_bad(account: int):
    bad = db.setting("login_bad", {}) or {}
    bad[str(account)] = _session_mtime(account)
    db.set_setting("login_bad", bad)


def usable_accounts() -> list[int]:
    return [a["id"] for a in accounts.active() if a["logged_in"] and not login_bad(a["id"])]


# ─────────────────────────────────────────────
# 글 한 편 실행 (일꾼 스레드 안)
# ─────────────────────────────────────────────
def _today_done(account: int = 1) -> list:
    """오늘 공개(예정 포함)되는 이 계정 완료 글의 시각들."""
    today = dt.date.today().isoformat()
    return [dt.datetime.fromisoformat(r["scheduled_at"]) for r in db.q(
        "SELECT scheduled_at FROM posts WHERE status IN ('SCHEDULED','PUBLISHED') AND substr(scheduled_at,1,10)=? "
        "AND account=?", (today, account)) if r["scheduled_at"]]


def _run(kind: str, link_id: int | None, auto: bool, topic: str | None = None, mode: str | None = None,
         account: int = 1):
    s = get()
    mode = mode or s["mode"]
    pid = db.new_post(kind, topic=topic, link_id=link_id, auto=auto, account=account)
    if link_id:
        db.run("UPDATE links SET status='USED', used_post=? WHERE id=?", (pid, link_id))   # 쓰는 동안 다시 안 고르게
    multi = len(accounts.all()) > 1
    state["current"] = (f"[{accounts.get(account)['label']}] " if multi else "") + \
        f"{'쇼핑글' if kind == 'shop' else '정보글'} 작성 중"
    try:
        if kind == "shop":
            writer.write_shop(pid, link_id, s["per_day"], mode, account)
        else:
            writer.write_info(pid, s["per_day"], mode, account)
    except ai.OfflineError as e:
        db.update_post(pid, status="FAILED", error=f"[인터넷] {e}")
        if link_id:
            db.run("UPDATE links SET status='WAITING', used_post=NULL WHERE id=?", (link_id,))
        log(f"📴 인터넷 문제로 이번 글은 건너뜀: {e}")
    except Exception as e:  # noqa: BLE001
        msg = str(e) or e.__class__.__name__
        offline = not ai.online()
        db.update_post(pid, status="FAILED", error=("[인터넷] " if offline else "") + msg)
        if link_id:
            db.run("UPDATE links SET status=? WHERE id=?", ("WAITING" if offline else "FAILED", link_id))
        log(f"❌ 글쓰기 실패: {msg}")
        if isinstance(e, naver.LoginExpired):
            _mark_login_bad(account)
            if not usable_accounts():
                save(enabled=False, message="네이버 로그인이 풀려서 자동이 꺼졌어요. 설정 › 블로그 계정에서 다시 로그인한 뒤 켜주세요.")
    finally:
        state["current"] = None
        row = db.one("SELECT status FROM posts WHERE id=?", (pid,))
        if auto and row and row["status"] == "SCHEDULED":
            _wake.set()                   # 더 채울 게 있으면 10분 기다리지 않고 바로 다음 편


def _worker():
    while True:
        job = _jobs.get()
        with busy:
            try:
                job[0](*job[1:])
            except Exception as e:  # noqa: BLE001
                log(f"❌ 작업 오류: {e}")


def request(kind: str | None = None, topic: str | None = None, mode: str | None = None, account: int = 1,
            link_id: int | None = None) -> str:
    """대시보드 '지금 1편 쓰기'. kind=None 이면 비율대로."""
    if busy.locked() or not _jobs.empty():
        return "이미 작업 중이에요. 끝나면 다시 눌러주세요."
    link_id = None
    if kind in (None, "auto"):
        kind, link_id = next_kind(get()["ratio"], account)
    elif kind == "shop":
        link_id = link_id or waiting_link(account)
        if not link_id:
            return f"[{accounts.get(account)['label']}] 쇼핑 링크 대기열이 비어 있어요. 링크를 먼저 넣어주세요."
    mode = mode if mode in ("schedule", "now") else get()["mode"]
    _jobs.put((_run, kind, link_id, False, topic, mode, account))
    how = "바로 발행" if mode == "now" else "예약 발행"
    return f"{'쇼핑글' if kind == 'shop' else '정보글'} 작성을 시작했어요 ({how}). 로봇 브라우저는 닫지 말아주세요."


def run_trends() -> str:
    def job():
        state["current"] = "인기 키워드 모으는 중"
        try:
            trends.collect()
        except Exception as e:  # noqa: BLE001
            trends.save(error=str(e), tried_at=dt.datetime.now().isoformat(timespec="minutes"))
            log(f"⚠️ 인기 키워드 읽기 오류: {e}")
        finally:
            state["current"] = None
    if busy.locked() or not _jobs.empty():
        return "다른 작업 중이에요. 끝나면 다시 눌러주세요."
    _jobs.put((job,))
    return "인기 키워드를 모으고 있어요 (1분쯤)."


def run_recommend(keyword: str | None = None) -> str:
    def job():
        state["current"] = "쇼핑커넥트 추천 상품 고르는 중"
        try:
            shopconnect.recommend(keyword)
        except Exception as e:  # noqa: BLE001
            shopconnect.save(message=f"추천 상품 고르기 실패: {e}")
            log(f"⚠️ 추천 상품 고르기 실패: {e}")
        finally:
            shopconnect.save(tried=dt.date.today().isoformat())
            state["current"] = None
    if busy.locked() or not _jobs.empty():
        return "다른 작업 중이에요. 끝나면 다시 눌러주세요."
    _jobs.put((job,))
    return "추천 상품을 고르고 있어요 (1~3분). 상품 정보만 읽고 아무것도 발급하지 않아요."


def run_recon(keyword: str) -> str:
    def job():
        state["current"] = "쇼핑커넥트 화면 살펴보는 중"
        try:
            state["recon"] = naver.recon_shopping_connect(keyword)
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ 쇼핑커넥트 화면 살펴보기 실패: {e}")
        finally:
            state["current"] = None
    if busy.locked() or not _jobs.empty():
        return "다른 작업 중이에요. 끝나면 다시 눌러주세요."
    _jobs.put((job,))
    return "로봇 브라우저로 쇼핑커넥트 화면을 살펴볼게요. 검색까지만 하고 아무것도 발급하지 않아요."


def run_editor_check(account: int = 1) -> str:
    def job():
        state["current"] = "글쓰기 화면 점검 중"
        w = None
        try:
            w = naver.Writer(account)
            res = w.selfcheck()
            bad = [n for n, ok in res if not ok]
            state["editorcheck"] = {"at": dt.datetime.now().isoformat(timespec="minutes"), "account": account,
                                    "items": [{"name": n, "ok": ok} for n, ok in res]}
            log("🔍 글쓰기 화면 점검: " + (", ".join(f"{n} {'✅' if ok else '❌'}" for n, ok in res)))
            if bad:
                log("   ⚠️ 바뀐 것 같아요: " + ", ".join(bad) + " — 이 기록을 Claude에게 보여주면 고쳐드려요.")
        except Exception as e:  # noqa: BLE001
            state["editorcheck"] = {"at": dt.datetime.now().isoformat(timespec="minutes"), "account": account,
                                    "items": [], "error": str(e)}
            log(f"⚠️ 글쓰기 화면 점검 실패: {e}")
        finally:
            try:
                if w:
                    w.close()
            except Exception:  # noqa: BLE001
                pass
            state["current"] = None
    if busy.locked() or not _jobs.empty():
        return "다른 작업 중이에요. 끝나면 다시 눌러주세요."
    _jobs.put((job,))
    return "글쓰기 화면을 열어 점검할게요 (글은 쓰지 않아요, 1분 정도)."


def run_comments(force: bool = False) -> str:
    """댓글 자동 한 번 (내 글 답글 + 이웃 댓글). 글쓰기와 같은 일꾼 스레드라 겹치지 않음."""
    def job():
        state["current"] = "댓글 확인 중"
        try:
            comments.run(usable_accounts(), on_login_bad=_mark_login_bad, force=force)
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ 댓글 확인 오류: {e}")
        finally:
            state["current"] = None
    if busy.locked() or not _jobs.empty():
        return "다른 작업 중이에요. 끝나면 다시 눌러주세요."
    _jobs.put((job,))
    return "댓글을 확인하고 있어요. 로봇 브라우저는 닫지 말아주세요."


def run_login(account: int = 1):
    def job():
        state["current"] = f"[{accounts.get(account)['label']}] 네이버 로그인 창 열림"
        try:
            naver.login(account=account)
        finally:
            state["current"] = None
    if busy.locked() or not _jobs.empty():
        return "다른 작업 중이에요. 끝나면 다시 눌러주세요."
    _jobs.put((job,))
    return "로그인 창을 열었어요. 열린 창에서 네이버에 로그인해 주세요."


# ─────────────────────────────────────────────
# [시작] 한 세트 — 켜진 계정마다 정보글 3편 + 쇼핑글 1편 (모두 예약 발행)
# ─────────────────────────────────────────────
SET_PLAN = ["info", "info", "info", "shop"]


def start_batch() -> str:
    if busy.locked() or not _jobs.empty():
        return "다른 작업 중이에요. 끝나면 다시 눌러주세요."
    accs = usable_accounts()
    if not accs:
        return "쓸 수 있는 계정이 없어요. 설정 › 블로그 계정에서 블로그 아이디와 로그인을 확인해 주세요."
    items = [{"account": a, "kind": k, "status": "대기"} for a in accs for k in SET_PLAN]
    state["batch"] = {"items": items, "done": 0, "stopped": False, "started": dt.datetime.now().isoformat(timespec="minutes")}
    for i in range(len(items)):
        _jobs.put((_batch_step, i))
    names = " → ".join(accounts.get(a)["label"] for a in accs)
    log(f"▶️ 시작: {names} — 계정마다 정보글 3편 + 쇼핑글 1편 (예약 발행)")
    return f"시작했어요. {names} 순서로 정보글 3편 + 쇼핑글 1편씩 써서 예약해요."


def stop_batch() -> str:
    b = state.get("batch")
    if not b or b.get("finished"):
        return "진행 중인 작업이 없어요."
    b["stopped"] = True
    log("⏹️ 멈춤 — 지금 쓰는 글까지만 마치고 멈춰요")
    return "지금 쓰는 글까지만 마치고 멈출게요."


def _batch_step(i: int):
    b = state.get("batch")
    if not b:
        return
    it = b["items"][i]
    if b.get("stopped"):
        it["status"] = "멈춤"
    elif it["kind"] == "shop":
        link = waiting_link(it["account"])
        if not link:
            it["status"] = "링크 없음"
            log(f"   🛒 [{accounts.get(it['account'])['label']}] 쇼핑 링크가 없어 쇼핑글은 건너뛰어요")
        else:
            it["status"] = "작성 중"
            _run("shop", link, False, None, "schedule", it["account"])
            it["status"] = _last_status(it["account"])
    else:
        it["status"] = "작성 중"
        _run("info", None, False, None, "schedule", it["account"])
        it["status"] = _last_status(it["account"])
    b["done"] = i + 1
    if i + 1 == len(b["items"]):
        b["finished"] = dt.datetime.now().isoformat(timespec="minutes")
        ok = sum(1 for x in b["items"] if x["status"] == "완료")
        log(f"✅ 한 세트 끝: {ok}/{len(b['items'])}편 예약 완료")


def _last_status(account: int) -> str:
    r = db.one("SELECT status FROM posts WHERE account=? ORDER BY id DESC LIMIT 1", (account,))
    return {"SCHEDULED": "완료", "PUBLISHED": "완료", "FAILED": "실패"}.get(r["status"] if r else "", "?")


# ─────────────────────────────────────────────
# 10분마다 확인
# ─────────────────────────────────────────────
def tick() -> str:
    s = get()
    now = dt.datetime.now()
    # 1) 멈춘 작업 정리
    stale = (now - dt.timedelta(minutes=STALE_MIN)).isoformat(timespec="seconds")
    if not busy.locked():
        db.run("UPDATE posts SET status='FAILED', error='작성이 30분 넘게 끝나지 않아 멈춘 것으로 처리했어요.', "
               "updated_at=? WHERE status='WRITING' AND updated_at < ?", (db.now(), stale))
    if not s["enabled"]:
        return "꺼져 있음"
    if busy.locked() or not _jobs.empty():
        return "작성 중"
    # 2) 인터넷
    if not ai.online():
        _msg("인터넷 연결 안 됨 — 연결되면 이어서 써요")
        return "오프라인"
    # 3) 켠 뒤 연속 3번 실패 → 꺼짐 (인터넷 실패는 안 셈)
    rows = db.q("SELECT status, error FROM posts WHERE auto=1 AND status IN ('FAILED','SCHEDULED','PUBLISHED') "
                "AND created_at >= ? ORDER BY id DESC LIMIT 10", (s.get("enabled_at") or "",))
    rows = [r for r in rows if not (r["status"] == "FAILED" and (r["error"] or "").startswith("[인터넷]"))][:3]
    if len(rows) == 3 and all(r["status"] == "FAILED" for r in rows):
        save(enabled=False, message=f"연속 3번 실패해서 자동으로 꺼졌어요: {rows[0]['error']}")
        log("⛔ 연속 3번 실패 → 자동 꺼짐")
        return "연속 실패로 꺼짐"
    accs = usable_accounts()
    if not accs:
        _msg("쓸 수 있는 계정이 없어요 (블로그 아이디·로그인 확인)")
        return "계정 없음"
    if s["mode"] == "now":
        return _tick_now(s, now, accs)
    # 4) 예약 채우기 — 1번 → 2번 → 3번 순서로, 모자란 계정부터
    multi = len(accounts.all()) > 1
    for a in accs:
        c = counts(a)
        if c["upcoming"] >= c["target"]:
            continue
        kind, link_id = next_kind(s["ratio"], a)
        who = f"[{accounts.get(a)['label']}] " if multi else ""
        _msg(f"{who}예약 {c['upcoming']}/{c['target']}개 — {'쇼핑글' if kind == 'shop' else '정보글'} 작성 시작")
        _jobs.put((_run, kind, link_id, True, None, None, a))
        return "작성 시작"
    _msg("모든 계정 예약이 충분해요" if multi else f"예약된 글 {counts(accs[0])['upcoming']}개 — 충분해요")
    return "충분함"


def _tick_now(s: dict, now: dt.datetime, accs: list) -> str:
    """즉시 발행 모드: 계정마다, 하루 시간 칸(오전·오후·저녁)의 정해진 시각이 되면 써서 바로 발행."""
    last = "충분함"
    for a in accs:
        last = _tick_now_one(s, now, a)
        if last == "작성 시작":
            return last
    return last


def _tick_now_one(s: dict, now: dt.datetime, account: int) -> str:
    done = _today_done(account)
    who = f"[{accounts.get(account)['label']}] " if len(accounts.all()) > 1 else ""
    if len(done) >= s["per_day"]:
        _msg(f"{who}오늘 {len(done)}편 발행 완료 — 내일 이어서 써요")
        return "충분함"
    for i, (h1, h2) in enumerate(writer.SLOT_WINDOWS):
        ws, we = now.replace(hour=h1, minute=0, second=0, microsecond=0), now.replace(hour=h2, minute=0, second=0, microsecond=0)
        if not (ws <= now < we):
            continue
        if any(ws <= t < we for t in done):
            _msg(f"{who}이 시간대 글은 이미 발행했어요 — 다음 시간대에 써요")
            return "충분함"
        # 매일 조금씩 다른 시각에 시작 (기계처럼 보이지 않게), 끝나기 40분 전까지는 시작
        span = max(1, int((we - ws).total_seconds() // 60) - 40)
        start = ws + dt.timedelta(minutes=(zlib.crc32(f"{now.date()}-{i}-{account}".encode()) % span))
        if now < start:
            _msg(f"{who}오늘 {len(done)}/{s['per_day']}편 — {start:%H:%M} 쯤 바로 발행할 글을 써요")
            return "충분함"
        kind, link_id = next_kind(s["ratio"], account)
        _msg(f"{who}오늘 {len(done)}/{s['per_day']}편 — {'쇼핑글' if kind == 'shop' else '정보글'} 작성 후 바로 발행")
        _jobs.put((_run, kind, link_id, True, None, "now", account))
        return "작성 시작"
    nxt = next((f"{h1}시" for h1, _ in writer.SLOT_WINDOWS if now.hour < h1), "내일 9시")
    _msg(f"{who}오늘 {len(done)}/{s['per_day']}편 — 다음 발행 시간대({nxt})를 기다려요")
    return "충분함"


def config_ok() -> bool:
    a = accounts.get(1)
    return bool(a["sc_base"]) and a["logged_in"]


def _loop():
    time.sleep(30)                        # 켜고 30초 뒤 첫 확인
    while True:
        try:
            if trends.due() and not busy.locked() and _jobs.empty():
                run_trends()               # 하루 한 번 인기 키워드
                time.sleep(5)
            elif shopconnect.due() and config_ok() and not busy.locked() and _jobs.empty():
                run_recommend()            # 하루 한 번 추천 상품 (인기 키워드 모은 뒤)
                time.sleep(5)
        except Exception as e:  # noqa: BLE001
            log(f"[자동] 인기 키워드 예약 오류: {e}")
        try:
            r = tick()
            if r not in ("꺼져 있음", "충분함"):
                log(f"[자동] {r}")
        except Exception as e:  # noqa: BLE001
            log(f"[자동] 확인 중 오류: {e}")
        try:
            if comments.due() and not busy.locked() and _jobs.empty() and usable_accounts():
                run_comments()             # 켜져 있으면 1시간쯤마다 (9~22시)
        except Exception as e:  # noqa: BLE001
            log(f"💬 댓글 예약 오류: {e}")
        try:
            style.check()                  # 6시간마다: 공개된 글과 초안 비교 → 말투 메모 갱신
        except Exception as e:  # noqa: BLE001
            log(f"🗣️ 말투 학습 중 오류: {e}")
        state["next_check"] = (dt.datetime.now() + dt.timedelta(seconds=TICK_S)).strftime("%H:%M")
        # 작성이 끝나면 바로 다음 확인 (여러 편 모자랄 때 빨리 채우기)
        for _ in range(TICK_S // 5):
            time.sleep(5)
            if _wake.is_set():
                _wake.clear()
                break


_wake = threading.Event()


def start():
    threading.Thread(target=_worker, daemon=True, name="worker").start()
    threading.Thread(target=_loop, daemon=True, name="auto").start()
    log("⏱️ 자동 확인을 시작했어요 (10분마다, 대시보드에서 켜야 동작)")
