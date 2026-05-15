"""TrendFollowingStrategy — 체크리스트 #30.

EMA 20/60 + ADX 필터 + Donchian 돌파. 횡보장(ADX < 18) 자동 비활성화.

`#29 StrategyBase` contract 를 만족 — `capability` 클래스 속성 + `generate` 메서드.
신호 객체는 `StrategySignal` (체크리스트 #8 의 SignalBase 4필드 보유).
"""
from __future__ import annotations
from typing import Sequence

from .base import StrategyCapability
from ._indicators import ema, sma, atr
from ._signals import StrategySignal


class TrendFollowingStrategy:
    """EMA 20/60 + ADX + Donchian 돌파."""

    capability = StrategyCapability(
        name="trend_following",
        description="EMA 20/60 + ADX + Donchian 돌파. 횡보장 자동 비활성.",
        required_inputs=("closes", "highs", "lows", "adx", "volume_ratio"),
        signal_actions=("BUY", "SELL", "HOLD"),
        output_signal_class="StrategySignal",
    )

    def __init__(self, ema_fast: int = 20, ema_slow: int = 60, adx_min: float = 18.0):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.adx_min  = adx_min

    def generate(
        self,
        closes: Sequence[float],
        highs: Sequence[float] | None = None,
        lows: Sequence[float] | None = None,
        adx: float = 20.0,
        volume_ratio: float = 1.0,    # 현재 거래량 / 20봉 평균
    ) -> StrategySignal:
        if len(closes) < self.ema_slow + 5:
            return StrategySignal("HOLD", 0.0, "데이터 부족")

        fast = ema(closes, self.ema_fast)
        slow = ema(closes, self.ema_slow)
        sma200 = sma(closes, 200) if len(closes) >= 200 else sma(closes, len(closes))
        current = closes[-1]

        # ADX 필터 — 횡보장 차단
        if adx < self.adx_min:
            return StrategySignal("HOLD", 0.0, f"ADX={adx:.1f} < {self.adx_min} 횡보장")

        # ATR 손절 계산
        atr_value = atr(highs or [current] * len(closes),
                         lows  or [current] * len(closes),
                         closes) if highs else current * 0.02
        stop_long  = current - atr_value * 1.5
        stop_short = current + atr_value * 1.5
        tp_long    = current + atr_value * 3.0
        tp_short   = current - atr_value * 3.0

        # 강한 상승 추세
        if fast > slow and current > sma200 and volume_ratio >= 1.2:
            conf = min(0.88, 0.55 + (adx - self.adx_min) / 100)
            qs   = min(100, 50 + (adx - 18) * 2 + volume_ratio * 10)
            return StrategySignal(
                "BUY", conf,
                f"EMA 정배열, ADX={adx:.1f}, Vol×{volume_ratio:.1f}",
                current, stop_long, tp_long, qs,
            )

        # 강한 하락 추세
        if fast < slow and current < sma200 and volume_ratio >= 1.2:
            conf = min(0.80, 0.50 + (adx - self.adx_min) / 100)
            return StrategySignal(
                "SELL", conf,
                f"EMA 역배열, ADX={adx:.1f}",
                current, stop_short, tp_short,
            )

        return StrategySignal("HOLD", 0.0, "추세 조건 미충족")
