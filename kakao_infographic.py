import base64
import html as html_mod
import io
import json
import os
import re
import sys
import smtplib
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from PIL import Image

REST_API_KEY        = os.environ["KAKAO_REST_API_KEY"]
REFRESH_TOKEN       = os.environ["KAKAO_REFRESH_TOKEN"]
NAVER_CLIENT_ID     = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]
IMGBB_API_KEY       = os.environ["IMGBB_API_KEY"]
GMAIL_ADDRESS       = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")

TOKEN_URL        = "https://kauth.kakao.com/oauth/token"
SEND_URL         = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
NAVER_SEARCH_URL = "https://openapi.naver.com/v1/search/news.json"

RECIPIENTS = [GMAIL_ADDRESS, "wondertajo@gmail.com"]

QUERIES = [
    "경제 인포그래픽",
    "경제지표 동향",
    "주간 경제동향",
    "경제 현황 차트",
    "경제 통계 그래프",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")


# ── 네이버 뉴스 검색 ──────────────────────────────────────────────────────────

def is_fresh(pub_date_str: str, hours: int = 48) -> bool:
    try:
        pub_dt    = parsedate_to_datetime(pub_date_str)
        age_hours = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
        return age_hours <= hours
    except Exception:
        return True


def search_naver(query: str, n: int = 20) -> list[dict]:
    params = urllib.parse.urlencode({"query": query, "display": n, "sort": "date"})
    req    = urllib.request.Request(f"{NAVER_SEARCH_URL}?{params}")
    req.add_header("X-Naver-Client-Id",     NAVER_CLIENT_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_CLIENT_SECRET)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  Naver 검색 오류 ({query}): {e}")
        return []
    results = []
    for item in data.get("items", []):
        title   = html_mod.unescape(re.sub(r"<[^>]+>", "", item.get("title", ""))).strip()
        link    = item.get("link", "").strip()
        pub_date = item.get("pubDate", "")
        if title and link and is_fresh(pub_date, hours=48):
            results.append({"title": title, "link": link, "pubDate": pub_date})
    return results


# ── 기사 대표 이미지 추출 ─────────────────────────────────────────────────────

def get_og_image(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read(80000).decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"    페이지 오류 ({url[:60]}): {e}")
        return None

    # og:image 추출
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    for pat in patterns:
        m = re.search(pat, raw, re.IGNORECASE)
        if m:
            img_url = m.group(1).strip()
            if img_url.startswith("http") and any(
                ext in img_url.lower() for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]
            ) or "image" in img_url.lower():
                return img_url
    return None


def is_valid_image(url: str) -> bool:
    """실제 이미지이고 최소 30KB 이상인지 확인 (작은 아이콘/썸네일 제외)"""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as resp:
            ct  = resp.headers.get("Content-Type", "")
            cl  = int(resp.headers.get("Content-Length", 0))
            return "image" in ct and cl > 15_000
    except Exception:
        return False


# ── 이미지 수집 ───────────────────────────────────────────────────────────────

def collect_images(target: int = 15) -> list[dict]:
    seen_links  = set()
    seen_images = set()
    results     = []

    for query in QUERIES:
        if len(results) >= target:
            break
        print(f"\n[검색] '{query}'")
        articles = search_naver(query, n=30)
        print(f"  기사 {len(articles)}건")

        for art in articles:
            if len(results) >= target:
                break
            if art["link"] in seen_links:
                continue
            seen_links.add(art["link"])

            img_url = get_og_image(art["link"])
            if not img_url or img_url in seen_images:
                continue
            if not is_valid_image(img_url):
                print(f"  ✗ 이미지 무효: {art['title'][:40]}")
                continue

            seen_images.add(img_url)
            results.append({
                "title":   art["title"],
                "link":    art["link"],
                "img_url": img_url,
            })
            print(f"  ✓ [{len(results)}] {art['title'][:50]}")

    return results


# ── 콜라주 생성 + imgbb 업로드 ────────────────────────────────────────────────

def download_image(url: str) -> Image.Image | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return Image.open(io.BytesIO(resp.read())).convert("RGB")
    except Exception as e:
        print(f"    이미지 다운로드 실패: {e}")
        return None


