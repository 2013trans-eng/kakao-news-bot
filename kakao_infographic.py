import base64
import html as html_lib
import io
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
from email.mime.image import MIMEImage

from PIL import Image

REST_API_KEY        = os.environ["KAKAO_REST_API_KEY"]
REFRESH_TOKEN       = os.environ["KAKAO_REFRESH_TOKEN"]
IMGBB_API_KEY       = os.environ.get("IMGBB_API_KEY", "")
GMAIL_ADDRESS       = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")
GITHUB_REPOSITORY   = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_TOKEN        = os.environ.get("GITHUB_TOKEN", "")

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

# 소스별 최대 수집 수
PER_SOURCE = 5

# 제외할 키워드 (로고·아이콘·버튼 등 장식 이미지)
SKIP_KW = [
    "icon", "logo", "btn", "arrow", "bullet", "blank", "loading", "spinner",
    "gnb", "lnb", "bnr", "banner", "menu", "nav_", "bg_", "back_",
    "facebook", "twitter", "youtube", "kakao", "naver", "share", "sns_",
    "header", "footer", "prev", "next", "close", "del_", "edit_",
]

# 연합뉴스: 경제/금융 섹션 URL 패턴만 허용
YNA_ECON_KW = ["/economy/", "/business/", "/finance/", "/industry/", "GYH", "AKR"]

IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
MIN_FILE_SIZE = 50 * 1024  # 50KB 미만은 아이콘·썸네일로 간주


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

