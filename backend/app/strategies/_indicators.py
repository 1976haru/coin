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


# ── 체크리스트 #30 — Donchian / ADX / true_range ────────────────


def true_range(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> list[float]:
    """봉별 True Range 시퀀스.

    ``tr_i = max(high_i - low_i, |high_i - close_{i-1}|, |low_i - close_{i-1}|)``.
    길이는 ``len(closes) - 1``. 입력 부족/길이 불일치 시 빈 리스트.
    """
    n = len(closes)
    if n < 2:
        return []
    if len(highs) != n or len(lows) != n:
        return []
    trs: list[float] = []
    for i in range(1, n):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    return trs


def donchian_channel(
    highs: Sequence[float],
    lows: Sequence[float],
    period: int = 20,
    *,
    exclude_current: bool = True,
) -> tuple[float, float]:
    """Donchian channel (high, low) — 최근 ``period`` 봉.

    ``exclude_current=True`` (기본) 이면 현재 봉을 제외한 직전 ``period`` 봉의
    max(high) / min(low) 를 반환 — *돌파* 신호 판정에 사용. False 면 현재 봉 포함.
    """
    if not highs or not lows:
        return 0.0, 0.0
    if len(highs) != len(lows):
        return 0.0, 0.0
    if period <= 0:
        return 0.0, 0.0
    if exclude_current:
        end = len(highs) - 1
    else:
        end = len(highs)
    if end <= 0:
        return 0.0, 0.0
    start = max(0, end - period)
    window_highs = highs[start:end]
    window_lows = lows[start:end]
    if not window_highs or not window_lows:
        return 0.0, 0.0
    return max(window_highs), min(window_lows)


def _wilder_smooth(values: Sequence[float], period: int) -> float:
    """Wilder's smoothing — 마지막 값 반환. Wilder 알고리즘:

        seed = sum(values[0..period-1])
        for v in values[period..]: seed = seed - seed/period + v
    """
    if len(values) < period or period <= 0:
        if not values:
            return 0.0
        return sum(values) / len(values)
    seed = sum(values[:period])
    for v in values[period:]:
        seed = seed - (seed / period) + v
    return seed


def adx(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float:
    """ADX (Average Directional Index) — Wilder 정의.

    알고리즘 (문서화 — 트레이딩뷰와 완전 동일하지 않을 수 있지만 결정론적):
      1. TR_i = true_range[i]
      2. +DM_i = max(0, high_i - high_{i-1}) 가 max(0, low_{i-1} - low_i) 보다 클 때만,
         그렇지 않으면 0. -DM_i 대칭.
      3. Wilder 평활 — TR, +DM, -DM 각각 ``period`` 길이로.
      4. +DI = 100 * (+DM_smooth / TR_smooth)
         -DI = 100 * (-DM_smooth / TR_smooth)
      5. DX = 100 * |+DI - -DI| / (+DI + -DI)
      6. ADX = Wilder 평활된 DX (간단화: 마지막 DX 단일 값 — full Wilder 의 second
         pass 는 데이터 더 많이 필요하므로 본 단계 구현에서는 단순화).

    데이터 부족 (n < period + 1) 시 0.0 반환.
    """
    n = len(closes)
    if n < period + 1:
        return 0.0
    if len(highs) != n or len(lows) != n:
        return 0.0
    trs: list[float] = []
    plus_dms: list[float] = []
    minus_dms: list[float] = []
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    if len(trs) < period:
        return 0.0
    tr_smooth = _wilder_smooth(trs, period)
    plus_smooth = _wilder_smooth(plus_dms, period)
    minus_smooth = _wilder_smooth(minus_dms, period)
    if tr_smooth <= 0:
        return 0.0
    plus_di = 100.0 * (plus_smooth / tr_smooth)
    minus_di = 100.0 * (minus_smooth / tr_smooth)
    di_sum = plus_di + minus_di
    if di_sum <= 0:
        return 0.0
    dx = 100.0 * abs(plus_di - minus_di) / di_sum
    return dx
