"""체크리스트 #23 Binance Adapter — 회귀 테스트.

검증 (네트워크 호출 없음 — fake ccxt client 주입):
  1. 심볼 정규화 (BTC / BTCUSDT / BTC-USDT / BTC/USDT)
  2. capability = READ_ONLY, spot only (supports_futures=False)
  3. API 키 / secret 거부
  4. fetch_ticker — bid/ask/price/volume, timestamp 파싱
  5. fetch_orderbook — depth, 정렬
  6. 빈/누락 응답 안전 처리
  7. ExchangeAdapter contract — MarketDataSource Protocol
  8. collector 직접 주입
  9. 출금 메서드 부재
 10. lazy import 검증
"""
from __future__ import annotations
import pytest

from app.brokers import (
    BinanceAdapter, ExchangeAdapter, ExchangeAdapterDisabledError,
    conforms_to_market_data_source, assert_no_withdrawal_methods,
)
from app.market.collector import MarketDataCollector


# ── Fake ccxt.binance client ─────────────────────────────────────

class FakeCcxtBinance:
    """ccxt.binance 의 fetch_ticker / fetch_order_book 만 흉내내는 fake.

    기본 timestamp 는 호출 시점 — collector freshness 통과를 위해.
    """

    def __init__(
        self,
        prices: dict[str, float] | None = None,
        ts_ms: int | None = None,
    ):
        import time
        self._prices = prices or {"BTC/USDT": 50_000.0,
                                   "ETH/USDT": 3_000.0,
                                   "ETH/BTC":  0.06}
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
            "baseVolume": 100.0,
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
    ("BTCUSDT",     "BTC/USDT"),    # Binance native
    ("ETHUSDC",     "ETH/USDC"),
    ("ETHBTC",      "ETH/BTC"),
    ("BNBETH",      "BNB/ETH"),
    ("ETH/BTC",     "ETH/BTC"),
])
def test_symbol_normalization(inp, expected):
    assert BinanceAdapter.to_binance_symbol(inp) == expected


def test_symbol_native_unknown_quote_falls_back_to_usdt():
    """알려진 quote suffix 가 아니면 통째로 USDT pair 로 간주."""
    # 'XYZ' (3글자, quote 후미 미일치) → XYZ/USDT
    assert BinanceAdapter.to_binance_symbol("XYZ") == "XYZ/USDT"


# ── 2. Capability ────────────────────────────────────────────────

def test_capability_is_read_only_spot():
    a = BinanceAdapter(client=FakeCcxtBinance())
    cap = a.capability
    assert cap.mode == "READ_ONLY"
    assert cap.name == "binance"
    assert cap.can_fetch_ticker is True
    assert cap.can_fetch_orderbook is True
    assert cap.can_fetch_balance is False
    assert cap.can_place_order is False
    assert cap.supports_futures is False  # spot only — 선물은 별도 어댑터(#67)
    assert cap.requires_secret is False


