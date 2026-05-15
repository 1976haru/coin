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
