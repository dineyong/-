"""손그림 스타일 일러스트 — illustrations.ts 를 그대로 옮긴 것.

- 두 번 그은 삐뚤빼뚤 연필선, 선 밖으로 살짝 삐져나온 파스텔 색칠, 표정 있는 캐릭터
- SVG 문자열을 만들고 크롬(playwright)으로 PNG를 찍는다
- 실제 상품은 그리지 않는다 (상황·정보 일러스트만)
- 난수·숫자 표기까지 TS 버전과 똑같이 맞춰서, 같은 입력이면 같은 그림이 나온다
"""
from __future__ import annotations

import math
import re
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

W = 1080
INK = "#4A3E3A"
SUB = "#786860"
C = {
    "paper": "#FDF9F0", "pink": "#FFC4C8", "mint": "#BEE8D6", "butter": "#FFEAAA", "sky": "#C4E0F6",
    "lav": "#DED0F4", "peach": "#FFD6B8", "wood": "#F2D6AA", "wood2": "#E8C696", "dust": "#D6D0C8",
    "white": "#FFFFFF", "red": "#E86E78", "green": "#46966F",
}
FONT = ("'Nanum Pen Script','나눔손글씨 펜','NanumPen','Gaegu','Apple SD Gothic Neo','Malgun Gothic',"
        "'Noto Sans CJK KR','Noto Sans KR',sans-serif")
SCENE_TYPES = ["desk", "kitchen", "living", "vanity", "closet", "bedroom", "bathroom", "travel", "veranda"]

M32 = 0xFFFFFFFF


# ─────────────────────────────────────────────
# 자바스크립트와 똑같이 동작하는 도구들
# ─────────────────────────────────────────────
def rng(seed: float):
    """mulberry32 — TS의 rng()와 같은 수열."""
    a = int(seed) & M32

    def nxt() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & M32
        t = ((a ^ (a >> 15)) * (1 | a)) & M32
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & M32)) & M32) ^ t
        return ((t ^ (t >> 14)) & M32) / 4294967296

    return nxt


def hash_seed(s: str) -> int:
    h = 2166136261
    for ch in s:
        code = ord(ch)
        if code > 0xFFFF:                       # JS charCodeAt(0) = 상위 서로게이트
            code = 0xD800 + ((code - 0x10000) >> 10)
        h = ((h ^ code) * 16777619) & M32
    return h


