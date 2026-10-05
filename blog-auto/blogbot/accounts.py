"""블로그 계정 (최대 3개).

- 1번 계정: 예전과 같은 자리 (settings.env 의 NAVER_BLOG_ID·SHOPPING_CONNECT_URL, data/naver-session.json)
  → 1개만 쓰던 설정이 그대로 1번이 됨
- 2·3번 계정: DB 설정 "accounts" 에 저장, 로그인은 data/naver-session-2.json / -3.json
- 계정마다: 이름표, 블로그 아이디, 쇼핑커넥트 주소, 로그인, 링크 대기열(links.account), 글(posts.account)
- 자동 작성·[시작]은 1번 → 2번 → 3번 순서
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config, db

MAX = 3
KEY = "accounts"


def session_file(n: int) -> Path:
    return config.SESSION_FILE if n == 1 else config.DATA / f"naver-session-{n}.json"


def _extra() -> dict:
    return {str(k): v for k, v in (db.setting(KEY, {}) or {}).items()}


def get(n: int) -> dict:
    ex = _extra().get(str(n), {})
    if n == 1:
        blog, sc = config.get("NAVER_BLOG_ID").strip(), config.get("SHOPPING_CONNECT_URL").strip()
    else:
        blog, sc = ex.get("blog_id", ""), ex.get("sc_url", "")
    return {"id": n, "label": ex.get("label") or f"{n}번 계정", "blog_id": blog, "sc_url": sc,
            "enabled": ex.get("enabled", True), "logged_in": session_file(n).exists(),
            "sc_base": sc_base(sc)}


def all() -> list[dict]:  # noqa: A001
    out = [get(1)]
    ex = _extra()
    for n in range(2, MAX + 1):
        if str(n) in ex:
            out.append(get(n))
    return out


def active() -> list[dict]:
    """자동 작성·[시작]에 쓰는 계정 (켜져 있고 블로그 아이디가 있는 것), 번호 순."""
    return [a for a in all() if a["enabled"] and a["blog_id"]]


def ids() -> list[int]:
    return [a["id"] for a in all()]


def _clean_blog(v: str) -> str:
    m = re.search(r"blog\.naver\.com/([A-Za-z0-9_\-]+)", v or "")
    return (m.group(1) if m else (v or "")).strip()


def save(n: int, label: str | None = None, blog_id: str | None = None, sc_url: str | None = None,
         enabled: bool | None = None) -> dict:
    if not 1 <= n <= MAX:
        raise ValueError("계정은 1~3번까지예요.")
    ex = _extra()
    cur = ex.get(str(n), {})
    if label is not None:
        cur["label"] = label.strip()[:20]
    if enabled is not None:
        cur["enabled"] = bool(enabled)
    if n == 1:
        upd = {}
        if blog_id is not None:
            upd["NAVER_BLOG_ID"] = _clean_blog(blog_id)
        if sc_url is not None:
            upd["SHOPPING_CONNECT_URL"] = sc_url.strip()
        if upd:
            config.save(upd)
    else:
        if blog_id is not None:
            cur["blog_id"] = _clean_blog(blog_id)
        if sc_url is not None:
            cur["sc_url"] = sc_url.strip()
    ex[str(n)] = cur
    db.set_setting(KEY, ex)
    return get(n)


def add() -> int:
    """빈 계정 칸 추가 (2 → 3)."""
    ex = _extra()
    for n in range(2, MAX + 1):
        if str(n) not in ex:
            ex[str(n)] = {"label": f"{n}번 계정", "enabled": True}
            db.set_setting(KEY, ex)
            return n
    raise ValueError("계정은 3개까지만 넣을 수 있어요.")


def remove(n: int):
    if n == 1:
        raise ValueError("1번 계정은 지울 수 없어요.")
    ex = _extra()
    ex.pop(str(n), None)
    db.set_setting(KEY, ex)


def sc_base(url: str) -> str:
    m = re.search(r"brandconnect\.naver\.com/(\d{6,})", url or "")
    return f"https://brandconnect.naver.com/{m.group(1)}/affiliate/products" if m else ""


def name(n: int) -> str:
    a = get(n)
    return f"{a['label']}({a['blog_id']})" if a["blog_id"] else a["label"]
