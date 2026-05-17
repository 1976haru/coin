"""체크리스트 #21 Upbit Adapter — 회귀 테스트.

검증 (네트워크 호출 없음 — fake client 주입):
  1. 심볼 정규화 (다양한 입력 형식 → 'KRW-XXX')
  2. capability = READ_ONLY, 주문/잔고 disabled
  3. API 키 주입 거부 (생성자에서 raise)
  4. fetch_ticker / fetch_orderbook 정상 경로
  5. 빈 orderbook / None price 에 대한 안전 처리
  6. ExchangeAdapter contract — MarketDataSource Protocol 만족
  7. collector 에 직접 주입 가능
  8. 출금 메서드 부재 (CLAUDE.md §2.1.2)
"""
from __future__ import annotations
import pytest

from app.brokers import (
    UpbitAdapter, ExchangeAdapter, ExchangeAdapterDisabledError,
    conforms_to_market_data_source, assert_no_withdrawal_methods,
)
from app.market.collector import MarketDataCollector


# ── Fake pyupbit client (네트워크 호출 차단) ─────────────────────

class FakeUpbit:
    """get_current_price / get_orderbook 만 구현한 결정론적 fake."""

    def __init__(
        self,
        prices: dict[str, float] | None = None,
        orderbook_size: int = 5,
    ):
        # symbol(KRW-XXX) → 가격
        self._prices = prices or {"KRW-BTC": 50_000_000.0,
                                   "KRW-ETH": 3_000_000.0}
        self._orderbook_size = orderbook_size
        self.calls: list[tuple[str, str]] = []

    def get_current_price(self, symbol: str):
        self.calls.append(("get_current_price", symbol))
        return self._prices.get(symbol)

    def get_orderbook(self, symbol: str):
        self.calls.append(("get_orderbook", symbol))
        price = self._prices.get(symbol)
        if price is None:
            return [{"orderbook_units": []}]
        units = []
        for i in range(self._orderbook_size):
            units.append({
                "ask_price": price * (1 + 0.0005 * (i + 1)),
                "bid_price": price * (1 - 0.0005 * (i + 1)),
                "ask_size":  1.0 + 0.1 * i,
                "bid_size":  1.0 + 0.1 * i,
            })
        return [{"orderbook_units": units}]


# ── 1. 심볼 정규화 ───────────────────────────────────────────────

@pytest.mark.parametrize("inp,expected", [
    ("BTC",      "KRW-BTC"),
    ("btc",      "KRW-BTC"),
    ("KRW-BTC",  "KRW-BTC"),
    ("BTC-KRW",  "KRW-BTC"),
    ("BTC/KRW",  "KRW-BTC"),
    ("ETH",      "KRW-ETH"),
    ("USDT-BTC", "USDT-BTC"),  # KRW 외는 그대로 (정렬만)
])
def test_symbol_normalization(inp, expected):
    assert UpbitAdapter.to_upbit_symbol(inp) == expected


# ── 2. Capability ────────────────────────────────────────────────

def test_capability_is_read_only():
    a = UpbitAdapter(client=FakeUpbit())
    cap = a.capability
    assert cap.mode == "READ_ONLY"
    assert cap.can_fetch_ticker is True
    assert cap.can_fetch_orderbook is True
    assert cap.can_fetch_balance is False
    assert cap.can_place_order is False
    assert cap.can_cancel_order is False
    assert cap.requires_secret is False


def test_disabled_methods_raise():
    a = UpbitAdapter(client=FakeUpbit())
    with pytest.raises(ExchangeAdapterDisabledError):
        a.fetch_balance()
    with pytest.raises(ExchangeAdapterDisabledError):
        a.place_order({"symbol": "BTC", "side": "BUY", "notional_usdt": 100})


def test_disabled_cancel_returns_rejected():
    a = UpbitAdapter(client=FakeUpbit())
    r = a.cancel_order("xyz")
    assert r.status == "REJECTED"


# ── 3. API 키 주입 차단 ──────────────────────────────────────────

def test_constructor_rejects_api_key():
    with pytest.raises(ValueError):
        UpbitAdapter(api_key="leak")


