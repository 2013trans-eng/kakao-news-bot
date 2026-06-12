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

RSS_LOCAL = (
    "https://news.google.com/rss/search"
    "?q=%EB%8C%80%EA%B5%AC%EA%B2%BD%EB%B6%81+%EC%8A%A4%ED%83%80%ED%8A%B8%EC%97%85"
    "&hl=ko&gl=KR&ceid=KR:ko"
)
RSS_NATIONAL = (
    "https://news.google.com/rss/search"
    "?q=%EC%8A%A4%ED%83%80%ED%8A%B8%EC%97%85+%EC%B0%BD%EC%97%85+%ED%88%AC%EC%9E%90"
    "&hl=ko&gl=KR&ceid=KR:ko"
)


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
    t = re.sub(r"\s*[-|]\s*\S+$", "", title)
    t = re.sub(r"[\s\[\]()【】「」《》<>]", "", t)
    return t.lower()


# ── RSS 파싱 ──────────────────────────────────────────────────────────────────

def fetch_rss(url: str) -> list[dict]:
    """제목 + 링크 dict 목록 반환"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)
    results = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        # ElementTree에서 <link> 는 text가 아닌 tail로 오는 경우 있음
        link_el = item.find("link")
        if link_el is not None:
            link = (link_el.text or link_el.tail or "").strip()
        else:
            link = ""
        # guid 도 fallback
        if not link:
            link = (item.findtext("guid") or "").strip()
        results.append({"title": title, "link": link})
    return results


# ── 뉴스 수집 + 단축 ─────────────────────────────────────────────────────────

def collect_news(total: int = 10) -> tuple[list[dict], list[dict]]:
    seen = set()

    def pick(items: list[dict], limit: int) -> list[dict]:
        out = []
        for it in items:
            key = normalize(it["title"])
            if key and key not in seen:
                seen.add(key)
                out.append(it)
            if len(out) == limit:
                break
        return out

    print("[2] 대구·경북 스타트업 뉴스 수집 중...")
    try:
        local_raw = fetch_rss(RSS_LOCAL)
        print(f"  [+] {len(local_raw)}건 수신")
    except Exception as e:
        print(f"  [!] 오류: {e}")
        local_raw = []
    local = pick(local_raw, total)
    print(f"  [+] {len(local)}건 선택")

    national = []
    remain = total - len(local)
    if remain > 0:
        print(f"[3] 전국 창업 뉴스로 {remain}건 보충 중...")
        try:
            nat_raw = fetch_rss(RSS_NATIONAL)
            print(f"  [+] {len(nat_raw)}건 수신")
            national = pick(nat_raw, remain)
            print(f"  [+] {len(national)}건 선택")
        except Exception as e:
            print(f"  [!] 오류: {e}")

    # URL 단축 (TinyURL)
    print("[4] URL 단축 중...")
    for group in (local, national):
        for it in group:
            original = it["link"]
            it["short"] = shorten(original)
            print(f"  {it['short']}  ← {it['title'][:30]}...")

    return local, national


# ── 카카오 ────────────────────────────────────────────────────────────────────

def refresh_access_token() -> tuple[str, str | None]:
    print("[1] 토큰 갱신 요청 중...")
    result = post_form(TOKEN_URL, {
        "grant_type":    "refresh_token",
        "client_id":     REST_API_KEY,
        "refresh_token": REFRESH_TOKEN,
    })
    if "access_token" not in result:
        print(f"  [!] 토큰 오류: {result}")
        sys.exit(1)
    new_rt = result.get("refresh_token")
    print(f"  [+] access_token 발급 (앞 10자: {result['access_token'][:10]}...)")
    return result["access_token"], new_rt


def send_message(access_token: str, text: str) -> dict:
    print("[5] 카카오톡 전송 요청 중...")
    print(f"  메시지 길이: {len(text)}자 / 2000자 한도")
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

def format_items(items: list[dict]) -> str:
    lines = []
    for it in items:
        line = f"• {it['title']}"
        if it.get("short"):
            line += f"\n  {it['short']}"
        lines.append(line)
    return "\n\n".join(lines)


def main():
    print(f"{'='*50}")
    print(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'='*50}")

    access_token, new_rt = refresh_access_token()
    if new_rt:
        with open(os.environ.get("GITHUB_ENV", "/dev/null"), "a") as f:
            f.write(f"NEW_REFRESH_TOKEN={new_rt}\n")

    local, national = collect_news(total=10)

    if not local and not national:
        print("[!] 뉴스 없음 — 대체 메시지 전송")
        body = "• (뉴스를 불러오지 못했습니다.)"
    else:
        today = datetime.now().strftime("%Y.%m.%d")
        sections = [f"🚀 오늘의 창업 뉴스 ({today})"]
        if local:
            sections.append(f"[ 대구·경북 창업 ]\n{format_items(local)}")
        if national:
            sections.append(f"[ 전국 창업·스타트업 ]\n{format_items(national)}")
        body = "\n\n".join(sections)

    print(f"\n전송 메시지 ({len(body)}자):\n{body}\n")

    result = send_message(access_token, body)
    if result.get("result_code") == 0:
        print("[+] 카카오톡 전송 성공!")
    else:
        print(f"[!] 전송 실패: {result}")
        sys.exit(1)


if __name__ == "__main__":
    main()
