"""Data Quality — 체크리스트 #17.

시세/호가/환율의 sanity 검사 모음. Strategy/Collector/Agent 가 결과 플래그를
받아 신호 생성 전 차단 조건으로 사용한다 (예: KimpStrategy 의 liquidity_ok,
fx_anomaly_ok 입력).

설계 원칙:
  - 모든 체크는 순수 함수 — 외부 I/O, 글로벌 상태 없음
  - 결과는 frozen dataclass (severity: block / warn / ok)
  - QualityReport.has_blocking 이 True 면 신규 진입 차단
  - 호가 spread/볼륨/깊이 한도는 인자로 주입 — 운용 환경마다 settings 로 덮어씀
"""
from __future__ import annotations
from dataclasses import dataclass, field

from app.schemas import Ticker, OrderBook


# ── 기본 한도 ────────────────────────────────────────────────────

DEFAULT_MAX_SPREAD_PCT       = 0.5         # 0.5%
DEFAULT_MIN_VOLUME_24H_USDT  = 100_000.0
DEFAULT_MIN_OB_LEVELS        = 5
DEFAULT_MIN_TOP_SIZE         = 1.0
DEFAULT_MAX_FX_DEVIATION_PCT = 5.0          # USDT/KRW ±5%
DEFAULT_MAX_PRICE_SPIKE_PCT  = 8.0          # 봉 간 ±8%


# ── 결과 타입 ────────────────────────────────────────────────────

@dataclass(frozen=True)
class QualityCheck:
    name: str           # spread / volume / ob_depth / ob_top_size / fx_anomaly / spike / quote
    ok: bool
    severity: str       # "block" | "warn" | "ok"
    reason: str
    value: float | None = None


