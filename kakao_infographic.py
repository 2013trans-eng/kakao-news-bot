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

from PIL import Image

REST_API_KEY        = os.environ["KAKAO_REST_API_KEY"]
REFRESH_TOKEN       = os.environ["KAKAO_REFRESH_TOKEN"]
IMGBB_API_KEY       = os.environ.get("IMGBB_API_KEY", "")
GMAIL_ADDRESS       = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")
GITHUB_REPOSITORY      = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_TOKEN           = os.environ.get("GITHUB_TOKEN", "")
ONEDRIVE_CLIENT_ID     = os.environ.get("ONEDRIVE_CLIENT_ID", "")
ONEDRIVE_REFRESH_TOKEN = os.environ.get("ONEDRIVE_REFRESH_TOKEN", "")
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
GDRIVE_FOLDER_NAME   = "카드뉴스"
GOOGLE_TOKEN_URL     = "https://oauth2.googleapis.com/token"
GDRIVE_API           = "https://www.googleapis.com/drive/v3"
GDRIVE_UPLOAD_API    = "https://www.googleapis.com/upload/drive/v3"
ONEDRIVE_FOLDER = "!천지운(2026.06.19업데이트)/카드뉴스"
GRAPH_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
GRAPH_API       = "https://graph.microsoft.com/v1.0"

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
]

# 소스별 최대 수집 수
PER_SOURCE = 20

# 제외할 키워드 (로고·아이콘·버튼 등 장식 이미지)
SKIP_KW = [
    "icon", "logo", "btn", "arrow", "bullet", "blank", "loading", "spinner",
    "gnb", "lnb", "bnr", "banner", "menu", "nav_", "bg_", "back_",
    "facebook", "twitter", "youtube", "kakao", "naver", "share", "sns_",
    "header", "footer", "prev", "next", "close", "del_", "edit_",
    "mark", "symbol", "emblem", "_ci", "stamp", "seal", "trademark",
]

IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
MIN_FILE_SIZE = 10 * 1024  # 10KB 미만만 제외 (아이콘 수준)


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


def today_patterns() -> list[str]:
    now = datetime.now()
    return [
        now.strftime("%Y%m%d"),    # 20260623
        now.strftime("%Y/%m/%d"),  # 2026/06/23
        now.strftime("%Y-%m-%d"),  # 2026-06-23
        now.strftime("%Y.%m.%d"),  # 2026.06.23
    ]


def filter_by_today(images: list[str], html: str, patterns: list[str]) -> list[str]:
    """당일 날짜가 URL에 포함되거나 HTML 근처(±600자)에 날짜가 있는 이미지만 반환"""
    # 1단계: URL에 날짜 직접 포함
    url_dated = [u for u in images if any(p in u for p in patterns)]
    if url_dated:
        return url_dated

    # 2단계: HTML에서 날짜 텍스트 근처에 나타난 이미지
    ctx_dated = []
    for pat in patterns:
        for m in re.finditer(re.escape(pat), html):
            chunk = html[max(0, m.start()-600): m.end()+600]
            for url in images:
                fname = url.split("/")[-1].split("?")[0]
                if fname and fname in chunk and url not in ctx_dated:
                    ctx_dated.append(url)
    return ctx_dated


def filter_images(images: list[str], source_name: str, limit: int) -> list[str]:
    """파일 크기 필터 적용 (10KB 미만 아이콘 제외)"""
    filtered = []
    for url in images:
        if len(filtered) >= limit:
            break
        size = check_file_size(url)
        if size > 0 and size < MIN_FILE_SIZE:
            print(f"      [skip] {size//1024}KB 미만: {url[:60]}")
            continue
        filtered.append(url)
        print(f"      ✓ {size//1024 if size else '?'}KB  {url[:70]}")
    return filtered


def scrape_source(name: str, url: str, date_patterns: list[str]) -> list[str]:
    print(f"  [{name}]")
    html = fetch_html(url)
    if not html:
        return []
    candidates = extract_images(html, url)
    print(f"    후보 {len(candidates)}개 → 날짜 필터 중...")
    today_imgs = filter_by_today(candidates, html, date_patterns)
    if today_imgs:
        print(f"    오늘 날짜 이미지 {len(today_imgs)}개 → 크기 필터 중...")
        filtered = filter_images(today_imgs, name, PER_SOURCE)
    else:
        print(f"    오늘 자료 없음 — 스킵")
        filtered = []
    print(f"    선정: {len(filtered)}개")
    return filtered


