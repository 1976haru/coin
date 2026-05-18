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


# ─────────────────────────────────────────────────────────────────
# 체크리스트 #23 확장 — public client / rate limit / account / trade stubs
# ─────────────────────────────────────────────────────────────────

import re
from pathlib import Path

from app.brokers import (
    BinancePublicClient, BinancePublicAPIError, BinanceTransportResponse,
    BinanceAccountClient, BinanceAccountPermissionError,
    BinanceTradeClient,
    BinanceRateLimitState,
    BINANCE_PUBLIC_DATA_HOST, BINANCE_ALLOWED_KLINE_INTERVALS,
    BINANCE_WEIGHT_SOFT_LIMIT,
    parse_binance_used_weight, should_throttle_binance,
    normalize_binance_symbol, binance_to_internal_symbol,
    is_supported_binance_quote,
)


# ── A. symbol 정규화 — module-level helpers ─────────────────────

def test_module_normalize_binance_symbol_basic():
    assert normalize_binance_symbol("BTC") == "BTCUSDT"
    assert normalize_binance_symbol("btc") == "BTCUSDT"
    assert normalize_binance_symbol("BTC-USDT") == "BTCUSDT"
    assert normalize_binance_symbol("BTC/USDT") == "BTCUSDT"
    assert normalize_binance_symbol("BTCUSDT") == "BTCUSDT"
    assert normalize_binance_symbol("btcusdt") == "BTCUSDT"


def test_normalize_rejects_empty():
    with pytest.raises(ValueError):
        normalize_binance_symbol("")
    with pytest.raises(ValueError):
        normalize_binance_symbol("   ")
    with pytest.raises(ValueError):
        normalize_binance_symbol(None)  # type: ignore[arg-type]


def test_normalize_rejects_perp_or_futures():
    with pytest.raises(ValueError):
        normalize_binance_symbol("BTCUSDT-PERP")
    with pytest.raises(ValueError):
        normalize_binance_symbol("BTCUSDT_PERP")


def test_binance_to_internal_symbol():
    assert binance_to_internal_symbol("BTCUSDT") == "BTC-USDT"
    assert binance_to_internal_symbol("ETHUSDC") == "ETH-USDC"
    assert binance_to_internal_symbol("BNBETH") == "BNB-ETH"
    assert binance_to_internal_symbol("BTC-USDT") == "BTC-USDT"
    assert binance_to_internal_symbol("BTC/USDT") == "BTC-USDT"
    # 알 수 없는 quote 후미 — 그대로 반환
    assert binance_to_internal_symbol("ABCXYZ") == "ABCXYZ"


def test_is_supported_binance_quote_defaults():
    assert is_supported_binance_quote("BTC-USDT") is True
    assert is_supported_binance_quote("BTCUSDT") is True
    assert is_supported_binance_quote("ETHBTC") is True
    # 알 수 없는 quote — false
    assert is_supported_binance_quote("ABCXYZ") is False
    assert is_supported_binance_quote("") is False


def test_is_supported_binance_quote_custom_list():
    assert is_supported_binance_quote("BTCUSDT", ["USDT"]) is True
    assert is_supported_binance_quote("BTCUSDC", ["USDT"]) is False


# ── B. Rate limit / used weight 헤더 ────────────────────────────

def test_parse_used_weight_basic():
    headers = {"X-MBX-USED-WEIGHT-1M": "23",
               "X-MBX-USED-WEIGHT": "23",
               "X-MBX-ORDER-COUNT-10S": "0",
               "X-MBX-ORDER-COUNT-1M": "5"}
    out = parse_binance_used_weight(headers)
    assert out["used_weight_1m"] == 23
    assert out["order_count_10s"] == 0
    assert out["order_count_1m"] == 5


def test_parse_used_weight_case_insensitive():
    headers = {"x-mbx-used-weight": "100"}
    out = parse_binance_used_weight(headers)
    assert out["used_weight_1m"] == 100


