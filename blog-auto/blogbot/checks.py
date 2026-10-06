"""발행 전 검사 — 다 쓴 글이 기본 기준을 넘는지 확인한다.

기준을 못 넘으면(fatal) 한 번 다시 쓰고, 그래도 안 되면 발행하지 않고 이유를 남긴다.
(품질 미달 글을 올리는 것보다 안 올리는 편이 낫다는 원칙)
경고(warn)는 기록만 남기고 그대로 진행한다.
"""
from __future__ import annotations

import re

# 직접 써보지 않은 상품에 붙으면 안 되는 체험·구매 표현
_FAKE_EXP = re.compile(r"내돈내산|직접\s*(써|사용|구매|먹|입어|착용)|써봤|사용해\s*봤|며칠\s*(써|사용)|배송\s*(와서|받|왔)|재구매|구매해서|주문해서|받아보니|열어보니|써보니|사용해보니|한\s*달\s*(써|사용)")
_WON = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{4,})\s*원")


def _won(x) -> int:
    return int(re.sub(r"[^\d]", "", str(x or "")) or 0)


def _tokens(name: str) -> list[str]:
    """상품명에서 확인할 만한 덩어리: 첫 단어(브랜드)와 영문+숫자 모델명."""
    parts = re.findall(r"[A-Za-z]+[A-Za-z0-9\-]*\d[A-Za-z0-9\-]*|[A-Za-z]{2,}\d+|[가-힣A-Za-z0-9]+", name or "")
    out = []
    if parts:
        out.append(parts[0])
    out += [p for p in parts if re.search(r"[A-Za-z]", p) and re.search(r"\d", p) and len(p) >= 3]
    seen = []
    for t in out:
        if t not in seen:
            seen.append(t)
    return seen[:3]


def shop(title: str, sections: list[str], p: dict, has_memo: bool) -> tuple[list[str], list[str]]:
    fatal: list[str] = []
    warn: list[str] = []
    body = "\n".join(sections)
    allv = f"{title}\n{body}"

    if len(sections) < 8:
        fatal.append(f"섹션이 {len(sections)}개뿐이에요 (9개여야 해요)")
    elif len(sections) != 9:
        warn.append(f"섹션이 {len(sections)}개예요 (9개가 기본)")
    chars = len(re.sub(r"\s", "", body))
    if chars < 900:
        fatal.append(f"본문이 너무 짧아요 ({chars}자)")

    # 상품명(브랜드·모델명)이 글에 들어갔는지
    for tok in _tokens(p.get("name", "")):
        if tok.lower() not in allv.lower():
            (fatal if tok == _tokens(p.get("name", ""))[0] else warn).append(f"상품명 '{tok}'이(가) 글에 없어요")

    # 가격: 수집한 값 말고 다른 금액이 나오면 지어낸 것
    known = {_won(p.get(k)) for k in ("price", "original_price")} - {0}
    if known and len(known) == 2:
        known.add(max(known) - min(known))
    for k in ("description", "coupon", "delivery", "discount_rate"):
        known |= {_won(m) for m in _WON.findall(str(p.get(k) or ""))}
    for f in (p.get("features") or []):
        known |= {_won(m) for m in _WON.findall(str(f))}
    known.discard(0)
    bad = sorted({_won(m) for m in _WON.findall(body)} - known)
    if bad and known:
        fatal.append("상품 정보에 없는 금액이 있어요: " + ", ".join(f"{v:,}원" for v in bad[:3]))
    if p.get("price") and str(_won(p["price"])) not in re.sub(r"[,\s]", "", body):
        warn.append("수집한 가격이 글에 안 보여요")

    # 직접 써보지 않은 상품에 체험·구매 표현
    if not has_memo:
        m = _FAKE_EXP.search(allv)
        if m:
            fatal.append(f"써보지 않은 상품인데 체험 표현이 있어요: '{m.group(0)}'")

    # 본문 속 안내 문구·자리표시자 찌꺼기
    if re.search(r"\[(구매링크|상품명|가격|링크)\]|\{\{|\}\}|TODO|lorem", body.replace("[구매링크]", "")):
        fatal.append("자리표시자(예: [상품명])가 남아 있어요")
    return fatal, warn


def info(title: str, sections: list[str]) -> tuple[list[str], list[str]]:
    fatal: list[str] = []
    warn: list[str] = []
    body = "\n".join(sections)
    if len(sections) < 5:
        fatal.append(f"섹션이 {len(sections)}개뿐이에요")
    chars = len(re.sub(r"\s", "", body))
    if chars < 800:
        fatal.append(f"본문이 너무 짧아요 ({chars}자)")
    if _FAKE_EXP.search(body) and not re.search(r"예를 들어|라고들|하시는 분", body):
        warn.append("체험담처럼 읽히는 표현이 있어요")
    if re.search(r"\{\{|\}\}|TODO|lorem", body):
        fatal.append("자리표시자가 남아 있어요")
    return fatal, warn
