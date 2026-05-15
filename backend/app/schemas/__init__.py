"""공유 스키마 패키지 — 체크리스트 #8 Shared Schemas.

모듈 간 데이터 교환 형식의 단일 진입점.

사용:
    from app.schemas import (
        Ticker, OHLCV, KimpSnapshot, OrderBook,            # 시세
        SignalBase, StrategySignal, KimpSignal, PairSignal, # 신호
        OrderRequest, OrderResult,                          # 주문
        Position,                                            # 포지션
        AccountSnapshot, RiskDecision,                      # 리스크
        AgentDecision,                                       # 에이전트
    )

신호/판단 객체의 is_order_intent 기본값은 False (CLAUDE.md §2.3).
"""
from .market import Ticker, OHLCV, KimpSnapshot, OrderBook
from .signal import SignalBase, Action, Side
from .order import OrderRequest, OrderResult, OrderType, OrderStatus, OrderRoute
from .position import Position, PositionSide, PositionStatus
from .risk import RiskDecision, AccountSnapshot
from .agent import AgentDecision

# 전략 신호 — 정규 위치에서 재export (단일 진입점)
from app.strategies.strategies import StrategySignal, PairSignal
from app.strategies.kimp_mean_reversion import KimpSignal

__all__ = [
    # market
    "Ticker", "OHLCV", "KimpSnapshot", "OrderBook",
    # signal
    "SignalBase", "Action", "Side",
    "StrategySignal", "PairSignal", "KimpSignal",
    # order
    "OrderRequest", "OrderResult", "OrderType", "OrderStatus", "OrderRoute",
    # position
    "Position", "PositionSide", "PositionStatus",
    # risk
    "RiskDecision", "AccountSnapshot",
    # agent
    "AgentDecision",
]