def f1(n: float) -> str:
    """JS toFixed(1)."""
    neg = n < 0
    d = Decimal(abs(n)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return ("-" if neg else "") + f"{d:.1f}"


def jn(n: float) -> str:
    """JS 숫자 → 문자열 (템플릿에 그대로 넣을 때)."""
    if float(n).is_integer():
        return str(int(n))
    return repr(float(n))


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


Pt = tuple


def jitter(pts: list, amt: float, r, closed: bool = True) -> list:
    out = []
    last = len(pts) if closed else len(pts) - 1
    for i in range(last):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        n = max(2, math.floor(math.hypot(x2 - x1, y2 - y1) / 18))
        for k in range(n):
            t = k / n
            out.append((x1 + (x2 - x1) * t + (r() * 2 - 1) * amt, y1 + (y2 - y1) * t + (r() * 2 - 1) * amt))
    if not closed:
        out.append((pts[-1][0] + (r() * 2 - 1) * amt, pts[-1][1] + (r() * 2 - 1) * amt))
    return out


def pts_str(p: list) -> str:
    return " ".join(f"{f1(x)},{f1(y)}" for x, y in p)


def shape(pts: list, fill: str | None = None, w: int = 5, amt: float = 2.2, seed: float = 1, closed: bool = True) -> str:
    r = rng(seed)
    s = ""
    if fill:
        fp = [(x + 7, y + 6) for x, y in jitter(pts, amt, r)]
        s += f'<polygon points="{pts_str(fp)}" fill="{fill}"/>'
    for pas in range(2):
        p = jitter(pts, amt, r, closed)
        if closed:
            p = p + [p[0]]
        s += (f'<polyline points="{pts_str(p)}" fill="none" stroke="{INK}" stroke-width="{jn(w - pas * 2)}" '
              f'stroke-linejoin="round" stroke-linecap="round"/>')
    return s


def ell(cx, cy, rx, ry, n: int = 36) -> list:
    return [(cx + rx * math.cos((2 * math.pi * i) / n), cy + ry * math.sin((2 * math.pi * i) / n)) for i in range(n)]


def rrect(x1, y1, x2, y2, rad=20, n: int = 6) -> list:
    pts = []
    for cx, cy, a0 in ((x2 - rad, y1 + rad, -90), (x2 - rad, y2 - rad, 0), (x1 + rad, y2 - rad, 90), (x1 + rad, y1 + rad, 180)):
        for i in range(n + 1):
            a = ((a0 + (90 * i) / n) * math.pi) / 180
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    return pts


def arc(cx, cy, rx, ry, a0, a1, color: str = INK, w: int = 4) -> str:
    pts = []
    for i in range(17):
        a = ((a0 + ((a1 - a0) * i) / 16) * math.pi) / 180
        pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
    return f'<polyline points="{pts_str(pts)}" fill="none" stroke="{color}" stroke-width="{w}" stroke-linecap="round"/>'


def text(x, y, s: str, size, fill: str = INK, weight: int = 600, seed: float | None = None, anchor: str = "middle") -> str:
    r = rng(hash_seed(s) if seed is None else seed)
    chars = list(s)
    rot = " ".join(f1((r() * 2 - 1) * 6) for _ in chars)
    prev = 0.0
    dys = []
    for _ in chars:
        v = (r() * 2 - 1) * 3
        dys.append(f1(v - prev))
        prev = v
    return (f'<text x="{f1(x)}" y="{f1(y)}" font-family="{FONT}" font-size="{jn(size)}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}" rotate="{rot}" dy="{" ".join(dys)}">{esc(s)}</text>')


_KO = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")


def approx_width(s: str, size: float) -> float:
    w = 0.0
    for ch in s:
        w += size * 0.95 if _KO.match(ch) else size * 0.33 if ch == " " else size * 0.55
    return w


def wrap(s: str, size: float, max_w: float, max_lines: int = 2) -> list:
    lines, cur = [], ""
    for w in s.split(" "):
        nxt = f"{cur} {w}" if cur else w
        if approx_width(nxt, size) > max_w and cur:
            lines.append(cur)
            cur = w
        else:
            cur = nxt
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        kept = lines[:max_lines]
        kept[-1] = re.sub(r".$", "…", kept[-1])
        return kept
    return lines


def face(cx, cy, s: float = 1, mood: str = "sad", blush: bool = True) -> str:
    o = (f'<ellipse cx="{f1(cx - 14 * s)}" cy="{f1(cy)}" rx="5" ry="5" fill="{INK}"/>'
         f'<ellipse cx="{f1(cx + 14 * s)}" cy="{f1(cy)}" rx="5" ry="5" fill="{INK}"/>')
    if blush:
        o += (f'<ellipse cx="{f1(cx - 30 * s)}" cy="{f1(cy + 13)}" rx="9" ry="5" fill="#FFAAAA"/>'
              f'<ellipse cx="{f1(cx + 30 * s)}" cy="{f1(cy + 13)}" rx="9" ry="5" fill="#FFAAAA"/>')
    o += arc(cx, cy + 6, 9, 7, 20, 160) if mood == "happy" else arc(cx, cy + 20, 8, 6, 200, 340)
    return o


def sparkle(x, y, s, color: str = "#FFBE5A") -> str:
    return (f'<line x1="{jn(x - s)}" y1="{jn(y)}" x2="{jn(x + s)}" y2="{jn(y)}" stroke="{color}" stroke-width="4" stroke-linecap="round"/>'
            f'<line x1="{jn(x)}" y1="{jn(y - s)}" x2="{jn(x)}" y2="{jn(y + s)}" stroke="{color}" stroke-width="4" stroke-linecap="round"/>')


def heart(x, y, s, color: str = C["pink"], seed: float = 1) -> str:
    pts = []
    for i in range(30):
        t = (i / 30) * 2 * math.pi
        pts.append((x + s * 16 * math.pow(math.sin(t), 3) / 16,
                    y - s * (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)) / 16))
    return shape(pts, fill=color, seed=seed, w=4, amt=1)


def buddy(cx, cy, r, seed, mood: str = "sad", fill: str = C["dust"], fuzzy: bool = True) -> str:
    rr = rng(seed)
    pts = []
    for i in range(22):
        px = cx + (r + (rr() * 14 - 6)) * math.cos((2 * math.pi * i) / 22)
        py = cy + (r * 0.8 + (rr() * 14 - 6)) * math.sin((2 * math.pi * i) / 22)
        pts.append((px, py))
    s = shape(pts, fill=fill, seed=seed, amt=3 if fuzzy else 1.5)
    if fuzzy:
        for _ in range(8):
            a = rr() * 2 * math.pi
            x, y = cx + (r + 4) * math.cos(a), cy + (r * 0.8 + 4) * math.sin(a)
            s += (f'<line x1="{f1(x)}" y1="{f1(y)}" x2="{f1(x + 10 * math.cos(a))}" y2="{f1(y + 10 * math.sin(a))}" '
                  f'stroke="{INK}" stroke-width="3" stroke-linecap="round"/>')
    return s + face(cx, cy - 4, max(0.5, r / 70), mood)


