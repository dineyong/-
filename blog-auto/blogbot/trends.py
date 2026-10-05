"""인기 키워드 — 네이버 데이터랩 쇼핑인사이트의 분야별 인기 검색어 순위를 하루 한 번 모은다.

공식 API는 '내가 넣은 키워드의 추세'만 주고 순위 목록은 주지 않아서, 데이터랩 화면이 쓰는 순위 요청을
그 화면 안(같은 사이트)에서 그대로 보낸다. 하루 한 번만 읽음. 네이버가 화면을 바꾸면 고쳐야 할 수 있음
→ 실패하면 화면 구조를 기록으로 남겨 고칠 수 있게 함.
"""
from __future__ import annotations

import datetime as dt
import json

from . import config, db
from .log import log

KEY = "trends"
PAGE = "https://datalab.naver.com/shoppingInsight/sCategory.naver"
RANK = "https://datalab.naver.com/shoppingInsight/getCategoryKeywordRank.naver"

# 데이터랩 쇼핑 분야 (1분류) 번호
CATS = {
    "50000008": "생활/건강",
    "50000004": "가구/인테리어",
    "50000003": "디지털/가전",
    "50000006": "식품",
    "50000005": "출산/육아",
    "50000007": "스포츠/레저",
    "50000002": "화장품/미용",
    "50000000": "패션의류",
    "50000001": "패션잡화",
}
DEFAULT_CATS = ["50000008", "50000004", "50000003"]   # 생활 꿀팁 블로그에 맞는 분야


def get() -> dict:
    s = {"date": None, "cats": DEFAULT_CATS, "items": [], "error": "", "updated_at": None,
         "extra": [], "skip": {}, "suggesting": False, "suggest_msg": ""}
    s.update(db.setting(KEY, {}) or {})
    return s


def save(**patch) -> dict:
    s = get()
    s.update(patch)
    db.set_setting(KEY, s)
    return s


JS = """async ({url, cid, day, count}) => {
  const body = new URLSearchParams({cid, timeUnit: 'date', startDate: day, endDate: day,
                                    age: '', gender: '', device: '', page: '1', count: String(count)});
  const r = await fetch(url, {method: 'POST', credentials: 'include',
     headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest'},
     body});
  return {status: r.status, text: await r.text()};
}"""


def collect(count: int = 20) -> dict:
    """데이터랩에서 분야별 인기 검색어를 읽어 저장. 로봇 브라우저는 화면 없이(headless) 잠깐만 씀."""
    from playwright.sync_api import sync_playwright
    s = get()
    cats = [c for c in (s.get("cats") or DEFAULT_CATS) if c in CATS] or DEFAULT_CATS
    day = (dt.date.today() - dt.timedelta(days=1)).isoformat()      # 어제 하루 기준 (오늘 자료는 아직 없음)
    prev = {(x["cat"], x["kw"]): x["rank"] for x in s.get("items", [])}
    items, errors = [], []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            page = b.new_page(locale="ko-KR")
            page.goto(PAGE, timeout=30000)
            page.wait_for_timeout(1500)
            for cid in cats:
                try:
                    res = page.evaluate(JS, {"url": RANK, "cid": cid, "day": day, "count": count})
                    data = json.loads(res["text"])
                    ranks = data.get("ranks") or []
                    if not ranks:
                        raise ValueError(f"순위가 비어 있어요 (응답 {res['status']}: {res['text'][:120]})")
                    for r in ranks[:count]:
                        kw = str(r.get("keyword", "")).strip()
                        if not kw:
                            continue
                        rank = int(r.get("rank") or 0)
                        before = prev.get((cid, kw))
                        items.append({"kw": kw, "cat": cid, "cat_name": CATS[cid], "rank": rank, "prev": before,
                                      # 새로 들어왔거나 5계단 이상 오르면 🔥
                                      "hot": (before is None and bool(prev)) or (before is not None and before - rank >= 5)})
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{CATS[cid]}: {e}")
            if errors and not items:
                _save_page_for_fix(page)
        finally:
            b.close()
    msg = "; ".join(errors)
    if items:
        save(date=dt.date.today().isoformat(), day=day, items=items, error=msg,
             updated_at=dt.datetime.now().isoformat(timespec="minutes"))
        hot = [x["kw"] for x in items if x["hot"]]
        log(f"🔥 인기 키워드 {len(items)}개 모음 ({', '.join(CATS[c] for c in cats)})" +
            (f" — 급상승: {', '.join(hot[:5])}" if hot else ""))
    else:
        save(error=msg or "인기 키워드를 못 읽었어요", tried_at=dt.datetime.now().isoformat(timespec="minutes"))
        log(f"⚠️ 인기 키워드 읽기 실패: {msg}")
    return get()


def _save_page_for_fix(page):
    """실패하면 화면을 저장해 두기 (Claude가 보고 고칠 수 있게)."""
    try:
        d = config.HOME / "recon"
        d.mkdir(exist_ok=True)
        page.screenshot(path=str(d / "datalab.png"), full_page=True)
        (d / "datalab.html").write_text(page.content(), "utf-8")
    except Exception:
        pass


