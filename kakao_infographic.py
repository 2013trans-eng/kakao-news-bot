import base64
import html as html_lib
import json
import os
import re
import sys
import smtplib
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

REST_API_KEY        = os.environ["KAKAO_REST_API_KEY"]
REFRESH_TOKEN       = os.environ["KAKAO_REFRESH_TOKEN"]
NAVER_CLIENT_ID     = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]
IMGBB_API_KEY       = os.environ.get("IMGBB_API_KEY", "")
GMAIL_ADDRESS       = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")

TOKEN_URL      = "https://kauth.kakao.com/oauth/token"
SEND_URL       = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"

RECIPIENTS = [GMAIL_ADDRESS, "wondertajo@gmail.com"]

QUERIES = [
    "코스피 환율 금리 주간 그래프",
    "경제지표 통계 차트",
    "수출입 GDP 성장률 그래프",
    "물가 인플레이션 통계 차트",
]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# ── og:image 추출 ─────────────────────────────────────────────────────────────

def extract_og_image(url: str) -> str | None:
    """기사 URL에서 og:image 메타 태그 추출 (앞 30KB만 읽음)"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as resp:
            chunk = resp.read(30000).decode("utf-8", errors="ignore")
        for pat in [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](https?://[^"\'>\s]+)',
            r'<meta[^>]+content=["\'](https?://[^"\'>\s]+)["\'][^>]+property=["\']og:image["\']',
        ]:
            m = re.search(pat, chunk, re.IGNORECASE)
            if m:
                return html_lib.unescape(m.group(1))
    except Exception:
        pass
    return None


# ── 뉴스 검색 + 이미지 수집 ───────────────────────────────────────────────────

def search_news(query: str, n: int = 10) -> list[str]:
    """네이버 뉴스 검색 → 기사 URL 목록"""
    params = urllib.parse.urlencode({"query": query, "display": n, "sort": "date"})
    req = urllib.request.Request(f"{NAVER_NEWS_URL}?{params}")
    req.add_header("X-Naver-Client-Id",     NAVER_CLIENT_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_CLIENT_SECRET)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        urls = [it.get("link", "") for it in data.get("items", []) if it.get("link")]
        print(f"  '{query}' → {len(urls)}건")
        return urls
    except Exception as e:
        print(f"  '{query}' 오류: {e}")
        return []


def collect_images(target: int = 10) -> list[str]:
    print("\n[인포그래픽 수집]")
    seen_urls  = set()
    seen_imgs  = set()
    images     = []

    for query in QUERIES:
        if len(images) >= target:
            break
        for article_url in search_news(query):
            if article_url in seen_urls:
                continue
            seen_urls.add(article_url)

            img = extract_og_image(article_url)
            if img and img not in seen_imgs:
                # 너무 작거나 기본 이미지로 보이는 것 제외
                if any(skip in img for skip in ["default", "logo", "icon", "blank"]):
                    continue
                seen_imgs.add(img)
                images.append(img)
                print(f"  ✓ ({len(images)}) {img[:70]}")
                if len(images) >= target:
                    break

            time.sleep(0.2)  # 요청 간격

    print(f"  수집 완료: {len(images)}개")
    return images


# ── imgbb 업로드 ──────────────────────────────────────────────────────────────

def upload_imgbb(img_url: str) -> tuple[str, str] | None:
    """(direct_url, viewer_page_url) 반환"""
    if not IMGBB_API_KEY:
        print("[imgbb] API 키 없음")
        return None
    try:
        data = urllib.request.urlopen(
            urllib.request.Request(img_url, headers={"User-Agent": UA}), timeout=15
        ).read()
        b64     = base64.b64encode(data).decode()
        payload = urllib.parse.urlencode({"key": IMGBB_API_KEY, "image": b64}).encode()
        req     = urllib.request.Request("https://api.imgbb.com/1/upload", data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read().decode())
        direct  = result["data"]["url"]
        viewer  = result["data"].get("url_viewer", direct)
        print(f"[imgbb] 업로드 완료: {direct}")
        return direct, viewer
    except Exception as e:
        print(f"[imgbb] 실패: {e}")
        return None


# ── 카카오 ────────────────────────────────────────────────────────────────────

def refresh_access_token() -> str:
    print("\n[토큰] 갱신 중...")
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
        print(f"  오류: {result}")
        sys.exit(1)
    new_rt = result.get("refresh_token")
    if new_rt:
        with open(os.environ.get("GITHUB_ENV", "/dev/null"), "a") as f:
            f.write(f"NEW_REFRESH_TOKEN={new_rt}\n")
    print(f"  발급 완료 ({result['access_token'][:10]}...)")
    return result["access_token"]


def send_kakao(access_token: str, image_url: str, viewer_url: str, today: str) -> None:
    print(f"\n[카카오] 전송 중...")
    template = {
        "object_type": "feed",
        "content": {
            "title":       f"경제 인포그래픽 ({today})",
            "description": "오늘의 경제 차트 · 그래프 모음",
            "image_url":   image_url,
            "link":        {"web_url": viewer_url},
        },
        "buttons": [
            {
                "title": "이미지 크게 보기",
                "link":  {"web_url": viewer_url},
            }
        ],
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
            code   = result.get("result_code")
            print(f"  result_code={code} {'✓' if code == 0 else '✗'}")
    except urllib.error.HTTPError as e:
        print(f"  [HTTPError {e.code}] {e.read().decode()}")


# ── 이메일 ───────────────────────────────────────────────────────────────────

def send_email(images: list[str], today: str) -> None:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("\n[이메일] 환경변수 없음 — 건너뜀")
        return
    to_list = [r for r in RECIPIENTS if r]
    print(f"\n[이메일] 전송 중 → {', '.join(to_list)}")

    imgs_html = "\n".join(
        f'<img src="{u}" style="width:100%;display:block;margin-bottom:12px;border-radius:6px">'
        for u in images
    )
    html_body = f"""
    <html><body style="background:#f4f4f4;margin:0;padding:24px">
      <div style="max-width:700px;margin:0 auto">
        <h2 style="color:#1d3557;font-family:sans-serif;margin-bottom:20px">
          경제 인포그래픽 ({today})
        </h2>
        {imgs_html}
      </div>
    </body></html>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"경제 인포그래픽 ({today})"
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

    today  = datetime.now().strftime("%Y.%m.%d")
    images = collect_images(target=10)

    if not images:
        print("[!] 수집된 이미지 없음 — 종료")
        sys.exit(1)

    print(f"\n[imgbb] 카카오톡용 업로드 중...")
    imgbb = upload_imgbb(images[0])
    if imgbb:
        kakao_img, kakao_viewer = imgbb
    else:
        kakao_img = kakao_viewer = images[0]
        print(f"  원본 URL 직접 사용")

    access_token = refresh_access_token()
    send_kakao(access_token, kakao_img, kakao_viewer, today)
    send_email(images, today)

    print(f"\n{'='*50}\n[+] 완료!\n{'='*50}")


if __name__ == "__main__":
    main()
