"""맥 앱 묶음 만들기:  python3 mac/build_app.py  →  dist/블로그자동화.app + dist/블로그자동화.zip

리눅스에서도 만들 수 있게 표준 라이브러리만 사용 (아이콘은 크롬으로 PNG를 찍어 icns에 담음).
zip 안에 실행 권한(755)을 직접 기록해야 맥에서 풀었을 때 앱이 실행됨.
"""
from __future__ import annotations

import os
import shutil
import struct
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from blogbot import __version__  # noqa: E402

NAME = "블로그자동화"
DIST = ROOT / "dist"
APP = DIST / f"{NAME}.app"
PW_VERSION = "1.56.0"

PLIST = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>{NAME}</string>
  <key>CFBundleDisplayName</key><string>블로그 자동화</string>
  <key>CFBundleIdentifier</key><string>com.everysales.blogauto</string>
  <key>CFBundleVersion</key><string>{__version__}</string>
  <key>CFBundleShortVersionString</key><string>{__version__}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppleEventsUsageDescription</key><string>설치 진행 상황을 알려주는 안내 창을 띄우기 위해 필요해요.</string>
</dict>
</plist>
"""

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3fbf86"/><stop offset="1" stop-color="#23915f"/></linearGradient></defs>
<rect x="64" y="64" width="896" height="896" rx="200" fill="url(#g)"/>
<rect x="232" y="250" width="520" height="560" rx="56" fill="#fffaf0"/>
<rect x="232" y="250" width="520" height="120" rx="56" fill="#ffd66b"/>
<rect x="232" y="320" width="520" height="50" fill="#ffd66b"/>
<rect x="330" y="200" width="44" height="110" rx="22" fill="#26231f"/>
<rect x="610" y="200" width="44" height="110" rx="22" fill="#26231f"/>
<rect x="300" y="450" width="300" height="34" rx="17" fill="#d8d1c2"/>
<rect x="300" y="530" width="240" height="34" rx="17" fill="#d8d1c2"/>
<rect x="300" y="610" width="270" height="34" rx="17" fill="#d8d1c2"/>
<g transform="rotate(40 690 610)"><rect x="660" y="380" width="80" height="380" rx="14" fill="#ff8a65"/>
<rect x="660" y="380" width="80" height="60" rx="14" fill="#f4b3a2"/><path d="M660 760 L740 760 L700 840 Z" fill="#f5d6b0"/>
<path d="M686 812 L714 812 L700 840 Z" fill="#26231f"/></g>
</svg>"""


def make_icns(out: Path) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    exe = os.environ.get("CHROME")
    chunks = []
    with sync_playwright() as p:
        b = p.chromium.launch(**({"executable_path": exe} if exe else {}))
        page = b.new_page()
        for typ, px in ((b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024)):
            page.set_viewport_size({"width": px, "height": px})
            svg = ICON_SVG.replace('width="1024" height="1024"', f'width="{px}" height="{px}"', 1)
            page.set_content(f'<html><body style="margin:0;background:transparent">{svg}</body></html>')
            png = page.locator("svg").screenshot(omit_background=True)
            chunks.append(typ + struct.pack(">I", len(png) + 8) + png)
        b.close()
    body = b"".join(chunks)
    out.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)
    return True


def build():
    shutil.rmtree(APP, ignore_errors=True)
    (APP / "Contents" / "MacOS").mkdir(parents=True)
    res = APP / "Contents" / "Resources"
    res.mkdir()
    (APP / "Contents" / "Info.plist").write_text(PLIST, "utf-8")
    shutil.copy(ROOT / "mac" / "launcher.sh", APP / "Contents" / "MacOS" / "launcher")
    os.chmod(APP / "Contents" / "MacOS" / "launcher", 0o755)
    shutil.copytree(ROOT / "blogbot", res / "app" / "blogbot",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (res / "playwright-version.txt").write_text(PW_VERSION)
    print("아이콘:", "만듦" if make_icns(res / "AppIcon.icns") else "건너뜀")

    zpath = DIST / f"{NAME}.zip"
    zpath.unlink(missing_ok=True)
    extra = [p for p in (ROOT / "dist-extra").glob("*")] if (ROOT / "dist-extra").exists() else []
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(APP.rglob("*")):
            arc = f"{NAME}/{f.relative_to(DIST)}"
            info = zipfile.ZipInfo(arc + ("/" if f.is_dir() else ""))
            info.create_system = 3                       # 유닉스 → 권한 기록
            mode = 0o755 if (f.is_dir() or f.name == "launcher") else 0o644
            info.external_attr = ((0o040000 if f.is_dir() else 0o100000) | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, b"" if f.is_dir() else f.read_bytes())
        for f in extra:
            z.write(f, f"{NAME}/{f.name}")
    print("완성:", zpath, f"{zpath.stat().st_size // 1024}KB")


if __name__ == "__main__":
    build()
