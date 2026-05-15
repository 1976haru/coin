"""OrderExecutor — 체크리스트 #54 OrderExecutor.

OrderGateway 가 route 결정 후 위임할 실행기 추상화. 동일한 결과 형식으로
Paper / Shadow / Live 를 통일.

설계 원칙 (CLAUDE.md):
  - LiveExecutor 는 미연결 placeholder. 실 거래소 연결은 별도 PR + 검증 통과 후.
  - ShadowExecutor 는 주문 송신 없이 audit log 만 — Live 직전 단계 검증용 (#57).
  - Strategy / Agent / Frontend 는 OrderExecutor 직접 import 금지 (브로커층).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.audit.audit_log import AuditLog
from app.brokers.paper_broker import PaperBroker


@dataclass(frozen=True)
class ExecutorResult:
    """OrderExecutor.execute() 의 표준 결과."""

    status: str                            # FILLED / TIMEOUT / SHADOW_LOGGED / BLOCKED / ACCEPTED
    route: str                             # paper / shadow / live_not_wired / live
    reason: str = ""
    result: dict = field(default_factory=dict)   # 브로커가 반환한 raw dict
    audit_event: dict = field(default_factory=dict)


@runtime_checkable
class OrderExecutor(Protocol):
    """모든 executor 가 만족해야 할 contract."""

    name: str

    def execute(
        self,
        order: dict,
        *,
        audit_log: AuditLog | None = None,
    ) -> ExecutorResult: ...


# ── 구체 구현 ─────────────────────────────────────────────────────

class PaperExecutor:
    """PaperBroker 를 통한 가상 주문 체결."""

    name = "paper"

    def __init__(self, broker: PaperBroker | None = None):
        self.broker = broker or PaperBroker()

    def execute(
        self,
        order: dict,
        *,
        audit_log: AuditLog | None = None,
    ) -> ExecutorResult:
        result = self.broker.place_order(order)
        ev = audit_log.record("PAPER_ORDER_FILLED", result) if audit_log else {}
        broker_status = result.get("status", "")
        # OrderGateway 호환: ACCEPTED 가 외부 status, broker status 는 result 내부.
        gateway_status = "ACCEPTED" if broker_status == "FILLED" else "ACCEPTED"
        return ExecutorResult(
            status=gateway_status,
            route="paper",
            result=result,
            audit_event=ev,
        )


class ShadowExecutor:
    """체크리스트 #57 — 주문 송신 없이 audit 로깅만.

    LIVE_SHADOW 운용 모드에서 PermissionGate 가 'shadow' route 를 결정하면
    호출됨. 실제 거래소에 송신하지 않고 신호만 영구 기록.
    """

    name = "shadow"

    def execute(
        self,
        order: dict,
        *,
        audit_log: AuditLog | None = None,
    ) -> ExecutorResult:
        ev = (audit_log.record("SHADOW_SIGNAL_LOGGED", {"order": order})
              if audit_log else {})
        return ExecutorResult(
            status="SHADOW_LOGGED",
            route="shadow",
            reason="shadow logged only — 주문 송신 없음",
            audit_event=ev,
        )


class LiveExecutor:
    """LIVE 실행기 — 의도적으로 미연결.

    실제 거래소 연결은 별도 PR + 검증(Paper 4주 + Shadow 2주 + 300건) 후에만.
    호출 시 BLOCKED 결과로 안전 차단.
    """

    name = "live"

    def execute(
        self,
        order: dict,
        *,
        audit_log: AuditLog | None = None,
    ) -> ExecutorResult:
        ev = (audit_log.record("LIVE_EXECUTOR_NOT_WIRED", {"order": order})
              if audit_log else {})
        return ExecutorResult(
            status="BLOCKED",
            route="live_not_wired",
            reason="live executor not wired — paper/shadow 검증 먼저",
            audit_event=ev,
        )
