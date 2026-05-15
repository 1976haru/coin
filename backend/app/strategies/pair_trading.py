"""PairTradingStrategy — 체크리스트 #32.

두 심볼(A, B) 간 z-score 기반 평균회귀. 기본 BTC-ETH.

`#29 StrategyBase` contract 만족 — `capability` + `generate` 메서드.
신호 객체는 `PairSignal` (체크리스트 #8 의 SignalBase 호환).
"""
from __future__ import annotations
import math
from typing import Sequence

from .base import StrategyCapability
from ._signals import PairSignal


class PairTradingStrategy:
    """BTC-ETH (또는 임의 페어) z-score 기반 평균회귀."""

    capability = StrategyCapability(
        name="pair_trading",
        description="두 심볼 z-score 평균회귀 (예: BTC-ETH).",
        required_inputs=("prices_a", "prices_b", "symbol_a", "symbol_b"),
        signal_actions=("OPEN_LONG_A_SHORT_B", "OPEN_SHORT_A_LONG_B",
                        "CLOSE", "HOLD", "BLOCKED"),
        supports_pair=True,
        output_signal_class="PairSignal",
    )

    def __init__(self, entry_z: float = 2.0, exit_z: float = 0.5, window: int = 60):
        self.entry_z = entry_z
        self.exit_z  = exit_z
        self.window  = window

    def generate(
        self,
        prices_a: Sequence[float],
        prices_b: Sequence[float],
        symbol_a: str = "BTC",
        symbol_b: str = "ETH",
    ) -> PairSignal:
        n = min(len(prices_a), len(prices_b), self.window)
        if n < 20:
            return PairSignal("HOLD", symbol_a, symbol_b, 0, 1.0, 0.0, "데이터 부족")

        a = list(prices_a[-n:])
        b = list(prices_b[-n:])

        # OLS hedge ratio
        mean_a = sum(a) / n
        mean_b = sum(b) / n
        cov_ab = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n)) / n
        var_b  = sum((x - mean_b) ** 2 for x in b) / n
        hedge  = cov_ab / var_b if var_b > 0 else 1.0

        # 스프레드 z-score
        spread = [a[i] - hedge * b[i] for i in range(n)]
        mean_s = sum(spread) / n
        std_s  = math.sqrt(sum((s - mean_s) ** 2 for s in spread) / n) or 1e-9
        z      = (spread[-1] - mean_s) / std_s

        if abs(z) < self.exit_z:
            return PairSignal(
                "CLOSE", symbol_a, symbol_b,
                round(z, 3), round(hedge, 4),
                0.8, f"평균 회귀 달성 (z={z:.2f})",
            )

        if z > self.entry_z:
            conf = min(0.88, 0.5 + (z - self.entry_z) * 0.15)
            return PairSignal(
                "OPEN_SHORT_A_LONG_B", symbol_a, symbol_b,
                round(z, 3), round(hedge, 4),
                conf, f"{symbol_a} 과열 Short / {symbol_b} Long (z={z:.2f})",
            )

        if z < -self.entry_z:
            conf = min(0.88, 0.5 + (abs(z) - self.entry_z) * 0.15)
            return PairSignal(
                "OPEN_LONG_A_SHORT_B", symbol_a, symbol_b,
                round(z, 3), round(hedge, 4),
                conf, f"{symbol_a} 침체 Long / {symbol_b} Short (z={z:.2f})",
            )

        return PairSignal(
            "HOLD", symbol_a, symbol_b,
            round(z, 3), round(hedge, 4),
            0.0, f"z-score 중립 ({z:.2f})",
        )
