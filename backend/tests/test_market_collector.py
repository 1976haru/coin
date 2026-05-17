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

from app.db.models import Base, CoinCandle, CoinTick, CoinOrderbookSnapshot
from app.market.collector import (
    MarketDataCollector, MockMarketDataSource, CollectorReport,
    MultiCollectorReport, EmptyWatchlistError, ALLOWED_INCLUDES,
    MarketDataSource,
)
from app.market.market_persister import persist_report
from app.schemas import Ticker, OHLCV, OrderBook, FundingRate, FxRate


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


# ── 9. MockMarketDataSource — OHLCV / funding / FX (#15 신규) ────

def test_mock_source_returns_ohlcv_with_requested_limit():
    s = MockMarketDataSource("mock")
    candles = s.fetch_ohlcv("BTC", timeframe="1m", limit=10)
    assert isinstance(candles, list)
    assert len(candles) == 10
    for c in candles:
        assert isinstance(c, OHLCV)
        assert c.timeframe == "1m"
        assert c.symbol == "BTC"
        # OHLC 정상성
        assert c.low <= c.open <= c.high
        assert c.low <= c.close <= c.high
    # 시간 단조 증가
    ts_list = [c.ts for c in candles]
    assert ts_list == sorted(ts_list)


def test_mock_source_ohlcv_deterministic_for_same_inputs():
    s = MockMarketDataSource("mock")
    a = s.fetch_ohlcv("BTC", "1m", 5)
    b = s.fetch_ohlcv("BTC", "1m", 5)
    assert [c.open for c in a] == [c.open for c in b]
    assert [c.close for c in a] == [c.close for c in b]


def test_mock_source_funding_none_for_spot():
    """기본 spot 모드 mock — funding 미지원, None 반환."""
    s = MockMarketDataSource("mock_spot")
    assert s.fetch_funding("BTC") is None


def test_mock_source_funding_when_supported():
    s = MockMarketDataSource("mock_perp", supports_funding=True)
    fr = s.fetch_funding("BTC-PERP")
    assert isinstance(fr, FundingRate)
    assert fr.symbol == "BTC-PERP"
    assert fr.exchange == "mock_perp"
    # 결정론
    fr2 = s.fetch_funding("BTC-PERP")
    assert fr.funding_rate == fr2.funding_rate


def test_mock_source_fx_when_supported():
    s = MockMarketDataSource("mock_fx", supports_fx=True)
    r = s.fetch_fx("USDT-KRW")
    assert isinstance(r, FxRate)
    assert r.pair == "USDT-KRW"
    assert 1200 <= r.rate <= 1400


def test_mock_source_fx_none_when_not_supported():
    s = MockMarketDataSource("mock_no_fx")
    assert s.fetch_fx("USDT-KRW") is None


# ── 10. collect_all — Watchlist 기반 / 다중 데이터 (#15) ─────────

def test_collect_all_includes_all_data_types():
    c = MarketDataCollector(sources={"mock": MockMarketDataSource("mock")})
    rep = c.collect_all(
        [("BTC", "mock"), ("ETH", "mock")],
        includes={"ticker", "ohlcv", "orderbook"},
        ohlcv_limit=5,
    )
    assert isinstance(rep, MultiCollectorReport)
    assert rep.symbol_count == 2
    assert rep.success_count == 2
    for e in rep.entries:
        assert e.ticker is not None
        assert len(e.ohlcv) == 5
        assert e.orderbook is not None


def test_collect_all_funding_spot_returns_none_without_failure():
    """spot mock 은 fetch_funding 이 None 을 반환 → failures 에 안 기록되어야 한다."""
    c = MarketDataCollector(sources={"mock_spot": MockMarketDataSource("mock_spot")})
    rep = c.collect_all(
        [("BTC", "mock_spot")],
        includes={"ticker", "funding"},
    )
    e = rep.entries[0]
    assert e.funding is None
    assert e.failures == ()  # spot funding 부재는 실패가 아님


