"""Kimp Risk Guards — 체크리스트 #35 Kimp Guards.

김프/역김프 진입 *후보 차단* 계층. 본 모듈은:

  - Signal 을 직접 주문으로 바꾸지 *않는다*.
  - broker / adapter / OrderGateway / execution 계층을 import 하지 *않는다*.
  - place_order / cancel_order / get_balance 를 호출하지 *않는다*.
  - BUY / SELL / ENTER / EXIT 를 반환값으로 사용하지 *않는다*.
  - KimpStrategy / KimpAgent 가 사용할 ``KimpGuardDecision`` 만 반환한다.

기존 ``app.strategies.kimp_guards`` (#35 1차 — float 기반 7+1 단계 가드) 는 변경
없이 보존된다. 본 모듈은 그 위에 구조적 ``KimpGuardDecision`` API 를 추가한다 —
notice / FX / liquidity / bull-market short / funding / freshness / data quality /
missing-context 8개 가드를 합성한다.

정책 (CLAUDE.md §2.3 / §2.5 / §3.1):
  - 하나라도 CRITICAL / HIGH 사유가 있으면 *blocked* (recommended_action=BLOCK_CANDIDATE).
  - INFO / WARNING 사유만 있으면 *required_review=True* (recommended_action=REVIEW_REQUIRED).
  - 사유가 없으면 *allowed* (recommended_action=ALLOW_CANDIDATE).
  - 모든 경로에서 ``direct_order_allowed=False`` / ``used_for_order=False`` 영구.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from app.market.kimp_calculator import KimpResult


# ── 상수 ─────────────────────────────────────────────────────────


class GuardSeverity:
    """가드 사유 심각도. CRITICAL/HIGH 는 차단, WARNING/INFO 는 검토 권고."""

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


class RecommendedAction:
    """KimpGuards 가 호출자에게 권고하는 다음 동작 라벨. *주문 명령이 아님*."""

    ALLOW_CANDIDATE = "ALLOW_CANDIDATE"
    CONTEXT_ONLY = "CONTEXT_ONLY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCK_CANDIDATE = "BLOCK_CANDIDATE"


class GuardSource:
    """가드 사유의 *출처*. 어떤 가드가 사유를 만들었는지 추적용."""

    NOTICE = "notice"
    FX = "fx"
    LIQUIDITY = "liquidity"
    BULL_MARKET = "bull_market"
    FUNDING = "funding"
    FRESHNESS = "freshness"
    DATA_QUALITY = "data_quality"
    MISSING_CONTEXT = "missing_context"


class GuardCode:
    """개별 가드 사유 코드 (machine-readable)."""

    # Notice
    DEPOSIT_WITHDRAWAL_SUSPENDED = "deposit_withdrawal_suspended"
    DELISTING = "delisting_notice"
    CAUTION_NOTICE = "caution_notice"
    TRADING_SUSPENSION = "trading_suspension"
    HIGH_SEVERITY_NOTICE = "high_severity_notice"
    NOTICE_CONTEXT_MISSING = "notice_context_missing"
    # FX
    FX_INVALID = "fx_invalid"
    FX_STALE = "fx_stale"
    FX_ANOMALY = "fx_anomaly"
    FX_SOURCE_MISSING = "fx_source_missing"
    # Liquidity
    LIQUIDITY_THIN = "liquidity_thin"
    SPREAD_WIDE = "spread_wide"
    ORDERBOOK_MISSING = "orderbook_missing"
    ORDERBOOK_STALE = "orderbook_stale"
    ORDERBOOK_INVALID = "orderbook_invalid"
    # Bull market
    BULL_MARKET_SHORT_BLOCKED = "bull_market_short_blocked"
    # Funding
    FUNDING_RISK_HIGH = "funding_risk_high"
    FUNDING_DIRECTION_ADVERSE = "funding_direction_adverse"
    FUNDING_STALE = "funding_stale"
    FUNDING_CONTEXT_MISSING = "funding_context_missing"
    # Freshness
    DOMESTIC_PRICE_STALE = "domestic_price_stale"
    FOREIGN_PRICE_STALE = "foreign_price_stale"
    PRICE_TIMESTAMP_MISSING = "price_timestamp_missing"
    # Data quality
    DATA_QUALITY_EXCLUDE = "data_quality_exclude"
    DATA_QUALITY_WARNING = "data_quality_warning"
    # Generic
    MISSING_CRITICAL_CONTEXT = "missing_critical_context"


# 후보 김프 상태 — 본 모듈은 *후보 라벨* 만 받는다 (action 토큰 아님).
class KimpCandidateState:
    REVERSE_KIMP_CANDIDATE = "REVERSE_KIMP_CANDIDATE"
    KIMP_CANDIDATE = "KIMP_CANDIDATE"
    NEUTRAL_CANDIDATE = "NEUTRAL_CANDIDATE"
    UNKNOWN = "UNKNOWN"


# ── 설정 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KimpGuardConfig:
    """가드 임계값. 모두 Decimal/int — Settings 등 외부 입력은 호출자가 변환."""

    # FX
    max_fx_age_seconds: int = 60
    # 가격
    max_price_age_seconds: int = 30
    # 호가창
    max_orderbook_age_seconds: int = 10
    min_bid_size: Decimal = Decimal("0")    # 0 이면 size 체크 비활성
    min_ask_size: Decimal = Decimal("0")
    max_spread_bps: Decimal = Decimal("50")  # 0.5%
    require_orderbook_context: bool = False
    # 강세장 short 금지
    block_reverse_kimp_short_in_bull_market: bool = True
    bull_market_regimes: tuple[str, ...] = (
        "STRONG_BULL", "BULL_TREND",
    )
    bull_market_themes: tuple[str, ...] = (
        "ETF_INFLOW", "MARKET_WIDE_RALLY", "RISK_ON_STRONG",
    )
    # 펀딩
    funding_risk_threshold_bps: Decimal = Decimal("100")  # 1% per period
    max_funding_age_seconds: int = 600
    require_funding_context: bool = False
    # 공지 / 데이터 품질
    require_notice_context: bool = True
    block_on_data_quality_exclude: bool = True
    block_on_data_quality_warning: bool = False  # 기본 WARNING 은 review only
    # 영구 False
    direct_order_allowed: bool = False
    used_for_order: bool = False


# ── 입력 / 사유 / 결정 ───────────────────────────────────────────


@dataclass(frozen=True)
class KimpGuardInput:
    """KimpGuards 가 받는 모든 input 의 *읽기 전용* 묶음.

    필드는 모두 optional — 누락된 필드는 *missing_critical_context* 가드가 보수적
    경고 또는 차단을 만든다 (require_* 설정에 따라).

    Notice 는 듀크 타입 dict 의 시퀀스로 받는다 — 기존 ``app.market.notice_context``
    의 ``NoticeContext`` 와 결합도 없이 ``app.db.models.ExchangeNotice`` row 또는
    builder 가 만든 dict 모두 처리 가능. 기대 키:
        ``notice_type`` / ``severity`` / ``symbols`` / ``exchange`` / ``title``
    """

    symbol: str
    intended_kimp_state: str = KimpCandidateState.UNKNOWN
    domestic_exchange: str = "upbit"
    foreign_exchange: str = "okx"
    # Notice
    notices: tuple[Mapping[str, Any], ...] = ()
    notice_context_available: bool = True
    # FX
    fx_rate_krw: Decimal | None = None
    fx_timestamp: datetime | None = None
    fx_reference: Decimal | None = None
    fx_source: str | None = None
    # 계산 결과 (#34)
    kimp_result: KimpResult | None = None
    # 호가창
    domestic_bid: Decimal | None = None
    domestic_ask: Decimal | None = None
    domestic_bid_size: Decimal | None = None
    domestic_ask_size: Decimal | None = None
    foreign_bid: Decimal | None = None
    foreign_ask: Decimal | None = None
    foreign_bid_size: Decimal | None = None
    foreign_ask_size: Decimal | None = None
    orderbook_timestamp: datetime | None = None
    # 시장 regime / 테마
    market_regime: str | None = None
    theme_tags: tuple[str, ...] = ()
    short_leg_implied: bool = False
    # 펀딩
    funding_rate_pct: Decimal | None = None
    funding_timestamp: datetime | None = None
    funding_position_side: str | None = None  # "short" / "long"
    # 가격 freshness
    domestic_price_timestamp: datetime | None = None
    foreign_price_timestamp: datetime | None = None
    # 데이터 품질
    data_quality_grade: str | None = None  # GOOD / WARNING / EXCLUDE
    # 현재 시각 (테스트 결정성)
    now: datetime | None = None


@dataclass(frozen=True)
class KimpGuardReason:
    """단일 가드 사유. 가드별로 다수 생성 가능."""

    code: str
    severity: str
    source: str
    message: str
    exchange: str | None = None
    symbol: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_blocking(self) -> bool:
        return self.severity in _BLOCKING_SEVERITIES


@dataclass(frozen=True)
class KimpGuardDecision:
    """가드 평가 결과. *Signal 이 아님* — KimpStrategy 가 참조용으로만 사용."""

    input: KimpGuardInput
    allowed: bool
    required_review: bool
    recommended_action: str
    reasons: tuple[KimpGuardReason, ...]
    blocked_by: tuple[str, ...]
    review_codes: tuple[str, ...]
    computed_at: datetime
    direct_order_allowed: bool = False  # 영구 False
    used_for_order: bool = False        # 영구 False

    @property
    def has_blocking(self) -> bool:
        return any(r.is_blocking for r in self.reasons)

    def summary(self) -> str:
        if self.recommended_action == RecommendedAction.BLOCK_CANDIDATE:
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
    delta = (now - n).total_seconds()
    return max(0.0, delta)


def _notice_targets(notice: Mapping[str, Any]) -> set[str]:
    """notice 의 대상 심볼 set (대문자). 비어 있으면 *전역 공지*."""
    raw = notice.get("symbols") or ()
    if isinstance(raw, str):
        raw = (raw,)
    return {str(s).strip().upper() for s in raw if str(s).strip()}


def _notice_targets_match(
    notice: Mapping[str, Any],
    symbol: str,
    domestic_exchange: str,
    foreign_exchange: str,
) -> tuple[bool, str | None]:
    """notice 가 (symbol, domestic|foreign) 와 매칭되는지.

    반환:
      (matches, matched_exchange_or_None)
    매칭 규칙:
      - notice.exchange 가 domestic/foreign 어디에도 일치하지 않으면 미매칭.
      - notice.symbols 가 비어 있으면 *전역* 공지 — 거래소만 일치하면 매칭.
      - notice.symbols 에 symbol 이 있으면 매칭.
    """
    notice_exchange = (notice.get("exchange") or "").strip().lower()
    domestic = (domestic_exchange or "").strip().lower()
    foreign = (foreign_exchange or "").strip().lower()
    matched_exchange: str | None = None
    if notice_exchange == domestic:
        matched_exchange = domestic
    elif notice_exchange == foreign:
        matched_exchange = foreign
    elif notice_exchange == "":
        matched_exchange = None  # 거래소 미명시 — 둘 다 적용 가능
    else:
        return False, None
    symbols = _notice_targets(notice)
    sym_u = (symbol or "").strip().upper()
    if not symbols:
        return True, matched_exchange  # 전역 공지
    if sym_u in symbols:
        return True, matched_exchange
    return False, matched_exchange


_NOTICE_TYPE_TO_CODE: Mapping[str, str] = {
    "DEPOSIT_WITHDRAWAL_SUSPENSION": GuardCode.DEPOSIT_WITHDRAWAL_SUSPENDED,
    "DELISTING": GuardCode.DELISTING,
    "CAUTION": GuardCode.CAUTION_NOTICE,
    "TRADING_SUSPENSION": GuardCode.TRADING_SUSPENSION,
}


# ── 개별 가드 ────────────────────────────────────────────────────


def check_notice_risk(
    input: KimpGuardInput,
    config: KimpGuardConfig,
) -> list[KimpGuardReason]:
    """공지 기반 진입 차단.

    - notice_context_available=False 면 require_notice_context 설정에 따라 보수적
      차단/경고.
    - notice 유형: deposit/withdrawal suspension / delisting / trading suspension
      → CRITICAL (차단).
    - caution / 유의종목 → HIGH (차단).
    - severity HIGH/CRITICAL 의 기타 공지 → HIGH (차단).
    - domestic 또는 foreign leg 어디든 매칭되면 차단.
    """
    out: list[KimpGuardReason] = []
    if not input.notice_context_available:
        sev = GuardSeverity.HIGH if config.require_notice_context else GuardSeverity.WARNING
        out.append(KimpGuardReason(
            code=GuardCode.NOTICE_CONTEXT_MISSING,
            severity=sev,
            source=GuardSource.NOTICE,
            message="notice_context 미수신 — 보수적 차단/검토",
            symbol=input.symbol,
            evidence={"require_notice_context": config.require_notice_context},
        ))
        return out
    for notice in input.notices:
        matched, mex = _notice_targets_match(
            notice, input.symbol,
            input.domestic_exchange, input.foreign_exchange,
        )
        if not matched:
            continue
        n_type = str(notice.get("notice_type", "")).upper()
        n_sev = str(notice.get("severity", "")).upper()
        n_title = str(notice.get("title", ""))
        # 유형 우선 매핑
        mapped_code = _NOTICE_TYPE_TO_CODE.get(n_type)
        if mapped_code is not None:
            # 유형별로 심각도 분리: DELISTING / DEPOSIT_WITHDRAWAL_SUSPENSION
            # / TRADING_SUSPENSION 은 CRITICAL, CAUTION 은 HIGH.
            severity = (
                GuardSeverity.CRITICAL
                if n_type != "CAUTION"
                else GuardSeverity.HIGH
            )
            out.append(KimpGuardReason(
                code=mapped_code,
                severity=severity,
                source=GuardSource.NOTICE,
                message=f"공지 차단 [{n_type}] {n_title} ({mex or 'unknown'})",
                exchange=mex,
                symbol=input.symbol,
                evidence={
                    "notice_type": n_type,
                    "title": n_title,
                    "exchange": mex,
                },
            ))
            continue
        # 미매핑 유형이라도 심각도 HIGH/CRITICAL 이면 차단
        if _SEVERITY_RANK.get(n_sev, 0) >= _SEVERITY_RANK[GuardSeverity.HIGH]:
            out.append(KimpGuardReason(
                code=GuardCode.HIGH_SEVERITY_NOTICE,
                severity=n_sev,
                source=GuardSource.NOTICE,
                message=f"고위험 공지 [{n_type}] {n_title} ({mex or 'unknown'})",
                exchange=mex,
                symbol=input.symbol,
                evidence={
                    "notice_type": n_type,
                    "severity": n_sev,
                    "title": n_title,
                },
            ))
    return out


def check_fx_risk(
    input: KimpGuardInput,
    config: KimpGuardConfig,
) -> list[KimpGuardReason]:
    """FX 환율 안전성 검사.

    - fx_rate_krw 없거나 ≤ 0 → CRITICAL.
    - fx_source 미설정 → WARNING.
    - fx_timestamp stale → HIGH.
    - KimpResult.fx_anomaly=True → HIGH (sanity range 이탈 또는 reference deviation).
    """
    out: list[KimpGuardReason] = []
    fx = _to_decimal(input.fx_rate_krw)
    if fx is None or fx <= Decimal("0"):
        out.append(KimpGuardReason(
            code=GuardCode.FX_INVALID,
            severity=GuardSeverity.CRITICAL,
            source=GuardSource.FX,
            message=f"FX 환율 비정상: {fx}",
            symbol=input.symbol,
            evidence={"fx_rate_krw": str(fx) if fx is not None else None},
        ))
        # 비정상 fx 의 경우 stale 등 후속 검사 의미 없음 → 조기 반환
        return out
    if not input.fx_source:
        out.append(KimpGuardReason(
            code=GuardCode.FX_SOURCE_MISSING,
            severity=GuardSeverity.WARNING,
            source=GuardSource.FX,
            message="fx_source 미설정 — 데이터 출처 불명",
            symbol=input.symbol,
        ))
    now = _normalize_dt(input.now) or datetime.now(timezone.utc)
    age = _age_seconds(input.fx_timestamp, now)
    if age is None:
        out.append(KimpGuardReason(
            code=GuardCode.FX_STALE,
            severity=GuardSeverity.HIGH,
            source=GuardSource.FX,
            message="fx_timestamp 없음 — stale 로 간주",
            symbol=input.symbol,
        ))
    elif age > config.max_fx_age_seconds:
        out.append(KimpGuardReason(
            code=GuardCode.FX_STALE,
            severity=GuardSeverity.HIGH,
            source=GuardSource.FX,
            message=(
                f"FX 데이터 stale: age={age:.2f}s "
                f"> max={config.max_fx_age_seconds}s"
            ),
            symbol=input.symbol,
            evidence={
                "age_seconds": age,
                "max_age_seconds": config.max_fx_age_seconds,
            },
        ))
    if input.kimp_result is not None and input.kimp_result.fx_anomaly:
        out.append(KimpGuardReason(
            code=GuardCode.FX_ANOMALY,
            severity=GuardSeverity.HIGH,
            source=GuardSource.FX,
            message=(
                f"KimpResult.fx_anomaly=True — "
                f"{input.kimp_result.fx_anomaly_reason or 'unspecified'}"
            ),
            symbol=input.symbol,
            evidence={
                "fx_anomaly_reason": input.kimp_result.fx_anomaly_reason,
                "fx_deviation_bps": (
                    None if input.kimp_result.fx_deviation_bps is None
                    else str(input.kimp_result.fx_deviation_bps)
                ),
            },
        ))
    return out


def check_liquidity_risk(
    input: KimpGuardInput,
    config: KimpGuardConfig,
) -> list[KimpGuardReason]:
    """호가창 유동성 검사.

    - 호가 모두 미수신 + require_orderbook_context=True → HIGH.
    - bid ≤ 0 또는 ask ≤ 0 → CRITICAL.
    - bid_size / ask_size 가 min_bid_size / min_ask_size 미만 → HIGH.
    - spread_bps > max_spread_bps → HIGH.
    - orderbook_timestamp stale → HIGH.
    """
    out: list[KimpGuardReason] = []
    legs = (
        ("domestic", input.domestic_exchange, input.domestic_bid,
         input.domestic_ask, input.domestic_bid_size, input.domestic_ask_size),
        ("foreign", input.foreign_exchange, input.foreign_bid,
         input.foreign_ask, input.foreign_bid_size, input.foreign_ask_size),
    )
    has_any_book = any(
        any(v is not None for v in (bid, ask, bid_sz, ask_sz))
        for _, _, bid, ask, bid_sz, ask_sz in legs
    )
    if not has_any_book:
        if config.require_orderbook_context:
            out.append(KimpGuardReason(
                code=GuardCode.ORDERBOOK_MISSING,
                severity=GuardSeverity.HIGH,
                source=GuardSource.LIQUIDITY,
                message="호가창 context 미수신 — 강제 검토 필요",
                symbol=input.symbol,
            ))
        return out
    for leg_name, exchange, bid_raw, ask_raw, bid_sz_raw, ask_sz_raw in legs:
        bid = _to_decimal(bid_raw)
        ask = _to_decimal(ask_raw)
        bid_sz = _to_decimal(bid_sz_raw)
        ask_sz = _to_decimal(ask_sz_raw)
        if bid is None and ask is None and bid_sz is None and ask_sz is None:
            continue
        # 음수/0 가격 — 비정상
        if bid is not None and bid <= Decimal("0"):
            out.append(KimpGuardReason(
                code=GuardCode.ORDERBOOK_INVALID,
                severity=GuardSeverity.CRITICAL,
                source=GuardSource.LIQUIDITY,
                message=f"{leg_name}({exchange}) bid={bid} 비정상",
                exchange=exchange,
                symbol=input.symbol,
                evidence={"leg": leg_name, "bid": str(bid)},
            ))
        if ask is not None and ask <= Decimal("0"):
            out.append(KimpGuardReason(
                code=GuardCode.ORDERBOOK_INVALID,
                severity=GuardSeverity.CRITICAL,
                source=GuardSource.LIQUIDITY,
                message=f"{leg_name}({exchange}) ask={ask} 비정상",
                exchange=exchange,
                symbol=input.symbol,
                evidence={"leg": leg_name, "ask": str(ask)},
            ))
        # 사이즈 부족
        if config.min_bid_size > Decimal("0") and bid_sz is not None:
            if bid_sz < config.min_bid_size:
                out.append(KimpGuardReason(
                    code=GuardCode.LIQUIDITY_THIN,
                    severity=GuardSeverity.HIGH,
                    source=GuardSource.LIQUIDITY,
                    message=(
                        f"{leg_name}({exchange}) bid_size={bid_sz} "
                        f"< min={config.min_bid_size}"
                    ),
                    exchange=exchange,
                    symbol=input.symbol,
                    evidence={
                        "leg": leg_name, "bid_size": str(bid_sz),
                        "min_bid_size": str(config.min_bid_size),
                    },
                ))
        if config.min_ask_size > Decimal("0") and ask_sz is not None:
            if ask_sz < config.min_ask_size:
                out.append(KimpGuardReason(
                    code=GuardCode.LIQUIDITY_THIN,
                    severity=GuardSeverity.HIGH,
                    source=GuardSource.LIQUIDITY,
                    message=(
                        f"{leg_name}({exchange}) ask_size={ask_sz} "
                        f"< min={config.min_ask_size}"
                    ),
                    exchange=exchange,
                    symbol=input.symbol,
                    evidence={
                        "leg": leg_name, "ask_size": str(ask_sz),
                        "min_ask_size": str(config.min_ask_size),
                    },
                ))
        # 스프레드
        if bid is not None and ask is not None and ask > Decimal("0"):
            mid = (bid + ask) / Decimal("2")
            if mid > Decimal("0"):
                spread = ask - bid
                spread_bps = (spread / mid) * Decimal("10000")
                if spread_bps > config.max_spread_bps:
                    out.append(KimpGuardReason(
                        code=GuardCode.SPREAD_WIDE,
                        severity=GuardSeverity.HIGH,
                        source=GuardSource.LIQUIDITY,
                        message=(
                            f"{leg_name}({exchange}) spread={spread_bps:.2f} bps "
                            f"> max={config.max_spread_bps} bps"
                        ),
                        exchange=exchange,
                        symbol=input.symbol,
                        evidence={
                            "leg": leg_name,
                            "spread_bps": str(spread_bps),
                            "max_spread_bps": str(config.max_spread_bps),
                        },
                    ))
    now = _normalize_dt(input.now) or datetime.now(timezone.utc)
    age = _age_seconds(input.orderbook_timestamp, now)
    if age is not None and age > config.max_orderbook_age_seconds:
        out.append(KimpGuardReason(
            code=GuardCode.ORDERBOOK_STALE,
            severity=GuardSeverity.HIGH,
            source=GuardSource.LIQUIDITY,
            message=(
                f"호가창 stale: age={age:.2f}s > "
                f"max={config.max_orderbook_age_seconds}s"
            ),
            symbol=input.symbol,
            evidence={
                "age_seconds": age,
                "max_age_seconds": config.max_orderbook_age_seconds,
            },
        ))
    return out


def check_bull_market_short_risk(
    input: KimpGuardInput,
    config: KimpGuardConfig,
) -> list[KimpGuardReason]:
    """강세장 + 역김프 short leg 위험 차단.

    - block_reverse_kimp_short_in_bull_market=False → 항상 통과.
    - intended_kimp_state 가 REVERSE_KIMP_CANDIDATE 가 아니면 통과.
    - short_leg_implied=False 이면 통과 (해외 leg 가 long 이라면 강세장 위험 없음).
    - market_regime 가 bull_market_regimes 에 포함 → HIGH 차단.
    - theme_tags 중 하나가 bull_market_themes 에 포함 → HIGH 차단.
    """
    if not config.block_reverse_kimp_short_in_bull_market:
        return []
    if input.intended_kimp_state != KimpCandidateState.REVERSE_KIMP_CANDIDATE:
        return []
    if not input.short_leg_implied:
        return []
    out: list[KimpGuardReason] = []
    regime = (input.market_regime or "").strip().upper()
    if regime and regime in config.bull_market_regimes:
        out.append(KimpGuardReason(
            code=GuardCode.BULL_MARKET_SHORT_BLOCKED,
            severity=GuardSeverity.HIGH,
            source=GuardSource.BULL_MARKET,
            message=(
                f"강세장 regime={regime} 에서 역김프 short leg 후보 차단"
            ),
            symbol=input.symbol,
            evidence={
                "market_regime": regime,
                "blocking_regimes": list(config.bull_market_regimes),
            },
        ))
        return out
    matching_themes = [
        t for t in input.theme_tags
        if t.strip().upper() in config.bull_market_themes
    ]
    if matching_themes:
        out.append(KimpGuardReason(
            code=GuardCode.BULL_MARKET_SHORT_BLOCKED,
            severity=GuardSeverity.HIGH,
            source=GuardSource.BULL_MARKET,
            message=(
                f"강세 테마 {matching_themes} 에서 역김프 short leg 후보 차단"
            ),
            symbol=input.symbol,
            evidence={
                "matching_themes": matching_themes,
                "blocking_themes": list(config.bull_market_themes),
            },
        ))
    return out


def check_funding_risk(
    input: KimpGuardInput,
    config: KimpGuardConfig,
) -> list[KimpGuardReason]:
    """펀딩비 안전성.

    - funding context 누락 + require_funding_context=True → WARNING/HIGH.
    - |funding_rate_pct| × 100 > funding_risk_threshold_bps → HIGH.
    - funding_timestamp stale → WARNING.
    - position_side=short 이고 funding_rate < 0 (short 가 cost) → WARNING.
    - position_side=long 이고 funding_rate > 0 → WARNING.
    """
    out: list[KimpGuardReason] = []
    rate = _to_decimal(input.funding_rate_pct)
    if rate is None:
        if config.require_funding_context:
            out.append(KimpGuardReason(
                code=GuardCode.FUNDING_CONTEXT_MISSING,
                severity=GuardSeverity.HIGH,
                source=GuardSource.FUNDING,
                message="펀딩 context 미수신 — 차단",
                symbol=input.symbol,
            ))
        # require_funding_context=False 면 침묵 (펀딩 데이터 옵션)
        return out
    rate_bps = abs(rate) * Decimal("100")  # pct → bps
    if rate_bps > config.funding_risk_threshold_bps:
        out.append(KimpGuardReason(
            code=GuardCode.FUNDING_RISK_HIGH,
            severity=GuardSeverity.HIGH,
            source=GuardSource.FUNDING,
            message=(
                f"펀딩비 이상치: |rate|={rate_bps:.2f} bps > "
                f"max={config.funding_risk_threshold_bps} bps"
            ),
            symbol=input.symbol,
            evidence={
                "rate_pct": str(rate),
                "rate_bps": str(rate_bps),
                "max_bps": str(config.funding_risk_threshold_bps),
            },
        ))
    now = _normalize_dt(input.now) or datetime.now(timezone.utc)
    age = _age_seconds(input.funding_timestamp, now)
    if age is not None and age > config.max_funding_age_seconds:
        out.append(KimpGuardReason(
            code=GuardCode.FUNDING_STALE,
            severity=GuardSeverity.WARNING,
            source=GuardSource.FUNDING,
            message=(
                f"펀딩 데이터 stale: age={age:.2f}s "
                f"> max={config.max_funding_age_seconds}s"
            ),
            symbol=input.symbol,
            evidence={
                "age_seconds": age,
                "max_age_seconds": config.max_funding_age_seconds,
            },
        ))
    side = (input.funding_position_side or "").strip().lower()
    if side in ("short", "long"):
        adverse = (
            (side == "short" and rate < Decimal("0"))
            or (side == "long" and rate > Decimal("0"))
        )
        if adverse:
            out.append(KimpGuardReason(
                code=GuardCode.FUNDING_DIRECTION_ADVERSE,
                severity=GuardSeverity.WARNING,
                source=GuardSource.FUNDING,
                message=(
                    f"펀딩 방향 불리: side={side}, rate={rate} % — 비용 부담"
                ),
                symbol=input.symbol,
                evidence={
                    "side": side, "rate_pct": str(rate),
                },
            ))
    return out


def check_freshness_risk(
    input: KimpGuardInput,
    config: KimpGuardConfig,
) -> list[KimpGuardReason]:
    """가격 timestamp 신선도.

    - domestic_price_timestamp 또는 foreign_price_timestamp 가 stale → HIGH.
    - 둘 다 None → WARNING (PRICE_TIMESTAMP_MISSING).
    """
    out: list[KimpGuardReason] = []
    now = _normalize_dt(input.now) or datetime.now(timezone.utc)
    pairs = (
        ("domestic", input.domestic_exchange,
         input.domestic_price_timestamp, GuardCode.DOMESTIC_PRICE_STALE),
        ("foreign", input.foreign_exchange,
         input.foreign_price_timestamp, GuardCode.FOREIGN_PRICE_STALE),
    )
    any_present = False
    for leg_name, exchange, ts, code in pairs:
        if ts is None:
            continue
        any_present = True
        age = _age_seconds(ts, now)
        if age is None:
            continue
        if age > config.max_price_age_seconds:
            out.append(KimpGuardReason(
                code=code,
                severity=GuardSeverity.HIGH,
                source=GuardSource.FRESHNESS,
                message=(
                    f"{leg_name}({exchange}) 가격 stale: "
                    f"age={age:.2f}s > max={config.max_price_age_seconds}s"
                ),
                exchange=exchange,
                symbol=input.symbol,
                evidence={
                    "leg": leg_name,
                    "age_seconds": age,
                    "max_age_seconds": config.max_price_age_seconds,
                },
            ))
    if not any_present:
        out.append(KimpGuardReason(
            code=GuardCode.PRICE_TIMESTAMP_MISSING,
            severity=GuardSeverity.WARNING,
            source=GuardSource.FRESHNESS,
            message="domestic / foreign 가격 timestamp 모두 미수신",
            symbol=input.symbol,
        ))
    return out


def check_data_quality_risk(
    input: KimpGuardInput,
    config: KimpGuardConfig,
) -> list[KimpGuardReason]:
    """데이터 품질 등급.

    - grade=EXCLUDE → CRITICAL (block_on_data_quality_exclude=True 일 때).
    - grade=WARNING → WARNING 또는 HIGH (block_on_data_quality_warning).
    """
    out: list[KimpGuardReason] = []
    grade = (input.data_quality_grade or "").strip().upper()
    if grade == "EXCLUDE":
        sev = (
            GuardSeverity.CRITICAL if config.block_on_data_quality_exclude
            else GuardSeverity.WARNING
        )
        out.append(KimpGuardReason(
            code=GuardCode.DATA_QUALITY_EXCLUDE,
            severity=sev,
            source=GuardSource.DATA_QUALITY,
            message="데이터 품질 EXCLUDE — 진입 금지",
            symbol=input.symbol,
        ))
    elif grade == "WARNING":
        sev = (
            GuardSeverity.HIGH if config.block_on_data_quality_warning
            else GuardSeverity.WARNING
        )
        out.append(KimpGuardReason(
            code=GuardCode.DATA_QUALITY_WARNING,
            severity=sev,
            source=GuardSource.DATA_QUALITY,
            message="데이터 품질 WARNING — 검토 필요",
            symbol=input.symbol,
        ))
    return out


def check_missing_critical_context(
    input: KimpGuardInput,
    config: KimpGuardConfig,
) -> list[KimpGuardReason]:
    """필수 context 누락 일괄 체크 (각 가드가 분산 처리 못한 missing 보충)."""
    out: list[KimpGuardReason] = []
    missing: list[str] = []
    if input.fx_rate_krw is None:
        missing.append("fx_rate_krw")
    if input.kimp_result is None:
        missing.append("kimp_result")
    if input.intended_kimp_state == KimpCandidateState.UNKNOWN:
        missing.append("intended_kimp_state")
    if missing:
        sev = (
            GuardSeverity.HIGH
            if "fx_rate_krw" in missing or "kimp_result" in missing
            else GuardSeverity.WARNING
        )
        out.append(KimpGuardReason(
            code=GuardCode.MISSING_CRITICAL_CONTEXT,
            severity=sev,
            source=GuardSource.MISSING_CONTEXT,
            message=f"필수 context 누락: {missing}",
            symbol=input.symbol,
            evidence={"missing": missing},
        ))
    return out


# ── 합성 평가 ────────────────────────────────────────────────────


def _collect_reasons(
    input: KimpGuardInput,
    config: KimpGuardConfig,
) -> tuple[KimpGuardReason, ...]:
    """모든 가드 실행 후 reason 리스트 합산."""
    all_reasons: list[KimpGuardReason] = []
    all_reasons.extend(check_notice_risk(input, config))
    all_reasons.extend(check_fx_risk(input, config))
    all_reasons.extend(check_liquidity_risk(input, config))
    all_reasons.extend(check_bull_market_short_risk(input, config))
    all_reasons.extend(check_funding_risk(input, config))
    all_reasons.extend(check_freshness_risk(input, config))
    all_reasons.extend(check_data_quality_risk(input, config))
    all_reasons.extend(check_missing_critical_context(input, config))
    return tuple(all_reasons)


def evaluate_kimp_guards(
    input: KimpGuardInput,
    *,
    config: KimpGuardConfig | None = None,
) -> KimpGuardDecision:
    """모든 가드 실행 후 ``KimpGuardDecision`` 합성.

    정책:
      - blocking severity (HIGH/CRITICAL) reason 이 하나라도 있으면 allowed=False
        + recommended_action=BLOCK_CANDIDATE.
      - 그 외 reason (INFO/WARNING) 만 있으면 allowed=True + required_review=True
        + recommended_action=REVIEW_REQUIRED.
      - reason 이 없으면 allowed=True + required_review=False
        + recommended_action=ALLOW_CANDIDATE.

    ``direct_order_allowed=False`` / ``used_for_order=False`` 영구.
    """
    cfg = config or KimpGuardConfig()
    reasons = _collect_reasons(input, cfg)
    blocked_by = tuple(r.code for r in reasons if r.is_blocking)
    review_codes = tuple(r.code for r in reasons if not r.is_blocking)
    if blocked_by:
        allowed = False
        required_review = True
        action = RecommendedAction.BLOCK_CANDIDATE
    elif review_codes:
        allowed = True
        required_review = True
        action = RecommendedAction.REVIEW_REQUIRED
    else:
        allowed = True
        required_review = False
        action = RecommendedAction.ALLOW_CANDIDATE
    return KimpGuardDecision(
        input=input,
        allowed=allowed,
        required_review=required_review,
        recommended_action=action,
        reasons=reasons,
        blocked_by=blocked_by,
        review_codes=review_codes,
        computed_at=datetime.now(timezone.utc),
    )


# ── KimpAgent context hook ───────────────────────────────────────


def build_kimp_guard_context(decision: KimpGuardDecision) -> dict:
    """KimpAgent / RiskManager 가 참조할 *평탄 dict* context.

    - ``direct_order_allowed=False`` / ``used_for_order=False`` 명시.
    - reasons 는 (code, severity, source, message, exchange, symbol) 평탄 list.
    - evidence 의 Decimal/datetime 은 str 직렬화 (JSON 호환).
    """
    return {
        "kind": "kimp_guard_context",
        "direct_order_allowed": False,
        "used_for_order": False,
        "symbol": decision.input.symbol,
        "intended_kimp_state": decision.input.intended_kimp_state,
        "domestic_exchange": decision.input.domestic_exchange,
        "foreign_exchange": decision.input.foreign_exchange,
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
                "exchange": r.exchange,
                "symbol": r.symbol,
            }
            for r in decision.reasons
        ],
        "summary": decision.summary(),
        "computed_at": decision.computed_at.isoformat(),
    }


__all__ = (
    "GuardSeverity",
    "GuardSource",
    "GuardCode",
    "RecommendedAction",
    "KimpCandidateState",
    "KimpGuardConfig",
    "KimpGuardInput",
    "KimpGuardReason",
    "KimpGuardDecision",
    "check_notice_risk",
    "check_fx_risk",
    "check_liquidity_risk",
    "check_bull_market_short_risk",
    "check_funding_risk",
    "check_freshness_risk",
    "check_data_quality_risk",
    "check_missing_critical_context",
    "evaluate_kimp_guards",
    "build_kimp_guard_context",
)
