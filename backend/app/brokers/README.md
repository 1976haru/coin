# brokers

증권사/거래소 API **어댑터** 영역.

- 거래소별 REST/WebSocket 호출을 BrokerAdapter 추상 인터페이스로 감싼다.
- 시세 조회(read), 잔고 조회, 주문 송신 등 거래소 통신의 단일 경계 레이어다.
- 출금 권한이 부여된 API Key는 절대 사용하지 않는다 (`ENABLE_WITHDRAWAL`은 영구 false).
- `BrokerAdapter` 는 단일 주문 경로(`OrderGateway → PaperExecutor/ShadowExecutor`)에서만 호출된다.
- AI 에이전트 / 전략 / 프론트엔드는 brokers 를 직접 import 하지 않는다.
