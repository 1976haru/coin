"""RiskManager → Agent context 변환 — 체크리스트 #38.

RiskOfficerAgent 가 결정 시 사용할 context dict 를 RiskManager 상태와 주문/계좌
스냅샷으로부터 생성한다.

설계:
  - 함수형 헬퍼 — RiskOfficerAgent 와 RiskManager 의 결합도 최소화.
  - app.agents.* 는 app.risk.* 를 import 가능 (CLAUDE.md §3.1 — 금지 대상은
    brokers/execution 만).
  - 본 모듈이 그 경계를 명확히 표시 (agents → risk OK, risk → agents 금지).
"""
from __future__ import annotations
from typing import Any


def risk_context_from_manager(
    risk_manager: Any,
    *,
    order: dict | None = None,
    account: dict | None = None,
) -> dict:
    """RiskManager 상태 + (옵션) order/account → RiskOfficerAgent ctx dict.

    Parameters
    ----------
    risk_manager:
        ``app.risk.manager.RiskManager`` 인스턴스 (또는 동일 인터페이스).
    order:
        평가할 주문 dict (notional_usdt, leverage 등).
    account:
        계좌 스냅샷 dict (open_positions, emergency_stop).

    Returns
    -------
    dict — RiskOfficerAgent.decide(signal, ctx) 의 ctx 인자에 그대로 전달 가능.
    """
    status = risk_manager.status()
    ctx: dict = {
        "kill_switch":             status.get("kill_switch", False),
        "consecutive_losses":      status.get("consecutive_losses", 0),
        "daily_loss_pct":          status.get("daily_pnl_pct", 0.0),
        "max_consecutive_losses":  risk_manager.max_consecutive_losses,
        "daily_loss_limit_pct":    -risk_manager.daily_loss_limit_pct,
        "max_order_notional_usdt": risk_manager.max_order_notional_usdt,
        "max_leverage":            risk_manager.max_leverage,
        "max_open_positions":      risk_manager.max_open_positions,
    }
    if account is not None:
        ctx["open_positions"]  = int(account.get("open_positions", 0))
        ctx["emergency_stop"]  = bool(account.get("emergency_stop", False))
    if order is not None:
        ctx["order_notional_usdt"] = float(order.get("notional_usdt", 0) or 0)
        ctx["order_leverage"]      = float(order.get("leverage", 1) or 1)
    return ctx