def make_collage(items: list[dict], cols: int = 3) -> bytes:
    """이미지를 격자 형태로 합쳐 PNG bytes 반환"""
    print("\n[콜라주] 이미지 다운로드 중...")
    cell_w, cell_h = 600, 400
    images = []
    for it in items:
        img = download_image(it["img_url"])
        if img:
            img = img.resize((cell_w, cell_h), Image.LANCZOS)
            images.append(img)
            print(f"  ✓ ({len(images)}) {it['title'][:45]}")
        if len(images) >= 12:
            break

    if not images:
        raise RuntimeError("다운로드된 이미지 없음")

    rows   = (len(images) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), (245, 245, 245))
    for i, img in enumerate(images):
        x = (i % cols) * cell_w
        y = (i // cols) * cell_h
        canvas.paste(img, (x, y))

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=88)
    buf.seek(0)
    print(f"  콜라주 완료: {rows}×{cols} ({len(images)}장)")
    return buf.read()


def upload_imgbb(image_bytes: bytes) -> str:
    print("\n[imgbb] 업로드 중...")
    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": IMGBB_API_KEY, "image": base64.b64encode(image_bytes).decode()},
        timeout=60,
    )
    resp.raise_for_status()
    url = resp.json()["data"]["url"]
    print(f"  완료: {url}")
    return url


# ── 카카오 ────────────────────────────────────────────────────────────────────

def refresh_access_token() -> str:
    print("[토큰] 갱신 중...")
    body = urllib.parse.urlencode({
        "grant_type":    "refresh_token",
        "client_id":     REST_API_KEY,
        "refresh_token": REFRESH_TOKEN,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode())
    if "access_token" not in result:
        print(f"  토큰 오류: {result}")
        sys.exit(1)
    new_rt = result.get("refresh_token")
    if new_rt:
        with open(os.environ.get("GITHUB_ENV", "/dev/null"), "a") as f:
            f.write(f"NEW_REFRESH_TOKEN={new_rt}\n")
    print(f"  발급 완료 ({result['access_token'][:10]}...)")
    return result["access_token"]


def send_kakao_collage(access_token: str, collage_url: str, today: str) -> None:
    """콜라주 이미지를 feed 템플릿으로 전송"""
    print("\n[카카오] 콜라주 이미지 전송 중...")
    template = {
        "object_type": "feed",
        "content": {
            "title":       f"📰 경제 인포그래픽 ({today})",
            "description": "오늘의 경제 뉴스 이미지 모음",
            "image_url":   collage_url,
            "link":        {"web_url": "https://news.naver.com/section/economy"},
        },
    }
    payload = urllib.parse.urlencode(
        {"template_object": json.dumps(template, ensure_ascii=False)}
    ).encode("utf-8")
    req = urllib.request.Request(SEND_URL, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type",  "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode())
            print(f"  [HTTP {resp.status}] result_code={result.get('result_code')}")
    except urllib.error.HTTPError as e:
        print(f"  [HTTPError {e.code}] {e.read().decode()}")


# ── 이메일 ───────────────────────────────────────────────────────────────────

def send_email(items: list[dict], today: str) -> None:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("[이메일] 환경변수 없음 — 건너뜀")
        return
    to_list = [r for r in RECIPIENTS if r]
    print(f"\n[이메일] 전송 중 → {', '.join(to_list)}")

    imgs = ""
    for it in items:
        imgs += f'<img src="{it["img_url"]}" style="width:100%;display:block;margin-bottom:12px;border-radius:6px">\n'

    html_body = f"""
    <html>
    <body style="background:#f1f3f5;margin:0;padding:24px">
      <div style="max-width:700px;margin:0 auto">
        <h2 style="color:#1d3557;margin-bottom:20px;font-family:sans-serif">
          📰 경제 인포그래픽 ({today})
        </h2>
        {imgs}
      </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📰 경제 인포그래픽 ({today})"
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = ", ".join(to_list)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        smtp.sendmail(GMAIL_ADDRESS, to_list, msg.as_bytes())
    print("  전송 완료")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 50)

    today = datetime.now().strftime("%Y.%m.%d")

    items = collect_images(target=15)
    print(f"\n총 {len(items)}개 인포그래픽 수집 완료")

    if not items:
        print("[!] 수집된 이미지 없음 — 종료")
        sys.exit(1)

    # 콜라주 생성 → imgbb 업로드 → 카카오톡 전송
    collage_bytes = make_collage(items)
    collage_url   = upload_imgbb(collage_bytes)

    access_token = refresh_access_token()
    send_kakao_collage(access_token, collage_url, today)
    send_email(items, today)

    print(f"\n{'='*50}\n[+] 완료!\n{'='*50}")


if __name__ == "__main__":
    main()
