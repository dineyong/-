"""자동 업데이트용으로 GitHub 저장소 폴더에 내보내기:  python3 mac/release.py <저장소폴더> "바뀐 점"

<저장소폴더>/blog-auto/ 에 blogbot 코드 + manifest.json(버전·파일별 sha256) + mac 스크립트를 넣는다.
버전은 blogbot/__init__.py 의 __version__ — 올릴 때마다 꼭 올릴 것 (같거나 낮으면 앱이 안 받음).
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from blogbot import __version__  # noqa: E402

repo = Path(sys.argv[1]).resolve()
notes = sys.argv[2] if len(sys.argv) > 2 else ""
out = repo / "blog-auto"
shutil.rmtree(out / "blogbot", ignore_errors=True)
shutil.copytree(ROOT / "blogbot", out / "blogbot", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
shutil.rmtree(out / "mac", ignore_errors=True)
shutil.copytree(ROOT / "mac", out / "mac", ignore=shutil.ignore_patterns("__pycache__"))
files = {}
for f in sorted((out / "blogbot").rglob("*")):
    if f.is_file():
        files[f.relative_to(out).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
(out / "manifest.json").write_text(json.dumps({"version": __version__, "notes": notes, "files": files},
                                              ensure_ascii=False, indent=1), "utf-8")
(out / "README.md").write_text(f"""# 블로그 자동화 (네이버 블로그 · 쇼핑커넥트)

맥 앱 `블로그자동화.app`이 이 폴더의 `manifest.json`을 1시간마다 확인해서 새 버전을 자동으로 받습니다.
API 키·네이버 로그인·글 목록은 사용자 맥(~/BlogAuto)에만 있고 여기에는 올라오지 않습니다.

- 현재 버전: **{__version__}**
- 바뀐 점: {notes or '-'}
""", "utf-8")
print(f"내보냄: {out}  버전 {__version__}  파일 {len(files)}개")