def test_disabled_methods_raise():
    a = BinanceAdapter(client=FakeCcxtBinance())
    with pytest.raises(ExchangeAdapterDisabledError):
        a.fetch_balance()
    with pytest.raises(ExchangeAdapterDisabledError):
        a.place_order({"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 100})


def test_disabled_cancel_returns_rejected():
    a = BinanceAdapter(client=FakeCcxtBinance())
    r = a.cancel_order("xyz")
    assert r.status == "REJECTED"


# ── 3. API 키 거부 ───────────────────────────────────────────────

def test_constructor_rejects_api_key():
    with pytest.raises(ValueError):
        BinanceAdapter(api_key="leak")


def test_constructor_rejects_api_secret():
    with pytest.raises(ValueError):
        BinanceAdapter(api_secret="leak")


def test_constructor_rejects_both():
    with pytest.raises(ValueError):
        BinanceAdapter(api_key="a", api_secret="b")


# ── 4. fetch_ticker ──────────────────────────────────────────────

def test_fetch_ticker_normal():
    fake = FakeCcxtBinance()
    a = BinanceAdapter(client=fake)
    t = a.fetch_ticker("BTC")
    assert t.symbol == "BTC"  # 호출자 입력 형식 유지
    assert t.price == 50_000.0
    assert t.bid > 0
    assert t.ask > t.bid
    assert t.spread_pct > 0
    assert t.volume_24h == 50_000.0 * 100.0


def test_fetch_ticker_uses_normalized_symbol_in_calls():
    fake = FakeCcxtBinance()
    a = BinanceAdapter(client=fake)
    a.fetch_ticker("BTCUSDT")  # native 형식 정규화
    assert ("fetch_ticker", "BTC/USDT") in fake.calls


def test_fetch_ticker_parses_timestamp_to_utc():
    fake = FakeCcxtBinance(ts_ms=1_700_000_000_000)
    a = BinanceAdapter(client=fake)
    t = a.fetch_ticker("BTC")
    assert t.ts.tzinfo is not None
    assert int(t.ts.timestamp() * 1000) == 1_700_000_000_000


def test_fetch_ticker_handles_missing_timestamp():
    class NoTsFake(FakeCcxtBinance):
        def fetch_ticker(self, symbol):
            d = super().fetch_ticker(symbol)
            d.pop("timestamp", None)
            return d
        def fetch_order_book(self, symbol, limit=5):
            d = super().fetch_order_book(symbol, limit)
            d.pop("timestamp", None)
            return d
    a = BinanceAdapter(client=NoTsFake())
    t = a.fetch_ticker("BTC")
    assert t.ts is not None  # now() fallback


# ── 5. fetch_orderbook ───────────────────────────────────────────

def test_fetch_orderbook_returns_correct_depth():
    a = BinanceAdapter(client=FakeCcxtBinance())
    ob = a.fetch_orderbook("BTC", depth=5)
    assert len(ob.bids) == 5
    assert len(ob.asks) == 5


def test_fetch_orderbook_bid_descending_ask_ascending():
    a = BinanceAdapter(client=FakeCcxtBinance())
    ob = a.fetch_orderbook("BTC", depth=5)
    bid_prices = [p for p, _ in ob.bids]
    ask_prices = [p for p, _ in ob.asks]
    assert bid_prices == sorted(bid_prices, reverse=True)
    assert ask_prices == sorted(ask_prices)


def test_fetch_orderbook_for_eth_btc_pair():
    a = BinanceAdapter(client=FakeCcxtBinance())
    ob = a.fetch_orderbook("ETH/BTC", depth=3)
    assert len(ob.bids) == 3


# ── 6. 빈/누락 응답 안전 처리 ────────────────────────────────────

def test_fetch_ticker_raises_on_empty_response():
    a = BinanceAdapter(client=FakeCcxtBinance(prices={}))
    with pytest.raises(RuntimeError):
        a.fetch_ticker("UNKNOWN")


def test_fetch_orderbook_returns_empty_when_unavailable():
    a = BinanceAdapter(client=FakeCcxtBinance(prices={}))
    ob = a.fetch_orderbook("UNKNOWN")
    assert ob.bids == ()
    assert ob.asks == ()


def test_fetch_ticker_handles_missing_bid_ask():
    class NoBidAskFake:
        def fetch_ticker(self, symbol):
            return {"last": 100.0, "close": 100.0, "quoteVolume": 1000.0,
                    "timestamp": 1_700_000_000_000}
        def fetch_order_book(self, symbol, limit=5):
            return {"bids": [], "asks": [], "timestamp": 1_700_000_000_000}

    a = BinanceAdapter(client=NoBidAskFake())
    t = a.fetch_ticker("BTC")
    assert t.price == 100.0
    assert t.bid == 0.0
    assert t.ask == 0.0
    assert t.spread_pct == 0.0


# ── 7. ExchangeAdapter contract ──────────────────────────────────

def test_satisfies_market_data_source_protocol():
    a = BinanceAdapter(client=FakeCcxtBinance())
    assert conforms_to_market_data_source(a) is True


def test_isinstance_exchange_adapter():
    a = BinanceAdapter(client=FakeCcxtBinance())
    assert isinstance(a, ExchangeAdapter)


# ── 8. Collector 통합 ────────────────────────────────────────────

def test_collector_can_use_binance_adapter():
    fake = FakeCcxtBinance(prices={"BTC/USDT": 50_000.0, "ETH/USDT": 3_000.0})
    a = BinanceAdapter(client=fake)
    c = MarketDataCollector(sources={"binance": a})
    report = c.collect([("BTC", "binance"), ("ETH", "binance")])
    assert report.ok_count == 2


def test_collector_multi_exchange_with_okx_and_binance():
    """OKX + Binance 동시 사용. 다중 거래소 collector 패턴 검증."""
    from app.brokers import OkxAdapter
    okx = OkxAdapter(client=__make_fake_okx())
    binance = BinanceAdapter(client=FakeCcxtBinance())
    c = MarketDataCollector(sources={"okx": okx, "binance": binance})
    report = c.collect([
        ("BTC", "okx"),
        ("BTC", "binance"),
        ("ETH", "okx"),
    ])
    assert report.ok_count == 3


def __make_fake_okx():
    """OKX fake 도 동일한 형식이라 재사용 가능 — 별도 import 회피용 헬퍼."""
    from tests.test_okx_adapter import FakeCcxtOkx  # type: ignore[import-not-found]
    return FakeCcxtOkx()


# ── 9. 출금 메서드 부재 ──────────────────────────────────────────

def test_no_withdrawal_methods_on_binance_adapter():
    assert_no_withdrawal_methods(BinanceAdapter)


# ── 10. lazy import 검증 ─────────────────────────────────────────

def test_no_top_level_ccxt_import():
    """import 시점에 ccxt 가 강제되면 안 됨."""
    import importlib, sys
    saved = sys.modules.pop("ccxt", None)
    try:
        importlib.reload(importlib.import_module("app.brokers.binance_adapter"))
        assert "ccxt" not in sys.modules, \
            "binance_adapter 모듈 import 만으로 ccxt 가 import 되면 안 됨"
    finally:
        if saved is not None:
            sys.modules["ccxt"] = saved
