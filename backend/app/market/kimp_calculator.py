"""Kimp Formula Calculator — 체크리스트 #34 Kimp Formula.

표준 김프/역김프 계산 모듈. 전략이 아니라 *계산 모듈* 이다. 본 모듈은:
  - Signal 을 생성하지 않는다.
  - 주문하지 않는다.
  - broker / adapter / OrderGateway / execution 계층을 import 하지 않는다.
  - BUY / SELL / ENTER / EXIT 를 반환하지 않는다 (direction 은 상태 설명일 뿐).

KimpStrategy, KimpAgent, RiskManager 등이 *같은 계산 기준* 을 쓰도록 단일 진리
소스를 제공한다. 기존 float 기반 ``app.market.kimp`` 는 그대로 유지된다 (KimpStrategy
회귀 보호). 본 모듈은 Decimal 기반 표준 계산식과 풍부한 context 산출을 추가한다.

공식 (Decimal):
  foreign_price_krw = foreign_price_quote × fx_rate_krw
  premium_ratio    = (domestic_price_krw - foreign_price_krw) / foreign_price_krw
  premium_percent  = premium_ratio × 100
  premium_bps      = premium_ratio × 10_000

상태 분류:
  direction         : KIMP / REVERSE_KIMP / NEUTRAL
  convergence_state : EXPANDING / CONVERGING / NEUTRAL / UNKNOWN
  fx_anomaly        : sanity 범위 이탈 또는 reference 대비 deviation 초과
  dislocation_kind  : STRUCTURAL / TEMPORARY / MIXED / UNKNOWN
                      (classify_structural_vs_temporary_dislocation)

원칙 (CLAUDE.md §2.3 / §3.1):
  - direct_order_allowed = False 영구.
  - KimpResult 는 Signal 이 아니며, fee_adjusted 결과 또한 거래 가능성을 보장하지 않음.
  - 김프/역김프는 입출금 중단, FX 이상, 수수료, 전송 지연, 규제, 세금, funding
    리스크가 크다. raw premium 만 보고 진입하는 것을 금지한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, getcontext
from typing import Iterable

# 가격 단위가 큼 (BTC ≈ 1.4억 KRW). 누적 곱셈/나눗셈 정밀도 충분히 확보.
getcontext().prec = 38


# ── 상태 라벨 ────────────────────────────────────────────────────


class Direction:
    """premium_bps 의 부호/크기로 분류된 상태 라벨."""

    KIMP = "KIMP"
    REVERSE_KIMP = "REVERSE_KIMP"
    NEUTRAL = "NEUTRAL"


class ConvergenceState:
    """previous_premium_bps 대비 |premium_bps| 변화 방향."""

    EXPANDING = "EXPANDING"
    CONVERGING = "CONVERGING"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class DislocationKind:
    """다중 관측치에 대한 구조적/일시적 괴리 분류."""

    STRUCTURAL = "STRUCTURAL"
    TEMPORARY = "TEMPORARY"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


# ── Config ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class KimpCalculatorConfig:
    """계산 기준 파라미터. 모두 Decimal — 통계/임계값 일관성 유지."""

    # |premium_bps| ≤ neutral_band_bps 이면 direction 을 NEUTRAL 로 본다.
    neutral_band_bps: Decimal = Decimal("5")
    # |delta_bps| > convergence_threshold_bps 일 때 EXPANDING / CONVERGING 분류.
    convergence_threshold_bps: Decimal = Decimal("10")
    # USDT/KRW sanity 범위 — 이 밖이면 fx_anomaly.
    fx_rate_min: Decimal = Decimal("500")
    fx_rate_max: Decimal = Decimal("3000")
    # reference_fx 대비 deviation 한계 (bps).
    fx_anomaly_deviation_bps: Decimal = Decimal("500")
    # STRUCTURAL 판정에 필요한 최소 관측 개수.
    structural_min_count: int = 3
    # STRUCTURAL 판정에 필요한 평균 |premium_bps| 하한.
    structural_min_abs_bps: Decimal = Decimal("80")
    # 영구 False — 본 모듈은 어떤 주문 권한도 부여하지 않는다.
    direct_order_allowed: bool = False


# ── 입력 / 결과 ──────────────────────────────────────────────────


@dataclass(frozen=True)
class KimpInputs:
    """김프 계산에 필요한 *읽기 전용* 입력.

    - ``domestic_price_krw`` : 국내 (Upbit 등) KRW 가격.
    - ``foreign_price_quote``: 해외 거래소 (OKX / Binance 등) USDT 가격.
    - ``fx_rate_krw``        : USDT/KRW (또는 USD/KRW) 환율.
    - ``previous_premium_bps``: 직전 관측치 (있으면 EXPANDING/CONVERGING 분류).
    - ``reference_fx_rate_krw``: 표준 환율 (있으면 deviation_bps 계산).

    숫자형은 Decimal 로 코어 계산. int/float/str 입력은 ``_to_decimal`` 로 안전 변환.
    """

    domestic_price_krw: Decimal
    foreign_price_quote: Decimal
    fx_rate_krw: Decimal
    symbol: str | None = None
    domestic_exchange: str = "upbit"
    foreign_exchange: str = "okx"
    quote_currency: str = "USDT"
    timestamp: datetime | None = None
    previous_premium_bps: Decimal | None = None
    reference_fx_rate_krw: Decimal | None = None


@dataclass(frozen=True)
class KimpResult:
    """김프 계산 결과 — Signal 이 아님. ``direct_order_allowed=False`` 영구."""

    inputs: KimpInputs
    foreign_price_krw: Decimal
    premium_ratio: Decimal
    premium_percent: Decimal
    premium_bps: Decimal
    direction: str
    convergence_state: str
    delta_bps: Decimal | None
    fx_anomaly: bool
    fx_anomaly_reason: str | None
    fx_deviation_bps: Decimal | None
    is_valid: bool
    invalid_reason: str | None
    risk_flags: tuple[str, ...]
    computed_at: datetime
    direct_order_allowed: bool = False  # 영구 False


# ── 내부 헬퍼 ────────────────────────────────────────────────────


def _to_decimal(value: Decimal | int | float | str | None) -> Decimal | None:
    """안전 변환. None → None. float 는 str() 경유로 부정확 비트 패턴 회피."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _classify_direction(premium_bps: Decimal, neutral_band_bps: Decimal) -> str:
    if abs(premium_bps) <= neutral_band_bps:
        return Direction.NEUTRAL
    if premium_bps > Decimal("0"):
        return Direction.KIMP
    return Direction.REVERSE_KIMP


