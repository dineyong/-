"""댓글 자동 — 두 가지를 따로 켜고 끔 (처음엔 둘 다 꺼짐, 연습 모드 켜짐).

1. 내 글 답글: 내 블로그 최근 글(RSS)에 달린 댓글에 AI가 짧게 답글
2. 이웃 댓글: 적어둔 이웃 블로그(+ 내 글에 댓글 단 사람 = 답방)의 새 글에 댓글 1개

안전장치 (네이버 스팸 판정·계정 제한 막기)
- 연습 모드: 무엇을 달지 기록만 하고 실제로는 안 닮. 기록을 보고 괜찮으면 끄기
- 하루 최대 개수 (답글 ≤ 30, 이웃 댓글 ≤ 10), 9시~22시에만, 1시간쯤마다 한 번
- 한 번에 답글 최대 5개·이웃 댓글 1개, 사이사이 랜덤 대기, 같은 블로그엔 하루 1개
- 같은 댓글·같은 글에는 다시 안 닮 (comments 표에 기록), 내 계정끼리 같은 글에 겹쳐 달지 않음
- 광고·욕설·비밀 댓글·민감한 글은 건너뜀 (AI가 판단)
- 네이버가 "제한·스팸" 알림을 띄우면 오늘은 모든 댓글 멈춤
- 로그인이 풀리면 그 계정은 멈춤, 연속 3번 실패하면 둘 다 꺼짐
- 화면을 못 읽으면 ~/BlogAuto/recon/comments.html·png 로 저장 (Claude에게 보내면 고침)
"""
from __future__ import annotations

import datetime as dt
import random
import re
import time

from playwright.sync_api import Page, sync_playwright

from . import accounts, ai, config, db, naver, style
from .log import log

KEY = "comments"
DEFAULT = {"reply_on": False, "visit_on": False, "practice": True, "reply_per_day": 10, "visit_per_day": 3,
           "visit_ids": "", "visit_back": True, "message": "", "next_at": None, "fails": 0, "paused_day": None}
REPLY_MAX, VISIT_MAX = 30, 10      # 하루 상한 (설정으로도 못 넘김)
PER_RUN_REPLY = 5
HOURS = (9, 22)                    # 이 시간 안에서만
EVERY_MIN = (50, 80)               # 다음 확인까지 (랜덤)
REPLY_DAYS = 7                     # 이보다 오래된 댓글엔 답글 안 닮
MY_POSTS = 8                       # 내 글은 최근 몇 편까지 확인
VISIT_DAYS = 3                     # 이웃 글은 며칠 안에 올라온 것만
BACK_KEEP = 40                     # 답방 후보 몇 명까지 기억
STOP_WORDS = ("제한", "스팸", "차단", "어뷰징", "일시적으로", "너무 많", "도배")

db.run("""CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account INTEGER NOT NULL,
  kind TEXT NOT NULL,           -- reply(내 글 답글) | visit(이웃 댓글)
  target TEXT NOT NULL,         -- reply: 블로그/글번호/댓글번호, visit: 블로그/글번호
  post_url TEXT, post_title TEXT, who TEXT, their_text TEXT, my_text TEXT,
  status TEXT NOT NULL,         -- DONE | PRACTICE | SKIP | FAILED
  error TEXT,
  created_at TEXT NOT NULL)""")
db.run("CREATE INDEX IF NOT EXISTS comments_target ON comments(kind, target)")


class Paused(Exception):
    """네이버가 제한 알림을 띄움 → 오늘은 그만."""


def get() -> dict:
    s = dict(DEFAULT)
    s.update(db.setting(KEY, {}) or {})
    return s


def save(**patch) -> dict:
    s = get()
    if (patch.get("reply_on") and not s["reply_on"]) or (patch.get("visit_on") and not s["visit_on"]):
        patch.setdefault("fails", 0)
        patch.setdefault("next_at", None)          # 켜면 다음 확인 때 바로
    s.update(patch)
    s["reply_per_day"] = min(max(int(s["reply_per_day"]), 1), REPLY_MAX)
    s["visit_per_day"] = min(max(int(s["visit_per_day"]), 1), VISIT_MAX)
    db.set_setting(KEY, s)
    return s


