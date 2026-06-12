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

RSS_DAEGU = (
    "https://news.google.com/rss/search"
    "?q=%EB%8C%80%EA%B5%AC%EA%B2%BD%EB%B6%81+%EC%8A%A4%ED%83%80%ED%8A%B8%EC%97%85"
    "&hl=ko&gl=KR&ceid=KR:ko"
)
RSS_NATIONAL = (
    "https://news.google.com/rss/search"
    "?q=%EC%8A%A4%ED%83%80%ED%8A%B8%EC%97%85+%EC%B0%BD%EC%97%85+%ED%88%AC%EC%9E%90+%EC%A0%95%EC%B1%85"
    "&hl=ko&gl=KR&ceid=KR:ko"
)

# 반복·행사성 기사 차단
NOISE_KEYWORDS = [
    "커넥팅데이", "데모데이", "IR피칭", "채용박람회", "네트워킹데이",
    "보도자료", "설명회", "간담회", "수료식", "발대식", "모집 공고",
]


# ── 중복 판별 (단어 겹침 비율) ───────────────────────────────────────────────

def words(title: str) -> frozenset:
    """출처 제거 후 2자 이상 단어 추출"""
    t = re.sub(r"\s*[-|]\s*\S+$", "", title)          # ' - 언론사' 제거
    t = re.sub(r"[^\w]", " ", t)                       # 특수문자 → 공백
    t = re.sub(r"(개|곳|명|억|조|만|원)\b", "", t)     # 단위어 제거
    return frozenset(w for w in t.split() if len(w) >= 2)


def is_duplicate(title: str, accepted: list[str]) -> bool:
    """기존 기사들과 단어 겹침 65% 이상이면 같은 기사로 판단"""
    w = words(title)
    if not w:
        return False
    for other in accepted:
        ow = words(other)
        if not ow:
            continue
        overlap = len(w & ow) / min(len(w), len(ow))
        if overlap >= 0.65:
            return True
    return False


def is_noise(title: str) -> bool:
    return any(kw in title for kw in NOISE_KEYWORDS)


# ── RSS 파싱 ──────────────────────────────────────────────────────────────────

def fetch_rss(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        root = ET.fromstring(resp.read())
    results = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link_el = item.find("link")
        link = ""
        if link_el is not None:
            link = (link_el.text or link_el.tail or "").strip()
        if not link:
            link = (item.findtext("guid") or "").strip()
        if title:
            results.append({"title": title, "link": link})
    return results


# ── 섹션별 수집 ───────────────────────────────────────────────────────────────

def fetch_section(label: str, rss_url: str, n: int, global_accepted: list[str]) -> list[dict]:
    """
    RSS에서 n건의 고유 기사를 반환.
    global_accepted로 다른 섹션과의 교차 중복도 제거.
    Google News는 이미 관련도·최신순으로 정렬되어 있으므로
    위에서부터 순서대로 선택하면 중요한 기사가 우선됨.
    """
    print(f"\n[{label}] {n}건 수집 중...")
    try:
        items = fetch_rss(rss_url)
        print(f"  수신 {len(items)}건")
    except Exception as e:
        print(f"  오류: {e}")
        return []

    accepted_titles = list(global_accepted)
    results = []
    skipped_noise = skipped_dup = 0

    for it in items:
        if is_noise(it["title"]):
            skipped_noise += 1
            continue
        if is_duplicate(it["title"], accepted_titles):
            skipped_dup += 1
            continue
        accepted_titles.append(it["title"])
        results.append(it)
        if len(results) == n:
            break

    print(f"  노이즈 {skipped_noise}건 제거 | 중복 {skipped_dup}건 제거 | 선택 {len(results)}건")
    for r in results:
        print(f"  ✓ {r['title'][:50]}...")
    return results


# ── URL 단축 ─────────────────────────────────────────────────────────────────

def resolve_url(url: str) -> str:
    """Google News 리다이렉트를 따라가 실제 기사 URL 반환"""
    if not url:
        return url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.url   # urllib이 리다이렉트를 자동 추적
    except Exception:
        return url


def shorten(url: str) -> str:
    """TinyURL 단축 — 실패 시 원본 반환"""
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


# ── 카카오 ────────────────────────────────────────────────────────────────────

def refresh_access_token() -> tuple[str, str | None]:
    print("[토큰] 갱신 중...")
    data = {
        "grant_type":    "refresh_token",
        "client_id":     REST_API_KEY,
        "refresh_token": REFRESH_TOKEN,
    }
    body = urllib.parse.urlencode(data).encode()
    req  = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  오류 {e.code}: {e.read().decode()}")
        sys.exit(1)
    if "access_token" not in result:
        print(f"  토큰 오류: {result}")
        sys.exit(1)
    print(f"  발급 완료 (앞 10자: {result['access_token'][:10]}...)")
    return result["access_token"], result.get("refresh_token")


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

def format_section(label: str, items: list[dict]) -> str:
    lines = [f"[ {label} ]"]
    for it in items:
        entry = f"• {it['title']}"
        if it.get("short"):
            entry += f"\n  {it['short']}"
        lines.append(entry)
    return "\n\n".join(lines)


def main():
    print(f"{'='*50}")
    print(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'='*50}")

    # 1. 토큰
    access_token, new_rt = refresh_access_token()
    if new_rt:
        with open(os.environ.get("GITHUB_ENV", "/dev/null"), "a") as f:
            f.write(f"NEW_REFRESH_TOKEN={new_rt}\n")

    # 2. 뉴스 수집 — 5 + 5 고정 구조
    global_accepted: list[str] = []

    daegu = fetch_section("대구·경북 창업", RSS_DAEGU, 3, global_accepted)
    global_accepted.extend(it["title"] for it in daegu)

    national = fetch_section("전국 창업·스타트업", RSS_NATIONAL, 3, global_accepted)
    global_accepted.extend(it["title"] for it in national)

    # 3. URL 단축
    print("\n[TinyURL] 단축 중...")
    for it in daegu + national:
        it["short"] = shorten(resolve_url(it["link"]))

    # 4. 메시지 조립
    if not daegu and not national:
        body = "뉴스를 불러오지 못했습니다."
    else:
        today = datetime.now().strftime("%Y.%m.%d")
        sections = [f"🚀 오늘의 창업 뉴스 ({today})"]
        if daegu:
            sections.append(format_section("대구·경북 창업", daegu))
        if national:
            sections.append(format_section("전국 창업·스타트업", national))
        body = "\n\n".join(sections)

    print(f"\n{'='*50}\n전송 메시지 ({len(body)}자):\n{body}\n{'='*50}")

    # 5. 전송
    result = send_message(access_token, body)
    if result.get("result_code") == 0:
        print("[+] 전송 성공!")
    else:
        print(f"[!] 전송 실패: {result}")
        sys.exit(1)


if __name__ == "__main__":
    main()