def paper(body: str, caption: bool = True) -> str:
    cap = text(W / 2, 1040, "이해를 돕기 위한 일러스트예요", 30, fill="#A09690", weight=400, seed=99) if caption else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{W}" viewBox="0 0 {W} {W}">\n'
            '<defs><filter id="grain"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="3"/>'
            '<feColorMatrix values="0 0 0 0 0.55  0 0 0 0 0.5  0 0 0 0 0.42  0 0 0 0.07 0"/></filter></defs>\n'
            f'<rect width="{W}" height="{W}" fill="{C["paper"]}"/><rect width="{W}" height="{W}" filter="url(#grain)"/>\n'
            f'{body}{cap}</svg>')


def speech(cx, cy, rx, ry, tail, main: str, sub: str, seed) -> str:
    s = shape(ell(cx, cy, rx, ry, 44), fill=C["white"], seed=seed)
    s += shape(tail, fill=C["white"], seed=seed + 1)
    s += text(cx, cy - 8, main, 50, weight=700, seed=seed + 2)
    if sub:
        s += text(cx, cy + 46, sub, 31, fill=SUB, weight=500, seed=seed + 3)
    return s


# ─────────────────────────────────────────────
# 1) 썸네일
# ─────────────────────────────────────────────
def thumbnail_svg(t: dict) -> str:
    b = shape(rrect(90, 90, 990, 990, 50), fill=C["white"], seed=1)
    tag_w = max(220, approx_width(t["tag"], 42) + 80)
    b += shape(rrect(W / 2 - tag_w / 2, 200, W / 2 + tag_w / 2, 268, 34), fill=C["pink"], seed=2)
    b += text(W / 2, 250, t["tag"], 42, weight=700, seed=3)
    s1 = min(110, math.floor(760 / max(1, approx_width(t["line1"], 1))))
    s2 = min(96, math.floor(760 / max(1, approx_width(t["line2"], 1))))
    b += text(W / 2, 430, t["line1"], s1, weight=800, seed=4)
    b += (f'<rect x="{f1(W / 2 - approx_width(t["line2"], s2) / 2 - 10)}" y="{jn(560 - s2 * 0.35)}" '
          f'width="{f1(approx_width(t["line2"], s2) + 20)}" height="{f1(s2 * 0.38)}" fill="{C["butter"]}" rx="8"/>')
    b += text(W / 2, 560, t["line2"], s2, weight=800, fill=C["green"], seed=5)
    b += shape([(330, 640), (750, 640)], closed=False, seed=6, w=5)
    b += buddy(270, 800, 78, 11, "happy", C["mint"], False)
    b += buddy(810, 805, 66, 12, "happy", C["lav"], False)
    b += heart(520, 790, 46, C["pink"], 13) + heart(610, 745, 28, C["peach"], 14)
    b += sparkle(180, 170, 14) + sparkle(910, 690, 12) + sparkle(420, 900, 9) + sparkle(900, 180, 10)
    return paper(b, False)


# ─────────────────────────────────────────────
# 2) 도입 장면 (상품 없이 상황만)
# ─────────────────────────────────────────────
def _table() -> str:
    return (shape([(70, 600), (1010, 600), (1010, 650), (70, 650)], fill=C["wood"], seed=5)
            + shape([(120, 650), (160, 650), (160, 945), (120, 945)], fill=C["wood2"], seed=6)
            + shape([(920, 650), (960, 650), (960, 945), (920, 945)], fill=C["wood2"], seed=7))


def _window() -> str:
    return (shape(rrect(650, 110, 950, 370, 16), fill=C["sky"], seed=1)
            + shape([(800, 115), (800, 365)], closed=False, seed=2)
            + shape([(655, 240), (945, 240)], closed=False, seed=3))


def _floor() -> str:
    return shape([(20, 960), (1060, 960)], closed=False, seed=50)


def _mug(x, y) -> str:
    return (shape(rrect(x, y, x + 90, y + 100, 14), fill=C["white"], seed=11)
            + arc(x + 98, y + 50, 26, 26, -90, 90, INK, 6)
            + face(x + 45, y + 45, 0.7, "happy"))


def _desk() -> str:
    b = _window()
    b += shape(rrect(560, 500, 640, 600, 12), fill=C["peach"], seed=4)
    for i, (a, h) in enumerate([(-30, -48), (0, -60), (30, -48)]):
        b += shape(ell(600 + a, 500 + h, 18, 30, 14), fill=C["mint"], seed=10 + i)
    b += _table()
    b += shape(rrect(240, 390, 500, 590, 14), fill=C["lav"], seed=8)
    b += shape([(210, 592), (530, 592), (555, 612), (185, 612)], fill="#DCD6E2", seed=9)
    b += _mug(700, 500)
    b += buddy(150, 548, 52, 21) + buddy(880, 555, 42, 22) + buddy(540, 572, 30, 23)
    return b + _floor()


