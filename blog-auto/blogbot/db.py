"""SQLite 저장소.

posts: 정보글·쇼핑글 모두 (kind = 'info' | 'shop')
links: 쇼핑커넥트 링크 대기열
settings: 키-값 (자동 켜짐 여부 등)
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from typing import Any

from . import config

_lock = threading.RLock()
_conn = sqlite3.connect(config.DB_FILE, check_same_thread=False, isolation_level=None)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")

_conn.executescript(
    """
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,                 -- info | shop
  status TEXT NOT NULL DEFAULT 'READY', -- READY | WRITING | SCHEDULED | PUBLISHED | FAILED
  topic TEXT,
  title TEXT,
  link_id INTEGER,
  scheduled_at TEXT,                  -- 네이버 예약 공개 시각 (ISO)
  post_url TEXT,
  error TEXT,
  auto INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  memo TEXT,
  status TEXT NOT NULL DEFAULT 'WAITING', -- WAITING | USED | FAILED
  product_name TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
)

# 나중에 추가한 칸 (예전 DB에도 붙여줌)
_NEW_COLS = {
    "mode": "TEXT",          # schedule(예약) | now(즉시)
    "note": "TEXT",          # 실패는 아니지만 알려줄 말 (예: 완료 확인이 늦음)
    "body": "TEXT",          # AI가 쓴 초안 (말투 학습 때 사용자가 고친 최종본과 비교)
    "learned": "INTEGER",    # 말투 학습에 반영했는지
    "account": "INTEGER NOT NULL DEFAULT 1",   # 어느 블로그 계정 (1~3)
}
_have = {r[1] for r in _conn.execute("PRAGMA table_info(posts)")}
for _c, _t in _NEW_COLS.items():
    if _c not in _have:
        _conn.execute(f"ALTER TABLE posts ADD COLUMN {_c} {_t}")
_LINK_COLS = {
    "account": "INTEGER NOT NULL DEFAULT 1",   # 어느 계정의 링크인지 (쇼핑커넥트 링크는 발급한 계정 것)
    "used_post": "INTEGER",                    # 이 링크로 쓴 글
    "used_at": "TEXT",
}
_have = {r[1] for r in _conn.execute("PRAGMA table_info(links)")}
for _c, _t in _LINK_COLS.items():
    if _c not in _have:
        _conn.execute(f"ALTER TABLE links ADD COLUMN {_c} {_t}")


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def q(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in _conn.execute(sql, args).fetchall()]


def one(sql: str, args: tuple = ()) -> dict[str, Any] | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def run(sql: str, args: tuple = ()) -> int:
    with _lock:
        cur = _conn.execute(sql, args)
        return cur.lastrowid or cur.rowcount


# ── 글 ──────────────────────────────────────
def new_post(kind: str, topic: str | None = None, link_id: int | None = None, auto: bool = True,
             account: int = 1) -> int:
    t = now()
    return run(
        "INSERT INTO posts(kind,status,topic,link_id,auto,account,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (kind, "WRITING", topic, link_id, 1 if auto else 0, account, t, t),
    )


def update_post(pid: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    run(f"UPDATE posts SET {cols} WHERE id=?", (*fields.values(), pid))


# ── 설정 ────────────────────────────────────
def setting(key: str, default: Any = None) -> Any:
    row = one("SELECT value FROM settings WHERE key=?", (key,))
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except ValueError:
        return default


def set_setting(key: str, value: Any) -> None:
    run("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False)))
