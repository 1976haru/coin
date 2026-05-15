"""Backtest Metrics — 체크리스트 #61.

PerformanceAgent (#45) 의 분석을 백테스트 결과에 적용하는 thin wrapper.
백테스트 특화 지표 (equity curve drawdown, sharpe-like) 추가.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field, asdict

from app.agents.performance import PerformanceAgent, PerformanceMetrics
from .engine import BacktestResult


@dataclass(frozen=True)
class BacktestMetrics:
    """백테스트 통합 지표 — PerformanceMetrics + 백테스트 특화."""

    perf: PerformanceMetrics
    initial_equity: float
    final_equity: float
    return_pct: float
    equity_max_drawdown_pct: float    # equity curve 기준 (PerformanceMetrics 의 trade-level 과 다름)
    sharpe_like: float                # 봉별 수익률 mean / std × sqrt(n)
    bars_processed: int

    def to_dict(self) -> dict:
        d = asdict(self.perf)
        d["initial_equity"] = self.initial_equity
        d["final_equity"] = self.final_equity
        d["return_pct"] = self.return_pct
        d["equity_max_drawdown_pct"] = self.equity_max_drawdown_pct
        d["sharpe_like"] = self.sharpe_like
        d["bars_processed"] = self.bars_processed
        return d


def compute_metrics(result: BacktestResult) -> BacktestMetrics:
    """BacktestResult → BacktestMetrics."""
    perf = PerformanceAgent().analyze(result.trades)

    initial = result.initial_equity
    final = result.final_equity
    return_pct = ((final - initial) / initial * 100.0) if initial > 0 else 0.0

    eq_dd = _equity_curve_max_drawdown_pct(result.equity_curve, initial)
    sharpe = _sharpe_like(result.equity_curve)

    return BacktestMetrics(
        perf=perf,
        initial_equity=initial,
        final_equity=round(final, 6),
        return_pct=round(return_pct, 4),
        equity_max_drawdown_pct=round(eq_dd, 4),
        sharpe_like=round(sharpe, 4),
        bars_processed=result.bars_processed,
    )


# ── 내부 ─────────────────────────────────────────────────────────

def _equity_curve_max_drawdown_pct(curve: tuple[float, ...], initial: float) -> float:
    if not curve:
        return 0.0
    peak = initial
    max_dd_pct = 0.0
    for eq in curve:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd_pct = (peak - eq) / peak * 100.0
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
    return max_dd_pct


def _sharpe_like(curve: tuple[float, ...]) -> float:
    """봉별 수익률 mean/std × sqrt(n). 일/연 환산 없는 단순 지표."""
    if len(curve) < 2:
        return 0.0
    rets = []
    for i in range(1, len(curve)):
        prev = curve[i - 1]
        curr = curve[i]
        if prev != 0:
            rets.append((curr - prev) / prev)
    if not rets:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return mean / std * math.sqrt(len(rets))
