"""글 분위기 사진 — AI 생성(OpenAI·Gemini 이미지) 또는 무료 스톡(Pexels·Pixabay) 중 주제에 더 맞는 쪽.

- 글 AI가 visual.photos 에 사진마다 source("ai"/"stock"), 영어 검색어(query), 영어 장면 설명(prompt)을 준다
- stock: 키가 있는 스톡 사이트에서 후보를 모아 Gemini가 사진을 직접 보고 가장 잘 맞는 한 장을 고름 (없으면 AI로)
- ai: OpenAI(키가 있을 때) 또는 Gemini 이미지 모델로 생성, AI 티가 나면 한 번 더 (실패하면 스톡으로)
- 둘 다 안 되면 None → 기존 손그림 장면이 대신 들어감
- 상품 자체나 '사용하는 척' 하는 장면은 만들지 않는다 (가짜 사용 사진 금지 원칙)
"""
from __future__ import annotations

import base64
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config
from .log import log

OPENAI_API = "https://api.openai.com/v1/images/generations"
GEN_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
UA = "blogbot (+https://github.com/dineyong/-)"

# 글마다 같은 느낌이 반복되지 않게 분위기·구도를 여러 개 두고 한 장마다 하나씩 고른다
LOOKS = [
    "soft morning daylight from a window, eye-level phone snapshot",
    "warm late-afternoon light with long soft shadows, slightly angled shot",
    "bright overcast daylight, even soft light, top-down view",
    "cozy evening indoor lamp light, warm tones, close-up with blurry background",
    "clean midday light, wide shot showing the whole space",
    "cool early-morning light, quiet mood, shot from a low angle",
]
AI_BASE = ("Natural, candid photo of an ordinary Korean home or neighborhood, lived-in and slightly imperfect, "
           "realistic colors and textures like a real phone photo, not a polished ad or 3D render. "
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
def _gemini_image(text: str) -> tuple[bytes, str, str] | None:
    key = config.get("GEMINI_API_KEY").strip()
    if not key:
        return None
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
                    return base64.b64decode(inline["data"]), "png" if "png" in mime else "jpg", model
            log(f"   ↪️ 이미지 모델 {model}가 그림을 안 줌 → 다음")
            break
    return None


def _openai_image(text: str) -> tuple[bytes, str, str] | None:
    """ChatGPT(OpenAI) 이미지 모델 — 설정에 OpenAI 키가 있을 때만."""
    key = config.get("OPENAI_API_KEY").strip()
    if not key:
        return None
    models = [m.strip() for m in config.get("OPENAI_IMAGE_MODELS").split(",") if m.strip()]
    for model in models:
        body = {"model": model, "prompt": text[:3800], "size": "1536x1024", "quality": "medium",
                "output_format": "jpeg", "n": 1}
        try:
            data = json.loads(_req(OPENAI_API, body, {"Authorization": f"Bearer {key}"}, 240))
        except urllib.error.HTTPError as e:
            log(f"   ↪️ OpenAI 이미지 모델 {model} 실패({e.code}) → 다음")
            continue
        except Exception as e:  # noqa: BLE001
            log(f"   ↪️ OpenAI 이미지 모델 {model} 실패({e}) → 다음")
            continue
        b64 = ((data.get("data") or [{}])[0] or {}).get("b64_json")
        if b64:
            return base64.b64decode(b64), "jpg", model
        log(f"   ↪️ OpenAI 이미지 모델 {model}가 그림을 안 줌 → 다음")
    return None


def _looks_real(raw: bytes, ext: str, want: str) -> bool:
    """Gemini가 만든 사진을 다시 보고 AI 티(이상한 손·깨진 글자·너무 매끈함)가 나는지 확인. 확인 못 하면 통과."""
    key = config.get("GEMINI_API_KEY").strip()
    if not key or config.get("PHOTO_CHECK").strip().lower() == "off":
        return True
    body = {"contents": [{"role": "user", "parts": [
        {"text": f"블로그에 넣을 사진이에요. 원하는 장면: {want}\n"
                 "실제 사람이 폰으로 찍은 사진처럼 자연스러운지 보세요. 아래 중 하나라도 있으면 bad:\n"
                 "- 손가락·사물 모양이 뒤틀리거나 녹아내린 부분\n- 읽을 수 있거나 깨진 글자·로고·워터마크\n"
                 "- 광고나 3D 렌더처럼 지나치게 매끈하고 완벽한 느낌\n- 원하는 장면과 상관없는 내용\n"
                 'JSON만: {"ok": true 또는 false, "why": "짧게"}'},
        {"inlineData": {"mimeType": "image/png" if ext == "png" else "image/jpeg",
                        "data": base64.b64encode(raw).decode()}}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    try:
        data = json.loads(_req(GEN_API.format(model=config.get("GEMINI_MODEL")), body, {"x-goog-api-key": key}, 120))
        res = json.loads("".join(p.get("text", "") for p in _parts(data) if not p.get("thought")))
    except Exception as e:  # noqa: BLE001
        log(f"   ⚠️ 사진 검사 건너뜀: {e}")
        return True
    if res.get("ok") is False:
        log(f"   ↪️ AI 티가 나서 다시 만듦: {str(res.get('why') or '')[:60]}")
        return False
    return True


def generate(prompt: str, out_dir: Path, name: str, shop: bool = False) -> str | None:
    """OpenAI 키가 있으면 OpenAI 먼저, 아니면 Gemini. AI 티가 나면 한 번 더 만들고, 그래도 나면 None(스톡·손그림으로)."""
    if not prompt:
        return None
    rule = (" Do not show any product, package or device being sold; show only the everyday situation or place."
            if shop else "")
    for _ in range(2):
        text = f"{prompt.strip()}\n\nStyle: {random.choice(LOOKS)}. {AI_BASE}{rule}"
        got = None
        for mk in (_openai_image, _gemini_image):
            got = mk(text)
            if got:
                break
        if not got:
            return None
        raw, ext, model = got
        if _looks_real(raw, ext, prompt):
            path = _save(raw, out_dir, name, ext)
            log(f"   🖼️ AI 사진 생성: {name} ({model})")
            return path
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
            cands += fn(query, 8)
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
