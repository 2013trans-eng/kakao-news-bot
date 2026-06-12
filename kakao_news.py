import json
import os
import re
import sys
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime

REST_API_KEY  = os.environ["KAKAO_REST_API_KEY"]
REFRESH_TOKEN = os.environ["KAKAO_REFRESH_TOKEN"]

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_URL  = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

# 우선순위 순서대로 정의
# 반복·행사성 기사 필터 키워드
NOISE_KEYWORDS = [
    "커넥팅데이", "채용박람회", "보도자료", "IR피칭", "데모데이",
    "네트워킹데이", "설명회", "간담회", "수료식", "발대식",
]

RSS_SOURCES = [
    {
        "label": "대구·경북 창업",
        "url": (
            "https://news.google.com/rss/search"
            "?q=%EB%8C%80%EA%B5%AC%EA%B2%BD%EB%B6%81+%EC%8A%A4%ED%83%80%ED%8A%B8%EC%97%85"
            "&hl=ko&gl=KR&ceid=KR:ko"
        ),
    },
    {
        "label": "전국 창업·스타트업",
        "url": (
            "https://news.google.com/rss/search"
            "?q=%EC%8A%A4%ED%83%80%ED%8A%B8%EC%97%85+%EC%B0%BD%EC%97%85+%ED%88%AC%EC%9E%90"
            "&hl=ko&gl=KR&ceid=KR:ko"
        ),
    },
    {
        "label": "기술·IT",
        "url": (
            "https://news.google.com/rss/search"
            "?q=AI+%EA%B8%B0%EC%88%A0+IT+%EC%8A%A4%ED%83%80%ED%8A%B8%EC%97%85"
            "&hl=ko&gl=KR&ceid=KR:ko"
        ),
    },
]


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req  = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
            print(f"  [HTTP {resp.status}] {url}")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        print(f"  [HTTPError {e.code}] {url} → {e.read().decode()}")
        raise


def shorten(url: str) -> str:
    """TinyURL 무료 API — 실패 시 원본 반환"""
    if not url:
        return ""
    try:
        api = "https://tinyurl.com/api-create.php?url=" + urllib.parse.quote(url, safe="")
        req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            short = resp.read().decode().strip()
        return short if short.startswith("http") else url
    except Exception:
        return url


def normalize(title: str) -> str:
    """출처·조사·공백 제거 → 중복 판별 키"""
    t = re.sub(r"\s*[-|]\s*[^\s].{0,15}$", "", title)   # ' - 언론사' 제거
    t = re.sub(r"[\s\[\]()【】「」《》<>''\"·…,]", "", t)   # 공백·특수문자 제거
    t = re.sub(r"(개|곳|명|억|조|만)\b", "", t)             # 단위 표현 통일
    return t.lower()


def is_duplicate(key: str, seen: set) -> bool:
    """
    1) 완전 일치
    2) 한쪽이 다른 쪽의 앞부분(startswith) → 같은 기사 다른 길이
    3) 앞 16자 일치 → 미묘한 표현 차이 제거 (37곳 / 37개 등)
    """
    if key in seen:
        return True
    for s in seen:
        short, long = (key, s) if len(key) <= len(s) else (s, key)
        if len(short) >= 10 and long.startswith(short):
            return True
    if len(key) >= 16:
        if any(s[:16] == key[:16] for s in seen if len(s) >= 16):
            return True
    return False


def is_noise(title: str) -> bool:
    return any(kw in title for kw in NOISE_KEYWORDS)


# ── RSS 파싱 ──────────────────────────────────────────────────────────────────

def fetch_rss(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)
    results = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link_el = item.find("link")
        if link_el is not None:
            link = (link_el.text or link_el.tail or "").strip()
        else:
            link = (item.findtext("guid") or "").strip()
        if title:
            results.append({"title": title, "link": link})
    return results


# ── 뉴스 수집 ────────────────────────────────────────────────────────────────

