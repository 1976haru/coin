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


# ─────────────────────────────────────────────────────────────────
# 체크리스트 #22 확장 — public client / rate limit / account / trade
# ─────────────────────────────────────────────────────────────────

import re
from pathlib import Path

from app.brokers import (
    OkxPublicClient, OkxPublicAPIError, OkxTransportResponse,
    OkxAccountClient, OkxAccountPermissionError, OkxAccountTransportResponse,
    OkxTradeClient, OkxPaperOrderClient,
    OkxApiError, OkxRateLimitState,
    parse_okx_api_error, is_okx_rate_limit_error, should_throttle_okx,
    OKX_RATE_LIMIT_CODES, OKX_ALLOWED_INST_TYPES, OKX_ALLOWED_BARS,
    normalize_okx_inst_id, infer_okx_inst_type, okx_to_internal_symbol,
)


# ── A. instrument 정규화 ───────────────────────────────────────

def test_normalize_inst_id_default_spot():
    assert normalize_okx_inst_id("BTC") == "BTC-USDT"
    assert normalize_okx_inst_id("btc") == "BTC-USDT"
    assert normalize_okx_inst_id("BTC-USDT") == "BTC-USDT"
    assert normalize_okx_inst_id("btc-usdt") == "BTC-USDT"
    assert normalize_okx_inst_id("BTC/USDT") == "BTC-USDT"


def test_normalize_inst_id_swap_suffix():
    assert normalize_okx_inst_id("BTC-USDT-SWAP") == "BTC-USDT-SWAP"
    assert normalize_okx_inst_id("btc-usdt-swap") == "BTC-USDT-SWAP"


def test_normalize_inst_id_with_explicit_swap_type():
    assert normalize_okx_inst_id("BTC", instrument_type="SWAP") == "BTC-USDT-SWAP"
    assert normalize_okx_inst_id("BTC-USDT", instrument_type="SWAP") == "BTC-USDT-SWAP"


def test_normalize_inst_id_rejects_empty():
    with pytest.raises(ValueError):
        normalize_okx_inst_id("")
    with pytest.raises(ValueError):
        normalize_okx_inst_id("   ")
    with pytest.raises(ValueError):
        normalize_okx_inst_id(None)  # type: ignore[arg-type]


def test_normalize_inst_id_rejects_option():
    """OPTION (예: BTC-USD-260626-50000-C) 은 본 단계 미지원."""
    with pytest.raises(ValueError):
        normalize_okx_inst_id("BTC-USD-260626-50000-C")
    with pytest.raises(ValueError):
        normalize_okx_inst_id("BTC-USD-260626-50000-P")


def test_infer_inst_type():
    assert infer_okx_inst_type("BTC-USDT") == "SPOT"
    assert infer_okx_inst_type("BTC-USDT-SWAP") == "SWAP"
    assert infer_okx_inst_type("BTC-USD-FUTURES") == "FUTURES"
    assert infer_okx_inst_type("") == "UNKNOWN"
    assert infer_okx_inst_type("BTC-USD-260626-50000-C") == "UNKNOWN"


def test_okx_to_internal_symbol():
    assert okx_to_internal_symbol("BTC-USDT") == "BTC-USDT"
    assert okx_to_internal_symbol("BTC-USDT-SWAP") == "BTC-USDT-SWAP"
    with pytest.raises(ValueError):
        okx_to_internal_symbol("")


# ── B. Rate limit / error 파싱 ──────────────────────────────────

def test_parse_okx_api_error_ok():
    err = parse_okx_api_error({"code": "0", "msg": "", "data": [{"x": 1}]})
    assert err.is_ok is True
    assert err.is_rate_limit is False
    assert err.data == [{"x": 1}]


def test_parse_okx_api_error_rate_limit():
    err = parse_okx_api_error({"code": "50011", "msg": "Requests too frequent"})
    assert err.is_ok is False
    assert err.is_rate_limit is True
    assert "50011" in OKX_RATE_LIMIT_CODES


def test_parse_okx_api_error_non_dict_safe():
    err = parse_okx_api_error(None)
    assert err.is_ok is False
    assert err.is_rate_limit is False


