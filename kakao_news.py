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
RSS_URL   = (
    "https://news.google.com/rss/search"
    "?q=%EB%8C%80%EA%B5%AC%EA%B2%BD%EB%B6%81+%EC%8A%A4%ED%83%80%ED%8A%B8%EC%97%85"
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
    """출처(` - 언론사명`) 제거 후 공백·특수문자 정규화 → 중복 판별 키"""
    title = re.sub(r"\s*-\s*\S+$", "", title)   # " - 뉴스1" 등 제거
    title = re.sub(r"[\s\[\]()【】「」]", "", title)  # 공백·괄호 제거
    return title.lower()


def fetch_news(want: int = 5) -> list[str]:
    """중복 제거 후 want건의 고유 뉴스를 반환"""
    print("[2] Google News RSS 요청 중...")
    try:
        req = urllib.request.Request(RSS_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        print(f"  [+] RSS 응답 {len(raw)} bytes")
        root = ET.fromstring(raw)
        all_items = root.findall(".//item")
        print(f"  [+] 전체 항목 {len(all_items)}건")

        seen, results = set(), []
        for item in all_items:
            title = (item.findtext("title") or "").strip()
            key   = normalize(title)
            if key and key not in seen:
                seen.add(key)
                results.append(f"• {title}")
            if len(results) == want:
                break

        print(f"  [+] 중복 제거 후 {len(results)}건 선택")
        for r in results:
            print(f"    {r}")
        return results
    except Exception as e:
        print(f"  [!] RSS 오류: {e}")
        return []


def send_message(access_token: str, text: str) -> dict:
    print("[3] 카카오톡 전송 요청 중...")
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

    # 2. 뉴스 수집 (중복 제거, 5건)
    news = fetch_news(want=5)
    if not news:
        print("[!] 뉴스 항목 없음 — 대체 메시지 전송")
        news = ["• (뉴스를 불러오지 못했습니다. 연결을 확인하세요.)"]

    today = datetime.now().strftime("%Y.%m.%d")
    message = f"🚀 대경 창업 뉴스 ({today})\n\n" + "\n\n".join(news)
    print(f"\n전송 메시지:\n{message}\n")

    # 3. 카카오톡 전송
    result = send_message(access_token, message)
    if result.get("result_code") == 0:
        print("[+] 카카오톡 전송 성공!")
    else:
        print(f"[!] 전송 실패 응답: {result}")
        sys.exit(1)


if __name__ == "__main__":
    main()
