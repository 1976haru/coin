"""Strategy 패키지 — 체크리스트 #29.

공개 API:
  - StrategyBase, StrategyCapability, StrategyRegistry  (#29 base contract)
  - assert_signal_contract                              (회귀 헬퍼)
  - collect_default_strategies                          (기본 4개 등록)
  - StrategySignal, PairSignal, KimpSignal              (신호 타입)
  - 4개 전략 클래스                                       (TrendFollowing/VolatilityBreakout/PairTrading/KimpMeanReversion)
"""
from .base import (
    StrategyBase, StrategyCapability, StrategyRegistry,
    assert_signal_contract, collect_default_strategies,
)
from .strategies import (
    StrategySignal, PairSignal,
    TrendFollowingStrategy, VolatilityBreakoutStrategy, PairTradingStrategy,
)
from .kimp_mean_reversion import KimpSignal, KimpMeanReversionStrategy

__all__ = [
    "StrategyBase", "StrategyCapability", "StrategyRegistry",
    "assert_signal_contract", "collect_default_strategies",
    "StrategySignal", "PairSignal", "KimpSignal",
    "TrendFollowingStrategy", "VolatilityBreakoutStrategy", "PairTradingStrategy",
    "KimpMeanReversionStrategy",
]