@dataclass(frozen=True)
class QualityReport:
    label: str          # 예: "BTC/USDT@upbit"
    checks: tuple[QualityCheck, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.has_blocking

    @property
    def has_blocking(self) -> bool:
        return any(c.severity == "block" and not c.ok for c in self.checks)

    @property
    def blocks(self) -> tuple[QualityCheck, ...]:
        return tuple(c for c in self.checks if c.severity == "block" and not c.ok)

    @property
    def warnings(self) -> tuple[QualityCheck, ...]:
        return tuple(c for c in self.checks if c.severity == "warn" and not c.ok)

    @property
    def liquidity_ok(self) -> bool:
        """KimpStrategy 의 liquidity_ok 입력으로 직결되는 플래그.

        spread / volume / orderbook 깊이 / top-size 중 하나라도 block 이면 False.
        """
        names = {"spread", "volume", "ob_depth", "ob_top_size", "quote"}
        for c in self.checks:
            if c.name in names and c.severity == "block" and not c.ok:
                return False
        return True

    @property
    def fx_anomaly_ok(self) -> bool:
        for c in self.checks:
            if c.name == "fx_anomaly" and c.severity == "block" and not c.ok:
                return False
        return True


# ── 개별 체크 ────────────────────────────────────────────────────

def check_quote_sanity(t: Ticker, max_spread_pct: float = DEFAULT_MAX_SPREAD_PCT
                       ) -> tuple[QualityCheck, ...]:
    """price 가 bid <= price <= ask, spread 가 한도 이내."""
    out: list[QualityCheck] = []

    if t.bid <= 0 or t.ask <= 0:
        out.append(QualityCheck("quote", False, "block",
                                f"bid/ask 비정상: bid={t.bid}, ask={t.ask}"))
        return tuple(out)

    if t.bid > t.ask:
        out.append(QualityCheck("quote", False, "block",
                                f"bid({t.bid}) > ask({t.ask}) — crossed market"))

    if t.price > 0 and not (t.bid <= t.price <= t.ask):
        out.append(QualityCheck("quote", False, "warn",
                                f"price({t.price}) 가 bid({t.bid})~ask({t.ask}) 범위 밖"))

    spread_pct = (t.ask - t.bid) / t.bid * 100.0 if t.bid > 0 else 0.0
    if spread_pct > max_spread_pct:
        out.append(QualityCheck("spread", False, "block",
                                f"spread {spread_pct:.3f}% > 한도 {max_spread_pct}%",
                                value=spread_pct))
    else:
        out.append(QualityCheck("spread", True, "ok",
                                f"spread {spread_pct:.3f}%", value=spread_pct))
    return tuple(out)


def check_volume_floor(t: Ticker,
                       min_volume: float = DEFAULT_MIN_VOLUME_24H_USDT) -> QualityCheck:
    if t.volume_24h < min_volume:
        return QualityCheck("volume", False, "block",
                            f"24h 볼륨 {t.volume_24h:,.0f} < 한도 {min_volume:,.0f}",
                            value=t.volume_24h)
    return QualityCheck("volume", True, "ok",
                        f"24h 볼륨 {t.volume_24h:,.0f}", value=t.volume_24h)


def check_orderbook_depth(
    ob: OrderBook,
    min_levels: int = DEFAULT_MIN_OB_LEVELS,
    min_top_size: float = DEFAULT_MIN_TOP_SIZE,
) -> tuple[QualityCheck, ...]:
    out: list[QualityCheck] = []
    n_bids = len(ob.bids)
    n_asks = len(ob.asks)
    if n_bids < min_levels or n_asks < min_levels:
        out.append(QualityCheck("ob_depth", False, "block",
            f"호가 깊이 부족: bids={n_bids}, asks={n_asks} (한도 {min_levels})"))
    else:
        out.append(QualityCheck("ob_depth", True, "ok",
            f"호가 깊이 OK: {n_bids}/{n_asks}"))

    top_bid_size = float(ob.bids[0][1]) if ob.bids else 0.0
    top_ask_size = float(ob.asks[0][1]) if ob.asks else 0.0
    if top_bid_size < min_top_size or top_ask_size < min_top_size:
        out.append(QualityCheck("ob_top_size", False, "block",
            f"top-of-book 사이즈 부족: bid={top_bid_size}, ask={top_ask_size} "
            f"(한도 {min_top_size})"))
    else:
        out.append(QualityCheck("ob_top_size", True, "ok",
            f"top-of-book OK: bid={top_bid_size}, ask={top_ask_size}"))
    return tuple(out)


def check_fx_rate_sanity(
    rate: float,
    fallback: float,
    max_deviation_pct: float = DEFAULT_MAX_FX_DEVIATION_PCT,
) -> QualityCheck:
    """USDT/KRW 환율 sanity. fallback 대비 ±N% 이내여야 함.

    KimpStrategy.fx_anomaly_ok 로 직결.
    """
    if rate <= 0:
        return QualityCheck("fx_anomaly", False, "block",
                            f"환율 비정상: {rate}", value=rate)
    if fallback <= 0:
        return QualityCheck("fx_anomaly", True, "warn",
                            f"fallback 환율 미설정({fallback}) — 비교 불가",
                            value=rate)
    deviation = abs(rate - fallback) / fallback * 100.0
    if deviation > max_deviation_pct:
        return QualityCheck("fx_anomaly", False, "block",
            f"환율 이상치: {rate:.2f} vs fallback {fallback:.2f} 편차 {deviation:.2f}% "
            f"> 한도 {max_deviation_pct}%", value=deviation)
    return QualityCheck("fx_anomaly", True, "ok",
                        f"환율 정상: {rate:.2f} (편차 {deviation:.3f}%)",
                        value=deviation)


def check_price_spike(
    current_price: float,
    prev_price: float,
    max_pct: float = DEFAULT_MAX_PRICE_SPIKE_PCT,
) -> QualityCheck:
    if prev_price <= 0:
        return QualityCheck("spike", True, "warn",
                            "이전 가격 없음 — 비교 불가")
    delta_pct = (current_price - prev_price) / prev_price * 100.0
    if abs(delta_pct) > max_pct:
        return QualityCheck("spike", False, "block",
                            f"가격 급변: {delta_pct:+.2f}% (한도 ±{max_pct}%)",
                            value=delta_pct)
    return QualityCheck("spike", True, "ok",
                        f"가격 정상 변동: {delta_pct:+.3f}%", value=delta_pct)


# ── 집계 ─────────────────────────────────────────────────────────

def assess_quote(
    label: str,
    ticker: Ticker,
    *,
    max_spread_pct: float = DEFAULT_MAX_SPREAD_PCT,
    min_volume: float = DEFAULT_MIN_VOLUME_24H_USDT,
    prev_price: float | None = None,
    max_spike_pct: float = DEFAULT_MAX_PRICE_SPIKE_PCT,
) -> QualityReport:
    checks: list[QualityCheck] = []
    checks.extend(check_quote_sanity(ticker, max_spread_pct))
    checks.append(check_volume_floor(ticker, min_volume))
    if prev_price is not None:
        checks.append(check_price_spike(ticker.price, prev_price, max_spike_pct))
    return QualityReport(label=label, checks=tuple(checks))


def assess_orderbook(
    label: str,
    ob: OrderBook,
    *,
    min_levels: int = DEFAULT_MIN_OB_LEVELS,
    min_top_size: float = DEFAULT_MIN_TOP_SIZE,
) -> QualityReport:
    return QualityReport(
        label=label,
        checks=tuple(check_orderbook_depth(ob, min_levels, min_top_size)),
    )
