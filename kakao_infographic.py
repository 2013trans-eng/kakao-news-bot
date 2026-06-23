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

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches
import yfinance as yf

REST_API_KEY        = os.environ["KAKAO_REST_API_KEY"]
REFRESH_TOKEN       = os.environ["KAKAO_REFRESH_TOKEN"]
NAVER_CLIENT_ID     = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
IMGBB_API_KEY       = os.environ.get("IMGBB_API_KEY", "")
GMAIL_ADDRESS       = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_URL  = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

RECIPIENTS = [GMAIL_ADDRESS, "wondertajo@gmail.com"]

TICKERS = [
    ("^KS11",    "코스피",    "한국 주식시장",  "pt"),
    ("USDKRW=X", "달러/원",  "원화 환율",      "원"),
    ("^GSPC",    "S&P 500",  "미국 주식시장",  "pt"),
    ("^IXIC",    "나스닥",   "미국 기술주",    "pt"),
    ("CL=F",     "WTI 유가", "국제 원유 가격", "$/배럴"),
    ("GC=F",     "금",       "안전자산 금값",  "$/온스"),
]

IMG_PATH = "/tmp/infographic.png"


def setup_font():
    for fp in ["/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
               "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"]:
        if os.path.exists(fp):
            fm.fontManager.addfont(fp)
            plt.rcParams["font.family"] = fm.FontProperties(fname=fp).get_name()
            print(f"  폰트: {plt.rcParams['font.family']}")
            return
    print("  경고: 나눔폰트 없음")


def fetch_market_data() -> list[dict]:
    print("\n[시장 데이터]")
    results = []
    for ticker, name, desc, unit in TICKERS:
        try:
            closes = yf.Ticker(ticker).history(period="5d")["Close"].dropna()
            if closes.empty:
                print(f"  {name}: 데이터 없음")
                continue
            curr = float(closes.iloc[-1])
            prev = float(closes.iloc[-2]) if len(closes) >= 2 else curr
            pct  = (curr - prev) / prev * 100 if prev else 0.0
            results.append({"name": name, "desc": desc, "value": curr, "pct": pct, "unit": unit})
            print(f"  {name}: {curr:,.2f}  ({pct:+.2f}%)")
        except Exception as e:
            print(f"  {name} 오류: {e}")
    return results


def fmt_value(val: float, unit: str) -> str:
    if unit == "원":
        return f"{val:,.1f}"
    if val >= 10000:
        return f"{val:,.0f}"
    if val >= 1000:
        return f"{val:,.1f}"
    return f"{val:,.2f}"