def _msg(text: str):
    save(message=f"{text} ({dt.datetime.now():%m/%d %H:%M})")


def visit_ids() -> list[str]:
    """직접 적은 이웃 + (켜져 있으면) 내 글에 댓글 단 사람. 내 블로그는 뺌."""
    s = get()
    mine = {a["blog_id"].lower() for a in accounts.all() if a["blog_id"]}
    ids = [accounts._clean_blog(x) for x in re.split(r"[\s,]+", s["visit_ids"] or "") if x.strip()]
    if s["visit_back"]:
        ids += [b["id"] for b in back_list()]
    return [i for i in dict.fromkeys(ids) if i and i.lower() not in mine]


def back_list() -> list[dict]:
    return db.setting("comment_back", []) or []


def _remember_back(blog_id: str, nick: str):
    lst = [b for b in back_list() if b["id"] != blog_id]
    lst.insert(0, {"id": blog_id, "nick": nick[:20], "at": db.now()[:16]})
    db.set_setting("comment_back", lst[:BACK_KEEP])


def today_counts(account: int) -> dict:
    today = dt.date.today().isoformat()
    rows = db.q("SELECT kind, COUNT(*) n FROM comments WHERE account=? AND status='DONE' AND substr(created_at,1,10)=? "
                "GROUP BY kind", (account, today))
    c = {"reply": 0, "visit": 0}
    c.update({r["kind"]: r["n"] for r in rows})
    return c


def recent(n: int = 60) -> list[dict]:
    return db.q("SELECT * FROM comments WHERE status != 'SKIP' OR created_at >= ? ORDER BY id DESC LIMIT ?",
                ((dt.date.today() - dt.timedelta(days=1)).isoformat(), n))


def _seen(kind: str, target: str, practice: bool, account: int | None = None) -> bool:
    sts = ("DONE", "SKIP", "PRACTICE") if practice else ("DONE", "SKIP")
    sql = f"SELECT COUNT(*) n FROM comments WHERE kind=? AND target=? AND status IN ({','.join('?' * len(sts))})"
    args: tuple = (kind, target, *sts)
    if account is not None:
        sql += " AND account=?"
        args += (account,)
    if db.one(sql, args)["n"]:
        return True
    # 두 번 실패한 건 포기
    return db.one("SELECT COUNT(*) n FROM comments WHERE kind=? AND target=? AND status='FAILED'", (kind, target))["n"] >= 2


def _record(account: int, kind: str, target: str, status: str, **f):
    db.run("INSERT INTO comments(account, kind, target, post_url, post_title, who, their_text, my_text, status, error, "
           "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
           (account, kind, target, f.get("post_url"), (f.get("post_title") or "")[:120], (f.get("who") or "")[:40],
            (f.get("their_text") or "")[:500], f.get("my_text"), status, f.get("error"), db.now()))


def due() -> bool:
    s = get()
    if not (s["reply_on"] or s["visit_on"]):
        return False
    now = dt.datetime.now()
    if s["paused_day"] == now.date().isoformat() or not (HOURS[0] <= now.hour < HOURS[1]):
        return False
    return not s["next_at"] or s["next_at"] <= now.isoformat(timespec="minutes")


# ─────────────────────────────────────────────
# AI가 쓸 말
# ─────────────────────────────────────────────
COMMON = """말투 규칙:
- 실제 사람이 폰으로 쓰는 것처럼 짧고 자연스럽게. 1~2문장, 25~90자
- 존댓말, 상대가 한 말이나 글의 구체적인 한 부분을 받아서 말하기
- 매번 다른 표현. "좋은 정보 감사합니다", "잘 보고 갑니다" 같은 뻔한 문장만으로 끝내지 않기
- 이모지·이모티콘은 0~1개, 느낌표 남발 금지, 해시태그·링크·내 블로그 홍보·"서이추/맞팔/방문해주세요" 금지
- AI처럼 정리하거나 요약하지 않기, 과한 칭찬 금지"""


