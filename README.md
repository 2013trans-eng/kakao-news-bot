# 대경 창업 뉴스 카카오톡 봇

매일 아침 9시(KST)에 대구·경북 스타트업 관련 Google 뉴스 헤드라인을 카카오톡 '나에게 보내기'로 자동 전송합니다.

## 동작 방식

```
GitHub Actions (매일 09:00 KST)
  └─ kakao_news.py
       ├─ 1. Kakao OAuth refresh_token → 새 access_token 발급
       ├─ 2. Google News RSS 파싱 (대구경북 스타트업, 최신 3건)
       └─ 3. KakaoTalk '나에게 보내기' API 전송
```

## 설정 방법

### 1단계 — Kakao 인증 코드 발급

브라우저에서 아래 URL에 접속(카카오 계정 로그인 필요):

```
https://kauth.kakao.com/oauth/authorize?client_id=904e2d55da7c9d9163195a973853463f&redirect_uri=https://www.naver.com&response_type=code&scope=talk_message
```

리다이렉트된 URL에서 `?code=` 뒤의 값을 복사합니다.

### 2단계 — 초기 토큰 발급

터미널에서 아래 명령어 실행 (`YOUR_CODE` 교체):

```bash
curl -X POST https://kauth.kakao.com/oauth/token \
  -d "grant_type=authorization_code" \
  -d "client_id=904e2d55da7c9d9163195a973853463f" \
  -d "redirect_uri=https://www.naver.com" \
  -d "code=YOUR_CODE"
```

응답 JSON에서 `refresh_token` 값을 복사합니다.

### 3단계 — GitHub Secrets 등록

저장소 **Settings → Secrets and variables → Actions** 에서 아래 두 Secret을 추가합니다:

| Secret 이름 | 값 |
|---|---|
| `KAKAO_REST_API_KEY` | `904e2d55da7c9d9163195a973853463f` |
| `KAKAO_REFRESH_TOKEN` | 2단계에서 복사한 refresh_token |

### (선택) 4단계 — Refresh Token 자동 갱신

Kakao는 refresh_token 만료 1개월 전부터 새 refresh_token을 함께 발급합니다.
자동 갱신을 원하면 `repo` 권한을 가진 GitHub Personal Access Token을 `GH_PAT` Secret으로 추가하세요.

## 수동 실행

Actions 탭 → **대경 창업 뉴스 카카오톡 전송** → **Run workflow**

## 파일 구조

```
.
├── kakao_news.py                        # 뉴스 수집 & 전송 스크립트
└── .github/workflows/kakao-news.yml    # GitHub Actions 워크플로
```
