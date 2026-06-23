import json
import os
import sys
import smtplib
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
GMAIL_ADDRESS       = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")

TOKEN_URL       = "https://kauth.kakao.com/oauth/token"
SEND_URL        = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
NAVER_IMAGE_URL = "https://openapi.naver.com/v1/search/image.json"

RECIPIENTS = [GMAIL_ADDRESS, "wondertajo@gmail.com"]

QUERIES = [
    "경제 인포그래픽",
    "경제지표 그래프",
    "한국 경제 차트",
    "경제동향 통계",
]


# ── 네이버 이미지 검색 ────────────────────────────────────────────────────────

def search_images(query: str, n: int = 20) -> list[str]:
    """썸네일 URL 리스트 반환 (네이버 CDN 이미지)"""
    params = urllib.parse.urlencode({"query": query, "display": n, "sort": "date"})
    req = urllib.request.Request(f"{NAVER_IMAGE_URL}?{params}")
    req.add_header("X-Naver-Client-Id",     NAVER_CLIENT_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_CLIENT_SECRET)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            print(f"  '{query}': {len(data.get('items', []))}건")
    except Exception as e:
        print(f"  '{query}' 오류: {e}")
        return []
    urls = []
    for item in data.get("items", []):
        thumb = item.get("thumbnail", "").strip()
        w = int(item.get("sizewidth",  0) or 0)
        h = int(item.get("sizeheight", 0) or 0)
        if thumb and thumb.startswith("http") and w >= 300 and h >= 200:
            urls.append(thumb)
    return urls


def collect(target: int = 15) -> list[str]:
    seen    = set()
    results = []
    for q in QUERIES:
        if len(results) >= target:
            break
        for url in search_images(q):
            if url not in seen:
                seen.add(url)
                results.append(url)
                if len(results) >= target:
                    break
    print(f"  수집 완료: {len(results)}개")
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


def send_kakao(access_token: str, image_url: str, today: str) -> None:
    print(f"\n[카카오] 전송 중...")
    template = {
        "object_type": "feed",
        "content": {
            "title":       f"📰 경제 인포그래픽 ({today})",
            "description": "오늘의 경제 이미지 모음 — 메일에서 전체 확인",
            "image_url":   image_url,
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
            print(f"  result_code={result.get('result_code')}")
    except urllib.error.HTTPError as e:
        print(f"  [HTTPError {e.code}] {e.read().decode()}")


# ── 이메일 ───────────────────────────────────────────────────────────────────

def send_email(urls: list[str], today: str) -> None:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("[이메일] 환경변수 없음 — 건너뜀")
        return
    to_list = [r for r in RECIPIENTS if r]
    print(f"\n[이메일] 전송 중 → {', '.join(to_list)}")

    imgs = "\n".join(
        f'<img src="{url}" style="width:100%;display:block;margin-bottom:10px;border-radius:6px">'
        for url in urls
    )
    html_body = f"""
    <html>
    <body style="background:#f1f3f5;margin:0;padding:24px">
      <div style="max-width:700px;margin:0 auto">
        <h2 style="color:#1d3557;font-family:sans-serif;margin-bottom:20px">
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

    print("\n[이미지 검색]")
    urls = collect(target=15)

    if not urls:
        print("[!] 수집된 이미지 없음 — 종료")
        sys.exit(1)

    access_token = refresh_access_token()
    send_kakao(access_token, urls[0], today)
    send_email(urls, today)

    print(f"\n{'='*50}\n[+] 완료!\n{'='*50}")


if __name__ == "__main__":
    main()
