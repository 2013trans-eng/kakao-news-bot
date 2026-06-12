import json
import os
import sys
import urllib.request
import urllib.parse
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
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def refresh_access_token() -> tuple[str, str | None]:
    """access_token, (rotated refresh_token or None) 반환"""
    result = post_form(TOKEN_URL, {
        "grant_type":    "refresh_token",
        "client_id":     REST_API_KEY,
        "refresh_token": REFRESH_TOKEN,
    })
    new_rt = result.get("refresh_token")  # Kakao는 갱신 시점에만 새 refresh_token 발급
    return result["access_token"], new_rt


def fetch_news(n: int = 3) -> list[str]:
    req = urllib.request.Request(RSS_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        root = ET.fromstring(resp.read())
    items = root.findall(".//item")[:n]
    return [f"• {(item.findtext('title') or '').strip()}" for item in items]


def send_message(access_token: str, text: str) -> dict:
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
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 시작")

    # 1. 토큰 갱신
    access_token, new_refresh_token = refresh_access_token()
    print("[+] access_token 갱신 완료")

    # 새 refresh_token이 발급된 경우 환경변수 파일에 기록 → 워크플로에서 Secret 업데이트
    if new_refresh_token:
        print(f"::notice::NEW_REFRESH_TOKEN={new_refresh_token}")
        with open(os.environ.get("GITHUB_ENV", "/dev/null"), "a") as f:
            f.write(f"NEW_REFRESH_TOKEN={new_refresh_token}\n")

    # 2. 뉴스 수집
    news = fetch_news()
    if not news:
        print("[!] 뉴스 항목 없음 — 전송 건너뜀")
        return

    today = datetime.now().strftime("%Y.%m.%d")
    message = f"🚀 대경 창업 뉴스 ({today})\n\n" + "\n\n".join(news)
    print(f"[*] 메시지:\n{message}")

    # 3. 카카오톡 전송
    result = send_message(access_token, message)
    if result.get("result_code") == 0:
        print("[+] 카카오톡 전송 성공!")
    else:
        print(f"[!] 전송 응답: {result}")
        sys.exit(1)


if __name__ == "__main__":
    main()
