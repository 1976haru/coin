# governance

승인, 감사로그, 실거래 전환 조건, **안전장치** 영역.

> **실거래 전환 전 승인 / 점검 / 감사로그 필요.**
> 체크리스트 PASS 자체는 실거래 허가가 아니다. LIVE 활성화는 별도 수동 승인,
> 별도 환경변수, 별도 문서, 별도 테스트를 모두 통과한 후에만 가능하다.

- `promotion_gates` 는 모드 승격(예: PAPER → LIVE_SHADOW → LIVE_MANUAL_APPROVAL)의 사전 점검을 담당한다.
- 모든 차단/거절/승인 이벤트는 `AuditLog.record()` 로 기록된다.
- 관리 액션(모드 변경, 킬스위치, promotion)은 admin token 인증이 필요하다.
- 본 레이어는 단일 주문 경로의 PermissionGate / ApprovalQueue 와 함께 동작한다.
