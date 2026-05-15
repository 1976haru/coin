"""체크리스트 #29 StrategyBase — 회귀 테스트.

검증:
  1. StrategyCapability 데이터클래스 + to_dict
  2. StrategyBase Protocol — runtime_checkable, capability 속성 강제
  3. 기존 4개 전략에 capability 클래스 속성 존재
  4. 각 capability.name 고유성 + naming convention
  5. StrategyRegistry CRUD + catalog
  6. assert_signal_contract — 신호 객체에 SignalBase 필드 강제
  7. /api/strategies/catalog endpoint
  8. 모듈 경계 — strategies/base.py 가 brokers/execution import 안 함
"""
from __future__ import annotations
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.strategies import (
    StrategyBase, StrategyCapability, StrategyRegistry,
    assert_signal_contract, collect_default_strategies,
    TrendFollowingStrategy, VolatilityBreakoutStrategy,
    PairTradingStrategy, KimpMeanReversionStrategy,
    StrategySignal, PairSignal, KimpSignal,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


# ── 1. StrategyCapability ────────────────────────────────────────

def test_capability_minimal_fields():
    cap = StrategyCapability(
        name="x", description="d",
        required_inputs=("a",), signal_actions=("HOLD",),
    )
    assert cap.supports_pair is False
    assert cap.supports_kimp is False
    assert cap.supports_futures is False
    assert cap.output_signal_class == "StrategySignal"


def test_capability_to_dict_roundtrip_keys():
    cap = StrategyCapability(
        name="x", description="d",
        required_inputs=("a", "b"), signal_actions=("BUY", "HOLD"),
        supports_pair=True,
    )
    d = cap.to_dict()
    for k in ("name", "description", "required_inputs", "signal_actions",
              "supports_pair", "supports_kimp", "supports_futures",
              "output_signal_class"):
        assert k in d


# ── 2. StrategyBase Protocol ─────────────────────────────────────

def test_strategy_base_runtime_check_passes_for_real_strategies():
    """기존 4개 전략 인스턴스가 StrategyBase Protocol 만족."""
    for cls in (TrendFollowingStrategy, VolatilityBreakoutStrategy,
                PairTradingStrategy, KimpMeanReversionStrategy):
        assert isinstance(cls(), StrategyBase), \
            f"{cls.__name__} 가 StrategyBase Protocol 미준수"


def test_strategy_base_runtime_check_fails_for_object_without_capability():
    class Bare:
        pass
    assert not isinstance(Bare(), StrategyBase)


# ── 3. 기존 전략에 capability 존재 ───────────────────────────────

@pytest.mark.parametrize("cls,name,output_cls", [
    (TrendFollowingStrategy,    "trend_following",     "StrategySignal"),
    (VolatilityBreakoutStrategy, "volatility_breakout", "StrategySignal"),
    (PairTradingStrategy,        "pair_trading",        "PairSignal"),
    (KimpMeanReversionStrategy,  "kimp_mean_reversion", "KimpSignal"),
])
def test_strategy_classes_expose_capability(cls, name, output_cls):
    cap = cls.capability
    assert isinstance(cap, StrategyCapability)
    assert cap.name == name
    assert cap.output_signal_class == output_cls
    assert cap.description
    assert cap.required_inputs
    assert cap.signal_actions


def test_pair_strategy_marks_supports_pair():
    assert PairTradingStrategy.capability.supports_pair is True


def test_kimp_strategy_marks_supports_kimp():
    assert KimpMeanReversionStrategy.capability.supports_kimp is True


# ── 4. capability.name 고유성 ────────────────────────────────────

def test_default_strategy_names_are_unique():
    caps = [
        TrendFollowingStrategy.capability,
        VolatilityBreakoutStrategy.capability,
        PairTradingStrategy.capability,
        KimpMeanReversionStrategy.capability,
    ]
    names = [c.name for c in caps]
    assert len(names) == len(set(names)), f"capability.name 중복: {names}"


@pytest.mark.parametrize("cls", [
    TrendFollowingStrategy, VolatilityBreakoutStrategy,
    PairTradingStrategy, KimpMeanReversionStrategy,
])
def test_capability_names_use_snake_case(cls):
    name = cls.capability.name
    assert name.islower()
    assert " " not in name
    assert "-" not in name


# ── 5. StrategyRegistry ──────────────────────────────────────────

def test_registry_register_and_get_by_capability_name():
    r = StrategyRegistry()
    s = TrendFollowingStrategy()
    r.register(s)
    assert r.get("trend_following") is s
    assert "trend_following" in r.names()


def test_registry_register_with_explicit_name_override():
    r = StrategyRegistry()
    r.register(TrendFollowingStrategy(), name="my_custom_name")
    assert r.get("my_custom_name") is not None
    assert r.get("trend_following") is None


def test_registry_rejects_object_without_capability():
    class Bare:
        pass
    r = StrategyRegistry()
    with pytest.raises(TypeError):
        r.register(Bare())


def test_registry_rejects_wrong_capability_type():
    class Wrong:
        capability = "not a StrategyCapability"
    r = StrategyRegistry()
    with pytest.raises(TypeError):
        r.register(Wrong())


def test_registry_remove_and_clear():
    r = StrategyRegistry()
    r.register(TrendFollowingStrategy())
    assert r.remove("trend_following") is True
    assert r.remove("trend_following") is False
    r.register(VolatilityBreakoutStrategy())
    r.clear()
    assert r.names() == []


def test_registry_catalog_returns_dicts():
    r = StrategyRegistry()
    r.register(TrendFollowingStrategy())
    r.register(KimpMeanReversionStrategy())
    cat = r.catalog()
    assert len(cat) == 2
    assert all(isinstance(c, dict) for c in cat)
    assert {c["name"] for c in cat} == {"trend_following", "kimp_mean_reversion"}


def test_collect_default_strategies_has_four():
    r = collect_default_strategies()
    assert sorted(r.names()) == [
        "kimp_mean_reversion", "pair_trading",
        "trend_following", "volatility_breakout",
    ]


# ── 6. Signal contract ───────────────────────────────────────────

def test_assert_signal_contract_passes_for_strategy_signal():
    s = StrategySignal(action="BUY", confidence=0.8, reason="test")
    assert_signal_contract(s)  # no raise


def test_assert_signal_contract_passes_for_kimp_signal():
    s = KimpSignal(action="HOLD", symbol="BTC", kimp_pct=-1.0,
                   confidence=0.0, reason="test")
    assert_signal_contract(s)


def test_assert_signal_contract_passes_for_pair_signal():
    s = PairSignal(action="HOLD", symbol_a="BTC", symbol_b="ETH",
                   z_score=0.0, hedge_ratio=1.0, confidence=0.0, reason="t")
    assert_signal_contract(s)


def test_assert_signal_contract_fails_for_object_missing_field():
    class FakeSignal:
        action = "BUY"
        confidence = 0.5
        # reason / is_order_intent 누락
    with pytest.raises(AssertionError):
        assert_signal_contract(FakeSignal())


# ── 7. Integration — 실제 generate 가 contract 만족 ─────────────

def test_trend_strategy_generate_returns_contract_compliant_signal():
    s = TrendFollowingStrategy()
    closes = list(range(100, 200))
    sig = s.generate(closes, adx=25.0, volume_ratio=1.5)
    assert_signal_contract(sig)
    assert sig.is_order_intent is False  # 기본 false


def test_kimp_strategy_generate_returns_contract_compliant_signal():
    s = KimpMeanReversionStrategy()
    sig = s.generate_signal("BTC", 980, 1, 1000)
    assert_signal_contract(sig)
    assert sig.is_order_intent is False


# ── 8. /api/strategies/catalog endpoint ──────────────────────────

def test_api_strategies_catalog_returns_four():
    from app.main import app
    client = TestClient(app)
    r = client.get("/api/strategies/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 4
    names = {s["name"] for s in body["strategies"]}
    assert names == {"trend_following", "volatility_breakout",
                     "pair_trading", "kimp_mean_reversion"}


def test_api_strategies_catalog_contains_required_inputs():
    from app.main import app
    client = TestClient(app)
    r = client.get("/api/strategies/catalog")
    body = r.json()
    for s in body["strategies"]:
        assert isinstance(s["required_inputs"], list)
        assert len(s["required_inputs"]) > 0


# ── 9. 모듈 경계 ─────────────────────────────────────────────────

def test_strategy_base_does_not_import_brokers_or_execution():
    """체크리스트 §3.1: strategies/base.py 가 brokers/execution 을 직접 import 금지."""
    text = (REPO_ROOT / "backend" / "app" / "strategies" / "base.py"
            ).read_text(encoding="utf-8")
    for line in text.splitlines():
        s = line.strip()
        if not (s.startswith("import ") or s.startswith("from ")):
            continue
        for forbidden in ("app.brokers", "app.execution"):
            assert forbidden not in s, \
                f"strategies/base.py 가 forbidden import 보유: {s}"
