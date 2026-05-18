"""체크리스트 #29 StrategyContract — 회귀 테스트 (신규 ABC 확장).

기존 `test_strategy_base.py` (#29 1차 — Protocol 기반 StrategyBase + 4개 기존
전략 contract 검증) 회귀 없이 본 모듈은 **신규 StrategyContract ABC** 와
연관 타입/registry/agent hook + 정적 회귀를 검증한다.

검증:
  StrategyContract ABC:
    1. 직접 인스턴스화 불가
    2. 필수 메서드 미구현 subclass 는 인스턴스화 불가
    3. 완전 구현 DummyStrategy 인스턴스화 가능
    4. evaluate() 가 dict 반환 (signal/sizing/exit/explanation 포함)
    5. is_order_intent / direct_order_allowed 영구 False
  StrategyContext:
    6. 기본 생성
    7. closes 가 list → tuple 변환
    8. extra 에 secret 류 키 → StrategyContractError
  StrategySignal:
    9. action 기본값 / is_order_intent 기본 False
   10. is_order_intent=True 신호를 반환하면 evaluate() 가 raise
  PositionSizingHint:
   11. 기본 is_final_order_size=False
   12. PositionSizingHint 자체에 broker 인자 부재 (정적)
  ExitRuleDecision:
   13. should_exit=True 여도 is_order_intent=False
   14. exit_qty_fraction 범위 검증 (0..1)
   15. urgency 검증 (normal/high/critical)
  SignalExplanation:
   16. 필수 필드 포함 (strategy_name/symbol/summary/confidence/generated_at)
   17. to_dict 직렬화
  ContractRegistry:
   18. StrategyContract 하위 클래스만 register 허용
   19. 같은 name 중복 등록 시 ValueError
   20. get_strategy / list_strategies / catalog
   21. create_strategy — 인스턴스화
   22. filter_by_market_regime
   23. filter_by_symbol — pair 전략 제외
   24. filter_enabled — enabled_by_default=False 기본
   25. set_enabled
  StrategySelectionAgent hook:
   26. select_active_strategies 가 StrategyActivationDecision 반환
   27. direct_order_allowed=False 영구
   28. regime 매칭 동작
   29. pair 전략은 단일 symbol context 에서 제외
   30. notice_high_risk_count > 0 → notes 추가, 차단은 안 함
  Safety guards (allowed actions):
   31. is_safe_action allowed catalog
   32. assert_no_order_intent 가 True 신호에 raise
  Static regression:
   33. backend/app/strategies/ 에 app.brokers / app.execution import 부재
   34. backend/app/strategies/ 에 place_order / cancel_order / get_balance /
       submit_order 호출 부재
   35. backend/app/strategies/contract*.py 에 broker SDK import 부재
   36. contract module 에 ENABLE_LIVE_TRADING=True literal 부재
  Freshness/Quality 통합:
   37. data_quality_grade=EXCLUDE → DummyStrategy BLOCKED 반환 가능 (sample)
   38. freshness_ok=False → DummyStrategy NO_ACTION 반환 가능 (sample)
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest

from app.strategies.base import StrategyCapability
from app.strategies._signals import StrategySignal
from app.strategies.contract import (
    StrategyContext, PositionSizingHint, ExitRuleDecision,
    SignalExplanation, StrategyContract, StrategyContractError,
    ALLOWED_SIGNAL_ACTIONS, assert_no_order_intent, is_safe_action,
)
from app.strategies.contract_registry import (
    ContractRegistry, build_empty_registry,
)
from app.agents.strategy_selection import (
    StrategyActivationContext, StrategyActivationDecision,
    select_active_strategies,
)


# ── Helper — DummyStrategy ───────────────────────────────────────


class _DummyStrategy(StrategyContract):
    """test fixture — 안전 동작.

    - data_quality_grade==EXCLUDE → action=BLOCKED
    - freshness_ok==False → action=NO_ACTION
    - 그 외 → action=HOLD
    """

    capability = StrategyCapability(
        name="dummy_for_test",
        description="dummy strategy for #29 contract tests",
        required_inputs=("closes",),
        signal_actions=("HOLD", "BLOCKED", "NO_ACTION"),
    )
    enabled_by_default = False
    preferred_regimes = ("UNKNOWN",)

    def generate_signal(self, context):
        if context.data_quality_grade == "EXCLUDE":
            return StrategySignal(action="BLOCKED", confidence=0.0,
                                  reason="data quality EXCLUDE")
        if not context.freshness_ok:
            return StrategySignal(action="NO_ACTION", confidence=0.0,
                                  reason="stale data")
        return StrategySignal(action="HOLD", confidence=0.5, reason="ok")

    def calculate_size(self, context, signal):
        return PositionSizingHint(
            symbol=context.symbol, suggested_notional_usdt=100.0,
            confidence=signal.confidence, reason="dummy size hint",
        )

    def exit_rule(self, context, signal):
        return ExitRuleDecision(symbol=context.symbol, should_exit=False)

    def explain_signal(self, context, signal):
        return SignalExplanation(
            strategy_name=self.capability.name,
            symbol=context.symbol,
            summary=f"dummy: action={signal.action}",
            reasons=(signal.reason,),
            confidence=signal.confidence,
        )


class _IncompleteStrategy(StrategyContract):
    """generate_signal 만 구현 — 나머지 미구현 → 인스턴스화 실패."""
    capability = StrategyCapability(
        name="incomplete", description="incomplete",
        required_inputs=(), signal_actions=("HOLD",),
    )

    def generate_signal(self, context):
        return StrategySignal(action="HOLD", confidence=0, reason="x")


class _PairLikeStrategy(StrategyContract):
    """pair 전략 — filter_by_symbol 에서 제외 검증용."""
    capability = StrategyCapability(
        name="pair_dummy", description="pair dummy",
        required_inputs=(), signal_actions=("HOLD",),
        supports_pair=True,
    )

    def generate_signal(self, context):
        return StrategySignal(action="HOLD", confidence=0, reason="x")

    def calculate_size(self, context, signal):
        return PositionSizingHint(symbol=context.symbol)

    def exit_rule(self, context, signal):
        return ExitRuleDecision(symbol=context.symbol)

    def explain_signal(self, context, signal):
        return SignalExplanation(
            strategy_name=self.capability.name, symbol=context.symbol,
            summary="pair", confidence=0,
        )


class _RegimeSpecificStrategy(StrategyContract):
    """preferred_regimes 필터 검증용."""
    capability = StrategyCapability(
        name="regime_trender", description="trender",
        required_inputs=(), signal_actions=("HOLD",),
    )
    preferred_regimes = ("TREND_UP", "TREND_DOWN")

    def generate_signal(self, context):
        return StrategySignal(action="HOLD", confidence=0, reason="x")

    def calculate_size(self, context, signal):
        return PositionSizingHint(symbol=context.symbol)

    def exit_rule(self, context, signal):
        return ExitRuleDecision(symbol=context.symbol)

    def explain_signal(self, context, signal):
        return SignalExplanation(
            strategy_name=self.capability.name, symbol=context.symbol,
            summary="trender", confidence=0,
        )


class _BadOrderIntentStrategy(StrategyContract):
    """is_order_intent=True 를 반환하는 잘못된 전략 — evaluate() 가 raise 해야."""
    capability = StrategyCapability(
        name="bad_oi", description="bad",
        required_inputs=(), signal_actions=("BUY",),
    )

    def generate_signal(self, context):
        # contract 위반 — is_order_intent=True 강제
        return StrategySignal(action="BUY", confidence=1, reason="bad",
                              is_order_intent=True)

    def calculate_size(self, context, signal):
        return PositionSizingHint(symbol=context.symbol)

    def exit_rule(self, context, signal):
        return ExitRuleDecision(symbol=context.symbol)

    def explain_signal(self, context, signal):
        return SignalExplanation(
            strategy_name=self.capability.name, symbol=context.symbol,
            summary="bad", confidence=0,
        )


# ── 1-3. ABC instantiation ──────────────────────────────────────


def test_cannot_instantiate_strategy_contract_directly():
    with pytest.raises(TypeError):
        StrategyContract()  # type: ignore[abstract]


def test_incomplete_subclass_cannot_instantiate():
    with pytest.raises(TypeError):
        _IncompleteStrategy()  # type: ignore[abstract]


def test_complete_subclass_can_instantiate():
    s = _DummyStrategy()
    assert isinstance(s, StrategyContract)


# ── 4-5. evaluate ───────────────────────────────────────────────


def test_evaluate_returns_dict_with_all_layers():
    s = _DummyStrategy()
    ctx = StrategyContext(symbol="BTC-USDT", closes=(100.0, 101.0, 102.0))
    out = s.evaluate(ctx)
    for k in ("strategy", "symbol", "signal", "sizing", "exit",
              "explanation", "is_order_intent", "direct_order_allowed",
              "used_for_order"):
        assert k in out
    assert out["is_order_intent"] is False
    assert out["direct_order_allowed"] is False
    assert out["used_for_order"] is False


def test_evaluate_raises_when_order_intent_true():
    s = _BadOrderIntentStrategy()
    ctx = StrategyContext(symbol="BTC-USDT")
    with pytest.raises(StrategyContractError):
        s.evaluate(ctx)


# ── 6-8. StrategyContext ────────────────────────────────────────


def test_strategy_context_defaults():
    c = StrategyContext(symbol="BTC-USDT")
    assert c.symbol == "BTC-USDT"
    assert c.timeframe == "1m"
    assert c.regime == "UNKNOWN"
    assert c.freshness_ok is True
    assert c.data_quality_grade == "GOOD"
    assert c.is_in_universe is True


def test_strategy_context_list_to_tuple_conversion():
    c = StrategyContext(symbol="x", closes=[1.0, 2.0, 3.0])
    assert isinstance(c.closes, tuple)
    assert c.closes == (1.0, 2.0, 3.0)


def test_strategy_context_rejects_secret_keys_in_extra():
    with pytest.raises(StrategyContractError):
        StrategyContext(symbol="x", extra={"api_key": "leak"})
    with pytest.raises(StrategyContractError):
        StrategyContext(symbol="x", extra={"OKX_PASSPHRASE": "leak"})
    with pytest.raises(StrategyContractError):
        StrategyContext(symbol="x", extra={"access_token": "leak"})


# ── 9-10. StrategySignal ────────────────────────────────────────


def test_strategy_signal_defaults():
    s = StrategySignal(action="HOLD", confidence=0.5, reason="x")
    assert s.is_order_intent is False


def test_assert_no_order_intent_on_true_raises():
    s = StrategySignal(action="BUY", confidence=1, reason="x",
                       is_order_intent=True)
    with pytest.raises(StrategyContractError):
        assert_no_order_intent(s)


def test_assert_no_order_intent_on_false_ok():
    s = StrategySignal(action="HOLD", confidence=0, reason="x")
    assert_no_order_intent(s)  # raises 안 함


# ── 11-12. PositionSizingHint ───────────────────────────────────


def test_position_sizing_hint_defaults():
    h = PositionSizingHint(symbol="BTC-USDT")
    assert h.is_final_order_size is False
    assert h.used_for_order is False
    assert h.leverage_hint == 1.0


def test_position_sizing_hint_no_broker_field():
    """PositionSizingHint 의 to_dict 에 broker/adapter 류 key 부재."""
    h = PositionSizingHint(symbol="x", suggested_notional_usdt=100)
    d = h.to_dict()
    for bad in ("broker", "adapter", "order_gateway", "place_order"):
        assert bad not in d


# ── 13-15. ExitRuleDecision ─────────────────────────────────────


def test_exit_rule_decision_is_order_intent_false_even_when_should_exit():
    e = ExitRuleDecision(symbol="x", should_exit=True,
                         exit_qty_fraction=0.5, reason="risk")
    assert e.is_order_intent is False


def test_exit_rule_decision_rejects_invalid_fraction():
    with pytest.raises(ValueError):
        ExitRuleDecision(symbol="x", exit_qty_fraction=1.5)
    with pytest.raises(ValueError):
        ExitRuleDecision(symbol="x", exit_qty_fraction=-0.1)


def test_exit_rule_decision_rejects_invalid_urgency():
    with pytest.raises(ValueError):
        ExitRuleDecision(symbol="x", urgency="extreme")


# ── 16-17. SignalExplanation ────────────────────────────────────


def test_signal_explanation_required_fields():
    e = SignalExplanation(
        strategy_name="dummy", symbol="BTC-USDT",
        summary="test summary", confidence=0.7,
        reasons=("a", "b"), risks=("c",),
    )
    assert e.strategy_name == "dummy"
    assert e.confidence == 0.7
    assert e.generated_at  # auto-filled


def test_signal_explanation_to_dict():
    e = SignalExplanation(
        strategy_name="d", symbol="BTC", summary="s",
        reasons=("r1",), evidence=("ev1",),
        risks=("rk1",), limitations=("lim",),
        confidence=0.5,
    )
    d = e.to_dict()
    assert d["reasons"] == ["r1"]
    assert d["evidence"] == ["ev1"]


# ── 18-25. ContractRegistry ─────────────────────────────────────


def _fresh_registry() -> ContractRegistry:
    return build_empty_registry()


def test_registry_register_only_strategycontract_subclass():
    r = _fresh_registry()
    class NotAContract:
        capability = StrategyCapability(
            name="nope", description="", required_inputs=(),
            signal_actions=(),
        )
    with pytest.raises(TypeError):
        r.register_strategy(NotAContract)  # type: ignore[arg-type]


def test_registry_duplicate_name_raises():
    r = _fresh_registry()
    r.register_strategy(_DummyStrategy)
    with pytest.raises(ValueError):
        r.register_strategy(_DummyStrategy)


def test_registry_get_list_catalog():
    r = _fresh_registry()
    r.register_strategy(_DummyStrategy)
    r.register_strategy(_RegimeSpecificStrategy)
    assert "dummy_for_test" in r.list_strategies()
    assert r.get_strategy("dummy_for_test") is _DummyStrategy
    cat = r.catalog()
    names = {c["name"] for c in cat}
    assert names == {"dummy_for_test", "regime_trender"}
    for entry in cat:
        assert entry["enabled"] is False
        assert entry["direct_order_allowed"] is False


def test_registry_create_strategy():
    r = _fresh_registry()
    r.register_strategy(_DummyStrategy)
    inst = r.create_strategy("dummy_for_test")
    assert isinstance(inst, _DummyStrategy)


def test_registry_create_strategy_unknown_raises():
    r = _fresh_registry()
    with pytest.raises(KeyError):
        r.create_strategy("nope")


def test_registry_filter_by_market_regime():
    r = _fresh_registry()
    r.register_strategy(_DummyStrategy)             # preferred=("UNKNOWN",)
    r.register_strategy(_RegimeSpecificStrategy)    # preferred=("TREND_UP", "TREND_DOWN")
    out_up = [e.capability.name for e in r.filter_by_market_regime("TREND_UP")]
    assert "regime_trender" in out_up
    out_range = [e.capability.name for e in r.filter_by_market_regime("RANGE")]
    # RANGE 에서는 trender 가 제외, dummy 는 UNKNOWN 이라 보수적으로 통과 안 함.
    assert "regime_trender" not in out_range


def test_registry_filter_by_symbol_excludes_pair():
    r = _fresh_registry()
    r.register_strategy(_DummyStrategy)
    r.register_strategy(_PairLikeStrategy)
    names = [e.capability.name for e in r.filter_by_symbol("BTC-USDT")]
    assert "pair_dummy" not in names
    assert "dummy_for_test" in names


def test_registry_filter_enabled_default_false():
    r = _fresh_registry()
    r.register_strategy(_DummyStrategy)
    assert r.filter_enabled() == []
    assert r.set_enabled("dummy_for_test", True) is True
    en = r.filter_enabled()
    assert len(en) == 1


def test_registry_set_enabled_unknown_returns_false():
    r = _fresh_registry()
    assert r.set_enabled("nope", True) is False


# ── 26-30. StrategySelectionAgent hook ──────────────────────────


def test_select_active_strategies_returns_decision():
    r = _fresh_registry()
    r.register_strategy(_DummyStrategy)
    r.register_strategy(_RegimeSpecificStrategy)
    ctx = StrategyActivationContext(symbol="BTC-USDT", regime="TREND_UP")
    d = select_active_strategies(ctx, r)
    assert isinstance(d, StrategyActivationDecision)
    assert d.direct_order_allowed is False
    assert d.used_for_order is False


def test_select_activated_regime_match():
    r = _fresh_registry()
    r.register_strategy(_RegimeSpecificStrategy)
    d = select_active_strategies(
        StrategyActivationContext(symbol="BTC", regime="TREND_UP"), r,
    )
    assert "regime_trender" in d.activated


def test_select_skipped_regime_mismatch():
    r = _fresh_registry()
    r.register_strategy(_RegimeSpecificStrategy)
    d = select_active_strategies(
        StrategyActivationContext(symbol="BTC", regime="RANGE"), r,
    )
    assert "regime_trender" in d.skipped
    assert "regime_mismatch" in d.skipped_reasons["regime_trender"]


def test_select_pair_strategy_excluded_for_single_symbol():
    r = _fresh_registry()
    r.register_strategy(_PairLikeStrategy)
    d = select_active_strategies(
        StrategyActivationContext(symbol="BTC", regime="UNKNOWN"), r,
    )
    assert "pair_dummy" in d.skipped
    assert "pair_strategy_requires_two_symbols" in d.skipped_reasons["pair_dummy"]


def test_select_notice_high_risk_adds_notes_but_does_not_block():
    r = _fresh_registry()
    r.register_strategy(_DummyStrategy)
    d = select_active_strategies(
        StrategyActivationContext(
            symbol="BTC", regime="UNKNOWN", notice_high_risk_count=3,
        ),
        r,
    )
    assert "dummy_for_test" in d.activated  # 차단 안 함
    assert any("notice_high_risk_count" in n for n in d.notes)


def test_select_theme_review_required_adds_notes():
    r = _fresh_registry()
    r.register_strategy(_DummyStrategy)
    d = select_active_strategies(
        StrategyActivationContext(
            symbol="BTC", regime="UNKNOWN", theme_review_required=True,
        ),
        r,
    )
    assert any("review_required" in n for n in d.notes)


def test_select_unknown_regime_includes_all_entries():
    r = _fresh_registry()
    r.register_strategy(_DummyStrategy)
    r.register_strategy(_RegimeSpecificStrategy)
    d = select_active_strategies(
        StrategyActivationContext(symbol="BTC", regime="UNKNOWN"), r,
    )
    # UNKNOWN → 모든 비-pair 후보 통과
    assert "dummy_for_test" in d.activated
    assert "regime_trender" in d.activated


# ── 31-32. Safety helpers ───────────────────────────────────────


def test_is_safe_action_catalog():
    for a in ("BUY", "SELL", "HOLD", "BLOCKED", "NO_ACTION", "WATCH_ONLY"):
        assert is_safe_action(a) is True
    assert is_safe_action("PLACE_ORDER") is False
    assert is_safe_action("") is False
    assert is_safe_action(None) is False  # type: ignore[arg-type]


def test_allowed_signal_actions_does_not_contain_forbidden():
    for a in ALLOWED_SIGNAL_ACTIONS:
        assert "PLACE" not in a
        assert "SUBMIT" not in a


# ── 33-36. Static regression ────────────────────────────────────


_REPO_BACKEND_APP = Path(__file__).resolve().parent.parent / "app"


def _scan(directory, pattern, glob="**/*.py"):
    hits = []
    for p in directory.glob(glob):
        if "__pycache__" in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            hits.append(p)
    return hits


def test_strategies_do_not_import_brokers():
    """app/strategies/ 어디서도 app.brokers 를 import 하지 않는다."""
    pat = re.compile(
        r"^\s*(?:from\s+app\.brokers|import\s+app\.brokers)",
        re.M,
    )
    hits = _scan(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy imports app.brokers: {hits}"


def test_strategies_do_not_import_execution_or_order_gateway():
    pat = re.compile(
        r"(?:from|import)\s+app\.(?:execution\.order_gateway|execution\.order_executor|execution)\b",
    )
    hits = _scan(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy imports execution: {hits}"


def test_strategies_no_order_method_calls():
    """strategies/ 에 .place_order(/.cancel_order(/.get_balance(/.submit_order( 호출 부재."""
    pat = re.compile(
        r"\.(?:place_order|cancel_order|get_balance|submit_order)\s*\(",
    )
    hits = _scan(_REPO_BACKEND_APP / "strategies", pat)
    assert not hits, f"strategy calls order method: {hits}"


def test_contract_modules_no_broker_sdk_imports():
    pat = re.compile(
        r"^\s*(?:import\s+(?:requests|httpx|ccxt|pyupbit|"
        r"binance|binance_connector|okx)|"
        r"from\s+(?:requests|httpx|ccxt|pyupbit|"
        r"binance|binance_connector|okx))",
        re.M,
    )
    for fname in ("contract.py", "contract_registry.py"):
        text = (_REPO_BACKEND_APP / "strategies" / fname).read_text(encoding="utf-8")
        assert not pat.search(text), f"{fname} imports broker/network SDK"


def test_contract_modules_no_forbidden_substrings():
    forbidden = (
        "ENABLE_LIVE_TRADING = True",
        "ENABLE_AI_EXECUTION = True",
        "ENABLE_CRYPTO_FUTURES_LIVE = True",
        "is_order_intent: bool = True",
        "is_final_order_size: bool = True",
    )
    for fname in ("contract.py", "contract_registry.py"):
        text = (_REPO_BACKEND_APP / "strategies" / fname).read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{fname} contains {needle!r}"


def test_strategy_selection_agent_does_not_import_brokers():
    text = (_REPO_BACKEND_APP / "agents" / "strategy_selection.py").read_text(
        encoding="utf-8",
    )
    pat = re.compile(
        r"^\s*(?:from\s+app\.brokers|import\s+app\.brokers)",
        re.M,
    )
    assert not pat.search(text)


# ── 37-38. Freshness / Data quality 통합 (sample) ───────────────


def test_dummy_strategy_blocks_on_data_quality_exclude():
    s = _DummyStrategy()
    ctx = StrategyContext(
        symbol="BTC-USDT",
        data_quality_grade="EXCLUDE",
    )
    out = s.evaluate(ctx)
    assert out["signal"]["action"] == "BLOCKED"
    assert out["is_order_intent"] is False


def test_dummy_strategy_no_action_on_stale_data():
    s = _DummyStrategy()
    ctx = StrategyContext(
        symbol="BTC-USDT", freshness_ok=False,
    )
    out = s.evaluate(ctx)
    assert out["signal"]["action"] == "NO_ACTION"


# ── 39. notice/theme context 통합 (sample) ─────────────────────


def test_strategy_explanation_can_include_notice_summary_without_order_intent():
    """notice/theme context 가 explanation 에 포함되더라도 주문 신호로 변환되지 않음."""
    s = _DummyStrategy()
    ctx = StrategyContext(
        symbol="LUNA-USDT",
        notice_context={"high_risk_symbols": ["LUNA"], "direct_order_allowed": False},
        theme_context={"review_required_symbols": ["LUNA"]},
    )
    out = s.evaluate(ctx)
    assert out["is_order_intent"] is False
    assert out["direct_order_allowed"] is False


# ── 40. ContractRegistry exports ───────────────────────────────


def test_strategies_module_exports_contract_types():
    """app.strategies 패키지가 contract 타입을 노출하는지 (선택적 — 직접 import 도 가능)."""
    from app.strategies import contract as ctr
    for name in ("StrategyContext", "StrategyContract", "PositionSizingHint",
                 "ExitRuleDecision", "SignalExplanation",
                 "ALLOWED_SIGNAL_ACTIONS"):
        assert hasattr(ctr, name), f"contract.{name} missing"