def test_parse_used_weight_none_safe():
    assert parse_binance_used_weight(None) == {}
    assert parse_binance_used_weight({}) == {}
    assert parse_binance_used_weight({"foo": "bar"}) == {}


def test_parse_used_weight_malformed_value_skipped():
    out = parse_binance_used_weight({"X-MBX-USED-WEIGHT": "not-an-int"})
    assert "used_weight_1m" not in out


def test_should_throttle_binance_at_soft_limit():
    state = {"used_weight_1m": BINANCE_WEIGHT_SOFT_LIMIT}
    assert should_throttle_binance(state) is True
    state2 = {"used_weight_1m": BINANCE_WEIGHT_SOFT_LIMIT - 1}
    assert should_throttle_binance(state2) is False


def test_should_throttle_binance_empty_false():
    assert should_throttle_binance({}) is False


def test_rate_limit_state_sleep_injection():
    slept: list[float] = []
    state = BinanceRateLimitState(sleep_fn=lambda s: slept.append(s))
    state.update({"X-MBX-USED-WEIGHT-1M": str(BINANCE_WEIGHT_SOFT_LIMIT + 10)})
    assert state.used_weight_1m is not None
    assert state.maybe_throttle(sleep_seconds=0.05) is True
    assert slept == [0.05]
    assert state.throttle_count == 1


def test_rate_limit_state_no_throttle_when_safe():
    state = BinanceRateLimitState()
    state.update({"X-MBX-USED-WEIGHT": "10"})
    assert state.maybe_throttle() is False


# ── C. BinancePublicClient — FakeTransport ──────────────────────

class _FakeBinanceTransport:
    def __init__(self, responses: dict[str, BinanceTransportResponse] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict, dict]] = []

    def __call__(self, method, path, params=None, headers=None):
        self.calls.append((method, path, dict(params or {}), dict(headers or {})))
        if path in self.responses:
            return self.responses[path]
        return BinanceTransportResponse(
            status_code=200, body={},
            headers={"X-MBX-USED-WEIGHT-1M": "1"},
        )


def _ok(body, *, used_weight: int = 1):
    return BinanceTransportResponse(
        status_code=200, body=body,
        headers={"X-MBX-USED-WEIGHT-1M": str(used_weight)},
    )


def test_public_client_raises_without_transport():
    c = BinancePublicClient()
    with pytest.raises(RuntimeError):
        c.fetch_server_time()


def test_public_client_fetch_server_time():
    t = _FakeBinanceTransport({
        "/api/v3/time": _ok({"serverTime": 1_700_000_000_000}),
    })
    c = BinancePublicClient(transport=t)
    assert c.fetch_server_time() == 1_700_000_000_000
    assert t.calls[0][1] == "/api/v3/time"


def test_public_client_fetch_exchange_info():
    t = _FakeBinanceTransport({
        "/api/v3/exchangeInfo": _ok({
            "symbols": [
                {"symbol": "BTCUSDT", "status": "TRADING",
                 "baseAsset": "BTC", "quoteAsset": "USDT",
                 "isSpotTradingAllowed": True},
                {"symbol": "ETHUSDT", "status": "TRADING",
                 "baseAsset": "ETH", "quoteAsset": "USDT",
                 "isSpotTradingAllowed": True},
            ],
        }),
    })
    c = BinancePublicClient(transport=t)
    out = c.fetch_exchange_info()
    assert {x["symbol"] for x in out} == {"BTCUSDT", "ETHUSDT"}


def test_public_client_fetch_ticker():
    t = _FakeBinanceTransport({
        "/api/v3/ticker/24hr": _ok({
            "symbol": "BTCUSDT", "lastPrice": "50000.0",
            "bidPrice": "49990.0", "askPrice": "50010.0",
            "highPrice": "51000", "lowPrice": "49000",
            "volume": "100", "quoteVolume": "5000000",
            "openTime": 1_700_000_000_000, "closeTime": 1_700_000_000_100,
        }),
    })
    c = BinancePublicClient(transport=t)
    tk = c.fetch_ticker("BTCUSDT")
    assert tk["last_price"] == 50000.0
    assert tk["bid_price"] < tk["ask_price"]


