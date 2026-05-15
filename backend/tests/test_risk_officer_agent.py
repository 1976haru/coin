"""체크리스트 #38 Risk Officer Agent — 회귀 테스트.

검증:
  1. risk_context_from_manager 헬퍼 — RiskManager 상태 변환
  2. order/account 파라미터 옵션 처리
  3. RiskOfficerAgent 신규 가드 (emergency_stop / position 한도 / notional / leverage)
  4. 평가 순서 — kill_switch 가 먼저, WATCH_ONLY 가 마지막
  5. 기존 시나리오 (kill_switch / consecutive / daily / low_confidence) 보존
  6. RiskManager 통합 — 실제 RiskManager 인스턴스로 ctx 생성
  7. 모든 결정이 is_order_intent=False (CLAUDE.md §2.3)
"""
from __future__ import annotations
import pytest

from app.agents.risk_officer import RiskOfficerAgent
from app.agents.risk_context import risk_context_from_manager
from app.risk.manager import RiskManager


# ── 헬퍼 ─────────────────────────────────────────────────────────

def make_risk_manager(**kwargs):
    defaults = dict(
        max_order_notional_usdt=100.0,
        max_open_positions=5,
        daily_loss_limit_pct=2.0,
        max_leverage=2.0,
        max_consecutive_losses=5,
        re_entry_cooldown_min=0,
    )
    defaults.update(kwargs)
    return RiskManager(**defaults)


# ── 1. risk_context_from_manager — 기본 상태 ───────────────────

def test_risk_context_basic_fields():
    rm = make_risk_manager()
    ctx = risk_context_from_manager(rm)
    assert ctx["kill_switch"] is False
    assert ctx["consecutive_losses"] == 0
    assert ctx["daily_loss_pct"] == 0.0
    assert ctx["max_consecutive_losses"] == 5
    assert ctx["daily_loss_limit_pct"] == -2.0   # 음수로 변환
    assert ctx["max_order_notional_usdt"] == 100.0
    assert ctx["max_leverage"] == 2.0
    assert ctx["max_open_positions"] == 5


def test_risk_context_reflects_kill_switch():
    rm = make_risk_manager()
    rm.activate_kill_switch("test")
    ctx = risk_context_from_manager(rm)
    assert ctx["kill_switch"] is True


def test_risk_context_reflects_state_changes():
    rm = make_risk_manager()
    rm._consecutive_losses = 3
    rm._daily_pnl_pct = -0.015
    ctx = risk_context_from_manager(rm)
    assert ctx["consecutive_losses"] == 3
    assert ctx["daily_loss_pct"] == pytest.approx(-1.5, abs=1e-6)


# ── 2. order/account 옵션 ───────────────────────────────────────

def test_risk_context_includes_order_fields():
    rm = make_risk_manager()
    ctx = risk_context_from_manager(rm,
                                     order={"notional_usdt": 50, "leverage": 2})
    assert ctx["order_notional_usdt"] == 50.0
    assert ctx["order_leverage"] == 2.0


def test_risk_context_includes_account_fields():
    rm = make_risk_manager()
    ctx = risk_context_from_manager(rm,
                                     account={"open_positions": 3,
                                              "emergency_stop": True})
    assert ctx["open_positions"] == 3
    assert ctx["emergency_stop"] is True


def test_risk_context_without_order_or_account():
    rm = make_risk_manager()
    ctx = risk_context_from_manager(rm)
    assert "order_notional_usdt" not in ctx
    assert "open_positions" not in ctx


# ── 3. 신규 가드: emergency_stop ────────────────────────────────

def test_emergency_stop_blocks_order():
    a = RiskOfficerAgent()
    d = a.decide(
        {"action": "BUY", "confidence": 0.9},
        {"emergency_stop": True},
    )
    assert d.risk_veto is True
    assert "Emergency Stop" in d.reason


# ── 4. 신규 가드: position 한도 ─────────────────────────────────

def test_position_limit_blocks_when_full():
    a = RiskOfficerAgent()
    d = a.decide(
        {"action": "BUY", "confidence": 0.9},
        {"open_positions": 5, "max_open_positions": 5},
    )
    assert d.risk_veto is True
    assert "포지션" in d.reason


def test_position_limit_passes_when_not_full():
    a = RiskOfficerAgent()
    d = a.decide(
        {"action": "BUY", "confidence": 0.9},
        {"open_positions": 2, "max_open_positions": 5},
    )
    assert d.risk_veto is False


def test_position_limit_skipped_when_action_is_close():
    """SELL/CLOSE 는 포지션 슬롯을 늘리지 않으므로 한도 적용 안 함."""
    a = RiskOfficerAgent()
    d = a.decide(
        {"action": "CLOSE", "confidence": 0.9},
        {"open_positions": 5, "max_open_positions": 5},
    )
    # CLOSE 액션이라 position 한도 적용되지 않고 통과
    assert d.action == "CLOSE"
    assert d.risk_veto is False


# ── 5. 신규 가드: notional 한도 ─────────────────────────────────

def test_notional_over_limit_blocks():
    a = RiskOfficerAgent()
    d = a.decide(
        {"action": "BUY", "confidence": 0.9},
        {"order_notional_usdt": 200, "max_order_notional_usdt": 100},
    )
    assert d.risk_veto is True
    assert "주문 금액" in d.reason


def test_notional_at_limit_passes():
    a = RiskOfficerAgent()
    d = a.decide(
        {"action": "BUY", "confidence": 0.9},
        {"order_notional_usdt": 100, "max_order_notional_usdt": 100},
    )
    assert d.risk_veto is False


