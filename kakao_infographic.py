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

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import yfinance as yf

REST_API_KEY       = os.environ["KAKAO_REST_API_KEY"]
REFRESH_TOKEN      = os.environ["KAKAO_REFRESH_TOKEN"]
IMGBB_API_KEY      = os.environ.get("IMGBB_API_KEY", "")
GMAIL_ADDRESS      = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_URL  = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

RECIPIENTS = [GMAIL_ADDRESS, "wondertajo@gmail.com"]

TICKERS = [
    ("^KS11",    "코스피 (한국주식)",  "pt"),
    ("USDKRW=X", "달러/원 (환율)",     "원"),
    ("^GSPC",    "S&P500 (미국주식)",  "pt"),
    ("^IXIC",    "나스닥 (미국기술)",  "pt"),
    ("CL=F",     "WTI 원유 (유가)",    "$/배럴"),
    ("GC=F",     "금 (안전자산)",      "$/온스"),
]

IMG_PATH = "/tmp/infographic.png"


# ── 폰트 설정 ─────────────────────────────────────────────────────────────────

def setup_font():
    candidates = [
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ]
    for fp in candidates:
        if os.path.exists(fp):
            fm.fontManager.addfont(fp)
            name = fm.FontProperties(fname=fp).get_name()
            plt.rcParams["font.family"] = name
            print(f"  폰트 적용: {name}")
            return
    print("  경고: 나눔폰트 없음 — 기본 폰트 사용")


# ── 시장 데이터 ───────────────────────────────────────────────────────────────

def fetch_market_data() -> list[dict]:
    print("\n[시장 데이터 수집]")
    results = []
    for ticker, label, unit in TICKERS:
        try:
            hist   = yf.Ticker(ticker).history(period="5d")
            closes = hist["Close"].dropna()
            if closes.empty:
                print(f"  {label}: 데이터 없음")
                continue
            curr = float(closes.iloc[-1])
            prev = float(closes.iloc[-2]) if len(closes) >= 2 else curr
            pct  = (curr - prev) / prev * 100 if prev else 0.0
            results.append({"label": label, "value": curr, "pct": pct, "unit": unit})
            print(f"  {label}: {curr:,.2f}  ({pct:+.2f}%)")
        except Exception as e:
            print(f"  {label} 오류: {e}")
    return results


# ── 인포그래픽 생성 ───────────────────────────────────────────────────────────

def make_infographic(data: list[dict], today: str) -> str:
    setup_font()

    BG_MAIN  = "#0d1b2a"
    BG_UP    = "#0a2e1a"
    BG_DN    = "#2e0a0a"
    BG_FLAT  = "#1a2233"
    C_UP     = "#00e676"
    C_DN     = "#ff5252"
    C_FLAT   = "#78909c"
    C_LABEL  = "#90caf9"
    C_BORDER = "#1e3a5f"

    fig = plt.figure(figsize=(14, 9), facecolor=BG_MAIN)
    fig.text(0.5, 0.96,
             f"경제 인포그래픽  |  {today}",
             ha="center", va="top",
             fontsize=26, color="white", fontweight="bold")

    COLS, ROWS = 3, 2
    PX, PY = 0.04, 0.05
    GAP = 0.018
    W = (1 - PX * 2 - GAP * (COLS - 1)) / COLS
    H = (0.88 - PY * 2 - GAP * (ROWS - 1)) / ROWS

    for i, item in enumerate(data[:6]):
        row = i // COLS
        col = i % COLS
        x = PX + col * (W + GAP)
        y = PY + (ROWS - 1 - row) * (H + GAP)

        pct   = item["pct"]
        bg    = BG_UP if pct > 0 else BG_DN if pct < 0 else BG_FLAT
        arrow = "▲" if pct > 0 else "▼" if pct < 0 else "─"
        color = C_UP  if pct > 0 else C_DN  if pct < 0 else C_FLAT

        ax = fig.add_axes([x, y, W, H])
        ax.set_facecolor(bg)
        for spine in ax.spines.values():
            spine.set_edgecolor(C_BORDER)
            spine.set_linewidth(1.5)
        ax.set_xticks([])
        ax.set_yticks([])

        # 지표 이름
        ax.text(0.5, 0.84, item["label"],
                transform=ax.transAxes, ha="center",
                fontsize=17, color=C_LABEL, fontweight="bold")

        # 현재 값
        val = item["value"]
        if item["unit"] == "원":
            val_str = f"{val:,.1f} 원"
        elif val >= 10000:
            val_str = f"{val:,.0f}"
        elif val >= 1000:
            val_str = f"{val:,.1f}"
        else:
            val_str = f"{val:,.2f}"
        ax.text(0.5, 0.48, val_str,
                transform=ax.transAxes, ha="center",
                fontsize=23, color="white", fontweight="bold")

        # 등락률 + 전일대비 설명
        ax.text(0.5, 0.20, f"{arrow}  {pct:+.2f}%",
                transform=ax.transAxes, ha="center",
                fontsize=18, color=color, fontweight="bold")
        ax.text(0.5, 0.04, "전일 대비",
                transform=ax.transAxes, ha="center",
                fontsize=10, color="#546e7a")

    fig.savefig(IMG_PATH, dpi=150, bbox_inches="tight",
                facecolor=BG_MAIN, edgecolor="none")
    plt.close(fig)
    print(f"\n  이미지 저장: {IMG_PATH}")
    return IMG_PATH


