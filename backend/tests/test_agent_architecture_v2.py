"""체크리스트 #37 Agent Architecture (6-role) — 회귀 테스트.

본 테스트는 ``app.agents.base`` 의 6-role 구조적 layer (Observer / Analyst /
Risk Auditor / Strategy Researcher / Report Writer / Execution Recommender)
검증. 기존 4-agent 시스템 테스트 (``test_agent_architecture.py`` 40 케이스) 는
변경 없이 유지된다 (회귀 보호).

검증:
  Role enum / 권한:
    1. AgentArchitectureRole 6개 값
    2. AgentPermission 카탈로그에 FORBIDDEN 8개 존재
    3. FORBIDDEN_AGENT_PERMISSIONS frozenset 8개
    4. AgentPermission FORBIDDEN 은 어떤 카드 allowed_permissions 에도 없음
  JSON 직렬화:
    5. AgentInput.to_dict / to_json
    6. AgentOutput.to_dict / to_json + ISO timestamp
    7. AgentDecision.to_dict findings + recommendations 평탄
    8. AgentRecommendation.to_dict + is_order_request 노출
    9. AgentFinding.to_dict
  영구 False 플래그:
   10. AgentOutput.direct_order_allowed 기본 False
   11. AgentOutput.used_for_order 기본 False
   12. AgentDecision.is_executable 기본 False
   13. AgentRecommendation.is_order_request 기본 False
   14. AgentCard.direct_order_allowed / can_invoke_broker / can_invoke_order_gateway 기본 False
   15. AgentSafetyPolicy 4개 영구 False
   16. 모든 dataclass frozen
  ABC / safety:
   17. StructuredAgentBase 직접 인스턴스화 불가
   18. card.grants_any_forbidden → AgentSafetyViolation
   19. safety.direct_order_allowed=True → AgentSafetyViolation
   20. card.role 과 ClassVar role 불일치 → AgentSafetyViolation
  6 role skeleton agents:
   21. ObserverAgent — observation finding + no recommendation
   22. AnalystAgent — analysis findings + no recommendation
   23. RiskAuditorAgent — blocked_by + review_codes 평탄화
   24. StrategyResearcherAgent — regime + catalog findings
   25. ReportWriterAgent — report recommendation requires_review=True
   26. ExecutionRecommenderAgent — execution_recommendation + is_order_request=False
  Registry:
   27. StructuredAgentRegistry register + by_role + all + cards + catalog
   28. collect_architecture_agents 6개 role 모두 등록
   29. catalog dict 직렬화 + forbidden 권한 부재 검증
   30. roles_present == 6
  MOCA / 문서:
   31. 각 카드 forbidden_actions 에 execute_order/invoke_broker 명시
   32. docs/agent_architecture.md 존재
   33. docs 에 6개 역할 설명 존재
   34. docs 에 직접 주문 금지 명시
   35. CLAUDE.md 에 Agent 직접 주문 금지 원칙 존재
  Static guards (base.py):
   36. broker / execution import 부재
   37. order_gateway / adapter import 부재
   38. network SDK import 부재
   39. order method 호출 부재 (.place_order / .cancel_order / .get_balance)
   40. forbidden literal 부재 (direct_order_allowed=True 등)
   41. executable_order / order_request / broker_payload string literal 부재
  Backward compat:
   42. 기존 AgentBase / AgentCapability / AgentRegistry 보존
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.agents import base as agents_base
from app.agents.base import (
    FORBIDDEN_AGENT_PERMISSIONS,
    AgentArchitectureRole,
    AgentCapability,
    AgentCard,
    AgentDecision,
    AgentFinding,
    AgentInput,
    AgentOutput,
    AgentPermission,
    AgentRecommendation,
    AgentRegistry,
    AgentSafetyPolicy,
    AgentSafetyViolation,
    AnalystAgent,
    ExecutionRecommenderAgent,
    ObserverAgent,
    ReportWriterAgent,
    RiskAuditorAgent,
    StrategyResearcherAgent,
    StructuredAgentBase,
    StructuredAgentRegistry,
    collect_architecture_agents,
)


_BASE_PY = Path(agents_base.__file__)
_DOC_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs" / "agent_architecture.md"
)
_CLAUDE_MD = (
    Path(__file__).resolve().parents[2] / "CLAUDE.md"
)


# ── 1-4. Role enum / Permission ──────────────────────────────────


def test_role_enum_has_six_values():
    names = {r.name for r in AgentArchitectureRole}
    assert names == {
        "OBSERVER", "ANALYST", "RISK_AUDITOR",
        "STRATEGY_RESEARCHER", "REPORT_WRITER", "EXECUTION_RECOMMENDER",
    }


def test_forbidden_permission_enum_members_exist():
    for name in (
        "EXECUTE_ORDER", "INVOKE_BROKER", "INVOKE_ORDER_GATEWAY",
        "READ_SECRETS", "WRITE_ORDER_REQUEST",
        "PLACE_ORDER_PERMISSION", "CANCEL_ORDER_PERMISSION",
        "GET_BALANCE_PERMISSION",
    ):
        assert hasattr(AgentPermission, name)


def test_forbidden_permission_set_has_eight():
    assert len(FORBIDDEN_AGENT_PERMISSIONS) == 8


def test_forbidden_permission_not_granted_to_any_default_agent():
    r = collect_architecture_agents()
    for agent in r.all():
        bad = agent.card.allowed_permissions & FORBIDDEN_AGENT_PERMISSIONS
        assert not bad, f"{type(agent).__name__} grants forbidden: {bad}"


# ── 5-9. JSON 직렬화 ────────────────────────────────────────────


def test_agent_input_json_serializable():
    inp = AgentInput(role="OBSERVER", task="observe", payload={"symbol": "BTC"})
    d = inp.to_dict()
    assert d["role"] == "OBSERVER" and d["task"] == "observe"
    parsed = json.loads(inp.to_json())
    assert parsed["payload"]["symbol"] == "BTC"


def test_agent_output_json_serializable_and_isoformat():
    agent = ObserverAgent()
    out = agent.evaluate(AgentInput(role="OBSERVER", task="observe", payload={}))
    d = out.to_dict()
    assert d["direct_order_allowed"] is False
    assert d["used_for_order"] is False
    # ISO 8601 timestamp
    assert "T" in d["generated_at"]
    parsed = json.loads(out.to_json())
    assert parsed["decision"]["is_executable"] is False


def test_agent_decision_dict_lists_findings_and_recommendations():
    d = AgentDecision(
        role="ANALYST", summary="s",
        findings=(AgentFinding(kind="k", severity="INFO", message="m"),),
        recommendations=(AgentRecommendation(kind="r", summary="rs"),),
    )
    dd = d.to_dict()
    assert isinstance(dd["findings"], list) and len(dd["findings"]) == 1
    assert isinstance(dd["recommendations"], list) and len(dd["recommendations"]) == 1
    assert dd["is_executable"] is False


def test_agent_recommendation_dict_exposes_is_order_request_false():
    rec = AgentRecommendation(kind="x", summary="y")
    d = rec.to_dict()
    assert d["is_order_request"] is False
    assert d["requires_review"] is True


def test_agent_finding_dict_shape():
    f = AgentFinding(kind="k", severity="HIGH", message="m", evidence={"a": 1})
    d = f.to_dict()
    assert d == {"kind": "k", "severity": "HIGH", "message": "m", "evidence": {"a": 1}}


# ── 10-16. 영구 False 플래그 + frozen ───────────────────────────


def test_agent_output_defaults_direct_order_allowed_false():
    out = AgentOutput(
        role="OBSERVER", version="v1",
        generated_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc),
        decision=AgentDecision(role="OBSERVER", summary="s"),
    )
    assert out.direct_order_allowed is False
    assert out.used_for_order is False


def test_agent_decision_defaults_is_executable_false():
    d = AgentDecision(role="ANALYST", summary="s")
    assert d.is_executable is False


def test_agent_recommendation_defaults_is_order_request_false():
    rec = AgentRecommendation(kind="x", summary="y")
    assert rec.is_order_request is False


def test_agent_card_default_flags_false():
    card = ObserverAgent.card
    assert card.direct_order_allowed is False
    assert card.can_invoke_broker is False
    assert card.can_invoke_order_gateway is False


def test_safety_policy_all_false():
    p = AgentSafetyPolicy()
    assert p.direct_order_allowed is False
    assert p.can_invoke_broker is False
    assert p.can_invoke_order_gateway is False
    assert p.used_for_order is False


def test_dataclasses_are_frozen():
    inp = AgentInput(role="x", task="y")
    out = ObserverAgent().evaluate(inp)
    with pytest.raises(Exception):
        out.direct_order_allowed = True  # type: ignore[misc]
    rec = AgentRecommendation(kind="x", summary="y")
    with pytest.raises(Exception):
        rec.is_order_request = True  # type: ignore[misc]
    decision = AgentDecision(role="x", summary="y")
    with pytest.raises(Exception):
        decision.is_executable = True  # type: ignore[misc]


# ── 17-20. ABC / safety ─────────────────────────────────────────


def test_structured_agent_base_cannot_be_instantiated():
    with pytest.raises(TypeError):
        StructuredAgentBase()  # type: ignore[abstract]


def test_validate_safety_rejects_forbidden_permission_in_card():
    class BadAgent(StructuredAgentBase):
        role = AgentArchitectureRole.OBSERVER
        card = AgentCard(
            role=AgentArchitectureRole.OBSERVER,
            title="bad",
            description="d",
            allowed_permissions=frozenset((AgentPermission.EXECUTE_ORDER,)),
        )

        def evaluate(self, input):  # pragma: no cover - never reached
            raise AssertionError

    with pytest.raises(AgentSafetyViolation):
        BadAgent().validate_safety()


def test_validate_safety_rejects_direct_order_allowed_safety_policy():
    class BadAgent(StructuredAgentBase):
        role = AgentArchitectureRole.OBSERVER
        card = AgentCard(
            role=AgentArchitectureRole.OBSERVER,
            title="bad",
            description="d",
        )
        safety = AgentSafetyPolicy(direct_order_allowed=True)

        def evaluate(self, input):  # pragma: no cover - never reached
            raise AssertionError

    with pytest.raises(AgentSafetyViolation):
        BadAgent().validate_safety()


def test_validate_safety_rejects_role_mismatch():
    class MismatchAgent(StructuredAgentBase):
        role = AgentArchitectureRole.OBSERVER
        card = AgentCard(
            role=AgentArchitectureRole.ANALYST,  # 불일치
            title="x",
            description="d",
        )

        def evaluate(self, input):  # pragma: no cover - never reached
            raise AssertionError

    with pytest.raises(AgentSafetyViolation):
        MismatchAgent().validate_safety()


# ── 21-26. 6 role skeleton agents ───────────────────────────────


def test_observer_makes_observation_findings_only():
    out = ObserverAgent().evaluate(AgentInput(
        role="OBSERVER", task="observe",
        payload={
            "symbol": "BTC",
            "freshness_state": "ok",
            "data_quality_grade": "GOOD",
            "notice_context": {},
            "theme_context": {},
        },
    ))
    assert out.decision.recommendations == ()
    assert len(out.decision.findings) == 4
    assert all(f.severity == "INFO" for f in out.decision.findings)


def test_analyst_emits_analysis_findings():
    out = AnalystAgent().evaluate(AgentInput(
        role="ANALYST", task="analyze",
        payload={"strategy_signal": {"action": "HOLD"}, "regime": "RANGE"},
    ))
    kinds = {f.kind for f in out.decision.findings}
    assert "signal_analysis" in kinds
    assert "regime_analysis" in kinds
    assert out.decision.recommendations == ()


def test_risk_auditor_extracts_blocked_and_review_codes():
    out = RiskAuditorAgent().evaluate(AgentInput(
        role="RISK_AUDITOR", task="audit",
        payload={
            "kimp_guard_decision": {
                "blocked_by": ["fx_invalid"],
                "review_codes": ["fx_source_missing"],
            },
            "funding_guard_decision": {
                "blocked_by": ["funding_extreme"],
                "review_codes": [],
            },
        },
    ))
    kinds = {f.kind for f in out.decision.findings}
    assert "kimp_guard_blocked" in kinds
    assert "kimp_guard_review" in kinds
    assert "funding_guard_blocked" in kinds


def test_strategy_researcher_findings():
    out = StrategyResearcherAgent().evaluate(AgentInput(
        role="STRATEGY_RESEARCHER", task="research",
        payload={"regime": "TREND_UP", "strategy_catalog": [1, 2, 3]},
    ))
    kinds = {f.kind for f in out.decision.findings}
    assert "regime_research" in kinds
    assert "catalog_research" in kinds


def test_report_writer_emits_review_required_recommendation():
    out = ReportWriterAgent().evaluate(AgentInput(
        role="REPORT_WRITER", task="report",
        payload={"findings_bundle": ["f1", "f2", "f3"]},
    ))
    recs = out.decision.recommendations
    assert len(recs) == 1
    assert recs[0].requires_review is True
    assert recs[0].is_order_request is False
    assert "3" in recs[0].summary


def test_execution_recommender_is_not_order_request():
    out = ExecutionRecommenderAgent().evaluate(AgentInput(
        role="EXECUTION_RECOMMENDER", task="recommend",
        payload={"candidate_summary": "BTC long candidate"},
    ))
    recs = out.decision.recommendations
    assert len(recs) == 1
    assert recs[0].kind == "execution_recommendation"
    assert recs[0].is_order_request is False  # 영구
    assert recs[0].requires_review is True
    assert out.decision.is_executable is False
    assert out.direct_order_allowed is False


# ── 27-30. Registry ─────────────────────────────────────────────


def test_registry_register_by_role_all_cards():
    r = StructuredAgentRegistry()
    r.register(ObserverAgent())
    r.register(AnalystAgent())
    assert len(r.all()) == 2
    assert len(r.by_role(AgentArchitectureRole.OBSERVER)) == 1
    assert len(r.cards()) == 2


def test_collect_architecture_agents_registers_all_six():
    r = collect_architecture_agents()
    assert r.roles_present() == set(AgentArchitectureRole)
    assert len(r.all()) == 6


def test_registry_catalog_has_no_forbidden_permissions():
    r = collect_architecture_agents()
    forbidden_values = {p.value for p in FORBIDDEN_AGENT_PERMISSIONS}
    for card_dict in r.catalog():
        for perm in card_dict["allowed_permissions"]:
            assert perm not in forbidden_values, (
                f"forbidden permission '{perm}' present in card "
                f"role={card_dict['role']}"
            )
        assert card_dict["direct_order_allowed"] is False
        assert card_dict["can_invoke_broker"] is False
        assert card_dict["can_invoke_order_gateway"] is False


def test_roles_present_equals_six():
    r = collect_architecture_agents()
    assert len(r.roles_present()) == 6


# ── 31-35. MOCA / 문서 ──────────────────────────────────────────


def test_each_card_lists_forbidden_actions():
    r = collect_architecture_agents()
    for agent in r.all():
        # 모든 카드는 최소한 execute_order / invoke_broker / invoke_order_gateway
        # 를 forbidden_actions 에 포함해야 한다 — UI/문서 표시 의무.
        fa = set(agent.card.forbidden_actions)
        assert "execute_order" in fa, f"{type(agent).__name__} 카드 누락: execute_order"
        assert "invoke_broker" in fa, f"{type(agent).__name__} 카드 누락: invoke_broker"
        assert "invoke_order_gateway" in fa, (
            f"{type(agent).__name__} 카드 누락: invoke_order_gateway"
        )


def test_doc_agent_architecture_md_exists():
    assert _DOC_PATH.exists(), f"missing doc: {_DOC_PATH}"


def test_doc_contains_six_role_descriptions():
    text = _DOC_PATH.read_text(encoding="utf-8")
    for role_name in (
        "Observer", "Analyst", "Risk Auditor",
        "Strategy Researcher", "Report Writer", "Execution Recommender",
    ):
        assert role_name in text, f"doc missing role: {role_name}"


def test_doc_states_direct_order_ban():
    text = _DOC_PATH.read_text(encoding="utf-8")
    assert "직접 주문" in text or "direct_order_allowed" in text
    assert "OrderGateway" in text  # 단일 주문 경로 도식


def test_claude_md_has_agent_direct_order_ban():
    text = _CLAUDE_MD.read_text(encoding="utf-8")
    # CLAUDE.md §2.3 — "AI Agent 는 분석·추천·설명만 한다. 직접 주문 금지."
    assert ("직접 주문 금지" in text) or ("direct order" in text.lower())


# ── 36-41. Static guards (base.py) ──────────────────────────────


def test_base_py_no_broker_or_execution_imports():
    pat = re.compile(
        r"^\s*(?:from\s+app\.(?:brokers|execution)|"
        r"import\s+app\.(?:brokers|execution))",
        re.M,
    )
    text = _BASE_PY.read_text(encoding="utf-8")
    assert not pat.search(text)


def test_base_py_no_order_gateway_or_adapter_imports():
    pat = re.compile(
        r"^\s*(?:from\s+app\.order_gateway|"
        r"import\s+app\.order_gateway|"
        r"from\s+app\.(?:adapters|broker))",
        re.M,
    )
    text = _BASE_PY.read_text(encoding="utf-8")
    assert not pat.search(text)


def test_base_py_no_network_sdk_imports():
    pat = re.compile(
        r"^\s*(?:import\s+(?:requests|httpx|ccxt|pyupbit|"
        r"binance|binance_connector|okx)|"
        r"from\s+(?:requests|httpx|ccxt|pyupbit|"
        r"binance|binance_connector|okx))",
        re.M,
    )
    text = _BASE_PY.read_text(encoding="utf-8")
    assert not pat.search(text)


def test_base_py_no_order_method_calls():
    pat = re.compile(
        r"\.(?:place_order|cancel_order|get_balance|submit_order|"
        r"withdraw|deposit|set_leverage|set_margin)\s*\(",
    )
    text = _BASE_PY.read_text(encoding="utf-8")
    assert not pat.search(text)


def test_base_py_no_forbidden_substrings():
    forbidden = (
        "ENABLE_LIVE_TRADING = True",
        "ENABLE_LIVE_TRADING=True",
        "ENABLE_AI_EXECUTION = True",
        "ENABLE_AI_EXECUTION=True",
        "ENABLE_CRYPTO_FUTURES_LIVE = True",
        "ENABLE_CRYPTO_FUTURES_LIVE=True",
        "is_executable: bool = True",
        "is_executable=True",
        "is_order_request: bool = True",
        "is_order_request=True",
        "direct_order_allowed=True",
        "direct_order_allowed: bool = True",
        "used_for_order=True",
        "used_for_order: bool = True",
    )
    text = _BASE_PY.read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in text, f"forbidden literal present: {needle}"


def test_base_py_no_executable_order_or_broker_payload_literals():
    """``executable_order`` / ``order_request`` / ``broker_payload`` 같은 output 키를
    base.py 의 production 코드 (docstring/주석 외) 에 노출하지 않는다.

    설명용 docstring/주석 노출은 허용. 따옴표로 감싼 dict 키/필드명 리터럴만 검사.
    """
    text = _BASE_PY.read_text(encoding="utf-8")
    # 따옴표로 감싼 키만 매치 (docstring 의 일반 단어 노출은 허용)
    for needle in (
        r'"executable_order"', r"'executable_order'",
        r'"order_request"', r"'order_request'",
        r'"broker_payload"', r"'broker_payload'",
        r'"place_order_payload"', r"'place_order_payload'",
    ):
        assert needle not in text, (
            f"forbidden output key literal {needle} in base.py"
        )


# ── 42. Backward compat ────────────────────────────────────────


def test_existing_agent_base_preserved():
    """기존 4-agent 시스템 (#37 1차) — AgentCapability / AgentBase Protocol /
    AgentRegistry / collect_default_agents 가 그대로 유지.
    """
    from app.agents.base import (
        AgentBase,  # Protocol
        AgentCapability,  # dataclass
        AgentRegistry,  # legacy registry
        collect_default_agents,
    )
    cap = AgentCapability(name="x", role="anomaly", description="d")
    assert cap.has_veto_power is False
    reg = collect_default_agents()
    assert len(reg.all()) == 4
    # legacy Protocol 충족 검사
    from app.agents.anomaly import AnomalyAgent
    assert isinstance(AnomalyAgent(), AgentBase)
