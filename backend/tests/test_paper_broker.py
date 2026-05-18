"""체크리스트 #25 PaperBroker (PaperMarketBroker + PaperTrader) — 회귀 테스트.

검증:
  PaperMarketBroker:
    1. source 미주입 + require_source=True 면 모든 주문 REJECTED
    2. 정상 source 시세 사용해 MARKET BUY FILLED + 잔고/포지션 반영
    3. LIMIT crossable 즉시 체결 / non-crossable open + cancel
    4. universe 화이트리스트 밖 BUY REJECTED (EXIT 는 허용)
    5. stale ticker BUY 차단 (EXIT 는 허용)
    6. fee/slippage 적용
    7. allow_short / allow_margin
    8. duplicate client_order_id idempotent
    9. LIVE mode 거부 (mode/trading_mode)
   10. 응답 envelope (mode/is_real_trade/execution_source/warning/fill_quality_warning)
   11. audit secret sanitize
  PaperTrader:
   12. select_paper_source 카탈로그 검증 (unknown → error)
   13. kis_readonly_stub 선택 시 warning
   14. start/stop/reset 상태 전이
   15. submit_paper_order_via_gateway → gateway.submit 위임 + 로그 + envelope
   16. running=False 면 PaperTraderError
   17. LIVE mode 직접 시도 차단 (gateway 도달 전)
   18. paper logs 필터 (client_order_id)
  REST API:
   19. GET /api/paper/status, /orders, /sources
   20. POST /api/paper/{start,stop,reset,source} admin gating
  정적 회귀:
   21. paper_market_broker / paper_trader 가 외부 네트워크 SDK import 부재
   22. Strategy/Agent 가 paper 모듈 직접 import/instantiate 부재
   23. brokers __all__ exports
"""
from __future__ import annotations
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.brokers import (
    MockExchangeAdapter,
    PaperMarketBroker, PaperMarketBrokerConfig, make_paper_universe,
    PaperTrader, PaperTraderError, AVAILABLE_PAPER_SOURCES,
)
from app.schemas import Ticker


# ── Fake source — 외부 호출 0 ────────────────────────────────────


class _FakeSource:
    """결정론 가짜 source. ts 를 외부에서 조작 가능 — staleness 테스트용."""

    name = "fake_paper_source"

    def __init__(
        self,
        prices: dict | None = None,
        ts: datetime | None = None,
        raise_on: tuple[str, ...] = (),
    ):
        self._prices = prices or {"BTC-USDT": 50_000.0, "ETH-USDT": 3_000.0}
        self._ts = ts or datetime.now(timezone.utc)
        self._raise_on = set(raise_on)
        self.calls: list[str] = []

    def fetch_ticker(self, symbol: str) -> Ticker | None:
        self.calls.append(symbol)
        if symbol in self._raise_on:
            raise RuntimeError("fake source error")
        price = self._prices.get(symbol)
        if price is None:
            return None
        bid = price * 0.9995
        ask = price * 1.0005
        return Ticker(
            symbol=symbol, price=price, bid=bid, ask=ask,
            spread_pct=(ask - bid) / bid, volume_24h=0.0, ts=self._ts,
        )

    def set_ts(self, ts: datetime) -> None:
        self._ts = ts


# ── PaperMarketBroker 픽스처 ─────────────────────────────────────


def _make_broker(
    *,
    source: object | None = None,
    universe: tuple[str, ...] | None = None,
    fee_bps: float = 5.0,
    slippage_bps: float = 0.0,
    initial_balances: dict | None = None,
    max_ticker_age_sec: float = 30.0,
    allow_short: bool = False,
    allow_margin: bool = False,
    require_source: bool = True,
) -> PaperMarketBroker:
    cfg = PaperMarketBrokerConfig(
        base_currency="USDT",
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        allow_short=allow_short,
        allow_margin=allow_margin,
        universe=universe,
        max_ticker_age_sec=max_ticker_age_sec,
        require_source=require_source,
        initial_balances=initial_balances or {"USDT": 10_000.0},
    )
    return PaperMarketBroker(source=source, config=cfg)


