# Data Freshness Policy

체크리스트 #16.

## 원칙
지연된 시세로 진입하는 사고를 사전에 차단한다. **신규 BUY는 신선한 시세에서만**, 청산(SELL)은 가능한 한 빠르게 허용.

## 임계치
- 기본 신선도: `FRESHNESS_THRESHOLD_SEC=5.0` (환경변수)
- 거래소별 조정: `app/market/freshness.py` 호출 시 인자로 override 가능

## BUY 차단 조건 (`should_block_new_buy`)
1. 시세 timestamp 부재
2. 시세 age > 임계치
3. WebSocket `connected=false`
4. WebSocket `reconnecting=true`

## SELL 정책
- freshness 체크는 OrderGateway에서 BUY/OPEN 계열만 적용
- SELL/CLOSE는 위험 축소 목적이므로 별도 가드 (예: 가격이 비정상이면 정책상 보류)

## 회귀 테스트
- `tests/test_freshness.py` — stale/reconnecting 차단
- `tests/test_order_gateway.py::test_gateway_rejects_stale_new_buy` — 통합

## 향후 확장
- #15 collector.py 와 결합해 거래소별 멀티-소스 freshness
- #17 quality.py 와 결합해 누락/이상값 탐지 시 BUY 차단
- #18 notices.py 와 결합해 공지 위험 시 BUY 차단
