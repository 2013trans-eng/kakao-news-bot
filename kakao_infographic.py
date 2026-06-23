import html as html_mod
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

REST_API_KEY        = os.environ["KAKAO_REST_API_KEY"]
REFRESH_TOKEN       = os.environ["KAKAO_REFRESH_TOKEN"]
NAVER_CLIENT_ID     = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]
GMAIL_ADDRESS       = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")

TOKEN_URL        = "https://kauth.kakao.com/oauth/token"
SEND_URL         = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
NAVER_SEARCH_URL = "https://openapi.naver.com/v1/search/news.json"

RECIPIENTS = [GMAIL_ADDRESS, "wondertajo@gmail.com"]

QUERIES = [
    "경제 인포그래픽",
    "경제지표 그래프 동향",
    "주간 경제동향 분석",
    "경제 현황 차트",
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
    """이미지 URL이 실제 이미지인지 헤더로 확인 (최소 10KB)"""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as resp:
            ct  = resp.headers.get("Content-Type", "")
            cl  = int(resp.headers.get("Content-Length", 0))
            return "image" in ct and cl > 10_000
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


def send_kakao_list(access_token: str, items: list[dict], today: str) -> None:
    """list 템플릿으로 최대 5개 이미지+링크 전송"""
    print("\n[카카오] list 템플릿 전송 중...")

    contents = []
    for it in items[:5]:
        contents.append({
            "title":       it["title"][:50],
            "description": "",
            "image_url":   it["img_url"],
            "link": {"web_url": it["link"]},
        })

    template = {
        "object_type": "list",
        "header_title": f"📰 경제 인포그래픽 ({today})",
        "header_link":  {"web_url": "https://news.naver.com"},
        "contents":     contents,
        "buttons": [{
            "title": "더보기",
            "link":  {"web_url": "https://news.naver.com/section/economy"},
        }],
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
        body_err = e.read().decode()
        print(f"  [HTTPError {e.code}] {body_err}")
        # list 템플릿 실패 시 feed 템플릿으로 재시도
        _send_kakao_feed_fallback(access_token, items[0], today)


def _send_kakao_feed_fallback(access_token: str, item: dict, today: str) -> None:
    print("  → feed 템플릿으로 재시도...")
    template = {
        "object_type": "feed",
        "content": {
            "title":       f"📰 경제 인포그래픽 ({today})",
            "description": item["title"][:80],
            "image_url":   item["img_url"],
            "image_width": 800,
            "image_height": 600,
            "link": {"web_url": item["link"]},
        },
        "buttons": [{
            "title": "기사 보기",
            "link":  {"web_url": item["link"]},
        }],
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

    cards = ""
    for it in items:
        cards += f"""
        <div style="margin-bottom:32px;border:1px solid #dee2e6;
                    border-radius:10px;overflow:hidden;background:#fff;
                    box-shadow:0 2px 6px rgba(0,0,0,0.07)">
          <a href="{it['link']}" target="_blank">
            <img src="{it['img_url']}"
                 style="width:100%;display:block;max-height:400px;object-fit:cover">
          </a>
          <div style="padding:14px 16px">
            <a href="{it['link']}" target="_blank"
               style="font-size:15px;font-weight:600;color:#212529;
                      text-decoration:none;line-height:1.5">
              {it['title']}
            </a>
          </div>
        </div>
        """

    html_body = f"""
    <html>
    <body style="background:#f1f3f5;margin:0;padding:28px;font-family:'Apple SD Gothic Neo',sans-serif">
      <div style="max-width:720px;margin:0 auto">
        <h2 style="color:#1d3557;margin-bottom:24px;font-size:20px">
          📰 경제 인포그래픽 뉴스 ({today})
        </h2>
        {cards}
        <p style="color:#adb5bd;font-size:11px;text-align:center;margin-top:24px">
          출처: 네이버 뉴스 검색 결과
        </p>
      </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📰 경제 인포그래픽 뉴스 ({today})"
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

    access_token = refresh_access_token()
    send_kakao_list(access_token, items, today)
    send_email(items, today)

    print(f"\n{'='*50}\n[+] 완료!\n{'='*50}")


if __name__ == "__main__":
    main()