def _style_hint() -> str:
    notes = (style.get().get("notes") or "").strip()
    return f"\n\n[이 블로그 주인의 말투 메모 — 참고만]\n{notes[:600]}" if notes else ""


def ai_reply(post_title: str, nick: str, text: str) -> dict:
    system = ("당신은 네이버 블로그 주인입니다. 내 글에 달린 댓글에 답글을 답니다.\n" + COMMON + """
- 질문이면 아는 만큼 짧게 답하고, 모르면 솔직하게
- 상대 닉네임은 부르지 않아도 됨 (부르면 '님'만 붙이기)
- 광고·홍보 링크·욕설·성인·도박·의미 없는 글자 댓글이면 skip=true

JSON만: {"skip": false, "why": "건너뛰는 이유(건너뛸 때만)", "reply": "답글"}""" + _style_hint())
    user = f"[내 글 제목]\n{post_title}\n\n[댓글 단 사람]\n{nick}\n\n[댓글]\n{text[:600]}"
    return ai.generate_json(system, user, validate=lambda d: d.get("skip") or len((d.get("reply") or "").strip()) >= 5)


def ai_visit(title: str, body: str) -> dict:
    system = ("당신은 네이버 블로그를 하는 사람입니다. 이웃 블로그 글을 읽고 댓글을 하나 남깁니다.\n" + COMMON + """
- 글을 실제로 읽은 티가 나게, 본문 속 구체적인 내용 하나를 언급하기
- 정치·종교·성인·도박·투자권유·광고 대행·부고/사고처럼 민감한 글이거나, 본문이 거의 없으면 skip=true

JSON만: {"skip": false, "why": "건너뛰는 이유(건너뛸 때만)", "comment": "댓글"}""" + _style_hint())
    user = f"[글 제목]\n{title}\n\n[본문 앞부분]\n{body[:1800]}"
    return ai.generate_json(system, user, validate=lambda d: d.get("skip") or len((d.get("comment") or "").strip()) >= 5)


def _clean(t: str) -> str:
    t = re.sub(r"https?://\S+|#\S+", "", t or "").strip().strip('"').strip()
    return re.sub(r"\s+", " ", t)[:150]


# ─────────────────────────────────────────────
# 브라우저 (모바일 댓글 화면 m.blog.naver.com/CommentList.naver)
# ─────────────────────────────────────────────
READ_JS = r"""
(root) => [...(root || document).querySelectorAll('li.u_cbox_comment')].map(li => {
  const info = li.getAttribute('data-info') || '';
  const g = (k) => { const m = info.match(new RegExp(k + "\\s*:\\s*'?([^',]*)")); return m ? m[1] : ''; };
  const q = (s) => li.querySelector(s);
  const prof = li.querySelector('a.u_cbox_name, .u_cbox_name a, a.u_cbox_thumb, a[href*="blog.naver.com"], a[href*="blogId="]');
  const date = q('.u_cbox_date');
  return {
    no: g('commentNo'), mine: g('mine') === 'true',
    secret: g('secret') === 'true' || !!q('.u_cbox_secret, .u_cbox_ico_secret'),
    deleted: g('deleted') === 'true' || !!q('.u_cbox_delete_contents'),
    reply: !!li.parentElement.closest('.u_cbox_reply_area, li.u_cbox_comment'),
    nick: ((q('.u_cbox_nick') || {}).textContent || '').trim(),
    text: ((q('.u_cbox_contents') || {}).innerText || '').trim(),
    date: date ? (date.getAttribute('data-value') || date.textContent || '') : '',
    replies: parseInt(((q('.u_cbox_reply_cnt') || {}).textContent || '0').replace(/\D/g, '')) || 0,
    href: prof ? (prof.getAttribute('href') || '') : ''
  };
})
"""
BOX_SELS = ("textarea.u_cbox_text", ".u_cbox_text[contenteditable='true']", ".u_cbox_inbox textarea",
            "div.u_cbox_text", "textarea")