def test_constructor_rejects_api_secret():
    with pytest.raises(ValueError):
        UpbitAdapter(api_secret="leak")


def test_constructor_rejects_both():
    with pytest.raises(ValueError):
        UpbitAdapter(api_key="a", api_secret="b")


# ── 4. fetch_ticker / fetch_orderbook ────────────────────────────

def test_fetch_ticker_normal():
    fake = FakeUpbit()
    a = UpbitAdapter(client=fake)
    t = a.fetch_ticker("BTC")
    assert t.symbol == "BTC"  # 호출자 입력 형식 유지
    assert t.price == 50_000_000.0
    assert t.bid > 0
    assert t.ask > t.bid
    assert t.spread_pct > 0


def test_fetch_ticker_uses_normalized_symbol_in_calls():
    fake = FakeUpbit()
    a = UpbitAdapter(client=fake)
    a.fetch_ticker("BTC/KRW")
    # 정규화된 KRW-BTC 로 호출
    assert ("get_current_price", "KRW-BTC") in fake.calls
    assert ("get_orderbook", "KRW-BTC") in fake.calls


def test_fetch_orderbook_returns_correct_depth():
    a = UpbitAdapter(client=FakeUpbit(orderbook_size=8))
    ob = a.fetch_orderbook("BTC", depth=5)
    assert len(ob.bids) == 5
    assert len(ob.asks) == 5


def test_fetch_orderbook_depth_clamped_to_available():
    a = UpbitAdapter(client=FakeUpbit(orderbook_size=3))
    ob = a.fetch_orderbook("BTC", depth=10)
    assert len(ob.bids) == 3


def test_fetch_orderbook_bid_descending_ask_ascending():
    a = UpbitAdapter(client=FakeUpbit(orderbook_size=5))
    ob = a.fetch_orderbook("BTC", depth=5)
    bid_prices = [p for p, _ in ob.bids]
    ask_prices = [p for p, _ in ob.asks]
    assert bid_prices == sorted(bid_prices, reverse=True)
    assert ask_prices == sorted(ask_prices)


# ── 5. 안전 처리 ─────────────────────────────────────────────────

def test_fetch_ticker_raises_when_price_none():
    a = UpbitAdapter(client=FakeUpbit(prices={}))
    with pytest.raises(RuntimeError):
        a.fetch_ticker("UNKNOWN")


def test_fetch_orderbook_returns_empty_when_unavailable():
    a = UpbitAdapter(client=FakeUpbit(prices={}))
    ob = a.fetch_orderbook("UNKNOWN")
    assert ob.bids == ()
    assert ob.asks == ()


def test_fetch_orderbook_handles_dict_response():
    """pyupbit 일부 버전이 list 대신 dict 반환할 경우 대비."""

    class DictResponseFake:
        def get_current_price(self, s): return 100.0
        def get_orderbook(self, s):
            return {
                "orderbook_units": [
                    {"ask_price": 100.5, "bid_price": 99.5,
                     "ask_size": 1.0, "bid_size": 1.0},
                ]
            }

    a = UpbitAdapter(client=DictResponseFake())
    ob = a.fetch_orderbook("BTC")
    assert len(ob.bids) == 1
    assert ob.bids[0][0] == 99.5


# ── 6. ExchangeAdapter contract ──────────────────────────────────

def test_satisfies_market_data_source_protocol():
    a = UpbitAdapter(client=FakeUpbit())
    assert conforms_to_market_data_source(a) is True


def test_isinstance_exchange_adapter():
    a = UpbitAdapter(client=FakeUpbit())
    assert isinstance(a, ExchangeAdapter)


# ── 7. Collector 통합 ────────────────────────────────────────────

def test_collector_can_use_upbit_adapter():
    fake = FakeUpbit(prices={"KRW-BTC": 50_000_000.0,
                              "KRW-ETH": 3_000_000.0})
    a = UpbitAdapter(client=fake)
    c = MarketDataCollector(sources={"upbit": a})
    report = c.collect([("BTC", "upbit"), ("ETH", "upbit")])
    assert report.ok_count == 2
    assert all(e.ticker is not None for e in report.entries)


