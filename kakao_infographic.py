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
IMGBB_API_KEY       = os.environ.get("IMGBB_API_KEY", "")
GMAIL_ADDRESS       = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_URL  = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
RECIPIENTS = [GMAIL_ADDRESS, "wondertajo@gmail.com"]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

SOURCES = [
    ("연합뉴스 그래픽",   "https://www.yna.co.kr/graphic/index"),
    ("기재부 인포그래픽",  "https://www.mofe.go.kr/sns/infographicList.do"),
    ("기재부 카드뉴스",    "https://www.mofe.go.kr/sns/cardNewsList.do"),
    ("KOSIS 인포그래픽",  "https://kosis.kr/visual/economyBoard/economyInfographic.do?lang=ko"),
    ("에너지경제연구원",   "https://www.keei.re.kr/gallery.es?mid=a10206010000&bid=0001"),
]

# 제외할 키워드 (로고·아이콘·버튼 등 장식 이미지)
SKIP_KW = [
    "icon", "logo", "btn", "arrow", "bullet", "blank", "loading", "spinner",
    "gnb", "lnb", "bnr", "banner", "menu", "nav_", "bg_", "back_",
    "facebook", "twitter", "youtube", "kakao", "naver", "share", "sns_",
    "header", "footer", "prev", "next", "close", "del_", "edit_",
]
IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")


# ── HTML 가져오기 ─────────────────────────────────────────────────────────────

def fetch_html(url: str, size: int = 600_000) -> str | None:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent":      UA,
            "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Accept-Encoding": "identity",
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read(size)
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
    found = []
    seen  = set()

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
        url_l = url.lower()
        if any(kw in url_l for kw in SKIP_KW):
            return
        if url not in seen:
            seen.add(url)
            found.append(url)

    # <img src / data-src / data-original>
    for m in re.finditer(
        r'<img[^>]+(?:src|data-src|data-original|data-lazy)\s*=\s*["\']([^"\'>\s]+)["\']',
        html, re.IGNORECASE,
    ):
        add(m.group(1))

    # JSON/JS 문자열에 포함된 이미지 경로
    for m in re.finditer(
        r'["\']([^"\']*\.(?:jpg|jpeg|png|gif|webp)(?:\?[^"\']*)?)["\']',
        html, re.IGNORECASE,
    ):
        add(m.group(1))

    # CSS background-image
    for m in re.finditer(
        r'background(?:-image)?\s*:\s*url\(["\']?(https?://[^"\')\s]+)["\']?\)',
        html, re.IGNORECASE,
    ):
        add(m.group(1))

    return found


# ── 소스별 수집 ───────────────────────────────────────────────────────────────

def scrape_source(name: str, url: str, limit: int = 20) -> list[str]:
    print(f"  [{name}]")
    html = fetch_html(url)
    if not html:
        return []
    images = extract_images(html, url)
    print(f"    → {len(images)}개 발견")
    for img in images[:2]:
        print(f"      {img[:90]}")
    return images[:limit]


def collect_all(per_source: int = 20) -> dict[str, list[str]]:
    print("\n[인포그래픽 수집 시작]")
    result: dict[str, list[str]] = {}
    for name, url in SOURCES:
        imgs = scrape_source(name, url, per_source)
        if imgs:
            result[name] = imgs
        time.sleep(0.8)
    total = sum(len(v) for v in result.values())
    print(f"\n  전체 수집: {total}개")
    return result


# ── imgbb 업로드 ──────────────────────────────────────────────────────────────

def upload_imgbb(img_url: str) -> tuple[str, str] | None:
    if not IMGBB_API_KEY:
        return None
    try:
        req_img = urllib.request.Request(img_url, headers={"User-Agent": UA})
        data    = urllib.request.urlopen(req_img, timeout=15).read()
        b64     = base64.b64encode(data).decode()
        payload = urllib.parse.urlencode({"key": IMGBB_API_KEY, "image": b64}).encode()
        req     = urllib.request.Request("https://api.imgbb.com/1/upload", data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode())
        direct = res["data"]["url"]
        viewer = res["data"].get("url_viewer", direct)
        print(f"  [imgbb] {direct}")
        return direct, viewer
    except Exception as e:
        print(f"  [imgbb] 실패: {e}")
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
        print(f"  오류: {result}"); sys.exit(1)
    new_rt = result.get("refresh_token")
    if new_rt:
        with open(os.environ.get("GITHUB_ENV", "/dev/null"), "a") as f:
            f.write(f"NEW_REFRESH_TOKEN={new_rt}\n")
    print(f"  완료 ({result['access_token'][:10]}...)")
    return result["access_token"]


def send_kakao(access_token: str, img_url: str, viewer_url: str, today: str) -> None:
    print("\n[카카오] 전송 중...")
    template = {
        "object_type": "feed",
        "content": {
            "title":       f"경제 인포그래픽 ({today})",
            "description": "연합뉴스 · 기재부 · KOSIS · 에너지경제연구원",
            "image_url":   img_url,
            "link":        {"web_url": viewer_url},
        },
        "buttons": [{"title": "크게 보기", "link": {"web_url": viewer_url}}],
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

def send_email(sources: dict[str, list[str]], today: str) -> None:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("\n[이메일] 환경변수 없음 — 건너뜀"); return
    to_list = [r for r in RECIPIENTS if r]
    print(f"\n[이메일] 전송 중 → {', '.join(to_list)}")

    sections = ""
    for name, imgs in sources.items():
        img_tags = "\n".join(
            f'<img src="{u}" style="width:100%;display:block;margin-bottom:10px;border-radius:4px">'
            for u in imgs
        )
        sections += f"""
        <h3 style="color:#1a3a5c;font-family:sans-serif;margin:24px 0 10px;
                   padding-bottom:6px;border-bottom:2px solid #dee2e6">
          {name}
        </h3>
        {img_tags}
        """

    html_body = f"""
    <html><body style="background:#f4f4f4;margin:0;padding:24px">
      <div style="max-width:720px;margin:0 auto">
        <h2 style="color:#1a3a5c;font-family:sans-serif;margin-bottom:4px">
          경제 인포그래픽 모음
        </h2>
        <p style="color:#6c757d;font-family:sans-serif;margin-top:0">{today}</p>
        {sections}
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
    print("  완료")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 50)

    today   = datetime.now().strftime("%Y.%m.%d")
    sources = collect_all(per_source=20)

    if not sources:
        print("[!] 수집된 이미지 없음 — 종료"); sys.exit(1)

    # 카카오톡용: 첫 번째 소스의 첫 번째 이미지를 imgbb에 업로드
    first_img = next(iter(sources.values()))[0]
    print(f"\n[imgbb] 카카오톡용 업로드: {first_img[:70]}")
    imgbb = upload_imgbb(first_img)
    if imgbb:
        kakao_img, kakao_viewer = imgbb
    else:
        kakao_img = kakao_viewer = first_img

    access_token = refresh_access_token()
    send_kakao(access_token, kakao_img, kakao_viewer, today)
    send_email(sources, today)

    total = sum(len(v) for v in sources.values())
    print(f"\n{'='*50}\n[+] 완료! 총 {total}개 이미지 전송\n{'='*50}")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception as e:
        print(f"\n[FATAL] {e}")
        traceback.print_exc()
        sys.exit(1)