def _classify_convergence(
    current_bps: Decimal,
    previous_bps: Decimal | None,
    threshold_bps: Decimal,
) -> tuple[str, Decimal | None]:
    """|current| 와 |previous| 비교로 EXPANDING / CONVERGING / NEUTRAL / UNKNOWN."""
    if previous_bps is None:
        return ConvergenceState.UNKNOWN, None
    delta = abs(current_bps) - abs(previous_bps)
    if delta > threshold_bps:
        return ConvergenceState.EXPANDING, delta
    if delta < -threshold_bps:
        return ConvergenceState.CONVERGING, delta
    return ConvergenceState.NEUTRAL, delta


def _detect_fx_anomaly(
    fx: Decimal,
    reference_fx: Decimal | None,
    config: KimpCalculatorConfig,
) -> tuple[bool, str | None, Decimal | None]:
    """sanity 범위 + reference 대비 deviation_bps 검사."""
    if fx < config.fx_rate_min or fx > config.fx_rate_max:
        reason = (
            f"fx out of sanity range "
            f"[{config.fx_rate_min}, {config.fx_rate_max}]: {fx}"
        )
        return True, reason, None
    if reference_fx is not None and reference_fx > Decimal("0"):
        deviation_ratio = (fx - reference_fx) / reference_fx
        deviation_bps = abs(deviation_ratio * Decimal("10000"))
        if deviation_bps > config.fx_anomaly_deviation_bps:
            reason = (
                f"fx deviates from reference: "
                f"{deviation_bps} bps > {config.fx_anomaly_deviation_bps} bps"
            )
            return True, reason, deviation_bps
        return False, None, deviation_bps
    return False, None, None


# ── 핵심 함수 ────────────────────────────────────────────────────


