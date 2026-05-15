"""체크리스트 #37 Agent Architecture — 회귀 테스트.

검증:
  1. AgentCapability + AgentBase Protocol
  2. 4개 Agent (Anomaly / SignalQuality / RiskOfficer / Orchestrator) capability
  3. AgentRegistry CRUD + by_role + catalog
  4. AnomalyAgent — anomaly/data_quality/freshness 차단
  5. SignalQualityAgent — 점수 산출 + 임계값 미달 HOLD
  6. RiskOfficerAgent — kill_switch / 연속손실 / 일손실 / WATCH_ONLY
  7. Orchestrator — backward compat decide() 유지
  8. Orchestrator.decide_with_pipeline — 단계별 보고
  9. is_order_intent=False 모든 단계 보장 (CLAUDE.md §2.3)
 10. /api/agents/catalog 공개 endpoint
"""
from __future__ import annotations
import pytest

from app.agents.base import (
    AgentBase, AgentCapability, AgentRegistry, collect_default_agents,
)
from app.agents.orchestrator import AgentOrchestrator, AgentDecision
from app.agents.anomaly import AnomalyAgent
from app.agents.signal_quality import SignalQualityAgent
from app.agents.risk_officer import RiskOfficerAgent


# ── 1. AgentCapability + Protocol ───────────────────────────────

def test_capability_minimal_fields():
    cap = AgentCapability(name="x", role="anomaly", description="d")
    assert cap.has_veto_power is False
    assert cap.is_deterministic is True
    assert cap.requires_llm is False
    assert cap.inputs == ()


def test_capability_to_dict():
    cap = AgentCapability(name="x", role="risk_officer", description="d",
                           has_veto_power=True, inputs=("a", "b"))
    d = cap.to_dict()
    for k in ("name", "role", "description", "has_veto_power",
              "is_deterministic", "requires_llm", "inputs"):
        assert k in d


def test_all_default_agents_satisfy_protocol():
    for cls in (AnomalyAgent, SignalQualityAgent, RiskOfficerAgent, AgentOrchestrator):
        assert isinstance(cls(), AgentBase), f"{cls.__name__} 가 AgentBase 미준수"


# ── 2. 각 Agent capability ──────────────────────────────────────

@pytest.mark.parametrize("cls,name,role,veto", [
    (AnomalyAgent,        "anomaly",        "anomaly",         True),
    (SignalQualityAgent,  "signal_quality", "signal_quality",  False),
    (RiskOfficerAgent,    "risk_officer",   "risk_officer",    True),
    (AgentOrchestrator,   "orchestrator",   "orchestrator",    False),
])
def test_agent_capabilities(cls, name, role, veto):
    cap = cls.capability
    assert cap.name == name
    assert cap.role == role
    assert cap.has_veto_power is veto


def test_agent_names_are_unique():
    caps = [
        AnomalyAgent.capability,
        SignalQualityAgent.capability,
        RiskOfficerAgent.capability,
        AgentOrchestrator.capability,
    ]
    names = [c.name for c in caps]
    assert len(names) == len(set(names))


# ── 3. AgentRegistry ────────────────────────────────────────────

def test_registry_register_and_lookup():
    r = AgentRegistry()
    r.register(AnomalyAgent())
    assert r.get("anomaly") is not None
    assert "anomaly" in r.names()


def test_registry_by_role():
    r = AgentRegistry()
    r.register(AnomalyAgent())
    r.register(RiskOfficerAgent())
    r.register(SignalQualityAgent())
    veto_holders = [a for a in r.all() if a.capability.has_veto_power]
    assert len(veto_holders) == 2  # Anomaly + RiskOfficer


def test_registry_rejects_object_without_capability():
    class Bare:
        pass
    r = AgentRegistry()
    with pytest.raises(TypeError):
        r.register(Bare())


def test_registry_catalog_returns_dicts():
    r = collect_default_agents()
    cat = r.catalog()
    assert len(cat) == 4
    assert {c["name"] for c in cat} == {
        "anomaly", "signal_quality", "risk_officer", "orchestrator",
    }


def test_collect_default_agents_returns_four():
    r = collect_default_agents()
    assert sorted(r.names()) == [
        "anomaly", "orchestrator", "risk_officer", "signal_quality",
    ]


# ── 4. AnomalyAgent ─────────────────────────────────────────────

def test_anomaly_blocks_when_anomaly_flag():
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"anomaly": True})
    assert d.action == "HOLD"
    assert d.risk_veto is True
    assert "이상 데이터" in d.reason


def test_anomaly_blocks_on_data_quality_alarm():
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"data_quality_alarm": True})
    assert d.risk_veto is True


def test_anomaly_blocks_on_freshness_stale():
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"freshness_stale": True})
    assert d.risk_veto is True


def test_anomaly_passes_when_clean_context():
    a = AnomalyAgent()
    d = a.decide({"action": "BUY", "confidence": 0.8}, {})
    assert d.risk_veto is False


# ── 5. SignalQualityAgent ───────────────────────────────────────

def test_signal_quality_calc_baseline():
    a = SignalQualityAgent()
    s = a.calc_quality({"action": "HOLD", "confidence": 0.0}, {})
    assert s == 50.0


def test_signal_quality_high_confidence_increases_score():
    a = SignalQualityAgent()
    s = a.calc_quality({"action": "BUY", "confidence": 1.0}, {})
    assert s >= 80.0


def test_signal_quality_holds_below_threshold():
    a = SignalQualityAgent()
    d = a.decide({"action": "BUY", "confidence": 0.0}, {})
    assert d.action == "HOLD"
    assert d.quality_score < a.MIN_QUALITY_SCORE


