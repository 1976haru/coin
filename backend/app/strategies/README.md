# strategies

매매전략 계산 및 **신호(Signal) 생성** 영역.

- 시장 데이터를 입력받아 `StrategySignal` 객체를 반환한다 (주문을 직접 만들지 않는다).
- 신호 객체의 필수 필드: `is_order_intent`(기본 false), `confidence`, `reason`.
- 본 레이어는 `app.brokers.*` / `app.execution.*` 를 직접 import 하지 않는다.
- 새 전략을 추가할 때는 단위 테스트와 기본 차단 회귀 테스트를 함께 작성한다.