# ── imgbb 업로드 ──────────────────────────────────────────────────────────────

def upload_imgbb(path: str) -> str | None:
    if not IMGBB_API_KEY:
        print("[imgbb] API 키 없음")
        return None
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    payload = urllib.parse.urlencode({"key": IMGBB_API_KEY, "image": b64}).encode()
    req = urllib.request.Request("https://api.imgbb.com/1/upload", data=payload, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read().decode())
        url = result["data"]["url"]
        print(f"[imgbb] 업로드 완료: {url}")
        return url
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


def send_kakao(access_token: str, image_url: str, today: str) -> None:
    print(f"\n[카카오] 전송 중...")
    template = {
        "object_type": "feed",
        "content": {
            "title":       f"경제 인포그래픽 ({today})",
            "description": "탭하면 큰 이미지로 볼 수 있어요",
            "image_url":   image_url,
            "link":        {"web_url": image_url},
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
            code = result.get("result_code")
            print(f"  result_code={code} {'✓' if code == 0 else '✗'}")
    except urllib.error.HTTPError as e:
        print(f"  [HTTPError {e.code}] {e.read().decode()}")


# ── 이메일 ───────────────────────────────────────────────────────────────────

def send_email(image_url: str, today: str) -> None:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("\n[이메일] 환경변수 없음 — 건너뜀")
        return
    to_list = [r for r in RECIPIENTS if r]
    print(f"\n[이메일] 전송 중 → {', '.join(to_list)}")

    html_body = f"""
    <html><body style="background:#0d1b2a;margin:0;padding:24px">
      <div style="max-width:700px;margin:0 auto">
        <h2 style="color:#90caf9;font-family:sans-serif;margin-bottom:16px">
          경제 인포그래픽 ({today})
        </h2>
        <img src="{image_url}"
             style="width:100%;border-radius:8px;display:block">
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

    today = datetime.now().strftime("%Y.%m.%d")

    data = fetch_market_data()
    if not data:
        print("[!] 시장 데이터 없음 — 종료")
        sys.exit(1)

    img_path = make_infographic(data, today)

    image_url = upload_imgbb(img_path)
    if not image_url:
        print("[!] imgbb 업로드 실패 — 종료")
        sys.exit(1)

    access_token = refresh_access_token()
    send_kakao(access_token, image_url, today)
    send_email(image_url, today)

    print(f"\n{'='*50}\n[+] 완료!\n{'='*50}")


if __name__ == "__main__":
    main()
