"""글 한 편 쓰기 — 정보글(info) / 쇼핑커넥트글(shop).

흐름: (주제·상품) → AI 글 → 손그림 → 예약 칸 고르기 → 네이버 에디터 입력 → 예약 발행 → 확인
브라우저는 글 한 편마다 열고, 끝나면 닫는다.
"""
from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

from . import ai, config, db, illustrations, prompts, style, text, trends
from .log import log
from .naver import StopError, Writer, fmt

SLOT_WINDOWS = [(9, 11), (13, 16), (19, 22)]   # 오전 / 오후 / 저녁


# ─────────────────────────────────────────────
# 예약 시간 고르기 (지금부터 최소 1시간 뒤, 하루 per_day개, 칸마다 1개, 10분 단위)
# ─────────────────────────────────────────────
def pick_time(per_day: int, exclude_id: int | None = None, now: dt.datetime | None = None) -> dt.datetime:
    now = now or dt.datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.q("SELECT scheduled_at FROM posts WHERE status IN ('SCHEDULED','PUBLISHED') "
                "AND scheduled_at >= ? AND id != ?", (today.isoformat(), exclude_id or -1))
    times = [dt.datetime.fromisoformat(r["scheduled_at"]) for r in rows if r["scheduled_at"]]
    per_day = max(1, min(per_day, len(SLOT_WINDOWS)))
    for d in range(30):
        day = today + dt.timedelta(days=d)
        day_times = [t for t in times if t.date() == day.date()]
        if len(day_times) >= per_day:
            continue
        for h1, h2 in SLOT_WINDOWS:
            ws, we = day.replace(hour=h1), day.replace(hour=h2)
            if any(ws <= t < we for t in day_times):
                continue
            earliest = max(ws, now + dt.timedelta(hours=1))
            latest = we - dt.timedelta(minutes=10)
            if earliest >= latest:
                continue
            slot = earliest + (latest - earliest) * random.random()
            slot = slot.replace(second=0, microsecond=0)
            add = (10 - slot.minute % 10) % 10       # 네이버는 10분 단위 (올림)
            return slot + dt.timedelta(minutes=add)
    raise StopError("앞으로 30일 안에 비어 있는 예약 칸이 없어요.")


def _cleanup(paths):
    for p in paths:
        try:
            Path(p).unlink()
        except Exception:
            pass


def _finish(w: Writer, pid: int, frame, at: dt.datetime | None, title: str):
    """at 이 있으면 예약 발행, 없으면 즉시 발행."""
    w.publish(frame, at)
    verdict, url, note = w.confirm(at is not None)
    if verdict == "fail":
        raise StopError(note or "발행 버튼은 눌렀지만 완료되지 않았어요. 열린 창을 확인해 주세요.")
    when = at or dt.datetime.now()
    db.update_post(pid, status="SCHEDULED" if at else "PUBLISHED", title=title, post_url=url,
                   scheduled_at=when.isoformat(timespec="minutes"), error=None, note=note)
    if at:
        log(f"⏰ 예약 완료! {fmt(at)}에 공개돼요 — {title}")
    else:
        log(f"🎉 발행 완료! — {title}" + (f" ({url})" if url else ""))
    if note:
        log(f"   ℹ️ {note}")


def _plan(pid: int, per_day: int, mode: str, title: str, sections: list) -> dt.datetime | None:
    """공개 시각 정하기 + 초안 저장 (실패해도 언제로 하려 했는지 남게)."""
    at = pick_time(per_day, pid) if mode == "schedule" else None
    db.update_post(pid, mode=mode, title=title, body=title + "\n\n" + "\n\n".join(sections),
                   scheduled_at=(at or dt.datetime.now()).isoformat(timespec="minutes"))
    log(f"   ⏰ 공개 예정: {fmt(at)}" if at else "   🚀 바로 발행해요")
    return at


# ─────────────────────────────────────────────
# 정보글
# ─────────────────────────────────────────────
def pick_topic() -> str:
    used = [r["title"] for r in db.q("SELECT title FROM posts WHERE title IS NOT NULL ORDER BY id DESC LIMIT 40")]
    prods = [r["product_name"] for r in db.q(
        "SELECT product_name FROM links WHERE product_name IS NOT NULL ORDER BY id DESC LIMIT 20")]
    s, u = prompts.topic(config.get("BLOG_TOPIC"), used, prods, trends.hints())
    t = str(ai.generate_json(s, u, lambda d: bool(str(d.get("topic", "")).strip())).get("topic", "")).strip()
    if not t:
        raise ai.AIError("AI가 정보글 주제를 고르지 못했어요.")
    return t