def test_is_okx_rate_limit_error_true_false():
    assert is_okx_rate_limit_error({"code": "50011"}) is True
    assert is_okx_rate_limit_error({"code": "0"}) is False
    assert is_okx_rate_limit_error("not a dict") is False


def test_should_throttle_okx():
    rl = OkxApiError(code="50011", msg="x", is_rate_limit=True)
    assert should_throttle_okx(rl) is True
    assert should_throttle_okx({"code": "50011"}) is True
    assert should_throttle_okx({"code": "0"}) is False
    assert should_throttle_okx(None) is False


def test_rate_limit_state_sleep_injection():
    slept: list[float] = []
    state = OkxRateLimitState(sleep_fn=lambda s: slept.append(s))
    state.update({"code": "50011", "msg": "rate"})
    assert state.last_error and state.last_error.is_rate_limit
    assert state.maybe_backoff(seconds=0.05) is True
    assert slept == [0.05]
    assert state.rate_limit_hits == 1


def test_rate_limit_state_ok_clears():
    state = OkxRateLimitState()
    state.update({"code": "50011", "msg": "x"})
    assert state.last_error is not None
    state.update({"code": "0", "msg": "", "data": []})
    assert state.last_error is None
    assert state.maybe_backoff() is False


# ── C. OkxPublicClient — FakeTransport ──────────────────────────

class _FakeOkxTransport:
    def __init__(self, responses: dict[str, OkxTransportResponse] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict, dict]] = []

    def __call__(self, method, path, params=None, headers=None):
        self.calls.append((method, path, dict(params or {}), dict(headers or {})))
        return self.responses.get(
            path,
            OkxTransportResponse(
                status_code=200,
                body={"code": "0", "msg": "", "data": []},
                headers={},
            ),
        )


def _ok_body(data):
    return OkxTransportResponse(
        status_code=200,
        body={"code": "0", "msg": "", "data": data},
        headers={},
    )


def test_public_client_raises_without_transport():
    c = OkxPublicClient()
    with pytest.raises(RuntimeError):
        c.fetch_instruments()


def test_public_client_fetch_instruments():
    t = _FakeOkxTransport({
        "/api/v5/public/instruments": _ok_body([
            {"instId": "BTC-USDT", "instType": "SPOT",
             "baseCcy": "BTC", "quoteCcy": "USDT", "state": "live"},
            {"instId": "ETH-USDT", "instType": "SPOT",
             "baseCcy": "ETH", "quoteCcy": "USDT", "state": "live"},
        ]),
    })
    c = OkxPublicClient(transport=t)
    out = c.fetch_instruments("SPOT")
    assert {x["instId"] for x in out} == {"BTC-USDT", "ETH-USDT"}
    # 호출 path 검증
    assert t.calls[0][1] == "/api/v5/public/instruments"


def test_public_client_fetch_instruments_rejects_unknown_type():
    c = OkxPublicClient(transport=_FakeOkxTransport())
    with pytest.raises(ValueError):
        c.fetch_instruments("OPTION")
    assert "SPOT" in OKX_ALLOWED_INST_TYPES
    assert "SWAP" in OKX_ALLOWED_INST_TYPES


def test_public_client_fetch_ticker_parses_price():
    t = _FakeOkxTransport({
        "/api/v5/market/ticker": _ok_body([
            {"instId": "BTC-USDT", "last": "50000.0",
             "bidPx": "49990", "askPx": "50010",
             "bidSz": "1.2", "askSz": "1.5",
             "vol24h": "100", "volCcy24h": "5000000",
             "ts": "1700000000000"},
        ]),
    })
    c = OkxPublicClient(transport=t)
    tk = c.fetch_ticker("BTC-USDT")
    assert tk["last"] == 50000.0
    assert tk["bid_px"] < tk["ask_px"]
    assert tk["timestamp"] == 1_700_000_000_000


def test_public_client_fetch_orderbook_best_bid_lt_ask():
    t = _FakeOkxTransport({
        "/api/v5/market/books": _ok_body([
            {"bids": [["49990", "1"], ["49980", "2"]],
             "asks": [["50010", "1"], ["50020", "2"]],
             "ts": "1700000000000"},
        ]),
    })
    c = OkxPublicClient(transport=t)
    ob = c.fetch_orderbook("BTC-USDT", depth=2)
    assert ob["bids"][0][0] < ob["asks"][0][0]


