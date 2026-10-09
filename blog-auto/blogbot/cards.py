"""깔끔한 정보형 카드 — 손그림 카드(illustrations.py) 대신 쓰는 두 번째 스타일.

- 캐릭터·하트·반짝이 없이, 네이버 매거진형 블로그에서 흔히 보는 단정한 카드
- 글마다 제목으로 색 테마를 골라서 매번 같은 색으로 보이지 않게 한다
- 입력(spec)은 illustrations.normalize() 결과를 그대로 쓴다
- 실제 상품은 그리지 않는다 (정보만)
"""
from __future__ import annotations

from .illustrations import W, approx_width, esc, hash_seed, wrap

FONT = ("'Pretendard','Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',"
        "'Noto Sans CJK KR',sans-serif")

# (이름, 진한 색, 옅은 바탕, 아주 옅은 바탕)
THEMES = [
    ("sage", "#3F7A5E", "#E3EFE8", "#F4F8F5"),
    ("terracotta", "#C0603F", "#F6E3DA", "#FBF4F0"),
    ("navy", "#2F4A7A", "#DFE6F3", "#F3F6FB"),
    ("mustard", "#B07C12", "#F5EBCF", "#FBF7EC"),
    ("plum", "#7A4A72", "#EEDFEC", "#F8F2F7"),
    ("teal", "#1F7480", "#D9EEF0", "#F1F8F9"),
]
INK = "#23211F"
SUB = "#6B6661"
LINE = "#E4E0DA"

SCENE_LABEL = {"desk": "책상 앞에서", "kitchen": "부엌에서", "living": "거실에서", "vanity": "화장대 앞에서",
               "closet": "옷장 앞에서", "bedroom": "침실에서", "bathroom": "욕실에서", "travel": "여행 준비하다가",
               "veranda": "베란다에서"}


def theme(seed_text: str) -> tuple:
    return THEMES[hash_seed(seed_text or "") % len(THEMES)]


def t(x, y, s: str, size, fill: str = INK, weight: int = 700, anchor: str = "start", ls: float = -0.5) -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}" letter-spacing="{ls}">{esc(s)}</text>')


def fit(s: str, size: int, max_w: float, min_size: int = 40) -> int:
    while size > min_size and approx_width(s, size) * 0.92 > max_w:
        size -= 2
    return size


def frame(body: str, bg: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{W}" viewBox="0 0 {W} {W}">\n'
            f'<rect width="{W}" height="{W}" fill="{bg}"/>\n{body}</svg>')


def pill(x, y, label: str, fg: str, bg: str, size: int = 34) -> str:
    w = approx_width(label, size) * 0.92 + 56
    return (f'<rect x="{x}" y="{y}" width="{w:.1f}" height="{size + 30}" rx="{(size + 30) / 2}" fill="{bg}"/>'
            + t(x + w / 2, y + size + 6, label, size, fg, 700, "middle", 0))


def header(title: str, th: tuple, kicker: str = "") -> str:
    _, dark, soft, _ = th
    s = ""
    if kicker:
        s += t(100, 150, kicker, 34, dark, 700, ls=1)
    s += t(100, 230, title, fit(title, 72, 880), INK, 800)
    s += f'<rect x="100" y="262" width="96" height="10" rx="5" fill="{dark}"/>'
    return s


# ─────────────────────────────────────────────
# 카드들
# ─────────────────────────────────────────────
def thumbnail_svg(tb: dict, th: tuple) -> str:
    _, dark, soft, pale = th
    b = f'<rect x="0" y="0" width="{W}" height="{W}" fill="{soft}"/>'
    b += f'<circle cx="930" cy="170" r="250" fill="{pale}" opacity="0.7"/>'
    b += f'<circle cx="120" cy="980" r="180" fill="{dark}" opacity="0.08"/>'
    b += pill(100, 300, tb["tag"], "#FFFFFF", dark, 38)
    s1 = fit(tb["line1"], 124, 880, 72)
    s2 = fit(tb["line2"], 108, 880, 64)
    b += t(100, 500, tb["line1"], s1, INK, 800, ls=-2)
    b += t(100, 500 + s2 + 34, tb["line2"], s2, dark, 800, ls=-2)
    b += f'<rect x="100" y="{560 + s2 + 40}" width="140" height="12" rx="6" fill="{INK}"/>'
    return frame(b, soft)


