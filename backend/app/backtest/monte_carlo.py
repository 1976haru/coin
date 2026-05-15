"""Monte Carlo Bootstrap — 체크리스트 #63.

거래 결과의 bootstrap 재추출(with replacement)로 percentile 분포 추정.
거래 1건의 운에 의존한 결과인지 통계적 신뢰 구간으로 확인.
"""
from __future__ import annotations
import random
from dataclasses import dataclass, field, asdict
from typing import Sequence

from app.agents.loss_tagging import TradeOutcome


@dataclass(frozen=True)
class MonteCarloResult:
    """B 회 부트스트랩 후 percentile 분포."""

    iterations: int
    n_trades_per_iter: int
    win_rate_p05: float
    win_rate_p50: float
    win_rate_p95: float
    total_pnl_p05: float
    total_pnl_p50: float
    total_pnl_p95: float
    max_drawdown_p05: float          # drawdown 분포에서 p05 = 작은 값 = 좋은 시뮬
    max_drawdown_p50: float
    max_drawdown_p95: float           # p95 = 큰 drawdown = 나쁜 시뮬

    def to_dict(self) -> dict:
        return asdict(self)


class MonteCarloRunner:
    """거래 outcomes 로부터 percentile 분포 추정."""

    def __init__(
        self,
        *,
        iterations: int = 1000,
        seed: int | None = 42,
    ):
        if iterations < 10:
            raise ValueError("iterations >= 10 권장")
        self.iterations = int(iterations)
        self._seed = seed

    def run(
        self,
        outcomes: Sequence[TradeOutcome],
    ) -> MonteCarloResult:
        rng = random.Random(self._seed)
        outs = list(outcomes)
        if not outs:
            return MonteCarloResult(
                iterations=self.iterations, n_trades_per_iter=0,
                win_rate_p05=0.0, win_rate_p50=0.0, win_rate_p95=0.0,
                total_pnl_p05=0.0, total_pnl_p50=0.0, total_pnl_p95=0.0,
                max_drawdown_p05=0.0, max_drawdown_p50=0.0, max_drawdown_p95=0.0,
            )

        n = len(outs)
        win_rates: list[float] = []
        total_pnls: list[float] = []
        max_dds: list[float] = []

        for _ in range(self.iterations):
            sample = [outs[rng.randrange(n)] for _ in range(n)]
            wins = sum(1 for t in sample if t.pnl_pct > 0)
            win_rates.append(wins / n)

            pnls = [t.pnl_pct for t in sample]
            total_pnls.append(sum(pnls))
            max_dds.append(self._max_drawdown(pnls))

        return MonteCarloResult(
            iterations=self.iterations,
            n_trades_per_iter=n,
            win_rate_p05=self._percentile(win_rates, 5),
            win_rate_p50=self._percentile(win_rates, 50),
            win_rate_p95=self._percentile(win_rates, 95),
            total_pnl_p05=self._percentile(total_pnls, 5),
            total_pnl_p50=self._percentile(total_pnls, 50),
            total_pnl_p95=self._percentile(total_pnls, 95),
            max_drawdown_p05=self._percentile(max_dds, 5),
            max_drawdown_p50=self._percentile(max_dds, 50),
            max_drawdown_p95=self._percentile(max_dds, 95),
        )

    # ── 내부 ──────────────────────────────────────────────────────

    @staticmethod
    def _percentile(values: list[float], pct: float) -> float:
        if not values:
            return 0.0
        sv = sorted(values)
        k = (len(sv) - 1) * pct / 100.0
        lo, hi = int(k), min(int(k) + 1, len(sv) - 1)
        if lo == hi:
            return round(sv[lo], 6)
        frac = k - lo
        return round(sv[lo] + (sv[hi] - sv[lo]) * frac, 6)

    @staticmethod
    def _max_drawdown(pnls: list[float]) -> float:
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cum += p
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
        return max_dd