def _blog_of(href: str) -> str | None:
    m = re.search(r"blogId=([A-Za-z0-9_\-]+)", href or "") or \
        re.search(r"blog\.naver\.com/([A-Za-z0-9_\-]+)(?:[/?#]|$)", href or "")
    if not m or m.group(1).endswith(".naver") or m.group(1) in ("PostView", "PostList", "CommentList"):
        return None
    return m.group(1)


class Browser:
    def __init__(self, account: int):
        self.account = account
        f = accounts.session_file(account)
        if not f.exists():
            raise naver.LoginExpired(f"[{accounts.name(account)}] 네이버 로그인이 필요해요.")
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=False, slow_mo=60,
                                                args=["--disable-blink-features=AutomationControlled"])
        self.ctx = self.browser.new_context(storage_state=str(f), viewport={"width": 430, "height": 900},
                                            locale="ko-KR", user_agent=style.UA, is_mobile=True, has_touch=True)
        self.page: Page = self.ctx.new_page()
        self.page.add_init_script(naver.STEALTH)
        self.alerts: list[str] = []
        self.page.on("dialog", self._dialog)

    def _dialog(self, d):
        msg = (d.message or "").strip()
        log(f"   💬 네이버 알림창: {msg[:120]}")
        self.alerts.append(msg)
        try:
            d.accept()
        except Exception:
            pass

    def close(self):
        for fn in (self.browser.close, self._pw.stop):
            try:
                fn()
            except Exception:
                pass

    def check_login(self):
        try:
            self.page.goto("https://m.blog.naver.com/MyBlog.naver", wait_until="domcontentloaded", timeout=30000)
            self.page.wait_for_timeout(2000)
        except Exception:
            pass
        if "nidlogin" in self.page.url or "nid.naver.com" in self.page.url:
            raise naver.LoginExpired(f"[{accounts.name(self.account)}] 네이버 로그인이 풀렸어요. 설정 › 블로그 계정에서 다시 로그인해 주세요.")

    def open(self, blog_id: str, log_no: str) -> bool:
        url = f"https://m.blog.naver.com/CommentList.naver?blogId={blog_id}&logNo={log_no}"
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            self.page.wait_for_selector(".u_cbox", timeout=15000)
            self.page.wait_for_timeout(1500)
            return True
        except Exception:
            dump(self.page)
            return False

    def comments(self, root=None) -> list[dict]:
        if root is None:
            return self.page.evaluate(READ_JS, None)
        return root.evaluate(READ_JS)

    def _wait(self, a: float, b: float):
        self.page.wait_for_timeout(int(random.uniform(a, b) * 1000))

    def _type_and_send(self, wrap, text: str) -> bool:
        """글 상자에 쓰고 [등록]. wrap = 그 글 상자를 감싼 요소."""
        guide = wrap.query_selector(".u_cbox_guide")
        if guide and guide.is_visible():
            guide.click()
            self._wait(0.5, 1.2)
        box = None
        for sel in BOX_SELS:
            box = wrap.query_selector(sel)
            if box and box.is_visible():
                break
            box = None
        if not box:
            dump(self.page)
            return False
        box.click()
        self._wait(0.6, 1.5)
        if (box.evaluate("e => e.tagName") or "").upper() == "TEXTAREA":
            box.fill(text)
        else:
            self.page.keyboard.insert_text(text)
        self._wait(1.5, 3.5)                       # 사람처럼 잠깐 읽어보고
        btn = wrap.query_selector(".u_cbox_btn_upload") or wrap.query_selector("button:has-text('등록')")
        if not btn:
            dump(self.page)
            return False
        n_alerts = len(self.alerts)
        btn.click()
        self.page.wait_for_timeout(3000)
        new = self.alerts[n_alerts:]
        if any(w in a for a in new for w in STOP_WORDS):
            raise Paused(new[-1][:120])
        return True

    def _posted(self, text: str, scope=None) -> bool:
        key = re.sub(r"\s", "", text)[:12]
        for c in self.comments(scope):
            if c["mine"] and key in re.sub(r"\s", "", c["text"]):
                return True
        return False

    def reply_to(self, no: str, text: str) -> tuple[bool, str]:
        li = self._li(no)
        if not li:
            return False, "댓글을 다시 못 찾음"
        area = li.query_selector(".u_cbox_reply_area")
        if not (area and area.is_visible()):
            b = li.query_selector(".u_cbox_btn_reply")
            if not b:
                return False, "답글 버튼을 못 찾음"
            b.click()
            self.page.wait_for_timeout(2000)
            area = li.query_selector(".u_cbox_reply_area")
        if not area:
            dump(self.page)
            return False, "답글 칸을 못 찾음"
        wrap = area.query_selector(".u_cbox_write_wrap") or area
        if not self._type_and_send(wrap, text):
            return False, "글 상자·등록 버튼을 못 찾음"
        if self._posted(text, area):
            return True, ""
        return False, (self.alerts[-1][:120] if self.alerts else "등록됐는지 확인 안 됨")

    def mine_replied(self, no: str) -> bool:
        """이 댓글에 내가 단 답글이 이미 있는지 (답글 칸을 열어서 확인, 열린 채로 둠)."""
        li = self._li(no)
        if not li:
            return False
        b = li.query_selector(".u_cbox_btn_reply")
        if b:
            b.click()
            self.page.wait_for_timeout(2000)
        area = li.query_selector(".u_cbox_reply_area")
        return bool(area) and any(c["mine"] for c in self.comments(area))

    def write_top(self, text: str) -> tuple[bool, str]:
        wraps = [w for w in self.page.query_selector_all(".u_cbox_write_wrap")
                 if not w.evaluate("e => !!e.closest('.u_cbox_reply_area')")]
        if not wraps:
            dump(self.page)
            return False, "댓글 칸을 못 찾음 (댓글을 막아둔 글일 수 있어요)"
        if not self._type_and_send(wraps[0], text):
            return False, "글 상자·등록 버튼을 못 찾음"
        self.page.wait_for_timeout(1500)
        if self._posted(text):
            return True, ""
        return False, (self.alerts[-1][:120] if self.alerts else "등록됐는지 확인 안 됨")

    def _li(self, no: str):
        for li in self.page.query_selector_all("li.u_cbox_comment"):
            if f"commentNo:'{no}'" in (li.get_attribute("data-info") or "").replace(" ", ""):
                return li
        return None


