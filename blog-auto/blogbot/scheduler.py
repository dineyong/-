"""자동 작성 — 프로그램이 켜져 있는 동안 10분마다 확인.

- 앞으로 예약된 글이 (하루 개수 × 며칠치)보다 적으면 1편 써서 예약
- 비율: 정보글 3편마다 쇼핑글 1편 (쇼핑 링크 대기열에 링크가 있을 때만, 없으면 정보글)
- 인터넷 끊김은 실패로 세지 않고 다음 확인 때 다시
- 켠 뒤로 연속 3번 실패하면 스스로 꺼짐 (로그인 풀림 등)
- 대시보드의 "지금 1편 쓰기"도 같은 일꾼 스레드가 처리 (동시에 두 편 안 씀)
"""
from __future__ import annotations

import datetime as dt
import queue
import threading
import time
import zlib

from . import ai, db, naver, style, writer
from .log import log

KEY = "auto"
DEFAULT = {"enabled": False, "per_day": 2, "days_ahead": 2, "ratio": 3, "message": "", "enabled_at": None,
           "mode": "schedule"}   # schedule = 네이버 예약 발행 / now = 시간 칸이 오면 바로 발행
TICK_S = 10 * 60
STALE_MIN = 30

_jobs: "queue.Queue[tuple]" = queue.Queue()
busy = threading.Lock()          # 글쓰기·로그인이 겹치지 않게
state = {"current": None, "next_check": None}


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
def next_kind(ratio: int) -> tuple[str, int | None]:
    """마지막 쇼핑글 이후 정보글이 ratio편 이상이고 대기 링크가 있으면 쇼핑글."""
    last_shop = db.one("SELECT id FROM posts WHERE kind='shop' AND status IN ('SCHEDULED','PUBLISHED') "
                       "ORDER BY id DESC LIMIT 1")
    infos = db.one("SELECT COUNT(*) n FROM posts WHERE kind='info' AND status IN ('SCHEDULED','PUBLISHED') AND id > ?",
                   (last_shop["id"] if last_shop else 0,))["n"]
    link = db.one("SELECT id FROM links WHERE status='WAITING' ORDER BY id LIMIT 1")
    if infos >= ratio and link:
        return "shop", link["id"]
    return "info", None


def counts() -> dict:
    s = get()
    last_shop = db.one("SELECT id FROM posts WHERE kind='shop' AND status IN ('SCHEDULED','PUBLISHED') "
                       "ORDER BY id DESC LIMIT 1")
    infos = db.one("SELECT COUNT(*) n FROM posts WHERE kind='info' AND status IN ('SCHEDULED','PUBLISHED') AND id > ?",
                   (last_shop["id"] if last_shop else 0,))["n"]
    up = db.one("SELECT COUNT(*) n FROM posts WHERE status='SCHEDULED' AND scheduled_at > ?", (db.now(),))["n"]
    return {"upcoming": up, "target": s["per_day"] * s["days_ahead"], "mode": s["mode"],
            "today": len(_today_done()), "per_day": s["per_day"],
            "info_since_shop": infos, "ratio": s["ratio"],
            "links_waiting": db.one("SELECT COUNT(*) n FROM links WHERE status='WAITING'")["n"]}


# ─────────────────────────────────────────────
# 글 한 편 실행 (일꾼 스레드 안)
# ─────────────────────────────────────────────
def _today_done() -> list:
    """오늘 공개(예정 포함)되는 완료 글의 시각들."""
    today = dt.date.today().isoformat()
    return [dt.datetime.fromisoformat(r["scheduled_at"]) for r in db.q(
        "SELECT scheduled_at FROM posts WHERE status IN ('SCHEDULED','PUBLISHED') AND substr(scheduled_at,1,10)=?",
        (today,)) if r["scheduled_at"]]


def _run(kind: str, link_id: int | None, auto: bool, topic: str | None = None, mode: str | None = None):
    s = get()
    mode = mode or s["mode"]
    pid = db.new_post(kind, topic=topic, link_id=link_id, auto=auto)
    if link_id:
        db.run("UPDATE links SET status='USED' WHERE id=?", (link_id,))   # 쓰는 동안 다시 안 고르게
    state["current"] = f"{'쇼핑글' if kind == 'shop' else '정보글'} 작성 중"
    try:
        if kind == "shop":
            writer.write_shop(pid, link_id, s["per_day"], mode)
        else:
            writer.write_info(pid, s["per_day"], mode)
    except ai.OfflineError as e:
        db.update_post(pid, status="FAILED", error=f"[인터넷] {e}")
        if link_id:
            db.run("UPDATE links SET status='WAITING' WHERE id=?", (link_id,))
        log(f"📴 인터넷 문제로 이번 글은 건너뜀: {e}")
    except Exception as e:  # noqa: BLE001
        msg = str(e) or e.__class__.__name__
        offline = not ai.online()
        db.update_post(pid, status="FAILED", error=("[인터넷] " if offline else "") + msg)
        if link_id:
            db.run("UPDATE links SET status=? WHERE id=?", ("WAITING" if offline else "FAILED", link_id))
        log(f"❌ 글쓰기 실패: {msg}")
        if isinstance(e, naver.LoginExpired):
            save(enabled=False, message="네이버 로그인이 풀려서 자동이 꺼졌어요. '네이버 로그인' 후 다시 켜주세요.")
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


