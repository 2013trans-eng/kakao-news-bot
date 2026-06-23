"""
경제 인포그래픽 다운로더
용도: 오늘 날짜 인포그래픽을 OneDrive 폴더에 저장
실행: python download_infographics.py
의존성: 표준 라이브러리만 사용 (별도 설치 불필요)
"""

import os
import re
import time
import urllib.request
import urllib.parse
import html as html_lib
from datetime import datetime
from pathlib import Path

# ── 설정 (필요 시 수정) ───────────────────────────────────────────────────────

SAVE_DIR = Path(r"C:\Users\user\OneDrive\!천지운(2026.06.19업데이트)\카드뉴스")

SOURCES = [
    ("연합뉴스 그래픽",  "https://www.yna.co.kr/graphic/index"),
    ("기재부 인포그래픽", "https://www.mofe.go.kr/sns/infographicList.do"),
    ("기재부 카드뉴스",   "https://www.mofe.go.kr/sns/cardNewsList.do"),
    ("KOSIS 인포그래픽", "https://kosis.kr/visual/economyBoard/economyInfographic.do?lang=ko"),
]

PER_SOURCE   = 20
MIN_SIZE     = 10 * 1024   # 10KB 미만 제외

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

SKIP_KW = [
    "icon", "logo", "btn", "arrow", "bullet", "blank", "loading", "spinner",
    "gnb", "lnb", "bnr", "banner", "menu", "nav_", "bg_", "back_",
    "facebook", "twitter", "youtube", "kakao", "naver", "share", "sns_",
    "header", "footer", "prev", "next", "close", "del_", "edit_",
    "mark", "symbol", "emblem", "_ci", "stamp", "seal", "trademark",
]

IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")


# ── 날짜 패턴 ─────────────────────────────────────────────────────────────────

def today_patterns():
    now = datetime.now()
    return [
        now.strftime("%Y%m%d"),
        now.strftime("%Y/%m/%d"),
        now.strftime("%Y-%m-%d"),
        now.strftime("%Y.%m.%d"),
    ]


# ── HTML 가져오기 ─────────────────────────────────────────────────────────────

def fetch_html(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent":      UA,
            "Accept":          "text/html,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Accept-Encoding": "identity",
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read(600_000)
            charset = "utf-8"
            ct = resp.headers.get("Content-Type", "")
            if "charset=" in ct:
                charset = ct.split("charset=")[-1].strip()
            return raw.decode(charset, errors="ignore")
    except Exception as e:
        print(f"    fetch 실패: {e}")
        return None


# ── 이미지 URL 추출 ───────────────────────────────────────────────────────────

def extract_images(html: str, base_url: str) -> list[str]:
    found, seen = [], set()

    def add(raw: str):
        url = raw.strip().split(" ")[0]
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            parsed = urllib.parse.urlparse(base_url)
            url = f"{parsed.scheme}://{parsed.netloc}{url}"
        elif not url.startswith("http"):
            return
        url = html_lib.unescape(url)
        path = url.split("?")[0].lower()
        if not any(path.endswith(e) for e in IMG_EXTS):
            return
        if any(kw in url.lower() for kw in SKIP_KW):
            return
        if url not in seen:
            seen.add(url)
            found.append(url)

    for m in re.finditer(
        r'<img[^>]+(?:src|data-src|data-original|data-lazy)\s*=\s*["\']([^"\'>\s]+)["\']',
        html, re.IGNORECASE,
    ):
        add(m.group(1))

    for m in re.finditer(
        r'["\']([^"\']*\.(?:jpg|jpeg|png|gif|webp)(?:\?[^"\']*)?)["\']',
        html, re.IGNORECASE,
    ):
        add(m.group(1))

    for m in re.finditer(
        r'background(?:-image)?\s*:\s*url\(["\']?(https?://[^"\')\s]+)["\']?\)',
        html, re.IGNORECASE,
    ):
        add(m.group(1))

    return found


# ── 날짜 필터 ─────────────────────────────────────────────────────────────────

def filter_by_today(images: list[str], html: str, patterns: list[str]) -> list[str]:
    url_dated = [u for u in images if any(p in u for p in patterns)]
    if url_dated:
        return url_dated

    ctx_dated = []
    for pat in patterns:
        for m in re.finditer(re.escape(pat), html):
            chunk = html[max(0, m.start()-600): m.end()+600]
            for url in images:
                fname = url.split("/")[-1].split("?")[0]
                if fname and fname in chunk and url not in ctx_dated:
                    ctx_dated.append(url)
    return ctx_dated


# ── 파일 크기 확인 ────────────────────────────────────────────────────────────

def check_size(url: str) -> int:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            return int(r.headers.get("Content-Length", "0"))
    except Exception:
        return 0


# ── 중복 감지 키 ──────────────────────────────────────────────────────────────

def dedup_key(url: str) -> str:
    fname = url.split("/")[-1].split("?")[0]
    fname = re.sub(r'[_-][A-Za-z]?\d*\.(jpg|jpeg|png|gif|webp)$', '', fname, flags=re.IGNORECASE)
    fname = re.sub(r'\.(jpg|jpeg|png|gif|webp)$', '', fname, flags=re.IGNORECASE)
    return fname.lower()


# ── 이미지 다운로드 및 저장 ───────────────────────────────────────────────────

def download_and_save(url: str, folder: Path, idx: int) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        data = urllib.request.urlopen(req, timeout=20).read()
        ext = url.split("?")[0].rsplit(".", 1)[-1].lower()
        if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
            ext = "jpg"
        fname = f"{idx:03d}.{ext}"
        (folder / fname).write_bytes(data)
        print(f"    저장: {fname}  ({len(data)//1024}KB)")
        return True
    except Exception as e:
        print(f"    다운로드 실패: {url[:60]} — {e}")
        return False


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    now    = datetime.now()
    today  = now.strftime("%Y-%m-%d")
    date_pats = today_patterns()

    # 오늘 날짜 폴더 생성
    save_folder = SAVE_DIR / today
    save_folder.mkdir(parents=True, exist_ok=True)
    print(f"저장 폴더: {save_folder}")
    print(f"날짜 필터: {date_pats[0]}\n")

    seen_keys: set[str] = set()
    total_saved = 0
    img_idx = 0

    for source_name, source_url in SOURCES:
        print(f"[{source_name}]")
        html = fetch_html(source_url)
        if not html:
            continue

        candidates = extract_images(html, source_url)
        today_imgs = filter_by_today(candidates, html, date_pats)

        if not today_imgs:
            print(f"  오늘 자료 없음 — 스킵\n")
            time.sleep(0.5)
            continue

        print(f"  오늘 날짜 이미지 {len(today_imgs)}개 발견")
        saved_this_source = 0

        for url in today_imgs:
            if saved_this_source >= PER_SOURCE:
                break

            # 중복 제거
            k = dedup_key(url)
            if k in seen_keys:
                print(f"  [중복] {url[:60]}")
                continue
            seen_keys.add(k)

            # 파일 크기 확인
            size = check_size(url)
            if size > 0 and size < MIN_SIZE:
                print(f"  [작음 {size//1024}KB] {url[:60]}")
                continue

            img_idx += 1
            if download_and_save(url, save_folder, img_idx):
                saved_this_source += 1
                total_saved += 1

        print(f"  → {saved_this_source}개 저장\n")
        time.sleep(0.8)

    print(f"{'='*50}")
    print(f"완료! 총 {total_saved}개 이미지 → {save_folder}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
