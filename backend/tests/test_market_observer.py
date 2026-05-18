"""체크리스트 #38 Market Observer — 회귀 테스트.

검증:
  Agent contract:
    1. MarketObserverAgent 는 StructuredAgentBase
    2. role = OBSERVER
    3. card.title / inputs / outputs / forbidden_actions 정상
    4. validate_safety() 통과 (FORBIDDEN 권한 부재)
    5. card 에 execute_order / invoke_broker / invoke_order_gateway 명시
    6. allowed_permissions 가 FORBIDDEN 카탈로그와 교집합 0
    7. AgentRegistry 에 등록 가능
  Output 영구 False 플래그:
    8. direct_order_allowed = False
    9. broker_call_allowed = False
   10. used_for_order = False
   11. MarketObserverOutput frozen
   12. AgentOutput.decision.is_executable = False
   13. AgentOutput.direct_order_allowed = False
  Breadth 요약:
   14. 정상 데이터 → tone 분류 + A/D ratio
   15. 빈 입력 → UNKNOWN tone
   16. 급락 다수 → RISK_OFF
   17. 급등 다수 → RISK_ON
   18. 혼재 → MIXED
  Volume flow:
   19. 정상 데이터 → total_volume / top_volume_symbols / surge_count
   20. 빈 입력 → 0
   21. surge_threshold 임계
  Top movers:
   22. 상위/하위 절댓값 정렬
   23. abs_change_threshold 필터
   24. 빈 입력 → 빈 tuple
  Sector flow:
   25. sector_map 우선
   26. theme_context fallback
   27. theme score 높을 때 *주문 신호 아님* note
   28. avg_change tone 분류
  Volatility:
   29. volatility_summary 우선 사용
   30. fallback dispersion 계산
   31. HIGH_VOLATILITY tone
   32. transition_risk 감지 (급락 + 고변동)
  Data health:
   33. freshness_ok mapping
   34. stale_symbols
   35. data_quality EXCLUDE count
  Observe end-to-end:
   36. has_data=True 시 모든 섹션 populate
   37. has_data=False 시 insufficient_data finding 1개
   38. notice/theme/kimp/funding context 그대로 노출
   39. JSON 직렬화 (Decimal/datetime → str)
   40. action token (BUY/SELL/ENTER/EXIT) 누설 없음
  MOCA / 문서:
   41. card.to_dict() 평탄 dict, FORBIDDEN 권한 없음
   42. docs/market_observer.md 존재
   43. docs 에 6개 관찰 영역 설명
   44. docs 에 direct_order_allowed=False 명시
   45. docs 에 broker_call_allowed=False 명시
  Static guards:
   46. broker / execution import 부재
   47. order_gateway / adapter import 부재
   48. network SDK import 부재
   49. order method 호출 부재
   50. forbidden literal 부재
   51. "BUY"/"SELL"/"ENTER"/"EXIT" quoted 리터럴 부재
   52. forbidden output key literal 부재 ("executable_order" 등)
  Backward compat:
   53. 기존 6-role registry 도 MarketObserver 추가 등록 가능
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.agents import market_observer as mo_module
from app.agents.base import (
    FORBIDDEN_AGENT_PERMISSIONS,
    AgentArchitectureRole,
    AgentInput,
    StructuredAgentBase,
    StructuredAgentRegistry,
)
from app.agents.market_observer import (
    MarketBreadthSnapshot,
    MarketObserverAgent,
    MarketObserverOutput,
    RiskTone,
    SectorTone,
    VolatilityTone,
    detect_top_movers,
    summarize_data_health,
    summarize_market_breadth,
    summarize_sector_flow,
    summarize_volatility_regime,
    summarize_volume_flow,
)


_TARGET = Path(mo_module.__file__)
_DOC_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs" / "market_observer.md"
)


# ── helpers ──────────────────────────────────────────────────────


def _market_ctx(*tickers, **extra) -> dict:
    ctx = {"tickers": list(tickers)}
    ctx.update(extra)
    return ctx


def _ticker(symbol="BTC", change=0.0, volume=100.0, avg_volume=50.0):
    return {
        "symbol": symbol, "change_pct": change,
        "volume": volume, "avg_volume": avg_volume,
    }


# ── 1-7. Agent contract ─────────────────────────────────────────


def test_agent_is_structured_agent_base():
    agent = MarketObserverAgent()
    assert isinstance(agent, StructuredAgentBase)


def test_agent_role_is_observer():
    assert MarketObserverAgent.role == AgentArchitectureRole.OBSERVER


def test_card_metadata_shape():
    card = MarketObserverAgent.card
    assert card.title == "Market Observer Agent"
    assert "market_context" in card.inputs
    assert "market_breadth" in card.outputs
    assert "execute_order" in card.forbidden_actions
    assert "invoke_broker" in card.forbidden_actions
    assert "invoke_order_gateway" in card.forbidden_actions
    assert "fetch_external_api" in card.forbidden_actions
    assert "collect_market_data" in card.forbidden_actions


def test_validate_safety_passes():
    MarketObserverAgent().validate_safety()


def test_card_forbidden_actions_include_required_three():
    fa = set(MarketObserverAgent.card.forbidden_actions)
    assert fa >= {"execute_order", "invoke_broker", "invoke_order_gateway"}


def test_card_allowed_permissions_no_forbidden_overlap():
    perms = MarketObserverAgent.card.allowed_permissions
    assert not (perms & FORBIDDEN_AGENT_PERMISSIONS)


def test_can_register_into_structured_registry():
    reg = StructuredAgentRegistry()
    reg.register(MarketObserverAgent())
    assert reg.by_role(AgentArchitectureRole.OBSERVER)


# ── 8-13. Output 영구 False ─────────────────────────────────────


def test_output_direct_order_allowed_false():
    out = MarketObserverAgent().observe(AgentInput(role="OBSERVER", task="x"))
    assert out.direct_order_allowed is False


def test_output_broker_call_allowed_false():
    out = MarketObserverAgent().observe(AgentInput(role="OBSERVER", task="x"))
    assert out.broker_call_allowed is False


def test_output_used_for_order_false():
    out = MarketObserverAgent().observe(AgentInput(role="OBSERVER", task="x"))
    assert out.used_for_order is False


def test_market_observer_output_is_frozen():
    out = MarketObserverAgent().observe(AgentInput(role="OBSERVER", task="x"))
    with pytest.raises(Exception):
        out.direct_order_allowed = True  # type: ignore[misc]
    with pytest.raises(Exception):
        out.broker_call_allowed = True  # type: ignore[misc]


def test_agent_evaluate_returns_agent_output_with_safe_flags():
    agent_output = MarketObserverAgent().evaluate(
        AgentInput(role="OBSERVER", task="x"),
    )
    assert agent_output.direct_order_allowed is False
    assert agent_output.decision.is_executable is False
    assert agent_output.used_for_order is False


def test_evaluate_findings_match_observe():
    inp = AgentInput(role="OBSERVER", task="x", payload={
        "market_context": _market_ctx(
            _ticker("BTC", change=2.0),
            _ticker("ETH", change=-1.0),
        ),
    })
    a = MarketObserverAgent()
    out = a.observe(inp)
    ev = a.evaluate(inp)
    # decision.findings 가 observe 의 findings 와 동일
    assert ev.decision.findings == out.findings


# ── 14-18. Breadth ──────────────────────────────────────────────


def test_breadth_normal_data_yields_tone_and_ratio():
    ctx = _market_ctx(
        _ticker("A", change=2.0), _ticker("B", change=1.0),
        _ticker("C", change=-0.5), _ticker("D", change=3.0),
    )
    b = summarize_market_breadth(ctx)
    assert b.total_symbols == 4
    assert b.advancing_count == 3
    assert b.declining_count == 1
    assert b.advance_decline_ratio == 3.0
    assert b.risk_tone == RiskTone.RISK_ON


def test_breadth_empty_input_unknown_tone():
    b = summarize_market_breadth(None)
    assert b.total_symbols == 0
    assert b.risk_tone == RiskTone.UNKNOWN


def test_breadth_majority_declining_risk_off():
    ctx = _market_ctx(
        _ticker("A", change=-3.0), _ticker("B", change=-1.0),
        _ticker("C", change=-2.0), _ticker("D", change=0.5),
    )
    b = summarize_market_breadth(ctx)
    assert b.risk_tone == RiskTone.RISK_OFF


def test_breadth_balanced_mixed_tone():
    ctx = _market_ctx(
        _ticker("A", change=1.0), _ticker("B", change=-1.0),
        _ticker("C", change=0.5), _ticker("D", change=-0.5),
    )
    b = summarize_market_breadth(ctx)
    assert b.risk_tone == RiskTone.MIXED


def test_breadth_no_change_pct_unchanged():
    ctx = _market_ctx({"symbol": "A"}, {"symbol": "B"})
    b = summarize_market_breadth(ctx)
    assert b.unchanged_count == 2
    assert b.advancing_count == 0


# ── 19-21. Volume flow ──────────────────────────────────────────


def test_volume_flow_normal_yields_totals_and_surges():
    ctx = _market_ctx(
        _ticker("A", volume=200, avg_volume=50),   # surge 4x
        _ticker("B", volume=80, avg_volume=80),
        _ticker("C", volume=300, avg_volume=100),  # surge 3x
    )
    v = summarize_volume_flow(ctx)
    assert v.total_volume == 580
    assert v.surge_count == 2
    assert v.top_volume_symbols[0] == "C"


def test_volume_flow_empty_zero():
    v = summarize_volume_flow(None)
    assert v.total_volume == 0.0
    assert v.surge_count == 0


def test_volume_flow_surge_threshold_configurable():
    ctx = _market_ctx(_ticker("A", volume=100, avg_volume=80))  # 1.25x
    v_strict = summarize_volume_flow(ctx, surge_threshold_ratio=2.0)
    v_loose = summarize_volume_flow(ctx, surge_threshold_ratio=1.2)
    assert v_strict.surge_count == 0
    assert v_loose.surge_count == 1


# ── 22-24. Top movers ───────────────────────────────────────────


def test_top_movers_sorted_by_abs_change():
    ctx = _market_ctx(
        _ticker("A", change=1.0), _ticker("B", change=-5.0),
        _ticker("C", change=3.0), _ticker("D", change=-1.0),
    )
    movers = detect_top_movers(ctx, top_n=3)
    assert [m.symbol for m in movers] == ["B", "C", "A"]
    assert movers[0].direction == "DOWN"


def test_top_movers_threshold_filters():
    ctx = _market_ctx(_ticker("A", change=0.3), _ticker("B", change=5.0))
    movers = detect_top_movers(ctx, abs_change_threshold_pct=1.0)
    assert [m.symbol for m in movers] == ["B"]


def test_top_movers_empty_input():
    assert detect_top_movers(None) == ()


# ── 25-28. Sector flow ──────────────────────────────────────────


def test_sector_flow_uses_sector_map_when_available():
    ctx = _market_ctx(
        _ticker("A", change=2.0), _ticker("B", change=1.5),
        _ticker("C", change=-1.0),
    )
    ctx["sector_map"] = {"L1": ["A", "B"], "Meme": ["C"]}
    flows = summarize_sector_flow(ctx)
    by_sector = {f.sector: f for f in flows}
    assert by_sector["L1"].tone == SectorTone.STRONG
    assert by_sector["Meme"].tone == SectorTone.WEAK


def test_sector_flow_uses_theme_context_fallback():
    ctx = _market_ctx(_ticker("BTC", change=2.5))
    theme = {"themes": [{"name": "L1", "related_symbols": ["BTC"], "score": 0.5}]}
    flows = summarize_sector_flow(ctx, theme)
    assert flows
    assert flows[0].sector == "L1"


def test_sector_flow_high_theme_score_notes_not_an_order_signal():
    ctx = _market_ctx(_ticker("BTC", change=0.0))
    theme = {"themes": [{"name": "L1", "related_symbols": ["BTC"], "score": 0.95}]}
    flows = summarize_sector_flow(ctx, theme)
    assert flows
    notes = flows[0].notes
    assert any("not an order signal" in n for n in notes)


def test_sector_flow_tone_classification():
    ctx = _market_ctx(_ticker("A", change=0.0))
    ctx["sector_map"] = {"Flat": ["A"]}
    flows = summarize_sector_flow(ctx)
    assert flows[0].tone == SectorTone.MIXED


# ── 29-32. Volatility ───────────────────────────────────────────


def test_volatility_uses_summary_when_available():
    ctx = {"volatility_summary": {
        "avg_volatility": 4.2,
        "volatility_tone": VolatilityTone.HIGH_VOLATILITY,
        "high_volatility_symbols": ["BTC", "ETH"],
        "transition_risk": True,
    }}
    v = summarize_volatility_regime(ctx)
    assert v.avg_volatility == 4.2
    assert v.volatility_tone == VolatilityTone.HIGH_VOLATILITY
    assert v.transition_risk is True


def test_volatility_fallback_dispersion():
    ctx = _market_ctx(
        _ticker("A", change=0.1), _ticker("B", change=-0.2),
        _ticker("C", change=0.3),
    )
    v = summarize_volatility_regime(ctx)
    assert v.volatility_tone == VolatilityTone.LOW_VOLATILITY


def test_volatility_high_when_avg_abs_change_high():
    ctx = _market_ctx(
        _ticker("A", change=5.0), _ticker("B", change=-4.5),
        _ticker("C", change=6.0),
    )
    v = summarize_volatility_regime(ctx)
    assert v.volatility_tone == VolatilityTone.HIGH_VOLATILITY


def test_volatility_transition_risk_when_sharp_drops():
    ctx = _market_ctx(
        _ticker("A", change=-5.0), _ticker("B", change=-4.0),
        _ticker("C", change=-6.0), _ticker("D", change=1.0),
    )
    v = summarize_volatility_regime(ctx)
    assert v.volatility_tone == VolatilityTone.HIGH_VOLATILITY
    assert v.transition_risk is True


# ── 33-35. Data health ──────────────────────────────────────────


def test_data_health_freshness_mapping():
    ctx = {"freshness_state": {"ok": False, "stale_symbols": ["BTC"]}}
    d = summarize_data_health(ctx)
    assert d.freshness_ok is False
    assert d.stale_symbols == ("BTC",)


def test_data_health_freshness_bool_input():
    ctx = {"freshness_state": True}
    d = summarize_data_health(ctx)
    assert d.freshness_ok is True
    assert d.stale_symbols == ()


def test_data_health_quality_exclude_count():
    ctx = {
        "data_quality_summary": {"grade": "EXCLUDE", "exclude_count": 3},
    }
    d = summarize_data_health(ctx)
    assert d.data_quality_grade == "EXCLUDE"
    assert d.quality_excluded_count == 3


# ── 36-40. Observe end-to-end ───────────────────────────────────


def test_observe_full_populated():
    inp = AgentInput(role="OBSERVER", task="x", payload={
        "market_context": _market_ctx(
            _ticker("BTC", change=2.0, volume=100),
            _ticker("ETH", change=-1.5, volume=80),
            _ticker("SOL", change=4.0, volume=200),
            sector_map={"L1": ["BTC", "ETH", "SOL"]},
        ),
        "theme_context": {"themes": [
            {"name": "L1", "score": 0.5, "related_symbols": ["BTC"]},
        ]},
        "notice_context": {
            "total_notices": 2, "high_risk_symbols": ["XRP"],
            "candidate_filter_flags": ["caution_notice"],
            "human_summary": "ok",
        },
        "kimp_context": {"premium_bps": "30"},
        "funding_context": {"rate_pct": "0.01"},
    })
    out = MarketObserverAgent().observe(inp)
    assert out.has_data is True
    assert out.market_breadth is not None
    assert out.volume_flow is not None
    assert len(out.top_movers) > 0
    assert len(out.sector_flows) > 0
    assert out.volatility_regime is not None
    assert out.data_health is not None
    assert out.notice_observation is not None
    assert out.notice_observation["total_notices"] == 2
    assert out.theme_observation is not None
    assert out.kimp_context == {"premium_bps": "30"}
    assert out.funding_context == {"rate_pct": "0.01"}


def test_observe_insufficient_data_safe_path():
    out = MarketObserverAgent().observe(
        AgentInput(role="OBSERVER", task="x", payload={}),
    )
    assert out.has_data is False
    assert any(f.kind == "insufficient_data" for f in out.findings)
    assert "insufficient_data" in out.summary


def test_observe_context_blocks_passthrough_when_no_market_data():
    out = MarketObserverAgent().observe(AgentInput(
        role="OBSERVER", task="x",
        payload={"kimp_context": {"premium_bps": "10"}},
    ))
    # kimp_context 만 있으면 has_data=False (tickers/theme/notice 모두 없음)
    assert out.has_data is False
    assert out.kimp_context == {"premium_bps": "10"}


def test_output_json_serializable():
    out = MarketObserverAgent().observe(AgentInput(
        role="OBSERVER", task="x", payload={
            "market_context": _market_ctx(_ticker("BTC", change=1.0)),
        },
    ))
    text = out.to_json()
    parsed = json.loads(text)
    assert parsed["direct_order_allowed"] is False
    assert parsed["broker_call_allowed"] is False
    assert "market_breadth" in parsed


def test_output_no_action_token_leak():
    out = MarketObserverAgent().observe(AgentInput(
        role="OBSERVER", task="x", payload={
            "market_context": _market_ctx(
                _ticker("BTC", change=2.0), _ticker("ETH", change=-1.0),
            ),
        },
    ))
    text = out.to_json()
    for tok in ("BUY", "SELL", "ENTER", "EXIT"):
        assert not re.search(rf"\b{tok}\b", text), (
            f"{tok} leaked into MarketObserverOutput: {text}"
        )


# ── 41-45. MOCA / 문서 ──────────────────────────────────────────


def test_card_to_dict_no_forbidden():
    d = MarketObserverAgent.card.to_dict()
    assert d["direct_order_allowed"] is False
    assert d["can_invoke_broker"] is False
    assert d["can_invoke_order_gateway"] is False
    forbidden_values = {p.value for p in FORBIDDEN_AGENT_PERMISSIONS}
    for perm in d["allowed_permissions"]:
        assert perm not in forbidden_values


def test_doc_exists():
    assert _DOC_PATH.exists(), f"missing doc: {_DOC_PATH}"


def test_doc_lists_six_observation_areas():
    text = _DOC_PATH.read_text(encoding="utf-8")
    for label in (
        "market breadth", "거래대금", "급등락", "섹터", "변동성",
        "freshness",
    ):
        assert label in text, f"doc missing area: {label}"


def test_doc_states_direct_order_allowed_false():
    text = _DOC_PATH.read_text(encoding="utf-8")
    assert "direct_order_allowed" in text


def test_doc_states_broker_call_allowed_false():
    text = _DOC_PATH.read_text(encoding="utf-8")
    assert "broker_call_allowed" in text


# ── 46-52. Static guards (market_observer.py) ──────────────────


def test_module_no_broker_or_execution_imports():
    pat = re.compile(
        r"^\s*(?:from\s+app\.(?:brokers|execution)|"
        r"import\s+app\.(?:brokers|execution))",
        re.M,
    )
    text = _TARGET.read_text(encoding="utf-8")
    assert not pat.search(text)


def test_module_no_order_gateway_or_adapter_imports():
    pat = re.compile(
        r"^\s*(?:from\s+app\.order_gateway|"
        r"import\s+app\.order_gateway|"
        r"from\s+app\.(?:adapters|broker))",
        re.M,
    )
    text = _TARGET.read_text(encoding="utf-8")
    assert not pat.search(text)


def test_module_no_network_sdk_imports():
    pat = re.compile(
        r"^\s*(?:import\s+(?:requests|httpx|ccxt|pyupbit|"
        r"binance|binance_connector|okx)|"
        r"from\s+(?:requests|httpx|ccxt|pyupbit|"
        r"binance|binance_connector|okx))",
        re.M,
    )
    text = _TARGET.read_text(encoding="utf-8")
    assert not pat.search(text)


def test_module_no_order_method_calls():
    pat = re.compile(
        r"\.(?:place_order|cancel_order|get_balance|submit_order|"
        r"withdraw|deposit|set_leverage|set_margin)\s*\(",
    )
    text = _TARGET.read_text(encoding="utf-8")
    assert not pat.search(text)


def test_module_no_forbidden_substrings():
    forbidden = (
        "ENABLE_LIVE_TRADING = True",
        "ENABLE_LIVE_TRADING=True",
        "ENABLE_AI_EXECUTION = True",
        "ENABLE_AI_EXECUTION=True",
        "ENABLE_CRYPTO_FUTURES_LIVE = True",
        "ENABLE_CRYPTO_FUTURES_LIVE=True",
        "direct_order_allowed=True",
        "direct_order_allowed: bool = True",
        "broker_call_allowed=True",
        "broker_call_allowed: bool = True",
        "used_for_order=True",
        "used_for_order: bool = True",
        "is_executable=True",
        "is_order_request=True",
    )
    text = _TARGET.read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in text, f"forbidden literal present: {needle}"


def test_module_no_quoted_action_tokens():
    """BUY/SELL/ENTER/EXIT 따옴표 리터럴이 production 모듈에 없어야 한다.
    docstring/주석 내 일반 단어 노출은 허용 — 카탈로그/금지 설명에 등장 가능.
    """
    text = _TARGET.read_text(encoding="utf-8")
    for needle in (
        r'"BUY"', r"'BUY'", r'"SELL"', r"'SELL'",
        r'"ENTER"', r"'ENTER'", r'"EXIT"', r"'EXIT'",
    ):
        assert needle not in text, (
            f"forbidden action literal {needle} in market_observer.py"
        )


def test_module_no_forbidden_output_key_literals():
    text = _TARGET.read_text(encoding="utf-8")
    for needle in (
        r'"executable_order"', r"'executable_order'",
        r'"order_request"', r"'order_request'",
        r'"broker_payload"', r"'broker_payload'",
        r'"place_order_payload"', r"'place_order_payload'",
    ):
        assert needle not in text, (
            f"forbidden output key literal {needle} in market_observer.py"
        )


# ── 53. Backward compat ────────────────────────────────────────


def test_existing_six_role_registry_can_add_market_observer():
    """기존 6-role registry (#37) 도 추가 MarketObserver 등록 가능."""
    from app.agents.base import collect_architecture_agents
    reg = collect_architecture_agents()
    reg.register(MarketObserverAgent())
    observers = reg.by_role(AgentArchitectureRole.OBSERVER)
    # 기존 ObserverAgent + MarketObserverAgent 합계 2개
    assert len(observers) == 2