def test_collect_all_funding_when_supported():
    c = MarketDataCollector(sources={"perp": MockMarketDataSource("perp", supports_funding=True)})
    rep = c.collect_all(
        [("BTC", "perp")],
        includes={"funding"},
    )
    assert rep.entries[0].funding is not None


def test_collect_all_fx_uses_dedicated_source():
    fx = MockMarketDataSource("fx_mock", supports_fx=True)
    c = MarketDataCollector(
        sources={"mock": MockMarketDataSource("mock")},
        fx_source=fx,
    )
    rep = c.collect_all(
        [("BTC", "mock")],
        includes={"ticker", "fx"},
        fx_pairs=["USDT-KRW", "USD-KRW"],
    )
    assert len(rep.fx_rates) == 2
    pairs = {r.pair for r in rep.fx_rates}
    assert pairs == {"USDT-KRW", "USD-KRW"}


def test_collect_all_empty_pairs_raises():
    """전체 시장 fallback 차단 — 빈 입력은 EmptyWatchlistError."""
    c = MarketDataCollector(sources={"mock": MockMarketDataSource("mock")})
    with pytest.raises(EmptyWatchlistError):
        c.collect_all([])


def test_collect_all_dedup_same_symbol_exchange():
    c = MarketDataCollector(sources={"mock": MockMarketDataSource("mock")})
    rep = c.collect_all(
        [("BTC", "mock"), ("BTC", "mock"), ("ETH", "mock"), ("BTC", "mock")],
    )
    assert rep.requested_pairs == 4
    assert rep.deduped_pairs   == 2
    assert rep.symbol_count    == 2


def test_collect_all_max_symbols_caps_output():
    c = MarketDataCollector(sources={"mock": MockMarketDataSource("mock")})
    pairs = [(f"SYM{i}", "mock") for i in range(50)]
    rep = c.collect_all(pairs, max_symbols=10)
    assert rep.deduped_pairs == 50
    assert rep.truncated_to  == 10
    assert rep.symbol_count  == 10


def test_collect_all_unknown_exchange_isolates_failure():
    """1개 unknown exchange 가 있어도 나머지 수집은 계속된다."""
    c = MarketDataCollector(sources={"mock": MockMarketDataSource("mock")})
    rep = c.collect_all(
        [("BTC", "mock"), ("ETH", "BOGUS"), ("SOL", "mock")],
        includes={"ticker"},
    )
    assert rep.symbol_count == 3
    by_sym = {e.symbol: e for e in rep.entries}
    assert by_sym["BTC"].failures  == ()
    assert by_sym["SOL"].failures  == ()
    assert by_sym["ETH"].failures != ()
    # 한 항목 실패가 전체를 중단하지 않음
    assert rep.success_count == 2
    assert rep.failure_count == 1


def test_collect_all_source_exception_isolated_per_data_type():
    """ticker 가 실패해도 orderbook 수집은 계속되어야 한다."""

    class PartialSource:
        name = "partial"

        def fetch_ticker(self, symbol):
            raise RuntimeError("ticker down")

        def fetch_orderbook(self, symbol, depth=5):
            from datetime import datetime as _dt, timezone as _tz
            return OrderBook(symbol=symbol, bids=(), asks=(),
                             ts=_dt.now(_tz.utc))

    c = MarketDataCollector(sources={"partial": PartialSource()})
    rep = c.collect_all(
        [("BTC", "partial")],
        includes={"ticker", "orderbook"},
    )
    e = rep.entries[0]
    assert e.ticker is None
    assert e.orderbook is not None
    assert any(t == "ticker" for t, _ in e.failures)


