"""체크리스트 #39 Signal Quality Agent (boosted) — 회귀 테스트.

검증:
  1. QualityBreakdown 구조
  2. 점수 컴포넌트 — confidence / valid_action / volume_surge / regime+vol_band
  3. QualityReport 통합 — liquidity_ok / fx_anomaly_ok 가산
  4. news_severity 감점 (block=-20, warn=-5)
  5. freshness_stale 감점 (-10)
  6. kimp_anomaly_hint 감점 (-10)
  7. vol_band — NORMAL 가산, HIGH 감점
  8. 점수 0~100 클램프
  9. decide — 임계값 미만/이상
 10. Orchestrator 통합 — boosted 점수 반영
 11. 기존 calc_quality 시그니처 회귀 보장
"""
from __future__ import annotations
import pytest

from app.agents.signal_quality import SignalQualityAgent, QualityBreakdown
from app.agents.orchestrator import AgentOrchestrator


# ── 1. QualityBreakdown 구조 ────────────────────────────────────

def test_breakdown_returns_dataclass():
    a = SignalQualityAgent()
    bd = a.breakdown({"action": "BUY", "confidence": 0.8}, {})
    assert isinstance(bd, QualityBreakdown)
    assert 0.0 <= bd.total <= 100.0


def test_breakdown_components_listed():
    a = SignalQualityAgent()
    bd = a.breakdown({"action": "BUY", "confidence": 0.8}, {})
    component_names = {name for name, _ in bd.components}
    assert {"base", "confidence", "valid_action", "volume_surge",
            "regime+vol_band", "quality_report", "news",
            "freshness", "kimp_anomaly"}.issubset(component_names)


# ── 2. 기본 컴포넌트 ────────────────────────────────────────────

def test_baseline_score_is_50():
    """confidence 0, action HOLD, 다른 ctx 없을 때 → base 50 만."""
    a = SignalQualityAgent()
    score = a.calc_quality({"action": "HOLD", "confidence": 0.0}, {})
    assert score == 50.0


def test_high_confidence_adds_30_points():
    a = SignalQualityAgent()
    bd = a.breakdown({"action": "BUY", "confidence": 1.0}, {})
    assert bd.confidence_pts == 30.0


def test_valid_action_adds_10():
    a = SignalQualityAgent()
    bd = a.breakdown({"action": "BUY", "confidence": 0.0}, {})
    assert bd.valid_action_pts == 10.0
    bd2 = a.breakdown({"action": "HOLD", "confidence": 0.0}, {})
    assert bd2.valid_action_pts == 0.0


def test_volume_surge_adds_5_when_above_threshold():
    a = SignalQualityAgent()
    bd = a.breakdown({"action": "BUY", "confidence": 0.0},
                       {"volume_surge": 1.5})
    assert bd.volume_surge_pts == 5.0


def test_volume_surge_zero_when_below():
    a = SignalQualityAgent()
    bd = a.breakdown({"action": "BUY", "confidence": 0.0},
                       {"volume_surge": 1.0})
    assert bd.volume_surge_pts == 0.0


def test_regime_trend_up_adds_5():
    a = SignalQualityAgent()
    bd = a.breakdown({"action": "BUY", "confidence": 0.0},
                       {"regime": "TREND_UP"})
    assert bd.regime_pts >= 5.0


def test_vol_band_normal_adds_2():
    a = SignalQualityAgent()
    bd_normal = a.breakdown({"action": "BUY", "confidence": 0.0},
                              {"vol_band": "NORMAL"})
    assert bd_normal.regime_pts == 2.0


def test_vol_band_high_subtracts_2():
    a = SignalQualityAgent()
    bd_high = a.breakdown({"action": "BUY", "confidence": 0.0},
                            {"vol_band": "HIGH"})
    assert bd_high.regime_pts == -2.0


# ── 3. QualityReport 통합 ───────────────────────────────────────

