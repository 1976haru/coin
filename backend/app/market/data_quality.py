"""Data Quality — 체크리스트 #17 (historical candle layer).

본 모듈은 **저장된 과거 candle 의 신뢰도** 를 검증한다. (#16 의 Data Freshness 와
역할이 다르다 — Freshness 는 "지금 데이터가 오래됐나", 본 모듈은 "보관된 과거
데이터가 신뢰 가능한가" 를 다룬다.)

검사 6종 (symbol/exchange/timeframe/day 단위):
  1. 누락 candle (missing_rate)
  2. 중복 candle (open_time 중복)
  3. OHLC 논리 오류 (high<low, open>high, …, NaN, ≤0)
  4. volume 이상 (음수, 과도한 0, rolling median spike)
  5. 가격 이상 (직전 close 대비 50%/90% 변동, ≤0)
  6. 장외 데이터 (unknown exchange, 미래 ts, Watchlist 밖, grid 불일치)

산출:
  - DataQualityDayReport (frozen dataclass)
  - grade ∈ {GOOD, WARNING, EXCLUDE}
  - reasons : 차단/경고 사유

부수기능:
  - BacktestPromotionGuard.evaluate(reports)
      EXCLUDE 가 하나라도 있거나 GOOD 비율 미달 시 승격 불가 reason 반환.
  - load_candles_for_day(session, ...) : ORM CoinCandle → CandleRecord 변환

설계 원칙 (CLAUDE.md §2.3 / §2.5):
  - 본 모듈은 *판단만* 한다. 실제 백테스트 승격을 수행하지 않는다.
  - 외부 거래소 API 호출 없음.
  - 전체 시장 자동 스캔 없음 — 호출자는 (symbol, exchange, timeframe, date) 를
    구체적으로 지정해야 한다.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from datetime import date as _date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Iterable, Sequence


# ── Grade ────────────────────────────────────────────────────────

class DataQualityGrade(str, Enum):
    """일별 데이터 품질 등급."""
    GOOD    = "GOOD"
    WARNING = "WARNING"
    EXCLUDE = "EXCLUDE"


# ── 기본 임계값 ────────────────────────────────────────────────────

@dataclass(frozen=True)
class DataQualityConfig:
    """data_quality 검사 임계값. 운용 환경에 맞춰 인자로 덮어쓴다."""

    # 누락
    missing_rate_good_max:    float = 0.001    # 0.1%
    missing_rate_warning_max: float = 0.01     # 1.0%

    # 중복 — 어느 하나라도 있으면 WARNING, 비율 높으면 EXCLUDE
    duplicate_rate_exclude_min: float = 0.01   # 1%

    # OHLC — 단일 위반은 WARNING, 다중이면 EXCLUDE
    ohlc_invalid_count_exclude_min: int = 5

    # volume — 음수는 EXCLUDE, zero 비율 높음/spike 는 WARNING
    zero_volume_rate_warning_min: float = 0.30   # 30% 이상 0 → WARNING
    volume_spike_multiplier:      float = 100.0  # rolling median 대비 100배 spike → WARNING

    # 가격 outlier — 1m 기준 단일 수익률 abs
    price_return_warning_pct: float = 50.0  # ±50% 초과 → WARNING
    price_return_exclude_pct: float = 90.0  # ±90% 초과 → EXCLUDE

    # 장외
    allowed_exchanges: frozenset[str] = field(
        default_factory=lambda: frozenset({"upbit", "binance", "okx", "mock", "paper"})
    )

    # rolling window 길이 (volume spike 계산용)
    volume_rolling_window: int = 20


# ── Timeframe ────────────────────────────────────────────────────

_TIMEFRAME_SECONDS = {
    "1m":  60,
    "5m":  300,
    "15m": 900,
    "1h":  3600,
    "4h":  14400,
    "1d":  86400,
}


def timeframe_seconds(tf: str) -> int:
    if tf not in _TIMEFRAME_SECONDS:
        raise ValueError(f"unsupported timeframe: {tf!r}")
    return _TIMEFRAME_SECONDS[tf]


def expected_candle_count(timeframe: str, day: _date) -> int:
    """24h day 의 기대 candle 수."""
    sec = timeframe_seconds(timeframe)
    return max(1, 86400 // sec)


# ── 입력 모델 (ORM 와 분리) ──────────────────────────────────────

@dataclass(frozen=True)
class CandleRecord:
    """data_quality 검사 입력 — ORM CoinCandle 와 별개의 가벼운 view object.

    테스트는 ORM 없이 직접 생성 가능. CLI / API 는 load_candles_for_day 로
    DB 에서 변환해 받는다.
    """
    exchange:  str
    symbol:    str
    timeframe: str
    ts:        datetime
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    float


# ── 결과 모델 ────────────────────────────────────────────────────

@dataclass(frozen=True)
class DataQualityDayReport:
    symbol:     str
    exchange:   str
    timeframe:  str
    date:       _date

    expected_count: int
    actual_count:   int
    missing_count:  int
    missing_rate:   float

    duplicate_count:        int
    invalid_ohlc_count:     int
    volume_anomaly_count:   int
    price_outlier_count:    int
    off_universe_count:     int
    future_timestamp_count: int

    grade:   DataQualityGrade
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "symbol":    self.symbol,
            "exchange":  self.exchange,
            "timeframe": self.timeframe,
            "date":      self.date.isoformat(),
            "expected_count":         self.expected_count,
            "actual_count":           self.actual_count,
            "missing_count":          self.missing_count,
            "missing_rate":           self.missing_rate,
            "duplicate_count":        self.duplicate_count,
            "invalid_ohlc_count":     self.invalid_ohlc_count,
            "volume_anomaly_count":   self.volume_anomaly_count,
            "price_outlier_count":    self.price_outlier_count,
            "off_universe_count":     self.off_universe_count,
            "future_timestamp_count": self.future_timestamp_count,
            "grade":   self.grade.value,
            "reasons": list(self.reasons),
        }


# ── 6가지 검사 — 순수 함수 ────────────────────────────────────────

def _is_nan(x) -> bool:
    try:
        return isinstance(x, float) and math.isnan(x)
    except (TypeError, ValueError):
        return False


def _normalize_ts(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _is_on_grid(ts: datetime, timeframe: str) -> bool:
    sec = timeframe_seconds(timeframe)
    epoch = int(_normalize_ts(ts).timestamp())
    return epoch % sec == 0


def check_missing(
    candles: Sequence[CandleRecord],
    timeframe: str,
    day: _date,
) -> tuple[int, float]:
    """주어진 day(24h) 에서 누락된 봉 개수와 비율.

    중복 봉 은 missing 계산에 포함하지 않는다 (유일 ts 수 기준).
    """
    expected = expected_candle_count(timeframe, day)
    unique_ts = {_normalize_ts(c.ts) for c in candles}
    actual = len(unique_ts)
    missing = max(0, expected - actual)
    rate = missing / expected if expected else 0.0
    return missing, rate


def check_duplicates(candles: Sequence[CandleRecord]) -> int:
    """동일 ts 중복 개수 (총 - 유일)."""
    seen: dict[datetime, int] = {}
    for c in candles:
        seen[_normalize_ts(c.ts)] = seen.get(_normalize_ts(c.ts), 0) + 1
    return sum(v - 1 for v in seen.values() if v > 1)


def check_ohlc_validity(candles: Sequence[CandleRecord]) -> int:
    """OHLC 논리 위반 개수."""
    bad = 0
    for c in candles:
        for v in (c.open, c.high, c.low, c.close):
            if v is None or _is_nan(v) or v <= 0:
                bad += 1
                break
        else:
            if c.high < c.low:
                bad += 1
                continue
            if c.open > c.high or c.open < c.low:
                bad += 1
                continue
            if c.close > c.high or c.close < c.low:
                bad += 1
                continue
    return bad


def check_volume_anomalies(
    candles: Sequence[CandleRecord],
    config: DataQualityConfig | None = None,
) -> tuple[int, int, int]:
    """volume 이상 — (negative_count, zero_count, spike_count) 반환.

    - negative : volume < 0 또는 NaN
    - zero     : volume == 0
    - spike    : rolling median 대비 multiplier 배 초과
    """
    cfg = config or DataQualityConfig()
    negative = 0
    zero = 0
    spike = 0
    window = max(2, cfg.volume_rolling_window)
    vol_list: list[float] = []
    for c in candles:
        v = c.volume
        if v is None or _is_nan(v) or v < 0:
            negative += 1
            vol_list.append(0.0)
            continue
        if v == 0:
            zero += 1
        # rolling median (이전 window 개의 positive volume)
        prev = [x for x in vol_list[-window:] if x > 0]
        if prev:
            prev_sorted = sorted(prev)
            med = prev_sorted[len(prev_sorted) // 2]
            if med > 0 and v > med * cfg.volume_spike_multiplier:
                spike += 1
        vol_list.append(v)
    return negative, zero, spike


def check_price_outliers(
    candles: Sequence[CandleRecord],
    config: DataQualityConfig | None = None,
) -> tuple[int, int]:
    """가격 outlier — (warning_count, exclude_count) 반환.

    직전 close 대비 abs return.
    """
    cfg = config or DataQualityConfig()
    sorted_c = sorted(candles, key=lambda c: _normalize_ts(c.ts))
    warn = 0
    excl = 0
    for prev, curr in zip(sorted_c, sorted_c[1:]):
        if prev.close <= 0 or _is_nan(prev.close):
            continue
        if curr.close is None or _is_nan(curr.close) or curr.close <= 0:
            # 가격 ≤ 0 / NaN 자체는 ohlc invalid 에서 잡힘 — 여기선 건너뜀.
            continue
        ret_pct = abs((curr.close - prev.close) / prev.close) * 100.0
        if ret_pct > cfg.price_return_exclude_pct:
            excl += 1
        elif ret_pct > cfg.price_return_warning_pct:
            warn += 1
    return warn, excl


def check_off_universe(
    candles: Sequence[CandleRecord],
    *,
    timeframe: str,
    day: _date,
    config: DataQualityConfig | None = None,
    watchlist_symbols: Iterable[tuple[str, str]] | None = None,
    now: datetime | None = None,
) -> tuple[int, int, int, int]:
    """장외 데이터 — (unknown_exchange, future_ts, off_watchlist, grid_mismatch) 반환.

    - unknown_exchange : config.allowed_exchanges 외
    - future_ts        : ts > now
    - off_watchlist    : watchlist_symbols 가 주어졌고 그 외 (symbol, exchange) 인 경우
    - grid_mismatch    : timeframe grid 에 정렬 안 된 ts
    """
    cfg = config or DataQualityConfig()
    now_ = now or datetime.now(timezone.utc)
    wl_set: set[tuple[str, str]] | None = None
    if watchlist_symbols is not None:
        wl_set = {(s.upper(), e.lower()) for (s, e) in watchlist_symbols}

    unknown_ex = 0
    future_ts = 0
    off_wl = 0
    grid_miss = 0
    for c in candles:
        if c.exchange not in cfg.allowed_exchanges:
            unknown_ex += 1
        if _normalize_ts(c.ts) > now_:
            future_ts += 1
        if wl_set is not None and (c.symbol.upper(), c.exchange.lower()) not in wl_set:
            off_wl += 1
        try:
            if not _is_on_grid(c.ts, timeframe):
                grid_miss += 1
        except ValueError:
            grid_miss += 1
    return unknown_ex, future_ts, off_wl, grid_miss


# ── Day 단위 종합 ────────────────────────────────────────────────

def _grade(
    *,
    missing_rate: float,
    duplicate_count: int,
    duplicate_rate: float,
    invalid_ohlc_count: int,
    volume_negative: int,
    volume_zero_rate: float,
    volume_spike: int,
    price_outlier_warn: int,
    price_outlier_excl: int,
    off_universe_count: int,
    future_ts_count: int,
    config: DataQualityConfig,
) -> tuple[DataQualityGrade, list[str]]:
    reasons: list[str] = []
    bad = DataQualityGrade.GOOD

    def bump(level: DataQualityGrade, reason: str):
        nonlocal bad
        order = {DataQualityGrade.GOOD: 0, DataQualityGrade.WARNING: 1,
                 DataQualityGrade.EXCLUDE: 2}
        if order[level] > order[bad]:
            bad = level
        reasons.append(reason)

    # missing
    if missing_rate > config.missing_rate_warning_max:
        bump(DataQualityGrade.EXCLUDE,
             f"missing_rate {missing_rate:.4f} > {config.missing_rate_warning_max}")
    elif missing_rate > config.missing_rate_good_max:
        bump(DataQualityGrade.WARNING,
             f"missing_rate {missing_rate:.4f} > {config.missing_rate_good_max}")

    # duplicate
    if duplicate_count > 0:
        if duplicate_rate > config.duplicate_rate_exclude_min:
            bump(DataQualityGrade.EXCLUDE,
                 f"duplicate_rate {duplicate_rate:.4f} > {config.duplicate_rate_exclude_min}")
        else:
            bump(DataQualityGrade.WARNING, f"duplicate_count={duplicate_count}")

    # ohlc
    if invalid_ohlc_count > 0:
        if invalid_ohlc_count >= config.ohlc_invalid_count_exclude_min:
            bump(DataQualityGrade.EXCLUDE,
                 f"invalid_ohlc_count={invalid_ohlc_count} ≥ {config.ohlc_invalid_count_exclude_min}")
        else:
            bump(DataQualityGrade.WARNING, f"invalid_ohlc_count={invalid_ohlc_count}")

    # volume
    if volume_negative > 0:
        bump(DataQualityGrade.EXCLUDE, f"negative_volume_count={volume_negative}")
    if volume_zero_rate >= config.zero_volume_rate_warning_min:
        bump(DataQualityGrade.WARNING,
             f"zero_volume_rate {volume_zero_rate:.3f} ≥ {config.zero_volume_rate_warning_min}")
    if volume_spike > 0:
        bump(DataQualityGrade.WARNING, f"volume_spike_count={volume_spike}")

    # price outlier
    if price_outlier_excl > 0:
        bump(DataQualityGrade.EXCLUDE, f"price_outlier_exclude_count={price_outlier_excl}")
    elif price_outlier_warn > 0:
        bump(DataQualityGrade.WARNING, f"price_outlier_warn_count={price_outlier_warn}")

    # off-universe
    if future_ts_count > 0:
        bump(DataQualityGrade.EXCLUDE, f"future_timestamp_count={future_ts_count}")
    if off_universe_count > 0:
        bump(DataQualityGrade.WARNING, f"off_universe_count={off_universe_count}")

    if not reasons:
        reasons.append("clean")
    return bad, reasons


def run_day_check(
    candles: Sequence[CandleRecord],
    *,
    symbol: str,
    exchange: str,
    timeframe: str,
    day: _date,
    config: DataQualityConfig | None = None,
    watchlist_symbols: Iterable[tuple[str, str]] | None = None,
    now: datetime | None = None,
) -> DataQualityDayReport:
    """단일 (symbol, exchange, timeframe, day) 의 검사를 수행하고 DayReport 반환."""
    cfg = config or DataQualityConfig()

    missing_count, missing_rate = check_missing(candles, timeframe, day)
    duplicate_count = check_duplicates(candles)
    duplicate_rate = (duplicate_count / max(1, len(candles))) if candles else 0.0
    invalid_ohlc_count = check_ohlc_validity(candles)
    vol_neg, vol_zero, vol_spike = check_volume_anomalies(candles, cfg)
    vol_zero_rate = (vol_zero / max(1, len(candles))) if candles else 0.0
    price_warn, price_excl = check_price_outliers(candles, cfg)
    unknown_ex, future_ts, off_wl, grid_miss = check_off_universe(
        candles, timeframe=timeframe, day=day, config=cfg,
        watchlist_symbols=watchlist_symbols, now=now,
    )

    off_universe_count = unknown_ex + off_wl + grid_miss
    volume_anomaly_count = vol_neg + vol_spike  # zero 는 비율만 사용

    grade, reasons = _grade(
        missing_rate=missing_rate,
        duplicate_count=duplicate_count,
        duplicate_rate=duplicate_rate,
        invalid_ohlc_count=invalid_ohlc_count,
        volume_negative=vol_neg,
        volume_zero_rate=vol_zero_rate,
        volume_spike=vol_spike,
        price_outlier_warn=price_warn,
        price_outlier_excl=price_excl,
        off_universe_count=off_universe_count,
        future_ts_count=future_ts,
        config=cfg,
    )

    return DataQualityDayReport(
        symbol=symbol, exchange=exchange, timeframe=timeframe, date=day,
        expected_count=expected_candle_count(timeframe, day),
        actual_count=len(candles),
        missing_count=missing_count,
        missing_rate=missing_rate,
        duplicate_count=duplicate_count,
        invalid_ohlc_count=invalid_ohlc_count,
        volume_anomaly_count=volume_anomaly_count,
        price_outlier_count=price_warn + price_excl,
        off_universe_count=off_universe_count,
        future_timestamp_count=future_ts,
        grade=grade,
        reasons=tuple(reasons),
    )


# ── Backtest 승격 guard ──────────────────────────────────────────

@dataclass(frozen=True)
class PromotionEvaluation:
    allowed: bool
    reason:  str
    good_ratio:    float
    warning_ratio: float
    exclude_ratio: float

    def as_dict(self) -> dict:
        return {
            "allowed":       self.allowed,
            "reason":        self.reason,
            "good_ratio":    self.good_ratio,
            "warning_ratio": self.warning_ratio,
            "exclude_ratio": self.exclude_ratio,
        }


class BacktestPromotionGuard:
    """일별 DataQualityDayReport 시퀀스로부터 백테스트 승격 가능 여부 판정.

    *** 실제 승격은 수행하지 않는다 *** — 판단 boolean 과 reason 만 반환.

    기본 기준:
      - min_good_ratio_for_promotion       = 0.9   (90% 이상 GOOD)
      - max_warning_ratio_for_promotion    = 0.1   (≤10% WARNING)
      - max_exclude_ratio_for_promotion    = 0.0   (EXCLUDE 가 있으면 불가)
    """

    def __init__(
        self,
        *,
        min_good_ratio:     float = 0.9,
        max_warning_ratio:  float = 0.1,
        max_exclude_ratio:  float = 0.0,
    ):
        self.min_good_ratio    = float(min_good_ratio)
        self.max_warning_ratio = float(max_warning_ratio)
        self.max_exclude_ratio = float(max_exclude_ratio)

    def evaluate(self, reports: Sequence[DataQualityDayReport]) -> PromotionEvaluation:
        n = len(reports)
        if n == 0:
            return PromotionEvaluation(
                allowed=False, reason="blocked_by_no_data_quality_reports",
                good_ratio=0.0, warning_ratio=0.0, exclude_ratio=0.0,
            )
        good    = sum(1 for r in reports if r.grade == DataQualityGrade.GOOD)
        warning = sum(1 for r in reports if r.grade == DataQualityGrade.WARNING)
        exclude = sum(1 for r in reports if r.grade == DataQualityGrade.EXCLUDE)
        g_r = good / n
        w_r = warning / n
        e_r = exclude / n

        if e_r > self.max_exclude_ratio:
            return PromotionEvaluation(
                allowed=False, reason="blocked_by_excluded_data_quality_day",
                good_ratio=g_r, warning_ratio=w_r, exclude_ratio=e_r,
            )
        if g_r < self.min_good_ratio:
            return PromotionEvaluation(
                allowed=False, reason="blocked_by_low_good_data_ratio",
                good_ratio=g_r, warning_ratio=w_r, exclude_ratio=e_r,
            )
        if w_r > self.max_warning_ratio:
            return PromotionEvaluation(
                allowed=False, reason="blocked_by_high_warning_ratio",
                good_ratio=g_r, warning_ratio=w_r, exclude_ratio=e_r,
            )
        if w_r > 0:
            return PromotionEvaluation(
                allowed=True, reason="warning_data_allowed_but_limited",
                good_ratio=g_r, warning_ratio=w_r, exclude_ratio=e_r,
            )
        return PromotionEvaluation(
            allowed=True, reason="approved",
            good_ratio=g_r, warning_ratio=w_r, exclude_ratio=e_r,
        )


# ── DB loader ────────────────────────────────────────────────────

def load_candles_for_day(
    session,
    *,
    symbol: str,
    exchange: str,
    timeframe: str,
    day: _date,
) -> list[CandleRecord]:
    """coin_candle 에서 (symbol, exchange, timeframe, day) candle 을 불러와
    CandleRecord 리스트로 반환. session 은 SQLAlchemy Session.

    DB 가 없으면 빈 리스트.
    """
    from sqlalchemy import select
    from app.db.models import CoinCandle

    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end   = start + timedelta(days=1)
    stmt = (
        select(CoinCandle)
        .where(CoinCandle.exchange == exchange)
        .where(CoinCandle.symbol == symbol)
        .where(CoinCandle.interval == timeframe)
        .where(CoinCandle.ts >= start)
        .where(CoinCandle.ts < end)
        .order_by(CoinCandle.ts)
    )
    rows = session.execute(stmt).scalars().all()
    return [
        CandleRecord(
            exchange=r.exchange, symbol=r.symbol, timeframe=r.interval,
            ts=r.ts, open=float(r.open), high=float(r.high),
            low=float(r.low), close=float(r.close),
            volume=float(r.volume),
        )
        for r in rows
    ]


__all__ = [
    "DataQualityGrade", "DataQualityConfig",
    "CandleRecord", "DataQualityDayReport",
    "expected_candle_count", "timeframe_seconds",
    "check_missing", "check_duplicates", "check_ohlc_validity",
    "check_volume_anomalies", "check_price_outliers", "check_off_universe",
    "run_day_check",
    "BacktestPromotionGuard", "PromotionEvaluation",
    "load_candles_for_day",
]
