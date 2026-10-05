"""말투 자동 학습 — 사용자가 예시를 따로 가져오지 않아도 되게.

1. 블로그 RSS(공개 글 목록)에서 이 프로그램이 쓴 글을 찾는다 (공개 시각·제목으로 짝 맞추기)
2. 실제 공개된 글(사용자가 폰으로 고친 최종본)을 읽어 AI 초안과 비교
3. 고친 부분이 있으면 AI가 "이 블로그 주인이 고치는 방식"을 말투 메모로 정리 (기존 메모와 합침)
4. 말투 메모는 다음 글부터 프롬프트에 들어감. 대시보드에서 직접 고칠 수도 있음
"""
from __future__ import annotations

import datetime as dt
import difflib
import email.utils
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

from . import ai, config, db
from .log import log

KEY = "style"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
WAIT_HOURS = 6            # 공개 후 이만큼 지난 글만 비교 (사용자가 고칠 시간)
CHECK_EVERY_H = 6


def get() -> dict:
    s = {"notes": "", "learned": 0, "edited": 0, "updated_at": None, "last_check": None, "last_result": ""}
    s.update(db.setting(KEY, {}) or {})
    return s


def save(**patch) -> dict:
    s = get()
    s.update(patch)
    db.set_setting(KEY, s)
    return s


def block() -> str:
    """프롬프트에 붙일 말투 메모."""
    notes = (get().get("notes") or "").strip()
    if not notes:
        return ""
    return ("\n[이 블로그 주인의 말투 — 주인이 직접 고친 글에서 배운 것. 다른 규칙보다 우선]\n" + notes + "\n")


# ─────────────────────────────────────────────
# 블로그 읽기
# ─────────────────────────────────────────────
def _get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout, context=config.SSL) as r:
        raw = r.read()
        cs = r.headers.get_content_charset() or "utf-8"
    return raw.decode(cs, "replace")


def rss(blog_id: str) -> list[dict]:
    root = ET.fromstring(_get(f"https://rss.blog.naver.com/{blog_id}.xml").encode("utf-8"))
    out = []
    for it in root.iter("item"):
        def t(tag):
            el = it.find(tag)
            return (el.text or "").strip() if el is not None else ""
        try:
            pub = email.utils.parsedate_to_datetime(t("pubDate"))
            pub = pub.astimezone().replace(tzinfo=None) if pub.tzinfo else pub
        except (TypeError, ValueError):
            pub = None
        out.append({"title": html.unescape(t("title")), "link": t("link"), "pub": pub,
                    "desc": _strip(html.unescape(t("description")))})
    return out


class _Para(HTMLParser):
    """네이버 에디터 본문(se-text-paragraph)의 글만 줄 단위로 모음."""

    def __init__(self):
        super().__init__()
        self.depth = 0          # se-text-paragraph 안 깊이
        self.buf: list[str] = []
        self.lines: list[str] = []

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class") or ""
        if self.depth:
            self.depth += 1
            if tag == "br":
                self.buf.append("\n")
        elif "se-text-paragraph" in cls:
            self.depth = 1

    def handle_startendtag(self, tag, attrs):
        if self.depth and tag == "br":
            self.buf.append("\n")

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1
            if self.depth == 0:
                self.lines.append("".join(self.buf).replace("​", "").strip())
                self.buf = []

    def handle_data(self, data):
        if self.depth:
            self.buf.append(data)


def _strip(s: str) -> str:
    s = re.sub(r"<br\s*/?>|</p>", "\n", s or "", flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"<[^>]+>", "", s)).strip()


def post_text(blog_id: str, link: str, fallback: str) -> str:
    m = re.search(r"/(\d{6,})", link) or re.search(r"logNo=(\d+)", link)
    if m:
        try:
            p = _Para()
            p.feed(_get(f"https://m.blog.naver.com/PostView.naver?blogId={blog_id}&logNo={m.group(1)}"))
            text = "\n".join(p.lines).strip()
            if len(text) > 200:
                return text
        except Exception as e:  # noqa: BLE001
            log(f"   ⚠️ 글 본문 읽기 실패({m.group(1)}): {e}")
    return fallback


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


# ─────────────────────────────────────────────
# 학습
# ─────────────────────────────────────────────
def _diff(draft: str, final: str, limit: int = 120) -> str:
    a = [x.strip() for x in draft.splitlines() if x.strip()]      # 빈 줄 차이는 무시
    b = [x.strip() for x in final.splitlines() if x.strip()]
    d = difflib.unified_diff(a, b, "AI초안", "주인최종본", n=1, lineterm="")
    lines = [x for x in d if not x.startswith(("---", "+++"))]
    return "\n".join(lines[:limit])


SYSTEM = ("당신은 블로그 글쓰기 코치입니다. AI가 쓴 초안과, 블로그 주인이 직접 고쳐서 공개한 최종본의 차이를 보고 "
          "주인이 원하는 말투·표현·구성 습관을 찾아 '다음 글을 쓸 AI에게 주는 규칙'으로 정리합니다.")