# ── 1. source 없음 ───────────────────────────────────────────────

def test_paper_broker_rejects_when_no_source():
    b = _make_broker(source=None, require_source=True)
    r = b.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usdt": 100,
    })
    assert r["status"] == "REJECTED"
    assert "market data" in r["reason"].lower()
    assert r["mode"] == "PAPER"


# ── 2. MARKET BUY ────────────────────────────────────────────────

def test_paper_broker_market_buy_uses_source_price():
    src = _FakeSource()
    b = _make_broker(source=src)
    r = b.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usdt": 100,
    })
    assert r["status"] == "FILLED"
    assert r["filled_price"] == 50_000.0
    assert r["mode"] == "PAPER"
    assert r["is_real_trade"] is False
    assert r["execution_source"] == "paper_broker"
    assert "Paper execution only" in r["warning"]
    assert "Paper fills may differ" in r["fill_quality_warning"]
    # 잔고 반영
    assert b.get_balance("USDT")["free"] == pytest.approx(10_000 - 100 - 0.05)
    assert b.get_balance("BTC")["free"] == pytest.approx(0.002)
    # 포지션
    p = b.get_position("BTC-USDT")
    assert p["qty"] == pytest.approx(0.002)
    assert p["avg_entry_price"] == 50_000.0


def test_paper_broker_market_sell_reduces_position():
    src = _FakeSource()
    b = _make_broker(source=src)
    b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                   "order_type": "MARKET", "notional_usdt": 100})
    src._prices["BTC-USDT"] = 55_000.0
    r = b.place_order({"symbol": "BTC-USDT", "side": "SELL",
                       "order_type": "MARKET", "qty": 0.001})
    assert r["status"] == "FILLED"
    p = b.get_position("BTC-USDT")
    assert p["realized_pnl"] == pytest.approx(5.0)


# ── 3. LIMIT 주문 ────────────────────────────────────────────────

def test_paper_broker_limit_buy_crossable_immediate():
    src = _FakeSource()
    b = _make_broker(source=src)
    r = b.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "LIMIT", "notional_usdt": 100, "price": 51_000,
    })
    assert r["status"] == "FILLED"


def test_paper_broker_limit_buy_non_crossable_open_then_cancel():
    src = _FakeSource()
    b = _make_broker(source=src)
    r = b.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "LIMIT", "notional_usdt": 100, "price": 49_000,
    })
    assert r["status"] == "ACCEPTED"
    locked_before = b.get_balance("USDT")["locked"]
    assert locked_before > 0
    c = b.cancel_order(r["order_id"])
    assert c["status"] == "ACCEPTED"
    assert b.get_balance("USDT")["locked"] == 0.0


# ── 4. Universe whitelist ────────────────────────────────────────

def test_paper_broker_universe_blocks_unknown_buy():
    src = _FakeSource(prices={"BTC-USDT": 50_000.0, "DOGE-USDT": 0.1})
    b = _make_broker(
        source=src,
        universe=make_paper_universe(["BTC-USDT", "ETH-USDT"]),
    )
    r = b.place_order({"symbol": "DOGE-USDT", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 50})
    assert r["status"] == "REJECTED"
    assert "review_required" in r["reason"]


def test_paper_broker_universe_allows_exit_outside():
    """EXIT(SELL) 는 universe 밖이어도 허용 — 위험 축소이므로."""
    src = _FakeSource(prices={"DOGE-USDT": 0.1})
    b = _make_broker(
        source=src,
        universe=make_paper_universe(["BTC-USDT"]),
        initial_balances={"USDT": 100.0, "DOGE": 100.0},
    )
    r = b.place_order({"symbol": "DOGE-USDT", "side": "SELL",
                       "order_type": "MARKET", "qty": 50})
    assert r["status"] == "FILLED"


# ── 5. staleness ─────────────────────────────────────────────────

def test_paper_broker_stale_ticker_blocks_buy():
    src = _FakeSource(ts=datetime.now(timezone.utc) - timedelta(seconds=120))
    b = _make_broker(source=src, max_ticker_age_sec=30.0)
    r = b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 100})
    assert r["status"] == "REJECTED"
    assert "stale" in r["reason"].lower()


