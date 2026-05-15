"""체크리스트 #41 Explain Agent — 회귀 테스트.

검증:
  1. capability + AgentBase Protocol
  2. explain_signal — short/full/markdown
  3. explain_decision — dict/객체 입력
  4. explain_pipeline — Orchestrator.decide_with_pipeline 결과
  5. action 라벨 매핑 (BUY/CLOSE/OPEN_REVERSE_KIMP/WATCH_ONLY 등)
  6. ctx 보조 정보 (regime/vol_band/volume_surge/themes/freshness/kimp)
  7. risk_veto 표시 (⛔)
  8. is_order_intent=False
"""
from __future__ import annotations
import pytest

from app.agents.explain import ExplainAgent
from app.agents.orchestrator import AgentOrchestrator, AgentDecision


# ── 1. Capability + Protocol ────────────────────────────────────

def test_capability_metadata():
    cap = ExplainAgent.capability
    assert cap.name == "explain"
    assert cap.role == "explain"
    assert cap.has_veto_power is False
    assert cap.is_deterministic is True


def test_satisfies_agent_base_protocol():
    from app.agents.base import AgentBase
    assert isinstance(ExplainAgent(), AgentBase)


# ── 2. explain_signal — formats ─────────────────────────────────

def test_short_format_one_line():
    a = ExplainAgent()
    s = a.explain_signal({"action": "BUY", "confidence": 0.85, "reason": "추세"},
                          format="short")
    assert "BUY" in s
    assert "추세" in s
    assert "\n" not in s


def test_full_format_multiple_lines():
    a = ExplainAgent()
    s = a.explain_signal(
        {"action": "BUY", "confidence": 0.85, "reason": "추세",
         "quality_score": 85},
        {"regime": "TREND_UP"},
        format="full",
    )
    assert "\n" in s
    assert "신뢰도" in s
    assert "TREND_UP" in s


def test_markdown_format_uses_bullets():
    a = ExplainAgent()
    s = a.explain_signal(
        {"action": "BUY", "confidence": 0.85, "reason": "추세",
         "quality_score": 85},
        {"regime": "TREND_UP"},
        format="markdown",
    )
    assert s.startswith("###")
    assert "**사유**" in s


def test_short_format_includes_quality_when_positive():
    a = ExplainAgent()
    s = a.explain_signal(
        {"action": "BUY", "confidence": 0.85, "reason": "x", "quality_score": 87.5},
        format="short",
    )
    assert "87.5" in s


def test_short_format_no_quality_when_zero():
    a = ExplainAgent()
    s = a.explain_signal(
        {"action": "HOLD", "confidence": 0.0, "reason": "x", "quality_score": 0.0},
        format="short",
    )
    assert "품질" not in s


# ── 3. action label mapping ─────────────────────────────────────

@pytest.mark.parametrize("action,label_part", [
    ("BUY", "매수"),
    ("SELL", "매도"),
    ("HOLD", "관망"),
    ("BLOCKED", "차단"),
    ("CLOSE", "청산"),
    ("OPEN_REVERSE_KIMP", "역김프"),
    ("WATCH_ONLY", "관찰"),
])
def test_action_labels_in_short_format(action, label_part):
    a = ExplainAgent()
    s = a.explain_signal({"action": action, "confidence": 0.5, "reason": ""},
                          format="short")
    assert label_part in s


def test_unknown_action_falls_back_to_action_string():
    a = ExplainAgent()
    s = a.explain_signal({"action": "CUSTOM", "confidence": 0.5, "reason": ""},
                          format="short")
    assert "CUSTOM" in s


# ── 4. explain_decision — dict/object ───────────────────────────

def test_explain_decision_with_dict():
    a = ExplainAgent()
    d = {"action": "BUY", "confidence": 0.85, "reason": "추세",
         "quality_score": 85, "risk_veto": False}
    s = a.explain_decision(d, format="short")
    assert "BUY" in s


def test_explain_decision_with_agent_decision_object():
    a = ExplainAgent()
    d = AgentDecision("BUY", 0.85, "추세", quality_score=85)
    s = a.explain_decision(d, format="short")
    assert "BUY" in s


def test_explain_decision_shows_veto_tag():
    a = ExplainAgent()
    d = AgentDecision("HOLD", 0.0, "Kill Switch",
                      quality_score=0.0, risk_veto=True)
    s = a.explain_decision(d, format="short")
    assert "⛔" in s


def test_explain_decision_no_veto_tag_when_passed():
    a = ExplainAgent()
    d = AgentDecision("BUY", 0.85, "추세", quality_score=85, risk_veto=False)
    s = a.explain_decision(d, format="short")
    assert "⛔" not in s


def test_explain_decision_markdown_shows_full_detail():
    a = ExplainAgent()
    d = AgentDecision("BUY", 0.85, "추세", quality_score=85,
                      explain_text="상세 정보")
    s = a.explain_decision(d, format="markdown")
    assert "**사유**" in s
    assert "**품질**" in s
    assert "상세 정보" in s


# ── 5. explain_pipeline ─────────────────────────────────────────

def test_explain_pipeline_short_format_summarizes():
    a = ExplainAgent()
    pipeline = {
        "final": {"action": "BUY", "confidence": 0.85, "reason": "추세",
                  "quality_score": 85, "risk_veto": False},
        "stages": [],
    }
    s = a.explain_pipeline(pipeline, format="short")
    assert "BUY" in s
    assert "파이프라인" in s