def _learn(pairs: list[dict], old_notes: str) -> str:
    blocks = []
    for i, p in enumerate(pairs[:5], 1):
        blocks.append(f"### 글 {i}: {p['title']}\n(- 줄은 주인이 지운/바꾼 초안, + 줄은 주인이 쓴 최종본)\n{p['diff']}")
    user = f"""기존 말투 메모 (있으면 유지·보완):
{old_notes or '(없음)'}

주인이 고친 내용:
{chr(10).join(blocks)}

조건:
- 실제로 고친 부분에서 반복되는 경향만 규칙으로 (한 번만 고친 사소한 오타는 무시)
- "~요체 유지" 같은 뻔한 말 대신 구체적으로 (예: "문장 끝 '~더라구요'를 '~더라고요'로", "소제목 이모지는 빼기", "도입은 2문단 이내")
- 공정위 문구·구매 링크·해시태그·사진 위치 차이는 무시
- 글 주제·상품에 묶인 내용은 빼고, 다른 글에도 쓸 수 있는 말투·형식 규칙만
- 기존 메모와 겹치면 합치고, 새 수정과 반대되는 기존 규칙은 새 쪽으로 바꾸기
- 최대 12줄, 각 줄은 "- "로 시작

JSON만 출력: {{"notes": "- ...\\n- ..."}}"""
    data = ai.generate_json(SYSTEM, user, lambda d: bool(str(d.get("notes", "")).strip()))
    notes = str(data["notes"]).strip()
    return "\n".join(x for x in notes.splitlines() if x.strip())[:1500]


def check(force: bool = False) -> str:
    s = get()
    now = dt.datetime.now()
    if not force and s.get("last_check"):
        try:
            if now - dt.datetime.fromisoformat(s["last_check"]) < dt.timedelta(hours=CHECK_EVERY_H):
                return ""
        except ValueError:
            pass
    blog_id = config.get("NAVER_BLOG_ID").strip()
    if not blog_id:
        return ""
    cutoff = (now - dt.timedelta(hours=WAIT_HOURS)).isoformat(timespec="minutes")
    mine = db.q("SELECT id, title, body, scheduled_at FROM posts WHERE status IN ('SCHEDULED','PUBLISHED') "
                "AND body IS NOT NULL AND learned IS NULL AND scheduled_at <= ? ORDER BY id DESC LIMIT 20", (cutoff,))
    save(last_check=now.isoformat(timespec="minutes"))
    if not mine:
        save(last_result="비교할 새 글이 아직 없어요 (공개 후 6시간 지난 글부터 비교해요)")
        return "새 글 없음"
    try:
        items = rss(blog_id)
    except Exception as e:  # noqa: BLE001
        save(last_result=f"블로그 글 목록을 못 읽었어요: {e}")
        log(f"🗣️ 말투 학습: 블로그 글 목록 읽기 실패 — {e}")
        return "실패"

    pairs, matched = [], 0
    for p in mine:
        at = dt.datetime.fromisoformat(p["scheduled_at"])
        item = next((it for it in items if _norm(it["title"]) == _norm(p["title"])), None) or next(
            (it for it in items if it["pub"] and abs((it["pub"] - at).total_seconds()) <= 600), None)
        if not item:
            if at < now - dt.timedelta(days=3):          # 3일 지나도 없으면 (삭제·비공개 등) 포기
                db.update_post(p["id"], learned=0)
            continue
        matched += 1
        final = post_text(blog_id, item["link"], item["desc"])
        draft = p["body"]
        # 초안 제목 줄은 빼고 본문끼리 비교
        draft_body = draft.split("\n\n", 1)[1] if "\n\n" in draft else draft
        ratio = difflib.SequenceMatcher(None, _norm(draft_body), _norm(final)).ratio()
        db.update_post(p["id"], learned=1)
        if ratio < 0.97 and len(final) > 200:
            pairs.append({"title": item["title"], "diff": _diff(draft_body, final), "ratio": ratio})
    n_learned = s.get("learned", 0) + matched
    if not pairs:
        msg = f"글 {matched}편 비교 — 고친 곳이 없어서 말투 메모는 그대로예요" if matched else "아직 공개된 글을 못 찾았어요"
        save(learned=n_learned, last_result=msg)
        log(f"🗣️ 말투 학습: {msg}")
        return msg
    try:
        notes = _learn(pairs, s.get("notes", ""))
    except Exception as e:  # noqa: BLE001
        save(learned=n_learned, last_result=f"AI 정리 실패: {e}")
        return "실패"
    save(notes=notes, learned=n_learned, edited=s.get("edited", 0) + len(pairs),
         updated_at=now.isoformat(timespec="minutes"),
         last_result=f"고친 글 {len(pairs)}편에서 말투를 배웠어요")
    log(f"🗣️ 말투 학습: 고친 글 {len(pairs)}편에서 배운 점을 말투 메모에 반영했어요")
    return "학습함"