def compute_kimp(
    inputs: KimpInputs,
    *,
    config: KimpCalculatorConfig | None = None,
) -> KimpResult:
    """표준 김프 계산. 비정상 입력은 ``is_valid=False`` 로 표시 (raise 없음).

    유효성 검사:
      - domestic_price_krw > 0
      - foreign_price_quote > 0
      - fx_rate_krw > 0
    위 하나라도 실패하면 ``is_valid=False`` + ``risk_flags=("invalid_input",)`` +
    premium 관련 값은 0 으로 채운다.

    유효 입력이면 ``foreign_price_krw``, ``premium_ratio/percent/bps``, ``direction``,
    ``convergence_state``, ``fx_anomaly`` 를 모두 채워 반환한다.
    """
    cfg = config or KimpCalculatorConfig()
    now = datetime.now(timezone.utc)

    domestic = _to_decimal(inputs.domestic_price_krw)
    foreign = _to_decimal(inputs.foreign_price_quote)
    fx = _to_decimal(inputs.fx_rate_krw)

    invalid_reason: str | None = None
    if domestic is None or domestic <= Decimal("0"):
        invalid_reason = f"domestic_price_krw invalid: {domestic}"
    elif foreign is None or foreign <= Decimal("0"):
        invalid_reason = f"foreign_price_quote invalid: {foreign}"
    elif fx is None or fx <= Decimal("0"):
        invalid_reason = f"fx_rate_krw invalid: {fx}"

    if invalid_reason is not None:
        return KimpResult(
            inputs=inputs,
            foreign_price_krw=Decimal("0"),
            premium_ratio=Decimal("0"),
            premium_percent=Decimal("0"),
            premium_bps=Decimal("0"),
            direction=Direction.NEUTRAL,
            convergence_state=ConvergenceState.UNKNOWN,
            delta_bps=None,
            fx_anomaly=False,
            fx_anomaly_reason=None,
            fx_deviation_bps=None,
            is_valid=False,
            invalid_reason=invalid_reason,
            risk_flags=("invalid_input",),
            computed_at=now,
        )

    foreign_krw = foreign * fx
    ratio = (domestic - foreign_krw) / foreign_krw
    pct = ratio * Decimal("100")
    bps = ratio * Decimal("10000")

    direction = _classify_direction(bps, cfg.neutral_band_bps)

    prev_bps = _to_decimal(inputs.previous_premium_bps)
    convergence_state, delta_bps = _classify_convergence(
        bps, prev_bps, cfg.convergence_threshold_bps,
    )

    reference_fx = _to_decimal(inputs.reference_fx_rate_krw)
    fx_anomaly, fx_anomaly_reason, fx_deviation_bps = _detect_fx_anomaly(
        fx, reference_fx, cfg,
    )

    risk_flags: list[str] = []
    if fx_anomaly:
        risk_flags.append("fx_anomaly")
    if abs(bps) >= cfg.structural_min_abs_bps:
        risk_flags.append("large_premium")
    if convergence_state == ConvergenceState.EXPANDING:
        risk_flags.append("expanding")

    return KimpResult(
        inputs=inputs,
        foreign_price_krw=foreign_krw,
        premium_ratio=ratio,
        premium_percent=pct,
        premium_bps=bps,
        direction=direction,
        convergence_state=convergence_state,
        delta_bps=delta_bps,
        fx_anomaly=fx_anomaly,
        fx_anomaly_reason=fx_anomaly_reason,
        fx_deviation_bps=fx_deviation_bps,
        is_valid=True,
        invalid_reason=None,
        risk_flags=tuple(risk_flags),
        computed_at=now,
    )


# ── Fee/funding/transfer adjusted helper ─────────────────────────


def calculate_fee_adjusted_premium_bps(
    raw_premium_bps: Decimal | int | float | str,
    *,
    domestic_fee_bps: Decimal | int | float | str = Decimal("0"),
    foreign_fee_bps: Decimal | int | float | str = Decimal("0"),
    fx_fee_bps: Decimal | int | float | str = Decimal("0"),
    transfer_cost_bps: Decimal | int | float | str = Decimal("0"),
    funding_bps: Decimal | int | float | str = Decimal("0"),
) -> Decimal:
    """raw premium 의 부호를 유지하고 |값| 만큼만 비용을 차감한 *참고용* 보정값.

    공식:
        total_cost_bps = domestic_fee + foreign_fee + fx_fee + transfer_cost
                         + |funding|
        adjusted       = sign(raw) × max(0, |raw| - total_cost_bps)

    경고: 본 보정값은 **실제 거래 가능성을 보장하지 않는다**. 입출금 중단, 슬리피지,
    매칭 실패, 규제, 세금 등 외부 리스크는 반영되지 않는다. 본 값만 보고 주문해서는
    안 된다 (CLAUDE.md §2.3). RiskManager / OrderGuard / PermissionGate 가 최종 판단.
    """
    raw = _to_decimal(raw_premium_bps)
    if raw is None:
        return Decimal("0")
    total_cost = (
        _to_decimal(domestic_fee_bps)
        + _to_decimal(foreign_fee_bps)
        + _to_decimal(fx_fee_bps)
        + _to_decimal(transfer_cost_bps)
        + abs(_to_decimal(funding_bps))
    )
    abs_adj = max(Decimal("0"), abs(raw) - total_cost)
    if raw >= Decimal("0"):
        return abs_adj
    return -abs_adj


