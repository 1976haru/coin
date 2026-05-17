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


# ─────────────────────────────────────────────────────────────────
# 체크리스트 #20 확장 — 스펙 메서드 명 / contract / 단일 경로 검증
# ─────────────────────────────────────────────────────────────────

import re
from pathlib import Path


# ── 8. 스펙 alias — fetch_price / get_balance ──────────────────

def test_fetch_price_alias_returns_float():
    m = MockExchangeAdapter()
    px = m.fetch_price("BTC/USDT")
    assert isinstance(px, float)
    assert px > 0


def test_fetch_price_matches_fetch_ticker_price():
    m = MockExchangeAdapter()
    assert m.fetch_price("ETH/USDT") == m.fetch_ticker("ETH/USDT").price


def test_get_balance_alias_matches_fetch_balance():
    m = MockExchangeAdapter(initial_balance_usdt=2500.0)
    assert m.get_balance() == m.fetch_balance()
    assert m.get_balance()["USDT"] == 2500.0


# ── 9. orderbook best_bid < best_ask ────────────────────────────

def test_orderbook_best_bid_lt_best_ask():
    m = MockExchangeAdapter()
    ob = m.fetch_orderbook("BTC/USDT", depth=5)
    assert ob.bids and ob.asks
    best_bid = ob.bids[0][0]
    best_ask = ob.asks[0][0]
    assert best_bid < best_ask


# ── 10. MARKET BUY / LIMIT SELL ─────────────────────────────────

def test_mock_place_order_market_buy_filled():
    m = MockExchangeAdapter(initial_balance_usdt=1_000.0)
    r = m.place_order({
        "symbol": "BTC/USDT", "side": "BUY",
        "order_type": "MARKET",
        "notional_usdt": 100.0, "price": 50_000,
    })
    assert r.status == "FILLED"
    assert r.order_id.startswith("mock-")
    assert r.route == "paper"


def test_mock_place_order_limit_sell_accepted():
    m = MockExchangeAdapter()
    r = m.place_order({
        "symbol": "BTC/USDT", "side": "SELL",
        "order_type": "LIMIT",
        "notional_usdt": 50.0, "price": 50_000,
    })
    assert r.status == "ACCEPTED"
    assert "LIMIT" in r.reason


def test_mock_place_order_limit_without_price_rejected():
    m = MockExchangeAdapter()
    r = m.place_order({
        "symbol": "BTC/USDT", "side": "BUY",
        "order_type": "LIMIT",
        "notional_usdt": 50.0,
    })
    assert r.status == "REJECTED"
    assert "LIMIT" in r.reason


def test_mock_cancel_known_order_id():
    m = MockExchangeAdapter()
    placed = m.place_order({
        "symbol": "BTC/USDT", "side": "SELL",
        "order_type": "LIMIT", "notional_usdt": 10, "price": 50_000,
    })
    canceled = m.cancel_order(placed.order_id)
    assert canceled.status == "ACCEPTED"
    assert canceled.order_id == placed.order_id


def test_mock_cancel_unknown_order_id_still_accepted():
    """grace — 알 수 없는 order_id 도 ACCEPTED + reason 명시."""
    m = MockExchangeAdapter()
    r = m.cancel_order("unknown-xyz")
    assert r.status == "ACCEPTED"
    assert "unknown" in r.reason.lower()


# ── 11. client_order_id 중복 처리 (idempotent) ──────────────────

def test_mock_idempotent_by_client_order_id():
    m = MockExchangeAdapter(initial_balance_usdt=1_000.0)
    req = {
        "symbol": "BTC/USDT", "side": "BUY",
        "order_type": "MARKET",
        "notional_usdt": 100.0, "price": 50_000,
        "client_order_id": "dup-001",
    }
    r1 = m.place_order(req)
    bal_after_first = m.get_balance()["USDT"]
    r2 = m.place_order(req)
    assert r1.order_id == r2.order_id
    assert r1.status == r2.status == "FILLED"
    # 중복 호출은 잔고를 두 번 차감하지 않는다
    assert m.get_balance()["USDT"] == bal_after_first


