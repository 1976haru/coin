# execution

주문 생성, 주문 상태 추적, 체결 처리 영역.

> **현재 단계에서는 실제 주문 실행 미구현.**
> 모든 주문은 `PaperExecutor` / `ShadowExecutor` 로만 흐르며, 실거래 송신 경로는 비활성이다.

단일 주문 경로 (우회 금지):

```
StrategySignal → AgentReview → RiskManager → OrderGuard → PermissionGate
              → ApprovalQueue → OrderGateway → PaperExecutor / ShadowExecutor
              → BrokerAdapter → AuditLog
```

- `OrderGateway` 는 단일 진입점이며 다른 모듈은 이 경로를 우회할 수 없다.
- 모든 차단/거절은 `AuditLog.record()` 로 이벤트 기록된다.
- 실거래 활성화는 별도 수동 승인 / 환경변수 / 문서 / 테스트가 모두 통과한 후에만 가능하다.