def test_collector_records_unknown_symbol_as_error():
    fake = FakeUpbit(prices={"KRW-BTC": 50_000_000.0})
    a = UpbitAdapter(client=fake)
    c = MarketDataCollector(sources={"upbit": a})
    report = c.collect([("UNKNOWN", "upbit")])
    e = report.entries[0]
    assert "RuntimeError" in e.error or e.ticker is None


# ── 8. 출금 메서드 부재 ──────────────────────────────────────────

def test_no_withdrawal_methods_on_upbit_adapter():
    assert_no_withdrawal_methods(UpbitAdapter)


# ── 9. 모듈 경계 ─────────────────────────────────────────────────

def test_no_top_level_pyupbit_import():
    """import 시점에 pyupbit 가 강제되면 안 됨 (lazy import 검증)."""
    import importlib, sys
    # pyupbit 가 sys.modules 에 있다면 다른 테스트가 트리거함 — 우리 import 자체로 강제하는지 검증
    saved = sys.modules.pop("pyupbit", None)
    try:
        importlib.reload(importlib.import_module("app.brokers.upbit_adapter"))
        assert "pyupbit" not in sys.modules, \
            "upbit_adapter 모듈 import 만으로 pyupbit 가 import 되면 안 됨"
    finally:
        if saved is not None:
            sys.modules["pyupbit"] = saved


# ─────────────────────────────────────────────────────────────────
# 체크리스트 #21 확장 — public client / rate limit / account / order
# ─────────────────────────────────────────────────────────────────

import re
from pathlib import Path

from app.brokers import (
    UpbitPublicClient, UpbitPublicAPIError, UpbitTransportResponse,
    UpbitAccountClient, UpbitAccountPermissionError,
    UpbitAccountTransportResponse,
    UpbitOrderClient,
    normalize_upbit_market, to_internal_symbol, is_krw_market,
    parse_remaining_req, should_throttle, RateLimitState,
)


# ── 1. 심볼 헬퍼 — module-level ─────────────────────────────────

def test_normalize_upbit_market_basic():
    assert normalize_upbit_market("BTC") == "KRW-BTC"
    assert normalize_upbit_market("btc") == "KRW-BTC"
    assert normalize_upbit_market("KRW-BTC") == "KRW-BTC"
    assert normalize_upbit_market("BTC-KRW") == "KRW-BTC"
    assert normalize_upbit_market("btc-krw") == "KRW-BTC"


def test_normalize_rejects_empty():
    with pytest.raises(ValueError):
        normalize_upbit_market("")
    with pytest.raises(ValueError):
        normalize_upbit_market("   ")
    with pytest.raises(ValueError):
        normalize_upbit_market(None)  # type: ignore[arg-type]


def test_to_internal_symbol_krw_market():
    assert to_internal_symbol("KRW-BTC") == "BTC-KRW"
    assert to_internal_symbol("KRW-ETH") == "ETH-KRW"


def test_to_internal_symbol_usdt_market():
    assert to_internal_symbol("USDT-BTC") == "BTC-USDT"


def test_to_internal_symbol_btc_market():
    assert to_internal_symbol("BTC-XRP") == "XRP-BTC"


def test_is_krw_market():
    assert is_krw_market("KRW-BTC") is True
    assert is_krw_market("krw-eth") is True
    assert is_krw_market("USDT-BTC") is False
    assert is_krw_market("BTC-XRP") is False
    assert is_krw_market("") is False


# ── 2. Rate limit — Remaining-Req 파싱 ──────────────────────────

def test_parse_remaining_req_normal():
    r = parse_remaining_req("group=market; min=599; sec=9")
    assert r == {"group": "market", "min": 599, "sec": 9}


def test_parse_remaining_req_whitespace_tolerant():
    r = parse_remaining_req("  group =  default ;   min=10 ;sec= 2  ")
    assert r["group"] == "default"
    assert r["min"] == 10
    assert r["sec"] == 2


def test_parse_remaining_req_none_and_empty():
    assert parse_remaining_req(None) == {}
    assert parse_remaining_req("") == {}
    assert parse_remaining_req("   ") == {}


