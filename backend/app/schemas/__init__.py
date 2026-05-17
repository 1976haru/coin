"""공유 스키마 패키지 — 체크리스트 #8 Shared Schemas.

모듈 간 데이터 교환 형식의 단일 진입점.

두 계층을 함께 제공한다:
  1. (legacy) frozen dataclass 기반 타입 — 본 패키지의 직접 export.
     기존 1300+ 회귀 테스트와 OrderGateway/RiskManager 등의 콜러가 의존.
  2. (new) Pydantic v2 모델 — `app.schemas.models` 서브모듈 경유.
     FastAPI 요청/응답 컨트랙트 + 강한 validation.

사용 (legacy):
    from app.schemas import (
        Ticker, OHLCV, KimpSnapshot, OrderBook,            # 시세
        SignalBase, StrategySignal, KimpSignal, PairSignal, # 신호 (dataclass)
        OrderRequest, OrderResult,                          # 주문 (dataclass)
        Position,                                           # 포지션 (dataclass)
        AccountSnapshot, RiskDecision,                      # 리스크
        AgentDecision,                                      # 에이전트 (dataclass)
    )

사용 (new Pydantic):
    from app.schemas.models import (
        TradingMode, MarketType, OrderSide, OrderType, OrderStatus,
        PositionSide, RiskLevel, AgentAction,
        TradingSignal, OrderRequest, PositionSnapshot, FillEvent,
        RiskCheckResult, AgentDecision,
    )

신호/판단 객체의 is_order_intent 기본값은 False (CLAUDE.md §2.3).
"""
from .market import Ticker, OHLCV, KimpSnapshot, OrderBook
from .signal import SignalBase, Action, Side, TradingSignal
from .order import (
    OrderRequest, OrderResult, OrderType, OrderStatus, OrderRoute,
    OrderRequestModel,
)
from .position import Position, PositionSide, PositionStatus, PositionSnapshot
from .risk import RiskDecision, AccountSnapshot, RiskCheckResult
from .agent import AgentDecision, AgentDecisionModel
from .fill import FillEvent

# 전략 신호 — 정규 위치에서 재export (단일 진입점)
from app.strategies.strategies import StrategySignal, PairSignal
from app.strategies.kimp_mean_reversion import KimpSignal

# 신규 Enum 들은 이름 충돌(OrderType / OrderStatus / PositionSide 가 legacy Literal
# 별칭으로 이미 export 됨) 을 피하기 위해 본 __init__ 에서는 노출하지 않는다.
# Enum 이 필요한 코드는 `from app.schemas.enums import ...` 또는 `app.schemas.models`
# 를 사용한다.

__all__ = [
    # ── legacy (dataclass) ──────────────────────────────────────
    "Ticker", "OHLCV", "KimpSnapshot", "OrderBook",
    "SignalBase", "Action", "Side",
    "StrategySignal", "PairSignal", "KimpSignal",
    "OrderRequest", "OrderResult", "OrderType", "OrderStatus", "OrderRoute",
    "Position", "PositionSide", "PositionStatus",
    "RiskDecision", "AccountSnapshot",
    "AgentDecision",
    # ── new (Pydantic v2) — spec 이름 충돌을 피한 *Model / 신규 클래스 ─
    "TradingSignal",
    "OrderRequestModel",
    "PositionSnapshot",
    "FillEvent",
    "RiskCheckResult",
    "AgentDecisionModel",
]