def test_mock_idempotency_via_idempotency_key_field():
    """idempotency_key 도 client_order_id 로 인식."""
    m = MockExchangeAdapter(initial_balance_usdt=1_000.0)
    req = {
        "symbol": "BTC/USDT", "side": "BUY",
        "order_type": "MARKET",
        "notional_usdt": 100.0, "price": 50_000,
        "idempotency_key": "idem-dup-001",
    }
    r1 = m.place_order(req)
    r2 = m.place_order(req)
    assert r1.order_id == r2.order_id


# ── 12. 잔고 부족 REJECTED ──────────────────────────────────────

def test_mock_insufficient_balance_rejected():
    m = MockExchangeAdapter(initial_balance_usdt=50.0)
    r = m.place_order({
        "symbol": "BTC/USDT", "side": "BUY",
        "order_type": "MARKET",
        "notional_usdt": 500.0, "price": 50_000,
    })
    assert r.status == "REJECTED"
    assert "insufficient_balance" in r.reason
    # 잔고는 차감되지 않아야 함
    assert m.get_balance()["USDT"] == 50.0


def test_mock_zero_notional_rejected():
    m = MockExchangeAdapter()
    r = m.place_order({"symbol": "BTC", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 0})
    assert r.status == "REJECTED"


# ── 13. LIVE 모드 거부 ─────────────────────────────────────────

def test_mock_rejects_live_mode_request():
    """mode/trading_mode 가 LIVE 인 주문은 mock 이 거부 (mock 은 PAPER 전용)."""
    m = MockExchangeAdapter()
    r = m.place_order({
        "symbol": "BTC/USDT", "side": "BUY",
        "order_type": "MARKET",
        "notional_usdt": 100, "price": 50_000,
        "mode": "LIVE",
    })
    assert r.status == "REJECTED"
    assert "LIVE" in r.reason
    assert r.route == "live_not_wired"


def test_mock_rejects_live_via_trading_mode_field():
    m = MockExchangeAdapter()
    r = m.place_order({
        "symbol": "BTC/USDT", "side": "BUY",
        "order_type": "MARKET",
        "notional_usdt": 100, "price": 50_000,
        "trading_mode": "LIVE",
    })
    assert r.status == "REJECTED"


def test_base_blocks_live_adapter_when_flag_false(monkeypatch):
    """capability.mode='LIVE' adapter 가 있어도 ENABLE_LIVE_TRADING=false 면 거부."""
    from app.core.config import get_settings, reset_settings_cache

    class _LiveAdapter(ExchangeAdapter):
        @property
        def capability(self):
            return AdapterCapability(
                name="livestub", mode="LIVE",
                can_fetch_balance=True, can_place_order=True,
                can_cancel_order=True, requires_secret=True,
            )

        def fetch_ticker(self, symbol):
            from datetime import datetime, timezone
            return Ticker(symbol=symbol, price=1.0, bid=0.99, ask=1.01,
                          spread_pct=0.01, volume_24h=0,
                          ts=datetime.now(timezone.utc))

        def fetch_orderbook(self, symbol, depth=5):
            from datetime import datetime, timezone
            return OrderBook(symbol=symbol, bids=(), asks=(),
                             ts=datetime.now(timezone.utc))

        def _place_order_impl(self, order):  # 호출되면 안 됨 (base 가 사전 거부)
            raise AssertionError("LIVE _place_order_impl should not be reached")

        def _fetch_balance_impl(self):
            return {}

        def _cancel_order_impl(self, order_id):
            raise AssertionError("should not be reached")

    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    reset_settings_cache()
    try:
        assert get_settings().enable_live_trading is False
        r = _LiveAdapter().place_order({
            "symbol": "BTC", "side": "BUY",
            "order_type": "MARKET", "notional_usdt": 100,
        })
        assert r.status == "REJECTED"
        assert r.route == "live_not_wired"
        assert "ENABLE_LIVE_TRADING" in r.reason
    finally:
        reset_settings_cache()


# ── 14. raw_response 에 secret 없음 ─────────────────────────────

def test_mock_audit_strips_secret_fields():
    m = MockExchangeAdapter()
    r = m.place_order({
        "symbol": "BTC/USDT", "side": "BUY",
        "order_type": "MARKET",
        "notional_usdt": 100, "price": 50_000,
        # 의도적으로 secret 류 키를 넣어본다 — 응답 audit 에 새서는 안 됨
        "api_key": "AAAA", "api_secret": "BBBB",
        "access_token": "CCCC", "passphrase": "DDDD",
    })
    audit = r.audit or {}
    audit_str = repr(audit).lower()
    for needle in ("aaaa", "bbbb", "cccc", "dddd",
                   "api_key", "api_secret", "access_token", "passphrase"):
        assert needle not in audit_str, f"secret leaked: {needle} in {audit!r}"


