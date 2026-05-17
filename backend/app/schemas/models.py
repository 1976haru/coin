"""체크리스트 #8 Shared Schemas — 신규 Pydantic v2 모델 단일 진입점.

기존 `app.schemas` 는 dataclass 기반 legacy 타입(OrderRequest/AgentDecision 등)을
이미 export 하고 있고 1300+ 테스트가 이 이름들에 의존한다. 충돌 없이 신규
Pydantic 모델을 노출하기 위해 본 모듈을 둔다.

사용 예:
    from app.schemas.models import (
        TradingMode, MarketType, OrderSide, OrderType, OrderStatus,
        PositionSide, RiskLevel, AgentAction,
        TradingSignal, OrderRequest, PositionSnapshot, FillEvent,
        RiskCheckResult, AgentDecision,
    )

여기서 `OrderRequest` / `AgentDecision` 은 Pydantic 모델이다 (legacy dataclass 는
`app.schemas.OrderRequest` / `app.schemas.AgentDecision` 으로 그대로 import 가능).
"""
from __future__ import annotations

from .enums import (
    AgentAction,
    MarketType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    RiskLevel,
    TradingMode,
)
from .common import ConfiguredBaseModel, utc_now, Money
from .signal import TradingSignal
from .order import OrderRequestModel as OrderRequest
from .position import PositionSnapshot
from .fill import FillEvent
from .risk import RiskCheckResult
from .agent import AgentDecisionModel as AgentDecision

__all__ = [
    # base / utils
    "ConfiguredBaseModel", "utc_now", "Money",
    # enums
    "AgentAction", "MarketType", "OrderSide", "OrderStatus", "OrderType",
    "PositionSide", "RiskLevel", "TradingMode",
    # pydantic v2 models (spec names)
    "TradingSignal", "OrderRequest", "PositionSnapshot", "FillEvent",
    "RiskCheckResult", "AgentDecision",
]
