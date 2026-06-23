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
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
import numpy as np
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

# 색상 팔레트
C_UP   = "#e63946"   # 상승 (빨강)
C_DOWN = "#1d7cc4"   # 하락 (파랑)
C_NEU  = "#555555"
C_BG   = "#f8f9fa"
C_CARD = "#ffffff"
C_BORDER = "#dee2e6"
C_BAR1 = "#1d7cc4"
C_BAR2 = "#e63946"


# ── 데이터 수집 ───────────────────────────────────────────────────────────────

def get_yf_latest(ticker: str, period: str = "5d") -> tuple[float, float, str]:
    """(현재값, 전일대비변화, 단위레이블) 반환"""
    try:
        hist = yf.Ticker(ticker).history(period=period)
        if len(hist) < 2:
            return 0.0, 0.0, ""
        cur  = hist["Close"].iloc[-1]
        prev = hist["Close"].iloc[-2]
        return float(cur), float(cur - prev), ""
    except Exception as e:
        print(f"  yfinance 오류 ({ticker}): {e}")
        return 0.0, 0.0, ""


def get_yf_series(ticker: str, period: str = "6mo") -> tuple[list, list]:
    try:
        hist = yf.Ticker(ticker).history(period=period)
        if hist.empty:
            return [], []
        # 월별 마지막 값으로 리샘플
        hist.index = hist.index.tz_localize(None)
        monthly = hist["Close"].resample("ME").last().dropna()
        dates  = [d.strftime("%y/%m") for d in monthly.index]
        values = monthly.tolist()
        return dates, values
    except Exception as e:
        print(f"  yfinance 시계열 오류 ({ticker}): {e}")
        return [], []


def get_ecos_series(stat: str, item: str, cycle: str, n: int = 12) -> tuple[list, list]:
    now = datetime.now()
    if cycle == "M":
        end   = now.strftime("%Y%m")
        start = (now - timedelta(days=n * 31)).strftime("%Y%m")
    else:
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
            return [], []
        dates  = [r["TIME"] for r in rows]
        values = [float(r["DATA_VALUE"]) for r in rows]
        return dates, values
    except Exception as e:
        print(f"  ECOS 오류 ({stat}): {e}")
        return [], []


def get_ecos_latest(stat: str, item: str, cycle: str) -> tuple[float, float]:
    dates, vals = get_ecos_series(stat, item, cycle, n=4)
    if len(vals) >= 2:
        return vals[-1], vals[-1] - vals[-2]
    if len(vals) == 1:
        return vals[0], 0.0
    return 0.0, 0.0


# ── 헬퍼: 카드 그리기 ─────────────────────────────────────────────────────────

def draw_card(ax, title: str, value: float, diff: float,
              unit: str, fmt: str = ",.2f"):
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # 카드 배경
    rect = mpatches.FancyBboxPatch(
        (0.04, 0.06), 0.92, 0.88,
        boxstyle="round,pad=0.02",
        facecolor=C_CARD, edgecolor=C_BORDER, linewidth=1.2
    )
    ax.add_patch(rect)

    # 제목
    ax.text(0.5, 0.83, title, ha="center", va="center",
            fontsize=10, color="#495057", fontweight="bold")

    # 현재값
    val_str = f"{value:{fmt}}" if value != 0 else "—"
    ax.text(0.5, 0.55, f"{val_str}", ha="center", va="center",
            fontsize=16, color="#212529", fontweight="bold")
    ax.text(0.5, 0.38, unit, ha="center", va="center",
            fontsize=9, color="#6c757d")

    # 변화량
    if diff != 0:
        sign   = "▲" if diff > 0 else "▼"
        color  = C_UP if diff > 0 else C_DOWN
        ax.text(0.5, 0.18,
                f"{sign} {abs(diff):{fmt}}",
                ha="center", va="center",
                fontsize=9, color=color, fontweight="bold")


# ── 헬퍼: 막대 차트 ───────────────────────────────────────────────────────────

