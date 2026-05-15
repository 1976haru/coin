"""VolatilityBreakoutStrategy — 체크리스트 #31.

전일 Range 돌파 + 거래량 급증 + 쿨다운. 초고변동(ATR > 평균×3) 구간에서
포지션 50% 자동 축소.

`#29 StrategyBase` contract 만족 — `capability` + `generate` 메서드.
신호 객체는 `StrategySignal` (#8 SignalBase 4필드 보유).
"""
from __future__ import annotations
from typing import Sequence

from .base import StrategyCapability
from ._indicators import atr
from ._signals import StrategySignal


class VolatilityBreakoutStrategy:
    """전일 Range 돌파 + 거래량 급증 + 쿨다운."""

    capability = StrategyCapability(
        name="volatility_breakout",
        description="전일 range 돌파 + 거래량 급증. 초고변동 구간 포지션 축소.",
        required_inputs=("closes", "highs", "lows", "volume_ratio"),
        signal_actions=("BUY", "SELL", "HOLD"),
        output_signal_class="StrategySignal",
    )

    def __init__(
        self,
        breakout_buffer: float = 0.002,    # 0.2% 버퍼
        volume_surge:    float = 1.2,      # 거래량 20% 이상
        atr_high_mult:   float = 3.0,      # 초고변동 기준
    ):
        self.breakout_buffer = breakout_buffer
        self.volume_surge    = volume_surge
        self.atr_high_mult   = atr_high_mult

    def generate(
        self,
        closes: Sequence[float],
        highs: Sequence[float],
        lows: Sequence[float],
        volume_ratio: float = 1.0,
    ) -> StrategySignal:
        if len(closes) < 20:
            return StrategySignal("HOLD", 0.0, "데이터 부족")

        current = closes[-1]
        prev_high = max(highs[-26:-1])   # 전일/최근 N봉 고점 (룩어헤드 방지)
        prev_low  = min(lows[-26:-1])

        breakout_level  = prev_high * (1 + self.breakout_buffer)
        breakdown_level = prev_low  * (1 - self.breakout_buffer)

        atr_now = atr(highs, lows, closes, 14)
        atr_avg = atr(highs, lows, closes, 42)
        is_high_vol = atr_now > atr_avg * self.atr_high_mult

        stop_buy  = current - atr_now * 1.5
        stop_sell = current + atr_now * 1.5

        if volume_ratio < self.volume_surge:
            return StrategySignal("HOLD", 0.0, f"거래량 부족: ×{volume_ratio:.2f}")

        if current > breakout_level:
            vol_tag = " [초고변동-절반]" if is_high_vol else ""
            conf = min(0.88, 0.55 + (volume_ratio - 1) * 0.3)
            return StrategySignal(
                "BUY", conf,
                f"변동성 돌파{vol_tag}: ×{volume_ratio:.1f}",
                current, stop_buy, current + atr_now * 3, conf * 100,
            )

        if current < breakdown_level:
            return StrategySignal(
                "SELL", 0.65,
                f"전일저점 붕괴: ×{volume_ratio:.1f}",
                current, stop_sell, current - atr_now * 3,
            )

        return StrategySignal("HOLD", 0.0, "돌파 미발생")