def test_paper_broker_stale_ticker_allows_sell():
    """EXIT 는 stale 에서도 허용."""
    src = _FakeSource(ts=datetime.now(timezone.utc) - timedelta(seconds=120))
    b = _make_broker(
        source=src, max_ticker_age_sec=30.0,
        initial_balances={"USDT": 100.0, "BTC": 0.01},
    )
    r = b.place_order({"symbol": "BTC-USDT", "side": "SELL",
                       "order_type": "MARKET", "qty": 0.001})
    assert r["status"] == "FILLED"


# ── 6. fee / slippage ────────────────────────────────────────────

def test_paper_broker_fee_applied():
    src = _FakeSource()
    b = _make_broker(source=src, fee_bps=10.0)  # 0.1%
    r = b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 100})
    # 0.1% of 100 = 0.10
    assert r["fee_usdt"] == pytest.approx(0.10)


def test_paper_broker_slippage_buy_up():
    src = _FakeSource()
    b = _make_broker(source=src, slippage_bps=10.0)  # 0.1%
    r = b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 100})
    assert r["filled_price"] > 50_000


# ── 7. allow_short / allow_margin ────────────────────────────────

def test_paper_broker_allow_short_false_blocks_oversell():
    src = _FakeSource()
    b = _make_broker(source=src, allow_short=False)
    r = b.place_order({"symbol": "BTC-USDT", "side": "SELL",
                       "order_type": "MARKET", "qty": 0.5})
    assert r["status"] == "REJECTED"
    assert "insufficient_base_balance" in r["reason"]


def test_paper_broker_allow_margin_false_blocks_overbuy():
    src = _FakeSource()
    b = _make_broker(source=src, initial_balances={"USDT": 50.0},
                     allow_margin=False)
    r = b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 100})
    assert r["status"] == "REJECTED"
    assert "insufficient_balance" in r["reason"]


# ── 8. duplicate client_order_id ─────────────────────────────────

def test_paper_broker_idempotent_client_order_id():
    src = _FakeSource()
    b = _make_broker(source=src)
    req = {
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usdt": 100,
        "client_order_id": "dup-1",
    }
    r1 = b.place_order(req)
    bal = b.get_balance("USDT")["free"]
    r2 = b.place_order(req)
    assert r1["order_id"] == r2["order_id"]
    # 잔고 이중 차감 없음
    assert b.get_balance("USDT")["free"] == bal


# ── 9. LIVE mode 거부 ───────────────────────────────────────────

def test_paper_broker_rejects_live_mode_field():
    src = _FakeSource()
    b = _make_broker(source=src)
    r = b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 100,
                       "mode": "LIVE"})
    assert r["status"] == "REJECTED"
    assert "LIVE" in r["reason"]
    assert r["route"] == "live_not_wired"


def test_paper_broker_rejects_live_trading_mode_field():
    src = _FakeSource()
    b = _make_broker(source=src)
    r = b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 100,
                       "trading_mode": "LIVE"})
    assert r["status"] == "REJECTED"


# ── 10. envelope 필드 ────────────────────────────────────────────

def test_paper_broker_envelope_fields_present():
    src = _FakeSource()
    b = _make_broker(source=src)
    r = b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 100})
    for k in ("mode", "is_real_trade", "execution_source",
              "warning", "fill_quality_warning"):
        assert k in r
    assert r["mode"] == "PAPER"
    assert r["is_real_trade"] is False
    assert r["execution_source"] == "paper_broker"


def test_paper_broker_reject_envelope_fields_present():
    src = _FakeSource()
    b = _make_broker(source=src, max_ticker_age_sec=0.001)
    src.set_ts(datetime.now(timezone.utc) - timedelta(seconds=60))
    r = b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 100})
    assert r["status"] == "REJECTED"
    assert r["mode"] == "PAPER"
    assert r["is_real_trade"] is False
    assert "Paper fills" in r["fill_quality_warning"]


# ── 11. audit secret sanitize ────────────────────────────────────

