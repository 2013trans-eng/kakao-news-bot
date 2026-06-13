import html
import json
import os
import re
import sys
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

REST_API_KEY        = os.environ["KAKAO_REST_API_KEY"]
REFRESH_TOKEN       = os.environ["KAKAO_REFRESH_TOKEN"]
NAVER_CLIENT_ID     = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]

TOKEN_URL        = "https://kauth.kakao.com/oauth/token"
SEND_URL         = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
NAVER_SEARCH_URL = "https://openapi.naver.com/v1/search/news.json"

QUERY   = "대구경북 경제"
MAX_NUM = 8

NOISE_KEYWORDS = [
    "커넥팅데이", "데모데이", "IR피칭", "채용박람회", "네트워킹데이",
    "보도자료", "설명회", "간담회", "수료식", "발대식", "모집 공고",
    # 창업·IT 뉴스와 겹침 방지
    "스타트업", "창업지원", "예비창업", "초기창업", "창업패키지",
]

# 엔티티 캡에서 제외할 일반 명사 (인물·기관명 아닌 것)
ENTITY_STOPWORDS = {
    "경제", "뉴스", "기업", "투자", "정책", "대구", "경북", "대구경북",
    "한국", "정부", "지원", "발표", "추진", "확대", "사업", "지역",
    "성장", "올해", "내년", "시장", "산업", "관련", "진행", "예정",
    "계획", "효과", "결과", "문제", "상황", "방안", "개선", "강화",
}


# ── 중복 판별 ─────────────────────────────────────────────────────────────────

def words(title: str) -> frozenset:
    t = re.sub(r"\s*[-|]\s*\S+$", "", title)
    t = re.sub(r"[^\w]", " ", t)
    t = re.sub(r"(개|곳|명|억|조|만|원)\b", "", t)
    result = set()
    for w in t.split():
        if len(w) >= 2:
            # 조사 제거: 어근만 비교해 '추경호가' == '추경호는' 처리
            w = re.sub(r"(에서|으로|이|가|은|는|을|를|의|에|로|과|와|도|만)$", "", w)
        if len(w) >= 2:
            result.add(w)
    return frozenset(result)


def is_duplicate(title: str, accepted: list[str]) -> bool:
    w = words(title)
    if not w:
        return False
    for other in accepted:
        ow = words(other)
        if not ow:
            continue
        if len(w & ow) / min(len(w), len(ow)) >= 0.50:
            return True
    return False


def is_noise(title: str) -> bool:
    return any(kw in title for kw in NOISE_KEYWORDS)


def is_fresh(pub_date_str: str) -> bool:
    """24시간 이내 발행 기사인지 확인"""
    try:
        pub_dt = parsedate_to_datetime(pub_date_str)
        age_hours = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
        return age_hours <= 24
    except Exception:
        return True


# ── 네이버 뉴스 검색 ──────────────────────────────────────────────────────────

def fetch_naver_news(query: str) -> list[dict]:
    params = urllib.parse.urlencode({
        "query":   query,
        "display": 30,
        "sort":    "date",
    })
    req = urllib.request.Request(f"{NAVER_SEARCH_URL}?{params}")
    req.add_header("X-Naver-Client-Id",     NAVER_CLIENT_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_CLIENT_SECRET)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise Exception(f"Naver API HTTP {e.code}: {e.read().decode()}")
    results = []
    for item in data.get("items", []):
        title    = html.unescape(re.sub(r"<[^>]+>", "", item.get("title", ""))).strip()
        link     = item.get("link", "").strip()
        pub_date = item.get("pubDate", "")
        if title and link:
            results.append({"title": title, "link": link, "pubDate": pub_date})
    return results


def fetch_news(n: int) -> list[dict]:
    print(f"\n[대구·경북 경제] {n}건 수집 중...")
    try:
        items = fetch_naver_news(QUERY)
        print(f"  수신 {len(items)}건")
    except Exception as e:
        print(f"  오류: {e}")
        return []

    accepted, results = [], []
    entity_count: dict[str, int] = {}
    skipped_noise = skipped_dup = skipped_entity = skipped_old = 0

    for it in items:
        if not is_fresh(it["pubDate"]):
            skipped_old += 1
            continue
        if is_noise(it["title"]):
            skipped_noise += 1
            continue
        if is_duplicate(it["title"], accepted):
            skipped_dup += 1
            continue
        # 인물·기관명 캡: 같은 고유명사가 이미 1건 있으면 제외
        ws = words(it["title"])
        entities = [w for w in ws if len(w) >= 3 and w not in ENTITY_STOPWORDS]
        if any(entity_count.get(e, 0) >= 1 for e in entities):
            skipped_entity += 1
            continue
        for e in entities:
            entity_count[e] = entity_count.get(e, 0) + 1
        accepted.append(it["title"])
        results.append(it)
        if len(results) == n:
            break

    print(f"  오래된 기사 {skipped_old}건 | 노이즈 {skipped_noise}건 | 중복 {skipped_dup}건 | 엔티티캡 {skipped_entity}건 제거 | 선택 {len(results)}건")
    for r in results:
        print(f"  ✓ {r['title'][:55]}")
    return results


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
        "link":        {"web_url": "https://news.naver.com"},
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


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print(f"{'='*50}")
    print(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'='*50}")

    access_token, new_rt = refresh_access_token()
    if new_rt:
        with open(os.environ.get("GITHUB_ENV", "/dev/null"), "a") as f:
            f.write(f"NEW_REFRESH_TOKEN={new_rt}\n")

    items = fetch_news(MAX_NUM)

    if not items:
        body = "뉴스를 불러오지 못했습니다."
    else:
        today = datetime.now().strftime("%Y.%m.%d")
        lines = [f"📈 대구·경북 경제 뉴스 ({today})\n"]
        for it in items:
            lines.append(f"• {it['title']}\n  {it['link']}")
        body = "\n\n".join(lines)

    print(f"\n{'='*50}\n전송 메시지 ({len(body)}자):\n{body}\n{'='*50}")

    result = send_message(access_token, body)
    if result.get("result_code") == 0:
        print("[+] 전송 성공!")
    else:
        print(f"[!] 전송 실패: {result}")
        sys.exit(1)


if __name__ == "__main__":
    main()