def test_public_client_fetch_orderbook_best_bid_lt_ask():
    t = _FakeBinanceTransport({
        "/api/v3/depth": _ok({
            "lastUpdateId": 1234,
            "bids": [["49990", "1"], ["49980", "2"]],
            "asks": [["50010", "1"], ["50020", "2"]],
        }),
    })
    c = BinancePublicClient(transport=t)
    ob = c.fetch_orderbook("BTCUSDT", limit=5)
    assert ob["bids"][0][0] < ob["asks"][0][0]


def test_public_client_orderbook_rejects_unsupported_limit():
    c = BinancePublicClient(transport=_FakeBinanceTransport())
    with pytest.raises(ValueError):
        c.fetch_orderbook("BTCUSDT", limit=7)


def test_public_client_fetch_klines():
    t = _FakeBinanceTransport({
        "/api/v3/klines": _ok([
            [1700000000000, "50000", "50100", "49900", "50050", "10",
             1700000060000, "500000", 100],
            [1700000060000, "50050", "50200", "50000", "50150", "12",
             1700000120000, "600000", 110],
        ]),
    })
    c = BinancePublicClient(transport=t)
    out = c.fetch_klines("BTCUSDT", interval="1m", limit=2)
    assert len(out) == 2
    assert out[0]["close"] == 50050.0


def test_public_client_klines_rejects_unknown_interval():
    c = BinancePublicClient(transport=_FakeBinanceTransport())
    with pytest.raises(ValueError):
        c.fetch_klines("BTCUSDT", interval="7m")
    assert "1m" in BINANCE_ALLOWED_KLINE_INTERVALS


def test_public_client_rejects_non_public_path():
    """transport 가 어쩌다 private path 로 호출되어도 client 가 차단."""
    c = BinancePublicClient(transport=_FakeBinanceTransport())
    with pytest.raises(BinancePublicAPIError):
        c._call("/api/v3/order")
    with pytest.raises(BinancePublicAPIError):
        c._call("/api/v3/account")


def test_public_client_status_4xx_raises():
    t = _FakeBinanceTransport({
        "/api/v3/time": BinanceTransportResponse(
            status_code=429, body={"code": -1003, "msg": "Too many requests"},
            headers={},
        ),
    })
    c = BinancePublicClient(transport=t)
    with pytest.raises(BinancePublicAPIError):
        c.fetch_server_time()


def test_public_client_updates_rate_limit_state():
    t = _FakeBinanceTransport({
        "/api/v3/time": _ok({"serverTime": 1_700_000_000_000}, used_weight=500),
    })
    c = BinancePublicClient(transport=t)
    c.fetch_server_time()
    assert c.rate_limit.used_weight_1m == 500


def test_public_client_invalid_symbol_rejected():
    c = BinancePublicClient(transport=_FakeBinanceTransport())
    with pytest.raises(ValueError):
        c.fetch_ticker("BTC/USDT")  # slash forbidden in native
    with pytest.raises(ValueError):
        c.fetch_ticker("BTC-USDT")  # dash forbidden in native


def test_public_data_host_constant():
    """공개 데이터 전용 host 가 코드에서 식별 가능."""
    assert "binance" in BINANCE_PUBLIC_DATA_HOST


# ── D. BinanceAdapter via BinancePublicClient ───────────────────