# ── KimpAgent hook helpers ───────────────────────────────────────


def build_kimp_context(result: KimpResult) -> dict:
    """KimpAgent / RiskManager 가 참조할 평탄한 context payload.

    Signal 이 아니며, ``direct_order_allowed=False`` 를 명시한다. Decimal 값은
    JSON 직렬화 호환을 위해 str 로 변환한다 (정밀도 보존).
    """
    return {
        "kind": "kimp_calculator_context",
        "direct_order_allowed": False,
        "is_valid": result.is_valid,
        "invalid_reason": result.invalid_reason,
        "symbol": result.inputs.symbol,
        "domestic_exchange": result.inputs.domestic_exchange,
        "foreign_exchange": result.inputs.foreign_exchange,
        "quote_currency": result.inputs.quote_currency,
        "foreign_price_krw": str(result.foreign_price_krw),
        "premium_ratio": str(result.premium_ratio),
        "premium_percent": str(result.premium_percent),
        "premium_bps": str(result.premium_bps),
        "direction": result.direction,
        "convergence_state": result.convergence_state,
        "delta_bps": None if result.delta_bps is None else str(result.delta_bps),
        "fx_anomaly": result.fx_anomaly,
        "fx_anomaly_reason": result.fx_anomaly_reason,
        "fx_deviation_bps": (
            None if result.fx_deviation_bps is None
            else str(result.fx_deviation_bps)
        ),
        "risk_flags": list(result.risk_flags),
        "computed_at": result.computed_at.isoformat(),
    }


def classify_structural_vs_temporary_dislocation(
    results: Iterable[KimpResult],
    *,
    config: KimpCalculatorConfig | None = None,
) -> dict:
    """관측치 시계열에서 구조적/일시적 괴리 분류 context 생성.

    분류 규칙:
      - is_valid 인 결과만 사용. 개수 < ``structural_min_count`` 면 UNKNOWN.
      - 부호가 일관되고 평균 |premium_bps| ≥ ``structural_min_abs_bps`` 이면
        STRUCTURAL.
      - 부호가 일관되지 않으면 TEMPORARY (확률적 변동).
      - 부호 일관 + 평균 미달 → MIXED (작지만 한 방향).
    어떤 경우에도 ``direct_order_allowed=False`` 를 명시한다.
    """
    cfg = config or KimpCalculatorConfig()
    seq = [r for r in results if r.is_valid]
    if len(seq) < cfg.structural_min_count:
        return {
            "kind": "kimp_dislocation_classification",
            "dislocation_kind": DislocationKind.UNKNOWN,
            "direct_order_allowed": False,
            "reason": (
                f"insufficient observations: "
                f"{len(seq)} < {cfg.structural_min_count}"
            ),
            "sample_count": len(seq),
        }
    positives = sum(1 for r in seq if r.premium_bps > Decimal("0"))
    negatives = sum(1 for r in seq if r.premium_bps < Decimal("0"))
    same_sign = positives == 0 or negatives == 0
    avg_abs = sum((abs(r.premium_bps) for r in seq), Decimal("0")) / Decimal(len(seq))
    fx_anomaly_any = any(r.fx_anomaly for r in seq)
    if not same_sign:
        kind = DislocationKind.TEMPORARY
    elif avg_abs >= cfg.structural_min_abs_bps:
        kind = DislocationKind.STRUCTURAL
    else:
        kind = DislocationKind.MIXED
    return {
        "kind": "kimp_dislocation_classification",
        "dislocation_kind": kind,
        "direct_order_allowed": False,
        "same_sign": same_sign,
        "avg_abs_premium_bps": str(avg_abs),
        "sample_count": len(seq),
        "fx_anomaly_present": fx_anomaly_any,
    }


__all__ = (
    "Direction",
    "ConvergenceState",
    "DislocationKind",
    "KimpCalculatorConfig",
    "KimpInputs",
    "KimpResult",
    "compute_kimp",
    "calculate_fee_adjusted_premium_bps",
    "build_kimp_context",
    "classify_structural_vs_temporary_dislocation",
)