def test_collect_all_records_last_status():
    c = MarketDataCollector(sources={"mock": MockMarketDataSource("mock")})
    c.collect_all([("BTC", "mock")], includes={"ticker"}, list_name="default")
    s = c.last_status()
    assert s["last_symbol_count"]  == 1
    assert s["last_success_count"] == 1
    assert s["last_list_name"]     == "default"
    assert s["mode"] == "read-only"


# ── 11. DB 영속화 (persist_report) ───────────────────────────────

def test_persist_report_writes_to_coin_tables(app_with_db):
    """collect_all 결과가 coin_candle / coin_tick / coin_orderbook_snapshot 에 저장."""
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine, select
    from sqlalchemy.pool import StaticPool

    eng = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    Sf = sessionmaker(bind=eng, expire_on_commit=False, future=True)

    c = MarketDataCollector(sources={"mock": MockMarketDataSource("mock")})
    rep = c.collect_all(
        [("BTC", "mock")],
        includes={"ticker", "ohlcv", "orderbook"},
        ohlcv_limit=5,
    )
    with Sf() as s:
        persisted = persist_report(s, rep)
        # 1차 결과
        assert persisted["candles_inserted"]    == 5
        assert persisted["candles_skipped"]     == 0
        assert persisted["ticks_inserted"]      == 1
        assert persisted["orderbooks_inserted"] == 1

        # 2차 재실행 — UNIQUE 충돌 시 skip (동일 ts)
        persisted2 = persist_report(s, rep)
        assert persisted2["candles_inserted"] == 0
        assert persisted2["candles_skipped"]  == 5

        rows = s.execute(select(CoinCandle)).scalars().all()
        assert len(rows) == 5  # 중복은 늘지 않음
        s.execute(select(CoinTick)).scalars().all()
        s.execute(select(CoinOrderbookSnapshot)).scalars().all()
    eng.dispose()


# ── 12. /api/market/collect 확장 (#15) ──────────────────────────

def test_api_collect_with_ohlcv_and_persist(app_with_db):
    from app.core.config import get_settings
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app_with_db)

    client.post("/api/watchlist",
                json={"symbol": "BTC", "exchange": "upbit"}, headers=H)

    r = client.post(
        "/api/market/collect",
        headers=H,
        json={
            "include": ["ticker", "ohlcv", "orderbook"],
            "timeframe": "1m",
            "limit": 10,
            "persist": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["symbol_count"] == 1
    assert body["success_count"] == 1
    assert len(body["entries"][0]["ohlcv"]) == 10
    assert body["entries"][0]["orderbook"] is not None
    # persist=True 면 persisted 카운트 포함
    assert body["persisted"]["candles_inserted"] == 10
    assert body["persisted"]["ticks_inserted"] == 1


def test_api_collect_bad_include_returns_400(app_with_db):
    from app.core.config import get_settings
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app_with_db)
    client.post("/api/watchlist",
                json={"symbol": "BTC", "exchange": "upbit"}, headers=H)
    r = client.post(
        "/api/market/collect",
        headers=H,
        json={"include": ["ticker", "balance"]},
    )
    assert r.status_code == 400


def test_api_collect_funding_only_returns_none_for_spot(app_with_db):
    from app.core.config import get_settings
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app_with_db)
    client.post("/api/watchlist",
                json={"symbol": "BTC", "exchange": "upbit"}, headers=H)
    r = client.post(
        "/api/market/collect",
        headers=H,
        json={"include": ["funding"]},
    )
    assert r.status_code == 200
    body = r.json()
    # spot mock 은 funding=None — 실패 아님
    assert body["failure_count"] == 0
    assert body["entries"][0]["funding"] is None


def test_api_collector_status_public(app_with_db):
    client = TestClient(app_with_db)
    r = client.get("/api/market/collector/status")
    assert r.status_code == 200
    body = r.json()
    assert "sources" in body
    assert "mode" in body
    assert body["mode"] == "read-only"
    # secret 노출 없음 — 단순 키 셋만 확인
    for k in ("api_key", "api_secret", "access_token", "passphrase"):
        assert k not in body