def test_adapter_uses_public_client_when_injected():
    t = _FakeBinanceTransport({
        "/api/v3/ticker/24hr": _ok({
            "symbol": "BTCUSDT", "lastPrice": "50000.0",
            "bidPrice": "49990.0", "askPrice": "50010.0",
            "quoteVolume": "5000000",
            "openTime": 0, "closeTime": 1_700_000_000_000,
        }),
        "/api/v3/depth": _ok({
            "lastUpdateId": 1, "bids": [["49990", "1"]], "asks": [["50010", "1"]],
        }),
    })
    pc = BinancePublicClient(transport=t)
    a = BinanceAdapter(public_client=pc)
    tk = a.fetch_ticker("BTC")
    assert tk.price == 50000.0
    assert tk.bid < tk.ask
    ob = a.fetch_orderbook("BTC", depth=5)
    assert ob.bids[0][0] == 49990.0


def test_adapter_public_client_routes_native_symbol():
    t = _FakeBinanceTransport({
        "/api/v3/ticker/24hr": _ok({
            "symbol": "BTCUSDT", "lastPrice": "1.0",
            "bidPrice": "0.99", "askPrice": "1.01",
            "openTime": 0, "closeTime": 0,
        }),
        "/api/v3/depth": _ok({"bids": [], "asks": []}),
    })
    pc = BinancePublicClient(transport=t)
    a = BinanceAdapter(public_client=pc)
    a.fetch_ticker("BTC-USDT")
    assert any(call[2].get("symbol") == "BTCUSDT" for call in t.calls)


# ── E. BinanceAccountClient — 모두 disabled ─────────────────────

def test_account_client_all_methods_disabled_without_credentials():
    c = BinanceAccountClient()
    assert c.credentials_loaded is False
    with pytest.raises(BinanceAccountPermissionError):
        c.fetch_balances()
    with pytest.raises(BinanceAccountPermissionError):
        c.fetch_account_info()
    with pytest.raises(BinanceAccountPermissionError):
        c.fetch_open_orders(symbol="BTCUSDT")


def test_account_client_disabled_even_with_credentials():
    """본 단계는 read-only research/skeleton — credentials 가 있어도 disabled."""
    c = BinanceAccountClient(api_key="x", api_secret="y", transport=object())
    assert c.credentials_loaded is False  # 보관 안 함
    with pytest.raises(BinanceAccountPermissionError):
        c.fetch_balances()


def test_account_client_repr_does_not_leak_credentials():
    c = BinanceAccountClient(
        api_key="dummy_key_a",
        api_secret="dummy_sec_b",
    )
    r = repr(c)
    assert "dummy_key_a" not in r
    assert "dummy_sec_b" not in r
    assert "disabled" in r.lower()
    assert "regulatory" in r.lower()


def test_account_client_has_no_withdrawal_methods():
    assert_no_withdrawal_methods(BinanceAccountClient)


# ── F. BinanceTradeClient — 모두 disabled ───────────────────────

def test_trade_client_all_operations_disabled():
    o = BinanceTradeClient()
    with pytest.raises(ExchangeAdapterDisabledError):
        o.place_order(symbol="BTCUSDT", side="BUY", quantity=0.001)
    with pytest.raises(ExchangeAdapterDisabledError):
        o.cancel_order(symbol="BTCUSDT", orderId="x")
    with pytest.raises(ExchangeAdapterDisabledError):
        o.get_order(symbol="BTCUSDT", orderId="x")


def test_trade_client_disabled_reason_mentions_regulatory():
    o = BinanceTradeClient()
    try:
        o.place_order(symbol="BTCUSDT", side="BUY", quantity=0.001)
    except ExchangeAdapterDisabledError as e:
        assert "regulatory" in str(e).lower()
    assert o.DISABLED_REASON == "binance_live_trading_disabled_until_regulatory_review"


def test_trade_client_ignores_credentials():
    o = BinanceTradeClient(
        api_key="dummy_key_a",
        api_secret="dummy_sec_b",
        transport=object(),
    )
    r = repr(o)
    assert "dummy_key_a" not in r
    assert "dummy_sec_b" not in r
    assert "disabled" in r.lower()