def dedup_key(url: str) -> str:
    """파일명에서 크기 접미사를 제거해 중복 감지 키 반환.
    예) YH20260623001800_P2.jpg → yh20260623001800"""
    fname = url.split("/")[-1].split("?")[0]
    fname = re.sub(r'[_-][A-Za-z]?\d*\.(jpg|jpeg|png|gif|webp)$', '', fname, flags=re.IGNORECASE)
    fname = re.sub(r'\.(jpg|jpeg|png|gif|webp)$', '', fname, flags=re.IGNORECASE)
    return fname.lower()


def collect_all() -> dict[str, list[str]]:
    print("\n[인포그래픽 수집 시작]")
    date_pats = today_patterns()
    print(f"  날짜 필터: {date_pats[0]} ({', '.join(date_pats[1:])})")
    result: dict[str, list[str]] = {}
    seen_keys: set[str] = set()
    for name, url in SOURCES:
        imgs = scrape_source(name, url, date_pats)
        unique = []
        for u in imgs:
            k = dedup_key(u)
            if k not in seen_keys:
                seen_keys.add(k)
                unique.append(u)
            else:
                print(f"      [중복 제거] {u[:70]}")
        if unique:
            result[name] = unique
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


# ── OneDrive 업로드 ───────────────────────────────────────────────────────────

def get_onedrive_access_token() -> str | None:
    if not ONEDRIVE_CLIENT_ID or not ONEDRIVE_REFRESH_TOKEN:
        return None
    try:
        body = urllib.parse.urlencode({
            "grant_type":    "refresh_token",
            "client_id":     ONEDRIVE_CLIENT_ID,
            "refresh_token": ONEDRIVE_REFRESH_TOKEN,
            "scope":         "Files.ReadWrite offline_access",
        }).encode()
        req = urllib.request.Request(GRAPH_TOKEN_URL, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read().decode())
        new_rt = result.get("refresh_token")
        if new_rt and new_rt != ONEDRIVE_REFRESH_TOKEN:
            with open(os.environ.get("GITHUB_ENV", "/dev/null"), "a") as f:
                f.write(f"NEW_ONEDRIVE_REFRESH_TOKEN={new_rt}\n")
        return result["access_token"]
    except Exception as e:
        print(f"  [OneDrive] 토큰 오류: {e}")
        return None


def upload_to_onedrive(sources: dict[str, list[str]], today: str) -> None:
    access_token = get_onedrive_access_token()
    if not access_token:
        print("\n[OneDrive] ONEDRIVE_CLIENT_ID/REFRESH_TOKEN 없음 — 건너뜀")
        return
    print(f"\n[OneDrive] 업로드 중... (폴더: {ONEDRIVE_FOLDER}/{today})")
    headers = {"Authorization": f"Bearer {access_token}"}
    idx = 0
    for imgs in sources.values():
        for url in imgs:
            try:
                req  = urllib.request.Request(url, headers={"User-Agent": UA})
                data = urllib.request.urlopen(req, timeout=15).read()
                ext  = url.split("?")[0].rsplit(".", 1)[-1].lower()
                if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
                    ext = "jpg"
                idx += 1
                fname       = f"{idx:03d}.{ext}"
                upload_path = f"{ONEDRIVE_FOLDER}/{today}/{fname}"
                graph_url   = (
                    f"{GRAPH_API}/me/drive/root:/"
                    f"{urllib.parse.quote(upload_path, safe='/')}:/content"
                )
                put_req = urllib.request.Request(
                    graph_url, data=data, method="PUT",
                    headers={**headers, "Content-Type": "application/octet-stream"},
                )
                with urllib.request.urlopen(put_req, timeout=30) as r:
                    r.read()
                print(f"  ✓ {fname}  ({len(data)//1024}KB)")
            except Exception as e:
                print(f"  [실패] {url[:50]}: {e}")
    print(f"  → 총 {idx}개 업로드 완료")


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
    sources = collect_all()

    if not sources:
        print("[!] 오늘 날짜 인포그래픽 없음 — 종료 (새 자료가 아직 없을 수 있음)")
        sys.exit(0)

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

    # 이미지 호스팅: imgbb 우선 (카카오 미리보기 호환성 best), 실패 시 GitHub raw
    kakao_img = None
    if collage_bytes:
        imgbb = upload_imgbb(collage_bytes)
        kakao_img = imgbb[0] if imgbb else None
    if not kakao_img and collage_bytes:
        kakao_img = push_collage_to_github(collage_bytes)
    if not kakao_img:
        kakao_img = all_imgs[0]

    upload_to_onedrive(sources, today)
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
