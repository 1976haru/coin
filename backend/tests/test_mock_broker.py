"""체크리스트 #24 MockBroker — 회귀 테스트.

검증:
  1. 초기 잔고 / 잔고 조회
  2. MARKET BUY / SELL 체결 + 잔고 + 포지션 + PnL
  3. LIMIT BUY / SELL — crossable 즉시 체결 / non-crossable open + cancel
  4. cancel_order — open 만 취소, locked balance 해제
  5. 수수료(fee_bps) 반영
  6. slippage(slippage_bps) 반영
  7. unrealized PnL 계산
  8. realized PnL 계산 (롱 청산)
  9. max_order_notional 한도
 10. allow_short=False 보유 초과 SELL 거부
 11. allow_margin=False quote 잔고 초과 BUY 거부
 12. duplicate client_order_id idempotent
 13. LIVE mode 거부 (`mode` / `trading_mode`)
 14. invalid side / order_type / symbol 거부
 15. notional_usdt + size 둘 다 0 → 거부
 16. 모든 결과에 mode=MOCK / is_real_trade=False / execution_source=mock_broker / warning
 17. audit 에 secret 키 미노출
 18. 외부 네트워크 import / 호출 부재 (정적 회귀)
 19. Strategy/Agent 가 MockBroker 미참조 (정적 회귀)
 20. config 검증 (LIVE mode 생성 거부)
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest

from app.brokers import (
    MockBroker, MockBrokerConfig,
    MockAccountState, MockPositionBook,
)


# ── 픽스처 ────────────────────────────────────────────────────────

@pytest.fixture
def broker() -> MockBroker:
    cfg = MockBrokerConfig(
        base_currency="USDT",
        fee_bps=5.0,           # 0.05%
        slippage_bps=0.0,
        allow_short=False,
        allow_margin=False,
        max_order_notional=0.0,
        mode="MOCK",
        initial_balances={"USDT": 10_000.0},
    )
    b = MockBroker(cfg)
    b.set_market_price("BTC-USDT", 50_000.0)
    return b


# ── 1. 초기 잔고 ──────────────────────────────────────────────────

def test_initial_balance(broker):
    bal = broker.get_balance("USDT")
    assert bal["free"] == 10_000.0
    assert bal["locked"] == 0.0
    assert bal["total"] == 10_000.0
    assert bal["mode"] == "MOCK"
    assert bal["is_real_trade"] is False
    assert bal["execution_source"] == "mock_broker"
    assert "Mock execution" in bal["warning"]


def test_get_balance_all(broker):
    out = broker.get_balance()
    assert "USDT" in out["balances"]
    assert out["is_real_trade"] is False


# ── 2. MARKET BUY ─────────────────────────────────────────────────

def test_market_buy_filled_and_balance_updated(broker):
    r = broker.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usdt": 100.0,
    })
    assert r["status"] == "FILLED"
    assert r["mode"] == "MOCK"
    assert r["is_real_trade"] is False
    assert r["execution_source"] == "mock_broker"
    assert r["filled_price"] == 50_000.0
    # 0.002 BTC + 0.05 USDT fee = 100.05 차감
    assert abs(r["fee_usdt"] - 0.05) < 1e-9
    assert broker.get_balance("USDT")["free"] == pytest.approx(10_000 - 100.05)
    assert broker.get_balance("BTC")["free"] == pytest.approx(0.002)


def test_market_buy_updates_position(broker):
    broker.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usdt": 100.0,
    })
    p = broker.get_position("BTC-USDT")
    assert p["qty"] == pytest.approx(0.002)
    assert p["avg_entry_price"] == 50_000.0
    assert p["realized_pnl"] == 0.0


def test_market_buy_avg_entry_price_weighted(broker):
    """두 번 BUY → 가중평균 entry price."""
    broker.place_order({"symbol": "BTC-USDT", "side": "BUY",
                        "order_type": "MARKET", "notional_usdt": 100})
    broker.set_market_price("BTC-USDT", 60_000.0)
    broker.place_order({"symbol": "BTC-USDT", "side": "BUY",
                        "order_type": "MARKET", "notional_usdt": 60})
    p = broker.get_position("BTC-USDT")
    # 0.002 @ 50k + 0.001 @ 60k → avg = (100 + 60) / 0.003 = 53333.33
    assert p["qty"] == pytest.approx(0.003)
    assert p["avg_entry_price"] == pytest.approx(53_333.333_333, rel=1e-3)


# ── 3. MARKET SELL ────────────────────────────────────────────────

def test_market_sell_full_close_realizes_pnl(broker):
    # 매수 후 가격 상승 → 전량 SELL → realized PnL +
    broker.place_order({"symbol": "BTC-USDT", "side": "BUY",
                        "order_type": "MARKET", "notional_usdt": 100})
    broker.set_market_price("BTC-USDT", 60_000.0)
    r = broker.place_order({"symbol": "BTC-USDT", "side": "SELL",
                            "order_type": "MARKET", "qty": 0.002})
    assert r["status"] == "FILLED"
    p = broker.get_position("BTC-USDT")
    assert p["qty"] == 0.0
    assert p["avg_entry_price"] == 0.0
    # (60000 - 50000) * 0.002 = +20
    assert p["realized_pnl"] == pytest.approx(20.0)


def test_market_sell_partial_keeps_avg_entry_price(broker):
    broker.place_order({"symbol": "BTC-USDT", "side": "BUY",
                        "order_type": "MARKET", "notional_usdt": 100})
    broker.set_market_price("BTC-USDT", 55_000.0)
    broker.place_order({"symbol": "BTC-USDT", "side": "SELL",
                        "order_type": "MARKET", "qty": 0.001})
    p = broker.get_position("BTC-USDT")
    assert p["qty"] == pytest.approx(0.001)
    assert p["avg_entry_price"] == 50_000.0  # 부분청산은 avg 유지
    # realized = (55000 - 50000) * 0.001 = +5
    assert p["realized_pnl"] == pytest.approx(5.0)


# ── 4. LIMIT 주문 ─────────────────────────────────────────────────

def test_limit_buy_immediate_fill_when_market_below_limit(broker):
    """시장가 50000, BUY LIMIT 51000 → 즉시 체결 (market <= limit)."""
    r = broker.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "LIMIT", "notional_usdt": 100, "price": 51_000,
    })
    assert r["status"] == "FILLED"


def test_limit_buy_below_market_stays_open(broker):
    """시장가 50000, BUY LIMIT 49000 → open (체결 안 됨)."""
    r = broker.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "LIMIT", "notional_usdt": 100, "price": 49_000,
        "client_order_id": "buy-limit-1",
    })
    assert r["status"] == "ACCEPTED"
    # 잔고는 locked 로 이동
    bal = broker.get_balance("USDT")
    assert bal["free"] < 10_000.0
    assert bal["locked"] > 0


def test_limit_sell_above_market_stays_open(broker):
    """BTC 매수 후 매도 LIMIT 60000 (현재가 50000) → open."""
    broker.place_order({"symbol": "BTC-USDT", "side": "BUY",
                        "order_type": "MARKET", "notional_usdt": 100})
    r = broker.place_order({
        "symbol": "BTC-USDT", "side": "SELL",
        "order_type": "LIMIT", "qty": 0.001, "price": 60_000,
    })
    assert r["status"] == "ACCEPTED"
    # BTC 0.001 locked
    bal_btc = broker.get_balance("BTC")
    assert bal_btc["locked"] == pytest.approx(0.001)


def test_limit_sell_below_market_immediate_fill(broker):
    """BTC 매수 후 매도 LIMIT 45000 (현재가 50000) → 즉시 체결."""
    broker.place_order({"symbol": "BTC-USDT", "side": "BUY",
                        "order_type": "MARKET", "notional_usdt": 100})
    r = broker.place_order({
        "symbol": "BTC-USDT", "side": "SELL",
        "order_type": "LIMIT", "qty": 0.001, "price": 45_000,
    })
    assert r["status"] == "FILLED"


def test_limit_order_without_price_rejected(broker):
    r = broker.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "LIMIT", "notional_usdt": 100,
    })
    assert r["status"] == "REJECTED"
    assert "price" in r["reason"].lower()


# ── 5. cancel_order ──────────────────────────────────────────────

def test_cancel_open_limit_releases_locked_balance(broker):
    r = broker.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "LIMIT", "notional_usdt": 100, "price": 49_000,
    })
    oid = r["order_id"]
    locked_before = broker.get_balance("USDT")["locked"]
    assert locked_before > 0
    c = broker.cancel_order(oid)
    assert c["status"] == "ACCEPTED"
    # 잔고 복원
    assert broker.get_balance("USDT")["locked"] == 0.0
    assert broker.get_balance("USDT")["free"] == 10_000.0


def test_cancel_unknown_order_rejected(broker):
    r = broker.cancel_order("nonexistent")
    assert r["status"] == "REJECTED"
    assert "unknown" in r["reason"].lower()


def test_cancel_by_client_order_id(broker):
    broker.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "LIMIT", "notional_usdt": 100, "price": 49_000,
        "client_order_id": "buy-cancel-cli",
    })
    c = broker.cancel_order("buy-cancel-cli")
    assert c["status"] == "ACCEPTED"


def test_cannot_cancel_filled_market_order(broker):
    r = broker.place_order({"symbol": "BTC-USDT", "side": "BUY",
                            "order_type": "MARKET", "notional_usdt": 100})
    assert r["status"] == "FILLED"
    c = broker.cancel_order(r["order_id"])
    # 이미 FILLED 라 open 에 없음 → REJECTED
    assert c["status"] == "REJECTED"


# ── 6. 수수료 / 슬리피지 ─────────────────────────────────────────

def test_fee_applied_at_5bps():
    cfg = MockBrokerConfig(fee_bps=5.0, slippage_bps=0.0,
                           initial_balances={"USDT": 1000})
    b = MockBroker(cfg)
    b.set_market_price("BTC-USDT", 50_000)
    r = b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 100})
    # 0.05% of 100 = 0.05
    assert r["fee_usdt"] == pytest.approx(0.05)


def test_slippage_applied_on_buy():
    cfg = MockBrokerConfig(fee_bps=0.0, slippage_bps=10.0,  # 0.1%
                           initial_balances={"USDT": 1000})
    b = MockBroker(cfg)
    b.set_market_price("BTC-USDT", 50_000)
    r = b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 100})
    # BUY 는 위로 — fill_price > 50000
    assert r["filled_price"] > 50_000
    assert r["slippage_pct"] > 0


def test_slippage_applied_on_sell():
    cfg = MockBrokerConfig(fee_bps=0.0, slippage_bps=10.0,
                           initial_balances={"BTC": 0.01})
    b = MockBroker(cfg)
    b.set_market_price("BTC-USDT", 50_000)
    r = b.place_order({"symbol": "BTC-USDT", "side": "SELL",
                       "order_type": "MARKET", "qty": 0.001})
    # SELL 는 아래로
    assert r["filled_price"] < 50_000


# ── 7. unrealized PnL ────────────────────────────────────────────

def test_unrealized_pnl_long(broker):
    broker.place_order({"symbol": "BTC-USDT", "side": "BUY",
                        "order_type": "MARKET", "notional_usdt": 100})
    broker.set_market_price("BTC-USDT", 60_000)
    p = broker.get_position("BTC-USDT")
    # (60000 - 50000) * 0.002 = 20
    assert p["unrealized_pnl"] == pytest.approx(20.0)


def test_unrealized_pnl_zero_when_flat(broker):
    p = broker.get_position("BTC-USDT")
    assert p["qty"] == 0
    assert p["unrealized_pnl"] == 0.0


# ── 8. max_order_notional ────────────────────────────────────────

def test_max_order_notional_rejects():
    cfg = MockBrokerConfig(max_order_notional=50,
                           initial_balances={"USDT": 10_000})
    b = MockBroker(cfg)
    b.set_market_price("BTC-USDT", 50_000)
    r = b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 100})
    assert r["status"] == "REJECTED"
    assert "max_order_notional" in r["reason"]


# ── 9. allow_short / allow_margin ────────────────────────────────

def test_allow_short_false_blocks_oversell():
    cfg = MockBrokerConfig(allow_short=False,
                           initial_balances={"USDT": 10_000})
    b = MockBroker(cfg)
    b.set_market_price("BTC-USDT", 50_000)
    r = b.place_order({"symbol": "BTC-USDT", "side": "SELL",
                       "order_type": "MARKET", "qty": 0.5})
    assert r["status"] == "REJECTED"
    assert "insufficient_base_balance" in r["reason"]


def test_allow_short_true_permits_short():
    cfg = MockBrokerConfig(allow_short=True,
                           initial_balances={"USDT": 10_000})
    b = MockBroker(cfg)
    b.set_market_price("BTC-USDT", 50_000)
    r = b.place_order({"symbol": "BTC-USDT", "side": "SELL",
                       "order_type": "MARKET", "qty": 0.01})
    assert r["status"] == "FILLED"
    p = b.get_position("BTC-USDT")
    assert p["qty"] < 0  # 숏 포지션


def test_allow_margin_false_blocks_overbuy():
    cfg = MockBrokerConfig(allow_margin=False,
                           initial_balances={"USDT": 50})
    b = MockBroker(cfg)
    b.set_market_price("BTC-USDT", 50_000)
    r = b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 100})
    assert r["status"] == "REJECTED"
    assert "insufficient_balance" in r["reason"]


# ── 10. duplicate client_order_id ────────────────────────────────

def test_duplicate_client_order_id_returns_first_result(broker):
    req = {
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usdt": 100,
        "client_order_id": "dup-1",
    }
    r1 = broker.place_order(req)
    bal_after = broker.get_balance("USDT")["free"]
    r2 = broker.place_order(req)
    assert r1["order_id"] == r2["order_id"]
    assert r1["status"] == r2["status"] == "FILLED"
    # 중복 호출이 잔고를 두 번 차감하지 않는다
    assert broker.get_balance("USDT")["free"] == bal_after


def test_duplicate_client_order_id_via_idempotency_key(broker):
    req = {
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usdt": 100,
        "idempotency_key": "idem-dup-1",
    }
    r1 = broker.place_order(req)
    r2 = broker.place_order(req)
    assert r1["order_id"] == r2["order_id"]


# ── 11. LIVE mode 거부 ───────────────────────────────────────────

def test_live_mode_in_request_rejected(broker):
    r = broker.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usdt": 100,
        "mode": "LIVE",
    })
    assert r["status"] == "REJECTED"
    assert "LIVE" in r["reason"]
    assert r["route"] == "live_not_wired"


def test_trading_mode_live_in_request_rejected(broker):
    r = broker.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usdt": 100,
        "trading_mode": "LIVE",
    })
    assert r["status"] == "REJECTED"


def test_config_rejects_live_mode():
    with pytest.raises(ValueError):
        MockBrokerConfig(mode="LIVE")


# ── 12. invalid input ────────────────────────────────────────────

def test_invalid_side_rejected(broker):
    r = broker.place_order({"symbol": "BTC-USDT", "side": "HOLD",
                            "order_type": "MARKET", "notional_usdt": 100})
    assert r["status"] == "REJECTED"
    assert "side" in r["reason"].lower()


def test_invalid_order_type_rejected(broker):
    r = broker.place_order({"symbol": "BTC-USDT", "side": "BUY",
                            "order_type": "STOP", "notional_usdt": 100})
    assert r["status"] == "REJECTED"


def test_zero_notional_rejected(broker):
    r = broker.place_order({"symbol": "BTC-USDT", "side": "BUY",
                            "order_type": "MARKET", "notional_usdt": 0})
    assert r["status"] == "REJECTED"


def test_invalid_symbol_rejected(broker):
    r = broker.place_order({"symbol": "GIBBERISH123", "side": "BUY",
                            "order_type": "MARKET", "notional_usdt": 100})
    # GIBBERISH 는 알려진 quote 후미 없음 → split 실패 → reject
    # (단, GIBBERISH123 같은 임의 토큰은 native 분리가 실패하면 거부)
    assert r["status"] == "REJECTED"


# ── 13. 응답에 MOCK 표시 ─────────────────────────────────────────

def test_all_result_fields_present(broker):
    r = broker.place_order({"symbol": "BTC-USDT", "side": "BUY",
                            "order_type": "MARKET", "notional_usdt": 100})
    for k in ("mode", "is_real_trade", "execution_source", "warning",
              "status", "filled_price", "fee_usdt", "qty"):
        assert k in r
    assert r["mode"] == "MOCK"
    assert r["is_real_trade"] is False
    assert r["execution_source"] == "mock_broker"
    assert "Not real profit" in r["warning"]


def test_reject_response_has_mock_flags(broker):
    r = broker.place_order({"symbol": "BTC-USDT", "side": "BUY",
                            "order_type": "MARKET", "notional_usdt": 0})
    assert r["status"] == "REJECTED"
    assert r["mode"] == "MOCK"
    assert r["is_real_trade"] is False
    assert "Not real profit" in r["warning"]


def test_paper_mode_config():
    cfg = MockBrokerConfig(mode="PAPER", initial_balances={"USDT": 1000})
    b = MockBroker(cfg)
    b.set_market_price("BTC-USDT", 50_000)
    r = b.place_order({"symbol": "BTC-USDT", "side": "BUY",
                       "order_type": "MARKET", "notional_usdt": 100})
    assert r["mode"] == "PAPER"
    assert r["is_real_trade"] is False


# ── 14. audit secret sanitize ────────────────────────────────────

def test_audit_strips_secret_keys(broker):
    r = broker.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usdt": 100,
        "api_key": "AAAA", "api_secret": "BBBB",
        "passphrase": "CCCC", "ok_access_sign": "DDDD",
        "x_mbx_apikey": "EEEE",
    })
    audit_str = repr(r.get("audit") or {}).lower()
    for bad in ("aaaa", "bbbb", "cccc", "dddd", "eeee",
                "api_key", "api_secret", "passphrase",
                "ok_access_sign", "x_mbx_apikey"):
        assert bad not in audit_str, f"secret leaked: {bad}"


# ── 15. reset ────────────────────────────────────────────────────

def test_reset_restores_initial_state(broker):
    broker.place_order({"symbol": "BTC-USDT", "side": "BUY",
                        "order_type": "MARKET", "notional_usdt": 100})
    assert broker.get_balance("BTC")["free"] > 0
    broker.reset()
    assert broker.get_balance("BTC")["free"] == 0
    assert broker.get_balance("USDT")["free"] == 10_000.0
    assert broker.get_position("BTC-USDT")["qty"] == 0


# ── 16. account summary ──────────────────────────────────────────

def test_account_summary_includes_filled_and_mock_flags(broker):
    broker.place_order({"symbol": "BTC-USDT", "side": "BUY",
                        "order_type": "MARKET", "notional_usdt": 100})
    summary = broker.get_account_summary()
    assert summary["filled_count"] == 1
    assert summary["rejected_count"] == 0
    assert summary["is_real_trade"] is False
    assert summary["execution_source"] == "mock_broker"
    assert "BTC-USDT" in summary["positions"]
    assert summary["config"]["mode"] == "MOCK"


# ── 17. MockAccountState 단위 ────────────────────────────────────

def test_account_state_lock_unlock():
    a = MockAccountState({"USDT": 1000})
    a.lock("USDT", 200)
    assert a.free("USDT") == 800
    assert a.locked("USDT") == 200
    a.unlock("USDT", 200)
    assert a.free("USDT") == 1000
    assert a.locked("USDT") == 0


def test_account_state_lock_insufficient():
    a = MockAccountState({"USDT": 50})
    with pytest.raises(ValueError):
        a.lock("USDT", 100)


# ── 18. MockPositionBook 단위 ────────────────────────────────────

def test_position_book_short_unrealized():
    pb = MockPositionBook()
    pb.on_sell("BTC-USDT", 0.01, 50_000)
    p = pb.get("BTC-USDT")
    assert p.qty == -0.01
    assert p.avg_entry_price == 50_000
    # 가격 하락 → 숏 +
    assert pb.unrealized_pnl("BTC-USDT", 45_000) == pytest.approx(50.0)


# ── 19. 외부 네트워크 / SDK 부재 정적 회귀 ──────────────────────

_REPO_BACKEND_APP = Path(__file__).resolve().parent.parent / "app"
_MOCK_FILE = _REPO_BACKEND_APP / "brokers" / "mock_simulation.py"


def test_mock_simulation_no_network_imports():
    pat = re.compile(
        r"^\s*(?:import\s+(?:requests|httpx|ccxt|pyupbit|"
        r"binance|binance_connector|okx)|"
        r"from\s+(?:requests|httpx|ccxt|pyupbit|"
        r"binance|binance_connector|okx))",
        re.M,
    )
    text = _MOCK_FILE.read_text(encoding="utf-8")
    assert not pat.search(text), "mock_simulation.py imports network library"


def test_mock_simulation_no_forbidden_substrings():
    forbidden = (
        "ENABLE_LIVE_TRADING = True",
        "ENABLE_AI_EXECUTION = True",
        "ENABLE_CRYPTO_FUTURES_LIVE = True",
        "requests.post(",
        "httpx.post(",
    )
    text = _MOCK_FILE.read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in text, f"mock_simulation.py contains {needle!r}"


# ── 20. Strategy/Agent 직접 호출 금지 ────────────────────────────


def _scan(directory, pattern, glob="**/*.py"):
    hits = []
    for p in directory.glob(glob):
        if "__pycache__" in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            hits.append(p)
    return hits


def test_strategies_do_not_import_mock_broker():
    pat = re.compile(
        r"(?:from|import)\s+app\.brokers\.mock_simulation",
    )
    hits = _scan(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy imports mock_simulation: {hits}"


def test_agents_do_not_import_mock_broker():
    pat = re.compile(
        r"(?:from|import)\s+app\.brokers\.mock_simulation",
    )
    whitelist = {"compliance.py"}
    hits = [p for p in _scan(_REPO_BACKEND_APP / "agents", pat)
            if p.name not in whitelist]
    assert not hits, f"agent imports mock_simulation: {hits}"


def test_strategies_no_mock_broker_instantiation():
    pat = re.compile(r"\bMockBroker\s*\(")
    hits = _scan(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy instantiates MockBroker: {hits}"


def test_agents_no_mock_broker_instantiation():
    pat = re.compile(r"\bMockBroker\s*\(")
    hits = _scan(_REPO_BACKEND_APP / "agents", pat)
    assert not hits, f"agent instantiates MockBroker: {hits}"


# ── 21. brokers __all__ exports ──────────────────────────────────


def test_brokers_module_exports_mock_simulation():
    from app import brokers
    for name in ("MockBroker", "MockBrokerConfig",
                 "MockAccountState", "MockPositionBook",
                 "MockMarket", "MockExecutionEngine"):
        assert name in brokers.__all__, f"{name} not exported"
        assert hasattr(brokers, name)


# ── 22. PaperBroker 호환 시그니처 — drop-in 가능 ────────────────

def test_place_order_signature_matches_paper_broker(broker):
    """OrderGateway 가 PaperBroker 와 동일한 호출 패턴(dict→dict) 으로 사용 가능."""
    r = broker.place_order({
        "symbol": "BTC-USDT", "side": "BUY",
        "order_type": "MARKET", "notional_usdt": 100,
        "leverage": 1, "confidence": 0.7, "reason": "test",
    })
    # PaperBroker 가 반환하는 필드도 모두 포함
    for k in ("order_id", "status", "symbol", "side",
              "notional_usdt", "filled_price", "fee_usdt", "slippage_pct"):
        assert k in r, f"missing field {k!r}"