def test_paper_broker_audit_strips_secrets():
    src = _FakeSource()
    b = _make_broker(source=src)
    r = b.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usdt": 100,
        "api_key": "AAAA", "api_secret": "BBBB",
        "passphrase": "CCCC", "ok_access_sign": "DDDD",
    })
    audit_str = repr(r.get("audit") or {}).lower()
    for bad in ("aaaa", "bbbb", "cccc", "dddd",
                "api_key", "api_secret", "passphrase", "ok_access_sign"):
        assert bad not in audit_str


# ── 12. account_summary 표시 ─────────────────────────────────────

def test_paper_broker_account_summary_marks_paper():
    src = _FakeSource()
    b = _make_broker(source=src)
    b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                   "order_type": "MARKET", "notional_usdt": 100})
    s = b.get_account_summary()
    assert s["mode"] == "PAPER"
    assert s["is_real_trade"] is False
    assert s["execution_source"] == "paper_broker"
    assert s["filled_count"] == 1
    assert s["source_name"] == "fake_paper_source"


# ── 13. PaperTrader source 선택 ──────────────────────────────────


def _trader_with_fake(source: object | None = None) -> PaperTrader:
    """source_factory 로 fake 를 주입한 PaperTrader."""

    def factory(name: str):
        if name == "kis_readonly_stub":
            return None
        return source

    return PaperTrader(
        default_source_name="mock",
        source_factory=factory,
        broker_config=PaperMarketBrokerConfig(
            base_currency="USDT", fee_bps=5.0,
            initial_balances={"USDT": 10_000.0},
        ),
    )


def test_trader_select_unknown_source_raises():
    t = _trader_with_fake()
    with pytest.raises(PaperTraderError):
        t.select_paper_source("nope")


def test_trader_select_kis_stub_sets_warning():
    t = _trader_with_fake(source=_FakeSource())
    t.select_paper_source("kis_readonly_stub")
    s = t.get_paper_status()
    assert s["source_name"] == "kis_readonly_stub"
    assert any("kis_readonly_stub" in w for w in s["warnings"])
    # KIS stub 으로 변경 시 broker.source 는 None — 모든 주문 REJECTED
    assert t.broker is not None
    assert t.broker.source is None


def test_trader_select_other_source_clears_kis_warning():
    t = _trader_with_fake(source=_FakeSource())
    t.select_paper_source("kis_readonly_stub")
    t.select_paper_source("mock")
    s = t.get_paper_status()
    assert not any("kis_readonly_stub" in w for w in s["warnings"])


def test_trader_available_sources_includes_all_five():
    t = _trader_with_fake()
    s = t.get_paper_status()
    assert set(s["available_sources"]) == set(AVAILABLE_PAPER_SOURCES)
    assert "kis_readonly_stub" in s["available_sources"]


# ── 14. start/stop/reset ────────────────────────────────────────

def test_trader_start_stop_reset_state_transitions():
    t = _trader_with_fake(source=_FakeSource())
    s = t.get_paper_status()
    assert s["running"] is False
    t.start_paper()
    assert t.get_paper_status()["running"] is True
    t.stop_paper()
    assert t.get_paper_status()["running"] is False
    t.start_paper()
    t.reset_paper()
    assert t.get_paper_status()["running"] is False
    assert t.get_paper_status()["orders_submitted"] == 0


# ── 15. submit_paper_order_via_gateway ──────────────────────────


class _FakeGateway:
    """간단한 fake — broker 를 직접 호출해 시뮬."""

    def __init__(self, broker: PaperMarketBroker):
        self._broker = broker
        self.submitted: list[dict] = []

    def submit(self, order: dict) -> dict:
        self.submitted.append(order)
        return self._broker.place_order(order)