def test_parse_remaining_req_malformed_safe():
    """깨진 토큰은 무시하고 인식 가능한 키만 채운다."""
    r = parse_remaining_req("garbage;;group=market;min=abc;sec=5;extra=ignored")
    assert r == {"group": "market", "sec": 5}


def test_should_throttle_when_sec_low():
    assert should_throttle({"group": "x", "min": 100, "sec": 0}) is True
    assert should_throttle({"group": "x", "min": 100, "sec": 1}) is True
    assert should_throttle({"group": "x", "min": 100, "sec": 2}) is False


def test_should_throttle_when_min_zero():
    assert should_throttle({"group": "x", "min": 0, "sec": 50}) is True


def test_should_throttle_with_empty_remaining():
    assert should_throttle({}) is False


def test_rate_limit_state_update_and_throttle_sleep_injection():
    slept_calls: list[float] = []
    state = RateLimitState(sleep_fn=lambda s: slept_calls.append(s))
    state.update("group=market; min=10; sec=0")
    assert state.last_sec == 0
    assert state.maybe_throttle(sleep_seconds=0.1) is True
    assert slept_calls == [0.1]
    assert state.throttle_count == 1


def test_rate_limit_state_no_throttle_when_safe():
    state = RateLimitState()
    state.update("group=market; min=500; sec=20")
    assert state.maybe_throttle() is False
    assert state.throttle_count == 0


# ── 3. UpbitPublicClient — FakeTransport ────────────────────────

class _FakeTransport:
    """결정론적 fake transport. 네트워크 호출 0."""

    def __init__(self, responses: dict[str, UpbitTransportResponse] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict, dict]] = []

    def __call__(self, method, path, params=None, headers=None):
        self.calls.append((method, path, dict(params or {}), dict(headers or {})))
        if path in self.responses:
            return self.responses[path]
        # 기본: 빈 응답
        return UpbitTransportResponse(
            status_code=200, body=[], headers={"Remaining-Req": "group=default; min=599; sec=9"},
        )


def _ok(body, *, remaining="group=market; min=599; sec=9"):
    return UpbitTransportResponse(
        status_code=200, body=body, headers={"Remaining-Req": remaining},
    )


def test_public_client_raises_without_transport():
    c = UpbitPublicClient()
    with pytest.raises(RuntimeError):
        c.fetch_markets()


def test_public_client_fetch_markets():
    t = _FakeTransport({
        "/v1/market/all": _ok([
            {"market": "KRW-BTC", "korean_name": "비트코인", "english_name": "Bitcoin"},
            {"market": "KRW-ETH", "korean_name": "이더리움", "english_name": "Ethereum"},
            {"market": "garbage"},  # 누락 — 필터링
        ]),
    })
    c = UpbitPublicClient(transport=t)
    markets = c.fetch_markets()
    assert len(markets) == 2
    assert {m["market"] for m in markets} == {"KRW-BTC", "KRW-ETH"}
    # 호출 path 검증
    assert t.calls[0][1] == "/v1/market/all"


def test_public_client_fetch_ticker_parses_price():
    t = _FakeTransport({
        "/v1/ticker": _ok([
            {"market": "KRW-BTC", "trade_price": 50_000_000.0,
             "acc_trade_volume_24h": 10.5},
        ]),
    })
    c = UpbitPublicClient(transport=t)
    out = c.fetch_ticker(["KRW-BTC"])
    assert out[0]["trade_price"] == 50_000_000.0


def test_public_client_fetch_orderbook_best_bid_lt_best_ask():
    t = _FakeTransport({
        "/v1/orderbook": _ok([
            {"market": "KRW-BTC",
             "orderbook_units": [
                 {"ask_price": 100.5, "bid_price": 99.5,
                  "ask_size": 1.0, "bid_size": 2.0},
                 {"ask_price": 101.0, "bid_price": 99.0,
                  "ask_size": 1.0, "bid_size": 2.0},
             ]},
        ]),
    })
    c = UpbitPublicClient(transport=t)
    obs = c.fetch_orderbook(["KRW-BTC"])
    units = obs[0]["orderbook_units"]
    assert units[0]["bid_price"] < units[0]["ask_price"]


