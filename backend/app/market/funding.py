"""Funding rate 계산 — 체크리스트 #36 Funding Cost Guard.

Perpetual futures 펀딩비 (보통 8시간 주기) 의 비용 기여도, 방향, 이상치, 연환산
계산을 한 곳에 모은다.

설계 원칙:
  - 모든 함수 순수 — 외부 I/O 없음.
  - rate_pct 단위는 % (예: 0.01 = 0.01%, 1.0 = 1%).
  - 8h 기본 주기 — 거래소별 다를 수 있으므로 ``interval_hours`` 인자로 노출.
  - 역김프 short 포지션 관점에서 부호 해석:
      양의 funding ⇒ long → short 지급 (short 가 받음, 비용↓)
      음의 funding ⇒ short → long 지급 (short 가 냄, 비용↑)
"""
from __future__ import annotations
from typing import Literal


PositionSide = Literal["long", "short"]

# 거래소별 펀딩 주기 — 보통 8h. 일부 (Bybit) 는 8h 고정, 일부 (FTX 과거) 1h.
DEFAULT_FUNDING_INTERVAL_HOURS = 8.0

# Funding rate 가 ±1% / interval 초과 시 비정상으로 간주 (보수적).
DEFAULT_EXTREME_THRESHOLD_PCT = 1.0


# ── 단일 funding 청구 비용 ──────────────────────────────────────

def funding_cost_contribution_pct(
    rate_pct: float,
    *,
    side: PositionSide = "short",
) -> float:
    """포지션 1단위가 1회 funding 시점에 지불/수취하는 비용 %.

    양수: 비용 (지불). 음수: 수익 (수취).
    """
    side = side.lower()  # type: ignore[assignment]
    if side == "short":
        # short 는 funding 부호의 반대를 받는다
        return -rate_pct
    if side == "long":
        return rate_pct
    raise ValueError(f"side must be 'long' or 'short' (got {side})")


def conservative_funding_cost_pct(rate_pct: float) -> float:
    """방향 무관하게 ``abs(rate)`` 를 비용으로 취급 — 보수적 견적.

    KimpStrategy 의 기존 cost 계산 호환. 수익 가능성을 비용으로 깎아낸다.
    """
    return abs(rate_pct)


# ── 보유 시간에 따른 누적 비용 ──────────────────────────────────

def projected_funding_payments(
    hours_held: float,
    *,
    interval_hours: float = DEFAULT_FUNDING_INTERVAL_HOURS,
) -> float:
    """보유 시간 동안 발생할 funding 이벤트 수 (분수 가능).

    보유시간 < interval_hours 면 0건 (그래도 0.something 반환 → 보수적
    예상치에는 ceil 사용 권장).
    """
    if hours_held <= 0:
        return 0.0
    if interval_hours <= 0:
        raise ValueError(f"interval_hours must be > 0 (got {interval_hours})")
    return hours_held / interval_hours


def projected_funding_cost_pct(
    rate_pct: float,
    hours_held: float,
    *,
    side: PositionSide = "short",
    interval_hours: float = DEFAULT_FUNDING_INTERVAL_HOURS,
    conservative: bool = True,
) -> float:
    """보유 기간 누적 funding 비용 %.

    conservative=True (기본): ``abs(rate) * payments`` — KimpStrategy 호환.
    conservative=False: side 부호 반영 (수취도 음수 비용으로).
    """
    n = projected_funding_payments(hours_held, interval_hours=interval_hours)
    if conservative:
        return conservative_funding_cost_pct(rate_pct) * n
    return funding_cost_contribution_pct(rate_pct, side=side) * n


# ── 연환산 ──────────────────────────────────────────────────────

def annualized_funding_rate_pct(
    rate_pct: float,
    *,
    interval_hours: float = DEFAULT_FUNDING_INTERVAL_HOURS,
) -> float:
    """단일 funding 주기 rate → APR (연환산).

    예: 0.01% / 8h → 0.01 × (24/8 × 365) = 10.95% APR
    """
    if interval_hours <= 0:
        raise ValueError(f"interval_hours must be > 0 (got {interval_hours})")
    periods_per_year = 24.0 * 365.0 / interval_hours
    return rate_pct * periods_per_year


# ── 이상치 / 방향 평가 ───────────────────────────────────────────

def is_extreme_funding(
    rate_pct: float,
    *,
    threshold_pct: float = DEFAULT_EXTREME_THRESHOLD_PCT,
) -> bool:
    """``|rate|`` 이 한계 초과 — 비정상 시장/거래소 장애 의심."""
    return abs(rate_pct) > threshold_pct


def is_funding_unfavorable(
    rate_pct: float,
    *,
    side: PositionSide = "short",
) -> bool:
    """포지션 방향에 funding 이 불리한 상황인지.

    - short + 음수 funding (short 가 냄) → True
    - long  + 양수 funding (long 이 냄)  → True
    """
    contribution = funding_cost_contribution_pct(rate_pct, side=side)
    return contribution > 0
