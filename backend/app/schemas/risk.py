"""리스크/계좌 스냅샷 스키마 — 체크리스트 #8 Shared Schemas.

RiskDecision은 RiskManager에서 정의되어 있으며, 단일 진입점 제공을 위해 여기서
재export한다. AccountSnapshot은 RiskManager.evaluate()가 받는 계좌 상태의
정규 형태로, 신규 코드는 .to_dict()로 변환해 전달한다.
"""
from __future__ import annotations
from dataclasses import dataclass

from app.risk.manager import RiskDecision

__all__ = ["RiskDecision", "AccountSnapshot"]


@dataclass(frozen=True)
class AccountSnapshot:
    """RiskManager.evaluate()가 받는 계좌 스냅샷 정규 형태."""

    open_positions: int = 0
    daily_pnl_pct: float = 0.0
    emergency_stop: bool = False
    equity_usdt: float = 0.0

    def to_dict(self) -> dict:
        return {
            "open_positions": self.open_positions,
            "daily_pnl_pct": self.daily_pnl_pct,
            "emergency_stop": self.emergency_stop,
            "equity_usdt": self.equity_usdt,
        }
