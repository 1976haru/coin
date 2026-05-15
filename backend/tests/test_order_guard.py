"""체크리스트 #51 Order Guard — 회귀 테스트.

검증:
  1. 정상 주문 통과
  2. 필수 필드 누락
  3. notional 양수/한도 검증
  4. leverage 검증
  5. action 화이트리스트
  6. symbol 형식 + blacklist
  7. source 화이트리스트
  8. OrderGateway 통합 — REJECTED route='guard'
  9. RiskManager 통과 후 OrderGuard 평가 순서 검증
"""
from __future__ import annotations
from datetime import datetime, timezone

import pytest

from app.execution.order_guard import OrderGuard, OrderGuardResult


# ── 1. 정상 주문 ────────────────────────────────────────────────

def test_clean_order_passes():
    g = OrderGuard()
    r = g.check({
        "symbol": "BTC/USDT", "side": "BUY",
        "notional_usdt": 50, "leverage": 1,
    })
    assert r.passed is True
    assert r.reasons == ()
    assert bool(r) is True


def test_default_leverage_treated_as_one():
    g = OrderGuard()
    r = g.check({"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 50})
    assert r.passed is True


# ── 2. 필수 필드 ────────────────────────────────────────────────

@pytest.mark.parametrize("missing_field", ["symbol", "side", "notional_usdt"])
def test_missing_required_field_blocks(missing_field):
    g = OrderGuard()
    order = {"symbol": "BTC", "side": "BUY", "notional_usdt": 50}
    del order[missing_field]
    r = g.check(order)
    assert r.passed is False
    assert any(missing_field in reason for reason in r.reasons)


def test_empty_string_field_treated_as_missing():
    g = OrderGuard()
    r = g.check({"symbol": "", "side": "BUY", "notional_usdt": 50})
    assert r.passed is False


def test_none_value_treated_as_missing():
    g = OrderGuard()
    r = g.check({"symbol": "BTC", "side": None, "notional_usdt": 50})
    assert r.passed is False


# ── 3. notional ─────────────────────────────────────────────────

def test_zero_notional_blocks():
    g = OrderGuard()
    r = g.check({"symbol": "BTC", "side": "BUY", "notional_usdt": 0})
    assert r.passed is False
    assert any("양수" in reason for reason in r.reasons)


def test_negative_notional_blocks():
    g = OrderGuard()
    r = g.check({"symbol": "BTC", "side": "BUY", "notional_usdt": -50})
    assert r.passed is False


def test_notional_above_absolute_cap_blocks():
    g = OrderGuard(absolute_max_notional_usdt=1000.0)
    r = g.check({"symbol": "BTC", "side": "BUY", "notional_usdt": 5000})
    assert r.passed is False
    assert any("절대 한도" in reason for reason in r.reasons)


def test_notional_at_cap_passes():
    g = OrderGuard(absolute_max_notional_usdt=1000.0)
    r = g.check({"symbol": "BTC", "side": "BUY", "notional_usdt": 1000.0})
    assert r.passed is True


def test_invalid_notional_type():
    g = OrderGuard()
    r = g.check({"symbol": "BTC", "side": "BUY", "notional_usdt": "fifty"})
    assert r.passed is False
    assert any("타입" in reason for reason in r.reasons)


# ── 4. leverage ─────────────────────────────────────────────────

def test_zero_leverage_blocks():
    g = OrderGuard()
    r = g.check({"symbol": "BTC", "side": "BUY", "notional_usdt": 50,
                  "leverage": 0})
    assert r.passed is False


def test_negative_leverage_blocks():
    g = OrderGuard()
    r = g.check({"symbol": "BTC", "side": "BUY", "notional_usdt": 50,
                  "leverage": -1})
    assert r.passed is False


def test_invalid_leverage_type():
    g = OrderGuard()
    r = g.check({"symbol": "BTC", "side": "BUY", "notional_usdt": 50,
                  "leverage": "high"})
    assert r.passed is False


# ── 5. action ───────────────────────────────────────────────────

@pytest.mark.parametrize("action", [
    "BUY", "SELL", "HOLD", "BLOCKED", "CLOSE",
    "OPEN_REVERSE_KIMP",
    "OPEN_LONG_A_SHORT_B", "OPEN_SHORT_A_LONG_B",
])
def test_default_actions_pass(action):
    g = OrderGuard()
    r = g.check({"symbol": "BTC", "side": action, "notional_usdt": 50})
    assert r.passed is True


def test_unknown_action_blocks():
    g = OrderGuard()
    r = g.check({"symbol": "BTC", "side": "WEIRD_ACTION", "notional_usdt": 50})
    assert r.passed is False
    assert any("action" in reason.lower() for reason in r.reasons)


def test_custom_allowed_actions():
    g = OrderGuard(allowed_actions={"BUY", "SELL"})
    assert g.check({"symbol": "BTC", "side": "BUY",
                     "notional_usdt": 50}).passed is True
    assert g.check({"symbol": "BTC", "side": "OPEN_REVERSE_KIMP",
                     "notional_usdt": 50}).passed is False


# ── 6. symbol ───────────────────────────────────────────────────

@pytest.mark.parametrize("symbol", [
    "BTC", "btc", "BTC/USDT", "BTC-USDT", "BTC_USDT", "ETH/BTC", "BTC123",
])
def test_valid_symbols_pass(symbol):
    g = OrderGuard()
    r = g.check({"symbol": symbol, "side": "BUY", "notional_usdt": 50})
    assert r.passed is True, f"valid symbol {symbol!r} blocked: {r.reasons}"


@pytest.mark.parametrize("symbol", [
    "BTC USDT",          # 공백
    "BTC@USDT",          # @
    "$BTC",              # $
    "A" * 33,            # 길이 33
    "🚀",                # 이모지
])
def test_invalid_symbol_format_blocks(symbol):
    g = OrderGuard()
    r = g.check({"symbol": symbol, "side": "BUY", "notional_usdt": 50})
    assert r.passed is False, f"invalid symbol {symbol!r} should block"


def test_symbol_blacklist():
    g = OrderGuard(symbol_blacklist={"DOGE", "PEPE"})
    r = g.check({"symbol": "DOGE", "side": "BUY", "notional_usdt": 50})
    assert r.passed is False
    assert any("blacklist" in reason for reason in r.reasons)


def test_symbol_blacklist_case_insensitive():
    g = OrderGuard(symbol_blacklist={"DOGE"})
    r = g.check({"symbol": "doge", "side": "BUY", "notional_usdt": 50})
    assert r.passed is False


def test_symbol_not_in_blacklist_passes():
    g = OrderGuard(symbol_blacklist={"DOGE"})
    r = g.check({"symbol": "BTC", "side": "BUY", "notional_usdt": 50})
    assert r.passed is True


# ── 7. source ───────────────────────────────────────────────────

@pytest.mark.parametrize("source", ["system", "strategy", "ai", "manual", "test"])
def test_default_sources_pass(source):
    g = OrderGuard()
    r = g.check({"symbol": "BTC", "side": "BUY", "notional_usdt": 50},
                  source=source)
    assert r.passed is True


def test_unknown_source_blocks():
    g = OrderGuard()
    r = g.check({"symbol": "BTC", "side": "BUY", "notional_usdt": 50},
                  source="bogus")
    assert r.passed is False


def test_custom_allowed_sources():
    g = OrderGuard(allowed_sources={"manual"})
    assert g.check({"symbol": "BTC", "side": "BUY", "notional_usdt": 50},
                    source="manual").passed is True
    assert g.check({"symbol": "BTC", "side": "BUY", "notional_usdt": 50},
                    source="ai").passed is False


# ── 8. OrderGuard 인스턴스 검증 ─────────────────────────────────

def test_invalid_absolute_cap_raises():
    with pytest.raises(ValueError):
        OrderGuard(absolute_max_notional_usdt=0)
    with pytest.raises(ValueError):
        OrderGuard(absolute_max_notional_usdt=-100)


# ── 9. 복합 위반 — 여러 reasons ─────────────────────────────────

def test_multiple_violations_collected():
    g = OrderGuard(absolute_max_notional_usdt=100)
    r = g.check({"symbol": "BTC@USDT", "side": "WEIRD",
                  "notional_usdt": 5000, "leverage": -1})
    assert r.passed is False
    assert len(r.reasons) >= 4   # symbol + action + notional + leverage


# ── 10. OrderGateway 통합 ───────────────────────────────────────

def test_order_gateway_uses_order_guard():
    """OrderGateway 가 OrderGuard 를 호출하고 REJECTED route='guard' 반환."""
    from app.core.config import Settings
    from app.core.modes import TradingMode
    from app.execution.order_gateway import OrderGateway
    from app.market.freshness import check_timestamp_freshness

    settings = Settings(trading_mode=TradingMode.PAPER)
    gw = OrderGateway(settings)
    fresh = check_timestamp_freshness(datetime.now(timezone.utc), 5, label="quote")

    # 한도 초과 — RiskManager 의 max_order_notional 보다 작지만 OrderGuard 절대 cap 초과
    # max_order_notional_usdt 기본 100, OrderGuard 절대 cap = 5×100 = 500
    # 그러나 RiskManager 가 먼저 거부 — 이 경로로는 guard 도달 불가
    # OrderGuard 가 발동하려면 RiskManager 통과 + guard 위반.
    # symbol 형식 위반 — RiskManager 는 symbol 형식 체크 안 함 → guard 가 잡음
    res = gw.submit(
        {"symbol": "BTC@USDT", "side": "BUY", "notional_usdt": 50, "leverage": 1},
        {"open_positions": 0},
        [fresh],
    )
    assert res["status"] == "REJECTED"
    assert res["route"] == "guard"
    assert any("symbol" in r.lower() for r in res["reasons"])


def test_order_gateway_clean_order_passes_guard():
    from app.core.config import Settings
    from app.core.modes import TradingMode
    from app.execution.order_gateway import OrderGateway
    from app.market.freshness import check_timestamp_freshness

    settings = Settings(trading_mode=TradingMode.PAPER)
    gw = OrderGateway(settings)
    fresh = check_timestamp_freshness(datetime.now(timezone.utc), 5, label="quote")
    res = gw.submit(
        {"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 50, "leverage": 1},
        {"open_positions": 0},
        [fresh],
    )
    assert res["status"] == "ACCEPTED"
    assert res["route"] == "paper"


def test_order_gateway_guard_runs_after_risk_manager():
    """Risk 위반 + Guard 위반 동시 → Risk 가 먼저 트리거 (평가 순서)."""
    from app.core.config import Settings
    from app.core.modes import TradingMode
    from app.execution.order_gateway import OrderGateway
    from app.market.freshness import check_timestamp_freshness

    settings = Settings(trading_mode=TradingMode.PAPER,
                         max_order_notional_usdt=100)
    gw = OrderGateway(settings)
    fresh = check_timestamp_freshness(datetime.now(timezone.utc), 5, label="quote")
    res = gw.submit(
        # notional 200 → Risk 거부. symbol 잘못됨도 있으나 Risk 가 먼저.
        {"symbol": "BTC@USDT", "side": "BUY", "notional_usdt": 200, "leverage": 1},
        {"open_positions": 0},
        [fresh],
    )
    assert res["status"] == "REJECTED"
    assert res["route"] == "risk"   # Risk 가 먼저 트리거


def test_order_gateway_audits_guard_rejection(tmp_path):
    """Guard 거절 시 ORDER_REJECTED_BY_GUARD 이벤트 생성."""
    from app.core.config import Settings
    from app.core.modes import TradingMode
    from app.execution.order_gateway import OrderGateway
    from app.market.freshness import check_timestamp_freshness
    from app.audit.audit_log import AuditLog

    audit = AuditLog(csv_path=str(tmp_path / "audit.csv"))
    settings = Settings(trading_mode=TradingMode.PAPER)
    gw = OrderGateway(settings, audit=audit)
    fresh = check_timestamp_freshness(datetime.now(timezone.utc), 5, label="quote")
    gw.submit(
        {"symbol": "BTC@USDT", "side": "BUY", "notional_usdt": 50, "leverage": 1},
        {"open_positions": 0},
        [fresh],
    )
    types = [e["event_type"] for e in audit.events]
    assert "ORDER_REJECTED_BY_GUARD" in types
