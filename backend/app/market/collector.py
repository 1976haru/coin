"""MarketDataCollector — 체크리스트 #15 Market Data Collector.

Watchlist 에서 enabled 심볼을 받아 시세를 1회 또는 주기적으로 수집한다.
ticker/orderbook 외에 OHLCV/funding/FX 도 옵션으로 함께 수집할 수 있다.

설계 원칙 (CLAUDE.md §2):
  - 거래소 SDK 직접 의존 금지 — `MarketDataSource` Protocol 로 추상화.
  - 실제 Upbit/OKX/Binance source 는 #21·#22·#23 Exchange Adapter 에 위치.
    본 모듈은 그것들을 import 하지 않는다 (회귀 테스트로 강제).
  - 본 모듈은 read-only — 주문/잔고/체결/계좌 호출 코드 없음.
  - Collector 는 broker / gateway / executor 계층과 분리되어 있다.
  - 전체 시장 자동 스캔 금지 — 반드시 Watchlist universe 기반.
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Protocol, runtime_checkable

from app.schemas import Ticker, OHLCV, OrderBook, FundingRate, FxRate
from app.market.freshness import FreshnessStatus, check_timestamp_freshness


# ── 추상 인터페이스 ──────────────────────────────────────────────
#
# Protocol 자체는 최소(`fetch_ticker`, `fetch_orderbook`)만 정의 —
# 기존 Upbit/OKX/Binance read-only adapter 들과의 호환을 위해.
# OHLCV/funding/FX 는 capability 가 있는 source 만 구현 (Collector 는 hasattr 로 우회).

@runtime_checkable
class MarketDataSource(Protocol):
    """거래소 시세 소스 — read-only.

    OHLCV/funding/FX 는 *optional capability* 이므로 본 Protocol 에는 두지 않는다.
    구현하는 source 만 같은 시그니처로 추가하면 Collector 가 발견해 사용한다.
    """

    name: str

    def fetch_ticker(self, symbol: str) -> Ticker: ...
    def fetch_orderbook(self, symbol: str, depth: int = 5) -> OrderBook: ...


# Optional capability — hasattr 로 확인.
#   fetch_ohlcv(symbol, timeframe="1m", limit=100) -> list[OHLCV]
#   fetch_funding(symbol) -> FundingRate | None
#   fetch_fx(pair) -> FxRate | None


# ── 결정론적 Mock ────────────────────────────────────────────────

_TIMEFRAME_SECONDS: dict[str, int] = {
    "1m":  60,
    "5m":  300,
    "15m": 900,
    "1h":  3600,
    "4h":  14400,
    "1d":  86400,
}


def timeframe_seconds(tf: str) -> int:
    """`1m/5m/15m/1h/4h/1d` 매핑. 미지원이면 ValueError."""
    if tf not in _TIMEFRAME_SECONDS:
        raise ValueError(f"unsupported timeframe: {tf!r}")
    return _TIMEFRAME_SECONDS[tf]


class MockMarketDataSource:
    """결정론적 mock — symbol hash 기반 가격, 매 호출 시 ts 갱신.

    funding/fx 지원 여부는 생성자 인자로 켠다 (spot-only 거래소 시뮬레이션).
    개발/테스트/CI 에서 외부 네트워크 없이 collector 동작을 검증한다.
    """

    def __init__(
        self,
        name: str = "mock",
        *,
        supports_funding: bool = False,
        supports_fx: bool = False,
    ):
        self.name = name
        self.supports_funding = supports_funding
        self.supports_fx = supports_fx

    @staticmethod
    def _seed(*parts: str) -> int:
        m = hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:8]
        return int(m, 16)

    def _base_price(self, symbol: str) -> float:
        return 1000.0 + float(self._seed(symbol) % 100_000)

    def fetch_ticker(self, symbol: str) -> Ticker:
        h = self._seed(symbol)
        price = self._base_price(symbol)
        bid = price * 0.9995
        ask = price * 1.0005
        return Ticker(
            symbol=symbol, price=price, bid=bid, ask=ask,
            spread_pct=(ask - bid) / bid,
            volume_24h=float(h % 1_000_000_000),
            ts=datetime.now(timezone.utc),
        )

    def fetch_orderbook(self, symbol: str, depth: int = 5) -> OrderBook:
        t = self.fetch_ticker(symbol)
        bids = tuple((t.bid * (1 - 0.0001 * i), 1.0) for i in range(depth))
        asks = tuple((t.ask * (1 + 0.0001 * i), 1.0) for i in range(depth))
        return OrderBook(symbol=symbol, bids=bids, asks=asks, ts=t.ts)

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100,
    ) -> list[OHLCV]:
        """결정론적 OHLCV 시퀀스. 최신 봉이 마지막 원소."""
        sec = timeframe_seconds(timeframe)
        base = self._base_price(symbol)
        out: list[OHLCV] = []
        # 현재 시각의 봉 경계
        end_epoch = int(datetime.now(timezone.utc).timestamp())
        end_epoch -= end_epoch % sec
        for i in range(limit):
            # 결정론적 변동 — symbol+timeframe+bar_index 시드
            h = self._seed(symbol, timeframe, str(i))
            drift = ((h % 1000) - 500) / 10_000.0   # ±5%
            close = base * (1.0 + drift)
            open_ = close * (1.0 + ((h >> 4) % 200 - 100) / 100_000.0)
            high  = max(open_, close) * 1.001
            low   = min(open_, close) * 0.999
            vol   = float((h >> 8) % 1_000_000) / 100.0
            ts = datetime.fromtimestamp(end_epoch - (limit - 1 - i) * sec, tz=timezone.utc)
            out.append(OHLCV(
                symbol=symbol, timeframe=timeframe, ts=ts,
                open=open_, high=high, low=low, close=close, volume=vol,
            ))
        return out

    def fetch_funding(self, symbol: str) -> FundingRate | None:
        """spot-only 시뮬레이션(default) 에서는 None. perpetual 시뮬레이션이면 결정론적 rate."""
        if not self.supports_funding:
            return None
        h = self._seed(self.name, symbol, "funding")
        rate = ((h % 2000) - 1000) / 1_000_000.0  # ±0.001 (=0.1%)
        ts = datetime.now(timezone.utc)
        next_ts = ts + timedelta(hours=8)
        return FundingRate(
            symbol=symbol, exchange=self.name,
            funding_rate=rate, ts=ts, next_funding_time=next_ts,
        )

    def fetch_fx(self, pair: str) -> FxRate | None:
        """FX 소스로 동작할 때만 결정론적 환율 반환."""
        if not self.supports_fx:
            return None
        h = self._seed(self.name, pair, "fx")
        # USDT-KRW 표준 기준선 1300 ± 30
        rate = 1300.0 + ((h % 60_000) / 1000.0 - 30.0)
        return FxRate(pair=pair, rate=rate, ts=datetime.now(timezone.utc), source=self.name)


# ── 결과 타입 ────────────────────────────────────────────────────

# 수집 옵션 — 어떤 데이터 타입을 수집할지.
ALLOWED_INCLUDES: frozenset[str] = frozenset({
    "ticker", "ohlcv", "orderbook", "funding", "fx",
})
DEFAULT_INCLUDES: frozenset[str] = frozenset({"ticker"})


@dataclass(frozen=True)
class CollectorEntry:
    """단일 (symbol, exchange) ticker 수집 결과 — legacy 인터페이스 유지."""

    symbol: str
    exchange: str
    ticker: Ticker | None
    freshness: FreshnessStatus
    error: str = ""


@dataclass(frozen=True)
class CollectorReport:
    """legacy 보고서 (ticker-only)."""

    started_at: datetime
    finished_at: datetime
    entries: tuple[CollectorEntry, ...] = field(default_factory=tuple)

    @property
    def ok_count(self) -> int:
        return sum(1 for e in self.entries if e.ticker is not None and e.freshness.ok)

    @property
    def stale_count(self) -> int:
        return sum(1 for e in self.entries if e.ticker is not None and not e.freshness.ok)

    @property
    def error_count(self) -> int:
        return sum(1 for e in self.entries if e.error)


@dataclass(frozen=True)
class MultiCollectorEntry:
    """단일 (symbol, exchange) 의 다중 데이터 타입 수집 결과 — #15 확장."""

    symbol: str
    exchange: str
    ticker: Ticker | None = None
    ohlcv: tuple[OHLCV, ...] = ()
    orderbook: OrderBook | None = None
    funding: FundingRate | None = None
    freshness: FreshnessStatus | None = None
    # 데이터 타입별 실패 사유 (해당 타입을 요청했지만 실패한 경우만 기록)
    failures: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class MultiCollectorReport:
    """`collect_all` 결과 — 다중 데이터 타입 / Watchlist 기반 / persist 옵션."""

    started_at: datetime
    finished_at: datetime
    requested_pairs: int
    deduped_pairs: int
    truncated_to: int            # MARKET_COLLECTOR_MAX_SYMBOLS 적용 결과
    includes: tuple[str, ...]
    list_name: str | None
    exchange_filter: str | None
    entries: tuple[MultiCollectorEntry, ...] = field(default_factory=tuple)
    fx_rates: tuple[FxRate, ...] = field(default_factory=tuple)
    persisted: dict[str, int] = field(default_factory=dict)

    @property
    def success_count(self) -> int:
        return sum(1 for e in self.entries if not e.failures)

    @property
    def failure_count(self) -> int:
        return sum(1 for e in self.entries if e.failures)

    @property
    def symbol_count(self) -> int:
        return len(self.entries)


