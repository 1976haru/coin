"""기술적 지표 헬퍼 — 체크리스트 #30·#31 분리 시 공유.

전략 모듈(`trend_following`/`volatility_breakout` 등)에서 공통으로 사용하는
순수 함수. 외부 의존성 없음, 결과는 단일 float.
"""
from __future__ import annotations
from typing import Sequence


def ema(prices: Sequence[float], period: int) -> float:
    """단순 EMA — 마지막 값 반환."""
    if not prices or period <= 0:
        return 0.0
    alpha = 2.0 / (period + 1)
    result = prices[0]
    for p in prices[1:]:
        result = alpha * p + (1 - alpha) * result
    return result


def sma(prices: Sequence[float], period: int) -> float:
    """SMA — 표본 부족 시 가용 표본 평균."""
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0.0
    return sum(prices[-period:]) / period


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float:
    """Average True Range — period 봉 평균."""
    if len(closes) < 2:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / min(period, len(trs)) if trs else 0.0


# 호환 alias (기존 strategies.py 가 _ema/_sma/_atr 로 참조)
_ema = ema
_sma = sma
_atr = atr