def scene_svg(scene: str, bubble: dict, th: tuple) -> str:
    _, dark, soft, pale = th
    b = f'<rect x="80" y="80" width="920" height="920" rx="44" fill="#FFFFFF"/>'
    b += pill(150, 170, SCENE_LABEL.get(scene, "생활 속에서"), dark, soft, 32)
    b += t(140, 470, "“", 260, soft, 800)
    lines = wrap(bubble["main"], 92, 760, 2)
    for i, ln in enumerate(lines):
        b += t(150, 520 + i * 116, ln, fit(ln, 92, 780, 60), INK, 800, ls=-2)
    y = 520 + len(lines) * 116 + 20
    if bubble.get("sub"):
        b += t(150, y, bubble["sub"], 46, SUB, 500)
    b += f'<rect x="150" y="870" width="780" height="2" fill="{LINE}"/>'
    b += t(150, 930, "이럴 때 꼭 필요한 정보만 모았어요", 34, dark, 600, ls=0)
    return frame(b, pale)


def checklist_svg(items: list, title: str, th: tuple) -> str:
    _, dark, soft, pale = th
    b = header(title, th, "CHECK POINT")
    top = 340 + (4 - len(items[:4])) * 60
    for i, it in enumerate(items[:4]):
        y = top + i * 165
        b += f'<rect x="100" y="{y}" width="880" height="140" rx="28" fill="#FFFFFF"/>'
        b += f'<circle cx="172" cy="{y + 70}" r="36" fill="{dark}"/>'
        b += (f'<polyline points="156,{y + 71} 168,{y + 84} 190,{y + 58}" fill="none" stroke="#FFFFFF" '
              'stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>')
        b += t(240, y + 64, it["label"], 46, INK, 800)
        desc = (wrap(it.get("desc", ""), 32, 700, 1) or [""])[0]
        b += t(240, y + 112, desc, 32, SUB, 500, ls=0)
    return frame(b, pale)


def fit_svg(fv: dict, titles: dict, th: tuple) -> str:
    _, dark, soft, pale = th
    b = header(titles["head"], th, "FOR YOU")

    rows = [[wrap(it, 36, 330, 2) for it in fv.get(k, [])[:3]] for k in ("good", "check")]
    hgt = max(sum(len(ls) * 50 + 46 for ls in r) for r in rows) + 170

    def col(x, title, mark, color, bg, items):
        s = f'<rect x="{x}" y="340" width="420" height="{hgt}" rx="32" fill="#FFFFFF"/>'
        s += f'<rect x="{x}" y="340" width="420" height="110" rx="32" fill="{bg}"/>'
        s += f'<rect x="{x}" y="410" width="420" height="40" fill="{bg}"/>'
        s += t(x + 40, 413, f"{mark}  {title}", 44, color, 800)
        y = 530
        for it in items[:3]:
            lines = wrap(it, 36, 330, 2)
            s += f'<circle cx="{x + 52}" cy="{y - 12}" r="7" fill="{color}"/>'
            for k, ln in enumerate(lines):
                s += t(x + 76, y + k * 50, ln, 36, INK, 600, ls=0)
            y += len(lines) * 50 + 46
        return s

    b += col(100, titles["good"], "○", dark, soft, fv.get("good", []))
    b += col(560, titles["check"], "△", "#9A5B2E", "#F3E6DA", fv.get("check", []))
    return frame(b, pale)


def step_svg(n: int, total: int, step: dict, th: tuple) -> str:
    _, dark, soft, pale = th
    b = f'<rect x="80" y="80" width="920" height="920" rx="44" fill="#FFFFFF"/>'
    b += t(150, 330, f"{n:02d}", 210, soft, 800, ls=-6)
    b += t(160, 180, f"STEP {n}", 36, dark, 800, ls=2)
    for i in range(total):
        b += f'<rect x="{770 + i * 60}" y="154" width="44" height="12" rx="6" fill="{dark if i < n else LINE}"/>'
    tl = wrap(step["title"], 70, 780, 2)
    for i, ln in enumerate(tl):
        b += t(150, 450 + i * 88, ln, fit(ln, 70, 780, 50), INK, 800, ls=-1.5)
    y = 450 + len(tl) * 88 + 10
    b += f'<rect x="150" y="{y}" width="780" height="2" fill="{LINE}"/>'
    for i, ln in enumerate(wrap(step.get("desc", ""), 46, 760, 5)):
        b += t(150, y + 90 + i * 72, ln, 46, "#3F3B37", 500, ls=0)
    return frame(b, pale)