def draw_bar(ax, dates: list, vals: list, title: str, unit: str,
             color: str = C_BAR1, horizontal: bool = False):
    ax.set_facecolor(C_CARD)
    for spine in ax.spines.values():
        spine.set_color(C_BORDER)
        spine.set_linewidth(0.8)

    ax.set_title(title, fontsize=10, color="#212529",
                 fontweight="bold", pad=8, loc="left")

    if not vals:
        ax.text(0.5, 0.5, "데이터 없음", transform=ax.transAxes,
                ha="center", va="center", color="#adb5bd")
        return

    n = len(vals)
    x = np.arange(n)

    if horizontal:
        colors = [C_UP if v >= 0 else C_DOWN for v in vals]
        bars = ax.barh(x, vals, color=colors, height=0.6)
        ax.set_yticks(x)
        ax.set_yticklabels(dates, fontsize=8, color="#495057")
        ax.tick_params(axis="x", labelsize=7, colors="#6c757d")
        ax.axvline(0, color="#adb5bd", linewidth=0.8)
        for bar, v in zip(bars, vals):
            xpos = v + (max(vals) * 0.02 if v >= 0 else min(vals) * 0.02)
            ax.text(xpos, bar.get_y() + bar.get_height() / 2,
                    f"{v:,.1f}", va="center",
                    fontsize=7.5, color="#212529", fontweight="bold")
    else:
        colors = [C_UP if v >= 0 else C_DOWN for v in vals]
        bars = ax.bar(x, vals, color=colors, width=0.6)
        ax.set_xticks(x)
        ax.set_xticklabels(dates, fontsize=7.5, color="#495057", rotation=30, ha="right")
        ax.tick_params(axis="y", labelsize=7, colors="#6c757d")
        ax.axhline(0, color="#adb5bd", linewidth=0.8)
        for bar, v in zip(bars, vals):
            ypos = v + (max(abs(vv) for vv in vals) * 0.03 * (1 if v >= 0 else -1))
            ax.text(bar.get_x() + bar.get_width() / 2, ypos,
                    f"{v:,.1f}", ha="center",
                    fontsize=7, color="#212529", fontweight="bold")

    ax.text(1, -0.12 if not horizontal else 0,
            unit, transform=ax.transAxes,
            ha="right", va="top", fontsize=7, color="#6c757d")
    ax.grid(True, axis="x" if horizontal else "y",
            color=C_BORDER, linewidth=0.5, alpha=0.8)


