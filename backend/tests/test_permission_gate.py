"""PermissionGate 단독 단위 테스트 — 모드 × 플래그 매트릭스."""
from app.core.modes import TradingMode
from app.risk.permission_gate import PermissionGate


def _gate(mode, *, live=False, ai=False, kimp=False) -> PermissionGate:
    return PermissionGate(mode, enable_live_trading=live,
                          enable_ai_execution=ai, enable_kimp_strategy=kimp)


def test_paper_route_is_paper():
    d = _gate(TradingMode.PAPER).check({"symbol": "BTC/USDT", "side": "BUY"})
    assert d.allowed is True
    assert d.route == "paper"


def test_simulation_route_is_paper():
    d = _gate(TradingMode.SIMULATION).check({"side": "BUY"})
    assert d.route == "paper"


def test_shadow_blocks_orders_but_logs():
    d = _gate(TradingMode.LIVE_SHADOW).check({"side": "BUY"})
    assert d.allowed is False
    assert d.route == "shadow"


def test_live_manual_routes_to_approval_queue_when_flag_on():
    d = _gate(TradingMode.LIVE_MANUAL_APPROVAL, live=True).check({"side": "BUY"})
    assert d.allowed is False
    assert d.route == "approval_queue"


def test_live_manual_blocked_when_live_flag_off():
    d = _gate(TradingMode.LIVE_MANUAL_APPROVAL, live=False).check({"side": "BUY"})
    assert d.allowed is False
    assert d.route == "blocked"


def test_kimp_blocked_when_flag_off():
    d = _gate(TradingMode.PAPER, kimp=False).check({"side": "OPEN_REVERSE_KIMP"})
    assert d.allowed is False
    assert d.route == "blocked"
    assert "ENABLE_KIMP_STRATEGY" in d.reason


def test_kimp_allowed_in_paper_when_flag_on():
    d = _gate(TradingMode.PAPER, kimp=True).check({"side": "OPEN_REVERSE_KIMP"})
    assert d.allowed is True
    assert d.route == "paper"


def test_ai_execution_requires_ai_flag():
    """source=ai 인데 ENABLE_AI_EXECUTION=false → 차단."""
    d = _gate(TradingMode.LIVE_AI_EXECUTION, live=True, ai=False).check(
        {"side": "BUY"}, source="ai")
    assert d.allowed is False
    assert "ENABLE_AI_EXECUTION" in d.reason


def test_ai_execution_allowed_when_both_flags_on():
    d = _gate(TradingMode.LIVE_AI_EXECUTION, live=True, ai=True).check(
        {"side": "BUY"}, source="ai")
    assert d.allowed is True
    assert d.route == "live"
