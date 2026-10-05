"""설정과 경로.

모든 데이터는 앱 위치와 상관없이 홈 폴더의 ~/BlogAuto 에 저장한다.
(앱을 옮기거나 새 버전으로 바꿔도 로그인·글 목록·설정이 그대로 남도록)
"""
from __future__ import annotations

import os
import re
from pathlib import Path

HOME = Path(os.environ.get("BLOGAUTO_HOME", Path.home() / "BlogAuto"))
DATA = HOME / "data"
LOGS = HOME / "logs"
TEMP = HOME / "temp"
ENV_FILE = HOME / "settings.env"
SESSION_FILE = DATA / "naver-session.json"
DB_FILE = DATA / "blog.db"

# 인터넷 보안 인증서 — python.org 맥 파이썬은 인증서가 비어 있어 https가 전부 실패하는 경우가 있어 certifi 사용
import ssl as _ssl
SSL = _ssl.create_default_context()          # 시스템 인증서
try:
    import certifi as _certifi
    SSL.load_verify_locations(cafile=_certifi.where())   # + certifi 인증서
except Exception:  # noqa: BLE001
    pass

for d in (HOME, DATA, LOGS, TEMP):
    d.mkdir(parents=True, exist_ok=True)

# 기본값 — 대시보드의 '설정'에서 바꿀 수 있음
DEFAULTS: dict[str, str] = {
    "GEMINI_API_KEY": "",
    "NAVER_BLOG_ID": "",
    "GEMINI_MODEL": "gemini-3.8-flash",
    "GEMINI_FALLBACK_MODELS": "gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite",
    "BLOG_TOPIC": "생활 꿀팁 (살림·청소, 정리수납, 계절 준비, 자취·원룸, 침구·수면 습관, 욕실 관리, "
                  "여행 짐싸기, 반려식물·베란다, 홈카페·간단 주방 팁, 절약 생활)",
    "FTC_DISCLOSURE": "이 포스팅은 네이버 쇼핑 커넥트 활동의 일환으로,\n판매가 발생되면 수수료를 제공받을 수 있습니다.",
    "IMAGE_WIDTH": "480",
    "THUMB_WIDTH": "600",
    "SHOPPING_CONNECT_URL": "",
    # 자동 업데이트 창고 (GitHub 공개 저장소의 blog-auto 폴더)
    "UPDATE_URL": "https://raw.githubusercontent.com/dineyong/-/main/blog-auto",
}

# 대시보드에서 바꿀 수 있는 항목 (화면 표시 이름)
EDITABLE = {
    "GEMINI_API_KEY": "Gemini API 키",
    "NAVER_BLOG_ID": "네이버 블로그 아이디 (blog.naver.com/ 뒤 부분)",
    "BLOG_TOPIC": "정보글 주제 범위",
    "FTC_DISCLOSURE": "공정위 문구 (쇼핑글 맨 위)",
    "SHOPPING_CONNECT_URL": "쇼핑커넥트 상품 목록 주소 (브랜드커넥트에서 상품 검색하는 화면 주소)",
}

_LINE = re.compile(r'^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$')


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        if raw.strip().startswith("#"):
            continue
        m = _LINE.match(raw)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        # 따옴표 벗기기 (맥 텍스트 편집기의 둥근 따옴표도)
        if len(val) >= 2 and val[0] in "\"'“”" and val[-1] in "\"'“”":
            val = val[1:-1]
        out[key] = val.replace("\\n", "\n")
    return out


def load() -> dict[str, str]:
    cfg = dict(DEFAULTS)
    if ENV_FILE.exists():
        cfg.update({k: v for k, v in _parse(ENV_FILE.read_text("utf-8")).items() if v != ""})
    return cfg


def save(updates: dict[str, str]) -> dict[str, str]:
    current = _parse(ENV_FILE.read_text("utf-8")) if ENV_FILE.exists() else {}
    current.update({k: str(v) for k, v in updates.items()})
    lines = ["# 블로그 자동화 설정 — 대시보드의 '설정'에서 바꾸는 걸 추천해요"]
    for k, v in current.items():
        lines.append(f'{k}="{v.replace(chr(10), chr(92) + "n")}"')
    ENV_FILE.write_text("\n".join(lines) + "\n", "utf-8")
    return load()


def get(key: str) -> str:
    return load().get(key, "")


OLD_COPY = HOME / "old"   # 터미널로 예전 파일을 복사해 두는 곳 (맥이 '다운로드' 폴더 접근을 막을 때)
OLD_DOWNLOADS = Path.home() / "Downloads" / "naver-bc-automation-main"


