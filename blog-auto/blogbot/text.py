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


# 여기서 끊으면 자연스러운 말끝 (쉼표·연결 어미·조사). "들어갈 / 때마다"처럼 꾸미는 말 뒤에서는 안 끊음
_GOOD_END = re.compile(r"(,|요|고|서|데|면|며|니까|지만|는데|면서|거든요|라서|해서|어서|아서|으며|해도|려면|때|을|를|에|에서|으로|로|도|까지|부터|만|과|와|랑|께)$")


def blog_lines(text: str) -> str:
    """문장마다 줄을 바꾸고, 긴 문장은 12~28자 근처의 자연스러운 말끝(~고, ~서, ~는데, 쉼표, 조사)에서 끊는다."""
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
            words, cur = sen.split(" "), ""
            for i, w in enumerate(words):
                cur = f"{cur} {w}" if cur else w
                rest = " ".join(words[i + 1:])
                if not rest:
                    break
                nxt_len = len(cur) + 1 + len(words[i + 1])
                # 14자 넘었고 말끝이 자연스러우면 끊기 / 너무 길어지면(28자) 어쩔 수 없이 끊기
                if (len(cur) >= 12 and _GOOD_END.search(w) and len(rest) >= 6) or nxt_len > 28:
                    out.append(cur)
                    cur = ""
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