def test_public_client_fetch_candles_parses_array():
    t = _FakeOkxTransport({
        "/api/v5/market/candles": _ok_body([
            ["1700000000000", "50000", "50100", "49900", "50050", "10", "500000"],
            ["1700000060000", "50050", "50200", "50000", "50150", "12", "600000"],
        ]),
    })
    c = OkxPublicClient(transport=t)
    out = c.fetch_candles("BTC-USDT", bar="1m", limit=2)
    assert len(out) == 2
    assert out[0]["close"] == 50050.0


def test_public_client_funding_rate():
    t = _FakeOkxTransport({
        "/api/v5/public/funding-rate": _ok_body([
            {"instId": "BTC-USDT-SWAP", "fundingRate": "0.0001",
             "nextFundingRate": "0.0002",
             "fundingTime": "1700000000000",
             "nextFundingTime": "1700028800000"},
        ]),
    })
    c = OkxPublicClient(transport=t)
    fr = c.fetch_funding_rate("BTC-USDT-SWAP")
    assert fr["inst_id"] == "BTC-USDT-SWAP"
    assert fr["funding_rate"] == 0.0001


def test_public_client_rate_limit_response_raises():
    t = _FakeOkxTransport({
        "/api/v5/market/ticker": OkxTransportResponse(
            status_code=200,
            body={"code": "50011", "msg": "Requests too frequent"},
            headers={},
        ),
    })
    c = OkxPublicClient(transport=t)
    with pytest.raises(OkxPublicAPIError):
        c.fetch_ticker("BTC-USDT")
    # rate_limit state 에 반영
    assert c.rate_limit.last_error is not None
    assert c.rate_limit.last_error.is_rate_limit is True


def test_public_client_rejects_non_public_path():
    c = OkxPublicClient(transport=_FakeOkxTransport())
    with pytest.raises(OkxPublicAPIError):
        c._call("/api/v5/account/balance")
    with pytest.raises(OkxPublicAPIError):
        c._call("/api/v5/trade/order")


def test_public_client_invalid_bar_rejected():
    c = OkxPublicClient(transport=_FakeOkxTransport())
    with pytest.raises(ValueError):
        c.fetch_candles("BTC-USDT", bar="7m")
    assert "1m" in OKX_ALLOWED_BARS


def test_public_client_invalid_inst_id_rejected():
    c = OkxPublicClient(transport=_FakeOkxTransport())
    with pytest.raises(ValueError):
        c.fetch_ticker("BTC")  # missing dash


# ── D. OkxAdapter via OkxPublicClient ───────────────────────────

def test_adapter_uses_public_client_for_spot():
    t = _FakeOkxTransport({
        "/api/v5/market/ticker": _ok_body([
            {"instId": "BTC-USDT", "last": "50000",
             "bidPx": "49990", "askPx": "50010",
             "vol24h": "0", "volCcy24h": "0", "ts": "1700000000000"},
        ]),
        "/api/v5/market/books": _ok_body([
            {"bids": [["49990", "1"]], "asks": [["50010", "1"]],
             "ts": "1700000000000"},
        ]),
    })
    pc = OkxPublicClient(transport=t)
    a = OkxAdapter(public_client=pc)
    tk = a.fetch_ticker("BTC")
    assert tk.price == 50000.0
    assert tk.bid < tk.ask
    ob = a.fetch_orderbook("BTC", depth=1)
    assert ob.bids[0][0] == 49990.0


def test_adapter_public_client_handles_swap_inst_id():
    t = _FakeOkxTransport({
        "/api/v5/market/ticker": _ok_body([
            {"instId": "BTC-USDT-SWAP", "last": "50100",
             "bidPx": "50090", "askPx": "50110",
             "vol24h": "0", "volCcy24h": "0", "ts": "1700000000000"},
        ]),
        "/api/v5/market/books": _ok_body([
            {"bids": [["50090", "1"]], "asks": [["50110", "1"]],
             "ts": "1700000000000"},
        ]),
    })
    pc = OkxPublicClient(transport=t)
    a = OkxAdapter(public_client=pc)
    tk = a.fetch_ticker("BTC-USDT-SWAP")
    assert tk.price == 50100.0


