"""작업 기록 — 화면(대시보드)과 파일(~/BlogAuto/logs) 양쪽에 남긴다."""
from __future__ import annotations

import datetime as dt
import threading
from collections import deque

from . import config

_lock = threading.Lock()
RECENT: deque[str] = deque(maxlen=400)   # 대시보드에 보여줄 최근 기록


def _file():
    return config.LOGS / f"{dt.date.today():%Y-%m-%d}.log"


def log(msg: str) -> None:
    line = f"[{dt.datetime.now():%m/%d %H:%M:%S}] {msg}"
    with _lock:
        RECENT.append(line)
        try:
            with _file().open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    print(line, flush=True)


def cleanup_old(days: int = 14) -> None:
    cutoff = dt.date.today() - dt.timedelta(days=days)
    for p in config.LOGS.glob("*.log"):
        try:
            if dt.date.fromisoformat(p.stem) < cutoff:
                p.unlink()
        except ValueError:
            pass