def test_signal_quality_passes_above_threshold():
    a = SignalQualityAgent()
    d = a.decide(
        {"action": "BUY", "confidence": 0.85},
        {"volume_surge": 1.5, "regime": "TREND_UP"},
    )
    assert d.action == "BUY"
    assert d.quality_score >= a.MIN_QUALITY_SCORE


# ── 6. RiskOfficerAgent ─────────────────────────────────────────

def test_risk_officer_blocks_kill_switch():
    r = RiskOfficerAgent()
    d = r.decide({"action": "BUY", "confidence": 0.9}, {"kill_switch": True})
    assert d.risk_veto is True
    assert "Kill Switch" in d.reason


def test_risk_officer_blocks_consecutive_losses():
    r = RiskOfficerAgent()
    d = r.decide({"action": "BUY", "confidence": 0.9},
                  {"consecutive_losses": 5, "max_consecutive_losses": 5})
    assert d.risk_veto is True
    assert "연속" in d.reason


def test_risk_officer_blocks_daily_loss():
    r = RiskOfficerAgent()
    d = r.decide({"action": "BUY", "confidence": 0.9},
                  {"daily_loss_pct": -3.0, "daily_loss_limit_pct": -2.0})
    assert d.risk_veto is True
    assert "일 손실" in d.reason


def test_risk_officer_watch_only_on_low_confidence():
    """저신뢰도 → WATCH_ONLY action 으로 변환."""
    r = RiskOfficerAgent()
    d = r.decide({"action": "BUY", "confidence": 0.2}, {})
    assert d.action == "WATCH_ONLY"
    assert d.risk_veto is False


def test_risk_officer_passes_normal():
    r = RiskOfficerAgent()
    d = r.decide({"action": "BUY", "confidence": 0.9}, {})
    assert d.risk_veto is False
    assert d.action == "BUY"


# ── 7. Orchestrator backward compat ─────────────────────────────

def test_orchestrator_decide_anomaly_returns_hold():
    o = AgentOrchestrator()
    d = o.decide({"action": "BUY", "confidence": 0.9}, {"anomaly": True})
    assert d.action == "HOLD"
    assert d.risk_veto is True


def test_orchestrator_decide_blocked_signal():
    o = AgentOrchestrator()
    d = o.decide({"action": "BLOCKED", "confidence": 0.9, "reason": "test"})
    assert d.action == "HOLD"
    assert d.risk_veto is True


def test_orchestrator_decide_low_quality_returns_hold():
    o = AgentOrchestrator()
    d = o.decide({"action": "BUY", "confidence": 0.0, "reason": "weak"})
    assert d.action == "HOLD"


def test_orchestrator_decide_high_quality_passes():
    o = AgentOrchestrator()
    d = o.decide(
        {"action": "BUY", "confidence": 0.85, "reason": "strong"},
        {"volume_surge": 1.5, "regime": "TREND_UP"},
    )
    assert d.action == "BUY"
    assert d.quality_score >= 70


def test_orchestrator_decide_kill_switch_blocks():
    o = AgentOrchestrator()
    d = o.decide(
        {"action": "BUY", "confidence": 0.85},
        {"volume_surge": 1.5, "regime": "TREND_UP", "kill_switch": True},
    )
    assert d.risk_veto is True


# ── 8. decide_with_pipeline ─────────────────────────────────────

def test_pipeline_returns_three_stages():
    o = AgentOrchestrator()
    out = o.decide_with_pipeline(
        {"action": "BUY", "confidence": 0.85},
        {"volume_surge": 1.5, "regime": "TREND_UP"},
    )
    assert len(out["stages"]) == 3
    names = [s["agent"] for s in out["stages"]]
    assert names == ["anomaly", "signal_quality", "risk_officer"]


def test_pipeline_final_matches_decide():
    o = AgentOrchestrator()
    sig = {"action": "BUY", "confidence": 0.85}
    ctx = {"volume_surge": 1.5, "regime": "TREND_UP"}
    direct = o.decide(sig, ctx)
    via_pipeline = o.decide_with_pipeline(sig, ctx)
    assert via_pipeline["final"]["action"] == direct.action


# ── 9. is_order_intent=False 보장 ───────────────────────────────

@pytest.mark.parametrize("ctx", [
    {},
    {"anomaly": True},
    {"kill_switch": True},
    {"volume_surge": 2.0, "regime": "TREND_UP"},
])
def test_orchestrator_decision_is_order_intent_always_false(ctx):
    """CLAUDE.md §2.3: 어떤 시나리오에서도 is_order_intent=False."""
    o = AgentOrchestrator()
    d = o.decide({"action": "BUY", "confidence": 0.85}, ctx)
    assert d.is_order_intent is False


def test_all_sub_agent_decisions_have_is_order_intent_false():
    for agent in (AnomalyAgent(), SignalQualityAgent(), RiskOfficerAgent()):
        d = agent.decide({"action": "BUY", "confidence": 0.85}, {})
        assert d.is_order_intent is False


# ── 10. /api/agents/catalog ─────────────────────────────────────

def test_api_agents_catalog_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.get("/api/agents/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 4
    names = {a["name"] for a in body["agents"]}
    assert names == {"anomaly", "signal_quality", "risk_officer", "orchestrator"}


def test_api_agents_catalog_exposes_role_and_veto():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    body = client.get("/api/agents/catalog").json()
    risk = next(a for a in body["agents"] if a["name"] == "risk_officer")
    assert risk["role"] == "risk_officer"
    assert risk["has_veto_power"] is True
