import io
import json
import os
import sys
import base64
import smtplib
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests
import yfinance as yf

# ── 환경변수 ──────────────────────────────────────────────────────────────────
REST_API_KEY       = os.environ["KAKAO_REST_API_KEY"]
REFRESH_TOKEN      = os.environ["KAKAO_REFRESH_TOKEN"]
ECOS_API_KEY       = os.environ["ECOS_API_KEY"]
IMGBB_API_KEY      = os.environ["IMGBB_API_KEY"]
GMAIL_ADDRESS      = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_URL  = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
ECOS_BASE = "https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/100/{stat}/{cycle}/{start}/{end}/{item}"

RECIPIENTS = [GMAIL_ADDRESS, "wondertajo@gmail.com"]

plt.rcParams["font.family"]        = "NanumGothic"
plt.rcParams["axes.unicode_minus"] = False


# ── 데이터 수집 ───────────────────────────────────────────────────────────────

def get_yf(ticker: str, period: str = "3mo") -> tuple[list, list]:
    try:
        hist = yf.Ticker(ticker).history(period=period)
        if hist.empty:
            return [], []
        dates  = [d.strftime("%m/%d") for d in hist.index]
        values = hist["Close"].tolist()
        return dates, values
    except Exception as e:
        print(f"  yfinance 오류 ({ticker}): {e}")
        return [], []


def get_ecos(stat: str, item: str, cycle: str, n: int = 24) -> tuple[list, list]:
    now = datetime.now()
    if cycle == "M":
        end   = now.strftime("%Y%m")
        start = (now - timedelta(days=n * 31)).strftime("%Y%m")
    else:  # Q
        q     = (now.month - 1) // 3 + 1
        end   = f"{now.year}Q{q}"
        start = f"{now.year - (n // 4 + 1)}Q1"

    url = ECOS_BASE.format(
        key=ECOS_API_KEY, stat=stat, cycle=cycle,
        start=start, end=end, item=item
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        rows = data.get("StatisticSearch", {}).get("row", [])
        if not rows:
            print(f"  ECOS 데이터 없음 ({stat}/{item})")
            return [], []
        dates  = [r["TIME"] for r in rows]
        values = [float(r["DATA_VALUE"]) for r in rows]
        return dates, values
    except Exception as e:
        print(f"  ECOS 오류 ({stat}): {e}")
        return [], []


# ── 차트 정의 ─────────────────────────────────────────────────────────────────
# (제목, 출처, 인자, 단위, 색상)
CHARTS = [
    ("코스피",           "yf",   ("^KS11",    "3mo"),             "pt",    "#4c9be8"),
    ("원/달러 환율",     "yf",   ("USDKRW=X", "3mo"),             "원",    "#f0883e"),
    ("기준금리 (한국)",  "ecos", ("722Y001",  "0101000", "M", 24), "%",     "#3fb950"),
    ("소비자물가 (CPI)", "ecos", ("901Y009",  "0",       "M", 24), "%",     "#f85149"),
    ("실질GDP 성장률",   "ecos", ("200Y001",  "10111",   "Q", 16), "%",     "#a371f7"),
    ("수출",             "ecos", ("403Y001",  "X",       "M", 24), "억달러","#79c0ff"),
    ("수입",             "ecos", ("403Y001",  "M",       "M", 24), "억달러","#ffb86c"),
    ("실업률",           "ecos", ("901Y027",  "I16B",    "M", 24), "%",     "#8b949e"),
    ("S&P 500",          "yf",   ("^GSPC",    "3mo"),             "pt",    "#ffa657"),
    ("WTI 유가",         "yf",   ("CL=F",     "3mo"),             "달러",  "#39d353"),
    ("금값",             "yf",   ("GC=F",     "3mo"),             "달러",  "#e3b341"),
    ("달러인덱스 (DXY)", "yf",   ("DX=F",     "3mo"),             "pt",    "#bc8cff"),
]


# ── 차트 생성 ─────────────────────────────────────────────────────────────────

def build_chart() -> bytes:
    fig, axes = plt.subplots(3, 4, figsize=(24, 15))
    fig.patch.set_facecolor("#0d1117")
    today = datetime.now().strftime("%Y.%m.%d")
    fig.suptitle(f"한국·세계 경제 지표  |  {today}",
                 fontsize=20, color="white", fontweight="bold", y=0.99)

    for ax, (title, src, args, unit, color) in zip(axes.flat, CHARTS):
        ax.set_facecolor("#161b22")
        for spine in ax.spines.values():
            spine.set_edgecolor("#30363d")

        if src == "yf":
            dates, vals = get_yf(*args)
        else:
            dates, vals = get_ecos(*args)

        if vals:
            x = list(range(len(vals)))
            ax.plot(x, vals, color=color, linewidth=2.0)
            ax.fill_between(x, vals, alpha=0.12, color=color)

            step = max(1, len(dates) // 5)
            ax.set_xticks(x[::step])
            ax.set_xticklabels(dates[::step], fontsize=7,
                               color="#8b949e", rotation=30)
            ax.tick_params(axis="y", labelsize=7, colors="#8b949e")
            ax.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda v, _: f"{v:,.1f}")
            )

            cur  = vals[-1]
            prev = vals[-2] if len(vals) >= 2 else cur
            diff = cur - prev
            sign  = "▲" if diff >= 0 else "▼"
            dcolor = "#3fb950" if diff >= 0 else "#f85149"

            ax.set_title(title, fontsize=10, color="white",
                         pad=6, fontweight="bold")
            ax.text(0.97, 0.95,
                    f"{cur:,.2f} {unit}\n{sign} {abs(diff):,.2f}",
                    transform=ax.transAxes,
                    ha="right", va="top", fontsize=8.5,
                    color=dcolor, fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.3",
                              facecolor="#0d1117", alpha=0.6, edgecolor="none"))
        else:
            ax.set_title(title, fontsize=10, color="white", pad=6)
            ax.text(0.5, 0.5, "데이터 없음",
                    transform=ax.transAxes,
                    ha="center", va="center",
                    color="#8b949e", fontsize=9)
            ax.set_xticks([])
            ax.set_yticks([])

        ax.grid(True, color="#21262d", linewidth=0.5, alpha=0.7)

    plt.tight_layout(rect=[0, 0, 1, 0.97])
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130,
                bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    buf.seek(0)
    return buf.read()