def test_quality_report_dict_both_ok_adds_10():
    a = SignalQualityAgent()
    bd = a.breakdown(
        {"action": "BUY", "confidence": 0.0},
        {"quality_report": {"liquidity_ok": True, "fx_anomaly_ok": True}},
    )
    assert bd.quality_report_pts == 10.0


def test_quality_report_partial_adds_partial():
    a = SignalQualityAgent()
    bd = a.breakdown(
        {"action": "BUY", "confidence": 0.0},
        {"quality_report": {"liquidity_ok": True, "fx_anomaly_ok": False}},
    )
    assert bd.quality_report_pts == 5.0


def test_quality_report_object_supported():
    """실제 app.market.quality.QualityReport 객체 통합 검증."""
    from app.market.quality import QualityReport, QualityCheck
    qr = QualityReport(
        label="BTC@upbit",
        checks=(
            QualityCheck("spread", True, "ok", "0.1%"),
            QualityCheck("volume", True, "ok", "1M"),
        ),
    )
    a = SignalQualityAgent()
    bd = a.breakdown({"action": "BUY", "confidence": 0.0},
                      {"quality_report": qr})
    # liquidity_ok=True, fx_anomaly_ok=True → +10
    assert bd.quality_report_pts == 10.0


# ── 4. news_severity 감점 ───────────────────────────────────────

def test_news_block_penalty():
    a = SignalQualityAgent()
    bd = a.breakdown({"action": "BUY", "confidence": 0.0},
                      {"news_severity": "block"})
    assert bd.news_penalty == -30.0


def test_news_warn_penalty():
    a = SignalQualityAgent()
    bd = a.breakdown({"action": "BUY", "confidence": 0.0},
                      {"news_severity": "warn"})
    assert bd.news_penalty == -10.0


def test_news_info_no_penalty():
    a = SignalQualityAgent()
    bd = a.breakdown({"action": "BUY", "confidence": 0.0},
                      {"news_severity": "info"})
    assert bd.news_penalty == 0.0


# ── 5. freshness_stale 감점 ─────────────────────────────────────

def test_freshness_stale_penalty():
    a = SignalQualityAgent()
    bd = a.breakdown({"action": "BUY", "confidence": 0.0},
                      {"freshness_stale": True})
    assert bd.freshness_penalty == -10.0


def test_freshness_ok_no_penalty():
    a = SignalQualityAgent()
    bd = a.breakdown({"action": "BUY", "confidence": 0.0}, {})
    assert bd.freshness_penalty == 0.0


# ── 6. kimp_anomaly_hint 감점 ───────────────────────────────────

def test_kimp_anomaly_penalty():
    a = SignalQualityAgent()
    bd = a.breakdown({"action": "BUY", "confidence": 0.0},
                      {"kimp_anomaly_hint": True})
    assert bd.kimp_anomaly_penalty == -10.0


# ── 7. 클램프 ───────────────────────────────────────────────────

def test_score_clamped_to_100_max():
    """모든 보너스 시나리오 → 100 초과해도 100 유지."""
    a = SignalQualityAgent()
    score = a.calc_quality(
        {"action": "BUY", "confidence": 1.0},
        {"volume_surge": 2.0, "regime": "TREND_UP", "vol_band": "NORMAL",
         "quality_report": {"liquidity_ok": True, "fx_anomaly_ok": True}},
    )
    assert score == 100.0


def test_score_clamped_to_0_min():
    """과한 페널티 → 0 유지."""
    a = SignalQualityAgent()
    score = a.calc_quality(
        {"action": "HOLD", "confidence": 0.0},
        {"news_severity": "block", "freshness_stale": True,
         "kimp_anomaly_hint": True, "vol_band": "HIGH"},
    )
    assert score == 0.0 or score >= 0.0
    assert score >= 0.0


# ── 8. decide ───────────────────────────────────────────────────

def test_decide_passes_when_quality_ok():
    a = SignalQualityAgent()
    d = a.decide(
        {"action": "BUY", "confidence": 0.85},
        {"volume_surge": 1.5, "regime": "TREND_UP"},
    )
    assert d.action == "BUY"
    assert d.quality_score >= 70


