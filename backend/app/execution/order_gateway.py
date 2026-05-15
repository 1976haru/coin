"""
OrderGateway — 모든 주문의 단일 진입점
GPT의 structured result 패턴 + idempotency + shadow 로깅

주문 흐름 (CLAUDE.md §2.4):
  submit()
    → Idempotency 중복 체크
    → Freshness 체크 (BUY만)
    → RiskManager.evaluate()
    → OrderGuard.check()    [#51]
    → PermissionGate.check()
    → route: paper | approval_queue | shadow | live | blocked
    → AuditLog 기록
"""
from __future__ import annotations
from dataclasses import dataclass
from uuid import uuid4

from app.core.config import Settings
from app.market.freshness import FreshnessStatus, should_block_new_buy
from app.risk.manager import RiskManager
from app.risk.permission_gate import PermissionGate
from app.risk.ai_execution_gate import AIExecutionGate
from app.execution.approval_queue import ApprovalQueue
from app.execution.order_guard import OrderGuard
from app.execution.order_executor import (
    OrderExecutor, PaperExecutor, ShadowExecutor, LiveExecutor,
)
from app.brokers.paper_broker import PaperBroker
from app.audit.audit_log import AuditLog


class OrderGateway:
    """
    AI·전략·프론트엔드가 브로커를 직접 호출하는 것을 차단.
    반드시 이 클래스를 통해서만 주문 가능.
    """

    def __init__(
        self,
        settings: Settings,
        risk: RiskManager | None = None,
        approvals: ApprovalQueue | None = None,
        audit: AuditLog | None = None,
        paper_broker: PaperBroker | None = None,
        guard: OrderGuard | None = None,
        ai_gate: AIExecutionGate | None = None,
    ):
        self.settings = settings
        self.risk = risk or RiskManager(
            settings.max_order_notional_usdt,
            settings.max_open_positions,
            settings.daily_loss_limit_pct,
            settings.max_leverage,
            settings.max_consecutive_losses,
            settings.re_entry_cooldown_min,
        )
        self.gate = PermissionGate(
            settings.trading_mode,
            settings.enable_live_trading,
            settings.enable_ai_execution,
            settings.enable_kimp_strategy,
        )
        # 절대 cap 은 RiskManager 한도의 5배 — 너무 큰 주문 자체 차단 (#51)
        self.guard = guard or OrderGuard(
            absolute_max_notional_usdt=max(
                settings.max_order_notional_usdt * 5.0, 1000.0,
            ),
        )
        # 체크리스트 #59 — AI 자동 실행 추가 가드
        self.ai_gate = ai_gate or AIExecutionGate()
        self.approvals   = approvals   or ApprovalQueue()
        self.audit       = audit       or AuditLog()
        self.paper       = paper_broker or PaperBroker()

        # 체크리스트 #54 — OrderExecutor 분리. route 별 실행기.
        self.executors: dict[str, OrderExecutor] = {
            "paper":           PaperExecutor(self.paper),
            "shadow":          ShadowExecutor(),       # #57 Live Shadow
            "live":            LiveExecutor(),
            "live_not_wired":  LiveExecutor(),
        }
        self._seen_keys: set[str] = set()

    def submit(
        self,
        order: dict,
        account: dict,
        freshness_statuses: list[FreshnessStatus] | None = None,
        source: str = "system",
    ) -> dict:
        """
        Returns:
            {"status": "ACCEPTED"|"REJECTED"|"BLOCKED"|"PENDING_APPROVAL"|"SHADOW_LOGGED",
             "route": str, "reason"/"reasons": ..., "audit": {...}}
        """
        # ── Idempotency ──────────────────────────────────────────
        idem_key = order.get("idempotency_key") or str(uuid4())
        if idem_key in self._seen_keys:
            return {"status": "REJECTED", "route": "idempotency",
                    "reason": "중복 idempotency_key", "audit": {}}
        self._seen_keys.add(idem_key)

        # ── Freshness (BUY 계열만) ────────────────────────────────
        is_buy = order.get("side") in {"BUY", "OPEN_REVERSE_KIMP"}
        freshness_reasons: list[str] = []
        if is_buy and freshness_statuses:
            _, freshness_reasons = should_block_new_buy(*freshness_statuses)

        # ── RiskManager ───────────────────────────────────────────
        decision = self.risk.evaluate(order, account, freshness_reasons)
        if not decision.approved:
            ev = self.audit.record("ORDER_REJECTED_BY_RISK",
                                   {"order": order, "reasons": decision.reasons})
            return {"status": "REJECTED", "route": "risk",
                    "reasons": decision.reasons, "audit": ev}

        # ── OrderGuard (#51) — pre-flight 형태/값 sanity ─────────
        guard_result = self.guard.check(order, source=source)
        if not guard_result.passed:
            ev = self.audit.record("ORDER_REJECTED_BY_GUARD",
                                   {"order": order, "reasons": list(guard_result.reasons)})
            return {"status": "REJECTED", "route": "guard",
                    "reasons": list(guard_result.reasons), "audit": ev}

        # ── PermissionGate ────────────────────────────────────────
        perm = self.gate.check(order, source=source)

        # 승인 큐 (특수 — Approval 객체 ID 가 필요해 별도 처리)
        # 체크리스트 #58 — AI Assist: source/explain 을 ApprovalItem 에 전달
        if not perm.allowed and perm.route == "approval_queue":
            item = self.approvals.add(
                order, perm.reason,
                source=source,
                agent_explain=str(order.get("agent_explain", "") or ""),
            )
            ev = self.audit.record("ORDER_QUEUED_FOR_APPROVAL",
                                   {"approval_id": item.id, "order": order,
                                    "source": source})
            return {"status": "PENDING_APPROVAL", "route": "approval_queue",
                    "approval_id": item.id, "reason": perm.reason, "audit": ev}

        # 차단 (executor 미배치 route)
        if not perm.allowed and perm.route not in self.executors:
            ev = self.audit.record("ORDER_BLOCKED_BY_PERMISSION",
                                   {"order": order, "reason": perm.reason})
            return {"status": "BLOCKED", "route": perm.route,
                    "reason": perm.reason, "audit": ev}

        # ── 체크리스트 #59 AI Execution Gate ──────────────────────
        # AI 자동 실행 (source='ai' + LIVE route) 인 경우 추가 가드 적용.
        if source == "ai" and perm.route == "live":
            ai_result = self.ai_gate.check(order)
            if not ai_result.allowed:
                ev = self.audit.record("ORDER_BLOCKED_BY_AI_GATE",
                                       {"order": order, "reasons": list(ai_result.reasons)})
                return {"status": "BLOCKED", "route": "ai_gate",
                        "reasons": list(ai_result.reasons), "audit": ev}

        # ── 체크리스트 #54 — OrderExecutor 위임 ──────────────────
        executor = self.executors.get(perm.route)
        if executor is None:
            # route 매핑 미존재 — fallback BLOCKED
            ev = self.audit.record("ORDER_BLOCKED_BY_PERMISSION",
                                   {"order": order, "reason": perm.reason or "unknown route"})
            return {"status": "BLOCKED", "route": perm.route,
                    "reason": perm.reason or "unknown route", "audit": ev}

        ex_result = executor.execute(order, audit_log=self.audit)
        # AI live 실행 성공 시 AI gate 카운터 갱신 (#59)
        if source == "ai" and perm.route == "live" and \
                ex_result.status not in {"BLOCKED", "REJECTED"}:
            self.ai_gate.record_executed(order)
        # Paper FILLED 시 RiskManager 에 진입 시각 기록 (기존 동작 유지)
        if perm.route == "paper" and is_buy and \
                ex_result.result.get("status") == "FILLED":
            self.risk.record_entry(order.get("symbol", ""))

        out: dict = {
            "status": ex_result.status,
            "route":  ex_result.route,
            "audit":  ex_result.audit_event,
        }
        if ex_result.reason:
            out["reason"] = ex_result.reason
        if ex_result.result:
            out["result"] = ex_result.result
        return out

        # 아래는 도달 불가 — guard ensures executor handled all routes.
        ev = self.audit.record("LIVE_EXECUTOR_NOT_WIRED", {"order": order})
        return {"status": "BLOCKED", "route": "live_not_wired",
                "reason": "live executor not wired — paper/shadow 검증 먼저",
                "audit": ev}

    def kill_switch(self, active: bool, reason: str = ""):
        if active:
            self.risk.activate_kill_switch(reason)
            self.audit.record("KILL_SWITCH_ACTIVATED", {"reason": reason})
        else:
            self.risk.deactivate_kill_switch()
            self.audit.record("KILL_SWITCH_DEACTIVATED", {})
