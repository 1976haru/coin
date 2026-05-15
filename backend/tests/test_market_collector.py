"""체크리스트 #15 Market Data Collector — 회귀 테스트.

검증:
  1. MockMarketDataSource — 결정론적, 동일 symbol → 동일 가격
  2. MarketDataCollector — collect/get_ticker/cache_size/clear_cache
  3. Freshness 통합 — stale ts 시 ok=False
  4. 알 수 없는 exchange → entry.error 채워지고 ticker None
  5. source.fetch_ticker 예외 시 캐시 보존 + error 채워짐
  6. Watchlist provider 통합
  7. /api/market/tickers, /api/market/collect REST + admin gating
  8. 모듈 경계: collector는 brokers/execution를 import 하지 않음
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.market.collector import (
    MarketDataCollector, MockMarketDataSource, CollectorReport, CollectorEntry,
)
from app.schemas import Ticker


# ── 1. MockMarketDataSource ─────────────────────────────────────

def test_mock_source_is_deterministic_for_symbol():
    s = MockMarketDataSource("test")
    t1 = s.fetch_ticker("BTC/USDT")
    t2 = s.fetch_ticker("BTC/USDT")
    # 가격은 결정론적, ts만 다름
    assert t1.price == t2.price
    assert t1.bid == t2.bid
    assert t1.ask == t2.ask
    assert t1.symbol == t2.symbol == "BTC/USDT"


def test_mock_source_different_symbols_have_different_prices():
    s = MockMarketDataSource()
    a = s.fetch_ticker("BTC")
    b = s.fetch_ticker("ETH")
    assert a.price != b.price


def test_mock_orderbook_has_correct_depth():
    s = MockMarketDataSource()
    ob = s.fetch_orderbook("BTC", depth=7)
    assert len(ob.bids) == 7
    assert len(ob.asks) == 7
    # bids 내림차순, asks 오름차순
    bid_prices = [p for p, _ in ob.bids]
    ask_prices = [p for p, _ in ob.asks]
    assert bid_prices == sorted(bid_prices, reverse=True)
    assert ask_prices == sorted(ask_prices)


# ── 2. Collector 기본 동작 ───────────────────────────────────────

def test_collector_collects_for_explicit_pairs():
    c = MarketDataCollector(sources={"mock": MockMarketDataSource("mock")})
    report = c.collect([("BTC", "mock"), ("ETH", "mock")])
    assert isinstance(report, CollectorReport)
    assert len(report.entries) == 2
    assert report.ok_count == 2
    assert report.error_count == 0
    for e in report.entries:
        assert e.ticker is not None
        assert e.freshness.ok is True


def test_collector_caches_tickers():
    c = MarketDataCollector(sources={"mock": MockMarketDataSource("mock")})
    assert c.cache_size() == 0
    c.collect([("BTC", "mock")])
    assert c.cache_size() == 1
    cached = c.get_ticker("BTC", "mock")
    assert cached is not None
    assert cached.symbol == "BTC"


def test_collector_clear_cache():
    c = MarketDataCollector(sources={"mock": MockMarketDataSource()})
    c.collect([("BTC", "mock")])
    c.clear_cache()
    assert c.cache_size() == 0
    assert c.get_ticker("BTC", "mock") is None


def test_collector_cached_pairs_sorted():
    c = MarketDataCollector(sources={"mock": MockMarketDataSource()})
    c.collect([("ETH", "mock"), ("BTC", "mock"), ("SOL", "mock")])
    assert c.cached_pairs() == [("BTC", "mock"), ("ETH", "mock"), ("SOL", "mock")]


def test_collector_known_exchanges():
    c = MarketDataCollector(sources={
        "upbit": MockMarketDataSource("upbit"),
        "okx":   MockMarketDataSource("okx"),
    })
    assert c.known_exchanges() == ["okx", "upbit"]


# ── 3. Freshness 통합 ────────────────────────────────────────────

def test_collector_marks_stale_when_ts_is_old():
    """source가 과거 ts를 반환하면 freshness.ok=False"""
    class PastSource:
        name = "past"
        def fetch_ticker(self, symbol: str) -> Ticker:
            return Ticker(
                symbol=symbol, price=100.0, bid=99.5, ask=100.5,
                spread_pct=0.01, volume_24h=0.0,
                ts=datetime.now(timezone.utc) - timedelta(seconds=30),
            )
        def fetch_orderbook(self, symbol: str, depth: int = 5):
            raise NotImplementedError

    c = MarketDataCollector(sources={"past": PastSource()}, freshness_threshold_sec=5.0)
    report = c.collect([("BTC", "past")])
    e = report.entries[0]
    assert e.ticker is not None
    assert e.freshness.ok is False
    assert "지연" in e.freshness.reason
    assert report.stale_count == 1
    assert report.ok_count == 0


# ── 4. 알 수 없는 exchange ───────────────────────────────────────

def test_collector_unknown_exchange_yields_error_entry():
    c = MarketDataCollector(sources={"upbit": MockMarketDataSource("upbit")})
    report = c.collect([("BTC", "binance")])
    e = report.entries[0]
    assert e.ticker is None
    assert e.freshness.ok is False
    assert "알 수 없는 거래소" in e.freshness.reason
    assert "binance" in e.error
    assert report.error_count == 1


# ── 5. fetch 예외 시 캐시 보존 ───────────────────────────────────

def test_collector_preserves_cache_on_source_exception():
    """첫 collect 성공 후 source가 예외를 던지면 마지막 캐시 ticker 보존."""
    call_count = {"n": 0}

    class FlakeySource:
        name = "flakey"
        def fetch_ticker(self, symbol: str) -> Ticker:
            call_count["n"] += 1
            if call_count["n"] >= 2:
                raise RuntimeError("temporary failure")
            return Ticker(
                symbol=symbol, price=100.0, bid=99.5, ask=100.5,
                spread_pct=0.01, volume_24h=0.0,
                ts=datetime.now(timezone.utc),
            )
        def fetch_orderbook(self, symbol: str, depth: int = 5):
            raise NotImplementedError

    c = MarketDataCollector(sources={"flakey": FlakeySource()})
    c.collect([("BTC", "flakey")])
    assert c.cache_size() == 1

    # 2번째 collect: 예외 → cache는 유지, error 필드 채워짐
    report = c.collect([("BTC", "flakey")])
    e = report.entries[0]
    assert e.ticker is not None  # 캐시값
    assert "RuntimeError" in e.error
    assert report.error_count == 1


# ── 6. Watchlist provider 통합 ───────────────────────────────────

def test_collector_collect_from_provider():
    c = MarketDataCollector(sources={
        "upbit": MockMarketDataSource("upbit"),
        "okx":   MockMarketDataSource("okx"),
    })
    pairs = [("BTC", "upbit"), ("ETH", "okx")]
    report = c.collect_from_provider(lambda: pairs)
    assert len(report.entries) == 2
    assert {e.exchange for e in report.entries} == {"upbit", "okx"}


# ── 7. REST API ──────────────────────────────────────────────────

@pytest.fixture
def app_with_db():
    """app + in-memory sqlite DB override."""
    eng = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    Sf = sessionmaker(bind=eng, expire_on_commit=False, future=True)

    from app.main import app
    from app.api.deps import get_db, get_collector

    def _override_db():
        s = Sf()
        try:
            yield s
        finally:
            s.close()

    # 깨끗한 collector (다른 테스트가 캐시를 채워둘 수 있음)
    test_collector = MarketDataCollector(sources={
        "upbit":   MockMarketDataSource("upbit"),
        "okx":     MockMarketDataSource("okx"),
        "binance": MockMarketDataSource("binance"),
    })

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_collector] = lambda: test_collector
    yield app
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_collector, None)
    eng.dispose()


def test_api_tickers_empty_when_no_collect_yet(app_with_db):
    client = TestClient(app_with_db)
    r = client.get("/api/market/tickers")
    assert r.status_code == 200
    body = r.json()
    assert body["tickers"] == []
    assert {"upbit", "okx", "binance"}.issubset(set(body["exchanges"]))


def test_api_collect_requires_admin(app_with_db):
    client = TestClient(app_with_db)
    r = client.post("/api/market/collect")
    assert r.status_code == 401


def test_api_collect_404_when_watchlist_empty(app_with_db):
    from app.core.config import get_settings
    token = get_settings().admin_token
    client = TestClient(app_with_db)
    r = client.post("/api/market/collect", headers={"X-Admin-Token": token})
    assert r.status_code == 404


def test_api_collect_runs_and_populates_tickers(app_with_db):
    from app.core.config import get_settings
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app_with_db)

    # watchlist 채우기
    client.post("/api/watchlist",
                json={"symbol": "BTC", "exchange": "upbit"}, headers=H)
    client.post("/api/watchlist",
                json={"symbol": "ETH", "exchange": "okx"}, headers=H)

    # collect 실행
    r = client.post("/api/market/collect", headers=H)
    assert r.status_code == 200
    rep = r.json()
    assert rep["ok_count"] == 2
    assert len(rep["entries"]) == 2

    # ticker 캐시 조회
    r2 = client.get("/api/market/tickers")
    body = r2.json()
    assert len(body["tickers"]) == 2
    symbols = {t["symbol"] for t in body["tickers"]}
    assert symbols == {"BTC", "ETH"}


def test_api_tickers_filter_by_exchange(app_with_db):
    from app.core.config import get_settings
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app_with_db)

    client.post("/api/watchlist",
                json={"symbol": "BTC", "exchange": "upbit"}, headers=H)
    client.post("/api/watchlist",
                json={"symbol": "ETH", "exchange": "okx"}, headers=H)
    client.post("/api/market/collect", headers=H)

    r = client.get("/api/market/tickers?exchange=upbit")
    body = r.json()
    assert len(body["tickers"]) == 1
    assert body["tickers"][0]["exchange"] == "upbit"


# ── 8. 모듈 경계 ─────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_collector_does_not_import_brokers_or_execution():
    """체크리스트 #15: collector 는 BrokerAdapter / OrderGateway 를 import 금지."""
    text = (REPO_ROOT / "backend" / "app" / "market" / "collector.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        s = line.strip()
        if not (s.startswith("import ") or s.startswith("from ")):
            continue
        for forbidden in ("app.brokers", "app.execution", "ccxt", "pyupbit"):
            assert forbidden not in s, \
                f"collector.py imports forbidden: {s}"