def _kitchen() -> str:
    b = _window()
    b += shape([(60, 600), (1020, 600), (1020, 950), (60, 950)], fill=C["white"], seed=5)
    b += shape([(60, 590), (1020, 590), (1020, 625), (60, 625)], fill=C["wood"], seed=6)
    for i in range(3):
        b += shape(rrect(110 + i * 310, 680, 380 + i * 310, 920, 14), fill=C["sky"] if i == 1 else C["white"], seed=40 + i)
    b += shape(rrect(130, 540, 470, 600, 20), fill="#E4E8EE", seed=12)
    for i, (x, y) in enumerate([(200, 520), (260, 505), (320, 492), (380, 480)]):
        b += shape(ell(x, y, 62, 16, 24), fill=[C["white"], C["pink"], C["white"], C["mint"]][i], seed=30 + i)
    b += shape(ell(700, 560, 90, 26, 28), fill="#B9B2AE", seed=13)
    b += shape([(790, 552), (930, 540), (932, 556), (792, 568)], fill=C["wood2"], seed=14)
    b += buddy(560, 545, 36, 24, "sad", C["butter"], False)
    return b


def _living() -> str:
    b = _window()
    b += shape([(930, 440), (960, 440), (960, 900), (930, 900)], fill=C["wood2"], seed=3)
    b += shape([(870, 330), (1020, 330), (985, 440), (905, 440)], fill=C["butter"], seed=4)
    b += shape(rrect(120, 560, 820, 860, 50), fill=C["lav"], seed=5)
    b += shape(rrect(90, 640, 200, 900, 40), fill="#CDBDEB", seed=6)
    b += shape(rrect(740, 640, 850, 900, 40), fill="#CDBDEB", seed=7)
    b += shape(rrect(210, 700, 730, 880, 30), fill="#E9DEFA", seed=8)
    b += shape(rrect(250, 600, 420, 700, 30), fill=C["pink"], seed=9)
    b += shape(ell(470, 945, 380, 36, 40), fill=C["mint"], seed=10)
    b += buddy(560, 650, 40, 25, "sad")
    b += buddy(300, 930, 34, 26, "sad")
    return b


def _vanity() -> str:
    b = shape(ell(540, 330, 200, 250, 48), fill=C["sky"], seed=1)
    b += shape(ell(540, 330, 160, 210, 48), fill="#E6F2FB", seed=2)
    b += _table()
    for i, (x, top, w, col) in enumerate([(220, 470, 80, C["pink"]), (320, 500, 70, C["mint"]),
                                           (760, 450, 90, C["lav"]), (860, 510, 60, C["peach"])]):
        b += shape(rrect(x, top, x + w, 600, 16), fill=col, seed=20 + i)
        b += shape(rrect(x + w * 0.3, top - 40, x + w * 0.7, top + 4, 8), fill=C["white"], seed=30 + i)
    b += buddy(540, 330, 70, 27, "sad", C["butter"], False)
    return b + _floor()


def _closet() -> str:
    b = shape(rrect(140, 90, 940, 940, 20), fill=C["wood"], seed=1)
    b += shape(rrect(180, 130, 900, 900, 14), fill="#FFF6E8", seed=2)
    b += shape([(200, 210), (880, 210)], closed=False, seed=3, w=7)
    for i, col in enumerate([C["sky"], C["pink"], C["mint"], C["lav"], C["butter"]]):
        x = 250 + i * 125
        b += arc(x + 45, 222, 14, 14, 180, 360, INK, 4)
        b += shape([(x, 240), (x + 90, 240), (x + 100, 520), (x - 10, 520)], fill=col, seed=10 + i)
    b += shape(rrect(230, 640, 850, 880, 16), fill=C["white"], seed=4)
    b += buddy(540, 760, 56, 28, "sad", C["peach"], False)
    return b


def _bedroom() -> str:
    b = _window()
    b += shape(ell(880, 200, 40, 40, 24), fill=C["butter"], seed=4)
    b += shape(ell(900, 190, 34, 34, 24), fill=C["sky"], seed=5, w=1)
    b += shape(rrect(90, 520, 170, 940, 20), fill=C["wood2"], seed=6)
    b += shape(rrect(150, 640, 860, 860, 26), fill=C["white"], seed=7)
    b += shape(rrect(170, 600, 380, 690, 40), fill=C["pink"], seed=8)
    b += shape([(330, 650), (860, 650), (880, 870), (300, 870)], fill=C["lav"], seed=9)
    b += shape(rrect(150, 860, 860, 900, 10), fill=C["wood"], seed=10)
    b += shape(rrect(880, 700, 1010, 900, 14), fill=C["wood"], seed=11)
    b += shape([(905, 600), (985, 600), (965, 690), (925, 690)], fill=C["butter"], seed=12)
    b += buddy(560, 700, 52, 29, "sad", C["sky"], False)
    return b + _floor()