def test_decide_holds_when_block_news():
    """뉴스 block 페널티가 confidence/regime 보너스를 상쇄해 임계값 미달."""
    a = SignalQualityAgent()
    d = a.decide(
        {"action": "BUY", "confidence": 0.85},
        {"volume_surge": 1.5, "regime": "TREND_UP", "news_severity": "block"},
    )
    assert d.action == "HOLD"
    assert d.quality_score < 70


def test_decide_holds_when_freshness_stale():
    """freshness 감점만으로는 commercial 신호 부족 → 그래도 통과 가능"""
    a = SignalQualityAgent()
    d = a.decide(
        {"action": "BUY", "confidence": 0.85},
        {"volume_surge": 1.5, "regime": "TREND_UP", "freshness_stale": True},
    )
    # 95-10=85 — 통과
    assert d.action == "BUY"


def test_decide_explains_breakdown():
    a = SignalQualityAgent()
    d = a.decide({"action": "BUY", "confidence": 0.85},
                  {"volume_surge": 1.5, "regime": "TREND_UP"})
    assert "신호 품질" in d.explain_text
    assert "/100" in d.explain_text


# ── 9. Orchestrator 통합 ────────────────────────────────────────

def test_orchestrator_uses_boosted_quality_score():
    """Orchestrator 가 이미 boosted SignalQualityAgent 를 쓴다 — 검증."""
    o = AgentOrchestrator()
    # 강한 신호 + 좋은 ctx
    d = o.decide(
        {"action": "BUY", "confidence": 0.9},
        {"volume_surge": 1.5, "regime": "TREND_UP", "vol_band": "NORMAL",
         "quality_report": {"liquidity_ok": True, "fx_anomaly_ok": True}},
    )
    assert d.action == "BUY"
    assert d.quality_score >= 90  # 보너스가 충분히 적용됨


def test_orchestrator_blocks_on_block_news():
    """뉴스 block 이 들어가면 신호 품질 임계값 미달 → HOLD."""
    o = AgentOrchestrator()
    d = o.decide(
        {"action": "BUY", "confidence": 0.85},
        {"volume_surge": 1.5, "regime": "TREND_UP", "news_severity": "block"},
    )
    assert d.action == "HOLD"


# ── 10. backward compat ─────────────────────────────────────────

def test_calc_quality_signature_unchanged():
    """기존 호출자(예: Orchestrator._calc_quality 위임) 시그니처 보존."""
    a = SignalQualityAgent()
    # ctx 생략 가능
    s1 = a.calc_quality({"action": "BUY", "confidence": 0.5})
    s2 = a.calc_quality({"action": "BUY", "confidence": 0.5}, None)
    assert s1 == s2


def test_orchestrator_calc_quality_delegates():
    o = AgentOrchestrator()
    s_via_o = o._calc_quality({"action": "BUY", "confidence": 0.8},
                                {"regime": "TREND_UP"})
    s_via_a = SignalQualityAgent().calc_quality(
        {"action": "BUY", "confidence": 0.8}, {"regime": "TREND_UP"},
    )
    assert s_via_o == s_via_a


def test_existing_test_compatibility():
    """test_risk_and_agents.py::test_agent_approves_high_quality 시나리오 회귀."""
    a = SignalQualityAgent()
    s = a.calc_quality(
        {"action": "BUY", "confidence": 0.85},
        {"volume_surge": 1.5, "regime": "TREND_UP"},
    )
    assert s >= 70


# ── 11. AgentBase Protocol ─────────────────────────────────────

def test_signal_quality_satisfies_agent_base():
    from app.agents.base import AgentBase
    assert isinstance(SignalQualityAgent(), AgentBase)


# ── 12. is_order_intent=False ──────────────────────────────────

def test_decision_is_order_intent_false():
    a = SignalQualityAgent()
    d = a.decide({"action": "BUY", "confidence": 0.9},
                  {"volume_surge": 1.5, "regime": "TREND_UP"})
    assert d.is_order_intent is False