# ── 6. 신규 가드: leverage 한도 ─────────────────────────────────

def test_leverage_over_limit_blocks():
    a = RiskOfficerAgent()
    d = a.decide(
        {"action": "BUY", "confidence": 0.9},
        {"order_leverage": 5.0, "max_leverage": 2.0},
    )
    assert d.risk_veto is True
    assert "레버리지" in d.reason


def test_leverage_at_limit_passes():
    a = RiskOfficerAgent()
    d = a.decide(
        {"action": "BUY", "confidence": 0.9},
        {"order_leverage": 2.0, "max_leverage": 2.0},
    )
    assert d.risk_veto is False


# ── 7. 평가 순서: kill_switch 우선 ──────────────────────────────

def test_kill_switch_takes_priority_over_other_violations():
    """kill_switch + 다른 위반들이 동시 → kill_switch 가 먼저 트리거."""
    a = RiskOfficerAgent()
    d = a.decide(
        {"action": "BUY", "confidence": 0.9},
        {
            "kill_switch": True,
            "emergency_stop": True,
            "order_notional_usdt": 9999, "max_order_notional_usdt": 100,
            "consecutive_losses": 10,
        },
    )
    assert d.risk_veto is True
    assert "Kill Switch" in d.reason


def test_emergency_stop_before_consecutive_losses():
    """평가 순서: emergency_stop (2) > consecutive_losses (3)."""
    a = RiskOfficerAgent()
    d = a.decide(
        {"action": "BUY", "confidence": 0.9},
        {
            "emergency_stop": True,
            "consecutive_losses": 10, "max_consecutive_losses": 5,
        },
    )
    assert "Emergency Stop" in d.reason


# ── 8. 기존 시나리오 보존 ───────────────────────────────────────

def test_kill_switch_still_blocks():
    """체크리스트 #37 회귀 보장."""
    a = RiskOfficerAgent()
    d = a.decide({"action": "BUY", "confidence": 0.9}, {"kill_switch": True})
    assert d.risk_veto is True


def test_low_confidence_still_returns_watch_only():
    a = RiskOfficerAgent()
    d = a.decide({"action": "BUY", "confidence": 0.2}, {})
    assert d.action == "WATCH_ONLY"
    assert d.risk_veto is False


def test_normal_signal_still_passes():
    a = RiskOfficerAgent()
    d = a.decide({"action": "BUY", "confidence": 0.9}, {})
    assert d.risk_veto is False
    assert d.action == "BUY"


# ── 9. RiskManager 통합 — end-to-end ────────────────────────────

def test_e2e_with_real_risk_manager_clean():
    rm = make_risk_manager()
    ctx = risk_context_from_manager(rm,
                                     order={"notional_usdt": 50, "leverage": 1},
                                     account={"open_positions": 0,
                                              "emergency_stop": False})
    a = RiskOfficerAgent()
    d = a.decide({"action": "BUY", "confidence": 0.85}, ctx)
    assert d.risk_veto is False
    assert d.action == "BUY"


def test_e2e_with_real_risk_manager_kill_switch():
    rm = make_risk_manager()
    rm.activate_kill_switch("incident")
    ctx = risk_context_from_manager(rm)
    a = RiskOfficerAgent()
    d = a.decide({"action": "BUY", "confidence": 0.85}, ctx)
    assert d.risk_veto is True
    assert "Kill Switch" in d.reason


def test_e2e_with_oversized_order_blocks():
    rm = make_risk_manager(max_order_notional_usdt=100.0)
    ctx = risk_context_from_manager(rm,
                                     order={"notional_usdt": 500, "leverage": 1})
    a = RiskOfficerAgent()
    d = a.decide({"action": "BUY", "confidence": 0.9}, ctx)
    assert d.risk_veto is True
    assert "주문 금액" in d.reason


def test_e2e_with_position_slots_full():
    rm = make_risk_manager(max_open_positions=3)
    ctx = risk_context_from_manager(rm,
                                     account={"open_positions": 3,
                                              "emergency_stop": False})
    a = RiskOfficerAgent()
    d = a.decide({"action": "BUY", "confidence": 0.9}, ctx)
    assert d.risk_veto is True
    assert "포지션" in d.reason


def test_e2e_consecutive_losses_blocks():
    rm = make_risk_manager(max_consecutive_losses=3)
    rm._consecutive_losses = 3
    ctx = risk_context_from_manager(rm)
    a = RiskOfficerAgent()
    d = a.decide({"action": "BUY", "confidence": 0.9}, ctx)
    assert d.risk_veto is True
    assert "연속" in d.reason


# ── 10. is_order_intent=False 보장 ──────────────────────────────

@pytest.mark.parametrize("ctx", [
    {},
    {"kill_switch": True},
    {"emergency_stop": True},
    {"consecutive_losses": 10, "max_consecutive_losses": 5},
    {"daily_loss_pct": -5.0, "daily_loss_limit_pct": -2.0},
    {"order_notional_usdt": 999, "max_order_notional_usdt": 100},
    {"order_leverage": 10, "max_leverage": 2},
])
def test_all_decisions_have_is_order_intent_false(ctx):
    a = RiskOfficerAgent()
    d = a.decide({"action": "BUY", "confidence": 0.85}, ctx)
    assert d.is_order_intent is False


# ── 11. AgentBase Protocol 만족 ─────────────────────────────────

def test_risk_officer_satisfies_agent_base_protocol():
    from app.agents.base import AgentBase
    assert isinstance(RiskOfficerAgent(), AgentBase)
