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

# 1순위: 대구·경북 스타트업
RSS_LOCAL = (
    "https://news.google.com/rss/search"
    "?q=%EB%8C%80%EA%B5%AC%EA%B2%BD%EB%B6%81+%EC%8A%A4%ED%83%80%ED%8A%B8%EC%97%85"
    "&hl=ko&gl=KR&ceid=KR:ko"
)
# 2순위: 전국 창업·스타트업
RSS_NATIONAL = (
    "https://news.google.com/rss/search"
    "?q=%EC%8A%A4%ED%83%80%ED%8A%B8%EC%97%85+%EC%B0%BD%EC%97%85+%ED%88%AC%EC%9E%90"
    "&hl=ko&gl=KR&ceid=KR:ko"
)


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
        body_err = e.read().decode()
        print(f"  [HTTPError {e.code}] {url} → {body_err}")
        raise


def refresh_access_token() -> tuple[str, str | None]:
    print("[1] 토큰 갱신 요청 중...")
    result = post_form(TOKEN_URL, {
        "grant_type":    "refresh_token",
        "client_id":     REST_API_KEY,
        "refresh_token": REFRESH_TOKEN,
    })
    if "access_token" not in result:
        print(f"  [!] 토큰 응답 오류: {result}")
        sys.exit(1)
    new_rt = result.get("refresh_token")
    print(f"  [+] access_token 발급 완료 (앞 10자: {result['access_token'][:10]}...)")
    if new_rt:
        print("  [+] 새 refresh_token 발급됨")
    return result["access_token"], new_rt


def normalize(title: str) -> str:
    """출처 제거 + 공백·특수문자 제거 → 중복 판별 키"""
    title = re.sub(r"\s*[-|]\s*\S+$", "", title)        # " - 뉴스1", " | 한경" 등 제거
    title = re.sub(r"[\s\[\]()【】「」《》<>]", "", title)  # 공백·괄호 제거
    return title.lower()


def fetch_rss(url: str) -> list[str]:
    """RSS URL에서 전체 제목 목록을 반환"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)
    return [(item.findtext("title") or "").strip() for item in root.findall(".//item")]


def collect_news(total: int = 10) -> tuple[list[str], list[str]]:
    """
    대구·경북 뉴스를 우선 수집하고, 부족분을 전국 뉴스로 채운다.
    반환: (대경 뉴스 리스트, 전국 뉴스 리스트)
    """
    seen = set()

    def pick(titles: list[str], limit: int) -> list[str]:
        results = []
        for t in titles:
            key = normalize(t)
            if key and key not in seen:
                seen.add(key)
                results.append(f"• {t}")
            if len(results) == limit:
                break
        return results

    print("[2] 대구·경북 스타트업 뉴스 수집 중...")
    try:
        local_raw = fetch_rss(RSS_LOCAL)
        print(f"  [+] {len(local_raw)}건 수신")
    except Exception as e:
        print(f"  [!] 오류: {e}")
        local_raw = []

    local_news = pick(local_raw, total)
    print(f"  [+] 중복 제거 후 {len(local_news)}건 선택")

    national_news = []
    remaining = total - len(local_news)
    if remaining > 0:
        print(f"[3] 전국 창업 뉴스로 {remaining}건 보충 중...")
        try:
            national_raw = fetch_rss(RSS_NATIONAL)
            print(f"  [+] {len(national_raw)}건 수신")
            national_news = pick(national_raw, remaining)
            print(f"  [+] 중복 제거 후 {len(national_news)}건 선택")
        except Exception as e:
            print(f"  [!] 오류: {e}")

    return local_news, national_news


def send_message(access_token: str, text: str) -> dict:
    print("[4] 카카오톡 전송 요청 중...")
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
            print(f"  [HTTP {resp.status}] 응답: {raw}")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()
        print(f"  [HTTPError {e.code}] {body_err}")
        raise


def main():
    print(f"{'='*50}")
    print(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'='*50}")

    # 1. 토큰 갱신
    access_token, new_refresh_token = refresh_access_token()
    if new_refresh_token:
        with open(os.environ.get("GITHUB_ENV", "/dev/null"), "a") as f:
            f.write(f"NEW_REFRESH_TOKEN={new_refresh_token}\n")

    # 2 & 3. 뉴스 수집 (대경 우선, 전국으로 보충, 총 10건)
    local_news, national_news = collect_news(total=10)
    all_news = local_news + national_news

    if not all_news:
        print("[!] 뉴스 없음 — 대체 메시지 전송")
        all_news = ["• (뉴스를 불러오지 못했습니다. 연결을 확인하세요.)"]

    today = datetime.now().strftime("%Y.%m.%d")
    sections = []
    if local_news:
        sections.append("[ 대구·경북 창업 ]\n" + "\n\n".join(local_news))
    if national_news:
        sections.append("[ 전국 창업·스타트업 ]\n" + "\n\n".join(national_news))

    message = f"🚀 오늘의 창업 뉴스 ({today})\n\n" + "\n\n".join(sections)
    print(f"\n전송 메시지:\n{message}\n")

    # 4. 카카오톡 전송
    result = send_message(access_token, message)
    if result.get("result_code") == 0:
        print("[+] 카카오톡 전송 성공!")
    else:
        print(f"[!] 전송 실패 응답: {result}")
        sys.exit(1)


if __name__ == "__main__":
    main()
