import base64
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
IMGBB_API_KEY       = os.environ.get("IMGBB_API_KEY", "")
GMAIL_ADDRESS       = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")

TOKEN_URL       = "https://kauth.kakao.com/oauth/token"
SEND_URL        = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
NAVER_IMAGE_URL = "https://openapi.naver.com/v1/search/image.json"

RECIPIENTS = [GMAIL_ADDRESS, "wondertajo@gmail.com"]

QUERIES = [
    "한국 경제 인포그래픽",
    "GDP 물가 환율 인포그래픽",
    "코스피 경제지표 그래프",
    "한국경제 주간동향 차트",
]


# ── 네이버 이미지 검색 ────────────────────────────────────────────────────────

def search_images(query: str, n: int = 20) -> list[str]:
    """Naver CDN 썸네일 URL 리스트 반환"""
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


def collect(target: int = 12) -> list[str]:
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


# ── imgbb 업로드 (카카오톡용) ─────────────────────────────────────────────────

def upload_imgbb(thumb_url: str) -> str | None:
    """썸네일을 imgbb에 재업로드 → 카카오 서버가 읽을 수 있는 URL 반환"""
    if not IMGBB_API_KEY:
        print("  [imgbb] API 키 없음 — 건너뜀")
        return None
    try:
        img_bytes = urllib.request.urlopen(thumb_url, timeout=10).read()
        b64 = base64.b64encode(img_bytes).decode()
        payload = urllib.parse.urlencode({"key": IMGBB_API_KEY, "image": b64}).encode()
        req = urllib.request.Request("https://api.imgbb.com/1/upload", data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            result = json.loads(r.read().decode())
        url = result["data"]["url"]
        print(f"  [imgbb] 업로드 완료: {url[:60]}...")
        return url
    except Exception as e:
        print(f"  [imgbb] 업로드 실패: {e}")
        return None


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
    print(f"\n[카카오] 전송 중... image_url={image_url[:60]}...")
    template = {
        "object_type": "feed",
        "content": {
            "title":       f"📊 오늘의 경제 인포그래픽 ({today})",
            "description": "경제 차트·그래프 모음 — 전체 이미지는 메일 확인",
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

    # 2열 그리드: 이미지를 자연 크기(최대 320px)로 표시
    cells = "".join(
        f'<td style="padding:6px;vertical-align:top">'
        f'<img src="{url}" style="max-width:320px;width:100%;border-radius:6px;display:block">'
        f'</td>'
        + ('<tr>' if (i + 1) % 2 == 0 else '')
        for i, url in enumerate(urls)
    )
    html_body = f"""
    <html><body style="background:#f1f3f5;margin:0;padding:24px">
      <div style="max-width:700px;margin:0 auto">
        <h2 style="color:#1d3557;font-family:sans-serif;margin-bottom:20px">
          📊 경제 인포그래픽 ({today})
        </h2>
        <table cellspacing="0" cellpadding="0" style="width:100%">
          <tr>{cells}</tr>
        </table>
      </div>
    </body></html>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📊 경제 인포그래픽 ({today})"
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
    print(f"IMGBB_API_KEY 설정: {'있음' if IMGBB_API_KEY else '없음'}")
    print(f"GMAIL_ADDRESS 설정: {'있음' if GMAIL_ADDRESS else '없음'}")

    today = datetime.now().strftime("%Y.%m.%d")

    print("\n[이미지 검색]")
    urls = collect(target=12)
    print(f"  수집된 URL 목록:")
    for u in urls[:3]:
        print(f"    {u}")

    if not urls:
        print("[!] 수집된 이미지 없음 — 종료")
        sys.exit(1)

    print("\n[imgbb] 카카오톡용 이미지 업로드...")
    kakao_image_url = upload_imgbb(urls[0])
    if not kakao_image_url:
        kakao_image_url = urls[0]
        print(f"  썸네일 URL 직접 사용: {kakao_image_url[:80]}")

    access_token = refresh_access_token()
    send_kakao(access_token, kakao_image_url, today)
    send_email(urls, today)

    print(f"\n{'='*50}\n[+] 완료!\n{'='*50}")


if __name__ == "__main__":
    main()