def test_explain_pipeline_full_includes_all_stages():
    a = ExplainAgent()
    pipeline = {
        "final": {"action": "BUY", "confidence": 0.85, "reason": "추세",
                  "quality_score": 85, "risk_veto": False},
        "stages": [
            {"agent": "anomaly", "decision":
             {"action": "BUY", "confidence": 0.85, "reason": "정상",
              "quality_score": 0, "risk_veto": False}},
            {"agent": "signal_quality", "decision":
             {"action": "BUY", "confidence": 0.85, "reason": "품질 통과",
              "quality_score": 85, "risk_veto": False}},
            {"agent": "risk_officer", "decision":
             {"action": "BUY", "confidence": 0.85, "reason": "리스크 OK",
              "quality_score": 85, "risk_veto": False}},
        ],
    }
    s = a.explain_pipeline(pipeline, format="full")
    assert "anomaly" in s
    assert "signal_quality" in s
    assert "risk_officer" in s
    assert "최종" in s


def test_explain_pipeline_markdown_format():
    a = ExplainAgent()
    pipeline = {
        "final": {"action": "HOLD", "confidence": 0.0, "reason": "Kill Switch",
                  "quality_score": 0, "risk_veto": True},
        "stages": [
            {"agent": "anomaly", "decision":
             {"action": "BUY", "confidence": 0.85, "reason": "정상",
              "quality_score": 0, "risk_veto": False}},
            {"agent": "risk_officer", "decision":
             {"action": "HOLD", "confidence": 0.0, "reason": "Kill Switch",
              "quality_score": 0, "risk_veto": True}},
        ],
    }
    s = a.explain_pipeline(pipeline, format="markdown")
    assert "## 의사결정 파이프라인" in s
    assert "## 최종 결정" in s
    assert "⛔" in s  # risk_officer veto


# ── 6. ctx 보조 정보 ────────────────────────────────────────────

def test_full_format_includes_themes():
    a = ExplainAgent()
    s = a.explain_signal(
        {"action": "BUY", "confidence": 0.85, "reason": "추세"},
        {"themes": ["AI", "L1"]},
        format="full",
    )
    assert "AI" in s
    assert "L1" in s


def test_full_format_includes_volume_surge():
    a = ExplainAgent()
    s = a.explain_signal(
        {"action": "BUY", "confidence": 0.85, "reason": "추세"},
        {"volume_surge": 1.8},
        format="full",
    )
    assert "1.80" in s


def test_full_format_includes_news_when_not_info():
    a = ExplainAgent()
    s = a.explain_signal(
        {"action": "BUY", "confidence": 0.85, "reason": "추세"},
        {"news_severity": "warn"},
        format="full",
    )
    assert "warn" in s


def test_full_format_omits_news_when_info():
    a = ExplainAgent()
    s = a.explain_signal(
        {"action": "BUY", "confidence": 0.85, "reason": "추세"},
        {"news_severity": "info"},
        format="full",
    )
    # info 는 default 라 표시하지 않음
    assert "news=info" not in s


def test_full_format_includes_freshness_stale_warning():
    a = ExplainAgent()
    s = a.explain_signal(
        {"action": "BUY", "confidence": 0.85, "reason": "추세"},
        {"freshness_stale": True},
        format="full",
    )
    assert "freshness" in s.lower() or "stale" in s.lower()


def test_full_format_includes_kimp_anomaly_warning():
    a = ExplainAgent()
    s = a.explain_signal(
        {"action": "BUY", "confidence": 0.85, "reason": "추세"},
        {"kimp_anomaly_hint": True},
        format="full",
    )
    assert "kimp" in s.lower()


# ── 7. AgentBase contract decide ────────────────────────────────

def test_decide_returns_agent_decision_with_explain_text():
    a = ExplainAgent()
    d = a.decide({"action": "BUY", "confidence": 0.85, "reason": "추세",
                  "quality_score": 85}, {"regime": "TREND_UP"})
    assert d.action == "BUY"
    assert d.explain_text  # non-empty
    assert "BUY" in d.explain_text or "매수" in d.explain_text


# ── 8. is_order_intent always False ─────────────────────────────

def test_decision_is_order_intent_false():
    a = ExplainAgent()
    d = a.decide({"action": "BUY", "confidence": 0.85}, {})
    assert d.is_order_intent is False


# ── 9. Orchestrator pipeline → ExplainAgent 통합 e2e ───────────

def test_e2e_orchestrator_pipeline_explain():
    """Orchestrator 가 만든 pipeline_result 를 ExplainAgent 가 설명."""
    o = AgentOrchestrator()
    pipeline = o.decide_with_pipeline(
        {"action": "BUY", "confidence": 0.85, "reason": "추세"},
        {"volume_surge": 1.5, "regime": "TREND_UP"},
    )
    explanation = ExplainAgent().explain_pipeline(pipeline, format="full")
    assert "anomaly" in explanation
    assert "signal_quality" in explanation
    assert "risk_officer" in explanation
    assert "최종" in explanation


def test_e2e_pipeline_with_veto_shows_in_explanation():
    o = AgentOrchestrator()
    pipeline = o.decide_with_pipeline(
        {"action": "BUY", "confidence": 0.85},
        {"kill_switch": True},
    )
    explanation = ExplainAgent().explain_pipeline(pipeline, format="markdown")
    assert "⛔" in explanation


# ── 10. ExplainAgent 가 default registry 에 등록되지 않음 ──────

def test_explain_agent_not_in_default_registry():
    """기본 4-agent 파이프라인에 자동 포함되지 않음 — 별도 호출 전용."""
    from app.agents.base import collect_default_agents
    r = collect_default_agents()
    assert "explain" not in r.names()
