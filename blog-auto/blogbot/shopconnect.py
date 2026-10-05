"""쇼핑커넥트 추천 상품 — 인기 키워드로 상품을 찾아 기준(수수료·리뷰)에 맞는 것을 골라 보여주기만 함.

링크 발급은 하지 않음. 사용자가 [쇼핑커넥트에서 열기]로 상품 화면을 열어 직접 [링크 발급]을 누르고,
받은 링크를 추천 목록 옆 칸에 붙여넣으면 상품 이름과 함께 링크 대기열에 들어감.
"""
from __future__ import annotations

import datetime as dt

from . import db, naver, trends
from .log import log

KEY = "shopconnect"
DEFAULT = {"min_reviews": 200, "min_commission": 5, "per_keyword": 2, "keywords": 3,
           "items": [], "updated_at": None, "message": "", "done_ids": []}


def get() -> dict:
    s = dict(DEFAULT)
    s.update(db.setting(KEY, {}) or {})
    return s


def save(**patch) -> dict:
    s = get()
    s.update(patch)
    s["min_reviews"] = max(0, int(s["min_reviews"]))
    s["min_commission"] = max(0, int(s["min_commission"]))
    s["done_ids"] = (s.get("done_ids") or [])[-300:]
    db.set_setting(KEY, s)
    return s


def recommend(keyword: str | None = None) -> list[dict]:
    """인기 키워드(또는 지정 키워드)로 검색 → 수수료 높은 순 → 상세에서 리뷰 확인 → 기준 맞는 상품 목록 저장."""
    s = get()
    done = set(s.get("done_ids") or [])
    kws = [keyword] if keyword else trends.hints(10)[: s["keywords"]]
    if not kws:
        save(message="인기 키워드가 아직 없어요. 인기 키워드를 먼저 모아주세요.")
        return []
    picked: list[dict] = []
    sc = naver.ShoppingConnect()
    try:
        sc.open()
        for kw in kws:
            items = [p for p in sc.search(kw) if p["id"] not in done and p["commission"] >= s["min_commission"]]
            items.sort(key=lambda p: -p["commission"])          # 수수료 높은 것부터 (같으면 검색 순서)
            got = 0
            for p in items[:6]:                                   # 상세는 키워드당 최대 6개만 열어봄
                n = sc.reviews(p)
                if n >= s["min_reviews"]:
                    p["found_at"] = dt.datetime.now().isoformat(timespec="minutes")
                    picked.append(p)
                    got += 1
                    if got >= s["per_keyword"]:
                        break
            log(f"   🛍️ '{kw}': 추천 {got}개")
    finally:
        sc.close()
    save(items=picked, updated_at=dt.datetime.now().isoformat(timespec="minutes"),
         message=f"추천 상품 {len(picked)}개" if picked else "기준에 맞는 상품을 못 찾았어요 (리뷰·수수료 기준을 낮춰 보세요)")
    log(f"🛍️ 쇼핑커넥트 추천 상품 {len(picked)}개를 골랐어요 — 대시보드에서 확인하세요")
    return picked


def add_link(product_id: str, url: str, memo: str = "") -> int:
    """사용자가 직접 발급한 링크를 상품 이름과 함께 대기열에 넣음."""
    s = get()
    p = next((x for x in s.get("items", []) if x["id"] == product_id), None)
    lid = db.run("INSERT INTO links(url, memo, status, product_name, created_at) VALUES(?,?, 'WAITING', ?, ?)",
                 (url, memo or None, p["name"] if p else None, db.now()))
    save(items=[x for x in s.get("items", []) if x["id"] != product_id], done_ids=s.get("done_ids", []) + [product_id])
    return lid


def skip(product_id: str):
    s = get()
    save(items=[x for x in s.get("items", []) if x["id"] != product_id], done_ids=s.get("done_ids", []) + [product_id])


def due() -> bool:
    """하루 한 번 (인기 키워드가 오늘 것으로 바뀐 뒤)."""
    s = get()
    t = trends.get()
    return bool(t.get("items")) and (s.get("updated_at") or "")[:10] != dt.date.today().isoformat() \
        and (s.get("tried") or "") != dt.date.today().isoformat()
