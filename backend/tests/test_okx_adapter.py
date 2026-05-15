"""체크리스트 #22 OKX Adapter — 회귀 테스트.

검증 (네트워크 호출 없음 — fake ccxt client 주입):
  1. 심볼 정규화 (BTC / BTC-USDT / BTC/USDT 등)
  2. capability = READ_ONLY, 주문/잔고 disabled
  3. API 키 / passphrase 주입 거부
  4. fetch_ticker — bid/ask/price/volume 매핑, 타임스탬프 파싱
  5. fetch_orderbook — depth, bid 내림차순/ask 오름차순
  6. 빈 응답 / 누락 필드 안전 처리
  7. ExchangeAdapter contract — MarketDataSource Protocol
  8. collector 직접 주입
  9. 출금 메서드 부재
 10. 모듈 import 시 ccxt lazy 검증
"""
from __future__ import annotations
import pytest

from app.brokers import (
    OkxAdapter, ExchangeAdapter, ExchangeAdapterDisabledError,
    conforms_to_market_data_source, assert_no_withdrawal_methods,
)
from app.market.collector import MarketDataCollector


# ── Fake ccxt.okx client (네트워크 차단) ─────────────────────────

class FakeCcxtOkx:
    """ccxt.okx 의 fetch_ticker / fetch_order_book 만 흉내내는 결정론적 fake.

    기본 timestamp 는 호출 시점 — collector freshness 통과를 위해. 특정 시각이
    필요한 테스트는 ``ts_ms=...`` 로 명시 주입.
    """

    def __init__(
        self,
        prices: dict[str, float] | None = None,
        ts_ms: int | None = None,
    ):
        import time
        self._prices = prices or {"BTC/USDT": 50_000.0, "ETH/USDT": 3_000.0}
        self._ts_ms = ts_ms if ts_ms is not None else int(time.time() * 1000)
        self.calls: list[tuple[str, str]] = []

    def fetch_ticker(self, symbol: str) -> dict:
        self.calls.append(("fetch_ticker", symbol))
        price = self._prices.get(symbol)
        if price is None:
            return {}
        bid, ask = price * 0.9995, price * 1.0005
        return {
            "symbol": symbol,
            "timestamp": self._ts_ms,
            "datetime": "2026-05-10T00:00:00.000Z",
            "high": price * 1.02, "low": price * 0.98,
            "bid": bid, "ask": ask, "last": price, "close": price,
            "baseVolume":  100.0,
            "quoteVolume": price * 100.0,
        }

    def fetch_order_book(self, symbol: str, limit: int = 5) -> dict:
        self.calls.append(("fetch_order_book", symbol))
        price = self._prices.get(symbol)
        if price is None:
            return {"bids": [], "asks": [], "timestamp": self._ts_ms}
        bids = [[price * (1 - 0.0005 * (i + 1)), 1.0 + 0.1 * i] for i in range(limit)]
        asks = [[price * (1 + 0.0005 * (i + 1)), 1.0 + 0.1 * i] for i in range(limit)]
        return {
            "symbol": symbol,
            "bids": bids, "asks": asks,
            "timestamp": self._ts_ms,
        }


# ── 1. 심볼 정규화 ───────────────────────────────────────────────

@pytest.mark.parametrize("inp,expected", [
    ("BTC",         "BTC/USDT"),
    ("btc",         "BTC/USDT"),
    ("BTC/USDT",    "BTC/USDT"),
    ("BTC-USDT",    "BTC/USDT"),
    ("ETH-USDT",    "ETH/USDT"),
    ("BTC/USD",     "BTC/USD"),     # 다른 quote 보존
    ("ETH-BTC",     "ETH/BTC"),
])
def test_symbol_normalization(inp, expected):
    assert OkxAdapter.to_okx_symbol(inp) == expected


# ── 2. Capability ────────────────────────────────────────────────

def test_capability_is_read_only():
    a = OkxAdapter(client=FakeCcxtOkx())
    cap = a.capability
    assert cap.mode == "READ_ONLY"
    assert cap.can_fetch_ticker is True
    assert cap.can_fetch_orderbook is True
    assert cap.can_fetch_balance is False
    assert cap.can_place_order is False
    assert cap.requires_secret is False


