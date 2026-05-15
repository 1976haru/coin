"""체크리스트 #3 — ModeCapability matrix 정확성 + 전환 규칙 회귀."""
import pytest

from app.core.modes import (
    TradingMode,
    ModeCapability,
    capability_for,
    allowed_transitions,
    promotion_distance,
    safe_default_mode,
)


# ── 매트릭스 형식 ─────────────────────────────────────────────────

def test_all_modes_have_capability_defined():
    for m in TradingMode:
        cap = capability_for(m)
        assert isinstance(cap, ModeCapability)


def test_capability_dataclass_is_frozen():
    cap = capability_for(TradingMode.PAPER)
    with pytest.raises(Exception):
        cap.can_execute_live = True   # type: ignore[misc]


def test_unknown_mode_raises():
    class Fake:
        pass
    with pytest.raises(Exception):
        capability_for(Fake())  # type: ignore[arg-type]


# ── default deny: 위험 capability 기본 차단 검증 ─────────────────

def test_paper_does_not_execute_live():
    cap = capability_for(TradingMode.PAPER)
    assert cap.can_execute_live is False
    assert cap.can_execute_live_ai_auto is False
    assert cap.can_run_paper_orders is True


def test_simulation_does_not_execute_live():
    cap = capability_for(TradingMode.SIMULATION)
    assert cap.can_execute_live is False
    assert cap.can_execute_live_ai_auto is False


def test_shadow_logs_but_does_not_execute():
    cap = capability_for(TradingMode.LIVE_SHADOW)
    assert cap.can_log_shadow is True
    assert cap.can_execute_live is False
    assert cap.can_run_paper_orders is False


def test_only_ai_execution_mode_allows_ai_auto():
    for m in TradingMode:
        cap = capability_for(m)
        if m == TradingMode.LIVE_AI_EXECUTION:
            assert cap.can_execute_live_ai_auto is True
        else:
            assert cap.can_execute_live_ai_auto is False


def test_manual_and_assist_require_manual_approval():
    assert capability_for(TradingMode.LIVE_MANUAL_APPROVAL).needs_manual_approval is True
    assert capability_for(TradingMode.LIVE_AI_ASSIST).needs_manual_approval is True
    assert capability_for(TradingMode.LIVE_AI_EXECUTION).needs_manual_approval is False
    assert capability_for(TradingMode.PAPER).needs_manual_approval is False


def test_futures_capability_is_off_for_all_modes():
    """Phase 8 완료 전까지 모든 모드 false 유지."""
    for m in TradingMode:
        assert capability_for(m).can_use_futures is False


def test_admin_token_required_in_all_modes():
    for m in TradingMode:
        assert capability_for(m).requires_admin_token is True


def test_signal_emission_allowed_in_all_modes():
    """모든 모드에서 신호/Agent 분석은 허용 (실행만 차단)."""
    for m in TradingMode:
        assert capability_for(m).can_emit_signal is True


# ── 호환 property ─────────────────────────────────────────────────

def test_property_allows_real_order_matches_matrix():
    for m in TradingMode:
        assert m.allows_real_order == capability_for(m).can_execute_live


def test_property_allows_ai_auto_execute_matches_matrix():
    for m in TradingMode:
        assert m.allows_ai_auto_execute == capability_for(m).can_execute_live_ai_auto


def test_property_requires_manual_approval_matches_matrix():
    for m in TradingMode:
        assert m.requires_manual_approval == capability_for(m).needs_manual_approval


# ── 전환 그래프 ───────────────────────────────────────────────────

def test_simulation_promote_is_paper():
    t = allowed_transitions(TradingMode.SIMULATION)
    assert t["promote"] == TradingMode.PAPER
    assert t["downgrade"] is None
    assert t["emergency"] == TradingMode.SIMULATION


def test_top_mode_cannot_promote():
    t = allowed_transitions(TradingMode.LIVE_AI_EXECUTION)
    assert t["promote"] is None
    assert t["downgrade"] == TradingMode.LIVE_AI_ASSIST
    assert t["emergency"] == TradingMode.SIMULATION


def test_promote_chain_is_one_step():
    chain = [
        TradingMode.SIMULATION,
        TradingMode.PAPER,
        TradingMode.LIVE_SHADOW,
        TradingMode.LIVE_MANUAL_APPROVAL,
        TradingMode.LIVE_AI_ASSIST,
        TradingMode.LIVE_AI_EXECUTION,
    ]
    for cur, nxt in zip(chain, chain[1:]):
        assert allowed_transitions(cur)["promote"] == nxt


def test_emergency_always_simulation():
    for m in TradingMode:
        assert allowed_transitions(m)["emergency"] == TradingMode.SIMULATION


def test_promotion_distance_correct():
    assert promotion_distance(TradingMode.PAPER, TradingMode.LIVE_SHADOW) == 1
    assert promotion_distance(TradingMode.LIVE_AI_ASSIST, TradingMode.PAPER) == -3
    assert promotion_distance(TradingMode.PAPER, TradingMode.PAPER) == 0


def test_safe_default_is_paper():
    assert safe_default_mode() == TradingMode.PAPER


# ── 문서-코드 일치 ────────────────────────────────────────────────

DOC = __import__("pathlib").Path(__file__).resolve().parents[2] / "docs" / "operating_modes.md"


def test_doc_references_all_modes():
    text = DOC.read_text(encoding="utf-8")
    for m in TradingMode:
        assert m.value in text, f"missing mode in doc: {m.value}"


def test_doc_references_capability_matrix():
    text = DOC.read_text(encoding="utf-8")
    for cap_field in [
        "can_emit_signal", "can_run_paper_orders", "can_log_shadow",
        "needs_manual_approval", "can_execute_live", "can_execute_live_ai_auto",
        "can_use_kimp_strategy", "can_use_futures", "requires_admin_token",
    ]:
        assert cap_field in text, f"capability field missing from doc: {cap_field}"


def test_doc_documents_promotion_chain():
    text = DOC.read_text(encoding="utf-8")
    for phrase in ["승격", "강등", "비상 정지", "한 단계만 위로"]:
        assert phrase in text
