"""
RiskManager + AgentOrchestrator + PaperBroker 단위 테스트
GPT의 명확한 테스트 패턴 적용
"""
import pytest
from app.risk.manager import RiskManager
from app.agents.orchestrator import AgentOrchestrator, AgentDecision
from app.brokers.paper_broker import PaperBroker


# ── RiskManager ───────────────────────────────────────────────────

def make_rm(**kwargs) -> RiskManager:
    defaults = dict(
        max_order_notional_usdt=100,
        max_open_positions=5,
        daily_loss_limit_pct=2.0,
        max_leverage=2.0,
        max_consecutive_losses=3,
        re_entry_cooldown_min=0,    # 테스트 쿨다운 없음
    )
    defaults.update(kwargs)
    return RiskManager(**defaults)


def test_risk_approves_normal_order():
    rm = make_rm()
    order = {"side": "BUY", "symbol": "BTC", "notional_usdt": 50, "leverage": 1}
    account = {"open_positions": 0, "daily_pnl_pct": 0.0, "emergency_stop": False}
    d = rm.evaluate(order, account)
    assert d.approved is True
    assert d.reasons == []


def test_risk_blocks_oversized_order():
    rm = make_rm(max_order_notional_usdt=100)
    order = {"side": "BUY", "symbol": "BTC", "notional_usdt": 200, "leverage": 1}
    d = rm.evaluate(order, {"open_positions": 0})
    assert d.approved is False
    assert any("초과" in r for r in d.reasons)


def test_risk_blocks_too_many_positions():
    rm = make_rm(max_open_positions=3)
    d = rm.evaluate(
        {"side": "BUY", "notional_usdt": 50, "leverage": 1},
        {"open_positions": 3},
    )
    assert d.approved is False


def test_risk_blocks_daily_loss():
    rm = make_rm(daily_loss_limit_pct=2.0)
    rm._daily_pnl_pct = -0.025   # -2.5%
    d = rm.evaluate({"side": "BUY", "notional_usdt": 10, "leverage": 1}, {"open_positions": 0})
    assert d.approved is False


def test_risk_blocks_consecutive_losses():
    rm = make_rm(max_consecutive_losses=3)
    rm._consecutive_losses = 3
    d = rm.evaluate({"side": "BUY", "notional_usdt": 10, "leverage": 1}, {"open_positions": 0})
    assert d.approved is False


def test_risk_win_resets_consecutive():
    rm = make_rm()
    rm._consecutive_losses = 2
    rm.record_trade("BTC", 1.0)   # 수익
    assert rm._consecutive_losses == 0


def test_risk_loss_increments_consecutive():
    rm = make_rm()
    before = rm._consecutive_losses
    rm.record_trade("ETH", -1.0)
    assert rm._consecutive_losses == before + 1


def test_kill_switch_blocks_all():
    rm = make_rm()
    rm.activate_kill_switch("테스트")
    d = rm.evaluate({"side": "BUY", "notional_usdt": 10, "leverage": 1}, {"open_positions": 0})
    assert d.approved is False
    assert "Kill Switch" in d.reasons[0]


def test_kill_switch_deactivation():
    rm = make_rm()
    rm.activate_kill_switch()
    rm.deactivate_kill_switch()
    d = rm.evaluate({"side": "BUY", "notional_usdt": 10, "leverage": 1}, {"open_positions": 0})
    assert d.approved is True


def test_freshness_blocks_buy():
    rm = make_rm()
    d = rm.evaluate(
        {"side": "BUY", "notional_usdt": 10, "leverage": 1},
        {"open_positions": 0},
        freshness_block_reasons=["upbit: stale 8.3s"],
    )
    assert d.approved is False


def test_freshness_does_not_block_sell():
    """SELL은 freshness 체크 안 함 (청산은 허용)"""
    rm = make_rm()
    d = rm.evaluate(
        {"side": "SELL", "notional_usdt": 10, "leverage": 1},
        {"open_positions": 1},
        freshness_block_reasons=[],   # OrderGateway에서 SELL엔 freshness 안 줌
    )
    assert d.approved is True


# ── AgentOrchestrator ─────────────────────────────────────────────

def test_agent_hold_on_anomaly():
    a = AgentOrchestrator()
    d = a.decide({"action": "BUY", "confidence": 0.9}, context={"anomaly": True})
    assert d.action == "HOLD"
    assert d.risk_veto is True


def test_agent_hold_on_blocked_signal():
    a = AgentOrchestrator()
    d = a.decide({"action": "BLOCKED", "confidence": 0.9, "reason": "입출금 중단"})
    assert d.action == "HOLD"
    assert d.risk_veto is True


def test_agent_hold_on_low_quality():
    a = AgentOrchestrator()
    a.MIN_QUALITY_SCORE = 70
    d = a.decide({"action": "BUY", "confidence": 0.0, "reason": "약한 신호"})
    assert d.action == "HOLD"


def test_agent_approves_high_quality():
    a = AgentOrchestrator()
    d = a.decide({"action": "BUY", "confidence": 0.85, "reason": "강한 추세"},
                 context={"volume_surge": 1.5, "regime": "TREND_UP"})
    assert d.action == "BUY"
    assert d.quality_score >= 70


# ── PaperBroker ───────────────────────────────────────────────────

def test_paper_broker_returns_filled():
    broker = PaperBroker(fill_chance=1.0)   # 항상 체결
    result = broker.place_order({"symbol": "BTC/USDT", "side": "BUY",
                                  "notional_usdt": 50, "price": 100000})
    assert result["status"] == "FILLED"
    assert result["fee_usdt"] > 0
    assert result["slippage_pct"] >= 0


def test_paper_broker_timeout_simulation():
    broker = PaperBroker(fill_chance=0.0)   # 항상 미체결
    result = broker.place_order({"symbol": "ETH/USDT", "side": "BUY",
                                  "notional_usdt": 30, "price": 3000})
    assert result["status"] == "TIMEOUT"
    assert result["fee_usdt"] == 0.0