def test_trader_submit_via_gateway_appends_log_and_envelope():
    src = _FakeSource()
    t = _trader_with_fake(source=src)
    t.start_paper()
    g = _FakeGateway(t.broker)
    r = t.submit_paper_order_via_gateway({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usdt": 100,
        "client_order_id": "cli-1",
    }, gateway=g)
    assert r["status"] == "FILLED"
    assert r["mode"] == "PAPER"
    assert r["is_real_trade"] is False
    # gateway 가 호출되었음
    assert len(g.submitted) == 1
    # 로그 적재
    logs = t.get_paper_logs()
    assert len(logs) == 1
    assert logs[0]["client_order_id"] == "cli-1"
    assert logs[0]["status"] == "FILLED"
    # status 카운터
    assert t.get_paper_status()["orders_filled"] == 1


def test_trader_submit_when_not_running_raises():
    t = _trader_with_fake(source=_FakeSource())
    g = _FakeGateway(t.broker)
    with pytest.raises(PaperTraderError):
        t.submit_paper_order_via_gateway({
            "symbol": "BTC-USDT", "side": "BUY",
            "order_type": "MARKET", "notional_usdt": 100,
        }, gateway=g)


def test_trader_submit_rejects_live_request_before_gateway():
    t = _trader_with_fake(source=_FakeSource())
    t.start_paper()
    g = _FakeGateway(t.broker)
    with pytest.raises(PaperTraderError):
        t.submit_paper_order_via_gateway({
            "symbol": "BTC-USDT", "side": "BUY",
            "order_type": "MARKET", "notional_usdt": 100,
            "mode": "LIVE",
        }, gateway=g)
    # gateway 에 도달하지 않음
    assert g.submitted == []


def test_trader_submit_requires_gateway_with_submit():
    t = _trader_with_fake(source=_FakeSource())
    t.start_paper()
    with pytest.raises(PaperTraderError):
        t.submit_paper_order_via_gateway({
            "symbol": "BTC-USDT", "side": "BUY",
            "order_type": "MARKET", "notional_usdt": 100,
        }, gateway=object())  # submit 없음


# ── 16. logs 필터 ───────────────────────────────────────────────

def test_trader_logs_filter_by_client_id():
    src = _FakeSource()
    t = _trader_with_fake(source=src)
    t.start_paper()
    g = _FakeGateway(t.broker)
    for i in range(3):
        t.submit_paper_order_via_gateway({
            "symbol": "BTC-USDT", "side": "BUY",
            "order_type": "MARKET", "notional_usdt": 10,
            "client_order_id": f"cli-{i}",
        }, gateway=g)
    f = t.get_paper_logs(client_order_id="cli-1")
    assert len(f) == 1
    assert f[0]["client_order_id"] == "cli-1"


# ── 17. REST API ────────────────────────────────────────────────


@pytest.fixture
def api_client():
    from app.main import app
    from app.api.deps import get_paper_trader as _dep

    # override 로 fake source 주입한 트레이더 사용
    t = _trader_with_fake(source=_FakeSource())
    app.dependency_overrides[_dep] = lambda: t
    yield TestClient(app), t
    app.dependency_overrides.pop(_dep, None)


def test_api_paper_status(api_client):
    client, _ = api_client
    r = client.get("/api/paper/status")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False
    assert body["mode"] == "PAPER"
    assert body["is_real_trade"] is False
    assert body["execution_source"] == "paper_trader"


def test_api_paper_sources(api_client):
    client, _ = api_client
    r = client.get("/api/paper/sources")
    assert r.status_code == 200
    body = r.json()
    assert "kis_readonly_stub" in body["available"]
    assert body["is_real_trade"] is False


def test_api_paper_start_requires_admin(api_client):
    client, _ = api_client
    r = client.post("/api/paper/start")
    assert r.status_code == 401


