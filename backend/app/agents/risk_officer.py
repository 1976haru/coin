"""RiskOfficerAgent — 체크리스트 #37 + #38 Risk Officer Agent (full).

CLAUDE.md §2.3: RiskOfficerAgent 가 최종 거부권. REJECT 시 어떠한 주문 후보도
생성하지 않는다. 낮은 confidence 는 WATCH_ONLY 처리.

결정론 — context 의 risk 관련 플래그 + 한도 검증 + RiskManager 상태 통합.
``risk_context_from_manager`` 헬퍼로 RiskManager 와 통합 사용:

    from app.agents.risk_officer import RiskOfficerAgent
    from app.agents.risk_context import risk_context_from_manager

    ctx = risk_context_from_manager(risk_manager, order=order, account=account)
    decision = RiskOfficerAgent().decide(strategy_signal, ctx)
"""
from __future__ import annotations
from typing import Any

from .base import AgentCapability


_ENTRY_ACTIONS = frozenset({
    "BUY",
    "OPEN_REVERSE_KIMP",
    "OPEN_LONG_A_SHORT_B",
    "OPEN_SHORT_A_LONG_B",
})


class RiskOfficerAgent:
    """최종 거부권 보유 Agent. 7가지 검사를 순차 평가."""

    LOW_CONFIDENCE_THRESHOLD = 0.4
    DEFAULT_DAILY_LOSS_LIMIT = -2.0
    DEFAULT_MAX_CONSECUTIVE_LOSSES = 5

    capability = AgentCapability(
        name="risk_officer",
        role="risk_officer",
        description="최종 거부권 — kill_switch/연속손실/일손실/주문한도/레버리지/포지션수/긴급정지/저신뢰도.",
        has_veto_power=True,
        is_deterministic=True,
        requires_llm=False,
        inputs=(
            "kill_switch", "consecutive_losses", "daily_loss_pct",
            "emergency_stop", "open_positions", "max_open_positions",
            "order_notional_usdt", "max_order_notional_usdt",
            "order_leverage", "max_leverage",
        ),
    )

    def decide(self, input_signal: dict, context: dict | None = None) -> Any:
        from .orchestrator import AgentDecision
        ctx = context or {}
        action = input_signal.get("action", "HOLD")
        confidence = float(input_signal.get("confidence", 0.0))

        # 1. Kill switch (가장 먼저)
        if ctx.get("kill_switch"):
            return AgentDecision(
                "HOLD", 0.0,
                "RiskOfficerAgent veto: Kill Switch 활성화",
                risk_veto=True,
                explain_text="긴급 정지 중 — 신규 진입 불가",
            )

        # 2. Emergency stop (계좌 레벨)
        if ctx.get("emergency_stop"):
            return AgentDecision(
                "HOLD", 0.0,
                "RiskOfficerAgent veto: Emergency Stop 활성화",
                risk_veto=True,
                explain_text="계좌 긴급 정지 — 신규 진입 불가",
            )

        # 3. 연속 손실 한도
        cl = int(ctx.get("consecutive_losses", 0))
        max_cl = int(ctx.get("max_consecutive_losses", self.DEFAULT_MAX_CONSECUTIVE_LOSSES))
        if cl >= max_cl:
            return AgentDecision(
                "HOLD", 0.0,
                f"RiskOfficerAgent veto: 연속 손실 {cl}회 ≥ 한도 {max_cl}",
                risk_veto=True,
                explain_text="연속 손실로 거래 일시 중단",
            )

        # 4. 일일 손실 한도
        daily_pnl = float(ctx.get("daily_loss_pct", 0.0))
        daily_limit = float(ctx.get("daily_loss_limit_pct", self.DEFAULT_DAILY_LOSS_LIMIT))
        if daily_pnl <= daily_limit:
            return AgentDecision(
                "HOLD", 0.0,
                f"RiskOfficerAgent veto: 일 손실 {daily_pnl:.2f}% ≤ 한도 {daily_limit}%",
                risk_veto=True,
                explain_text="일일 손실 한도 도달",
            )

        # 5. 동시 포지션 한도 (ENTRY 액션만 — CLOSE/SELL 은 슬롯 늘리지 않음)
        if "open_positions" in ctx and "max_open_positions" in ctx:
            op = int(ctx["open_positions"])
            max_op = int(ctx["max_open_positions"])
            if action in _ENTRY_ACTIONS and op >= max_op:
                return AgentDecision(
                    "HOLD", 0.0,
                    f"RiskOfficerAgent veto: 동시 포지션 한도 {op}/{max_op}",
                    risk_veto=True,
                    explain_text="포지션 슬롯 가득 참",
                )

        # 6. 주문 금액 한도
        if "order_notional_usdt" in ctx and "max_order_notional_usdt" in ctx:
            n = float(ctx["order_notional_usdt"])
            max_n = float(ctx["max_order_notional_usdt"])
            if n > max_n:
                return AgentDecision(
                    "HOLD", 0.0,
                    f"RiskOfficerAgent veto: 주문 금액 초과 {n:.2f} > {max_n:.2f} USDT",
                    risk_veto=True,
                    explain_text="단일 주문 한도 초과",
                )

        # 7. 레버리지 한도
        if "order_leverage" in ctx and "max_leverage" in ctx:
            lv = float(ctx["order_leverage"])
            max_lv = float(ctx["max_leverage"])
            if lv > max_lv:
                return AgentDecision(
                    "HOLD", 0.0,
                    f"RiskOfficerAgent veto: 레버리지 초과 {lv}x > {max_lv}x",
                    risk_veto=True,
                    explain_text="레버리지 한도 초과",
                )

        # 8. 저신뢰도 — WATCH_ONLY (action 변경)
        if action not in {"BLOCKED", "HOLD"} and confidence < self.LOW_CONFIDENCE_THRESHOLD:
            return AgentDecision(
                "WATCH_ONLY", confidence,
                f"RiskOfficerAgent: 저신뢰도 ({confidence:.2f} < {self.LOW_CONFIDENCE_THRESHOLD}) — 관찰만",
                quality_score=float(input_signal.get("quality_score", 0.0)),
                explain_text="신뢰도 부족 — 진입 보류, 모니터링 모드",
            )

        # 통과
        return AgentDecision(
            action, confidence,
            "RiskOfficerAgent: 통과",
            quality_score=float(input_signal.get("quality_score", 0.0)),
            risk_veto=False,
            explain_text="리스크 한도 내",
        )