def test_disabled_methods_raise():
    a = OkxAdapter(client=FakeCcxtOkx())
    with pytest.raises(ExchangeAdapterDisabledError):
        a.fetch_balance()
    with pytest.raises(ExchangeAdapterDisabledError):
        a.place_order({"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 100})


def test_disabled_cancel_returns_rejected():
    a = OkxAdapter(client=FakeCcxtOkx())
    r = a.cancel_order("xyz")
    assert r.status == "REJECTED"


# ── 3. API 키 거부 ───────────────────────────────────────────────

def test_constructor_rejects_api_key():
    with pytest.raises(ValueError):
        OkxAdapter(api_key="leak")


def test_constructor_rejects_api_secret():
    with pytest.raises(ValueError):
        OkxAdapter(api_secret="leak")


def test_constructor_rejects_api_password():
    with pytest.raises(ValueError):
        OkxAdapter(api_password="leak")


def test_constructor_rejects_all_three():
    with pytest.raises(ValueError):
        OkxAdapter(api_key="a", api_secret="b", api_password="c")


# ── 4. fetch_ticker ──────────────────────────────────────────────

def test_fetch_ticker_normal():
    fake = FakeCcxtOkx()
    a = OkxAdapter(client=fake)
    t = a.fetch_ticker("BTC")
    assert t.symbol == "BTC"  # 호출자 입력 형식 유지
    assert t.price == 50_000.0
    assert t.bid > 0
    assert t.ask > t.bid
    assert t.spread_pct > 0
    assert t.volume_24h == 50_000.0 * 100.0  # quoteVolume 매핑


def test_fetch_ticker_uses_normalized_symbol_in_calls():
    fake = FakeCcxtOkx()
    a = OkxAdapter(client=fake)
    a.fetch_ticker("BTC-USDT")
    assert ("fetch_ticker", "BTC/USDT") in fake.calls


def test_fetch_ticker_parses_timestamp_to_utc():
    fake = FakeCcxtOkx(ts_ms=1_700_000_000_000)
    a = OkxAdapter(client=fake)
    t = a.fetch_ticker("BTC")
    assert t.ts.tzinfo is not None
    assert int(t.ts.timestamp() * 1000) == 1_700_000_000_000


def test_fetch_ticker_handles_missing_timestamp():
    class NoTsFake(FakeCcxtOkx):
        def fetch_ticker(self, symbol):
            d = super().fetch_ticker(symbol)
            d.pop("timestamp", None)
            return d
        def fetch_order_book(self, symbol, limit=5):
            d = super().fetch_order_book(symbol, limit)
            d.pop("timestamp", None)
            return d
    a = OkxAdapter(client=NoTsFake())
    t = a.fetch_ticker("BTC")
    assert t.ts is not None  # now() fallback


# ── 5. fetch_orderbook ───────────────────────────────────────────

def test_fetch_orderbook_returns_correct_depth():
    a = OkxAdapter(client=FakeCcxtOkx())
    ob = a.fetch_orderbook("BTC", depth=5)
    assert len(ob.bids) == 5
    assert len(ob.asks) == 5


def test_fetch_orderbook_bid_descending_ask_ascending():
    a = OkxAdapter(client=FakeCcxtOkx())
    ob = a.fetch_orderbook("BTC", depth=5)
    bid_prices = [p for p, _ in ob.bids]
    ask_prices = [p for p, _ in ob.asks]
    assert bid_prices == sorted(bid_prices, reverse=True)
    assert ask_prices == sorted(ask_prices)


# ── 6. 빈/누락 응답 안전 처리 ────────────────────────────────────

def test_fetch_ticker_raises_on_empty_response():
    a = OkxAdapter(client=FakeCcxtOkx(prices={}))
    with pytest.raises(RuntimeError):
        a.fetch_ticker("UNKNOWN")


def test_fetch_orderbook_returns_empty_when_unavailable():
    a = OkxAdapter(client=FakeCcxtOkx(prices={}))
    ob = a.fetch_orderbook("UNKNOWN")
    assert ob.bids == ()
    assert ob.asks == ()


def test_fetch_ticker_handles_missing_bid_ask():
    """ccxt 일부 응답에 bid/ask 가 None 일 수 있음."""

    class NoBidAskFake:
        def fetch_ticker(self, symbol):
            return {"last": 100.0, "close": 100.0, "quoteVolume": 1000.0,
                    "timestamp": 1_700_000_000_000}
        def fetch_order_book(self, symbol, limit=5):
            return {"bids": [], "asks": [], "timestamp": 1_700_000_000_000}

    a = OkxAdapter(client=NoBidAskFake())
    t = a.fetch_ticker("BTC")
    assert t.price == 100.0
    assert t.bid == 0.0
    assert t.ask == 0.0
    assert t.spread_pct == 0.0  # bid=0 → safe path


# ── 7. ExchangeAdapter contract ──────────────────────────────────

def test_satisfies_market_data_source_protocol():
    a = OkxAdapter(client=FakeCcxtOkx())
    assert conforms_to_market_data_source(a) is True


def test_isinstance_exchange_adapter():
    a = OkxAdapter(client=FakeCcxtOkx())
    assert isinstance(a, ExchangeAdapter)


# ── 8. Collector 통합 ────────────────────────────────────────────

def test_collector_can_use_okx_adapter():
    fake = FakeCcxtOkx(prices={"BTC/USDT": 50_000.0, "ETH/USDT": 3_000.0})
    a = OkxAdapter(client=fake)
    c = MarketDataCollector(sources={"okx": a})
    report = c.collect([("BTC", "okx"), ("ETH", "okx")])
    assert report.ok_count == 2


# ── 9. 출금 메서드 부재 ──────────────────────────────────────────

def test_no_withdrawal_methods_on_okx_adapter():
    assert_no_withdrawal_methods(OkxAdapter)


# ── 10. lazy import 검증 ─────────────────────────────────────────

def test_no_top_level_ccxt_import():
    """import 시점에 ccxt 가 강제되면 안 됨."""
    import importlib, sys
    saved = sys.modules.pop("ccxt", None)
    try:
        importlib.reload(importlib.import_module("app.brokers.okx_adapter"))
        assert "ccxt" not in sys.modules, \
            "okx_adapter 모듈 import 만으로 ccxt 가 import 되면 안 됨"
    finally:
        if saved is not None:
            sys.modules["ccxt"] = saved
