"""MarketDataCollector — 체크리스트 #15 Market Data Collector.

Watchlist에서 enabled 심볼을 받아 시세를 주기적으로 수집하고, freshness 상태와
ticker 캐시를 유지한다.

설계 원칙 (CLAUDE.md):
  - 거래소 SDK 직접 의존 금지 — `MarketDataSource` Protocol 로 추상화
  - 실제 Upbit/OKX/Binance source는 #21·#22 Exchange Adapter 에서 구현
  - Collector 자체는 BrokerAdapter 를 import 하지 않으며 OrderGateway 와 무관
  - WebSocket 직접 연결 금지 — 레거시 quarantine 유지 (#15 후속에서 별도 검토)
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Protocol, runtime_checkable

from app.schemas import Ticker, OrderBook
from app.market.freshness import FreshnessStatus, check_timestamp_freshness


# ── 추상 인터페이스 ──────────────────────────────────────────────

@runtime_checkable
class MarketDataSource(Protocol):
    """거래소 시세 소스 — read-only.

    실 구현은 #21 (Upbit) / #22 (OKX) / #23 (Binance) 에서 추가된다.
    이 인터페이스는 OrderGateway·BrokerAdapter 와 분리되어 주문 흐름과 관련 없음.
    """

    name: str

    def fetch_ticker(self, symbol: str) -> Ticker: ...
    def fetch_orderbook(self, symbol: str, depth: int = 5) -> OrderBook: ...


# ── 결정론적 Mock ────────────────────────────────────────────────

class MockMarketDataSource:
    """결정론적 mock — symbol hash 기반 가격, 매 호출 시 ts 갱신.

    개발/테스트/CI 에서 외부 네트워크 없이 collector 동작을 검증한다.
    """

    def __init__(self, name: str = "mock"):
        self.name = name

    @staticmethod
    def _seed(symbol: str) -> int:
        return int(hashlib.md5(symbol.encode("utf-8")).hexdigest()[:8], 16)

    def fetch_ticker(self, symbol: str) -> Ticker:
        h = self._seed(symbol)
        price = 1000.0 + float(h % 100_000)
        bid = price * 0.9995
        ask = price * 1.0005
        return Ticker(
            symbol=symbol,
            price=price,
            bid=bid,
            ask=ask,
            spread_pct=(ask - bid) / bid,
            volume_24h=float(h % 1_000_000_000),
            ts=datetime.now(timezone.utc),
        )

    def fetch_orderbook(self, symbol: str, depth: int = 5) -> OrderBook:
        t = self.fetch_ticker(symbol)
        bids = tuple((t.bid * (1 - 0.0001 * i), 1.0) for i in range(depth))
        asks = tuple((t.ask * (1 + 0.0001 * i), 1.0) for i in range(depth))
        return OrderBook(symbol=symbol, bids=bids, asks=asks, ts=t.ts)


# ── 결과 타입 ────────────────────────────────────────────────────

@dataclass(frozen=True)
class CollectorEntry:
    """단일 (symbol, exchange) 수집 결과."""

    symbol: str
    exchange: str
    ticker: Ticker | None
    freshness: FreshnessStatus
    error: str = ""


@dataclass(frozen=True)
class CollectorReport:
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


# ── Collector ────────────────────────────────────────────────────

WatchlistProvider = Callable[[], Iterable[tuple[str, str]]]


class MarketDataCollector:
    """Watchlist 기반 시세 수집기.

    sources: exchange 이름 → MarketDataSource (예: {"upbit": UpbitSource(), "okx": OkxSource()})
    freshness_threshold_sec: ticker.ts 가 이보다 오래되면 freshness.ok=False
    """

    def __init__(
        self,
        sources: dict[str, MarketDataSource],
        freshness_threshold_sec: float = 5.0,
    ):
        self.sources = dict(sources)
        self.freshness_threshold_sec = float(freshness_threshold_sec)
        self._cache: dict[tuple[str, str], Ticker] = {}

    # ── public ────────────────────────────────────────────────────

    def collect(
        self,
        symbols: Iterable[tuple[str, str]],
        now: datetime | None = None,
    ) -> CollectorReport:
        """주어진 (symbol, exchange) 쌍에 대해 1회 수집."""
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
        """provider() 가 반환하는 (symbol, exchange) 시퀀스에 대해 1회 수집."""
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
        fr = check_timestamp_freshness(
            ticker.ts, self.freshness_threshold_sec, now=now,
            label=f"{exchange}:{symbol}",
        )
        return CollectorEntry(
            symbol=symbol, exchange=exchange,
            ticker=ticker, freshness=fr,
        )