def check_file_size(url: str) -> int:
    """HTTP HEAD로 파일 크기(bytes) 반환. 실패 시 0."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            cl = r.headers.get("Content-Length", "0")
            return int(cl)
    except Exception:
        return 0


def is_yna_econ(url: str) -> bool:
    """연합뉴스 URL이 경제 관련인지 확인"""
    return any(kw in url for kw in YNA_ECON_KW)


def filter_images(images: list[str], source_name: str, limit: int) -> list[str]:
    """파일 크기 및 소스별 필터 적용"""
    filtered = []
    for url in images:
        if len(filtered) >= limit:
            break
        # 연합뉴스는 경제 섹션 URL만
        if "yna.co.kr" in url and not is_yna_econ(url):
            continue
        # 파일 크기 확인 (HEAD 요청)
        size = check_file_size(url)
        if size > 0 and size < MIN_FILE_SIZE:
            print(f"      [skip] {size//1024}KB 미만: {url[:60]}")
            continue
        filtered.append(url)
        print(f"      ✓ {size//1024 if size else '?'}KB  {url[:70]}")
    return filtered


def scrape_source(name: str, url: str) -> list[str]:
    print(f"  [{name}]")
    html = fetch_html(url)
    if not html:
        return []
    candidates = extract_images(html, url)
    print(f"    후보 {len(candidates)}개 → 필터 중...")
    filtered = filter_images(candidates, name, PER_SOURCE)
    print(f"    선정: {len(filtered)}개")
    return filtered


def collect_all() -> dict[str, list[str]]:
    print("\n[인포그래픽 수집 시작]")
    result: dict[str, list[str]] = {}
    for name, url in SOURCES:
        imgs = scrape_source(name, url)
        if imgs:
            result[name] = imgs
        time.sleep(0.8)
    total = sum(len(v) for v in result.values())
    print(f"\n  전체 선정: {total}개")
    return result


# ── 콜라주 생성 ──────────────────────────────────────────────────────────────

def download_image(url: str) -> Image.Image | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        data = urllib.request.urlopen(req, timeout=15).read()
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as e:
        print(f"    [다운로드 실패] {url[:60]}: {e}")
        return None


def make_collage(urls: list[str], cols: int = 2, tile_w: int = 560, tile_h: int = 400) -> bytes | None:
    """상위 N장으로 2열 콜라주 JPEG 생성, bytes 반환"""
    imgs = []
    for url in urls:
        if len(imgs) >= 6:
            break
        img = download_image(url)
        if img:
            imgs.append(img)
    if not imgs:
        return None

    rows = (len(imgs) + cols - 1) // cols
    canvas = Image.new("RGB", (tile_w * cols, tile_h * rows), (240, 240, 240))
    for i, img in enumerate(imgs):
        r, c = divmod(i, cols)
        img.thumbnail((tile_w, tile_h), Image.LANCZOS)
        x = c * tile_w + (tile_w - img.width) // 2
        y = r * tile_h + (tile_h - img.height) // 2
        canvas.paste(img, (x, y))

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=82, optimize=True)
    print(f"  [콜라주] {len(imgs)}장 → {buf.tell()//1024}KB (JPEG)")
    return buf.getvalue()


# ── imgbb 업로드 ──────────────────────────────────────────────────────────────

def upload_imgbb(data: bytes) -> tuple[str, str] | None:
    if not IMGBB_API_KEY:
        return None
    try:
        b64     = base64.b64encode(data).decode()
        payload = urllib.parse.urlencode({"key": IMGBB_API_KEY, "image": b64}).encode()
        req     = urllib.request.Request("https://api.imgbb.com/1/upload", data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode())
        direct = res["data"]["url"]
        viewer = res["data"].get("url_viewer", direct)
        print(f"  [imgbb] 업로드 완료: {direct}")
        return direct, viewer
    except Exception as e:
        print(f"  [imgbb] 실패: {e}")
        return None


# ── GitHub 이미지 호스팅 ──────────────────────────────────────────────────────

def push_collage_to_github(data: bytes) -> str | None:
    """GitHub Contents API로 collage.jpg 업로드 후 commit-SHA raw URL 반환"""
    if not GITHUB_REPOSITORY or not GITHUB_TOKEN:
        print("  [GitHub] GITHUB_REPOSITORY/TOKEN 없음 — 건너뜀")
        return None
    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/collage.jpg"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github.v3+json",
        "Content-Type":  "application/json",
    }
    try:
        # 기존 파일 SHA 조회 (업데이트 시 필요)
        existing_sha = None
        try:
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as r:
                existing_sha = json.loads(r.read().decode()).get("sha")
        except Exception:
            pass

        body: dict = {
            "message": "chore: update daily infographic collage",
            "content": base64.b64encode(data).decode(),
        }
        if existing_sha:
            body["sha"] = existing_sha

        req = urllib.request.Request(api_url, data=json.dumps(body).encode(), method="PUT", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read().decode())

        raw_url = result["content"]["download_url"]
        print(f"  [GitHub] 업로드 완료: {raw_url}")
        return raw_url
    except Exception as e:
        print(f"  [GitHub] 실패: {e}")
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


def send_kakao(access_token: str, img_url: str, today: str) -> None:
    print("\n[카카오] 전송 중...")
    template = {
        "object_type": "feed",
        "content": {
            "title":     f"경제 인포그래픽 {today}",
            "image_url": img_url,
            "link":      {"web_url": img_url, "mobile_web_url": img_url},
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

    # 이미지를 다운로드해 인라인 첨부 (CID) 방식으로 삽입 — 핫링크 차단 우회
    sections = ""
    image_parts: list[MIMEImage] = []
    cid_idx = 0

    for name, imgs in sources.items():
        img_tags = ""
        for url in imgs:
            try:
                req  = urllib.request.Request(url, headers={"User-Agent": UA})
                data = urllib.request.urlopen(req, timeout=12).read()
                # 최대 600px 너비로 리사이즈 후 JPEG 변환
                im = Image.open(io.BytesIO(data)).convert("RGB")
                if im.width > 600:
                    im = im.resize((600, int(im.height * 600 / im.width)), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=82)
                cid_idx += 1
                cid = f"img{cid_idx}@kakaobot"
                part = MIMEImage(buf.getvalue(), _subtype="jpeg")
                part.add_header("Content-ID", f"<{cid}>")
                part.add_header("Content-Disposition", "inline")
                image_parts.append(part)
                img_tags += (
                    f'<img src="cid:{cid}" '
                    f'style="width:100%;max-width:600px;display:block;'
                    f'margin-bottom:10px;border-radius:4px">\n'
                )
            except Exception as e:
                print(f"      [이미지 다운로드 실패] {url[:60]}: {e}")
                img_tags += (
                    f'<img src="{url}" '
                    f'style="width:100%;max-width:600px;display:block;'
                    f'margin-bottom:10px;border-radius:4px">\n'
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

    msg = MIMEMultipart("related")
    msg["Subject"] = f"경제 인포그래픽 ({today})"
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = ", ".join(to_list)
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    for part in image_parts:
        msg.attach(part)

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        smtp.sendmail(GMAIL_ADDRESS, to_list, msg.as_bytes())
    print(f"  완료 (인라인 이미지 {len(image_parts)}장 첨부)")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 50)

    today   = datetime.now().strftime("%Y.%m.%d")
    sources = collect_all()

    if not sources:
        print("[!] 수집된 이미지 없음 — 종료"); sys.exit(1)

    # 카카오톡용: 전체 이미지 상위 6장으로 콜라주 생성
    all_imgs = [u for imgs in sources.values() for u in imgs]
    print(f"\n[콜라주] 상위 6장으로 2열 콜라주 생성 중...")
    collage_bytes = make_collage(all_imgs[:6])

    if not collage_bytes:
        # 콜라주 실패 시 첫 이미지 단독 사용
        try:
            collage_bytes = urllib.request.urlopen(
                urllib.request.Request(all_imgs[0], headers={"User-Agent": UA}), timeout=15
            ).read()
        except Exception:
            pass

    # 이미지 호스팅: GitHub raw 우선, 실패 시 imgbb, 실패 시 원본 URL
    kakao_img = None
    if collage_bytes:
        kakao_img = push_collage_to_github(collage_bytes)
    if not kakao_img and collage_bytes:
        imgbb = upload_imgbb(collage_bytes)
        kakao_img = imgbb[0] if imgbb else None
    if not kakao_img:
        kakao_img = all_imgs[0]

    access_token = refresh_access_token()
    send_kakao(access_token, kakao_img, today)
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