def test_api_paper_start_with_admin(api_client):
    from app.core.config import get_settings
    client, _ = api_client
    token = get_settings().admin_token
    r = client.post("/api/paper/start", headers={"X-Admin-Token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is True
    assert body["mode"] == "PAPER"


def test_api_paper_source_change(api_client):
    from app.core.config import get_settings
    client, _ = api_client
    token = get_settings().admin_token
    r = client.post("/api/paper/source",
                    json={"name": "kis_readonly_stub"},
                    headers={"X-Admin-Token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["selected"] == "kis_readonly_stub"
    assert any("kis_readonly_stub" in w for w in body["status"]["warnings"])


def test_api_paper_source_rejects_unknown(api_client):
    from app.core.config import get_settings
    client, _ = api_client
    token = get_settings().admin_token
    r = client.post("/api/paper/source",
                    json={"name": "zzz_invalid"},
                    headers={"X-Admin-Token": token})
    assert r.status_code == 400


def test_api_paper_orders_empty(api_client):
    client, t = api_client
    r = client.get("/api/paper/orders")
    assert r.status_code == 200
    body = r.json()
    assert body["orders"] == []
    assert body["count"] == 0
    assert body["is_real_trade"] is False


def test_api_paper_reset(api_client):
    from app.core.config import get_settings
    client, _ = api_client
    token = get_settings().admin_token
    r = client.post("/api/paper/reset", headers={"X-Admin-Token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["orders_submitted"] == 0


# ── 18. 정적 회귀 ───────────────────────────────────────────────

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


_PAPER_MODULES = (
    "paper_market_broker.py",
    "paper_trader.py",
)


def test_paper_modules_no_network_imports():
    pat = re.compile(
        r"^\s*(?:import\s+(?:requests|httpx|ccxt|pyupbit|"
        r"binance|binance_connector|okx)|"
        r"from\s+(?:requests|httpx|ccxt|pyupbit|"
        r"binance|binance_connector|okx))",
        re.M,
    )
    for fname in _PAPER_MODULES:
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(
            encoding="utf-8", errors="ignore",
        )
        assert not pat.search(text), f"{fname} imports network library"


def test_paper_modules_no_forbidden_substrings():
    forbidden = (
        "ENABLE_LIVE_TRADING = True",
        "ENABLE_AI_EXECUTION = True",
        "ENABLE_CRYPTO_FUTURES_LIVE = True",
        "requests.post(",
        "httpx.post(",
    )
    for fname in _PAPER_MODULES:
        text = (_REPO_BACKEND_APP / "brokers" / fname).read_text(
            encoding="utf-8", errors="ignore",
        )
        for needle in forbidden:
            assert needle not in text, f"{fname} contains {needle!r}"


def test_strategies_do_not_import_paper_modules():
    pat = re.compile(
        r"(?:from|import)\s+app\.brokers\.(?:paper_market_broker|paper_trader)",
    )
    hits = _scan(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy imports paper module: {hits}"


def test_agents_do_not_import_paper_modules():
    pat = re.compile(
        r"(?:from|import)\s+app\.brokers\.(?:paper_market_broker|paper_trader)",
    )
    whitelist = {"compliance.py"}
    hits = [p for p in _scan(_REPO_BACKEND_APP / "agents", pat)
            if p.name not in whitelist]
    assert not hits, f"agent imports paper module: {hits}"


def test_strategies_no_paper_instantiation():
    pat = re.compile(r"\bPaperMarketBroker\s*\(|\bPaperTrader\s*\(")
    hits = _scan(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy instantiates paper class: {hits}"


def test_agents_no_paper_instantiation():
    pat = re.compile(r"\bPaperMarketBroker\s*\(|\bPaperTrader\s*\(")
    hits = _scan(_REPO_BACKEND_APP / "agents", pat)
    assert not hits, f"agent instantiates paper class: {hits}"


def test_brokers_exports_paper_classes():
    from app import brokers
    for name in ("PaperMarketBroker", "PaperMarketBrokerConfig",
                 "PaperTrader", "PaperTraderError",
                 "PaperStatus", "PaperOrderLogEntry",
                 "AVAILABLE_PAPER_SOURCES", "make_paper_universe"):
        assert name in brokers.__all__, f"{name} not exported"
        assert hasattr(brokers, name)


# ── 19. real adapter 호환 — MockExchangeAdapter 가 source 로 동작 ─

def test_paper_broker_works_with_mock_exchange_adapter():
    """MockExchangeAdapter 는 fetch_ticker 가 있으므로 PaperMarketSource 호환."""
    src = MockExchangeAdapter("paper_mock_src")
    b = _make_broker(source=src)
    r = b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 100})
    assert r["status"] == "FILLED"
    assert r["mode"] == "PAPER"
