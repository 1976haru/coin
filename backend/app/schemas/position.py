"""포지션 스키마 — 체크리스트 #8 Shared Schemas.

전략별 내부 포지션 표현(예: KimpPosition)은 별도로 두되, 모듈 간 공유되는
포지션 스냅샷 형식은 이 타입을 따른다. PnL 계산은 외부에서 mark price로
채워 넣는다.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


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