def request(kind: str | None = None, topic: str | None = None, mode: str | None = None) -> str:
    """대시보드 '지금 1편 쓰기'. kind=None 이면 비율대로."""
    if busy.locked() or not _jobs.empty():
        return "이미 작업 중이에요. 끝나면 다시 눌러주세요."
    link_id = None
    if kind in (None, "auto"):
        kind, link_id = next_kind(get()["ratio"])
    elif kind == "shop":
        row = db.one("SELECT id FROM links WHERE status='WAITING' ORDER BY id LIMIT 1")
        if not row:
            return "쇼핑 링크 대기열이 비어 있어요. 링크를 먼저 넣어주세요."
        link_id = row["id"]
    mode = mode if mode in ("schedule", "now") else get()["mode"]
    _jobs.put((_run, kind, link_id, False, topic, mode))
    how = "바로 발행" if mode == "now" else "예약 발행"
    return f"{'쇼핑글' if kind == 'shop' else '정보글'} 작성을 시작했어요 ({how}). 로봇 브라우저는 닫지 말아주세요."


def run_login():
    def job():
        state["current"] = "네이버 로그인 창 열림"
        try:
            naver.login()
        finally:
            state["current"] = None
    if busy.locked() or not _jobs.empty():
        return "다른 작업 중이에요. 끝나면 다시 눌러주세요."
    _jobs.put((job,))
    return "로그인 창을 열었어요. 열린 창에서 네이버에 로그인해 주세요."


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
    if s["mode"] == "now":
        return _tick_now(s, now)
    # 4) 예약 채우기
    c = counts()
    if c["upcoming"] >= c["target"]:
        _msg(f"예약된 글 {c['upcoming']}개 — 충분해요")
        return "충분함"
    kind, link_id = next_kind(s["ratio"])
    _msg(f"예약 {c['upcoming']}/{c['target']}개 — {'쇼핑글' if kind == 'shop' else '정보글'} 작성 시작")
    _jobs.put((_run, kind, link_id, True, None))
    return "작성 시작"


def _tick_now(s: dict, now: dt.datetime) -> str:
    """즉시 발행 모드: 하루 시간 칸(오전·오후·저녁)마다 그 칸 안의 정해진 시각이 되면 써서 바로 발행."""
    done = _today_done()
    if len(done) >= s["per_day"]:
        _msg(f"오늘 {len(done)}편 발행 완료 — 내일 이어서 써요")
        return "충분함"
    for i, (h1, h2) in enumerate(writer.SLOT_WINDOWS):
        ws, we = now.replace(hour=h1, minute=0, second=0, microsecond=0), now.replace(hour=h2, minute=0, second=0, microsecond=0)
        if not (ws <= now < we):
            continue
        if any(ws <= t < we for t in done):
            _msg("이 시간대 글은 이미 발행했어요 — 다음 시간대에 써요")
            return "충분함"
        # 매일 조금씩 다른 시각에 시작 (기계처럼 보이지 않게), 끝나기 40분 전까지는 시작
        span = max(1, int((we - ws).total_seconds() // 60) - 40)
        start = ws + dt.timedelta(minutes=(zlib.crc32(f"{now.date()}-{i}".encode()) % span))
        if now < start:
            _msg(f"오늘 {len(done)}/{s['per_day']}편 — {start:%H:%M} 쯤 바로 발행할 글을 써요")
            return "충분함"
        kind, link_id = next_kind(s["ratio"])
        _msg(f"오늘 {len(done)}/{s['per_day']}편 — {'쇼핑글' if kind == 'shop' else '정보글'} 작성 후 바로 발행")
        _jobs.put((_run, kind, link_id, True, None, "now"))
        return "작성 시작"
    nxt = next((f"{h1}시" for h1, _ in writer.SLOT_WINDOWS if now.hour < h1), "내일 9시")
    _msg(f"오늘 {len(done)}/{s['per_day']}편 — 다음 발행 시간대({nxt})를 기다려요")
    return "충분함"


def _loop():
    time.sleep(30)                        # 켜고 30초 뒤 첫 확인
    while True:
        try:
            r = tick()
            if r not in ("꺼져 있음", "충분함"):
                log(f"[자동] {r}")
        except Exception as e:  # noqa: BLE001
            log(f"[자동] 확인 중 오류: {e}")
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