def test_public_client_rejects_non_public_path():
    """transport 가 어쩌다 private path 로 호출되면 client 가 차단."""
    c = UpbitPublicClient(transport=_FakeTransport())
    # 직접 _call 로 사설 path 시도 → 차단
    with pytest.raises(UpbitPublicAPIError):
        c._call("/v1/accounts")
    with pytest.raises(UpbitPublicAPIError):
        c._call("/v1/orders")


def test_public_client_updates_rate_limit_state():
    t = _FakeTransport({
        "/v1/ticker": _ok([{"market": "KRW-BTC", "trade_price": 1}],
                          remaining="group=market; min=300; sec=5"),
    })
    c = UpbitPublicClient(transport=t)
    c.fetch_ticker(["KRW-BTC"])
    assert c.rate_limit.last_group == "market"
    assert c.rate_limit.last_sec == 5
    assert c.rate_limit.last_min == 300


def test_public_client_rejects_invalid_market_arg():
    c = UpbitPublicClient(transport=_FakeTransport())
    with pytest.raises(ValueError):
        c.fetch_ticker([])  # empty
    with pytest.raises(ValueError):
        c.fetch_ticker(["BTC"])  # missing dash


def test_public_client_candles_minutes_validates_unit():
    c = UpbitPublicClient(transport=_FakeTransport())
    with pytest.raises(ValueError):
        c.fetch_candles_minutes("KRW-BTC", unit=7)


def test_public_client_status_4xx_raises():
    t = _FakeTransport({
        "/v1/market/all": UpbitTransportResponse(
            status_code=429, body={"error": "rate"}, headers={},
        ),
    })
    c = UpbitPublicClient(transport=t)
    with pytest.raises(UpbitPublicAPIError):
        c.fetch_markets()


# ── 4. UpbitAdapter via UpbitPublicClient ───────────────────────

def test_adapter_uses_public_client_when_injected():
    t = _FakeTransport({
        "/v1/ticker": _ok([{"market": "KRW-BTC", "trade_price": 50_000_000.0,
                            "acc_trade_volume_24h": 1.5}]),
        "/v1/orderbook": _ok([
            {"market": "KRW-BTC", "orderbook_units": [
                {"ask_price": 50_010_000, "bid_price": 49_990_000,
                 "ask_size": 1.0, "bid_size": 1.0}]},
        ]),
    })
    pc = UpbitPublicClient(transport=t)
    a = UpbitAdapter(public_client=pc)
    tk = a.fetch_ticker("BTC")
    assert tk.price == 50_000_000.0
    assert tk.bid < tk.ask
    # legacy pyupbit 경로는 사용되지 않음
    ob = a.fetch_orderbook("BTC", depth=1)
    assert ob.bids[0][0] == 49_990_000


def test_adapter_public_client_routes_normalized_market():
    t = _FakeTransport({
        "/v1/ticker": _ok([{"market": "KRW-BTC", "trade_price": 1.0}]),
        "/v1/orderbook": _ok([{"market": "KRW-BTC", "orderbook_units": []}]),
    })
    pc = UpbitPublicClient(transport=t)
    a = UpbitAdapter(public_client=pc)
    a.fetch_ticker("BTC/KRW")
    # public client 호출은 KRW-BTC 로 정규화
    assert any(call[2].get("markets") == "KRW-BTC" for call in t.calls)


# ── 5. UpbitAccountClient gating ─────────────────────────────────

def test_account_client_disabled_without_credentials():
    c = UpbitAccountClient()
    assert c.credentials_loaded is False
    with pytest.raises(UpbitAccountPermissionError):
        c.fetch_balances()


def test_account_client_disabled_without_transport_even_with_creds():
    c = UpbitAccountClient(api_key="x", api_secret="y")
    assert c.credentials_loaded is True
    with pytest.raises(UpbitAccountPermissionError):
        c.fetch_balances()


