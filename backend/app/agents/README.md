# agents

전략 조합, 시장상황 판단, 의사결정 설명 **AI 에이전트** 영역.

- AI 에이전트는 **분석·추천·설명만** 한다. 직접 주문 금지.
- `AgentDecision` 객체는 `is_order_intent=false` 를 기본값으로 가진다.
- 본 레이어는 `app.brokers.*`, `app.execution.paper_executor`, `app.execution.shadow_executor` 를
  직접 import 하지 않는다.
- 낮은 confidence 판단은 `WATCH_ONLY` 로 처리한다.
- RiskOfficerAgent 가 최종 거부권을 가지며 REJECT 시 어떤 주문 후보도 생성되지 않는다.
