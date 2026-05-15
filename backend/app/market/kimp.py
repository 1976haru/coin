"""김프/역김프 공식 — 체크리스트 #34 Kimp Formula.

본 모듈이 김프율 계산의 **단일 진리 소스**다. 다른 모듈은 본 함수만 호출한다:
  - ``app.schemas.market.KimpSnapshot.compute_kimp`` → 본 모듈로 위임
  - ``app.strategies.kimp_mean_reversion.KimpMeanReversionStrategy.calculate_kimp`` → 본 모듈로 위임

공식:
    kimp_pct = (upbit_krw / (okx_usdt × fx) - 1) × 100

  - 양수: 한국이 비싸다 (정김프)
  - 음수: 한국이 싸다 (역김프)

부가 계산:
  - ``breakeven_threshold_pct``  : 진입 손익분기 한계 비용 (왕복 가정)
  - ``expected_edge_pct``         : 현재 kimp 와 청산 임계값의 거리
  - ``is_anomaly``                : 김프율이 이상 범위인지 (FX 오류/장애)
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class KimpResult:
    """검증된 김프 계산 결과."""

    kimp_pct: float           # 양수=정김프, 음수=역김프
    upbit_krw: float
    okx_usdt: float
    fx: float                 # USDT/KRW
    valid: bool
    reason: str


# ── 핵심 공식 ────────────────────────────────────────────────────

def compute_kimp_pct(
    upbit_krw: float,
    okx_usdt: float,
    fx: float,
    *,
    strict: bool = False,
) -> float:
    """김프율 % 계산.

    Parameters
    ----------
    upbit_krw:
        Upbit 의 KRW 가격.
    okx_usdt:
        OKX 의 USDT 가격.
    fx:
        USDT/KRW 환율.
    strict:
        False (default) — 비정상 입력 시 0.0 반환 (silent).
        True — 비정상 입력 시 ``ValueError``.
    """
    if upbit_krw <= 0 or okx_usdt <= 0 or fx <= 0:
        if strict:
            raise ValueError(
                f"모든 가격과 환율은 양수여야 합니다 "
                f"(upbit_krw={upbit_krw}, okx_usdt={okx_usdt}, fx={fx})"
            )
        return 0.0
    return (upbit_krw / (okx_usdt * fx) - 1.0) * 100.0


def assess_kimp(
    upbit_krw: float,
    okx_usdt: float,
    fx: float,
) -> KimpResult:
    """검증 + 결과 패키징. 비정상 입력은 valid=False 로 표기 (raise 없음)."""
    if upbit_krw <= 0 or okx_usdt <= 0 or fx <= 0:
        return KimpResult(
            kimp_pct=0.0,
            upbit_krw=upbit_krw, okx_usdt=okx_usdt, fx=fx,
            valid=False,
            reason=(f"입력 비정상: upbit_krw={upbit_krw}, "
                    f"okx_usdt={okx_usdt}, fx={fx}"),
        )
    pct = (upbit_krw / (okx_usdt * fx) - 1.0) * 100.0
    return KimpResult(
        kimp_pct=pct,
        upbit_krw=upbit_krw, okx_usdt=okx_usdt, fx=fx,
        valid=True,
        reason=f"정상 계산: {pct:.4f}%",
    )


# ── 부가 계산: 비용/엣지/이상치 ─────────────────────────────────

def breakeven_threshold_pct(
    *,
    upbit_spread_pct: float = 0.05,
    okx_spread_pct: float = 0.05,
    upbit_fee_pct: float = 0.05,
    okx_fee_pct: float = 0.05,
    funding_pct: float = 0.0,
    slippage_pct: float = 0.0,
) -> float:
    """역김프 평균회귀 진입의 손익분기 한계 비용 % (왕복 가정).

    실제 진입 결정은 ``expected_edge_pct >= breakeven`` 일 때만 가능.
    음수 펀딩비도 비용으로 취급하기 위해 ``abs(funding_pct)`` 사용.
    """
    return (
        upbit_spread_pct + okx_spread_pct
        + upbit_fee_pct + okx_fee_pct
        + abs(funding_pct)
        + slippage_pct
    )


def expected_edge_pct(kimp_pct: float, exit_threshold_pct: float) -> float:
    """현재 김프율과 청산 임계값의 절대 거리.

    역김프 평균회귀 시 ``|kimp - exit_threshold|`` 만큼이 기대 수익 여지.
    """
    return abs(kimp_pct - exit_threshold_pct)


def is_anomaly(
    kimp_pct: float,
    *,
    abnormal_min: float = -10.0,
    abnormal_max: float = +10.0,
) -> bool:
    """김프율이 이상 범위인지 — FX 오류 / 거래소 장애 / 사이드런어웨이 감지.

    기본 ±10% 범위 밖이면 True.
    """
    return kimp_pct < abnormal_min or kimp_pct > abnormal_max