def test_account_client_with_fake_transport():
    """credentials + fake transport 가 있을 때만 응답 — read-only balance 구조 검증."""
    def fake(method, path, params, headers):
        assert path == "/v1/accounts"
        return UpbitAccountTransportResponse(
            status_code=200,
            body=[
                {"currency": "btc", "balance": "0.5", "locked": "0",
                 "avg_buy_price": "50000000", "avg_buy_price_modified": False,
                 "unit_currency": "krw"},
                {"currency": "krw", "balance": "1000000", "locked": "0",
                 "avg_buy_price": "0", "avg_buy_price_modified": False,
                 "unit_currency": "krw"},
            ],
            headers={},
        )
    c = UpbitAccountClient(api_key="x", api_secret="y", transport=fake)
    bal = c.fetch_balances()
    assert len(bal) == 2
    assert bal[0]["currency"] == "BTC"  # 정규화 upper
    assert bal[1]["unit_currency"] == "KRW"


def test_account_client_repr_does_not_leak_credentials():
    c = UpbitAccountClient(api_key="super-secret-key", api_secret="another-secret")
    r = repr(c)
    assert "super-secret" not in r
    assert "another-secret" not in r
    assert "credentials_loaded=True" in r


def test_account_client_has_no_withdrawal_methods():
    assert_no_withdrawal_methods(UpbitAccountClient)


# ── 6. UpbitOrderClient disabled stub ───────────────────────────

def test_order_client_all_operations_disabled():
    o = UpbitOrderClient()
    with pytest.raises(ExchangeAdapterDisabledError):
        o.place_order(symbol="KRW-BTC", side="bid", volume=0.001)
    with pytest.raises(ExchangeAdapterDisabledError):
        o.cancel_order(order_id="dummy")
    with pytest.raises(ExchangeAdapterDisabledError):
        o.get_order(order_id="dummy")


def test_order_client_ignores_api_keys_even_if_passed():
    """credentials 가 들어와도 stub 은 저장하지 않는다."""
    o = UpbitOrderClient(api_key="x", api_secret="y", transport=object())
    # 내부 dict 에 secret 이 잔존하지 않음 (best-effort 검증)
    r = repr(o)
    assert "x" not in r and "y" not in r
    assert "disabled" in r.lower()


def test_order_client_has_no_withdrawal_methods():
    assert_no_withdrawal_methods(UpbitOrderClient)


def test_order_client_capability_dict():
    cap = UpbitOrderClient.capability
    d = cap.to_dict()
    assert d["can_place_order"] is False
    assert d["can_cancel_order"] is False
    assert "OrderGateway" in d["note"]


# ── 7. UpbitAdapter LIVE-mode order request rejection (single path) ─

def test_adapter_place_order_disabled_even_with_live_dict():
    """READ_ONLY adapter 는 mode='LIVE' 가 와도 capability false 라 즉시 disabled."""
    a = UpbitAdapter(client=FakeUpbit())
    with pytest.raises(ExchangeAdapterDisabledError):
        a.place_order({
            "symbol": "KRW-BTC", "side": "BUY",
            "order_type": "MARKET", "notional_usdt": 100,
            "mode": "LIVE",  # 무시되고 disabled
        })


# ── 8. 단일 주문 경로 — Strategy/Agent 가 UpbitAdapter 직접 호출 부재 ─

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