# ── 15. Upbit/OKX stub: place_order disabled ─────────────────────

def test_upbit_adapter_place_order_disabled():
    from app.brokers.upbit_adapter import UpbitAdapter
    # client 주입으로 네트워크 호출 없음. UpbitAdapter 는 lazy import 라 client 없이
    # 인스턴스화 시 pyupbit import 시도 — 미설치라면 ImportError. 본 테스트는 인스턴스
    # 화에 성공한 경우에만 capability 검증, 아니면 skip.
    try:
        a = UpbitAdapter(client=object())
    except (ImportError, ModuleNotFoundError):
        pytest.skip("pyupbit unavailable in CI")
    assert a.capability.mode == "READ_ONLY"
    assert a.capability.can_place_order is False
    with pytest.raises(ExchangeAdapterDisabledError):
        a.place_order({"symbol": "BTC", "side": "BUY", "notional_usdt": 50,
                       "order_type": "MARKET"})


def test_okx_adapter_place_order_disabled():
    from app.brokers.okx_adapter import OkxAdapter
    try:
        a = OkxAdapter(client=object())
    except (ImportError, ModuleNotFoundError):
        pytest.skip("ccxt unavailable in CI")
    assert a.capability.mode == "READ_ONLY"
    assert a.capability.can_place_order is False
    with pytest.raises(ExchangeAdapterDisabledError):
        a.place_order({"symbol": "BTC", "side": "BUY", "notional_usdt": 50,
                       "order_type": "MARKET"})


def test_upbit_adapter_rejects_api_keys():
    from app.brokers.upbit_adapter import UpbitAdapter
    with pytest.raises(ValueError):
        UpbitAdapter(api_key="x", api_secret="y", client=object())


def test_okx_adapter_rejects_api_keys():
    from app.brokers.okx_adapter import OkxAdapter
    with pytest.raises(ValueError):
        OkxAdapter(api_key="x", api_secret="y", api_password="z",
                   client=object())


# ── 16. AI/전략이 adapter 직접 import / place_order 금지 ────────

_REPO_BACKEND_APP = Path(__file__).resolve().parent.parent / "app"


def _scan_for_pattern(directory: Path, pattern: re.Pattern, glob: str = "**/*.py") -> list[Path]:
    matches: list[Path] = []
    for p in directory.glob(glob):
        if "__pycache__" in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            matches.append(p)
    return matches


