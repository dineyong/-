"""본문 정리 — 에디터에 넣기 전에 마크다운·채팅체 제거, 블로그식 줄바꿈."""
from __future__ import annotations

import re

_PERSON = re.compile(
    "[\U0001F466-\U0001F487\U0001F645-\U0001F64F\U0001F926\U0001F937][‍♀♂️]*"
)


def clean_for_editor(text: str) -> str:
    text = re.sub(r"\s*(ㅎ{2,}|ㅋ{2,}|ㅠ{2,}|ㅜ{2,})", "", text)       # ㅎㅎ ㅋㅋ ㅠㅠ
    text = re.sub("[\U0001F3FB-\U0001F3FF]", "", text)                 # 피부색
    text = _PERSON.sub("", text)                                        # 🙋‍♀️ 같은 사람 이모지
    text = re.sub("‍[♀♂]️?", "", text)             # 남은 성별 조각
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)                        # **굵게**
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)                    # 인용
    text = re.sub(r"^\s*#{1,6}\s+", "", text, flags=re.M)               # 헤더
    return re.sub(r"\n{4,}", "\n\n\n", text)


_KEEP = re.compile(r"^(\s*$|[#📌✔✅💚🔎👇💰🎯⚠🤔📷※(]|[①②③④⑤⑥⑦⑧⑨]|https?://|제품명|가격|\d+\.|-\s)")


_CLAUSE = re.compile(r"(,|고|서|데|면|지만|는데|니까|으며|며|라서|해도|려면|할 때|때)$")


def blog_lines(text: str) -> str:
    """문장마다 줄을 바꾸고, 긴 문장은 말이 끊기는 자리(~고, ~서, ~는데, 쉼표)에서 나눈다.
    글자 수로 기계적으로 자르면 '어디서 끊겼지?' 싶은 줄이 생겨서, 자연스러운 곳이 없을 때만 길이로 자른다."""
    out: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if _KEEP.match(line) or len(line) <= 28:
            out.append(line)
            continue
        for sen in re.split(r"(?<=[.!?…~])\s+", line):
            if len(sen) <= 30:
                out.append(sen)
                continue
            cur = ""
            for w in sen.split(" "):
                nxt = f"{cur} {w}" if cur else w
                if cur and (len(nxt) > 32 or (len(cur) >= 12 and _CLAUSE.search(cur) and len(sen) - len(cur) > 8)):
                    out.append(cur)
                    cur = w
                else:
                    cur = nxt
            if cur:
                out.append(cur)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


def tidy(sections: list[str]) -> list[str]:
    return [blog_lines(clean_for_editor(str(s))) for s in sections]


def tags(raw: list, banned: str | None = None) -> list[str]:
    out = []
    for t in raw or []:
        t = re.sub(r"\s+", "", str(t).lstrip("#"))
        if t and not (banned and re.search(banned, t)):
            out.append(t)
    return out