def _bathroom() -> str:
    b = ""
    y = 80
    while y < 600:
        x = 40 + math.fmod(y / 90, 2) * 45
        while x < 1040:
            b += shape(rrect(x, y, x + 80, y + 80, 8), fill="#EAF4F8", seed=x * 7 + y, w=3, amt=1.2)
            x += 90
        y += 90
    b += shape(ell(540, 330, 150, 180, 40), fill=C["sky"], seed=1)
    b += shape(ell(540, 330, 115, 145, 40), fill="#F2F8FC", seed=2)
    b += shape(rrect(330, 600, 750, 680, 30), fill=C["white"], seed=3)
    b += shape(rrect(420, 680, 660, 940, 20), fill=C["white"], seed=4)
    b += shape(rrect(820, 420, 960, 760, 20), fill=C["mint"], seed=5)
    b += shape(rrect(380, 530, 440, 600, 10), fill=C["pink"], seed=6)
    b += shape([(395, 530), (400, 470), (410, 470), (408, 530)], fill=C["butter"], seed=7, w=3)
    b += buddy(540, 330, 66, 30, "sad", C["butter"], False)
    return b + _floor()


def _travel() -> str:
    b = _window()
    b += shape(rrect(130, 560, 620, 900, 30), fill=C["peach"], seed=1)
    b += shape(rrect(150, 580, 600, 880, 22), fill="#FFF1E6", seed=2)
    b += shape(rrect(150, 330, 600, 555, 26), fill="#FFC9A6", seed=3)
    b += shape(rrect(175, 355, 575, 530, 18), fill="#FFE3D2", seed=33, w=3)
    b += shape(rrect(330, 296, 420, 334, 14), fill=C["white"], seed=34, w=4)
    b += shape(rrect(190, 640, 360, 740, 16), fill=C["sky"], seed=4)
    b += shape(rrect(380, 620, 560, 720, 16), fill=C["pink"], seed=5)
    b += shape(rrect(220, 760, 400, 850, 16), fill=C["mint"], seed=6)
    b += shape(rrect(700, 720, 820, 880, 12), fill="#9CC3A4", seed=7)
    b += shape(ell(760, 790, 26, 26, 20), fill=C["butter"], seed=8, w=3)
    b += shape([(850, 700), (1010, 680), (1020, 880), (860, 900)], fill=C["white"], seed=9)
    b += shape([(880, 760), (930, 730), (980, 800)], closed=False, seed=10, w=3)
    b += buddy(470, 800, 40, 31, "sad", C["lav"], False)
    return b + _floor()


def _veranda() -> str:
    b = shape(rrect(60, 90, 1020, 520, 20), fill="#E3F1FA", seed=1)
    b += shape([(540, 95), (540, 515)], closed=False, seed=2)
    b += shape([(120, 180), (960, 180)], closed=False, seed=3, w=4)
    for i, (x, col) in enumerate([(200, C["pink"]), (340, C["sky"]), (480, C["butter"]), (700, C["mint"]), (840, C["lav"])]):
        b += shape([(x, 185), (x + 90, 185), (x + 80, 320), (x + 10, 320)], fill=col, seed=20 + i)
    for i, (x, w, col) in enumerate([(140, 120, C["peach"]), (330, 90, C["pink"]), (800, 110, C["butter"])]):
        b += shape(rrect(x, 760, x + w, 900, 14), fill=col, seed=30 + i)
        for j, (k, h) in enumerate([(-0.2, -90), (0.5, -130), (1.1, -80)]):
            b += shape(ell(x + w * k + 20, 760 + h * 0.6, 20, 42, 14), fill=C["mint"], seed=40 + i * 3 + j)
    b += shape(rrect(560, 800, 700, 900, 20), fill=C["sky"], seed=50)
    b += shape([(700, 820), (780, 770), (790, 785), (705, 840)], fill=C["sky"], seed=51)
    b += buddy(500, 860, 40, 32, "sad", C["butter"], False)
    return b + _floor()


SCENES = {"desk": _desk, "kitchen": _kitchen, "living": _living, "vanity": _vanity, "closet": _closet,
          "bedroom": _bedroom, "bathroom": _bathroom, "travel": _travel, "veranda": _veranda}