def make_infographic(data: list[dict], today: str) -> str:
    setup_font()
    plt.rcParams.update({"axes.unicode_minus": False})

    FIG_W, FIG_H = 13, 8.5
    BG      = "#FFFFFF"
    HEADER  = "#1a3a5c"
    C_UP    = "#e53935"   # 빨강=상승 (한국식)
    C_DN    = "#1565c0"   # 파랑=하락 (한국식)
    C_FLAT  = "#757575"
    CARD_BG = "#f8f9fa"
    BORDER  = "#dee2e6"

    fig = plt.figure(figsize=(FIG_W, FIG_H), facecolor=BG)

    # ── 헤더 ─────────────────────────────────────────────────────────────────
    header_ax = fig.add_axes([0, 0.88, 1, 0.12])
    header_ax.set_facecolor(HEADER)
    header_ax.set_xlim(0, 1); header_ax.set_ylim(0, 1)
    header_ax.axis("off")
    header_ax.text(0.03, 0.55, "오늘의 시장 동향",
                   fontsize=22, color="white", fontweight="bold", va="center")
    header_ax.text(0.03, 0.18, f"국내외 주요 경제지표  |  {today} 기준",
                   fontsize=11, color="#90caf9", va="center")
    header_ax.text(0.97, 0.55, "▲ 상승  ▼ 하락  (전일 종가 대비)",
                   fontsize=10, color="#aed6f1", va="center", ha="right")

    # ── 카드 그리드 (2행 3열) ────────────────────────────────────────────────
    COLS, ROWS = 3, 2
    PAD_X, PAD_Y = 0.025, 0.035
    GAP_X, GAP_Y = 0.018, 0.025
    W = (1 - PAD_X * 2 - GAP_X * (COLS - 1)) / COLS
    H = (0.86 - PAD_Y * 2 - GAP_Y * (ROWS - 1)) / ROWS

    for i, item in enumerate(data[:6]):
        row = i // COLS
        col = i % COLS
        x = PAD_X + col * (W + GAP_X)
        y = PAD_Y + (ROWS - 1 - row) * (H + GAP_Y)

        pct    = item["pct"]
        c_chg  = C_UP if pct > 0 else C_DN if pct < 0 else C_FLAT
        arrow  = "▲" if pct > 0 else "▼" if pct < 0 else "─"
        label  = "상승" if pct > 0 else "하락" if pct < 0 else "보합"

        ax = fig.add_axes([x, y, W, H])
        ax.set_facecolor(CARD_BG)
        for sp in ax.spines.values():
            sp.set_edgecolor(BORDER); sp.set_linewidth(1.2)
        ax.set_xticks([]); ax.set_yticks([])

        # 지표 이름 (굵게)
        ax.text(0.5, 0.90, item["name"],
                transform=ax.transAxes, ha="center",
                fontsize=18, color=HEADER, fontweight="bold")
        # 설명 (작게)
        ax.text(0.5, 0.76, item["desc"],
                transform=ax.transAxes, ha="center",
                fontsize=10, color="#6c757d")

        # 구분선
        ax.plot([0.08, 0.92], [0.68, 0.68], color=BORDER, linewidth=0.8,
                transform=ax.transAxes, clip_on=False)

        # 현재 값
        val_str = fmt_value(item["value"], item["unit"])
        unit_label = item["unit"] if item["unit"] != "pt" else ""
        ax.text(0.5, 0.46, f"{val_str} {unit_label}".strip(),
                transform=ax.transAxes, ha="center",
                fontsize=22, color="#212529", fontweight="bold")

        # 등락 (화살표 + %)
        ax.text(0.5, 0.22, f"{arrow} {abs(pct):.2f}%  {label}",
                transform=ax.transAxes, ha="center",
                fontsize=15, color=c_chg, fontweight="bold")
        ax.text(0.5, 0.06, "전일 종가 대비",
                transform=ax.transAxes, ha="center",
                fontsize=9, color="#adb5bd")

    fig.savefig(IMG_PATH, dpi=150, bbox_inches="tight",
                facecolor=BG, edgecolor="none")
    plt.close(fig)
    print(f"  이미지 저장: {IMG_PATH}")
    return IMG_PATH


# ── 연합뉴스 경제 그래픽 수집 ────────────────────────────────────────────────

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"

YNA_QUERIES = [
    "연합뉴스 경제 그래프",
    "연합뉴스 주가 환율 현황",
    "연합뉴스 금융 통계 그래픽",
]


def extract_og_image(url: str) -> str | None:
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


def fetch_yonhap_graphics(n: int = 8) -> list[str]:
    """연합뉴스(yna.co.kr) 경제 기사에서 그래픽 이미지 수집"""
    if not NAVER_CLIENT_ID:
        print("  [연합뉴스] Naver 키 없음 — 건너뜀")
        return []

    seen_urls, seen_imgs, images = set(), set(), []

    for query in YNA_QUERIES:
        if len(images) >= n:
            break
        params = urllib.parse.urlencode({"query": query, "display": 20, "sort": "date"})
        req = urllib.request.Request(f"{NAVER_NEWS_URL}?{params}")
        req.add_header("X-Naver-Client-Id",     NAVER_CLIENT_ID)
        req.add_header("X-Naver-Client-Secret", NAVER_CLIENT_SECRET)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                items = json.loads(r.read().decode()).get("items", [])
            print(f"  '{query}' → {len(items)}건")
        except Exception as e:
            print(f"  '{query}' 오류: {e}")
            continue

        for item in items:
            if len(images) >= n:
                break
            url = item.get("originallink") or item.get("link", "")
            if not url or "yna.co.kr" not in url or url in seen_urls:
                continue
            seen_urls.add(url)

            img = extract_og_image(url)
            if img and img not in seen_imgs and "default" not in img and "logo" not in img:
                seen_imgs.add(img)
                images.append(img)
                print(f"  ✓ ({len(images)}) {img[:65]}")
            time.sleep(0.2)

    print(f"  연합뉴스 그래픽 수집: {len(images)}개")
    return images


