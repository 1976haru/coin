"""
strategies.py — 분리 후 backward compat 재export 허브.

체크리스트 #29: 각 전략 클래스에 ``capability: StrategyCapability`` 선언.
체크리스트 #30 완료: TrendFollowingStrategy   → ``trend_following.py``
체크리스트 #31 완료: VolatilityBreakoutStrategy → ``volatility_breakout.py``
체크리스트 #32 완료: PairTradingStrategy + PairSignal → ``pair_trading.py`` / ``_signals.py``

기존 ``from app.strategies.strategies import ...`` 호출이 깨지지 않도록
본 파일은 지금부터 단순 재export 허브 역할만 한다.
"""
from app.strategies._signals import StrategySignal, PairSignal
from app.strategies._indicators import ema as _ema, sma as _sma, atr as _atr
from app.strategies.trend_following import TrendFollowingStrategy
from app.strategies.volatility_breakout import VolatilityBreakoutStrategy
from app.strategies.pair_trading import PairTradingStrategy

__all__ = [
    "StrategySignal", "PairSignal",
    "TrendFollowingStrategy", "VolatilityBreakoutStrategy", "PairTradingStrategy",
    "_ema", "_sma", "_atr",
]