def test_trade_client_capability_all_false():
    cap = BinanceTradeClient.capability
    d = cap.to_dict()
    for k in ("can_place_order", "can_cancel_order", "can_get_order",
              "can_set_leverage", "can_set_margin_type",
              "can_trade_futures", "can_trade_margin"):
        assert d[k] is False
    assert "regulatory" in d["note"].lower()


def test_trade_client_has_no_withdrawal_methods():
    assert_no_withdrawal_methods(BinanceTradeClient)


# ── G. BinanceAdapter LIVE-mode order rejection ─────────────────

def test_adapter_place_order_disabled_even_with_live_dict():
    """READ_ONLY adapter 는 mode='LIVE' 가 와도 capability false 라 즉시 disabled."""
    a = BinanceAdapter(client=FakeCcxtBinance())
    with pytest.raises(ExchangeAdapterDisabledError):
        a.place_order({
            "symbol": "BTCUSDT", "side": "BUY",
            "order_type": "MARKET", "notional_usdt": 100,
            "mode": "LIVE",
        })


# ── H. 단일 주문 경로 — Strategy/Agent 직접 호출 부재 ───────────

_REPO_BACKEND_APP = Path(__file__).resolve().parent.parent / "app"


def _scan(directory, pattern, glob="**/*.py"):
    hits = []
    for p in directory.glob(glob):
        if "__pycache__" in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            hits.append(p)
    return hits


