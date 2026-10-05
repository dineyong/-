"""네이버 자동화 — 로그인, 상품 정보 수집, 글쓰기 에디터 입력, 사진, 예약 발행.

Node 버전(simple-agent.ts)에서 실제로 겪고 고친 안전장치를 그대로 지킨다.
- 에디터는 iframe 안에 있을 수 있어 모든 frame에서 찾고, 못 찾으면 아무것도 입력하지 않고 멈춤
- 좌표 클릭 금지 (예전에 프로필 편집 창 30개 사고)
- 제목→본문은 본문 클릭/Enter, 처음 쓴 뒤 제목이 바뀌었으면 멈춤
- 글자는 키보드로 치고 이모지는 한 개씩 insert_text (줄 통째로 넣으면 글자 증발, 이모지를 치면 깨짐)
- 예상 못한 새 창은 닫고 반복되면 멈춤 (단, 최종 발행 버튼 누른 뒤는 정상)
- 그림 렌더링은 별도 context (새 창 감시에 안 걸리게)
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Frame, Page, sync_playwright

from . import config
from .log import log

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
STEALTH = """
Object.defineProperty(navigator, 'webdriver', { get: () => false });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['ko-KR', 'ko', 'en-US', 'en'] });
"""


class StopError(Exception):
    """안전을 위해 멈춘 경우 (사용자에게 그대로 보여줄 메시지)."""


class LoginExpired(StopError):
    pass


def fmt(d: dt.datetime) -> str:
    return f"{d.month}월 {d.day}일 {d.hour:02d}:{d.minute:02d}"


# ─────────────────────────────────────────────
# 로그인 (사용자가 직접 로그인하는 창을 띄워 세션 저장)
# ─────────────────────────────────────────────
def login(timeout_s: int = 300) -> bool:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR", user_agent=UA)
        page = ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => false });")
        page.goto("https://nid.naver.com/nidlogin.login")
        log("🔐 로그인 창을 열었어요. 네이버에 로그인해 주세요 (최대 5분).")
        ok = False
        end = time.time() + timeout_s
        while time.time() < end:
            try:
                if any(c["name"] == "NID_AUT" for c in ctx.cookies("https://naver.com")):
                    ok = True
                    break
                page.wait_for_timeout(1000)
            except Exception:  # 창을 닫은 경우
                break
        if ok:
            try:
                page.goto("https://blog.naver.com/", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)
            except Exception:
                pass
            ctx.storage_state(path=str(config.SESSION_FILE))
            log("✅ 로그인 저장 완료 (보통 7~30일 유지)")
        else:
            log("⚠️ 로그인이 확인되지 않았어요.")
        try:
            browser.close()
        except Exception:
            pass
        return ok


# ─────────────────────────────────────────────
# 글쓰기 세션 (브라우저 한 번 열어서 글 한 편)
# ─────────────────────────────────────────────
class Writer:
    def __init__(self):
        if not config.SESSION_FILE.exists():
            raise LoginExpired("네이버 로그인이 필요해요. 대시보드에서 '네이버 로그인'을 눌러주세요.")
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(
            headless=False, slow_mo=80,
            args=["--disable-blink-features=AutomationControlled", "--disable-features=IsolateOrigins,site-per-process"])
        self.ctx = self.browser.new_context(storage_state=str(config.SESSION_FILE),
                                            viewport={"width": 1280, "height": 900}, locale="ko-KR", user_agent=UA)
        self.page = self.ctx.new_page()
        self.page.add_init_script(STEALTH)
        self.abort_reason: str | None = None
        self.final_clicked = False
        self._extra = 0
        self.ctx.on("page", self._on_new_page)
        # 네이버가 띄우는 알림창(alert) 내용은 기록하고 닫음 → 실패 사유로 보여줌
        self.dialogs: list[str] = []
        self.page.on("dialog", self._on_dialog)
        # 그림 그리기용 (새 창 감시와 분리)
        self.art = self.browser.new_context(viewport={"width": 1080, "height": 1080})

    def _on_dialog(self, d):
        msg = (d.message or "").strip()
        log(f"   💬 네이버 알림창: {msg[:120]}")
        if self.final_clicked:
            self.dialogs.append(msg[:200])
        try:
            d.accept() if d.type in ("alert", "beforeunload") else d.dismiss()
        except Exception:
            pass

    def _on_new_page(self, p: Page):
        if p == self.page or self.final_clicked:
            return
        self._extra += 1
        log(f"   ⚠️ 예상 못한 새 창이 열려서 닫았어요 ({self._extra}번째)")
        try:
            p.close()
        except Exception:
            pass
        if self._extra >= 2:
            self.abort_reason = "예상 못한 새 창이 계속 열려서 안전을 위해 멈췄어요."

    def close(self):
        for fn in (self.art.close, self.browser.close, self._pw.stop):
            try:
                fn()
            except Exception:
                pass

    # ── 공통 안전 확인 ─────────────────────────
    def check(self):
        if self.abort_reason:
            raise StopError(self.abort_reason)
        if self.page.is_closed():
            raise StopError("글쓰기 창이 닫혔어요. (로봇 브라우저는 닫지 말아주세요)")

    def find_editor(self) -> Frame | None:
        for f in self.page.frames:
            try:
                if f.query_selector(".se-documentTitle"):
                    return f
            except Exception:
                pass
        return None

    def ensure_editor(self, frame: Frame):
        self.check()
        try:
            ok = bool(frame.query_selector(".se-documentTitle"))
        except Exception:
            ok = False
        if not ok:
            raise StopError("글쓰는 도중 에디터 화면을 벗어나서 안전을 위해 멈췄어요. (블로그 임시저장 글을 확인해 주세요)")

    # ─────────────────────────────────────────
    # 상품 정보 수집 (쇼핑커넥트 링크)
    # ─────────────────────────────────────────
    def product(self, url: str) -> dict:
        log("📦 상품 정보 수집")
        page = self.page
        page.goto(url, timeout=30000)
        page.wait_for_timeout(5000)

        def first_text(selectors, cond=lambda t: True, limit=None):
            for sel in selectors:
                try:
                    els = page.query_selector_all(sel)[: limit or 1]
                except Exception:
                    continue
                for el in els:
                    t = (el.text_content() or "").strip()
                    if t and cond(t):
                        return t
            return ""

        name = ""
        og = page.query_selector('meta[property="og:title"]')
        if og:
            name = (og.get_attribute("content") or "").split(":")[0].split("-")[0].strip()
        name = first_text(["._3oDjSvLwEZ", ".product_title", "h2._22kNQuEXmb", '[class*="product_title"]',
                           '[class*="ProductName"]'], lambda t: len(t) > 3) or name or page.title().split(":")[0].strip()
        desc = ""
        m = page.query_selector('meta[property="og:description"]')
        if m:
            desc = m.get_attribute("content") or ""
        features = []
        for el in page.query_selector_all('[class*="benefit"], [class*="feature"], [class*="spec"] li')[:5]:
            t = (el.text_content() or "").strip()
            if 3 < len(t) < 50:
                features.append(t)
        price = first_text(["._1LY7DqCnwR", ".total_price", '[class*="price"]:not([class*="original"])'], lambda t: "원" in t)
        original = first_text(["del", "strike", '[class*="original"]', "._2DywKu0J_Y", ".price_del"],
                              lambda t: "원" in t or bool(re.search(r"[\d,]+", t)))
        disc = first_text(['[class*="discount"]', "._2pgHN-ntx6", ".discount_rate", '[class*="percent"]'], lambda t: "%" in t)
        disc = (re.search(r"\d+%", disc) or [disc])[0] if disc else ""
        delivery = first_text(['[class*="delivery"]', '[class*="shipping"]', "._2OAJPEG1R8"],
                              lambda t: any(k in t for k in ("배송", "무료", "도착")))
        delivery = re.sub(r"\s+", " ", delivery)[:50]
        review_count, rating = "", ""
        for sel in ['[class*="review"]', '[class*="rating"]', "._2LvUD5PAiM", ".review_count"]:
            el = page.query_selector(sel)
            if not el:
                continue
            t = (el.text_content() or "").strip()
            mc = re.search(r"리뷰\s*([\d,]+)|([\d,]+)\s*(?:개|건)", t)
            if mc and not review_count:
                review_count = (mc.group(1) or mc.group(2)).replace(",", "")
            mr = re.search(r"\b([0-5]\.\d)\b", t)
            if mr and not rating:
                rating = mr.group(1)

        urls: list[str] = []
        for img in page.query_selector_all("img"):
            src = img.get_attribute("data-src") or img.get_attribute("src") or ""
            if ("shop-phinf.pstatic.net" in src or "shopping-phinf.pstatic.net" in src) and not any(
                    k in src for k in ("icon", "logo", "1x1")):
                hi = re.sub(r"\?type=.*", "?type=w860", src)
                if hi not in urls:
                    urls.append(hi)
            if len(urls) >= 15:
                break
        paths = []
        for i, u in enumerate(urls[:10]):
            f = config.TEMP / f"product_{int(time.time() * 1000)}_{i}.jpg"
            try:
                with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=20,
                                            context=config.SSL) as r:
                    if r.status == 200 and r.headers.get_content_type().startswith("image/"):
                        f.write_bytes(r.read())
                        paths.append(str(f))
            except Exception:
                log(f"   ⚠️ 상품 사진 {i + 1} 받기 실패")
        log(f"   📌 {name} / {price or '가격 정보 없음'} / 사진 {len(paths)}장")
        return {"name": name, "description": desc, "features": features, "price": price,
                "original_price": original, "discount_rate": disc, "delivery": delivery,
                "review_count": review_count, "rating": rating, "image_paths": paths}

    # ─────────────────────────────────────────
    # 에디터 열기 / 제목
    # ─────────────────────────────────────────
    def open_editor(self) -> Frame:
        log("📄 블로그 글쓰기 화면 열기")
        blog_id = config.get("NAVER_BLOG_ID").strip()
        if not blog_id or blog_id == "your_blog_id":
            raise StopError("블로그 아이디가 비어 있어요. 대시보드 → 설정에서 넣어주세요.")
        for url in (f"https://blog.naver.com/{blog_id}/postwrite", f"https://blog.naver.com/{blog_id}?Redirect=Write&"):
            self.check()
            self.page.goto(url, timeout=30000)
            if "nid.naver.com" in self.page.url:
                raise LoginExpired("네이버 로그인이 풀렸어요. 대시보드에서 '네이버 로그인'을 다시 해주세요.")
            frame = None
            end = time.time() + 20
            while time.time() < end and not frame:
                frame = self.find_editor()
                if not frame:
                    self.page.wait_for_timeout(1000)
            if not frame:
                log("   ⚠️ 이 주소에서는 에디터를 못 찾음")
                continue
            self.page.wait_for_timeout(1500)
            for _ in range(3):   # "작성 중인 글이 있습니다" → 취소
                btn = frame.query_selector(".se-popup-button-cancel")
                if not btn:
                    break
                try:
                    btn.click()
                except Exception:
                    pass
                self.page.wait_for_timeout(800)
            help_btn = frame.query_selector(".se-help-panel-close-button")
            if help_btn:
                try:
                    help_btn.click()
                except Exception:
                    pass
            log("   ✅ 에디터 준비 완료")
            return frame
        raise StopError(f"글쓰기 화면을 찾지 못해서 안전하게 멈췄어요. 블로그 아이디를 확인해 주세요. ({self.page.url})")

    def title(self, frame: Frame, title: str):
        self.ensure_editor(frame)
        el = frame.query_selector(".se-documentTitle .se-text-paragraph")
        if not el:
            raise StopError("제목 입력칸을 찾지 못해서 멈췄어요.")
        el.click()
        self.page.wait_for_timeout(300)
        self.page.keyboard.type(title, delay=30)
        log(f"   ✅ 제목: {title}")

    def _read_title(self, frame: Frame) -> str:
        el = frame.query_selector(".se-documentTitle .se-text-paragraph")
        return ((el.text_content() if el else "") or "").strip()

    def _move_to_body(self, frame: Frame):
        for p in frame.query_selector_all(".se-text-paragraph"):
            try:
                in_title = p.evaluate("el => !!el.closest('.se-documentTitle')")
                if not in_title and p.is_visible():
                    p.click()
                    self.page.wait_for_timeout(500)
                    return
            except Exception:
                continue
        self.page.keyboard.press("End")
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(500)

    # ─────────────────────────────────────────
    # 본문 입력
    # ─────────────────────────────────────────
    def _type_line(self, line: str):
        buf = ""
        for cluster in emoji_clusters(line):
            if is_emoji(cluster):
                if buf:
                    self.page.keyboard.type(buf, delay=3)
                    buf = ""
                self.page.keyboard.insert_text(cluster)
                self.page.wait_for_timeout(30)
            else:
                buf += cluster
        if buf:
            self.page.keyboard.type(buf, delay=3)

    def _section(self, frame: Frame, text: str):
        for line in text.split("\n"):
            self.check()
            if line.strip():
                self._type_line(line)
            self.page.keyboard.press("Enter")
            self.page.wait_for_timeout(50)
        self.page.keyboard.press("Enter")
        self.ensure_editor(frame)

    def _count_images(self, frame: Frame) -> int:
        try:
            return len(frame.query_selector_all(".se-component.se-image, .se-image-resource, .se-module-image img"))
        except Exception:
            return 0

    def _upload(self, frame: Frame, path: str) -> bool:
        before = self._count_images(frame)
        frames = [frame] + [f for f in self.page.frames if f != frame]

        def uploaded() -> bool:
            for _ in range(20):
                self.page.wait_for_timeout(500)
                if self._count_images(frame) > before:
                    return True
            return False

        for f in frames:
            for sel in IMAGE_BUTTONS:
                try:
                    btn = f.query_selector(sel)
                    if not btn or not btn.is_visible():
                        continue
                    with self.page.expect_file_chooser(timeout=5000) as fc:
                        btn.click()
                    fc.value.set_files(path)
                    if uploaded():
                        return True
                    log(f"   ⚠️ 파일은 넣었는데 에디터에 사진이 안 보임 ({sel})")
                except Exception as e:  # noqa: BLE001
                    log(f"   ⚠️ 사진 버튼({sel}) 실패: {str(e)[:80]}")
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
        for f in frames:
            try:
                inputs = f.query_selector_all('input[type="file"]')
            except Exception:
                inputs = []
            for inp in inputs:
                try:
                    inp.set_input_files(path)
                    if uploaded():
                        return True
                except Exception:
                    pass
        log("   ❌ 사진을 올리지 못했어요")
        return False

    def body(self, frame: Frame, lead: str | None, slots: list, sections: list[str], hashtags: list[str]) -> int:
        """slots[i] = sections[i] 앞에 넣을 사진 목록. 올린 사진 수를 돌려줌."""
        self._move_to_body(frame)
        self.ensure_editor(frame)
        title_before = self._read_title(frame)
        checked = False

        def check_title():
            nonlocal checked
            if checked:
                return
            checked = True
            if self._read_title(frame) != title_before:
                raise StopError("본문 글이 제목 칸에 들어가서 안전을 위해 멈췄어요. 열린 창의 제목을 확인해 주세요.")

        if lead:
            self._section(frame, lead)
            check_title()
        uploaded = 0
        for i in range(max(len(slots), len(sections))):
            for img in [x for x in (slots[i] if i < len(slots) else []) or [] if x]:
                log(f"   [{i + 1}] 🖼️ 사진 올리는 중")
                if self._upload(frame, img):
                    uploaded += 1
                self.ensure_editor(frame)
            if i < len(sections):
                log(f"   [{i + 1}] ✏️ 글 입력 ({len(sections[i])}자)")
                self._section(frame, sections[i])
                check_title()
                self.page.wait_for_timeout(300)
        self.check()
        self.page.keyboard.press("Enter")
        self.page.keyboard.type(" ".join("#" + re.sub(r"\s+", "", t) for t in hashtags), delay=10)
        self.ensure_editor(frame)
        log(f"   ✅ 사진 {uploaded}장, 글 {len(sections)}칸, 해시태그 {len(hashtags)}개")
        return uploaded

    # ─────────────────────────────────────────
    # 발행 / 예약
    # ─────────────────────────────────────────
    def _click_exact(self, frame: Frame, selector: str, label: str) -> bool:
        for el in frame.query_selector_all(selector):
            try:
                if (el.text_content() or "").strip() == label and el.is_visible():
                    el.click()
                    return True
            except Exception:
                continue
        return False

    def _select_number(self, frame: Frame, kind: str, value: int) -> bool:
        for sel in frame.query_selector_all("select"):
            try:
                if not sel.is_visible():
                    continue
                opts = sel.evaluate("el => Array.from(el.options).map(o => ({value: o.value, text: o.textContent.trim()}))")
            except Exception:
                continue
            nums = []
            for o in opts:
                digits = re.sub(r"\D", "", o["text"]) or re.sub(r"\D", "", o["value"]) or "-1"
                nums.append(int(digits))
            if not nums:
                continue
            looks_hour = 12 <= len(opts) <= 24 and 12 <= max(nums) <= 24
            looks_min = 50 in nums and max(nums) <= 59 and len(opts) <= 12
            if (kind == "hour" and not looks_hour) or (kind == "minute" and not looks_min):
                continue
            if value not in nums:
                return False
            sel.select_option(value=opts[nums.index(value)]["value"])
            chosen = sel.evaluate("el => el.options[el.selectedIndex]?.textContent || ''")
            return int(re.sub(r"\D", "", chosen) or -1) == value
        return False

    def _date_matches(self, frame: Frame, at: dt.datetime) -> bool:
        for inp in frame.query_selector_all('input[class*="date"], [class*="date"] input'):
            try:
                if not inp.is_visible():
                    continue
                v = inp.evaluate("el => el.value || el.textContent || ''")
            except Exception:
                continue
            nums = [int(x) for x in re.findall(r"\d+", v)]
            if at.year in nums and at.month in nums and at.day in nums:
                return True
        return False

    def _reserve(self, frame: Frame, at: dt.datetime):
        log(f"   ⏰ 예약 시간 설정: {fmt(at)}")
        manual = f"열린 창에서 직접 '예약'을 고르고 {fmt(at)}로 맞춘 뒤 발행을 눌러주세요."
        if not (self._click_exact(frame, "label", "예약") or self._click_exact(frame, "button", "예약")
                or self._click_exact(frame, "span", "예약")):
            raise StopError(f"발행 창에서 '예약' 버튼을 못 찾았어요. {manual}")
        self.page.wait_for_timeout(1000)
        if at.date() != dt.date.today():
            inputs = frame.query_selector_all('input[class*="date"], [class*="date"] input')
            if not inputs:
                raise StopError(f"예약 날짜 칸을 못 찾았어요. {manual}")
            inputs[0].click()
            self.page.wait_for_timeout(800)
            if at.month != dt.date.today().month:
                nxt = frame.query_selector('[class*="calendar"] [class*="next"], [class*="datepicker"] [class*="next"]')
                if nxt:
                    nxt.click()
                    self.page.wait_for_timeout(500)
            clicked = False
            for el in frame.query_selector_all("button, a, td, span"):
                try:
                    if (el.text_content() or "").strip() != str(at.day):
                        continue
                    ok = el.evaluate("n => !!n.closest('[class*=\"calendar\"], [class*=\"datepicker\"]') && !n.disabled && "
                                     "!/disabled|other|prev|next/i.test(n.className || '')")
                    if ok and el.is_visible():
                        el.click()
                        clicked = True
                        break
                except Exception:
                    continue
            self.page.wait_for_timeout(500)
            if not clicked or not self._date_matches(frame, at):
                raise StopError(f"예약 날짜를 맞추지 못했어요. {manual}")
        if not (self._select_number(frame, "hour", at.hour) and self._select_number(frame, "minute", at.minute)):
            raise StopError(f"예약 시간을 맞추지 못했어요. {manual}")
        log("   ✅ 예약 날짜/시간 설정 완료")

    def publish(self, frame: Frame, at: dt.datetime | None):
        self.ensure_editor(frame)
        for _ in range(3):
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(200)
        btn = None
        for sel in ('button[class*="publish_btn"]', 'button[data-click-area="tpb.publish"]', 'header button[class*="publish"]'):
            b = frame.query_selector(sel)
            if b and b.is_visible():
                btn = b
                break
        if not btn:
            raise StopError("상단 '발행' 버튼을 못 찾았어요. 글은 작성된 상태이니 열린 창에서 직접 발행해 주세요.")
        btn.click()
        self.page.wait_for_timeout(2500)
        if at:
            self._reserve(frame, at)
        for sel in ('button[class*="confirm_btn"]', 'button[class*="btn_publish"]', '[class*="layer"] button[class*="confirm"]'):
            b = frame.query_selector(sel)
            if b and b.is_visible():
                self.final_clicked = True
                b.click()
                log("   🎉 최종 발행 버튼 클릭")
                return
        for b in frame.query_selector_all('[class*="layer"] button, [class*="popup"] button'):
            t = (b.text_content() or "").strip()
            final = "발행" in t and (t != "예약" if at else "예약" not in t)
            if final and b.is_visible():
                self.final_clicked = True
                b.click()
                log("   🎉 최종 발행 버튼 클릭")
                return
        raise StopError("최종 '발행' 버튼을 못 찾았어요. 열린 창에서 직접 발행해 주세요.")

    def confirm(self, scheduled: bool) -> tuple[str, str | None, str | None]:
        """최종 버튼을 누른 뒤 결과 확인 → (판정, 글 주소, 메모)
        판정: "ok" 완료 확인 / "unsure" 오류는 없지만 확인이 늦음(완료로 처리) / "fail" 네이버가 오류를 보여줌
        예전엔 20초 안에 에디터가 안 닫히면 실패로 쳐서, 실제로는 예약된 글이 '실패'로 보이는 일이 있었음."""
        end = time.time() + 45
        while time.time() < end:
            if self.dialogs:
                return "fail", None, "네이버 안내: " + self.dialogs[-1]
            if self.page.is_closed():
                return "ok", None, None
            try:
                url = self.page.url
                m = re.search(r"PostView|logNo=|blog\.naver\.com/[^/?]+/\d{6,}", url)
                if m:
                    return "ok", url, None
                if not re.search(r"postwrite|Redirect=Write|PostWriteForm", url, re.I):
                    return "ok", None, None          # 글쓰기 화면을 벗어남 = 발행/예약 처리됨
                if not self.find_editor():
                    return "ok", None, None
                err = self._visible_alert()
                if err:
                    return "fail", None, "네이버 안내: " + err
                self.page.wait_for_timeout(1000)
            except Exception:
                return "ok", None, None              # 창이 바뀌는 중 = 넘어간 것
        # 45초가 지나도 글쓰기 화면 그대로
        try:
            for f in self.page.frames:
                b = f.query_selector('button[class*="confirm_btn"]')
                if b and b.is_visible():
                    return "fail", None, "발행 창이 그대로 열려 있어요. 열린 창을 확인해 주세요."
        except Exception:
            pass
        return "unsure", None, "오류 안내는 없었지만 완료 화면이 늦게 떠서, 블로그에서 한 번 확인해 주세요."

    def _visible_alert(self) -> str:
        for f in self.page.frames:
            try:
                for el in f.query_selector_all('.se-popup-alert, [role="alertdialog"], [class*="alert_layer"], '
                                               '[class*="toast"], [class*="error"]'):
                    if el.is_visible():
                        t = re.sub(r"\s+", " ", el.text_content() or "").strip()
                        if t and len(t) < 200:
                            return t
            except Exception:
                continue
        return ""


IMAGE_BUTTONS = [
    'button[data-name="image"]', ".se-toolbar-item-image button", "button.se-image-toolbar-button",
    'button[data-log="dot.img"]', 'button[aria-label*="사진"]', 'button[title*="사진"]',
]


# ─────────────────────────────────────────────
# 이모지 묶음 나누기 (표준 라이브러리만으로)
# ─────────────────────────────────────────────
def is_pictographic(ch: str) -> bool:
    c = ord(ch)
    return (0x1F000 <= c <= 0x1FAFF or 0x2600 <= c <= 0x27BF or 0x2B00 <= c <= 0x2BFF or 0x2300 <= c <= 0x23FF
            or 0x2190 <= c <= 0x21FF or 0x25A0 <= c <= 0x25FF or c in (0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122,
                                                                          0x2139, 0x2934, 0x2935, 0x3030, 0x303D,
                                                                          0x3297, 0x3299, 0x24C2))


_JOIN = {"️", "︎", "⃣", "‍"}


def emoji_clusters(s: str) -> list[str]:
    """글자 하나씩, 단 이모지는 (변형 선택자·피부색·ZWJ로 이어진 것까지) 한 덩어리로."""
    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if is_pictographic(ch) or (ch.isdigit() and i + 1 < len(s) and s[i + 1] in "️⃣"):
            j = i + 1
            if 0x1F1E6 <= ord(ch) <= 0x1F1FF and j < len(s) and 0x1F1E6 <= ord(s[j]) <= 0x1F1FF:
                j += 1                  # 국기 = 지역 문자 2개
            while j < len(s):
                n = s[j]
                if n in _JOIN or 0x1F3FB <= ord(n) <= 0x1F3FF:
                    j += 1
                    if n == "‍" and j < len(s):
                        j += 1          # ZWJ 다음 글자까지
                    continue
                break
            out.append(s[i:j])
            i = j
        else:
            out.append(ch)
            i += 1
    return out


def is_emoji(cluster: str) -> bool:
    return any(is_pictographic(c) or c in _JOIN for c in cluster)


# ─────────────────────────────────────────────
# 쇼핑커넥트(브랜드커넥트) 화면 살펴보기 — 링크 자동 발급을 만들기 위한 정찰
#   로그인된 로봇 브라우저로 화면을 열고 사진·구조만 저장. 누르는 건 '검색'까지 (발급 버튼은 절대 안 누름)
# ─────────────────────────────────────────────
OUTLINE_JS = r"""() => {
  const out = [];
  const pick = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    const t = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').trim().replace(/\s+/g, ' ');
    out.push({tag: el.tagName.toLowerCase(), text: t.slice(0, 80), cls: (el.className && el.className.baseVal === undefined ? el.className : '').toString().slice(0, 120),
              id: el.id || '', href: el.getAttribute('href') || '', type: el.getAttribute('type') || '',
              name: el.getAttribute('name') || '', ph: el.getAttribute('placeholder') || '', role: el.getAttribute('role') || '',
              data: Array.from(el.attributes).filter(a => a.name.startsWith('data-')).map(a => a.name + '=' + a.value.slice(0, 40)).join(' '),
              x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)});
  };
  document.querySelectorAll('input, textarea, select, button, a, [role=button], [role=tab], [role=link]').forEach(pick);
  // 상품 카드처럼 보이는 것 (수수료·리뷰·가격 글자가 든 작은 덩어리)
  document.querySelectorAll('li, article, [class*=item], [class*=card], [class*=product]').forEach(el => {
    const t = (el.innerText || '');
    if (t.length < 400 && /(수수료|리뷰|원|%|발급|링크)/.test(t)) pick(el);
  });
  return {url: location.href, title: document.title, items: out.slice(0, 600)};
}"""


class _Done(Exception):
    pass


def recon_shopping_connect(keyword: str) -> str:
    """브랜드커넥트 화면들을 저장하고 zip 경로를 돌려줌."""
    import zipfile
    stamp = dt.datetime.now().strftime("%m%d-%H%M")
    d = config.HOME / "recon" / stamp
    d.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    w = Writer()
    n = 0

    def snap(label: str):
        nonlocal n
        n += 1
        w.page.wait_for_timeout(2500)
        base = d / f"{n}-{label}"
        try:
            w.page.screenshot(path=str(base) + ".png", full_page=True)
        except Exception as e:  # noqa: BLE001
            notes.append(f"{label} 사진 실패: {e}")
        try:
            frames = []
            for f in w.page.frames:
                try:
                    frames.append(f.evaluate(OUTLINE_JS))
                except Exception:
                    pass
            (base.with_suffix(".json")).write_text(json.dumps(frames, ensure_ascii=False, indent=1), "utf-8")
            (base.with_suffix(".html")).write_text(w.page.content(), "utf-8")
        except Exception as e:  # noqa: BLE001
            notes.append(f"{label} 구조 저장 실패: {e}")
        notes.append(f"{n}-{label}: {w.page.url}")
        log(f"   📸 {label} 화면 저장 ({w.page.url[:80]})")

    def try_search(label: str) -> bool:
        for sel in ('input[type="search"]', 'input[placeholder*="검색"]', 'input[placeholder*="상품"]',
                    'input[name*="keyword" i]', 'input[name*="query" i]', 'input[type="text"]'):
            for f in w.page.frames:
                try:
                    el = f.query_selector(sel)
                    if el and el.is_visible():
                        el.click()
                        el.fill(keyword)
                        w.page.keyboard.press("Enter")
                        notes.append(f"{label}: '{sel}' 칸에 '{keyword}' 검색")
                        w.page.wait_for_timeout(4000)
                        snap(label)
                        return True
                except Exception:
                    continue
        notes.append(f"{label}: 검색 칸을 못 찾음")
        return False

    # 페이지가 내부적으로 부르는 주소 기록 (상품 검색·링크 발급이 어떤 요청인지 알 수 있음)
    reqs: list[str] = []

    def on_req(r):
        try:
            u = r.url
            if ("naver.com" in u and "pstatic" not in u and not re.search(r"\.(js|css|png|jpg|svg|woff2?|ico)(\?|$)", u)
                    and r.resource_type in ("xhr", "fetch", "document")):
                reqs.append(f"{r.method} {u[:300]}" + (f"  BODY {r.post_data[:300]}" if r.post_data else ""))
        except Exception:
            pass
    w.page.on("request", on_req)

    def on_resp(r):
        try:
            if "gw-brandconnect.naver.com" in r.url and "/query/me" in r.url:
                t = r.text()[:2000]
                # 개인정보는 빼고 계정 구분에 필요한 것만
                ids = re.findall(r'"(?:creatorSpaceId|channelId|spaceId|id)"\s*:\s*"?(\d{6,})', t)
                notes.append(f"query/me 응답의 번호들: {sorted(set(ids))[:10]}")
        except Exception:
            pass
    w.page.on("response", on_resp)

    def links_now() -> list:
        out = []
        for f in w.page.frames:
            try:
                out += f.evaluate("() => Array.from(document.querySelectorAll('a')).map(a => [a.innerText.trim().replace(/\\s+/g,' '), a.href])")
            except Exception:
                pass
        return out

    try:
        log(f"🔎 쇼핑커넥트 화면 살펴보기 (검색어: {keyword}) — 검색까지만 하고 아무것도 발급하지 않아요")
        base = config.shopping_connect_base()
        if base:
            # 사용자가 알려준 상품 목록 화면으로 바로
            w.page.goto(base, timeout=30000)
            snap("products")
            if "/about" in w.page.url:
                # 그 채널 주소를 이 로그인 계정으로는 못 엶 → 이 계정의 크리에이터 공간 번호로 다시 시도
                spaces = list(dict.fromkeys(re.findall(r"creator-spaces/(\d{6,})", "\n".join(reqs))))
                notes.append(f"알려준 주소가 소개 페이지로 넘어감 (로그인 계정이 다른 채널일 수 있음). 이 계정의 공간 번호: {spaces}")
                for sp in spaces[:2]:
                    alt = f"https://brandconnect.naver.com/{sp}/affiliate/products"
                    w.page.goto(alt, timeout=30000)
                    snap("products-mine")
                    if "/about" not in w.page.url:
                        notes.append(f"이 계정 공간으로는 열림: {alt}")
                        break
            if "nid.naver.com" in w.page.url:
                notes.append("로그인 화면으로 넘어감 → 네이버 로그인 필요")
            else:
                if try_search("products-search"):
                    # 검색 결과의 첫 상품 상세 화면 (주소로 이동만, 아무것도 누르지 않음)
                    detail = None
                    for t, h in links_now():
                        if h and re.search(r"/affiliate/products/\d+", h):
                            detail = h
                            break
                    if detail:
                        w.page.goto(detail, timeout=30000)
                        snap("product-detail")
                    else:
                        notes.append("검색 결과에서 상품 상세 주소(/affiliate/products/번호)를 못 찾음 — 카드가 버튼 방식일 수 있음")
            notes.append("상세 화면의 [링크 발급]은 누르지 않았어요")
            raise _Done()
        w.page.goto("https://brandconnect.naver.com/", timeout=30000)
        snap("home")
        if "nid.naver.com" in w.page.url:
            notes.append("로그인 화면으로 넘어감 → 네이버 로그인 필요")
        else:
            # 'MY' 메뉴 열기 (메뉴만 펼침)
            for sel in ('button[class*="my" i]', 'button:has-text("MY")', 'a:has-text("MY")'):
                try:
                    b = w.page.query_selector(sel)
                    if b and b.is_visible():
                        b.click()
                        notes.append(f"MY 메뉴 열기: {sel}")
                        snap("my-menu")
                        break
                except Exception:
                    continue
            cand, seen = [], set()
            for text, href in links_now():
                if not href or not href.startswith("http") or href in seen:
                    continue
                if re.search(r"logout|nid\.naver|help|notice|policy|terms|partner", href, re.I):
                    continue
                if re.search(r"쇼핑|커넥트|상품|제휴|링크|채널|크리에이터|MY|마이|홈|대시보드|affiliate|shopping|product|creator|channel|my",
                             f"{text} {href}", re.I):
                    seen.add(href)
                    cand.append((text, href))
            notes.append("후보 메뉴: " + " | ".join(f"{t[:20]}→{h}" for t, h in cand[:15]))
            i = 0
            while i < len(cand) and i < 10:          # 찾은 메뉴를 차례로 (중간에 새로 찾은 것도 이어서)
                text, href = cand[i]
                i += 1
                try:
                    w.page.goto(href, timeout=30000)
                    snap(f"menu{i}")
                    for t2, h2 in links_now():
                        if (h2 and h2.startswith("http") and h2 not in seen and len(seen) < 14
                                and re.search(r"쇼핑\s*커넥트|상품|affiliate|shopping|product", f"{t2} {h2}", re.I)
                                and not re.search(r"logout|help|notice", h2, re.I)):
                            seen.add(h2)
                            cand.insert(i, (t2, h2))       # 바로 다음에 보기
                    try_search(f"menu{i}-search")
                except Exception as e:  # noqa: BLE001
                    notes.append(f"{href} 열기 실패: {e}")
    except _Done:
        pass
    finally:
        (d / "notes.txt").write_text("\n".join(notes), "utf-8")
        (d / "requests.txt").write_text("\n".join(dict.fromkeys(reqs)), "utf-8")
        w.close()
    zpath = config.HOME / "recon" / f"쇼핑커넥트화면-{stamp}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(d.iterdir()):
            z.write(f, f.name)
    log(f"📦 저장 완료: {zpath} — 이 파일을 Claude에게 보내주세요")
    return str(zpath)