# ── E. OkxAccountClient gating ─────────────────────────────────

def test_account_client_disabled_without_credentials():
    c = OkxAccountClient()
    assert c.credentials_loaded is False
    with pytest.raises(OkxAccountPermissionError):
        c.fetch_balances()
    with pytest.raises(OkxAccountPermissionError):
        c.fetch_positions()


def test_account_client_requires_all_three_credentials():
    """OKX 는 key+secret+passphrase 모두 필요."""
    c = OkxAccountClient(api_key="x", api_secret="y")  # passphrase 누락
    assert c.credentials_loaded is False
    with pytest.raises(OkxAccountPermissionError):
        c.fetch_balances()


def test_account_client_with_fake_transport():
    def fake(method, path, params, headers):
        assert path == "/api/v5/account/balance"
        return OkxAccountTransportResponse(
            status_code=200,
            body={"code": "0", "msg": "", "data": [
                {"details": [
                    {"ccy": "btc", "bal": "0.5",
                     "frozenBal": "0", "availBal": "0.5", "eq": "25000"},
                    {"ccy": "usdt", "bal": "1000",
                     "frozenBal": "0", "availBal": "1000", "eq": "1000"},
                ]},
            ]},
            headers={},
        )
    c = OkxAccountClient(api_key="x", api_secret="y",
                         api_password="p", transport=fake)
    bal = c.fetch_balances()
    assert {b["ccy"] for b in bal} == {"BTC", "USDT"}


def test_account_client_repr_does_not_leak_credentials():
    c = OkxAccountClient(api_key="super-secret-key",
                         api_secret="another-secret",
                         api_password="super-passphrase")
    r = repr(c)
    assert "super-secret" not in r
    assert "another-secret" not in r
    assert "super-passphrase" not in r
    assert "credentials_loaded=True" in r


def test_account_client_has_no_withdrawal_methods():
    assert_no_withdrawal_methods(OkxAccountClient)


# ── F. OkxTradeClient disabled stub ────────────────────────────

def test_trade_client_all_operations_disabled():
    o = OkxTradeClient()
    with pytest.raises(ExchangeAdapterDisabledError):
        o.place_order(inst_id="BTC-USDT", side="buy", sz=0.001)
    with pytest.raises(ExchangeAdapterDisabledError):
        o.cancel_order(order_id="dummy")
    with pytest.raises(ExchangeAdapterDisabledError):
        o.amend_order(order_id="dummy", sz=0.002)
    with pytest.raises(ExchangeAdapterDisabledError):
        o.get_order(order_id="dummy")


def test_trade_client_ignores_credentials():
    """credentials 가 들어와도 stub 은 저장/노출하지 않는다."""
    o = OkxTradeClient(
        api_key="leaked-key-aaaa",
        api_secret="leaked-secret-bbbb",
        api_password="leaked-pass-cccc",
        transport=object(),
    )
    r = repr(o)
    assert "leaked-key-aaaa" not in r
    assert "leaked-secret-bbbb" not in r
    assert "leaked-pass-cccc" not in r
    assert "disabled" in r.lower()


def test_trade_client_capability_all_false():
    cap = OkxTradeClient.capability
    d = cap.to_dict()
    for k in ("can_place_order", "can_cancel_order", "can_amend_order",
              "can_get_order", "can_set_leverage", "can_set_margin_mode"):
        assert d[k] is False
    assert "OrderGateway" in d["note"]


def test_trade_client_has_no_withdrawal_methods():
    assert_no_withdrawal_methods(OkxTradeClient)


# ── G. OkxPaperOrderClient — spot/swap PAPER ────────────────────

def test_paper_spot_market_buy_filled():
    p = OkxPaperOrderClient(initial_balance_usdt=1_000.0)
    r = p.place_order({
        "inst_id": "BTC-USDT", "inst_type": "SPOT",
        "side": "BUY", "order_type": "MARKET",
        "notional_usdt": 100, "price": 50_000,
    })
    assert r.status == "FILLED"
    assert r.order_id.startswith("okx-paper-")
    assert r.route == "paper"
    assert "spot" in r.reason.lower()
    assert p.get_balance_usdt() == 900.0