def shopping_connect_base() -> str:
    """https://brandconnect.naver.com/{채널번호}/affiliate/products 형태로 정리 (없으면 빈 값)."""
    m = re.search(r"brandconnect\.naver\.com/(\d{6,})", get("SHOPPING_CONNECT_URL"))
    return f"https://brandconnect.naver.com/{m.group(1)}/affiliate/products" if m else ""


def can_see(p: Path) -> bool:
    """존재 확인. 맥 개인정보 보호로 '다운로드' 폴더 접근이 막히면 예외 대신 False."""
    try:
        return p.exists()
    except OSError:
        return False


def import_old_project(folder: Path) -> list[str]:
    """예전 Node 버전 폴더에서 API 키·블로그 아이디·로그인 세션을 가져온다."""
    done: list[str] = []
    try:
        return _import_all(folder, done)
    except OSError as e:          # 다운로드 폴더 접근 거부 등
        done.append(f"(예전 폴더를 읽지 못했어요: {e.strerror or e})")
        return done


def _import_all(folder: Path, done: list) -> list:
    old_env = folder / ".env"
    if old_env.exists():
        old = _parse(old_env.read_text("utf-8"))
        picked = {k: old[k] for k in ("GEMINI_API_KEY", "NAVER_BLOG_ID", "BLOG_TOPIC") if old.get(k)}
        if picked:
            save(picked)
            done.append("설정(" + ", ".join(picked) + ")")
    # 원래 폴더 구조 또는 ~/BlogAuto/old 에 파일만 복사해 둔 경우 둘 다
    old_session = next((p for p in (folder / "playwright" / "storage" / "naver-session.json",
                                    folder / "naver-session.json") if p.exists()), None)
    if old_session and not SESSION_FILE.exists():
        SESSION_FILE.write_bytes(old_session.read_bytes())
        done.append("네이버 로그인")
    try:
        done += _import_old_db(folder)
    except Exception as e:  # noqa: BLE001
        done.append(f"(예전 글 목록은 못 가져옴: {e})")
    return done


def _old_time(v):
    """Prisma SQLite 날짜: 밀리초 숫자 또는 ISO 문자열 → 내 컴퓨터 시각(분 단위 ISO)."""
    import datetime as dt
    if v is None:
        return None
    if isinstance(v, (int, float)) or str(v).isdigit():
        d = dt.datetime.fromtimestamp(int(v) / 1000)
    else:
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if d.tzinfo:
            d = d.astimezone().replace(tzinfo=None)
    return d.isoformat(timespec="minutes")


def _import_old_db(folder: Path) -> list:
    """예전 프로그램의 앞으로 예약된 글(예약 칸이 겹치지 않게)과 대기 중인 쇼핑 링크를 가져온다. 한 번만."""
    import datetime as dt
    import sqlite3
    from . import db
    if db.setting("imported_old_db"):
        return []
    src = next((p for p in (folder / "prisma" / "dev.db", folder / "prisma" / "prisma" / "dev.db",
                            folder / "dev.db") if p.exists()), None)
    if not src:
        return []
    old = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    old.row_factory = sqlite3.Row
    now = dt.datetime.now().isoformat(timespec="minutes")
    n_post = n_link = 0
    for table, kind, name_col in (("Post", "info", "title"), ("BrandLink", "shop", "productName")):
        try:
            rows = old.execute(f'SELECT {name_col} AS name, publishedAt FROM "{table}" WHERE status = \'SCHEDULED\'').fetchall()
        except sqlite3.Error:
            continue
        for r in rows:
            at = _old_time(r["publishedAt"])
            if at and at > now:
                t = db.now()
                db.run("INSERT INTO posts(kind,status,title,scheduled_at,auto,created_at,updated_at) "
                       "VALUES(?,'SCHEDULED',?,?,0,?,?)", (kind, (r["name"] or "") + " (예전 프로그램)", at, t, t))
                n_post += 1
    try:
        for r in old.execute('SELECT url, memo FROM "BrandLink" WHERE status = \'READY\'').fetchall():
            db.run("INSERT INTO links(url,memo,status,created_at) VALUES(?,?,'WAITING',?)", (r["url"], r["memo"], db.now()))
            n_link += 1
    except sqlite3.Error:
        pass
    old.close()
    db.set_setting("imported_old_db", True)
    out = []
    if n_post:
        out.append(f"예약된 글 {n_post}개")
    if n_link:
        out.append(f"대기 링크 {n_link}개")
    return out
