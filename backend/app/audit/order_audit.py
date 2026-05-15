"""OrderAuditLog — 주문 lifecycle 전용 감사 facade — 체크리스트 #11.

기본 AuditLog를 위임받아 주문 이벤트를 정형화된 타입으로 기록한다.
OrderGateway가 직접 호출해도 되고, 외부에서 결과 dict를 받아 매핑해도 된다.

표준 이벤트 타입:
  ORDER_INTENT                신호 → 주문 의도 발생 시점
  ORDER_SUBMITTED             OrderGateway.submit() 진입
  ORDER_REJECTED_BY_RISK
  ORDER_BLOCKED_BY_PERMISSION
  ORDER_QUEUED_FOR_APPROVAL
  ORDER_APPROVED
  ORDER_DENIED
  PAPER_ORDER_FILLED
  SHADOW_SIGNAL_LOGGED
  LIVE_EXECUTOR_NOT_WIRED
"""
from __future__ import annotations

from .audit_log import AuditLog


# OrderGateway.submit() 결과의 (status, route) → 정규 이벤트 타입 매핑
_RESULT_EVENT_MAP: dict[tuple[str, str], str] = {
    ("ACCEPTED", "paper"):                "PAPER_ORDER_FILLED",
    ("REJECTED", "risk"):                 "ORDER_REJECTED_BY_RISK",
    ("REJECTED", "idempotency"):          "ORDER_REJECTED_BY_IDEMPOTENCY",
    ("BLOCKED", "live_not_wired"):        "LIVE_EXECUTOR_NOT_WIRED",
    ("PENDING_APPROVAL", "approval_queue"): "ORDER_QUEUED_FOR_APPROVAL",
    ("SHADOW_LOGGED", "shadow"):          "SHADOW_SIGNAL_LOGGED",
}


class OrderAuditLog:
    """주문 lifecycle 이벤트를 정형화해 AuditLog에 기록하는 facade."""

    def __init__(self, audit: AuditLog | None = None):
        self.audit = audit or AuditLog()

    def record_intent(self, signal: dict, source: str = "system") -> dict:
        """신호 → 주문 의도 발생."""
        return self.audit.record("ORDER_INTENT", {
            "signal": signal,
            "source": source,
        })

    def record_submitted(self, order: dict, source: str = "system") -> dict:
        """OrderGateway.submit() 진입 직후."""
        return self.audit.record("ORDER_SUBMITTED", {
            "order": order,
            "source": source,
        })

    def record_result(self, gateway_result: dict, order: dict | None = None) -> dict:
        """OrderGateway.submit() 반환 dict를 정형 이벤트로 매핑해 기록.

        매핑되지 않는 (status, route) 조합은 ``ORDER_RESULT_OTHER`` 로 기록한다.
        """
        status = str(gateway_result.get("status", ""))
        route  = str(gateway_result.get("route", ""))
        # status가 BLOCKED이고 route가 매핑에 없으면 BLOCKED_BY_PERMISSION으로
        event_type = _RESULT_EVENT_MAP.get((status, route))
        if event_type is None:
            if status == "BLOCKED":
                event_type = "ORDER_BLOCKED_BY_PERMISSION"
            else:
                event_type = "ORDER_RESULT_OTHER"

        payload = {"result": gateway_result}
        if order is not None:
            payload["order"] = order
        return self.audit.record(event_type, payload)

    def record_approval_outcome(self, approval_id: str, approved: bool,
                                approver: str = "manual") -> dict:
        """수동 승인 큐의 승인/거부 결과 기록."""
        return self.audit.record(
            "ORDER_APPROVED" if approved else "ORDER_DENIED",
            {"approval_id": approval_id, "approver": approver},
        )

    def lifecycle(self, idempotency_key: str) -> list[dict]:
        """특정 주문의 전체 이벤트 시퀀스 추출 (idempotency_key 기준)."""
        events: list[dict] = []
        for e in self.audit.events:
            payload = e.get("payload", {}) or {}
            order = payload.get("order") or {}
            result = payload.get("result") or {}
            if (order.get("idempotency_key") == idempotency_key
                    or result.get("idempotency_key") == idempotency_key):
                events.append(e)
        return events

    def tail(self, limit: int = 100) -> list[dict]:
        """주문 관련 이벤트만 필터링해 최근 limit 건 반환."""
        order_events = [
            e for e in self.audit.events
            if e["event_type"].startswith(("ORDER_", "PAPER_ORDER", "SHADOW_", "LIVE_EXECUTOR_"))
        ]
        return order_events[-limit:]