# ── imgbb 업로드 ──────────────────────────────────────────────────────────────

def upload_imgbb(image_bytes: bytes) -> str:
    print("\n[imgbb] 이미지 업로드 중...")
    b64  = base64.b64encode(image_bytes).decode()
    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": IMGBB_API_KEY, "image": b64},
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
        "grant_type": "refresh_token",
        "client_id":  REST_API_KEY,
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
    print("\n[카카오] 이미지 전송 중...")
    template = {
        "object_type": "feed",
        "content": {
            "title": f"📊 경제 인포그래픽 ({today})",
            "description": "한국·세계 주요 경제 지표",
            "image_url": image_url,
            "image_width": 3120,
            "image_height": 1950,
            "link": {"web_url": "https://ecos.bok.or.kr"},
        },
        "buttons": [{
            "title": "한국은행 ECOS",
            "link": {"web_url": "https://ecos.bok.or.kr"},
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
        raise


# ── 이메일 ───────────────────────────────────────────────────────────────────

def send_email(image_bytes: bytes, today: str) -> None:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("[이메일] 환경변수 없음 — 건너뜀")
        return
    to_list = [r for r in RECIPIENTS if r]
    print(f"\n[이메일] 전송 중 → {', '.join(to_list)}")

    msg = MIMEMultipart("related")
    msg["Subject"] = f"📊 경제 인포그래픽 ({today})"
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = ", ".join(to_list)

    html_body = f"""
    <html>
    <body style="background:#0d1117;margin:0;padding:24px;font-family:sans-serif">
      <h2 style="color:#e6edf3;margin-bottom:16px">📊 한국·세계 경제 지표 ({today})</h2>
      <img src="cid:chart" style="max-width:100%;border-radius:10px;border:1px solid #30363d">
      <p style="color:#8b949e;font-size:12px;margin-top:12px">
        데이터 출처: 한국은행 ECOS · Yahoo Finance
      </p>
    </body>
    </html>
    """
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    img_part = MIMEImage(image_bytes, "png")
    img_part.add_header("Content-ID", "<chart>")
    img_part.add_header("Content-Disposition", "inline", filename="economic_chart.png")
    msg.attach(img_part)

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

    print("\n[차트] 데이터 수집 및 생성 중...")
    image_bytes = build_chart()
    print(f"  완료 ({len(image_bytes):,} bytes)")

    image_url = upload_imgbb(image_bytes)

    access_token = refresh_access_token()
    send_kakao(access_token, image_url, today)

    send_email(image_bytes, today)

    print(f"\n{'='*50}\n[+] 모든 전송 완료!\n{'='*50}")


if __name__ == "__main__":
    main()
