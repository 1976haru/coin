"""Walk-Forward Validation — 체크리스트 #62.

긴 시계열을 N 개 폴드로 나누어 각 폴드별 백테스트 실행. 데이터 스누핑(look-ahead)
방지를 위한 기본 도구.

폴딩 모드:
  - "expanding" — 각 폴드 시작은 0, 길이만 증가 (앞으로 가며 누적)
  - "rolling"   — 각 폴드는 고정 길이 윈도우가 한 칸씩 진행
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal, Sequence

from .engine import BacktestBar, BacktestRunner, BacktestResult, StrategyFn
from .metrics import BacktestMetrics, compute_metrics


FoldMode = Literal["expanding", "rolling"]


@dataclass(frozen=True)
class WalkForwardFold:
    index: int
    start: int            # bars 인덱스 (inclusive)
    end: int              # bars 인덱스 (exclusive)
    metrics: BacktestMetrics


@dataclass(frozen=True)
class WalkForwardResult:
    folds: tuple[WalkForwardFold, ...] = field(default_factory=tuple)

    @property
    def avg_return_pct(self) -> float:
        if not self.folds:
            return 0.0
        return sum(f.metrics.return_pct for f in self.folds) / len(self.folds)

    @property
    def avg_max_drawdown_pct(self) -> float:
        if not self.folds:
            return 0.0
        return sum(f.metrics.equity_max_drawdown_pct for f in self.folds) / len(self.folds)

    @property
    def avg_win_rate(self) -> float:
        if not self.folds:
            return 0.0
        return sum(f.metrics.perf.win_rate for f in self.folds) / len(self.folds)

    def to_dict(self) -> dict:
        return {
            "n_folds": len(self.folds),
            "avg_return_pct": round(self.avg_return_pct, 4),
            "avg_max_drawdown_pct": round(self.avg_max_drawdown_pct, 4),
            "avg_win_rate": round(self.avg_win_rate, 4),
            "folds": [
                {
                    "index": f.index,
                    "start": f.start, "end": f.end,
                    "metrics": f.metrics.to_dict(),
                }
                for f in self.folds
            ],
        }


class WalkForwardRunner:
    """N-fold walk-forward 실행기."""

    def __init__(
        self,
        runner: BacktestRunner | None = None,
        *,
        n_folds: int = 5,
        mode: FoldMode = "expanding",
        min_fold_bars: int = 50,
    ):
        if n_folds < 1:
            raise ValueError("n_folds must be >= 1")
        if mode not in ("expanding", "rolling"):
            raise ValueError(f"unknown mode: {mode}")
        self.runner = runner or BacktestRunner()
        self.n_folds = int(n_folds)
        self.mode = mode
        self.min_fold_bars = int(min_fold_bars)

    def run(
        self,
        strategy_fn: StrategyFn,
        bars: Sequence[BacktestBar],
        *,
        symbol: str = "TEST",
        strategy_name: str = "",
    ) -> WalkForwardResult:
        bars = list(bars)
        n = len(bars)
        if n < self.min_fold_bars * self.n_folds and self.mode == "rolling":
            # 너무 짧으면 expanding 으로 강등
            self.mode = "expanding"

        ranges = self._compute_ranges(n)
        folds: list[WalkForwardFold] = []
        for i, (start, end) in enumerate(ranges):
            slice_bars = bars[start:end]
            if len(slice_bars) < self.min_fold_bars:
                continue
            r = self.runner.run(strategy_fn, slice_bars,
                                  symbol=symbol, strategy_name=strategy_name)
            m = compute_metrics(r)
            folds.append(WalkForwardFold(index=i, start=start, end=end, metrics=m))

        return WalkForwardResult(folds=tuple(folds))

    # ── 내부 ──────────────────────────────────────────────────────

    def _compute_ranges(self, n: int) -> list[tuple[int, int]]:
        if self.n_folds <= 0 or n <= 0:
            return []
        fold_size = n // self.n_folds
        ranges: list[tuple[int, int]] = []
        if self.mode == "expanding":
            for i in range(self.n_folds):
                end = (i + 1) * fold_size if i < self.n_folds - 1 else n
                ranges.append((0, end))
        else:  # rolling
            for i in range(self.n_folds):
                start = i * fold_size
                end = start + fold_size if i < self.n_folds - 1 else n
                ranges.append((start, end))
        return ranges
