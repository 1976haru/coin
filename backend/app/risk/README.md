# risk

포지션 크기, 손실 제한, 일일 손실 한도 등 **리스크 관리** 영역.

- `RiskManager` / `PermissionGate` / `AIExecutionGate` 가 단일 주문 경로의 게이트로 동작한다.
- 다음 조건 중 하나라도 참이면 신규 BUY/진입 자동 차단:
  WebSocket reconnecting, stale data, quote missing, 환율 이상치, 거래소 공지 위험.
- 청산(SELL) 은 위험 축소 목적의 별도 정책으로 관리하며 BUY 보다 관대하다.
- RiskOfficerAgent 가 최종 거부권을 가지며 REJECT 시 어떤 주문 후보도 생성되지 않는다.
