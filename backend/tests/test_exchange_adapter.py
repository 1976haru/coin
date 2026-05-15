"""체크리스트 #20 Exchange Adapter Interface — 회귀 테스트.

검증:
  1. AdapterCapability 기본 / to_dict 직렬화
  2. ExchangeAdapter 추상 인터페이스 강제
  3. capability 외 동작 호출 시 ExchangeAdapterDisabledError
  4. MockExchangeAdapter 결정론 (동일 symbol → 동일 가격)
  5. MockExchangeAdapter 가 MarketDataSource Protocol 만족
  6. 출금 메서드 부재 (assert_no_withdrawal_methods)
  7. ExchangeAdapter 가 collector 에 그대로 주입 가능
"""
from __future__ import annotations
import pytest

from app.brokers import (
    ExchangeAdapter, AdapterCapability, MockExchangeAdapter,
    ExchangeAdapterDisabledError,
    conforms_to_market_data_source, assert_no_withdrawal_methods,
)
from app.brokers.paper_broker import PaperBroker
from app.market.collector import MarketDataCollector, MarketDataSource
from app.schemas import OrderRequest, OrderBook, Ticker


# ── 1. AdapterCapability ─────────────────────────────────────────

def test_capability_defaults():
    cap = AdapterCapability(name="x", mode="PAPER")
    assert cap.can_fetch_ticker is True
    assert cap.can_fetch_orderbook is True
    assert cap.can_fetch_balance is False
    assert cap.can_place_order is False
    assert cap.supports_futures is False


def test_capability_to_dict_roundtrip_keys():
    cap = AdapterCapability(name="x", mode="LIVE",
                            can_fetch_balance=True, can_place_order=True,
                            requires_secret=True)
    d = cap.to_dict()
    for k in ("name", "mode", "can_fetch_ticker", "can_fetch_orderbook",
              "can_fetch_balance", "can_place_order", "can_cancel_order",
              "supports_futures", "requires_secret"):
        assert k in d


# ── 2. 추상 인터페이스 강제 ──────────────────────────────────────

def test_cannot_instantiate_abstract_adapter():
    with pytest.raises(TypeError):
        ExchangeAdapter()  # type: ignore[abstract]


def test_subclass_must_implement_required_methods():
    """capability + fetch_ticker + fetch_orderbook 은 필수."""

    class Incomplete(ExchangeAdapter):
        @property
        def capability(self):
            return AdapterCapability(name="x", mode="PAPER")
        # fetch_ticker 미구현 → 추상 — 인스턴스화 실패
    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


# ── 3. capability 외 동작 보호 ───────────────────────────────────

class _ReadOnlyAdapter(ExchangeAdapter):
    """잔고/주문 capability 가 false — 호출 시 disabled 처리 검증용."""

    @property
    def capability(self) -> AdapterCapability:
        return AdapterCapability(
            name="readonly", mode="READ_ONLY",
            can_fetch_balance=False,
            can_place_order=False,
            can_cancel_order=False,
        )

    def fetch_ticker(self, symbol: str) -> Ticker:
        from datetime import datetime, timezone
        return Ticker(symbol=symbol, price=100.0, bid=99.5, ask=100.5,
                      spread_pct=0.01, volume_24h=0.0,
                      ts=datetime.now(timezone.utc))

    def fetch_orderbook(self, symbol: str, depth: int = 5) -> OrderBook:
        from datetime import datetime, timezone
        return OrderBook(symbol=symbol, bids=(), asks=(),
                         ts=datetime.now(timezone.utc))


def test_disabled_balance_raises():
    a = _ReadOnlyAdapter()
    with pytest.raises(ExchangeAdapterDisabledError):
        a.fetch_balance()


def test_disabled_place_order_raises():
    a = _ReadOnlyAdapter()
    with pytest.raises(ExchangeAdapterDisabledError):
        a.place_order({"symbol": "BTC", "side": "BUY", "notional_usdt": 50})


def test_disabled_cancel_returns_rejected():
    """cancel 은 호출 자체가 흔하므로 raise 대신 REJECTED 결과로 grace."""
    a = _ReadOnlyAdapter()
    r = a.cancel_order("xyz")
    assert r.status == "REJECTED"
    assert "disabled" in r.reason


