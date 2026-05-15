"""Backtest 패키지 — 체크리스트 #60-#63.

공개 API:
  - BacktestRunner / BacktestResult       (#60 Backtest Engine)
  - BacktestBar / BacktestSignal           (입력 형식)
  - compute_metrics                         (#61 — PerformanceAgent 위임)
  - WalkForwardRunner                      (#62 Walk-forward)
  - MonteCarloRunner / MonteCarloResult    (#63 Monte Carlo)
"""
from .engine import (
    BacktestBar, BacktestSignal, BacktestResult, BacktestRunner,
)
from .metrics import compute_metrics
from .walk_forward import WalkForwardRunner, WalkForwardResult
from .monte_carlo import MonteCarloRunner, MonteCarloResult

__all__ = [
    "BacktestBar", "BacktestSignal", "BacktestResult", "BacktestRunner",
    "compute_metrics",
    "WalkForwardRunner", "WalkForwardResult",
    "MonteCarloRunner", "MonteCarloResult",
]
