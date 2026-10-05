"""Gemini 호출 — 추가 설치 없이 표준 라이브러리(urllib)만 사용.

- 붐비면(429/500/503) 5초·15초 기다렸다 재시도, 그래도 안 되면 다음 모델
- 모델이 없어졌으면(404) 바로 다음 모델
- 인터넷이 끊겼으면 모델을 바꾸지 않고 1분씩 최대 5번 기다림
"""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request

from . import config
from .log import log

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class AIError(Exception):
    pass


class OfflineError(AIError):
    """인터넷 연결 문제 — 실패 횟수에 세지 않는다."""


def online(timeout: float = 8) -> bool:
    try:
        urllib.request.urlopen(urllib.request.Request(
            "https://generativelanguage.googleapis.com/", method="HEAD"), timeout=timeout, context=config.SSL)
        return True
    except urllib.error.HTTPError:
        return True   # 서버가 대답했으면(404 등) 인터넷은 되는 것
    except Exception:
        return False


def _call(model: str, key: str, system: str, user: str, json_mode: bool) -> str:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.75, "maxOutputTokens": 16384},
    }
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    req = urllib.request.Request(
        API.format(model=model),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180, context=config.SSL) as r:
        data = json.loads(r.read().decode("utf-8"))
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise AIError(f"빈 응답 (finishReason={(data.get('candidates') or [{}])[0].get('finishReason')})")
    return text


def generate(system: str, user: str, json_mode: bool = True) -> str:
    cfg = config.load()
    key = cfg["GEMINI_API_KEY"].strip()
    if not key:
        raise AIError("Gemini API 키가 없어요. 대시보드 → 설정에서 넣어주세요.")
    models = [cfg["GEMINI_MODEL"]] + [m.strip() for m in cfg["GEMINI_FALLBACK_MODELS"].split(",") if m.strip()]
    waits = [5, 15]
    offline_tries = 0
    last: Exception | None = None

    for model in models:
        attempt = 0
        while attempt <= len(waits):
            log(f"   🤖 {model} 호출 ({attempt + 1}번째 시도)")
            try:
                return _call(model, key, system, user, json_mode)
            except urllib.error.HTTPError as e:
                last = e
                detail = e.read().decode("utf-8", "ignore")[:300]
                if e.code == 404:
                    log(f"   ↪️ {model} 사용 불가 → 다음 모델")
                    break
                if e.code in (400, 401, 403) and "API key" in detail:
                    raise AIError(f"Gemini API 키가 올바르지 않아요 ({e.code})") from e
                if e.code in (429, 500, 502, 503, 504):
                    if attempt == len(waits):
                        break
                    log(f"   ⏳ 서버가 붐빔({e.code}). {waits[attempt]}초 후 다시 시도...")
                    time.sleep(waits[attempt])
                    attempt += 1
                    continue
                raise AIError(f"AI 오류 {e.code}: {detail}") from e
            except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError) as e:
                last = e
                offline_tries += 1
                if offline_tries > 5:
                    raise OfflineError(f"인터넷 연결이 끊겨 있어요 ({e})") from e
                log(f"   📡 인터넷 연결 안 됨. 60초 후 다시 시도... ({offline_tries}/5)")
                time.sleep(60)
                continue   # 같은 시도 다시
            except AIError as e:   # 빈 응답 등 → 다음 시도
                last = e
                if attempt == len(waits):
                    break
                attempt += 1
        log(f"   ↪️ {model} 계속 안 됨 → 다음 모델로 전환")
    raise AIError(f"모든 AI 모델이 실패했어요: {last}")


def generate_json(system: str, user: str, validate=None, tries: int = 2) -> dict:
    """JSON으로 받기. validate(dict)->bool 이 False면 한 번 더."""
    last = None
    for i in range(tries):
        text = generate(system, user, json_mode=True)
        m = re.search(r"\{[\s\S]*\}", text)
        try:
            data = json.loads(m.group(0) if m else text)
            if validate is None or validate(data):
                return data
            log(f"   ⚠️ AI 응답 형식이 부족해요. 다시 시도 {i + 1}/{tries}")
        except ValueError as e:
            last = e
            log(f"   ⚠️ JSON 해석 실패. 다시 시도 {i + 1}/{tries}")
    raise AIError(f"AI가 올바른 형식을 주지 않았어요 ({last or '형식 부족'})")