# ── 4. MockExchangeAdapter 결정론 ────────────────────────────────

def test_mock_adapter_capability():
    m = MockExchangeAdapter("mock1")
    cap = m.capability
    assert cap.name == "mock1"
    assert cap.mode == "PAPER"
    assert cap.can_place_order is True
    assert cap.requires_secret is False
    assert cap.supports_futures is False


def test_mock_adapter_ticker_is_deterministic_for_symbol():
    m = MockExchangeAdapter()
    t1 = m.fetch_ticker("BTC/USDT")
    t2 = m.fetch_ticker("BTC/USDT")
    assert t1.price == t2.price


def test_mock_adapter_different_symbols_different_prices():
    m = MockExchangeAdapter()
    a = m.fetch_ticker("BTC")
    b = m.fetch_ticker("ETH")
    assert a.price != b.price


def test_mock_adapter_orderbook_depth():
    m = MockExchangeAdapter()
    ob = m.fetch_orderbook("BTC", depth=8)
    assert len(ob.bids) == 8
    assert len(ob.asks) == 8


def test_mock_adapter_fetch_balance():
    m = MockExchangeAdapter(initial_balance_usdt=5_000.0)
    bal = m.fetch_balance()
    assert bal["USDT"] == 5_000.0


def test_mock_adapter_place_order_decrements_balance_on_buy():
    m = MockExchangeAdapter(initial_balance_usdt=1_000.0)
    r = m.place_order({"symbol": "BTC/USDT", "side": "BUY",
                       "notional_usdt": 100, "price": 50_000})
    assert r.status == "FILLED"
    assert m.fetch_balance()["USDT"] == 900.0
    assert m.filled_count == 1


def test_mock_adapter_place_order_with_typed_request():
    """OrderRequest 객체를 그대로 받을 수 있어야 함."""
    m = MockExchangeAdapter()
    req = OrderRequest(symbol="ETH/USDT", side="BUY",
                       notional_usdt=50.0, price=3000.0)
    r = m.place_order(req)
    assert r.status == "FILLED"
    assert r.symbol == "ETH/USDT"


def test_mock_adapter_cancel_returns_accepted():
    m = MockExchangeAdapter()
    r = m.cancel_order("mock-001")
    assert r.status == "ACCEPTED"
    assert r.order_id == "mock-001"


# ── 5. MarketDataSource Protocol 호환 ────────────────────────────

def test_mock_adapter_satisfies_market_data_source_protocol():
    m = MockExchangeAdapter()
    assert conforms_to_market_data_source(m) is True
    # collector 에 그대로 주입 가능
    isinstance_check: MarketDataSource = m  # type 체크용
    assert isinstance_check is m


def test_collector_can_use_exchange_adapter_directly():
    """ExchangeAdapter 인스턴스를 collector source 로 그대로 주입."""
    m = MockExchangeAdapter("upbit_mock")
    c = MarketDataCollector(sources={"upbit": m})
    report = c.collect([("BTC", "upbit"), ("ETH", "upbit")])
    assert report.ok_count == 2
    assert all(e.ticker is not None for e in report.entries)


# ── 6. 출금 메서드 부재 회귀 ─────────────────────────────────────

def test_no_withdrawal_methods_on_base():
    assert_no_withdrawal_methods(ExchangeAdapter)


def test_no_withdrawal_methods_on_mock():
    assert_no_withdrawal_methods(MockExchangeAdapter)


def test_no_withdrawal_methods_on_paper_broker():
    """PaperBroker 도 출금 메서드가 없어야 한다 (영구 안전)."""
    assert_no_withdrawal_methods(PaperBroker)


# ── 7. 모듈 경계 ─────────────────────────────────────────────────

def test_brokers_module_exports_canonical_types():
    from app import brokers
    expected = {
        "ExchangeAdapter", "AdapterCapability", "AdapterMode",
        "ExchangeAdapterDisabledError",
        "conforms_to_market_data_source", "assert_no_withdrawal_methods",
        "MockExchangeAdapter",
        "PaperBroker", "PaperOrderResult",
    }
    actual = set(brokers.__all__)
    assert expected.issubset(actual), f"missing exports: {expected - actual}"