def dump(page):
    """화면을 못 읽었을 때 저장 (Claude가 보고 고칠 수 있게)."""
    try:
        d = config.HOME / "recon"
        d.mkdir(exist_ok=True)
        page.screenshot(path=str(d / "comments.png"), full_page=True)
        (d / "comments.html").write_text(page.content(), "utf-8")
        log(f"   📸 댓글 화면을 저장했어요: {d / 'comments.html'} — 계속 안 되면 Claude에게 보내주세요")
    except Exception:
        pass


def _log_no(link: str) -> str | None:
    m = re.search(r"logNo=(\d+)", link or "") or re.search(r"/(\d{9,})", link or "")
    return m.group(1) if m else None


# ─────────────────────────────────────────────
# 한 번 확인 (일꾼 스레드 안, 글쓰기와 겹치지 않음)
# ─────────────────────────────────────────────
def run(accs: list[int], on_login_bad=None, force: bool = False) -> str:
    s = get()
    now = dt.datetime.now()
    save(next_at=(now + dt.timedelta(minutes=random.randint(*EVERY_MIN))).isoformat(timespec="minutes"))
    if not (s["reply_on"] or s["visit_on"]):
        if not force:
            return "꺼져 있음"
        s = dict(s, reply_on=True, visit_on=True, practice=True)   # 둘 다 꺼진 채 [지금 확인] → 연습으로 한 번
        log("🧪 댓글이 꺼져 있어서 연습으로 한 번 확인해요 (실제로는 안 달아요)")
    done_total, problems = [], []
    for a in accs:
        try:
            r = _run_account(a, s)
            if r:
                done_total.append(r)
        except Paused as e:
            save(paused_day=dt.date.today().isoformat(), message=f"네이버가 제한 알림을 띄워서 오늘은 댓글을 멈췄어요: {e}")
            log(f"⛔ 댓글 멈춤 (오늘): {e}")
            return "멈춤"
        except naver.LoginExpired as e:
            log(f"🔐 댓글: {e}")
            problems.append(str(e))
            if on_login_bad:
                on_login_bad(a)
        except ai.OfflineError as e:
            log(f"📴 댓글: 인터넷 문제로 다음에 다시: {e}")
            return "오프라인"
        except Exception as e:  # noqa: BLE001
            log(f"❌ 댓글 확인 오류 [{accounts.name(a)}]: {e}")
            problems.append(str(e) or e.__class__.__name__)
    s = get()
    if problems and not done_total:
        fails = s["fails"] + 1
        if fails >= 3:
            save(reply_on=False, visit_on=False, fails=0,
                 message=f"댓글이 연속 3번 실패해서 꺼졌어요: {problems[-1][:120]}")
            log("⛔ 댓글 연속 3번 실패 → 꺼짐")
            return "꺼짐"
        save(fails=fails)
        _msg(f"확인 실패 ({fails}/3): {problems[-1][:100]}")
        return "실패"
    save(fails=0)
    _msg(" · ".join(done_total) if done_total else "새로 달 댓글이 없어요")
    return "완료"