# ── Collector ────────────────────────────────────────────────────

WatchlistProvider = Callable[[], Iterable[tuple[str, str]]]


class EmptyWatchlistError(RuntimeError):
    """Watchlist 가 비어 있을 때 전체 시장 fallback 을 막기 위해 던진다."""


class MarketDataCollector:
    """Watchlist 기반 시세 수집기.

    legacy API (변경 없음):
      - collect(pairs)                 : ticker 1회 수집 → CollectorReport
      - collect_from_provider(provider): provider 가 반환하는 pairs 로 ticker 수집
      - get_ticker / cached_pairs / cache_size / clear_cache / known_exchanges

    신규 API (#15):
      - collect_all(...) : Watchlist 기반, ticker/ohlcv/orderbook/funding/fx 다중 수집

    sources: 거래소 이름 → MarketDataSource (`{"upbit": ..., "okx": ...}`)
    fx_source: FX 데이터 source (있으면 `collect_all(include={"fx"})` 시 사용)
    """

    def __init__(
        self,
        sources: dict[str, MarketDataSource],
        freshness_threshold_sec: float = 5.0,
        *,
        fx_source: object | None = None,
        freshness_tracker: object | None = None,
    ):
        self.sources = dict(sources)
        self.freshness_threshold_sec = float(freshness_threshold_sec)
        self.fx_source = fx_source
        # 체크리스트 #16: 수집 성공 시 마지막 수신 시각을 기록 — 신규 진입 guard 입력.
        self.freshness_tracker = freshness_tracker
        self._cache: dict[tuple[str, str], Ticker] = {}
        # 최근 수집 상태 — /api/market/collector/status 용
        self._last_status: dict = {
            "last_collected_at":  None,
            "last_symbol_count":  0,
            "last_success_count": 0,
            "last_failure_count": 0,
            "last_includes":      (),
            "last_list_name":     None,
        }

    def _mark_seen(self, symbol: str, exchange: str, data_type: str,
                   timeframe: str | None = None,
                   seen_at: datetime | None = None) -> None:
        """tracker 가 있으면 마지막 수신 시각을 기록. 실패는 무시 (수집 자체를 막지 않는다)."""
        t = self.freshness_tracker
        if t is None:
            return
        try:
            t.mark_seen(symbol=symbol, exchange=exchange,
                        data_type=data_type, timeframe=timeframe,
                        seen_at=seen_at)
        except Exception:
            pass

    # ── legacy API (그대로 유지) ──────────────────────────────────

    def collect(
        self,
        symbols: Iterable[tuple[str, str]],
        now: datetime | None = None,
    ) -> CollectorReport:
        """주어진 (symbol, exchange) 쌍에 대해 1회 ticker 수집."""
        now = now or datetime.now(timezone.utc)
        started_at = now
        entries = tuple(self._collect_one(s, ex, now) for s, ex in symbols)
        return CollectorReport(
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            entries=entries,
        )

    def collect_from_provider(
        self,
        provider: WatchlistProvider,
        now: datetime | None = None,
    ) -> CollectorReport:
        return self.collect(list(provider()), now=now)

    def get_ticker(self, symbol: str, exchange: str) -> Ticker | None:
        return self._cache.get((symbol, exchange))

    def cached_pairs(self) -> list[tuple[str, str]]:
        return sorted(self._cache.keys())

    def cache_size(self) -> int:
        return len(self._cache)

    def clear_cache(self) -> None:
        self._cache.clear()

    def known_exchanges(self) -> list[str]:
        return sorted(self.sources.keys())

    # ── #15 신규 API ─────────────────────────────────────────────

    def collect_all(
        self,
        pairs: Iterable[tuple[str, str]],
        *,
        includes: Iterable[str] = DEFAULT_INCLUDES,
        timeframe: str = "1m",
        ohlcv_limit: int = 100,
        orderbook_depth: int = 5,
        fx_pairs: Iterable[str] = (),
        max_symbols: int | None = None,
        list_name: str | None = None,
        exchange_filter: str | None = None,
        now: datetime | None = None,
    ) -> MultiCollectorReport:
        """Watchlist 기반 다중 데이터 수집.

        - `pairs`: (symbol, exchange) 시퀀스. 일반적으로 WatchlistService 가 공급.
        - `includes`: {"ticker","ohlcv","orderbook","funding","fx"} 부분집합.
        - `max_symbols`: 적용 후 처리할 최대 심볼 수. None → 무제한.
        - 비어 있는 pairs 입력 시 `EmptyWatchlistError` — 전체 시장 fallback 금지.
        """
        includes_set = frozenset(s.lower() for s in includes)
        unknown = includes_set - ALLOWED_INCLUDES
        if unknown:
            raise ValueError(f"unknown include keys: {sorted(unknown)}")

        now = now or datetime.now(timezone.utc)
        started_at = now

        pairs_list = list(pairs)
        requested = len(pairs_list)
        if requested == 0:
            raise EmptyWatchlistError(
                "watchlist universe is empty — refusing to fall back to full-market scan"
            )

        # 중복 (symbol, exchange) 제거 — 입력 순서 보존.
        seen: set[tuple[str, str]] = set()
        deduped: list[tuple[str, str]] = []
        for p in pairs_list:
            if p in seen:
                continue
            seen.add(p)
            deduped.append(p)

        # 최대 수 제한.
        if max_symbols is not None and max_symbols >= 0:
            truncated_pairs = deduped[:max_symbols]
        else:
            truncated_pairs = deduped

        entries: list[MultiCollectorEntry] = []
        for symbol, exchange in truncated_pairs:
            entries.append(self._collect_multi_one(
                symbol, exchange, now,
                includes=includes_set,
                timeframe=timeframe,
                ohlcv_limit=ohlcv_limit,
                orderbook_depth=orderbook_depth,
            ))

        fx_rates: list[FxRate] = []
        if "fx" in includes_set and self.fx_source is not None:
            for pair in fx_pairs:
                try:
                    r = self.fx_source.fetch_fx(pair)
                    if r is not None:
                        fx_rates.append(r)
                        # FX 는 symbol=pair, exchange="fx" 로 tracker 에 기록.
                        self._mark_seen(pair, "fx", "fx", seen_at=r.ts)
                except Exception:
                    # FX 실패는 collect 전체를 막지 않는다.
                    pass

        finished_at = datetime.now(timezone.utc)
        success_count = sum(1 for e in entries if not e.failures)
        failure_count = sum(1 for e in entries if e.failures)
        self._last_status = {
            "last_collected_at":  finished_at,
            "last_symbol_count":  len(entries),
            "last_success_count": success_count,
            "last_failure_count": failure_count,
            "last_includes":      tuple(sorted(includes_set)),
            "last_list_name":     list_name,
        }

        return MultiCollectorReport(
            started_at=started_at,
            finished_at=finished_at,
            requested_pairs=requested,
            deduped_pairs=len(deduped),
            truncated_to=len(truncated_pairs),
            includes=tuple(sorted(includes_set)),
            list_name=list_name,
            exchange_filter=exchange_filter,
            entries=tuple(entries),
            fx_rates=tuple(fx_rates),
        )

    def last_status(self) -> dict:
        """`/api/market/collector/status` 응답용 스냅샷."""
        s = dict(self._last_status)
        if isinstance(s.get("last_collected_at"), datetime):
            s["last_collected_at"] = s["last_collected_at"].isoformat()
        return {
            **s,
            "sources":          self.known_exchanges(),
            "fx_source":        getattr(self.fx_source, "name", None),
            "freshness_threshold_sec": self.freshness_threshold_sec,
            "cache_size":       self.cache_size(),
            "mode":             "read-only",
        }

    # ── internals ─────────────────────────────────────────────────

    def _collect_one(
        self, symbol: str, exchange: str, now: datetime,
    ) -> CollectorEntry:
        source = self.sources.get(exchange)
        if source is None:
            return CollectorEntry(
                symbol=symbol, exchange=exchange,
                ticker=self._cache.get((symbol, exchange)),
                freshness=FreshnessStatus(False, None,
                    f"{exchange}:{symbol}: 알 수 없는 거래소"),
                error=f"unknown exchange: {exchange}",
            )
        try:
            ticker = source.fetch_ticker(symbol)
        except Exception as e:
            cached = self._cache.get((symbol, exchange))
            return CollectorEntry(
                symbol=symbol, exchange=exchange,
                ticker=cached,
                freshness=check_timestamp_freshness(
                    cached.ts if cached else None,
                    self.freshness_threshold_sec,
                    now=now,
                    label=f"{exchange}:{symbol}",
                ),
                error=f"{type(e).__name__}: {e}",
            )

        self._cache[(symbol, exchange)] = ticker
        self._mark_seen(symbol, exchange, "ticker", seen_at=ticker.ts)
        fr = check_timestamp_freshness(
            ticker.ts, self.freshness_threshold_sec, now=now,
            label=f"{exchange}:{symbol}",
        )
        return CollectorEntry(
            symbol=symbol, exchange=exchange,
            ticker=ticker, freshness=fr,
        )

    def _collect_multi_one(
        self,
        symbol: str,
        exchange: str,
        now: datetime,
        *,
        includes: frozenset[str],
        timeframe: str,
        ohlcv_limit: int,
        orderbook_depth: int,
    ) -> MultiCollectorEntry:
        source = self.sources.get(exchange)
        if source is None:
            return MultiCollectorEntry(
                symbol=symbol, exchange=exchange,
                ticker=None,
                freshness=FreshnessStatus(False, None,
                    f"{exchange}:{symbol}: 알 수 없는 거래소"),
                failures=(("source", f"unknown exchange: {exchange}"),),
            )

        ticker: Ticker | None = None
        ohlcv: tuple[OHLCV, ...] = ()
        ob: OrderBook | None = None
        funding: FundingRate | None = None
        freshness: FreshnessStatus | None = None
        failures: list[tuple[str, str]] = []

        if "ticker" in includes:
            try:
                ticker = source.fetch_ticker(symbol)
                self._cache[(symbol, exchange)] = ticker
                self._mark_seen(symbol, exchange, "ticker", seen_at=ticker.ts)
                freshness = check_timestamp_freshness(
                    ticker.ts, self.freshness_threshold_sec, now=now,
                    label=f"{exchange}:{symbol}",
                )
            except Exception as e:
                failures.append(("ticker", f"{type(e).__name__}: {e}"))

        if "ohlcv" in includes:
            if not hasattr(source, "fetch_ohlcv"):
                failures.append(("ohlcv", f"{exchange}: source does not support OHLCV"))
            else:
                try:
                    ohlcv = tuple(source.fetch_ohlcv(symbol, timeframe, ohlcv_limit))
                    self._mark_seen(symbol, exchange, "ohlcv", timeframe=timeframe)
                except Exception as e:
                    failures.append(("ohlcv", f"{type(e).__name__}: {e}"))

        if "orderbook" in includes:
            try:
                ob = source.fetch_orderbook(symbol, depth=orderbook_depth)
                self._mark_seen(symbol, exchange, "orderbook", seen_at=ob.ts)
            except Exception as e:
                failures.append(("orderbook", f"{type(e).__name__}: {e}"))

        if "funding" in includes:
            if not hasattr(source, "fetch_funding"):
                # 정책: spot 거래소 등 funding 미지원은 실패가 아닌 빈 결과.
                funding = None
            else:
                try:
                    funding = source.fetch_funding(symbol)
                    if funding is not None:
                        self._mark_seen(symbol, exchange, "funding", seen_at=funding.ts)
                except Exception as e:
                    failures.append(("funding", f"{type(e).__name__}: {e}"))

        return MultiCollectorEntry(
            symbol=symbol, exchange=exchange,
            ticker=ticker, ohlcv=ohlcv, orderbook=ob,
            funding=funding, freshness=freshness,
            failures=tuple(failures),
        )