def test_paper_spot_limit_sell_accepted():
    p = OkxPaperOrderClient()
    r = p.place_order({
        "inst_id": "BTC-USDT", "inst_type": "SPOT",
        "side": "SELL", "order_type": "LIMIT",
        "notional_usdt": 50, "price": 50_000,
    })
    assert r.status == "ACCEPTED"
    assert "LIMIT" in r.reason


def test_paper_swap_market_buy_filled_no_real_leverage():
    p = OkxPaperOrderClient()
    r = p.place_order({
        "inst_id": "BTC-USDT-SWAP", "inst_type": "SWAP",
        "side": "BUY", "order_type": "MARKET",
        "notional_usdt": 100, "price": 50_000,
        "leverage": 5, "margin_mode": "cross", "reduce_only": False,
    })
    assert r.status == "FILLED"
    assert "swap" in r.reason.lower()
    # leverage 가 실제로 적용되었다는 표시 없음
    assert "applied" not in r.reason.lower() or "not applied" in r.reason.lower()


def test_paper_swap_requires_swap_suffix():
    """inst_type=SWAP 인데 instId 가 -SWAP 으로 끝나지 않으면 REJECTED."""
    p = OkxPaperOrderClient()
    r = p.place_order({
        "inst_id": "BTC-USDT", "inst_type": "SWAP",
        "side": "BUY", "order_type": "MARKET",
        "notional_usdt": 100, "price": 50_000,
    })
    assert r.status == "REJECTED"
    assert "SWAP" in r.reason


def test_paper_spot_cannot_use_swap_suffix():
    p = OkxPaperOrderClient()
    r = p.place_order({
        "inst_id": "BTC-USDT-SWAP", "inst_type": "SPOT",
        "side": "BUY", "order_type": "MARKET",
        "notional_usdt": 100, "price": 50_000,
    })
    assert r.status == "REJECTED"


def test_paper_invalid_inst_id_rejected():
    p = OkxPaperOrderClient()
    r = p.place_order({
        "inst_id": "BTC", "inst_type": "SPOT",
        "side": "BUY", "order_type": "MARKET",
        "notional_usdt": 100,
    })
    assert r.status == "REJECTED"


def test_paper_rejects_live_mode():
    p = OkxPaperOrderClient()
    r = p.place_order({
        "inst_id": "BTC-USDT", "inst_type": "SPOT",
        "side": "BUY", "order_type": "MARKET",
        "notional_usdt": 100, "price": 50_000,
        "mode": "LIVE",
    })
    assert r.status == "REJECTED"
    assert "LIVE" in r.reason
    assert r.route == "live_not_wired"


def test_paper_rejects_live_via_trading_mode_field():
    p = OkxPaperOrderClient()
    r = p.place_order({
        "inst_id": "BTC-USDT", "inst_type": "SPOT",
        "side": "BUY", "order_type": "MARKET",
        "notional_usdt": 100, "price": 50_000,
        "trading_mode": "LIVE",
    })
    assert r.status == "REJECTED"


def test_paper_insufficient_balance_rejected():
    p = OkxPaperOrderClient(initial_balance_usdt=50.0)
    r = p.place_order({
        "inst_id": "BTC-USDT", "inst_type": "SPOT",
        "side": "BUY", "order_type": "MARKET",
        "notional_usdt": 500, "price": 50_000,
    })
    assert r.status == "REJECTED"
    assert "insufficient_balance" in r.reason
    assert p.get_balance_usdt() == 50.0


def test_paper_idempotent_by_client_order_id():
    p = OkxPaperOrderClient(initial_balance_usdt=1_000.0)
    req = {
        "inst_id": "BTC-USDT", "inst_type": "SPOT",
        "side": "BUY", "order_type": "MARKET",
        "notional_usdt": 100, "price": 50_000,
        "client_order_id": "dup-okx-1",
    }
    r1 = p.place_order(req)
    bal_after = p.get_balance_usdt()
    r2 = p.place_order(req)
    assert r1.order_id == r2.order_id
    assert r1.status == r2.status == "FILLED"
    # 중복 호출 잔고 이중 차감 없음
    assert p.get_balance_usdt() == bal_after


