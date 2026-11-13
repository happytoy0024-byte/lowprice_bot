# Danawa Lowprice Bot (베베숲 20개 특가 알리미)

다나와에서 '베베숲 20개' 상품을 실시간 검색하여, 광고 상품을 제외한 실구매가(상품가 + 배송비) 기준 최저가를 비교하고 목표가(20,000원) 이하 특가 발생 시 카카오톡 '나에게 보내기'로 알림을 전송하는 봇입니다.

## 주요 기능
- **다나와 가격비교 자동 수집**: 광고 카드를 필터링하고 실제 20개(20팩) 상품의 쇼핑몰별 실구매가를 계산합니다.
- **최저가 TOP 3 랭킹 출력**: 콘솔에 1~3위 상품명, 실구매가, 쇼핑몰, 리뷰수, 링크를 표시합니다.
- **카카오톡 나에게 보내기 연동**: 목표가(20,000원) 이하 상품 발견 시 카카오톡으로 즉시 알림 메시지를 전송합니다.
- **토큰 자동 갱신**: 만료된 액세스 토큰을 리프레시 토큰으로 자동 갱신 처리합니다.

## 설치 및 설정

1. 저장소 복제 및 이동
   ```bash
   git clone git@github.com:happytoy0024-byte/lowprice_bot.git
   cd lowprice_bot
   ```

2. `.env` 파일 설정 (`.env.example` 복사 후 본인의 키 입력)
   ```bash
   cp .env.example .env
   ```
   `.env` 파일에 아래 정보를 설정합니다:
   ```env
   KAKAO_REST_API_KEY=your_rest_api_key
   KAKAO_CLIENT_SECRET=your_client_secret
   KAKAO_REFRESH_TOKEN=your_refresh_token
   ```

## 실행 방법

- 봇 실행:
  ```bash
  python lowprice_bot.py
  ```

- 단위/모의 검증 테스트 실행:
  ```bash
  python test_lowprice_bot.py
  ```