def scene_svg(scene: str, bubble: dict) -> str:
    b = SCENES.get(scene, _desk)()
    rx = min(330, max(240, approx_width(bubble["main"], 50) / 2 + 70, approx_width(bubble.get("sub", ""), 31) / 2 + 60))
    cx = max(rx + 30, 330)
    b += speech(cx, 200, rx, 112, [(cx - 120, 290), (cx - 70, 298), (cx - 145, 360)], bubble["main"], bubble.get("sub", ""), 31)
    b += sparkle(980, 470, 14) + sparkle(1020, 420, 9)
    return paper(b)


# ─────────────────────────────────────────────
# 3) 체크리스트 / 4) 추천 대상
# ─────────────────────────────────────────────
def checklist_svg(items: list, title: str = "고를 때 체크 포인트") -> str:
    b = text(W / 2, 140, title, 66, weight=800, seed=1)
    b += shape([(330, 175), (750, 175)], closed=False, seed=2, w=6)
    for i, it in enumerate(items[:4]):
        y = 240 + i * 175
        b += shape(rrect(100, y, 980, y + 145, 28), fill=[C["mint"], C["pink"], C["sky"], C["butter"]][i % 4], seed=10 + i)
        b += shape(rrect(140, y + 40, 205, y + 105, 10), fill=C["white"], seed=20 + i, w=4)
        b += (f'<polyline points="150,{y + 72} 168,{y + 94} 205,{y + 38}" fill="none" stroke="{C["red"]}" '
              f'stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>')
        b += text(245, y + 68, it["label"], 46, weight=800, anchor="start", seed=30 + i)
        desc = (wrap(it.get("desc", ""), 32, 680, 1) or [""])[0]
        b += text(245, y + 118, desc, 32, fill=SUB, weight=500, anchor="start", seed=40 + i)
    b += buddy(960, 960, 44, 51, "happy", C["lav"], False)
    return paper(b)


def fit_svg(fit: dict, titles: dict | None = None) -> str:
    titles = titles or {"head": "이런 분께 맞아요", "good": "잘 맞아요", "check": "한 번 더 확인"}
    b = text(W / 2, 140, titles["head"], 66, weight=800, seed=1)

    def col(x, title, color, items, seed):
        s = shape(rrect(x, 210, x + 430, 900, 34), fill=C["white"], seed=seed)
        s += shape(rrect(x, 210, x + 430, 310, 34), fill=color, seed=seed + 1)
        s += text(x + 215, 278, title, 46, weight=800, seed=seed + 2)
        y = 380
        for i, it in enumerate(items[:4]):
            lines = wrap(it, 34, 330, 2)
            s += heart(x + 46, y - 14, 15, color, seed + 10 + i)
            for k, ln in enumerate(lines):
                s += text(x + 80, y + k * 46, ln, 34, weight=600, anchor="start", seed=seed + 20 + i * 3 + k)
            y += len(lines) * 46 + 44
        return s

    b += col(90, titles["good"], C["mint"], fit.get("good", []), 10)
    b += col(560, titles["check"], C["peach"], fit.get("check", []), 60)
    b += sparkle(80, 120, 12) + sparkle(1000, 110, 10)
    return paper(b)


# ─────────────────────────────────────────────
# 5) 단계 / 6) 팁 / 7) 자주 묻는 질문 / 8) 마무리
# ─────────────────────────────────────────────
STEP_COLORS = [C["mint"], C["pink"], C["sky"], C["butter"], C["lav"]]


def step_svg(n: int, step: dict) -> str:
    col = STEP_COLORS[(n - 1) % len(STEP_COLORS)]
    b = shape(rrect(90, 90, 990, 990, 50), fill=C["white"], seed=100 + n)
    b += shape(ell(250, 260, 110, 110, 40), fill=col, seed=110 + n)
    b += text(250, 300, str(n), 130, weight=800, seed=120 + n)
    b += text(400, 225, f"STEP {n}", 40, fill=SUB, weight=700, anchor="start", seed=130 + n)
    for i, ln in enumerate(wrap(step["title"], 64, 520, 2)):
        b += text(400, 300 + i * 74, ln, 64, weight=800, anchor="start", seed=140 + n + i)
    b += shape([(160, 450), (920, 450)], closed=False, seed=150 + n, w=4)
    for i, ln in enumerate(wrap(step.get("desc", ""), 54, 740, 4)):
        b += text(170, 560 + i * 82, ln, 54, weight=600, fill=INK, anchor="start", seed=160 + n + i)
    b += buddy(840, 880, 70, 170 + n, "happy", col, False)
    b += sparkle(160, 920, 12) + sparkle(640, 930, 9) + heart(720, 860, 24, C["pink"], 180 + n)
    return paper(b, False)


