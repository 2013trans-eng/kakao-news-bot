"""
OneDrive 초기 토큰 발급 스크립트
Azure 앱 등록 후 딱 한 번만 실행하면 됩니다.
실행: python get_onedrive_token.py
"""
import urllib.request
import urllib.parse
import json

CLIENT_ID = input("Azure App의 Client ID를 입력하세요: ").strip()

# Device code 요청
body = urllib.parse.urlencode({
    "client_id": CLIENT_ID,
    "scope": "Files.ReadWrite offline_access",
}).encode()
req = urllib.request.Request(
    "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode",
    data=body, method="POST",
    headers={"Content-Type": "application/x-www-form-urlencoded"},
)
data = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())

print(f"\n[ 1단계 ] 브라우저에서 아래 주소 열기:")
print(f"          {data['verification_uri']}")
print(f"\n[ 2단계 ] 코드 입력: {data['user_code']}")
print(f"\n로그인 & 권한 허용 완료 후 여기서 엔터 누르세요...")
input()

# 토큰 교환
body = urllib.parse.urlencode({
    "grant_type":  "urn:ietf:params:oauth2:grant-type:device_code",
    "client_id":   CLIENT_ID,
    "device_code": data["device_code"],
}).encode()
req = urllib.request.Request(
    "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
    data=body, method="POST",
    headers={"Content-Type": "application/x-www-form-urlencoded"},
)
tokens = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())

print("\n" + "="*60)
print("GitHub Secrets에 아래 두 값을 저장하세요:")
print("="*60)
print(f"\nONEDRIVE_CLIENT_ID:\n  {CLIENT_ID}")
print(f"\nONEDRIVE_REFRESH_TOKEN:\n  {tokens.get('refresh_token', '오류 — 다시 시도')}")
print("="*60)