def upload_imgbb(path: str) -> tuple[str, str] | None:
    if not IMGBB_API_KEY:
        print("[imgbb] API 키 없음"); return None
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        payload = urllib.parse.urlencode({"key": IMGBB_API_KEY, "image": b64}).encode()
        req = urllib.request.Request("https://api.imgbb.com/1/upload", data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode())
        direct = res["data"]["url"]
        viewer = res["data"].get("url_viewer", direct)
        print(f"[imgbb] 완료: {direct}")
        return direct, viewer
    except Exception as e:
        print(f"[imgbb] 실패: {e}"); return None


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
            "title":       f"오늘의 시장 동향 ({today})",
            "description": "코스피 · 달러/원 · S&P500 · 나스닥 · 유가 · 금",
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
            code = result.get("result_code")
            print(f"  result_code={code} {'✓' if code == 0 else '✗'}")
    except urllib.error.HTTPError as e:
        print(f"  [HTTPError {e.code}] {e.read().decode()}")


def send_email(dashboard_url: str, yonhap_imgs: list[str], today: str) -> None:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("\n[이메일] 환경변수 없음 — 건너뜀"); return
    to_list = [r for r in RECIPIENTS if r]
    print(f"\n[이메일] 전송 중 → {', '.join(to_list)}")

    yonhap_section = ""
    if yonhap_imgs:
        imgs_html = "\n".join(
            f'<img src="{u}" style="width:100%;display:block;margin-bottom:12px;border-radius:6px">'
            for u in yonhap_imgs
        )
        yonhap_section = f"""
        <h3 style="color:#1a3a5c;font-family:sans-serif;margin:28px 0 12px">
          연합뉴스 경제 인포그래픽
        </h3>
        {imgs_html}
        """

    html_body = f"""
    <html><body style="background:#f4f4f4;margin:0;padding:24px">
      <div style="max-width:700px;margin:0 auto">
        <h2 style="color:#1a3a5c;font-family:sans-serif;margin-bottom:16px">
          오늘의 시장 동향 ({today})
        </h2>
        <img src="{dashboard_url}"
             style="width:100%;border-radius:8px;display:block;box-shadow:0 2px 8px rgba(0,0,0,0.1)">
        {yonhap_section}
      </div>
    </body></html>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"오늘의 시장 동향 ({today})"
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = ", ".join(to_list)
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        smtp.sendmail(GMAIL_ADDRESS, to_list, msg.as_bytes())
    print("  완료")


def main():
    print("=" * 50)
    print(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 50)

    today = datetime.now().strftime("%Y.%m.%d")
    data  = fetch_market_data()
    if not data:
        print("[!] 시장 데이터 없음 — 종료"); sys.exit(1)

    img_path = make_infographic(data, today)

    imgbb = upload_imgbb(img_path)
    if not imgbb:
        print("[!] imgbb 실패 — 종료"); sys.exit(1)
    img_url, viewer_url = imgbb

    print("\n[연합뉴스 그래픽 수집]")
    yonhap_imgs = fetch_yonhap_graphics(n=8)

    access_token = refresh_access_token()
    send_kakao(access_token, img_url, viewer_url, today)
    send_email(img_url, yonhap_imgs, today)

    print(f"\n{'='*50}\n[+] 완료!\n{'='*50}")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception as e:
        print(f"\n[FATAL ERROR] {e}")
        traceback.print_exc()
        sys.exit(1)