def draw_line(ax, dates: list, vals: list, title: str, unit: str, color: str = C_BAR1):
    ax.set_facecolor(C_CARD)
    for spine in ax.spines.values():
        spine.set_color(C_BORDER)
        spine.set_linewidth(0.8)
    ax.set_title(title, fontsize=10, color="#212529",
                 fontweight="bold", pad=8, loc="left")
    if not vals:
        ax.text(0.5, 0.5, "데이터 없음", transform=ax.transAxes,
                ha="center", va="center", color="#adb5bd")
        return
    x = np.arange(len(vals))
    ax.plot(x, vals, color=color, linewidth=2.2, marker="o",
            markersize=4, markerfacecolor="white", markeredgecolor=color)
    ax.fill_between(x, vals, alpha=0.08, color=color)
    step = max(1, len(dates) // 6)
    ax.set_xticks(x[::step])
    ax.set_xticklabels(dates[::step], fontsize=7.5, color="#495057",
                       rotation=30, ha="right")
    ax.tick_params(axis="y", labelsize=7, colors="#6c757d")
    ax.text(1, -0.12, unit, transform=ax.transAxes,
            ha="right", va="top", fontsize=7, color="#6c757d")
    ax.grid(True, color=C_BORDER, linewidth=0.5, alpha=0.8)


# ── 차트 생성 (메인) ──────────────────────────────────────────────────────────

def build_chart() -> bytes:
    print("  [데이터] 수집 중...")

    # ── 현재값 수집 ──
    kospi_v,   kospi_d,   _ = get_yf_latest("^KS11")
    usdkrw_v,  usdkrw_d,  _ = get_yf_latest("USDKRW=X")
    sp500_v,   sp500_d,   _ = get_yf_latest("^GSPC")
    wti_v,     wti_d,     _ = get_yf_latest("CL=F")
    gold_v,    gold_d,    _ = get_yf_latest("GC=F")
    dxy_v,     dxy_d,     _ = get_yf_latest("DX=F")
    rate_v,    rate_d        = get_ecos_latest("722Y001", "0101000", "M")
    cpi_v,     cpi_d         = get_ecos_latest("901Y009", "0",       "M")
    unemp_v,   unemp_d       = get_ecos_latest("901Y027", "I16B",    "M")

    # ── 시계열 수집 ──
    gdp_d,   gdp_v   = get_ecos_series("200Y001", "10111",   "Q", 12)
    exp_d,   exp_v   = get_ecos_series("403Y001",  "X",      "M", 12)
    imp_d,   imp_v   = get_ecos_series("403Y001",  "M",      "M", 12)
    rate_ds, rate_vs = get_ecos_series("722Y001", "0101000", "M", 18)
    kos_d,   kos_v   = get_yf_series("^KS11",    "12mo")
    usd_d,   usd_v   = get_yf_series("USDKRW=X", "12mo")

    print("  [차트] 레이아웃 생성 중...")

    # ── 레이아웃 ──
    fig = plt.figure(figsize=(22, 26), facecolor=C_BG)
    fig.patch.set_facecolor(C_BG)

    today = datetime.now().strftime("%Y.%m.%d")

    # 헤더
    header_ax = fig.add_axes([0.02, 0.965, 0.96, 0.032])
    header_ax.set_facecolor("#1d3557")
    header_ax.axis("off")
    header_ax.text(0.5, 0.5, f"📊  한국·세계 경제 인포그래픽  |  {today}",
                   ha="center", va="center",
                   fontsize=18, color="white", fontweight="bold")

    gs = gridspec.GridSpec(
        5, 3,
        figure=fig,
        left=0.04, right=0.97,
        top=0.955, bottom=0.02,
        hspace=0.48, wspace=0.22,
    )

    # ── 섹션 1: 국내 시장 카드 (3개) ──
    sec1_ax = fig.add_subplot(gs[0, :])
    sec1_ax.axis("off")
    sec1_ax.set_facecolor(C_BG)
    sec1_ax.text(0.0, 0.85, "■  국내 주요 지표",
                 fontsize=12, color="#1d3557", fontweight="bold",
                 transform=sec1_ax.transAxes)

    card_gs = gridspec.GridSpecFromSubplotSpec(
        1, 3, subplot_spec=gs[0, :], wspace=0.08
    )
    ax_k  = fig.add_subplot(card_gs[0, 0])
    ax_fx = fig.add_subplot(card_gs[0, 1])
    ax_rt = fig.add_subplot(card_gs[0, 2])

    draw_card(ax_k,  "코스피",       kospi_v,  kospi_d,  "pt",  ",.0f")
    draw_card(ax_fx, "원/달러 환율", usdkrw_v, usdkrw_d, "원",  ",.1f")
    draw_card(ax_rt, "한국 기준금리", rate_v,   rate_d,   "%",   ".2f")

    # ── 섹션 2: 세계 시장 카드 (3개) ──
    sec2_gs = gridspec.GridSpecFromSubplotSpec(
        1, 3, subplot_spec=gs[1, :], wspace=0.08
    )
    ax_sp  = fig.add_subplot(sec2_gs[0, 0])
    ax_wti = fig.add_subplot(sec2_gs[0, 1])
    ax_gld = fig.add_subplot(sec2_gs[0, 2])

    draw_card(ax_sp,  "S&P 500",       sp500_v, sp500_d, "pt",   ",.0f")
    draw_card(ax_wti, "WTI 유가",      wti_v,   wti_d,   "달러", ",.2f")
    draw_card(ax_gld, "금값 (달러/oz)",gold_v,  gold_d,  "달러", ",.1f")

    # 섹션 레이블
    for y_pos, label in [(0.775, "■  세계 주요 지표")]:
        lax = fig.add_axes([0.04, y_pos, 0.5, 0.012])
        lax.axis("off")
        lax.text(0, 0.5, label, fontsize=12, color="#1d3557", fontweight="bold")

    # ── 섹션 3: 코스피 + 환율 추이 ──
    ax_kline = fig.add_subplot(gs[2, 0])
    ax_uline = fig.add_subplot(gs[2, 1])
    ax_cpi   = fig.add_subplot(gs[2, 2])

    draw_line(ax_kline, kos_d, kos_v, "코스피 추이 (월별)", "pt", "#1d7cc4")
    draw_line(ax_uline, usd_d, usd_v, "원/달러 환율 추이", "원", "#e63946")

    cpi_ds, cpi_vs = get_ecos_series("901Y009", "0", "M", 12)
    draw_line(ax_cpi, cpi_ds, cpi_vs, "소비자물가 상승률 (CPI)", "%, 전년비", "#f4a261")

    # ── 섹션 4: 수출입 + GDP ──
    ax_exp  = fig.add_subplot(gs[3, 0])
    ax_imp  = fig.add_subplot(gs[3, 1])
    ax_gdp  = fig.add_subplot(gs[3, 2])

    draw_bar(ax_exp, exp_d[-8:], exp_v[-8:], "수출 (통관기준)", "억달러", C_BAR1)
    draw_bar(ax_imp, imp_d[-8:], imp_v[-8:], "수입 (통관기준)", "억달러", C_BAR2)
    draw_bar(ax_gdp, gdp_d[-8:], gdp_v[-8:], "실질GDP 성장률 (전기비)", "%", C_BAR1)

    # ── 섹션 5: 기준금리 추이 + 실업률 + DXY ──
    ax_rate  = fig.add_subplot(gs[4, 0])
    ax_unemp = fig.add_subplot(gs[4, 1])
    ax_dxy   = fig.add_subplot(gs[4, 2])

    draw_line(ax_rate, rate_ds, rate_vs, "한국 기준금리 추이", "%", "#2a9d8f")

    unemp_ds, unemp_vs = get_ecos_series("901Y027", "I16B", "M", 12)
    draw_line(ax_unemp, unemp_ds, unemp_vs, "실업률 추이", "%", "#e76f51")

    dxy_ds, dxy_vs = get_yf_series("DX=F", "12mo")
    draw_line(ax_dxy, dxy_ds, dxy_vs, "달러인덱스 (DXY)", "pt", "#6a0572")

    # 푸터
    footer_ax = fig.add_axes([0.02, 0.002, 0.96, 0.015])
    footer_ax.axis("off")
    footer_ax.text(
        0.5, 0.5,
        "데이터 출처: 한국은행 ECOS · Yahoo Finance  |  본 인포그래픽은 참고용이며 투자 권고가 아닙니다",
        ha="center", va="center", fontsize=8, color="#6c757d"
    )

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130,
                bbox_inches="tight", facecolor=C_BG)
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
            "image_width": 2860,
            "image_height": 3380,
            "link": {"web_url": "https://ecos.bok.or.kr"},
        },
        "buttons": [{
            "title": "한국은행 ECOS 바로가기",
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
    <body style="background:#f8f9fa;margin:0;padding:24px;font-family:sans-serif">
      <h2 style="color:#1d3557;margin-bottom:16px">
        📊 한국·세계 경제 인포그래픽 ({today})
      </h2>
      <img src="cid:chart"
           style="max-width:100%;border-radius:10px;
                  border:1px solid #dee2e6;box-shadow:0 2px 8px rgba(0,0,0,0.1)">
      <p style="color:#6c757d;font-size:11px;margin-top:12px">
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

    image_url    = upload_imgbb(image_bytes)
    access_token = refresh_access_token()
    send_kakao(access_token, image_url, today)
    send_email(image_bytes, today)

    print(f"\n{'='*50}\n[+] 모든 전송 완료!\n{'='*50}")


if __name__ == "__main__":
    main()