def test_api_tickers_filter_by_list_name(app_with_db):
    from app.core.config import get_settings
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app_with_db)

    client.post("/api/watchlist",
                json={"symbol": "BTC", "exchange": "upbit", "list_name": "majors"},
                headers=H)
    client.post("/api/watchlist",
                json={"symbol": "ETH", "exchange": "okx", "list_name": "default"},
                headers=H)
    client.post("/api/market/collect", headers=H)

    r = client.get("/api/market/tickers?list_name=majors")
    body = r.json()
    syms = {t["symbol"] for t in body["tickers"]}
    assert syms == {"BTC"}


def test_api_collect_max_symbols_truncates(app_with_db, monkeypatch):
    """Settings.market_collector_max_symbols 가 truncated_to 에 반영."""
    monkeypatch.setenv("MARKET_COLLECTOR_MAX_SYMBOLS", "2")
    from app.core.config import reset_settings_cache, get_settings
    reset_settings_cache()
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app_with_db)

    # WATCHLIST_MAX_ENABLED_TOTAL 기본 100 이므로 5개 등록 가능
    for sym in ("BTC", "ETH", "SOL", "XRP", "DOGE"):
        client.post("/api/watchlist",
                    json={"symbol": sym, "exchange": "upbit"}, headers=H)
    r = client.post(
        "/api/market/collect",
        headers=H,
        json={"include": ["ticker", "ohlcv"], "limit": 5, "persist": False},
    )
    assert r.status_code == 200
    body = r.json()
    # 5 개 등록되었으나 max_symbols=2 로 truncate
    assert body["deduped_pairs"] >= 2
    assert body["truncated_to"]  == 2
    assert body["symbol_count"]  == 2
    reset_settings_cache()


# ── 13. Protocol / 정적 금지 검증 (#15 보강) ─────────────────────

def test_market_data_source_protocol_has_no_order_methods():
    """주문/잔고/취소/체결 등 거래 메서드가 Protocol 에 없어야 한다."""
    members = set(dir(MarketDataSource))
    forbidden = (
        "place_order", "cancel_order", "get_balance", "get_account",
        "withdraw", "transfer", "send_order", "create_order",
    )
    leaked = [m for m in forbidden if m in members]
    assert not leaked, f"MarketDataSource Protocol exposes order methods: {leaked}"


_FORBIDDEN_STRINGS = (
    "place_order(",
    "cancel_order(",
    "get_balance(",
    "broker.place_order",
    "OrderExecutor",
    "route_order",
    "KIS_APP_KEY",
    "KIS_APP_SECRET",
    "API_SECRET",
    "ACCESS_TOKEN",
    "ENABLE_LIVE_TRADING = True",
    "ENABLE_AI_EXECUTION = True",
    "ENABLE_CRYPTO_FUTURES_LIVE = True",
)

_PROD_FILES_15 = (
    "backend/app/market/collector.py",
    "backend/app/market/market_persister.py",
    "backend/app/api/market.py",
    "backend/app/schemas/market.py",
)


def test_no_forbidden_strings_in_market_production_files():
    for rel in _PROD_FILES_15:
        p = REPO_ROOT / rel
        assert p.exists(), f"missing: {p}"
        text = p.read_text(encoding="utf-8")
        for needle in _FORBIDDEN_STRINGS:
            assert needle not in text, \
                f"{rel} contains forbidden string: {needle!r}"


def test_collect_all_unknown_include_rejected():
    c = MarketDataCollector(sources={"mock": MockMarketDataSource("mock")})
    with pytest.raises(ValueError):
        c.collect_all([("BTC", "mock")], includes={"ticker", "balance"})


def test_allowed_includes_constant_matches_spec():
    assert ALLOWED_INCLUDES == frozenset(
        {"ticker", "ohlcv", "orderbook", "funding", "fx"}
    )