def tip_svg(tip_text: str, th: tuple) -> str:
    _, dark, soft, pale = th
    b = f'<rect x="80" y="200" width="920" height="680" rx="44" fill="#FFFFFF"/>'
    b += f'<rect x="80" y="200" width="24" height="680" rx="12" fill="{dark}"/>'
    b += pill(170, 290, "TIP", "#FFFFFF", dark, 40)
    b += t(330, 336, "알아두면 편해요", 40, SUB, 600, ls=0)
    for i, ln in enumerate(wrap(tip_text, 56, 740, 5)):
        b += t(170, 480 + i * 84, ln, 56, INK, 700, ls=-1)
    return frame(b, soft)


def faq_svg(items: list, th: tuple) -> str:
    _, dark, soft, pale = th
    b = header("자주 묻는 질문", th, "Q&A")
    y = 340
    for it in items[:2]:
        ql = wrap(it["q"], 42, 720, 2)
        al = wrap(it["a"], 38, 720, 3)
        h = 70 + len(ql) * 56 + 30 + len(al) * 54 + 30
        b += f'<rect x="100" y="{y}" width="880" height="{h}" rx="28" fill="#FFFFFF"/>'
        b += t(150, y + 80, "Q", 46, dark, 800)
        for k, ln in enumerate(ql):
            b += t(210, y + 80 + k * 56, ln, 42, INK, 800, ls=-0.5)
        ay = y + 80 + len(ql) * 56 + 30
        b += f'<rect x="150" y="{ay - 34}" width="780" height="2" fill="{LINE}"/>'
        b += t(150, ay + 30, "A", 46, SUB, 800)
        for k, ln in enumerate(al):
            b += t(210, ay + 30 + k * 54, ln, 38, "#3F3B37", 500, ls=0)
        y += h + 34
    return frame(b, pale)


def outro_svg(o: dict, th: tuple) -> str:
    _, dark, soft, pale = th
    b = f'<circle cx="540" cy="540" r="420" fill="{soft}"/>'
    ml = wrap(o["main"], 80, 760, 2)
    y0 = 520 - (len(ml) - 1) * 50
    for i, ln in enumerate(ml):
        b += t(540, y0 + i * 100, ln, fit(ln, 80, 780, 54), INK, 800, "middle", -2)
    y = y0 + len(ml) * 100
    if o.get("sub"):
        b += t(540, y + 10, o["sub"], 44, SUB, 600, "middle", 0)
    b += f'<rect x="490" y="{y + 70}" width="100" height="10" rx="5" fill="{dark}"/>'
    return frame(b, pale)


# ─────────────────────────────────────────────
# illustrations.build_jobs 와 같은 모양
# ─────────────────────────────────────────────
INFO_TITLES = {"head": "이것만 기억해요", "good": "이렇게 해요", "check": "이건 주의"}
SHOP_TITLES = {"head": "이런 분께 맞아요", "good": "잘 맞아요", "check": "한 번 더 확인"}


def build_jobs(spec: dict, kind: str, card_px: int, thumb_px: int, seed_text: str = "") -> list:
    info = kind == "info"
    th = theme(seed_text or spec["thumbnail"]["line1"] + spec["thumbnail"]["line2"])
    steps = spec.get("steps") or []
    jobs = [("thumbnail", thumbnail_svg(spec["thumbnail"], th), thumb_px),
            ("scene", scene_svg(spec["scene"], spec["bubble"], th), card_px)]
    if spec["checklist"]:
        jobs.append(("checklist", checklist_svg(spec["checklist"], "한눈에 정리" if info else "고를 때 체크 포인트", th), card_px))
    for i, st in enumerate(steps):
        jobs.append((f"step{i + 1}", step_svg(i + 1, len(steps), st, th), card_px))
    if spec.get("tip"):
        jobs.append(("tip", tip_svg(spec["tip"], th), card_px))
    if spec.get("faq"):
        jobs.append(("faq", faq_svg(spec["faq"], th), card_px))
    if spec["fit"]["good"]:
        jobs.append(("fit", fit_svg(spec["fit"], INFO_TITLES if info else SHOP_TITLES, th), card_px))
    if spec.get("outro"):
        jobs.append(("outro", outro_svg(spec["outro"], th), card_px))
    return jobs
