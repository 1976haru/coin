"""리스크/계좌 스냅샷 스키마 — 체크리스트 #8 Shared Schemas.

RiskDecision은 RiskManager에서 정의되어 있으며, 단일 진입점 제공을 위해 여기서
재export한다. AccountSnapshot은 RiskManager.evaluate()가 받는 계좌 상태의
정규 형태로, 신규 코드는 .to_dict()로 변환해 전달한다.

신규: `RiskCheckResult` (Pydantic v2) — API 응답 + 강제 invariant:
  risk_level == blocked  ⇒  allowed must be False.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import List

from pydantic import Field, model_validator

from app.risk.manager import RiskDecision

from .common import ConfiguredBaseModel, utc_now
from .enums import RiskLevel

__all__ = ["RiskDecision", "AccountSnapshot", "RiskCheckResult"]


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


# ─────────────────────────────────────────────────────────────────
# Pydantic v2 모델 — 체크리스트 #8 (스펙 RiskCheckResult)
# ─────────────────────────────────────────────────────────────────

class RiskCheckResult(ConfiguredBaseModel):
    """리스크 게이트 판단 결과.

    안전 invariant (강제):
      `risk_level == BLOCKED`  ⇒  `allowed` 는 반드시 False.
    위반 시 ValidationError. 이는 "blocked 인데 허용됨" 같은 모순 상태가 어떤
    경로로도 생성되지 못하게 막는 단일 진리 가드이다.
    """

    allowed:    bool      = Field(..., description="주문/액션 허용 여부")
    risk_level: RiskLevel = Field(..., description="ok / warning / blocked")
    reason:     str       = Field(default="", description="결정 사유")
    reasons:    List[str] = Field(default_factory=list,
                                  description="복수 사유 목록 (감사로그용)")
    ts: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _enforce_blocked_means_not_allowed(self) -> "RiskCheckResult":
        if self.risk_level == RiskLevel.BLOCKED and self.allowed:
            raise ValueError(
                "risk_level=blocked requires allowed=False (cannot allow a blocked check)"
            )
        return self
