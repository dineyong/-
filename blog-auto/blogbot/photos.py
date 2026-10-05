"""글 분위기 사진 — AI 생성(Gemini 이미지) 또는 무료 스톡(Pexels·Pixabay) 중 주제에 더 맞는 쪽.

- 글 AI가 visual.photos 에 사진마다 source("ai"/"stock"), 영어 검색어(query), 영어 장면 설명(prompt)을 준다
- stock: 키가 있는 스톡 사이트에서 후보를 모아 Gemini가 사진을 직접 보고 가장 잘 맞는 한 장을 고름 (없으면 AI로)
- ai: Gemini 이미지 모델로 생성 (실패하면 스톡으로)
- 둘 다 안 되면 None → 기존 손그림 장면이 대신 들어감
- 상품 자체나 '사용하는 척' 하는 장면은 만들지 않는다 (가짜 사용 사진 금지 원칙)
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config
from .log import log

GEN_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
UA = "blogbot (+https://github.com/dineyong/-)"

AI_STYLE = ("Natural lifestyle photograph taken in an ordinary Korean home, soft daylight from a window, "
            "slightly imperfect and lived-in, shot on a phone camera at eye level, realistic colors, shallow depth of field. "
            "No text, no letters, no logos, no brand names, no watermark, no people's faces (hands are fine).")


def _req(url: str, data: dict | None = None, headers: dict | None = None, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8") if data is not None else None,
                                 headers={"User-Agent": UA, **({"Content-Type": "application/json"} if data else {}),
                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=config.SSL) as r:
        return r.read()


def _save(raw: bytes, out_dir: Path, name: str, ext: str = "jpg") -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / f"{name}_{int(time.time() * 1000)}.{ext}"
    f.write_bytes(raw)
    return str(f)


def _parts(data: dict) -> list:
    return (data.get("candidates") or [{}])[0].get("content", {}).get("parts", []) or []


# ─────────────────────────────────────────────
# AI 생성
# ─────────────────────────────────────────────
def generate(prompt: str, out_dir: Path, name: str, shop: bool = False) -> str | None:
    key = config.get("GEMINI_API_KEY").strip()
    if not key or not prompt:
        return None
    rule = (" Do not show any product, package or device being sold; show only the everyday situation or place."
            if shop else "")
    text = f"{prompt.strip()}\n\nStyle: {AI_STYLE}{rule}"
    models = [m.strip() for m in config.get("GEMINI_IMAGE_MODELS").split(",") if m.strip()]
    for model in models:
        for modal in (["IMAGE"], ["TEXT", "IMAGE"]):     # 이미지만 받는 걸 못 하는 모델이 있음
            body = {"contents": [{"role": "user", "parts": [{"text": text}]}],
                    "generationConfig": {"responseModalities": modal, "imageConfig": {"aspectRatio": "4:3"}}}
            try:
                data = json.loads(_req(GEN_API.format(model=model), body, {"x-goog-api-key": key}, 180))
            except urllib.error.HTTPError as e:
                if e.code == 400 and modal == ["IMAGE"]:
                    continue
                log(f"   ↪️ 이미지 모델 {model} 실패({e.code}) → 다음")
                break
            except Exception as e:  # noqa: BLE001
                log(f"   ↪️ 이미지 모델 {model} 실패({e}) → 다음")
                break
            for p in _parts(data):
                inline = p.get("inlineData") or p.get("inline_data")
                if inline and inline.get("data"):
                    mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                    path = _save(base64.b64decode(inline["data"]), out_dir, name, "png" if "png" in mime else "jpg")
                    log(f"   🖼️ AI 사진 생성: {name} ({model})")
                    return path
            log(f"   ↪️ 이미지 모델 {model}가 그림을 안 줌 → 다음")
            break
    return None


# ─────────────────────────────────────────────
# 무료 스톡 (Pexels / Pixabay — 둘 다 상업적 사용·출처 표시 없이 사용 가능)
# ─────────────────────────────────────────────
def _pexels(query: str, n: int) -> list[dict]:
    key = config.get("PEXELS_API_KEY").strip()
    if not key:
        return []
    url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode(
        {"query": query, "per_page": n, "orientation": "landscape", "locale": "ko-KR"})
    data = json.loads(_req(url, headers={"Authorization": key}, timeout=20))
    return [{"src": "pexels", "preview": p["src"]["medium"], "full": p["src"]["large"]} for p in data.get("photos", [])]


def _pixabay(query: str, n: int) -> list[dict]:
    key = config.get("PIXABAY_API_KEY").strip()
    if not key:
        return []
    url = "https://pixabay.com/api/?" + urllib.parse.urlencode(
        {"key": key, "q": query[:100], "per_page": max(3, n), "image_type": "photo", "orientation": "horizontal",
         "safesearch": "true", "order": "popular"})
    data = json.loads(_req(url, timeout=20))
    return [{"src": "pixabay", "preview": h["webformatURL"], "full": h.get("largeImageURL") or h["webformatURL"]}
            for h in data.get("hits", [])[:n]]


def has_stock() -> bool:
    return bool(config.get("PEXELS_API_KEY").strip() or config.get("PIXABAY_API_KEY").strip())


def _pick(cands: list[dict], want: str) -> int:
    """Gemini가 후보 사진을 직접 보고 글에 가장 맞는 번호를 고름. 맞는 게 없으면 -1."""
    key = config.get("GEMINI_API_KEY").strip()
    parts: list = [{"text": f"블로그 글에 넣을 사진을 고르려고 해요. 원하는 장면: {want}\n"
                            "아래 사진들 중 이 장면에 가장 잘 맞고, 글자·워터마크·어색한 연출이 없는 사진 번호 하나를 고르세요. "
                            "외국 느낌이 너무 강하거나 장면과 상관없으면 고르지 마세요.\n"
                            'JSON만: {"pick": 번호(0부터), 맞는 게 없으면 -1}'}]
    for i, c in enumerate(cands):
        try:
            raw = _req(c["preview"], timeout=20)
        except Exception:  # noqa: BLE001
            continue
        parts += [{"text": f"사진 {i}"}, {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(raw).decode()}}]
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    data = json.loads(_req(GEN_API.format(model=config.get("GEMINI_MODEL")), body, {"x-goog-api-key": key}, 120))
    txt = "".join(p.get("text", "") for p in _parts(data) if not p.get("thought"))
    n = int(json.loads(txt).get("pick", -1))
    return n if 0 <= n < len(cands) else -1


def stock(query: str, want: str, out_dir: Path, name: str) -> str | None:
    if not query or not has_stock():
        return None
    cands: list[dict] = []
    for fn in (_pexels, _pixabay):
        try:
            cands += fn(query, 4)
        except Exception as e:  # noqa: BLE001
            log(f"   ⚠️ 스톡 검색 실패({fn.__name__[1:]}): {e}")
    if not cands:
        log(f"   ↪️ 스톡에서 '{query}' 사진을 못 찾음")
        return None
    try:
        i = _pick(cands, want or query)
    except Exception as e:  # noqa: BLE001
        log(f"   ⚠️ 사진 고르기 실패({e}) → 첫 번째 후보 사용")
        i = 0
    if i < 0:
        log(f"   ↪️ 스톡 후보 {len(cands)}장 중 주제에 맞는 사진이 없음")
        return None
    try:
        path = _save(_req(cands[i]["full"], timeout=30), out_dir, name)
    except Exception as e:  # noqa: BLE001
        log(f"   ⚠️ 스톡 사진 받기 실패: {e}")
        return None
    log(f"   🖼️ 스톡 사진 선택: {name} ({cands[i]['src']}, 후보 {len(cands)}장 중)")
    return path


# ─────────────────────────────────────────────
# 글 한 편의 사진들
# ─────────────────────────────────────────────
def normalize(v) -> list[dict]:
    out = []
    for x in (v if isinstance(v, list) else [])[:2]:
        if not isinstance(x, dict):
            continue
        src = "stock" if str(x.get("source", "")).lower() == "stock" else "ai"
        q, p = str(x.get("query") or "").strip()[:80], str(x.get("prompt") or "").strip()[:600]
        if q or p:
            out.append({"source": src, "query": q, "prompt": p or q})
    return out


def make(specs: list[dict], out_dir: Path, shop: bool = False) -> list[str | None]:
    """specs 순서대로 사진 경로 (못 구하면 None). 글에서 고른 쪽을 먼저, 안 되면 다른 쪽."""
    result: list[str | None] = []
    if config.get("PHOTO_MODE").strip().lower() == "off":
        return [None] * len(specs)
    for i, s in enumerate(specs):
        name = f"photo{i + 1}"
        tries = [lambda: stock(s["query"], s["prompt"], out_dir, name),
                 lambda: generate(s["prompt"], out_dir, name, shop)]
        if s["source"] == "ai":
            tries.reverse()
        path = None
        for t in tries:
            try:
                path = t()
            except Exception as e:  # noqa: BLE001
                log(f"   ⚠️ 사진 만들기 실패: {e}")
            if path:
                break
        result.append(path)
    return result