def tip_svg(tip_text: str) -> str:
    b = '<g transform="rotate(-3 540 540)">'
    b += shape(rrect(150, 170, 930, 900, 18), fill=C["butter"], seed=201)
    b += '<rect x="440" y="140" width="200" height="60" fill="#E9DFF7" opacity="0.85" transform="rotate(4 540 170)"/>'
    b += shape(ell(270, 300, 46, 52, 28), fill="#FFF6C8", seed=202)
    b += shape(rrect(250, 350, 290, 385, 8), fill="#D9D2C8", seed=203, w=3)
    b += text(345, 325, "TIP", 72, weight=800, anchor="start", seed=204, fill=C["red"])
    for i, ln in enumerate(wrap(tip_text, 58, 660, 5)):
        b += text(215, 490 + i * 88, ln, 58, weight=700, anchor="start", seed=210 + i)
    b += "</g>"
    b += buddy(900, 930, 52, 220, "happy", C["mint"], False) + sparkle(120, 140, 12) + sparkle(980, 200, 10)
    return paper(b, False)


def faq_svg(items: list) -> str:
    b = text(W / 2, 140, "자주 묻는 질문", 66, weight=800, seed=301)
    b += shape([(350, 175), (730, 175)], closed=False, seed=302, w=6)
    y = 230
    for i, it in enumerate(items[:2]):
        ql = wrap(it["q"], 42, 600, 2)
        al = wrap(it["a"], 40, 600, 3)
        qh = 70 + len(ql) * 54
        b += shape(rrect(110, y, 840, y + qh, 36), fill=C["pink"], seed=310 + i)
        b += shape(ell(160, y + qh / 2, 34, 34, 24), fill=C["white"], seed=320 + i, w=4)
        b += text(160, y + qh / 2 + 15, "Q", 42, weight=800, fill=C["red"], seed=330 + i)
        for k, ln in enumerate(ql):
            b += text(215, y + 64 + k * 54, ln, 42, weight=700, anchor="start", seed=340 + i * 4 + k)
        y += qh + 26
        ah = 60 + len(al) * 52
        b += shape(rrect(240, y, 970, y + ah, 36), fill=C["mint"], seed=350 + i)
        b += shape(ell(290, y + ah / 2, 34, 34, 24), fill=C["white"], seed=360 + i, w=4)
        b += text(290, y + ah / 2 + 15, "A", 42, weight=800, fill=C["green"], seed=370 + i)
        for k, ln in enumerate(al):
            b += text(345, y + 58 + k * 52, ln, 40, weight=600, anchor="start", seed=380 + i * 4 + k)
        y += ah + 40
    b += buddy(130, 960, 44, 390, "happy", C["lav"], False)
    return paper(b, False)


def outro_svg(o: dict) -> str:
    b = shape(rrect(110, 140, 970, 700, 50), fill=C["white"], seed=401)
    ml = wrap(o["main"], 70, 760, 2)
    for i, ln in enumerate(ml):
        b += text(W / 2, 330 + i * 86, ln, 70, weight=800, seed=410 + i)
    b += text(W / 2, 330 + len(ml) * 86 + 50, o.get("sub", ""), 46, fill=SUB, weight=600, seed=420)
    b += buddy(300, 860, 74, 430, "happy", C["mint"], False)
    b += buddy(540, 880, 62, 431, "happy", C["pink"], False)
    b += buddy(780, 860, 74, 432, "happy", C["sky"], False)
    b += heart(420, 760, 26, C["pink"], 433) + heart(670, 750, 20, C["peach"], 434)
    b += sparkle(160, 120, 12) + sparkle(940, 760, 12) + sparkle(130, 760, 9)
    return paper(b, False)