def write_info(pid: int, per_day: int, mode: str = "schedule"):
    row = db.one("SELECT * FROM posts WHERE id=?", (pid,))
    topic = (row or {}).get("topic") or pick_topic()
    db.update_post(pid, topic=topic)
    log(f"📝 정보글 시작 — 주제: {topic}")

    s, u = prompts.info(topic)
    s += style.block()          # 주인이 고친 글에서 배운 말투
    data = ai.generate_json(s, u, lambda d: isinstance(d.get("sections"), list) and len(d["sections"]) >= 5)
    title = text.clean_for_editor(str(data.get("title") or topic)).strip()
    sections = text.tidy(data["sections"])
    tags = text.tags(data.get("hashtags"))
    st = data.get("strategy") or {}
    if st:
        log(f"   🎯 독자: {st.get('reader', '-')} / 키워드: {st.get('mainKeyword', '-')}")
    db.update_post(pid, title=title)

    w = Writer()
    temp: list[str] = []
    try:
        v: dict = {}
        try:
            spec = illustrations.normalize(data.get("visual"), topic, title)
            v = illustrations.render(w.art, spec, config.TEMP, "info",
                                     int(config.get("IMAGE_WIDTH") or 480), int(config.get("THUMB_WIDTH") or 600), log)
        except Exception as e:  # noqa: BLE001
            log(f"   ⚠️ 그림을 건너뜀: {e}")
        temp += list(v.values())
        # 섹션: 도입, 결론부터, ①, ②, ③, 주의, 마무리  /  slots[i] = i번째 섹션 앞
        g = v.get
        slots = [[g("thumbnail")], [g("scene")], [g("checklist"), g("step1")], [g("step2")], [g("step3")],
                 [g("tip")], [g("faq"), g("fit")], [g("outro")]]
        at = _plan(pid, per_day, mode, title, sections)
        frame = w.open_editor()
        w.title(frame, title)
        w.body(frame, None, slots, sections, tags)
        _finish(w, pid, frame, at, title)
    finally:
        _cleanup(temp)
        w.close()


# ─────────────────────────────────────────────
# 쇼핑커넥트 글
# ─────────────────────────────────────────────
def write_shop(pid: int, link_id: int, per_day: int, mode: str = "schedule"):
    link = db.one("SELECT * FROM links WHERE id=?", (link_id,))
    if not link:
        raise StopError("링크를 찾을 수 없어요.")
    log(f"🛒 쇼핑글 시작 — {link['url']}")
    w = Writer()
    temp: list[str] = []
    try:
        p = w.product(link["url"])
        temp += p["image_paths"]
        if not p["name"]:
            raise StopError("상품 이름을 읽지 못했어요. 링크가 맞는지 확인해 주세요.")
        db.run("UPDATE links SET product_name=? WHERE id=?", (p["name"], link_id))
        db.update_post(pid, topic=p["name"])

        memo = link.get("memo") or ""
        s, u = prompts.shop(p, memo)
        s += style.block()
        data = ai.generate_json(s, u, lambda d: isinstance(d.get("sections"), list) and len(d["sections"]) >= 5)
        title = text.clean_for_editor(str(data.get("title") or p["name"])).strip()
        sections = text.tidy(data["sections"])
        if any("[구매링크]" in x for x in sections):
            sections = [x.replace("[구매링크]", link["url"]) for x in sections]
        else:
            sections[-1] += f"\n\n👇👇👇\n{link['url']}"
        tags = text.tags(data.get("hashtags"), None if memo.strip() else r"내돈내산|솔직후기|사용후기")
        db.update_post(pid, title=title)

        v: dict = {}
        photos = p["image_paths"]
        try:
            spec = illustrations.normalize(data.get("visual"), p["name"], title)
            v = illustrations.render(w.art, spec, config.TEMP, "shop",
                                     int(config.get("IMAGE_WIDTH") or 480), int(config.get("THUMB_WIDTH") or 600), log)
            photos = illustrations.resize_photos(w.art, photos, config.TEMP, int(config.get("IMAGE_WIDTH") or 480))
            temp += [x for x in photos if x not in temp]
        except Exception as e:  # noqa: BLE001
            log(f"   ⚠️ 그림을 건너뜀: {e}")
        temp += list(v.values())
        ph = lambda i: photos[i] if i < len(photos) else None  # noqa: E731
        g = v.get
        # 섹션: 도입, 제품정보, 결론, 장점①, 장점②, 체크, 아쉬운점, 추천, 가격 (+ 마무리 카드)
        slots = [[g("thumbnail") or ph(0)], [g("scene")], [ph(0)], [], [ph(1)], [ph(2)],
                 [g("checklist")], [ph(3)], [g("fit")], [g("outro")]]
        at = _plan(pid, per_day, mode, title, sections)
        frame = w.open_editor()
        w.title(frame, title)
        lead = config.get("FTC_DISCLOSURE").replace("\\n", "\n") + "\n"   # 공정위 문구는 글 맨 위
        w.body(frame, lead, slots, sections, tags)
        _finish(w, pid, frame, at, title)
        db.run("UPDATE links SET status='USED' WHERE id=?", (link_id,))
    finally:
        _cleanup(temp)
        w.close()