def test_strategies_do_not_import_binance_module():
    pat = re.compile(
        r"(?:from|import)\s+app\.brokers\.(?:binance_adapter|binance_public|"
        r"binance_account|binance_trade|binance_rate_limit)",
    )
    hits = _scan(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy imports binance module: {hits}"


def test_agents_do_not_import_binance_module():
    pat = re.compile(
        r"(?:from|import)\s+app\.brokers\.(?:binance_adapter|binance_public|"
        r"binance_account|binance_trade|binance_rate_limit)",
    )
    whitelist = {"compliance.py"}
    hits = [p for p in _scan(_REPO_BACKEND_APP / "agents", pat)
            if p.name not in whitelist]
    assert not hits, f"agent imports binance module: {hits}"


def test_strategies_no_binance_client_instantiation():
    pat = re.compile(
        r"BinanceAdapter\s*\(|BinancePublicClient\s*\(|"
        r"BinanceAccountClient\s*\(|BinanceTradeClient\s*\(",
    )
    hits = _scan(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy instantiates binance client: {hits}"


def test_agents_no_binance_client_instantiation():
    pat = re.compile(
        r"BinanceAdapter\s*\(|BinancePublicClient\s*\(|"
        r"BinanceAccountClient\s*\(|BinanceTradeClient\s*\(",
    )
    hits = _scan(_REPO_BACKEND_APP / "agents", pat)
    assert not hits, f"agent instantiates binance client: {hits}"


# ── I. production 정적 금지 ────────────────────────────────────

_BINANCE_MODULES = (
    "binance_adapter.py", "binance_public.py",
    "binance_account.py", "binance_trade.py",
    "binance_rate_limit.py",
)


def test_binance_modules_no_forbidden_substrings():
    forbidden = (
        "ENABLE_LIVE_TRADING = True",
        "ENABLE_AI_EXECUTION = True",
        "ENABLE_CRYPTO_FUTURES_LIVE = True",
        # signing 구현
        "X-MBX-APIKEY",
        "hmac.new",
        # SDK import (정적 차단)
        "from binance ",
        "import binance",
    )
    for fname in _BINANCE_MODULES:
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(
            encoding="utf-8", errors="ignore",
        )
        for needle in forbidden:
            assert needle not in text, f"{fname} contains {needle!r}"


def test_binance_modules_no_real_trade_endpoint_literal():
    """실제 trade/account endpoint URL literal 부재."""
    forbidden = (
        "/api/v3/order",
        "/api/v3/openOrders",
        "/sapi/v1/margin",
        "/fapi/v1/order",
        "/fapi/v1/leverage",
        "/fapi/v1/marginType",
        "/sapi/v1/capital/withdraw",
        # 본 단계에서 account 도 stub 만 — endpoint literal 부재
        "/api/v3/account",
    )
    for fname in _BINANCE_MODULES:
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(
            encoding="utf-8", errors="ignore",
        )
        for needle in forbidden:
            assert needle not in text, f"{fname} contains {needle!r}"


def test_binance_modules_no_signing_imports():
    pat = re.compile(r"^\s*(?:import\s+(?:jwt|hmac)|from\s+(?:jwt|hmac))", re.M)
    for fname in _BINANCE_MODULES:
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(
            encoding="utf-8", errors="ignore",
        )
        assert not pat.search(text), f"{fname} imports jwt/hmac"


def test_binance_modules_no_requests_or_httpx_imports():
    pat = re.compile(
        r"^\s*(?:import\s+(?:requests|httpx)|from\s+(?:requests|httpx))",
        re.M,
    )
    for fname in _BINANCE_MODULES:
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(
            encoding="utf-8", errors="ignore",
        )
        assert not pat.search(text), f"{fname} imports requests/httpx"


def test_binance_public_module_no_ccxt_import():
    """binance_public.py 는 transport-기반 — ccxt 의존 없음."""
    pat = re.compile(r"^\s*(?:import\s+ccxt|from\s+ccxt)", re.M)
    text = (_REPO_BACKEND_APP / "brokers" / "binance_public.py").read_text(
        encoding="utf-8", errors="ignore",
    )
    assert not pat.search(text)


def test_binance_modules_no_python_binance_or_connector():
    """python-binance / binance-connector SDK import 부재."""
    pat = re.compile(
        r"(?:from|import)\s+(?:binance|binance_connector|binance\.client)",
    )
    for fname in _BINANCE_MODULES:
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(
            encoding="utf-8", errors="ignore",
        )
        assert not pat.search(text), f"{fname} imports python-binance/binance-connector"


def test_frontend_has_no_binance_secret_assignment():
    fe = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
    if not fe.exists():
        pytest.skip("frontend/src not present")
    pat = re.compile(
        r"BINANCE_API_KEY|BINANCE_SECRET_KEY|"
        r"API_SECRET|ACCESS_TOKEN",
    )
    hits = []
    for p in fe.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in {".ts", ".tsx", ".js", ".jsx", ".env"}:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if pat.search(text):
            hits.append(p)
    assert not hits, f"frontend leaks binance secret reference: {hits}"


# ── J. brokers __all__ exports ─────────────────────────────────

def test_brokers_module_exports_binance_helpers():
    from app import brokers
    for name in (
        "BinancePublicClient", "BinanceAccountClient", "BinanceTradeClient",
        "BinanceRateLimitState",
        "normalize_binance_symbol", "binance_to_internal_symbol",
        "is_supported_binance_quote",
        "parse_binance_used_weight", "should_throttle_binance",
        "BINANCE_PUBLIC_DATA_HOST", "BINANCE_ALLOWED_KLINE_INTERVALS",
        "BINANCE_WEIGHT_SOFT_LIMIT",
    ):
        assert name in brokers.__all__, f"{name} not exported"
        assert hasattr(brokers, name)


# ── K. collector — 시세만 호출되고 주문 메서드는 호출되지 않음 ──

def test_collector_with_binance_adapter_does_not_invoke_orders():
    fake = FakeCcxtBinance(prices={"BTC/USDT": 50_000.0})
    a = BinanceAdapter(client=fake)
    c = MarketDataCollector(sources={"binance": a})
    report = c.collect([("BTC", "binance")])
    assert report.ok_count == 1
    methods = {m for m, _ in fake.calls}
    assert methods.issubset({"fetch_ticker", "fetch_order_book"})
    assert a.capability.can_place_order is False