def _run_account(account: int, s: dict) -> str:
    acc = accounts.get(account)
    if not acc["blog_id"]:
        return ""
    cnt = today_counts(account)
    want_reply = s["reply_on"] and cnt["reply"] < s["reply_per_day"]
    want_visit = s["visit_on"] and cnt["visit"] < s["visit_per_day"]
    if not (want_reply or want_visit):
        return ""
    who = f"[{acc['label']}] " if len(accounts.all()) > 1 else ""
    b = Browser(account)
    out = []
    try:
        b.check_login()
        if want_reply:
            n = _replies(b, acc, s, min(PER_RUN_REPLY, s["reply_per_day"] - cnt["reply"]))
            if n:
                out.append(f"답글 {n}개" + (" (연습)" if s["practice"] else ""))
        if want_visit:
            if _visit(b, acc, s):
                out.append("이웃 댓글 1개" + (" (연습)" if s["practice"] else ""))
    finally:
        b.close()
    return (who + ", ".join(out)) if out else ""


def _replies(b: Browser, acc: dict, s: dict, limit: int) -> int:
    account, me, practice = acc["id"], acc["blog_id"], s["practice"]
    try:
        posts = style.rss(me)[:MY_POSTS]
    except Exception as e:  # noqa: BLE001
        log(f"   ⚠️ 내 블로그 글 목록(RSS)을 못 읽었어요: {e}")
        return 0
    oldest = (dt.date.today() - dt.timedelta(days=REPLY_DAYS)).isoformat()
    n = 0
    for p in posts:
        if n >= limit:
            break
        log_no = _log_no(p["link"])
        if not log_no:
            continue
        if not b.open(me, log_no):
            log(f"   ⚠️ 댓글 화면을 못 읽었어요: {p['title'][:30]}")
            continue
        for c in b.comments():
            if n >= limit:
                break
            if c["reply"] or c["mine"] or c["deleted"] or not c["no"]:
                continue
            target = f"{me}/{log_no}/{c['no']}"
            if _seen("reply", target, practice, account):
                continue
            base = dict(post_url=f"https://blog.naver.com/{me}/{log_no}", post_title=p["title"], who=c["nick"],
                        their_text=c["text"])
            bid = _blog_of(c["href"])
            if bid and bid.lower() != me.lower():
                _remember_back(bid, c["nick"])
            if c["secret"] or not c["text"]:
                _record(account, "reply", target, "SKIP", error="비밀 댓글", **base)
                continue
            if c["date"][:10] and c["date"][:10] < oldest:
                _record(account, "reply", target, "SKIP", error="오래된 댓글", **base)
                continue
            if c["replies"] and b.mine_replied(c["no"]):
                _record(account, "reply", target, "SKIP", error="이미 답글 있음", **base)
                continue
            r = ai_reply(p["title"], c["nick"], c["text"])
            if r.get("skip"):
                _record(account, "reply", target, "SKIP", error=f"AI가 건너뜀: {r.get('why') or ''}"[:120], **base)
                log(f"   ⏭️ 답글 건너뜀 ({c['nick']}): {r.get('why') or ''}")
                continue
            text = _clean(r["reply"])
            if practice:
                _record(account, "reply", target, "PRACTICE", my_text=text, **base)
                log(f"   🧪 연습 답글 · {c['nick']}: “{c['text'][:40]}” → “{text}”")
                n += 1
                continue
            ok, err = b.reply_to(c["no"], text)
            _record(account, "reply", target, "DONE" if ok else "FAILED", my_text=text, error=err or None, **base)
            log(f"   {'💬 답글 달았어요' if ok else '⚠️ 답글 실패'} · {c['nick']}: “{text}”" + (f" ({err})" if err else ""))
            if ok:
                n += 1
                b._wait(20, 60)
    return n


