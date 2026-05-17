"""포지션 스키마 — 체크리스트 #8 Shared Schemas.

전략별 내부 포지션 표현(예: KimpPosition)은 별도로 두되, 모듈 간 공유되는
포지션 스냅샷 형식은 이 타입을 따른다. PnL 계산은 외부에서 mark price로
채워 넣는다.

두 계층:
  1. (legacy) `Position` — frozen dataclass + Literal 별칭 PositionSide.
  2. (new) `PositionSnapshot` — Pydantic v2 BaseModel + Enum 기반.
     side==flat 이면 quantity==0 강제 (validation).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .common import ConfiguredBaseModel, utc_now
from .enums import PositionSide as PositionSideEnum


PositionSide = Literal["LONG", "SHORT", "FLAT"]
PositionStatus = Literal["OPEN", "CLOSING", "CLOSED"]


@dataclass(frozen=True)
class Position:
    """단일 포지션의 정규 형식."""

    symbol: str
    side: PositionSide
    entry_price: float
    qty: float
    notional_usdt: float
    leverage: float = 1.0
    status: PositionStatus = "OPEN"
    entry_ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    unrealized_pnl_pct: float = 0.0
    realized_pnl_pct: float = 0.0
    strategy: str = ""
    note: str = ""


# ─────────────────────────────────────────────────────────────────
# Pydantic v2 모델 — 체크리스트 #8 (스펙 PositionSnapshot)
# ─────────────────────────────────────────────────────────────────

class PositionSnapshot(ConfiguredBaseModel):
    """포지션 스냅샷 — API 응답 / 모듈 간 공유용.

    Validation:
      - quantity >= 0
      - avg_entry_price >= 0
      - side == flat 이면 quantity 는 정확히 0 이어야 한다
    """

    symbol:           str               = Field(..., min_length=1)
    side:             PositionSideEnum  = Field(..., description="long / short / flat")
    quantity:         Decimal           = Field(default=Decimal("0"), ge=0,
                                                description="보유 수량 (>=0)")
    avg_entry_price:  Decimal           = Field(default=Decimal("0"), ge=0,
                                                description="평균 진입가 (>=0)")
    unrealized_pnl:   Decimal           = Field(default=Decimal("0"),
                                                description="미실현 손익 (부호 허용)")
    realized_pnl:     Decimal           = Field(default=Decimal("0"),
                                                description="실현 손익 (부호 허용)")
    leverage:         Decimal           = Field(default=Decimal("1"), ge=0)
    ts: datetime = Field(default_factory=utc_now, description="스냅샷 시각 (UTC)")

    @model_validator(mode="after")
    def _validate_flat_has_zero_quantity(self) -> "PositionSnapshot":
        if self.side == PositionSideEnum.FLAT and self.quantity != 0:
            raise ValueError(
                f"side=flat requires quantity=0, got {self.quantity}"
            )
        return self