def test_paper_cancel_known_and_unknown():
    p = OkxPaperOrderClient()
    placed = p.place_order({
        "inst_id": "BTC-USDT", "inst_type": "SPOT",
        "side": "SELL", "order_type": "LIMIT",
        "notional_usdt": 10, "price": 50_000,
    })
    c1 = p.cancel_order(placed.order_id)
    assert c1.status == "ACCEPTED"
    c2 = p.cancel_order("unknown-xyz")
    assert c2.status == "ACCEPTED"
    assert "unknown" in c2.reason.lower()


def test_paper_audit_strips_secret_fields():
    """raw_response/audit 에 api_key / api_secret / passphrase 가 새지 않는다."""
    p = OkxPaperOrderClient()
    r = p.place_order({
        "inst_id": "BTC-USDT", "inst_type": "SPOT",
        "side": "BUY", "order_type": "MARKET",
        "notional_usdt": 100, "price": 50_000,
        "api_key": "AAAA", "api_secret": "BBBB",
        "api_password": "CCCC", "passphrase": "DDDD",
        "ok_access_key": "EEEE", "ok_access_sign": "FFFF",
    })
    audit_str = repr(r.audit or {}).lower()
    for bad in ("aaaa", "bbbb", "cccc", "dddd", "eeee", "ffff",
                "api_key", "api_secret", "passphrase",
                "ok_access_key", "ok_access_sign"):
        assert bad not in audit_str, f"secret leaked: {bad}"


# ── H. 단일 주문 경로 — Strategy/Agent 가 OKX 모듈 직접 호출 부재 ─

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