def hints(n: int = 15) -> list[str]:
    """정보글 주제 고를 때 참고할 요즘 인기 키워드 (급상승 먼저)."""
    items = get().get("items") or []
    items = sorted(items, key=lambda x: (not x.get("hot"), x.get("rank", 99)))
    out = []
    for x in items:
        if x["kw"] not in out:
            out.append(x["kw"])
    return out[:n]


def due() -> bool:
    s = get()
    if s.get("date") == dt.date.today().isoformat():
        return False
    t = s.get("tried_at")           # 실패했으면 3시간 뒤에 다시
    return not t or dt.datetime.now() - dt.datetime.fromisoformat(t) > dt.timedelta(hours=3)


# ─────────────────────────────────────────────
# 다른 키워드 — 안 쓸 키워드 빼기 / 직접 넣기 / AI 추천
# ─────────────────────────────────────────────
KEEP_DAYS = 14          # 빼기·추천 키워드를 기억하는 기간
USED_DAYS = 30          # 이 기간에 링크를 넣은 키워드는 '다 쓴 키워드'로
_running = False        # AI 추천이 실제로 도는 중인지 (앱이 중간에 꺼졌으면 '고르는 중' 표시를 풀기 위해)


def _fresh(d: str | None, days: int) -> bool:
    try:
        return dt.date.today() - dt.date.fromisoformat(str(d)[:10]) <= dt.timedelta(days=days)
    except Exception:  # noqa: BLE001
        return False


def tidy_lists() -> None:
    s = get()
    skip = {k: d for k, d in (s.get("skip") or {}).items() if _fresh(d, KEEP_DAYS)}
    extra = [x for x in (s.get("extra") or []) if _fresh(x.get("date"), KEEP_DAYS) and x.get("kw") not in skip]
    patch = {}
    if skip != s.get("skip") or extra != s.get("extra"):
        patch.update(skip=skip, extra=extra)
    if s.get("suggesting") and not _running:
        patch["suggesting"] = False
    if patch:
        save(**patch)


def skip(kw: str) -> None:
    s = get()
    sk = dict(s.get("skip") or {})
    sk[kw] = dt.date.today().isoformat()
    save(skip=sk, extra=[x for x in (s.get("extra") or []) if x.get("kw") != kw])


def unskip_all() -> int:
    n = len(get().get("skip") or {})
    save(skip={})
    return n


def add_extra(kws: list[dict], src: str) -> int:
    """직접 넣은 키워드(src='me')나 AI 추천(src='ai')을 목록 맨 앞에."""
    s = get()
    have = {x["kw"] for x in (s.get("extra") or [])}
    sk = dict(s.get("skip") or {})
    new = []
    for x in kws:
        kw = " ".join(str(x.get("kw", "")).split())[:30]
        if kw and kw not in have:
            have.add(kw)
            sk.pop(kw, None)
            new.append({"kw": kw, "why": str(x.get("why", ""))[:60], "src": src,
                        "date": dt.date.today().isoformat()})
    save(extra=new + list(s.get("extra") or []), skip=sk)
    return len(new)


def used_keywords(account: int | None = None) -> list[str]:
    since = (dt.date.today() - dt.timedelta(days=USED_DAYS)).isoformat()
    sql = "SELECT DISTINCT keyword FROM links WHERE keyword IS NOT NULL AND created_at >= ?"
    args: tuple = (since,)
    if account:
        sql += " AND account=?"
        args += (account,)
    return [r["keyword"] for r in db.q(sql, args)]


def suggest(account: int = 1) -> None:
    """AI가 블로그 주제·계절·인기 키워드를 보고, 아직 안 쓴 쇼핑 키워드 8개를 추천 (브라우저 안 씀)."""
    global _running
    from . import ai, prompts
    _running = True
    save(suggesting=True, suggest_msg="")
    try:
        s = get()
        cats = [CATS[c] for c in (s.get("cats") or DEFAULT_CATS) if c in CATS]
        avoid = sorted(set(used_keywords(account)) | set((s.get("skip") or {}).keys())
                       | {x["kw"] for x in (s.get("extra") or [])})
        sy, u = prompts.shop_keywords(config.get("BLOG_TOPIC"), cats, [x["kw"] for x in s.get("items") or []][:40], avoid)
        d = ai.generate_json(sy, u, lambda d: isinstance(d.get("keywords"), list) and len(d["keywords"]) >= 3)
        got = [x if isinstance(x, dict) else {"kw": x} for x in d["keywords"]]
        got = [x for x in got if str(x.get("kw", "")).strip() and str(x.get("kw")).strip() not in avoid]
        n = add_extra(got[:8], "ai")
        msg = f"AI 추천 키워드 {n}개를 맨 위에 넣었어요."
        log(f"💡 {msg} ({', '.join(x['kw'] for x in got[:8])})")
        save(suggesting=False, suggest_msg=msg)
    except Exception as e:  # noqa: BLE001
        save(suggesting=False, suggest_msg=f"추천 실패: {e}")
        log(f"⚠️ AI 키워드 추천 실패: {e}")
    finally:
        _running = False