def collect_news(total: int = 10) -> list[dict]:
    """
    RSS_SOURCES 순서대로 수집해 total건을 채운다.
    각 소스별로 label을 붙여 섹션 구분에 활용.
    """
    seen: set[str] = set()
    collected: list[dict] = []

    for source in RSS_SOURCES:
        remain = total - len(collected)
        if remain <= 0:
            break

        print(f"\n[RSS] {source['label']} — {remain}건 필요")
        try:
            items = fetch_rss(source["url"])
            print(f"  수신 {len(items)}건")
        except Exception as e:
            print(f"  오류: {e}")
            continue

        picked = 0
        for it in items:
            if is_noise(it["title"]):
                continue
            key = normalize(it["title"])
            if not key or is_duplicate(key, seen):
                continue
            seen.add(key)
            it["label"] = source["label"]
            collected.append(it)
            picked += 1
            if picked == remain:
                break

        print(f"  중복 제거 후 {picked}건 선택 (누적 {len(collected)}건)")

    # URL 단축
    print(f"\n[TinyURL] {len(collected)}건 단축 중...")
    for it in collected:
        it["short"] = shorten(it["link"])
        print(f"  {it['short']}  ← {it['title'][:35]}...")

    return collected


# ── 카카오 ────────────────────────────────────────────────────────────────────

def refresh_access_token() -> tuple[str, str | None]:
    print("[토큰] 갱신 요청 중...")
    result = post_form(TOKEN_URL, {
        "grant_type":    "refresh_token",
        "client_id":     REST_API_KEY,
        "refresh_token": REFRESH_TOKEN,
    })
    if "access_token" not in result:
        print(f"  오류: {result}")
        sys.exit(1)
    new_rt = result.get("refresh_token")
    print(f"  발급 완료 (앞 10자: {result['access_token'][:10]}...)")
    return result["access_token"], new_rt


def send_message(access_token: str, text: str) -> dict:
    print(f"\n[카카오] 전송 — {len(text)}자 / 2000자 한도")
    template = {
        "object_type": "text",
        "text":        text[:2000],
        "link":        {"web_url": "https://news.google.com"},
    }
    payload = urllib.parse.urlencode(
        {"template_object": json.dumps(template, ensure_ascii=False)}
    ).encode("utf-8")
    req = urllib.request.Request(SEND_URL, data=payload, method="POST")
    req.add_header("Authorization",  f"Bearer {access_token}")
    req.add_header("Content-Type",   "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode()
            print(f"  [HTTP {resp.status}] {raw}")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        print(f"  [HTTPError {e.code}] {e.read().decode()}")
        raise


# ── 메시지 조립 ───────────────────────────────────────────────────────────────

def build_message(news: list[dict]) -> str:
    today = datetime.now().strftime("%Y.%m.%d")
    lines = [f"🚀 오늘의 창업 뉴스 ({today})"]

    # 소스 레이블별로 묶어서 섹션 구성
    current_label = None
    section_items: list[str] = []

    def flush(label, items):
        if items:
            return [f"\n[ {label} ]"] + items
        return []

    for it in news:
        label = it.get("label", "기타")
        item_text = f"• {it['title']}"
        if it.get("short"):
            item_text += f"\n  {it['short']}"

        if label != current_label:
            lines.extend(flush(current_label, section_items))
            current_label = label
            section_items = [item_text]
        else:
            section_items.append(item_text)

    lines.extend(flush(current_label, section_items))
    return "\n\n".join(lines)


def main():
    print(f"{'='*50}")
    print(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'='*50}\n")

    access_token, new_rt = refresh_access_token()
    if new_rt:
        with open(os.environ.get("GITHUB_ENV", "/dev/null"), "a") as f:
            f.write(f"NEW_REFRESH_TOKEN={new_rt}\n")

    news = collect_news(total=10)

    if not news:
        body = "뉴스를 불러오지 못했습니다. 연결을 확인하세요."
    else:
        body = build_message(news)

    print(f"\n{'='*50}\n전송 메시지 ({len(body)}자):\n{body}\n{'='*50}")

    result = send_message(access_token, body)
    if result.get("result_code") == 0:
        print("[+] 카카오톡 전송 성공!")
    else:
        print(f"[!] 전송 실패: {result}")
        sys.exit(1)


if __name__ == "__main__":
    main()