# ─────────────────────────────────────────────
# AI가 준 그림 문구 정리 (길이 제한·기본값)
# ─────────────────────────────────────────────
def normalize(v: Any, name: str, title: str) -> dict:
    v = v if isinstance(v, dict) else {}

    def cut(s: Any, n: int) -> str:
        s = re.sub(r"\s*(ㅎ{2,}|ㅋ{2,}|ㅠ{2,}|ㅜ{2,})", "", str(s or ""))
        return re.sub(r"\s+", " ", s).strip()[:n]

    short_name = cut(re.split(r"[,(\[]", name or "")[0], 12)
    th = v.get("thumbnail") or {}
    out: dict = {
        "thumbnail": {
            "tag": cut(th.get("tag"), 8) or "장단점 정리",
            "line1": cut(th.get("line1"), 11) or short_name,
            "line2": cut(th.get("line2"), 12) or cut((title.split(",") + ["", ""])[1], 12) or "구매 전 체크",
        },
        "scene": v.get("scene") if v.get("scene") in SCENE_TYPES else "desk",
        "bubble": {"main": cut((v.get("bubble") or {}).get("main"), 13) or "이거 은근 불편하네…",
                   "sub": cut((v.get("bubble") or {}).get("sub"), 18)},
        "checklist": [c for c in ({"label": cut(x.get("label"), 6), "desc": cut(x.get("desc"), 18)}
                                  for x in (v.get("checklist") or []) if isinstance(x, dict)) if c["label"]][:4],
        "fit": {"good": [cut(s, 22) for s in ((v.get("fit") or {}).get("good") or []) if cut(s, 22)][:3],
                "check": [cut(s, 22) for s in ((v.get("fit") or {}).get("check") or []) if cut(s, 22)][:3]},
        "steps": [s for s in ({"title": cut(x.get("title"), 16), "desc": cut(x.get("desc"), 60)}
                              for x in (v.get("steps") or []) if isinstance(x, dict)) if s["title"]][:3],
        "tip": cut(v.get("tip"), 70),
        "faq": [f for f in ({"q": cut(x.get("q"), 30), "a": cut(x.get("a"), 60)}
                            for x in (v.get("faq") or []) if isinstance(x, dict)) if f["q"] and f["a"]][:2],
        "outro": None,
    }
    o = v.get("outro") or {}
    if isinstance(o, dict) and o.get("main"):
        out["outro"] = {"main": cut(o["main"], 18), "sub": cut(o.get("sub"), 22)}
    return out


# ─────────────────────────────────────────────
# PNG 만들기
# ─────────────────────────────────────────────
INFO_TITLES = {"head": "이것만 기억해요", "good": "이렇게 해요", "check": "이건 주의"}


def build_jobs(spec: dict, kind: str, card_px: int, thumb_px: int) -> list:
    info = kind == "info"
    jobs = [("thumbnail", thumbnail_svg(spec["thumbnail"]), thumb_px),
            ("scene", scene_svg(spec["scene"], spec["bubble"]), card_px)]
    if spec["checklist"]:
        jobs.append(("checklist", checklist_svg(spec["checklist"], "한눈에 정리" if info else "고를 때 체크 포인트"), card_px))
    for i, st in enumerate(spec.get("steps") or []):
        jobs.append((f"step{i + 1}", step_svg(i + 1, st), card_px))
    if spec.get("tip"):
        jobs.append(("tip", tip_svg(spec["tip"]), card_px))
    if spec.get("faq"):
        jobs.append(("faq", faq_svg(spec["faq"]), card_px))
    if spec["fit"]["good"]:
        jobs.append(("fit", fit_svg(spec["fit"], INFO_TITLES if info else None), card_px))
    if spec.get("outro"):
        jobs.append(("outro", outro_svg(spec["outro"]), card_px))
    return jobs


def render(context, spec: dict, out_dir: Path, kind: str, card_px: int = 480, thumb_px: int = 600, log=print) -> dict:
    """playwright 브라우저 context에서 SVG를 PNG로 찍는다. {이름: 파일경로}"""
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {}
    page = context.new_page()
    try:
        for key, svg, px in build_jobs(spec, kind, card_px, thumb_px):
            try:
                sized = svg.replace(f'width="{W}" height="{W}"', f'width="{px}" height="{px}"', 1)
                page.set_viewport_size({"width": px, "height": px})
                page.set_content('<!doctype html><html><head><meta charset="utf-8">'
                                 '<style>html,body{margin:0;background:#fff}</style></head><body>' + sized + "</body></html>")
                page.wait_for_timeout(200)
                f = out_dir / f"{key}_{int(time.time() * 1000)}.png"
                page.locator("svg").first.screenshot(path=str(f))
                result[key] = str(f)
                log(f"   🎨 일러스트 생성: {key} ({px}px)")
            except Exception as e:  # noqa: BLE001
                log(f"   ⚠️ 일러스트 생성 실패({key}): {e}")
    finally:
        page.close()
    return result


def resize_photos(context, paths: list, out_dir: Path, px: int = 480) -> list:
    """상품 사진을 가로 px로 줄인다 (크롬으로 그려서 찍음). 실패하면 원본."""
    import base64
    out = []
    page = context.new_page()
    try:
        for p in paths:
            try:
                data = base64.b64encode(Path(p).read_bytes()).decode()
                page.set_viewport_size({"width": px, "height": px * 3})
                page.set_content(f'<!doctype html><html><body style="margin:0"><img id="i" style="width:{px}px;display:block" '
                                 f'src="data:image/jpeg;base64,{data}"></body></html>')
                page.wait_for_selector("#i")
                page.wait_for_timeout(150)
                f = out_dir / f"small_{int(time.time() * 1000)}_{len(out)}.jpg"
                page.locator("#i").screenshot(path=str(f), type="jpeg", quality=88)
                out.append(str(f))
            except Exception:  # noqa: BLE001
                out.append(p)
    finally:
        page.close()
    return out