def _visit(b: Browser, acc: dict, s: dict) -> bool:
    account, practice = acc["id"], s["practice"]
    ids = visit_ids()
    random.shuffle(ids)
    today = dt.date.today().isoformat()
    since = dt.datetime.now() - dt.timedelta(days=VISIT_DAYS)
    for bid in ids[:8]:                    # 한 번에 RSS 8곳까지만 읽음
        if db.one("SELECT COUNT(*) n FROM comments WHERE kind='visit' AND target LIKE ? AND status IN ('DONE','PRACTICE') "
                  "AND substr(created_at,1,10)=?", (f"{bid}/%", today))["n"]:
            continue                       # 같은 블로그엔 하루 1개 (내 계정 전체)
        try:
            posts = [p for p in style.rss(bid) if p["pub"] and p["pub"] >= since][:2]
        except Exception:  # noqa: BLE001
            continue
        for p in posts:
            log_no = _log_no(p["link"])
            if not log_no:
                continue
            target = f"{bid}/{log_no}"
            if _seen("visit", target, practice):
                continue
            base = dict(post_url=f"https://blog.naver.com/{bid}/{log_no}", post_title=p["title"], who=bid,
                        their_text=p["desc"][:300])
            if not b.open(bid, log_no):
                _record(account, "visit", target, "FAILED", error="댓글 화면을 못 읽음", **base)
                continue
            if any(c["mine"] for c in b.comments()):
                _record(account, "visit", target, "SKIP", error="이미 댓글 있음", **base)
                continue
            r = ai_visit(p["title"], p["desc"])
            if r.get("skip"):
                _record(account, "visit", target, "SKIP", error=f"AI가 건너뜀: {r.get('why') or ''}"[:120], **base)
                continue
            text = _clean(r["comment"])
            if practice:
                _record(account, "visit", target, "PRACTICE", my_text=text, **base)
                log(f"   🧪 연습 이웃 댓글 · {bid} 「{p['title'][:30]}」 → “{text}”")
                return True
            b._wait(25, 70)                # 글 읽는 시간
            ok, err = b.write_top(text)
            _record(account, "visit", target, "DONE" if ok else "FAILED", my_text=text, error=err or None, **base)
            log(f"   {'💬 이웃 댓글 달았어요' if ok else '⚠️ 이웃 댓글 실패'} · {bid} 「{p['title'][:30]}」: “{text}”"
                + (f" ({err})" if err else ""))
            return ok
    return False