def test_strategies_do_not_import_okx_module():
    pat = re.compile(
        r"(?:from|import)\s+app\.brokers\.(?:okx_adapter|okx_public|"
        r"okx_account|okx_trade|okx_rate_limit)",
    )
    hits = _scan(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy imports okx module: {hits}"


def test_agents_do_not_import_okx_module():
    pat = re.compile(
        r"(?:from|import)\s+app\.brokers\.(?:okx_adapter|okx_public|"
        r"okx_account|okx_trade|okx_rate_limit)",
    )
    whitelist = {"compliance.py"}
    hits = [p for p in _scan(_REPO_BACKEND_APP / "agents", pat)
            if p.name not in whitelist]
    assert not hits, f"agent imports okx module: {hits}"


def test_strategies_no_okx_client_instantiation():
    pat = re.compile(
        r"OkxAdapter\s*\(|OkxPublicClient\s*\(|OkxAccountClient\s*\(|"
        r"OkxTradeClient\s*\(|OkxPaperOrderClient\s*\(",
    )
    hits = _scan(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy instantiates okx client: {hits}"


def test_agents_no_okx_client_instantiation():
    pat = re.compile(
        r"OkxAdapter\s*\(|OkxPublicClient\s*\(|OkxAccountClient\s*\(|"
        r"OkxTradeClient\s*\(|OkxPaperOrderClient\s*\(",
    )
    hits = _scan(_REPO_BACKEND_APP / "agents", pat)
    assert not hits, f"agent instantiates okx client: {hits}"


# ── I. production 정적 금지 검증 ───────────────────────────────

def test_okx_modules_no_forbidden_substrings():
    forbidden = (
        "ENABLE_LIVE_TRADING = True",
        "ENABLE_AI_EXECUTION = True",
        "ENABLE_CRYPTO_FUTURES_LIVE = True",
        # signing 구현
        "OK-ACCESS-SIGN", "OK_ACCESS_SIGN",
    )
    for fname in ("okx_adapter.py", "okx_public.py",
                  "okx_account.py", "okx_trade.py",
                  "okx_rate_limit.py"):
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(
            encoding="utf-8", errors="ignore",
        )
        for needle in forbidden:
            assert needle not in text, f"{fname} contains {needle!r}"


def test_okx_modules_no_real_trade_endpoint_literal():
    """실제 trade endpoint URL literal 부재 — 본 단계에서 추가 금지.

    /api/v5/account/balance / positions 는 account 모듈 화이트리스트로 존재 — OK.
    trade endpoint 만 차단.
    """
    forbidden_endpoints = (
        "/api/v5/trade/order",
        "/api/v5/trade/cancel-order",
        "/api/v5/trade/cancel-batch-orders",
        "/api/v5/account/set-leverage",
        "/api/v5/account/set-position-mode",
        # 출금
        "/api/v5/asset/withdrawal",
    )
    for fname in ("okx_adapter.py", "okx_public.py",
                  "okx_account.py", "okx_trade.py",
                  "okx_rate_limit.py"):
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(
            encoding="utf-8", errors="ignore",
        )
        for needle in forbidden_endpoints:
            assert needle not in text, f"{fname} contains {needle!r}"


def test_okx_modules_no_signing_imports():
    pat = re.compile(r"^\s*(?:import\s+(?:jwt|hmac)|from\s+(?:jwt|hmac))", re.M)
    for fname in ("okx_adapter.py", "okx_public.py",
                  "okx_account.py", "okx_trade.py",
                  "okx_rate_limit.py"):
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(
            encoding="utf-8", errors="ignore",
        )
        assert not pat.search(text), f"{fname} imports jwt/hmac"


def test_okx_modules_no_requests_or_httpx_imports():
    pat = re.compile(
        r"^\s*(?:import\s+(?:requests|httpx)|from\s+(?:requests|httpx))",
        re.M,
    )
    for fname in ("okx_adapter.py", "okx_public.py",
                  "okx_account.py", "okx_trade.py",
                  "okx_rate_limit.py"):
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(
            encoding="utf-8", errors="ignore",
        )
        assert not pat.search(text), f"{fname} imports requests/httpx"


def test_okx_public_module_no_ccxt_import():
    """okx_public.py 는 transport-기반 — ccxt 의존 없음."""
    pat = re.compile(r"^\s*(?:import\s+ccxt|from\s+ccxt)", re.M)
    text = (_REPO_BACKEND_APP / "brokers" / "okx_public.py").read_text(
        encoding="utf-8", errors="ignore",
    )
    assert not pat.search(text)


def test_frontend_has_no_okx_secret_assignment():
    fe = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
    if not fe.exists():
        pytest.skip("frontend/src not present")
    pat = re.compile(
        r"OKX_API_KEY|OKX_SECRET_KEY|OKX_PASSPHRASE|"
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
    assert not hits, f"frontend leaks okx secret reference: {hits}"


# ── J. brokers __all__ exports ─────────────────────────────────

def test_brokers_module_exports_okx_helpers():
    from app import brokers
    for name in (
        "OkxPublicClient", "OkxAccountClient", "OkxTradeClient",
        "OkxPaperOrderClient",
        "normalize_okx_inst_id", "infer_okx_inst_type",
        "okx_to_internal_symbol",
        "parse_okx_api_error", "is_okx_rate_limit_error",
        "should_throttle_okx",
        "OkxApiError", "OkxRateLimitState",
        "OKX_RATE_LIMIT_CODES", "OKX_ALLOWED_INST_TYPES", "OKX_ALLOWED_BARS",
    ):
        assert name in brokers.__all__, f"{name} not exported"
        assert hasattr(brokers, name)


# ── K. collector 통합 — paper 주문 메서드는 호출되지 않음 ───────

def test_collector_with_okx_adapter_does_not_invoke_orders():
    """OkxAdapter 를 collector 에 주입했을 때 시세만 호출되고 주문 경로는 안 탄다."""
    fake = FakeCcxtOkx(prices={"BTC/USDT": 50_000.0})
    a = OkxAdapter(client=fake)
    c = MarketDataCollector(sources={"okx": a})
    report = c.collect([("BTC", "okx")])
    assert report.ok_count == 1
    # adapter capability 가 주문 false 이므로 가능한 한 호출이 없었음을 표시
    assert a.capability.can_place_order is False
    # fake client 는 fetch_ticker / fetch_order_book 만 호출되었어야 함
    methods = {m for m, _ in fake.calls}
    assert methods.issubset({"fetch_ticker", "fetch_order_book"})