def test_strategies_do_not_import_brokers():
    """app/strategies 의 어떤 모듈도 app.brokers 를 import 하지 않는다 (모듈 경계)."""
    pat = re.compile(
        r"^\s*(?:from\s+app\.brokers|import\s+app\.brokers)",
        re.M,
    )
    hits = _scan_for_pattern(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy imports app.brokers: {hits}"


def test_agents_do_not_import_brokers():
    """app/agents 의 trading agent 가 app.brokers 를 import 하지 않는다.

    예외: ``compliance.py`` 는 CLAUDE.md 안전 원칙(출금 메서드 부재 등)을 *검증*
    하기 위해 brokers 를 import 한다 — trading 의도가 아니므로 허용."""
    pat = re.compile(
        r"^\s*(?:from\s+app\.brokers|import\s+app\.brokers)",
        re.M,
    )
    whitelist = {"compliance.py"}
    hits = [p for p in _scan_for_pattern(_REPO_BACKEND_APP / "agents", pat)
            if p.name not in whitelist]
    assert not hits, f"agent imports app.brokers: {hits}"


def test_strategies_do_not_call_place_order():
    """Strategy 코드가 ``.place_order(`` / ``.cancel_order(`` / ``.fetch_balance(`` 를
    직접 호출하지 않는다 (단일 주문 경로 보호)."""
    pat = re.compile(
        r"\.(place_order|cancel_order|fetch_balance|get_balance)\s*\(",
    )
    hits = _scan_for_pattern(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy calls broker order methods: {hits}"


def test_agents_do_not_call_place_order():
    pat = re.compile(
        r"\.(place_order|cancel_order|fetch_balance|get_balance)\s*\(",
    )
    hits = _scan_for_pattern(_REPO_BACKEND_APP / "agents", pat)
    assert not hits, f"agent calls broker order methods: {hits}"


def test_market_modules_do_not_call_place_order():
    """market data 계층도 주문 송신 금지 (#15·#18·#19 회귀)."""
    pat = re.compile(
        r"\.(place_order|cancel_order|fetch_balance|get_balance)\s*\(",
    )
    hits = _scan_for_pattern(_REPO_BACKEND_APP / "market", pat)
    assert not hits, f"market module calls broker order methods: {hits}"


# ── 17. 금지 문자열 / 외부 네트워크 호출 정적 검증 ──────────────

def test_brokers_no_live_trading_true_literal():
    """production broker 파일에 ENABLE_LIVE_TRADING = True 가 없음."""
    pat = re.compile(r"ENABLE_LIVE_TRADING\s*=\s*True")
    hits = _scan_for_pattern(_REPO_BACKEND_APP / "brokers", pat)
    assert not hits, f"ENABLE_LIVE_TRADING=True found in: {hits}"


def test_brokers_no_obvious_secret_assignment():
    """production broker 파일에 'API_SECRET = ...' / 'ACCESS_TOKEN = ...' 등의
    상수 대입이 없음. CLAUDE.md §2.1."""
    pat = re.compile(
        r"^\s*(API_SECRET|ACCESS_TOKEN|KIS_APP_KEY|KIS_APP_SECRET)\s*=\s*['\"]",
        re.M,
    )
    hits = _scan_for_pattern(_REPO_BACKEND_APP / "brokers", pat)
    assert not hits, f"secret literal assignment in: {hits}"


def test_mock_broker_no_network_calls():
    """mock_broker.py 가 requests/httpx/hmac 을 import 하지 않는다."""
    pat = re.compile(
        r"^\s*(?:import\s+(?:requests|httpx|hmac)|"
        r"from\s+(?:requests|httpx|hmac))",
        re.M,
    )
    hits = _scan_for_pattern(_REPO_BACKEND_APP / "brokers",
                             pat, glob="mock_broker.py")
    assert not hits, f"mock_broker imports network module: {hits}"


def test_base_adapter_no_network_calls():
    pat = re.compile(
        r"^\s*(?:import\s+(?:requests|httpx|hmac)|"
        r"from\s+(?:requests|httpx|hmac))",
        re.M,
    )
    hits = _scan_for_pattern(_REPO_BACKEND_APP / "brokers",
                             pat, glob="base.py")
    assert not hits, f"base.py imports network module: {hits}"


# ── 18. ExchangeAdapter 가 spec 메서드를 모두 노출 ──────────────

def test_exchange_adapter_exposes_spec_methods():
    """스펙(#20)이 요구하는 공통 메서드 명이 ExchangeAdapter 에 모두 정의되어 있음."""
    for name in ("fetch_price", "fetch_ticker", "fetch_orderbook",
                 "fetch_balance", "get_balance", "place_order", "cancel_order"):
        assert hasattr(ExchangeAdapter, name), f"missing spec method: {name}"


# ── 19. AdapterCapability dict 에 secret 부재 ───────────────────

def test_capability_to_dict_no_secret_values():
    """AdapterCapability.to_dict() 에 secret *값* 이 들어가지 않는다.

    ``requires_secret`` 같은 *bool 메타* 키는 허용 (값이 bool/숫자/None) — 실제
    secret 문자열이 값으로 들어가면 실패. capability 는 메타데이터 객체.
    """
    cap = AdapterCapability(
        name="x", mode="LIVE",
        can_place_order=True, requires_secret=True,
    )
    d = cap.to_dict()
    bad_substrings = ("api_key", "api_secret", "access_token",
                      "passphrase", "private_key", "password")
    # 값이 *문자열* 인 항목 중에 secret 류 토큰을 포함하면 실패. bool/numeric 메타는 허용.
    for k, v in d.items():
        if isinstance(v, str):
            for needle in bad_substrings:
                assert needle not in v.lower(), (
                    f"secret-like string in capability.to_dict[{k}]: {v!r}"
                )
