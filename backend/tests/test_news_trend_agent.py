"""체크리스트 #39 News / Trend Agent — 회귀 테스트.

검증:
  Agent contract:
    1. role = STRATEGY_RESEARCHER
    2. card.title / inputs / outputs / forbidden_actions
    3. validate_safety() 통과
    4. allowed_permissions FORBIDDEN 교집합 0
    5. card 에 execute_order / invoke_broker / invoke_order_gateway / fetch_external_*_api
    6. AgentRegistry 등록 가능 (#37 6-role layer)
  Output 영구 False:
    7. direct_order_allowed = False
    8. broker_call_allowed = False
    9. used_for_order = False
   10. ThemeCandidate.used_for_order = False
   11. AgentOutput.decision.is_executable = False
   12. NewsTrendAgentOutput frozen — mutation 차단
   13. ThemeCandidate frozen
  Keyword trends:
   14. 빈 input → 빈 tuple
   15. growth_pct 계산
   16. previous=0 신규 키워드 → growth_pct=None + UNKNOWN direction
   17. min_keyword_growth_pct 필터
   18. top_keywords_limit 적용
   19. related_symbols upper 처리
   20. surging / growing / declining 분류
  News volume:
   21. 정상 입력 → growth_pct + direction
   22. previous=0 → None
   23. SURGING / DECLINING 분류
   24. 빈 입력 → None
  Disclosures:
   25. payload.disclosures 우선
   26. notice_context fallback
   27. 빈 입력 → 빈 tuple
   28. symbol upper 처리
  Theme candidates:
   29. score 0~1 → 0~100 환산
   30. score 0~100 그대로 사용
   31. 동일 theme 다중 signal 누적 + cap=100
   32. HYPE 임계 (>= 90)
   33. HIGH_ATTENTION 임계 (>= 80)
   34. NORMAL 그 외
   35. attention 높음 → "elevated attention" note
   36. negative sentiment → "negative sentiment" note
   37. sentiment 평균 정확
   38. top_themes_limit 적용
   39. provider 호환 키 (provider/source) sources 에 포함
  Risk notes:
   40. HYPE → hype_risk HIGH
   41. HIGH_ATTENTION → high_attention WARNING
   42. negative sentiment → WARNING
  Analyze end-to-end:
   43. has_data=True → 모든 섹션 populate
   44. insufficient_data 안전 경로 (모든 input 빈)
   45. JSON 직렬화
   46. action token (BUY/SELL/ENTER/EXIT) 누설 없음
  MOCA / 문서:
   47. card.to_dict() 평탄 dict
   48. docs/news_trend_agent.md 존재
   49. docs 에 5개 주제 영역 설명
   50. docs 에 direct_order_allowed=false 명시
   51. docs 에 broker_call_allowed=false 명시
   52. docs 에 외부 뉴스/트렌드 API 직접 호출 금지 명시
  Static guards:
   53. broker / execution import 부재
   54. order_gateway / adapter import 부재
   55. network SDK import 부재
   56. order method 호출 부재
   57. forbidden literal 부재
   58. "BUY"/"SELL"/"ENTER"/"EXIT" quoted 리터럴 부재
   59. forbidden output key literal 부재
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.agents import news_trend_agent as nta_module
from app.agents.base import (
    FORBIDDEN_AGENT_PERMISSIONS,
    AgentArchitectureRole,
    AgentInput,
    StructuredAgentBase,
    StructuredAgentRegistry,
    collect_architecture_agents,
)
from app.agents.news_trend_agent import (
    DisclosureEventSummary,
    KeywordTrendSummary,
    NewsTrendAgent,
    NewsTrendAgentConfig,
    NewsTrendAgentOutput,
    NewsVolumeSummary,
    RiskLevel,
    ThemeCandidate,
    ThemeRiskNote,
    TrendDirection,
    compute_theme_risk_notes,
    derive_theme_candidates,
    summarize_disclosures,
    summarize_keyword_trends,
    summarize_news_volume,
)


_TARGET = Path(nta_module.__file__)
_DOC_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs" / "news_trend_agent.md"
)


# ── 1-6. Agent contract ─────────────────────────────────────────


def test_role_is_strategy_researcher():
    assert NewsTrendAgent.role == AgentArchitectureRole.STRATEGY_RESEARCHER


def test_card_metadata_shape():
    card = NewsTrendAgent.card
    assert card.title == "News / Trend Agent"
    assert "keywords" in card.inputs
    assert "theme_candidates" in card.outputs
    for needle in (
        "execute_order", "invoke_broker", "invoke_order_gateway",
        "fetch_external_news_api", "fetch_external_trend_api",
    ):
        assert needle in card.forbidden_actions


def test_validate_safety_passes():
    NewsTrendAgent().validate_safety()


def test_allowed_permissions_no_forbidden_overlap():
    perms = NewsTrendAgent.card.allowed_permissions
    assert not (perms & FORBIDDEN_AGENT_PERMISSIONS)


def test_can_register_into_structured_registry():
    reg = StructuredAgentRegistry()
    reg.register(NewsTrendAgent())
    assert reg.by_role(AgentArchitectureRole.STRATEGY_RESEARCHER)


def test_default_six_role_registry_can_add_news_trend_agent():
    reg = collect_architecture_agents()
    reg.register(NewsTrendAgent())
    researchers = reg.by_role(AgentArchitectureRole.STRATEGY_RESEARCHER)
    assert len(researchers) == 2  # 기존 StrategyResearcherAgent + NewsTrendAgent


# ── 7-13. Output 영구 False ─────────────────────────────────────


def test_output_direct_order_allowed_false():
    out = NewsTrendAgent().analyze(AgentInput(role="x", task="x"))
    assert out.direct_order_allowed is False


def test_output_broker_call_allowed_false():
    out = NewsTrendAgent().analyze(AgentInput(role="x", task="x"))
    assert out.broker_call_allowed is False


def test_output_used_for_order_false():
    out = NewsTrendAgent().analyze(AgentInput(role="x", task="x"))
    assert out.used_for_order is False


def test_theme_candidate_used_for_order_false():
    out = NewsTrendAgent().analyze(AgentInput(
        role="x", task="x", payload={"theme_signals": [
            {"theme": "T", "score": 0.5},
        ]},
    ))
    for t in out.theme_candidates:
        assert t.used_for_order is False


def test_evaluate_returns_agent_output_with_safe_flags():
    out = NewsTrendAgent().evaluate(AgentInput(role="x", task="x"))
    assert out.direct_order_allowed is False
    assert out.decision.is_executable is False


def test_output_frozen():
    out = NewsTrendAgent().analyze(AgentInput(role="x", task="x"))
    with pytest.raises(Exception):
        out.direct_order_allowed = True  # type: ignore[misc]
    with pytest.raises(Exception):
        out.broker_call_allowed = True  # type: ignore[misc]


def test_theme_candidate_frozen():
    t = ThemeCandidate(
        theme="x", related_symbols=(), related_keywords=(),
        attention_score=10.0, sentiment_avg=None,
        risk_level=RiskLevel.NORMAL, sources=(),
    )
    with pytest.raises(Exception):
        t.used_for_order = True  # type: ignore[misc]


# ── 14-20. Keyword trends ──────────────────────────────────────


def test_keyword_trends_empty_input_returns_empty():
    assert summarize_keyword_trends(None) == ()
    assert summarize_keyword_trends({}) == ()


def test_keyword_growth_pct_basic():
    out = summarize_keyword_trends({"keywords": [
        {"keyword": "ETF", "current_count": 300, "previous_count": 100},
    ]})
    assert len(out) == 1
    assert out[0].growth_pct == 200.0


def test_keyword_previous_zero_yields_none_growth():
    out = summarize_keyword_trends({"keywords": [
        {"keyword": "New", "current_count": 100, "previous_count": 0},
    ]})
    assert out[0].growth_pct is None
    assert out[0].direction == TrendDirection.UNKNOWN


def test_min_keyword_growth_pct_filters():
    out = summarize_keyword_trends({"keywords": [
        {"keyword": "Flat", "current_count": 11, "previous_count": 10},  # 10%
        {"keyword": "Spike", "current_count": 200, "previous_count": 100},  # 100%
    ]}, NewsTrendAgentConfig(min_keyword_growth_pct=50.0))
    keys = [k.keyword for k in out]
    assert "Flat" not in keys
    assert "Spike" in keys


def test_top_keywords_limit_applied():
    payload = {"keywords": [
        {"keyword": f"k{i}", "current_count": 100 + i, "previous_count": 1}
        for i in range(30)
    ]}
    out = summarize_keyword_trends(
        payload, NewsTrendAgentConfig(top_keywords_limit=5),
    )
    assert len(out) == 5


def test_related_symbols_uppercased():
    out = summarize_keyword_trends({"keywords": [
        {"keyword": "ETF", "current_count": 300, "previous_count": 100,
         "related_symbols": ["btc", "eth"]},
    ]})
    assert out[0].related_symbols == ("BTC", "ETH")


def test_keyword_direction_classification():
    cfg = NewsTrendAgentConfig(
        surging_growth_pct=200.0,
        min_keyword_growth_pct=50.0,
        declining_growth_pct=-30.0,
    )
    out = summarize_keyword_trends({"keywords": [
        {"keyword": "Surge", "current_count": 400, "previous_count": 100},   # 300%
        {"keyword": "Grow", "current_count": 200, "previous_count": 100},   # 100%
        {"keyword": "Drop", "current_count": 50, "previous_count": 100},   # -50%
    ]}, cfg)
    by_kw = {k.keyword: k.direction for k in out}
    # min_growth filter excludes "Drop" (negative). 별도로 회복 위해 test:
    drop_only = summarize_keyword_trends({"keywords": [
        {"keyword": "Drop", "current_count": 50, "previous_count": 100},
    ]}, NewsTrendAgentConfig(min_keyword_growth_pct=-100.0))  # don't filter
    assert drop_only[0].direction == TrendDirection.DECLINING
    assert by_kw["Surge"] == TrendDirection.SURGING
    assert by_kw["Grow"] == TrendDirection.GROWING


# ── 21-24. News volume ─────────────────────────────────────────


def test_news_volume_normal():
    n = summarize_news_volume({"news_volume": {
        "current": 600, "previous": 200, "window_hours": 24,
    }})
    assert n is not None
    assert n.growth_pct == 200.0
    assert n.direction == TrendDirection.SURGING


def test_news_volume_previous_zero_growth_none():
    n = summarize_news_volume({"news_volume": {
        "current": 100, "previous": 0,
    }})
    assert n is not None
    assert n.growth_pct is None


def test_news_volume_declining_direction():
    n = summarize_news_volume({"news_volume": {"current": 10, "previous": 100}})
    assert n.direction == TrendDirection.DECLINING


def test_news_volume_empty_returns_none():
    assert summarize_news_volume(None) is None
    assert summarize_news_volume({}) is None


# ── 25-28. Disclosures ─────────────────────────────────────────


def test_disclosures_uses_disclosures_key():
    out = summarize_disclosures({"disclosures": [
        {"exchange": "upbit", "symbol": "btc", "notice_type": "CAUTION",
         "severity": "HIGH", "title": "유의종목"},
    ]})
    assert len(out) == 1
    assert out[0].symbol == "BTC"
    assert out[0].severity == "HIGH"


def test_disclosures_notice_context_fallback():
    out = summarize_disclosures({"notice_context": {
        "symbol_summaries": [
            {"symbol": "XRP", "exchange": "upbit", "severity": "HIGH",
             "risk_flags": ["caution"]},
        ],
    }})
    assert len(out) == 1
    assert out[0].symbol == "XRP"


def test_disclosures_empty_input():
    assert summarize_disclosures(None) == ()
    assert summarize_disclosures({}) == ()


def test_disclosures_symbol_uppercased():
    out = summarize_disclosures({"disclosures": [
        {"exchange": "okx", "symbol": "doge", "notice_type": "DELISTING",
         "severity": "CRITICAL", "title": "delist"},
    ]})
    assert out[0].symbol == "DOGE"


# ── 29-39. Theme candidates ────────────────────────────────────


def test_theme_score_0_to_1_scaled_to_100():
    out = derive_theme_candidates({"theme_signals": [
        {"theme": "ETF", "score": 0.95, "related_symbols": ["BTC"]},
    ]})
    assert out[0].attention_score == 95.0


def test_theme_score_0_to_100_passthrough():
    out = derive_theme_candidates({"theme_signals": [
        {"theme": "Hype", "score": 85.0, "related_symbols": ["MEME"]},
    ]})
    assert out[0].attention_score == 85.0


def test_theme_multi_signal_accumulates_with_cap():
    out = derive_theme_candidates({"theme_signals": [
        {"theme": "ETF", "score": 0.8},
        {"theme": "ETF", "score": 0.8},
        {"theme": "ETF", "score": 0.8},
        {"theme": "ETF", "score": 0.8},
    ]})
    # cap=100
    assert out[0].attention_score <= 100.0


def test_theme_hype_risk_level():
    out = derive_theme_candidates({"theme_signals": [
        {"theme": "ETF", "score": 0.95},
    ]})
    assert out[0].risk_level == RiskLevel.HYPE


def test_theme_high_attention_level():
    out = derive_theme_candidates({"theme_signals": [
        {"theme": "ETF", "score": 0.85},
    ]})
    assert out[0].risk_level == RiskLevel.HIGH_ATTENTION


def test_theme_normal_level():
    out = derive_theme_candidates({"theme_signals": [
        {"theme": "Small", "score": 0.3},
    ]})
    assert out[0].risk_level == RiskLevel.NORMAL


def test_theme_high_attention_adds_observe_only_note():
    out = derive_theme_candidates({"theme_signals": [
        {"theme": "ETF", "score": 0.95},
    ]})
    notes = " ".join(out[0].notes)
    assert "not an order signal" in notes


def test_theme_negative_sentiment_note():
    out = derive_theme_candidates({"theme_signals": [
        {"theme": "Bad", "score": 0.3, "sentiment": -0.7},
    ]})
    notes = " ".join(out[0].notes)
    assert "negative sentiment" in notes


def test_theme_sentiment_average_correct():
    out = derive_theme_candidates({"theme_signals": [
        {"theme": "X", "score": 0.5, "sentiment": 0.4},
        {"theme": "X", "score": 0.5, "sentiment": 0.6},
    ]})
    assert out[0].sentiment_avg == 0.5


def test_top_themes_limit_applied():
    signals = [
        {"theme": f"t{i}", "score": 50 + i} for i in range(15)
    ]
    out = derive_theme_candidates(
        {"theme_signals": signals},
        NewsTrendAgentConfig(top_themes_limit=3),
    )
    assert len(out) == 3


def test_theme_provider_field_added_to_sources():
    out = derive_theme_candidates({"theme_signals": [
        {"theme": "X", "score": 0.3, "provider": "twitter"},
    ]})
    assert "twitter" in out[0].sources


# ── 40-42. Risk notes ──────────────────────────────────────────


def test_compute_risk_notes_hype():
    cands = derive_theme_candidates({"theme_signals": [
        {"theme": "ETF", "score": 0.95},
    ]})
    notes = compute_theme_risk_notes(cands)
    by_code = {n.code: n for n in notes}
    assert "hype_risk" in by_code
    assert by_code["hype_risk"].severity == "HIGH"


def test_compute_risk_notes_high_attention():
    cands = derive_theme_candidates({"theme_signals": [
        {"theme": "ETF", "score": 0.85},
    ]})
    notes = compute_theme_risk_notes(cands)
    by_code = {n.code: n for n in notes}
    assert "high_attention" in by_code
    assert by_code["high_attention"].severity == "WARNING"


def test_compute_risk_notes_negative_sentiment():
    cands = derive_theme_candidates({"theme_signals": [
        {"theme": "Y", "score": 0.3, "sentiment": -0.7},
    ]})
    notes = compute_theme_risk_notes(cands)
    by_code = {n.code: n for n in notes}
    assert "negative_sentiment" in by_code


# ── 43-46. Analyze end-to-end ─────────────────────────────────


def test_analyze_full_populated():
    inp = AgentInput(role="x", task="x", payload={
        "keywords": [
            {"keyword": "ETF", "current_count": 300, "previous_count": 100},
        ],
        "news_volume": {"current": 500, "previous": 200, "window_hours": 24},
        "disclosures": [
            {"exchange": "upbit", "symbol": "XRP", "notice_type": "CAUTION",
             "severity": "HIGH", "title": "유의종목"},
        ],
        "theme_signals": [
            {"theme": "ETF", "score": 0.95, "related_symbols": ["BTC"]},
        ],
    })
    out = NewsTrendAgent().analyze(inp)
    assert out.has_data is True
    assert len(out.keyword_trends) >= 1
    assert out.news_volume is not None
    assert len(out.disclosures) >= 1
    assert len(out.theme_candidates) >= 1
    assert len(out.risk_notes) >= 1


def test_analyze_insufficient_data_safe_path():
    out = NewsTrendAgent().analyze(AgentInput(role="x", task="x"))
    assert out.has_data is False
    assert any(f.kind == "insufficient_data" for f in out.findings)
    assert "insufficient_data" in out.summary


def test_analyze_json_serializable():
    out = NewsTrendAgent().analyze(AgentInput(role="x", task="x", payload={
        "theme_signals": [{"theme": "X", "score": 0.5}],
    }))
    parsed = json.loads(out.to_json())
    assert parsed["direct_order_allowed"] is False
    assert parsed["broker_call_allowed"] is False
    assert "theme_candidates" in parsed


def test_analyze_no_action_token_leak():
    out = NewsTrendAgent().analyze(AgentInput(role="x", task="x", payload={
        "theme_signals": [
            {"theme": "ETF", "score": 0.95, "related_symbols": ["BTC"]},
        ],
    }))
    text = out.to_json()
    for tok in ("BUY", "SELL", "ENTER", "EXIT"):
        assert not re.search(rf"\b{tok}\b", text), (
            f"{tok} leaked into NewsTrendAgentOutput: {text}"
        )


# ── 47-52. MOCA / 문서 ────────────────────────────────────────


def test_card_to_dict_flags_false():
    d = NewsTrendAgent.card.to_dict()
    assert d["direct_order_allowed"] is False
    assert d["can_invoke_broker"] is False
    assert d["can_invoke_order_gateway"] is False
    forbidden_values = {p.value for p in FORBIDDEN_AGENT_PERMISSIONS}
    for perm in d["allowed_permissions"]:
        assert perm not in forbidden_values


def test_doc_exists():
    assert _DOC_PATH.exists(), f"missing doc: {_DOC_PATH}"


def test_doc_contains_topic_areas():
    text = _DOC_PATH.read_text(encoding="utf-8")
    for label in ("키워드", "뉴스", "공시", "테마", "리스크"):
        assert label in text, f"doc missing area: {label}"


def test_doc_states_direct_order_allowed_false():
    text = _DOC_PATH.read_text(encoding="utf-8")
    assert "direct_order_allowed" in text


def test_doc_states_broker_call_allowed_false():
    text = _DOC_PATH.read_text(encoding="utf-8")
    assert "broker_call_allowed" in text


def test_doc_states_external_api_ban():
    text = _DOC_PATH.read_text(encoding="utf-8")
    assert ("외부 뉴스" in text) or ("external news api" in text.lower())


# ── 53-59. Static guards ──────────────────────────────────────


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
    text = _TARGET.read_text(encoding="utf-8")
    for needle in (
        r'"BUY"', r"'BUY'", r'"SELL"', r"'SELL'",
        r'"ENTER"', r"'ENTER'", r'"EXIT"', r"'EXIT'",
    ):
        assert needle not in text, (
            f"forbidden action literal {needle} in news_trend_agent.py"
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
            f"forbidden output key literal {needle} in news_trend_agent.py"
        )