def test_strategies_do_not_import_upbit_adapter():
    pat = re.compile(
        r"(?:from|import)\s+app\.brokers\.(?:upbit_adapter|upbit_public|"
        r"upbit_account|upbit_order|upbit_rate_limit)",
    )
    hits = _scan(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy imports upbit module: {hits}"


def test_agents_do_not_import_upbit_adapter():
    pat = re.compile(
        r"(?:from|import)\s+app\.brokers\.(?:upbit_adapter|upbit_public|"
        r"upbit_account|upbit_order|upbit_rate_limit)",
    )
    whitelist = {"compliance.py"}
    hits = [p for p in _scan(_REPO_BACKEND_APP / "agents", pat)
            if p.name not in whitelist]
    assert not hits, f"agent imports upbit module: {hits}"


def test_strategies_no_upbit_adapter_call():
    pat = re.compile(r"UpbitAdapter\s*\(|UpbitPublicClient\s*\(|"
                     r"UpbitAccountClient\s*\(|UpbitOrderClient\s*\(")
    hits = _scan(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy instantiates upbit client: {hits}"


def test_agents_no_upbit_adapter_call():
    pat = re.compile(r"UpbitAdapter\s*\(|UpbitPublicClient\s*\(|"
                     r"UpbitAccountClient\s*\(|UpbitOrderClient\s*\(")
    hits = _scan(_REPO_BACKEND_APP / "agents", pat)
    assert not hits, f"agent instantiates upbit client: {hits}"


# ── 9. production 정적 금지 ────────────────────────────────────

def test_upbit_modules_no_forbidden_substrings():
    forbidden = (
        "ENABLE_LIVE_TRADING = True",
        "ENABLE_AI_EXECUTION = True",
        "ENABLE_CRYPTO_FUTURES_LIVE = True",
        # 출금/이체
        "/v1/withdraws", "withdraw_coin", "withdraw_krw",
        # JWT/HMAC signing
        "jwt.encode", "hmac.new",
    )
    for fname in ("upbit_adapter.py", "upbit_public.py",
                  "upbit_account.py", "upbit_order.py",
                  "upbit_rate_limit.py"):
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(
            encoding="utf-8", errors="ignore",
        )
        for needle in forbidden:
            assert needle not in text, f"{fname} contains {needle!r}"


def test_upbit_modules_no_real_order_endpoint_strings():
    """주문 endpoint URL literal 부재 — 본 단계에서 추가 금지."""
    forbidden_urls = (
        "/v1/orders/cancel",
        "POST /v1/orders",
    )
    for fname in ("upbit_adapter.py", "upbit_public.py",
                  "upbit_account.py", "upbit_order.py",
                  "upbit_rate_limit.py"):
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(
            encoding="utf-8", errors="ignore",
        )
        for needle in forbidden_urls:
            assert needle not in text, f"{fname} references {needle!r}"


def test_upbit_modules_do_not_import_requests_or_httpx():
    """직접 네트워크 라이브러리 import 부재 — transport injection 강제."""
    pat = re.compile(
        r"^\s*(?:import\s+(?:requests|httpx)|"
        r"from\s+(?:requests|httpx))",
        re.M,
    )
    for fname in ("upbit_adapter.py", "upbit_public.py",
                  "upbit_account.py", "upbit_order.py",
                  "upbit_rate_limit.py"):
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(
            encoding="utf-8", errors="ignore",
        )
        assert not pat.search(text), f"{fname} imports requests/httpx"


def test_upbit_modules_no_jwt_signing_implementation():
    """JWT/HMAC signing 구현 부재 — 본 단계에서 추가 금지."""
    pat = re.compile(r"import\s+jwt|import\s+hmac\b")
    for fname in ("upbit_adapter.py", "upbit_public.py",
                  "upbit_account.py", "upbit_order.py",
                  "upbit_rate_limit.py"):
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(
            encoding="utf-8", errors="ignore",
        )
        assert not pat.search(text), f"{fname} imports jwt/hmac"


def test_frontend_has_no_upbit_secret_assignment():
    """frontend 에 UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 노출 부재."""
    fe = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
    if not fe.exists():
        pytest.skip("frontend/src not present")
    pat = re.compile(
        r"UPBIT_ACCESS_KEY|UPBIT_SECRET_KEY|API_SECRET|ACCESS_TOKEN",
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
    assert not hits, f"frontend leaks upbit secret reference: {hits}"


# ── 10. brokers __all__ exports ─────────────────────────────────

def test_brokers_module_exports_upbit_helpers():
    from app import brokers
    for name in ("UpbitPublicClient", "UpbitAccountClient", "UpbitOrderClient",
                 "normalize_upbit_market", "to_internal_symbol", "is_krw_market",
                 "parse_remaining_req", "should_throttle", "RateLimitState"):
        assert name in brokers.__all__, f"{name} not exported"
        assert hasattr(brokers, name)
