"""Funding Cost Guard — 체크리스트 #36 Funding Cost Guard.

펀딩비 *리스크/비용 판단 계층*. 본 모듈은:

  - Signal 을 직접 주문으로 바꾸지 *않는다*.
  - place_order / cancel_order / get_balance / submit_order / withdraw /
    deposit 를 호출하지 *않는다*.
  - broker / adapter / OrderGateway / execution 계층을 import 하지 *않는다*.
  - 실제 거래소 주문/선물·스왑/레버리지·마진 API 를 추가하지 *않는다*.
  - BUY / SELL / ENTER / EXIT 토큰을 *반환값으로* 사용하지 *않는다*.
  - Strategy 가 아니라 *리스크/비용 guard*. KimpGuards / KimpStrategy /
    PairTrading / RiskManager 가 참조용으로만 사용한다.

기존 ``app.market.funding`` (#36 1차 — 순수 펀딩 수식) 는 변경 없이 보존되며
KimpStrategy / kimp_guards 회귀를 보장한다. 본 모듈은 그 위에 구조적
``FundingGuardDecision`` API 를 추가한다.

핵심 산출 (Decimal):
  - FundingCostEstimate.cost_pct / abs_cost_pct / cost_bps : 보유 기간 누적 비용
  - FundingCostEstimate.annualized_pct                     : APR 환산
  - FundingCostEstimate.cost_to_edge_ratio                 : |비용| / |edge|
  - FundingCostEstimate.is_unfavorable                     : 포지션에 비용인지

원칙 (CLAUDE.md §2.3 / §2.5 / §3.1):
  - direct_order_allowed = False / used_for_order = False 영구.
  - FundingCostGuard 는 *예상 비용 추정값* 만 제공 — 실제 수익이 아니다.
  - 거래소별 funding 산식과 정산 시각은 다를 수 있다 (interval_hours 인자로 노출).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping


# ── 상수 / 라벨 ──────────────────────────────────────────────────


class GuardSeverity:
    """가드 사유 심각도."""

    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


_SEVERITY_RANK: Mapping[str, int] = {
    GuardSeverity.INFO: 0,
    GuardSeverity.WARNING: 1,
    GuardSeverity.HIGH: 2,
    GuardSeverity.CRITICAL: 3,
}

_BLOCKING_SEVERITIES: frozenset[str] = frozenset(
    (GuardSeverity.HIGH, GuardSeverity.CRITICAL),
)


class GuardSource:
    """펀딩 가드 사유 출처."""

    FUNDING_DATA = "funding_data"
    FUNDING_COST = "funding_cost"
    FUNDING_DIRECTION = "funding_direction"
    FUNDING_ACCUMULATED = "funding_accumulated"
    MISSING_CONTEXT = "missing_context"


class RecommendedAction:
    """가드가 호출자에게 권고하는 다음 동작 라벨. *주문 명령이 아님*.

    NOTE: 본 라벨 집합은 BUY / SELL / ENTER / EXIT 토큰을 *포함하지 않는다*
    (CLAUDE.md §3.1 — 정적 회귀로 강제).
    """

    ALLOW_NEW_CANDIDATE = "ALLOW_NEW_CANDIDATE"
    BLOCK_NEW_CANDIDATE = "BLOCK_NEW_CANDIDATE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    HOLD_CANDIDATE = "HOLD_CANDIDATE"
    REDUCE_CANDIDATE = "REDUCE_CANDIDATE"


class GuardCode:
    """개별 사유 코드 (machine-readable)."""

    # Data
    FUNDING_DATA_MISSING = "funding_data_missing"
    FUNDING_DATA_STALE = "funding_data_stale"
    FUNDING_INVALID_INTERVAL = "funding_invalid_interval"
    # Cost / extreme
    FUNDING_EXTREME = "funding_extreme"
    FUNDING_COST_EXCEEDS_EDGE = "funding_cost_exceeds_edge"
    FUNDING_COST_NEAR_EDGE = "funding_cost_near_edge"
    # Direction
    FUNDING_DIRECTION_ADVERSE = "funding_direction_adverse"
    # Held position
    FUNDING_ACCUMULATED_HIGH = "funding_accumulated_high"
    FUNDING_ACCUMULATED_REDUCE = "funding_accumulated_reduce"
    # Generic
    MISSING_CRITICAL_CONTEXT = "missing_critical_context"


# 허용 side 라벨. *주문 명령이 아닌 포지션 방향 설명*.
_VALID_SIDES: frozenset[str] = frozenset(("long", "short"))


# ── 설정 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FundingGuardConfig:
    """펀딩 가드 임계값 + 정책 토글.

    수치는 모두 Decimal 또는 int — 외부 입력 (Settings 등) 은 호출자가 변환.
    """

    # interval 기본 (snapshot 미지정 시)
    funding_interval_hours: Decimal = Decimal("8")
    # 데이터 stale 임계 (초)
    max_funding_age_seconds: int = 600
    # |rate| × 100 (bps) 가 이 이상이면 *extreme* — block
    extreme_threshold_bps: Decimal = Decimal("100")
    # cost_to_edge_ratio 임계
    review_ratio: Decimal = Decimal("0.4")   # 40% 이상 → REVIEW
    block_ratio: Decimal = Decimal("0.8")    # 80% 이상 → BLOCK
    # 보유 포지션 누적 비용
    accumulated_cost_warning_pct: Decimal = Decimal("1.0")  # 1% → review
    accumulated_cost_reduce_pct: Decimal = Decimal("2.0")   # 2% → reduce
    # missing/stale 정책
    require_funding_context: bool = True
    # 영구 False
    direct_order_allowed: bool = False
    used_for_order: bool = False


# ── 입력 / 추정 / 결정 ───────────────────────────────────────────


@dataclass(frozen=True)
class FundingRateSnapshot:
    """단일 시점의 펀딩비 관측치.

    - ``rate_pct`` : 단일 funding 주기당 % (예: 0.01 = 0.01%).
    - ``interval_hours`` : 거래소별 주기 (보통 8h, Bybit/Binance/OKX 등).
    - ``timestamp`` : 관측 시각. stale 판정에 사용.
    """

    rate_pct: Decimal
    timestamp: datetime | None = None
    interval_hours: Decimal = Decimal("8")
    exchange: str | None = None
    symbol: str | None = None


@dataclass(frozen=True)
class FundingCostInput:
    """FundingCostGuard 가 받는 *읽기 전용* 입력.

    - ``side`` : ``"long"`` 또는 ``"short"`` — 포지션 방향 설명 (주문 명령 아님).
    - ``snapshot`` : 펀딩 관측치. None 이면 missing 가드 작동.
    - ``intended_hours_held`` : 예상 보유 시간 (entry 평가에서 누적 비용 계산).
    - ``expected_edge_pct`` : 예상 기대수익 %. cost_to_edge_ratio 계산에 사용.
    - ``is_held`` : 이미 보유 중인지 (hold 평가에서 True).
    - ``accumulated_funding_cost_pct`` : 보유 중 누적된 펀딩 비용 %.
    - ``holding_hours_so_far`` : 누적 보유 시간 (정보용).
    - ``now`` : 결정성 (테스트용).
    """

    symbol: str
    side: str  # "long" / "short"
    snapshot: FundingRateSnapshot | None = None
    intended_hours_held: Decimal = Decimal("8")
    expected_edge_pct: Decimal | None = None
    is_held: bool = False
    accumulated_funding_cost_pct: Decimal | None = None
    holding_hours_so_far: Decimal | None = None
    now: datetime | None = None


@dataclass(frozen=True)
class FundingCostEstimate:
    """펀딩 비용 추정값. **실제 수익이 아닌 예상 비용**.

    - ``cost_pct`` : 부호 보존. 양수 = 비용 (포지션이 지불), 음수 = 수익 (수취).
    - ``abs_cost_pct`` : |cost_pct|.
    - ``cost_bps`` : abs_cost_pct × 100.
    - ``annualized_pct`` : 단일 주기 rate × (24/interval × 365).
    - ``num_funding_events`` : 보유 기간 / interval (분수 허용).
    - ``cost_to_edge_ratio`` : abs_cost_pct / |expected_edge_pct| (edge 양수일 때).
    - ``is_unfavorable`` : cost_pct > 0 (지불 방향).
    """

    cost_pct: Decimal
    abs_cost_pct: Decimal
    cost_bps: Decimal
    annualized_pct: Decimal | None
    num_funding_events: Decimal
    cost_to_edge_ratio: Decimal | None
    is_unfavorable: bool
    rate_pct: Decimal
    side: str
    interval_hours: Decimal


@dataclass(frozen=True)
class FundingGuardReason:
    """단일 가드 사유."""

    code: str
    severity: str
    source: str
    message: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_blocking(self) -> bool:
        return self.severity in _BLOCKING_SEVERITIES


@dataclass(frozen=True)
class FundingGuardDecision:
    """가드 평가 결과. Signal 이 아니며 주문 권한이 아니다."""

    input: FundingCostInput
    estimate: FundingCostEstimate | None
    allowed: bool
    required_review: bool
    recommended_action: str
    reasons: tuple[FundingGuardReason, ...]
    blocked_by: tuple[str, ...]
    review_codes: tuple[str, ...]
    computed_at: datetime
    mode: str = "entry"  # "entry" | "hold"
    direct_order_allowed: bool = False  # 영구 False
    used_for_order: bool = False        # 영구 False

    @property
    def has_blocking(self) -> bool:
        return any(r.is_blocking for r in self.reasons)

    def summary(self) -> str:
        if self.recommended_action in (
            RecommendedAction.BLOCK_NEW_CANDIDATE,
            RecommendedAction.REDUCE_CANDIDATE,
        ):
            return f"BLOCKED — {len(self.blocked_by)} blocking reason(s)"
        if self.recommended_action == RecommendedAction.REVIEW_REQUIRED:
            return f"REVIEW — {len(self.review_codes)} non-blocking reason(s)"
        return "ALLOWED — no risk reasons"


# ── 내부 헬퍼 ────────────────────────────────────────────────────


def _to_decimal(value: Decimal | int | float | str | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _normalize_dt(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _age_seconds(ts: datetime | None, now: datetime) -> float | None:
    n = _normalize_dt(ts)
    if n is None:
        return None
    return max(0.0, (now - n).total_seconds())


def _resolve_now(input: FundingCostInput) -> datetime:
    return _normalize_dt(input.now) or datetime.now(timezone.utc)


def _signed_per_event_cost(rate: Decimal, side: str) -> Decimal:
    """포지션 1단위 당 단일 funding 시점의 부호 있는 비용 %.

    - short + 양의 funding (long → short) → short 가 수취 → 음수 비용.
    - long  + 양의 funding              → long 이 지불 → 양수 비용.
    - 알 수 없는 side → 보수적 abs() (수익 가능성을 비용으로 깎음).
    """
    s = (side or "").strip().lower()
    if s == "short":
        return -rate
    if s == "long":
        return rate
    return abs(rate)


# ── 비용 추정 ────────────────────────────────────────────────────


def compute_funding_estimate(
    input: FundingCostInput,
    config: FundingGuardConfig | None = None,
) -> FundingCostEstimate | None:
    """입력으로부터 ``FundingCostEstimate`` 산출. snapshot 없으면 None."""
    cfg = config or FundingGuardConfig()
    snap = input.snapshot
    if snap is None:
        return None
    rate = _to_decimal(snap.rate_pct)
    if rate is None:
        return None
    raw_interval = _to_decimal(snap.interval_hours)
    interval = raw_interval if raw_interval is not None else cfg.funding_interval_hours
    if interval <= Decimal("0"):
        # invalid interval — caller 의 가드가 별도 사유 추가. estimate 는 0 분수.
        zero = Decimal("0")
        return FundingCostEstimate(
            cost_pct=zero, abs_cost_pct=zero, cost_bps=zero,
            annualized_pct=None, num_funding_events=zero,
            cost_to_edge_ratio=None,
            is_unfavorable=False,
            rate_pct=rate, side=(input.side or "").lower(),
            interval_hours=interval,
        )
    hours = _to_decimal(input.intended_hours_held) or Decimal("0")
    num_events = hours / interval if hours > Decimal("0") else Decimal("0")
    per_event = _signed_per_event_cost(rate, input.side)
    signed_cost = per_event * num_events
    abs_cost = abs(signed_cost)
    cost_bps = abs_cost * Decimal("100")
    periods_per_year = (Decimal("24") / interval) * Decimal("365")
    annualized = rate * periods_per_year
    edge = _to_decimal(input.expected_edge_pct)
    ratio: Decimal | None
    if edge is not None and abs(edge) > Decimal("0"):
        ratio = abs_cost / abs(edge)
    else:
        ratio = None
    return FundingCostEstimate(
        cost_pct=signed_cost,
        abs_cost_pct=abs_cost,
        cost_bps=cost_bps,
        annualized_pct=annualized,
        num_funding_events=num_events,
        cost_to_edge_ratio=ratio,
        is_unfavorable=signed_cost > Decimal("0"),
        rate_pct=rate,
        side=(input.side or "").lower(),
        interval_hours=interval,
    )


# ── 사유 합산 헬퍼 ───────────────────────────────────────────────


def _check_funding_data(
    input: FundingCostInput,
    config: FundingGuardConfig,
    now: datetime,
) -> list[FundingGuardReason]:
    """snapshot 존재 / interval / stale 검사."""
    out: list[FundingGuardReason] = []
    snap = input.snapshot
    if snap is None:
        sev = (
            GuardSeverity.HIGH if config.require_funding_context
            else GuardSeverity.WARNING
        )
        out.append(FundingGuardReason(
            code=GuardCode.FUNDING_DATA_MISSING,
            severity=sev,
            source=GuardSource.FUNDING_DATA,
            message="funding snapshot 미수신",
            evidence={"require_funding_context": config.require_funding_context},
        ))
        return out
    raw_interval = _to_decimal(snap.interval_hours)
    interval = raw_interval if raw_interval is not None else config.funding_interval_hours
    if interval <= Decimal("0"):
        out.append(FundingGuardReason(
            code=GuardCode.FUNDING_INVALID_INTERVAL,
            severity=GuardSeverity.HIGH,
            source=GuardSource.FUNDING_DATA,
            message=f"funding interval 비정상: {interval}",
            evidence={"interval_hours": str(interval)},
        ))
    age = _age_seconds(snap.timestamp, now)
    if age is None:
        out.append(FundingGuardReason(
            code=GuardCode.FUNDING_DATA_STALE,
            severity=GuardSeverity.HIGH,
            source=GuardSource.FUNDING_DATA,
            message="funding timestamp 없음 — stale 로 간주",
        ))
    elif age > config.max_funding_age_seconds:
        out.append(FundingGuardReason(
            code=GuardCode.FUNDING_DATA_STALE,
            severity=GuardSeverity.HIGH,
            source=GuardSource.FUNDING_DATA,
            message=(
                f"funding 데이터 stale: age={age:.2f}s "
                f"> max={config.max_funding_age_seconds}s"
            ),
            evidence={
                "age_seconds": age,
                "max_age_seconds": config.max_funding_age_seconds,
            },
        ))
    return out


def _check_funding_extreme(
    snap: FundingRateSnapshot,
    config: FundingGuardConfig,
) -> list[FundingGuardReason]:
    """|rate| × 100 (bps) 가 extreme_threshold_bps 초과."""
    rate = _to_decimal(snap.rate_pct)
    if rate is None:
        return []
    rate_bps = abs(rate) * Decimal("100")
    if rate_bps > config.extreme_threshold_bps:
        return [FundingGuardReason(
            code=GuardCode.FUNDING_EXTREME,
            severity=GuardSeverity.HIGH,
            source=GuardSource.FUNDING_COST,
            message=(
                f"펀딩비 이상치: |rate|={rate_bps} bps "
                f"> max={config.extreme_threshold_bps} bps"
            ),
            evidence={
                "rate_pct": str(rate),
                "rate_bps": str(rate_bps),
                "max_bps": str(config.extreme_threshold_bps),
            },
        )]
    return []


def _check_cost_to_edge(
    estimate: FundingCostEstimate | None,
    config: FundingGuardConfig,
) -> list[FundingGuardReason]:
    """cost_to_edge_ratio 정책: block_ratio 이상 BLOCK, review_ratio 이상 REVIEW.

    *불리한 방향* 일 때만 적용 — 수익 방향이면 비용 0 또는 음수라 ratio 의미 없음.
    """
    if estimate is None:
        return []
    if not estimate.is_unfavorable:
        return []
    ratio = estimate.cost_to_edge_ratio
    if ratio is None:
        return []
    if ratio >= config.block_ratio:
        return [FundingGuardReason(
            code=GuardCode.FUNDING_COST_EXCEEDS_EDGE,
            severity=GuardSeverity.HIGH,
            source=GuardSource.FUNDING_COST,
            message=(
                f"펀딩 비용이 기대 edge 의 {ratio:.3f} 배 "
                f"≥ block 임계 {config.block_ratio}"
            ),
            evidence={
                "cost_to_edge_ratio": str(ratio),
                "block_ratio": str(config.block_ratio),
                "abs_cost_pct": str(estimate.abs_cost_pct),
            },
        )]
    if ratio >= config.review_ratio:
        return [FundingGuardReason(
            code=GuardCode.FUNDING_COST_NEAR_EDGE,
            severity=GuardSeverity.WARNING,
            source=GuardSource.FUNDING_COST,
            message=(
                f"펀딩 비용이 기대 edge 의 {ratio:.3f} 배 "
                f"≥ review 임계 {config.review_ratio}"
            ),
            evidence={
                "cost_to_edge_ratio": str(ratio),
                "review_ratio": str(config.review_ratio),
                "abs_cost_pct": str(estimate.abs_cost_pct),
            },
        )]
    return []


def _check_direction_adverse(
    estimate: FundingCostEstimate | None,
) -> list[FundingGuardReason]:
    """estimate.is_unfavorable=True 면 WARNING (단독으로는 차단하지 않음)."""
    if estimate is None or not estimate.is_unfavorable:
        return []
    return [FundingGuardReason(
        code=GuardCode.FUNDING_DIRECTION_ADVERSE,
        severity=GuardSeverity.WARNING,
        source=GuardSource.FUNDING_DIRECTION,
        message=(
            f"펀딩 방향 불리: side={estimate.side}, rate={estimate.rate_pct} %"
            f" — 누적 비용 {estimate.abs_cost_pct} %"
        ),
        evidence={
            "side": estimate.side,
            "rate_pct": str(estimate.rate_pct),
            "abs_cost_pct": str(estimate.abs_cost_pct),
        },
    )]


def _check_accumulated_cost(
    input: FundingCostInput,
    config: FundingGuardConfig,
) -> list[FundingGuardReason]:
    """보유 중 누적 비용 정책 (hold 평가 전용)."""
    acc = _to_decimal(input.accumulated_funding_cost_pct)
    if acc is None:
        return []
    if acc >= config.accumulated_cost_reduce_pct:
        return [FundingGuardReason(
            code=GuardCode.FUNDING_ACCUMULATED_REDUCE,
            severity=GuardSeverity.HIGH,
            source=GuardSource.FUNDING_ACCUMULATED,
            message=(
                f"누적 펀딩 비용 {acc} % "
                f"≥ reduce 임계 {config.accumulated_cost_reduce_pct} %"
            ),
            evidence={
                "accumulated_pct": str(acc),
                "reduce_threshold_pct": str(config.accumulated_cost_reduce_pct),
            },
        )]
    if acc >= config.accumulated_cost_warning_pct:
        return [FundingGuardReason(
            code=GuardCode.FUNDING_ACCUMULATED_HIGH,
            severity=GuardSeverity.WARNING,
            source=GuardSource.FUNDING_ACCUMULATED,
            message=(
                f"누적 펀딩 비용 {acc} % "
                f"≥ warning 임계 {config.accumulated_cost_warning_pct} %"
            ),
            evidence={
                "accumulated_pct": str(acc),
                "warning_threshold_pct": str(
                    config.accumulated_cost_warning_pct,
                ),
            },
        )]
    return []


def _check_missing_critical(
    input: FundingCostInput,
) -> list[FundingGuardReason]:
    """side / symbol 등 필수 필드 누락 보충."""
    missing: list[str] = []
    side = (input.side or "").strip().lower()
    if side not in _VALID_SIDES:
        missing.append("side")
    if not (input.symbol or "").strip():
        missing.append("symbol")
    if missing:
        return [FundingGuardReason(
            code=GuardCode.MISSING_CRITICAL_CONTEXT,
            severity=GuardSeverity.HIGH,
            source=GuardSource.MISSING_CONTEXT,
            message=f"필수 context 누락: {missing}",
            evidence={"missing": missing},
        )]
    return []


# ── 합성 함수 ────────────────────────────────────────────────────


def _compose(
    *,
    mode: str,
    input: FundingCostInput,
    estimate: FundingCostEstimate | None,
    reasons: tuple[FundingGuardReason, ...],
) -> FundingGuardDecision:
    blocked_by = tuple(r.code for r in reasons if r.is_blocking)
    review_codes = tuple(r.code for r in reasons if not r.is_blocking)
    if blocked_by:
        allowed = False
        required_review = True
        action = (
            RecommendedAction.BLOCK_NEW_CANDIDATE if mode == "entry"
            else RecommendedAction.REDUCE_CANDIDATE
        )
    elif review_codes:
        allowed = True
        required_review = True
        action = RecommendedAction.REVIEW_REQUIRED
    else:
        allowed = True
        required_review = False
        action = (
            RecommendedAction.ALLOW_NEW_CANDIDATE if mode == "entry"
            else RecommendedAction.HOLD_CANDIDATE
        )
    return FundingGuardDecision(
        input=input,
        estimate=estimate,
        allowed=allowed,
        required_review=required_review,
        recommended_action=action,
        reasons=reasons,
        blocked_by=blocked_by,
        review_codes=review_codes,
        computed_at=datetime.now(timezone.utc),
        mode=mode,
    )


def evaluate_funding_entry(
    input: FundingCostInput,
    *,
    config: FundingGuardConfig | None = None,
) -> FundingGuardDecision:
    """신규 진입 후보용 평가.

    적용 가드:
      - missing/stale/invalid_interval (HIGH)
      - extreme |rate| (HIGH)
      - direction adverse (WARNING)
      - cost_to_edge_ratio (HIGH/WARNING)
      - missing critical context (side/symbol) (HIGH)
    """
    cfg = config or FundingGuardConfig()
    now = _resolve_now(input)
    reasons: list[FundingGuardReason] = []
    reasons.extend(_check_missing_critical(input))
    reasons.extend(_check_funding_data(input, cfg, now))
    estimate: FundingCostEstimate | None = None
    if input.snapshot is not None:
        reasons.extend(_check_funding_extreme(input.snapshot, cfg))
        estimate = compute_funding_estimate(input, cfg)
        reasons.extend(_check_direction_adverse(estimate))
        reasons.extend(_check_cost_to_edge(estimate, cfg))
    return _compose(
        mode="entry", input=input, estimate=estimate,
        reasons=tuple(reasons),
    )


def evaluate_funding_hold(
    input: FundingCostInput,
    *,
    config: FundingGuardConfig | None = None,
) -> FundingGuardDecision:
    """보유 포지션 유지 평가.

    적용 가드:
      - missing/stale (HIGH)
      - extreme |rate| (HIGH)
      - direction adverse (WARNING)
      - accumulated cost (WARNING / HIGH)
      - missing critical context (HIGH)
    """
    cfg = config or FundingGuardConfig()
    now = _resolve_now(input)
    reasons: list[FundingGuardReason] = []
    reasons.extend(_check_missing_critical(input))
    reasons.extend(_check_funding_data(input, cfg, now))
    estimate: FundingCostEstimate | None = None
    if input.snapshot is not None:
        reasons.extend(_check_funding_extreme(input.snapshot, cfg))
        estimate = compute_funding_estimate(input, cfg)
        reasons.extend(_check_direction_adverse(estimate))
    reasons.extend(_check_accumulated_cost(input, cfg))
    return _compose(
        mode="hold", input=input, estimate=estimate,
        reasons=tuple(reasons),
    )


# ── FundingCostGuard (클래스 진입점) ─────────────────────────────


class FundingCostGuard:
    """``FundingGuardConfig`` 를 묶어 evaluate_entry / evaluate_hold 를 제공.

    KimpGuards / KimpStrategy / PairTrading / RiskManager 가 의존성 주입 패턴으로
    쓰기 좋은 진입점. 본 클래스도 broker / OrderGateway / network SDK 를 import
    하지 않으며 주문 호출을 하지 않는다 (정적 회귀로 강제).
    """

    def __init__(self, config: FundingGuardConfig | None = None):
        self.config = config or FundingGuardConfig()

    def estimate(
        self, input: FundingCostInput,
    ) -> FundingCostEstimate | None:
        return compute_funding_estimate(input, self.config)

    def evaluate_entry(
        self, input: FundingCostInput,
    ) -> FundingGuardDecision:
        return evaluate_funding_entry(input, config=self.config)

    def evaluate_hold(
        self, input: FundingCostInput,
    ) -> FundingGuardDecision:
        return evaluate_funding_hold(input, config=self.config)


# ── KimpAgent / RiskManager hook ─────────────────────────────────


def build_funding_guard_context(decision: FundingGuardDecision) -> dict:
    """Agent / RiskManager 가 참조할 *평탄 dict* context.

    - ``direct_order_allowed=False`` / ``used_for_order=False`` 명시.
    - Decimal / datetime 은 str 직렬화 (정밀도 보존, JSON 호환).
    - reasons 평탄 list — code/severity/source/message 노출.
    """
    est = decision.estimate
    return {
        "kind": "funding_guard_context",
        "direct_order_allowed": False,
        "used_for_order": False,
        "mode": decision.mode,
        "symbol": decision.input.symbol,
        "side": (decision.input.side or "").lower(),
        "allowed": decision.allowed,
        "required_review": decision.required_review,
        "recommended_action": decision.recommended_action,
        "blocked_by": list(decision.blocked_by),
        "review_codes": list(decision.review_codes),
        "reasons": [
            {
                "code": r.code,
                "severity": r.severity,
                "source": r.source,
                "message": r.message,
            }
            for r in decision.reasons
        ],
        "estimate": (
            None if est is None
            else {
                "cost_pct": str(est.cost_pct),
                "abs_cost_pct": str(est.abs_cost_pct),
                "cost_bps": str(est.cost_bps),
                "annualized_pct": (
                    None if est.annualized_pct is None
                    else str(est.annualized_pct)
                ),
                "num_funding_events": str(est.num_funding_events),
                "cost_to_edge_ratio": (
                    None if est.cost_to_edge_ratio is None
                    else str(est.cost_to_edge_ratio)
                ),
                "is_unfavorable": est.is_unfavorable,
                "rate_pct": str(est.rate_pct),
                "side": est.side,
                "interval_hours": str(est.interval_hours),
            }
        ),
        "summary": decision.summary(),
        "computed_at": decision.computed_at.isoformat(),
    }


__all__ = (
    "GuardSeverity",
    "GuardSource",
    "GuardCode",
    "RecommendedAction",
    "FundingGuardConfig",
    "FundingRateSnapshot",
    "FundingCostInput",
    "FundingCostEstimate",
    "FundingGuardReason",
    "FundingGuardDecision",
    "compute_funding_estimate",
    "evaluate_funding_entry",
    "evaluate_funding_hold",
    "FundingCostGuard",
    "build_funding_guard_context",
)
