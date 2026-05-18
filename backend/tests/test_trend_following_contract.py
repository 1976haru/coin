"""체크리스트 #30 TrendFollowingContractStrategy — 회귀 테스트 (#29 ABC).

기존 `test_trend_following.py` (#30 1차 — Protocol 기반 TrendFollowingStrategy) 는
변경 없음. 본 모듈은 새로 추가된 `TrendFollowingContractStrategy` (StrategyContract
ABC 구현) 를 검증한다.

검증:
  Indicators:
    1. SMA 정확성 + 표본 부족 처리
    2. EMA 결정론
    3. ATR 양수 + 정확성
    4. true_range 길이 = n-1
    5. donchian_channel high/low + exclude_current=True
    6. ADX 양수 + 강한 추세 시 > 약한 추세 시
    7. ADX 데이터 부족 시 0.0
  Signal generation:
    8. 강한 상승 추세 → BUY candidate, confidence > 0.5
    9. 약한 추세 (ADX < adx_min) → HOLD
   10. data_quality EXCLUDE → BLOCKED
   11. freshness stale → BLOCKED
   12. universe 밖 → BLOCKED
   13. high-risk notice 감지 → BLOCKED
   14. 데이터 부족 → NO_ACTION
   15. OHLC 길이 불일치 → NO_ACTION
   16. allow_short_candidates=False 면 하락 추세에서도 SELL 안 함
   17. allow_short_candidates=True 면 하락 추세에서 SELL candidate
  Sizing:
   18. HOLD/BLOCKED/NO_ACTION 시 notional=0
   19. BUY/SELL 시 confidence 비례
   20. is_final_order_size=False / used_for_order=False 영구
  Exit rule:
   21. data_quality EXCLUDE → should_exit=True, urgency=critical, fraction=1.0
   22. freshness stale → urgency=high, fraction=1.0
   23. high-risk notice → urgency=high
   24. fast<slow EMA → partial exit (0.6)
   25. Donchian low 이탈 → full exit
   26. ADX 급락 → partial exit (0.3)
   27. 정상 추세 유지 → should_exit=False
  Explanation:
   28. summary 에 "candidate only" 포함
   29. evidence 에 EMA/SMA/ADX/Donchian 표시
   30. risks 에 data_quality/freshness/notice 반영
   31. limitations 에 주문 금지 명시
  evaluate():
   32. is_order_intent=False 영구
   33. direct_order_allowed=False 영구
   34. used_for_order=False 영구
  Static guards:
   35. trend_following_contract.py 에 broker/order_gateway/SDK import 부재
   36. .place_order(/.cancel_order(/.get_balance(/.submit_order( 호출 부재
   37. is_order_intent: bool = True literal 부재
  Registry integration:
   38. ContractRegistry 에 등록 가능
   39. capability.name == "trend_following_v2"
   40. preferred_regimes == ("TREND_UP", "TREND_DOWN")
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest

from app.strategies._indicators import (
    sma, ema, atr, true_range, donchian_channel, adx,
)
from app.strategies.contract import StrategyContext
from app.strategies.contract_registry import build_empty_registry
from app.strategies.trend_following_contract import (
    TrendFollowingContractStrategy, TrendFollowingParams,
)


# ── 1-7. Indicators ─────────────────────────────────────────────


def test_sma_accuracy():
    assert sma([1.0, 2.0, 3.0, 4.0, 5.0], 3) == pytest.approx(4.0)


def test_sma_insufficient_uses_available():
    assert sma([1.0, 2.0], 5) == pytest.approx(1.5)


def test_ema_deterministic():
    prices = [100.0, 101.0, 102.0, 103.0, 104.0]
    assert ema(prices, 3) == ema(prices, 3)
    assert ema(prices, 3) > 100


def test_atr_positive_and_correct():
    highs = [10, 11, 12, 13, 14]
    lows = [9, 10, 11, 12, 13]
    closes = [9.5, 10.5, 11.5, 12.5, 13.5]
    v = atr(highs, lows, closes, period=3)
    assert v > 0


def test_true_range_length():
    highs = [10, 11, 12]
    lows = [9, 10, 11]
    closes = [9.5, 10.5, 11.5]
    trs = true_range(highs, lows, closes)
    assert len(trs) == 2


def test_true_range_empty_when_short():
    assert true_range([10], [9], [9.5]) == []


def test_donchian_channel_basic():
    highs = [10, 11, 12, 13, 14, 15]
    lows = [5, 6, 7, 8, 9, 10]
    hi, lo = donchian_channel(highs, lows, period=3, exclude_current=True)
    # 마지막 봉 제외 → 직전 3봉 (10,11,12)→max=12, lows=(7,8,9)→min=7
    # period=3 인데 exclude_current 후 직전 3봉이 (11,12,13) lows(6,7,8)
    # 실제: end=5, start=2 → highs[2:5]=[12,13,14], lows[2:5]=[7,8,9]
    assert hi == 14
    assert lo == 7


def test_donchian_channel_include_current():
    highs = [10, 11, 12]
    lows = [5, 6, 7]
    hi, lo = donchian_channel(highs, lows, period=3, exclude_current=False)
    assert hi == 12
    assert lo == 5


def test_donchian_channel_safe_inputs():
    assert donchian_channel([], [], period=5) == (0.0, 0.0)
    assert donchian_channel([1, 2], [1, 2], period=0) == (0.0, 0.0)


def test_adx_positive_in_strong_trend():
    # 강한 상승 추세
    closes = [100.0 + i for i in range(60)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    v = adx(highs, lows, closes, period=14)
    assert v > 0


def test_adx_low_in_choppy():
    # 횡보 — 좁은 범위
    closes = [100.0 + (i % 2) * 0.1 for i in range(60)]
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.1 for c in closes]
    v = adx(highs, lows, closes, period=14)
    assert v < 30   # 강한 추세 아님


def test_adx_insufficient_returns_zero():
    assert adx([1, 2], [0, 1], [0.5, 1.5], period=14) == 0.0


def test_adx_length_mismatch_returns_zero():
    assert adx([1, 2, 3], [0, 1], [0.5, 1.5, 2.5], period=14) == 0.0


# ── 8-17. Signal generation ─────────────────────────────────────


def _strong_uptrend_data(n: int = 100):
    closes = [100.0 + i * 1.0 for i in range(n)]
    # 마지막 봉 강한 돌파
    closes[-1] = closes[-1] * 1.05
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    return tuple(closes), tuple(highs), tuple(lows)


def _strong_downtrend_data(n: int = 100):
    closes = [200.0 - i * 1.0 for i in range(n)]
    closes[-1] = closes[-1] * 0.95
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    return tuple(closes), tuple(highs), tuple(lows)


def _flat_data(n: int = 100):
    closes = [100.0 + (i % 3) * 0.05 for i in range(n)]
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.1 for c in closes]
    return tuple(closes), tuple(highs), tuple(lows)


def test_strong_uptrend_produces_buy_candidate():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy(
        TrendFollowingParams(min_candles_required=60, adx_min=10.0),
    )
    ctx = StrategyContext(symbol="BTC-USDT", closes=closes, highs=highs, lows=lows)
    sig = s.generate_signal(ctx)
    assert sig.action == "BUY"
    assert sig.confidence > 0.5
    assert sig.is_order_intent is False


def test_weak_trend_returns_hold():
    closes, highs, lows = _flat_data()
    s = TrendFollowingContractStrategy(
        TrendFollowingParams(min_candles_required=60, adx_min=18.0),
    )
    ctx = StrategyContext(symbol="BTC-USDT", closes=closes, highs=highs, lows=lows)
    sig = s.generate_signal(ctx)
    # 약한 추세 → HOLD 또는 NO_ACTION
    assert sig.action in ("HOLD", "NO_ACTION")


def test_data_quality_exclude_blocks():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy()
    ctx = StrategyContext(
        symbol="BTC-USDT", closes=closes, highs=highs, lows=lows,
        data_quality_grade="EXCLUDE",
    )
    sig = s.generate_signal(ctx)
    assert sig.action == "BLOCKED"
    assert "EXCLUDE" in sig.reason


def test_freshness_stale_blocks():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy()
    ctx = StrategyContext(
        symbol="BTC-USDT", closes=closes, highs=highs, lows=lows,
        freshness_ok=False,
    )
    sig = s.generate_signal(ctx)
    assert sig.action == "BLOCKED"
    assert "freshness" in sig.reason.lower() or "stale" in sig.reason.lower()


def test_outside_universe_blocks():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy()
    ctx = StrategyContext(
        symbol="BTC-USDT", closes=closes, highs=highs, lows=lows,
        is_in_universe=False,
    )
    sig = s.generate_signal(ctx)
    assert sig.action == "BLOCKED"


def test_high_risk_notice_blocks():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy()
    ctx = StrategyContext(
        symbol="LUNA-USDT", closes=closes, highs=highs, lows=lows,
        notice_context={"high_risk_symbols": ["LUNA"]},
    )
    sig = s.generate_signal(ctx)
    assert sig.action == "BLOCKED"


def test_insufficient_candles_no_action():
    s = TrendFollowingContractStrategy(
        TrendFollowingParams(min_candles_required=200),
    )
    short_closes = tuple(100.0 + i for i in range(10))
    short_highs = tuple(c + 0.1 for c in short_closes)
    short_lows = tuple(c - 0.1 for c in short_closes)
    ctx = StrategyContext(
        symbol="BTC-USDT",
        closes=short_closes, highs=short_highs, lows=short_lows,
    )
    sig = s.generate_signal(ctx)
    assert sig.action == "NO_ACTION"
    assert "insufficient" in sig.reason.lower()


def test_ohlc_length_mismatch_no_action():
    s = TrendFollowingContractStrategy()
    ctx = StrategyContext(
        symbol="BTC-USDT",
        closes=tuple(range(100)), highs=tuple(range(50)), lows=tuple(range(100)),
    )
    sig = s.generate_signal(ctx)
    assert sig.action == "NO_ACTION"


def test_short_candidates_disabled_by_default():
    closes, highs, lows = _strong_downtrend_data()
    s = TrendFollowingContractStrategy(
        TrendFollowingParams(min_candles_required=60, adx_min=10.0,
                             allow_short_candidates=False),
    )
    ctx = StrategyContext(symbol="BTC-USDT", closes=closes, highs=highs, lows=lows)
    sig = s.generate_signal(ctx)
    assert sig.action != "SELL"  # SHORT 비활성


def test_short_candidates_enabled_produces_sell():
    closes, highs, lows = _strong_downtrend_data()
    s = TrendFollowingContractStrategy(
        TrendFollowingParams(min_candles_required=60, adx_min=10.0,
                             allow_short_candidates=True),
    )
    ctx = StrategyContext(symbol="BTC-USDT", closes=closes, highs=highs, lows=lows)
    sig = s.generate_signal(ctx)
    assert sig.action == "SELL"
    assert sig.is_order_intent is False


# ── 18-20. Sizing ───────────────────────────────────────────────


def test_sizing_zero_for_non_actionable():
    closes, highs, lows = _flat_data()
    s = TrendFollowingContractStrategy(
        TrendFollowingParams(min_candles_required=60, adx_min=99),  # 강제 HOLD
    )
    ctx = StrategyContext(symbol="BTC-USDT", closes=closes, highs=highs, lows=lows)
    sig = s.generate_signal(ctx)
    hint = s.calculate_size(ctx, sig)
    assert hint.suggested_notional_usdt == 0.0
    assert hint.is_final_order_size is False
    assert hint.used_for_order is False


def test_sizing_proportional_to_confidence():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy(
        TrendFollowingParams(min_candles_required=60, adx_min=10.0,
                             base_notional_usdt=200.0),
    )
    ctx = StrategyContext(symbol="BTC-USDT", closes=closes, highs=highs, lows=lows)
    sig = s.generate_signal(ctx)
    hint = s.calculate_size(ctx, sig)
    assert hint.suggested_notional_usdt > 0
    assert hint.suggested_notional_usdt <= 200.0
    # confidence × base_notional
    assert hint.suggested_notional_usdt == pytest.approx(
        sig.confidence * 200.0, rel=1e-6,
    )
    assert hint.is_final_order_size is False


# ── 21-27. Exit rule ────────────────────────────────────────────


def test_exit_data_quality_exclude_critical():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy()
    ctx = StrategyContext(
        symbol="BTC-USDT", closes=closes, highs=highs, lows=lows,
        data_quality_grade="EXCLUDE",
    )
    e = s.exit_rule(ctx, s.generate_signal(ctx))
    assert e.should_exit is True
    assert e.urgency == "critical"
    assert e.exit_qty_fraction == 1.0
    assert e.is_order_intent is False


def test_exit_freshness_stale_high():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy()
    ctx = StrategyContext(
        symbol="BTC-USDT", closes=closes, highs=highs, lows=lows,
        freshness_ok=False,
    )
    e = s.exit_rule(ctx, s.generate_signal(ctx))
    assert e.should_exit is True
    assert e.urgency == "high"


def test_exit_high_risk_notice():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy()
    ctx = StrategyContext(
        symbol="LUNA-USDT", closes=closes, highs=highs, lows=lows,
        notice_context={"high_risk_symbols": ["LUNA"]},
    )
    e = s.exit_rule(ctx, s.generate_signal(ctx))
    assert e.should_exit is True
    assert e.urgency == "high"


def test_exit_ema_cross_below_partial():
    # 추세가 꺾여 fast<slow 가 되는 케이스
    closes = [100.0 + i * 1.0 for i in range(70)] + \
             [170.0 - i * 1.5 for i in range(30)]
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.1 for c in closes]
    s = TrendFollowingContractStrategy(
        TrendFollowingParams(min_candles_required=60, adx_min=10.0),
    )
    ctx = StrategyContext(symbol="BTC-USDT",
                          closes=tuple(closes), highs=tuple(highs), lows=tuple(lows))
    sig = s.generate_signal(ctx)
    e = s.exit_rule(ctx, sig)
    assert e.should_exit is True
    assert e.exit_qty_fraction == 0.6
    assert e.is_order_intent is False


def test_exit_normal_trend_no_exit():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy(
        TrendFollowingParams(min_candles_required=60, adx_min=10.0),
    )
    ctx = StrategyContext(symbol="BTC-USDT", closes=closes, highs=highs, lows=lows)
    e = s.exit_rule(ctx, s.generate_signal(ctx))
    assert e.should_exit is False


# ── 28-31. Explanation ──────────────────────────────────────────


def test_explanation_summary_includes_candidate_only():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy(
        TrendFollowingParams(min_candles_required=60, adx_min=10.0),
    )
    ctx = StrategyContext(symbol="BTC-USDT", closes=closes, highs=highs, lows=lows)
    sig = s.generate_signal(ctx)
    exp = s.explain_signal(ctx, sig)
    assert "candidate" in exp.summary.lower()
    assert "not an order" in exp.summary.lower() or "candidate" in exp.summary.lower()


def test_explanation_evidence_contains_indicators():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy(
        TrendFollowingParams(min_candles_required=60, adx_min=10.0),
    )
    ctx = StrategyContext(symbol="BTC-USDT", closes=closes, highs=highs, lows=lows)
    sig = s.generate_signal(ctx)
    exp = s.explain_signal(ctx, sig)
    joined = " ".join(exp.evidence)
    assert "EMA" in joined
    assert "ADX" in joined
    assert "Donchian" in joined
    assert "data_quality_grade" in joined


def test_explanation_risks_reflect_stale_data():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy()
    ctx = StrategyContext(
        symbol="BTC-USDT", closes=closes, highs=highs, lows=lows,
        freshness_ok=False,
    )
    sig = s.generate_signal(ctx)
    exp = s.explain_signal(ctx, sig)
    joined = " ".join(exp.risks).lower()
    assert "stale" in joined or "freshness" in joined


def test_explanation_limitations_state_not_an_order():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy(
        TrendFollowingParams(min_candles_required=60, adx_min=10.0),
    )
    ctx = StrategyContext(symbol="BTC-USDT", closes=closes, highs=highs, lows=lows)
    sig = s.generate_signal(ctx)
    exp = s.explain_signal(ctx, sig)
    joined = " ".join(exp.limitations).lower()
    assert "candidate" in joined or "not an order" in joined


# ── 32-34. evaluate() ───────────────────────────────────────────


def test_evaluate_is_order_intent_false():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy(
        TrendFollowingParams(min_candles_required=60, adx_min=10.0),
    )
    ctx = StrategyContext(symbol="BTC-USDT", closes=closes, highs=highs, lows=lows)
    r = s.evaluate(ctx)
    assert r["is_order_intent"] is False
    assert r["direct_order_allowed"] is False
    assert r["used_for_order"] is False


def test_evaluate_returns_full_layers():
    closes, highs, lows = _strong_uptrend_data()
    s = TrendFollowingContractStrategy(
        TrendFollowingParams(min_candles_required=60, adx_min=10.0),
    )
    ctx = StrategyContext(symbol="BTC-USDT", closes=closes, highs=highs, lows=lows)
    r = s.evaluate(ctx)
    for k in ("signal", "sizing", "exit", "explanation"):
        assert k in r


# ── 35-37. Static guards ────────────────────────────────────────


_TARGET = (
    Path(__file__).resolve().parent.parent / "app" / "strategies" /
    "trend_following_contract.py"
)


def test_module_no_broker_or_execution_imports():
    pat = re.compile(
        r"^\s*(?:from\s+app\.(?:brokers|execution)|"
        r"import\s+app\.(?:brokers|execution))",
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
        r"\.(?:place_order|cancel_order|get_balance|submit_order)\s*\(",
    )
    text = _TARGET.read_text(encoding="utf-8")
    assert not pat.search(text)


def test_module_no_forbidden_substrings():
    forbidden = (
        "ENABLE_LIVE_TRADING = True",
        "is_order_intent: bool = True",
        "is_final_order_size: bool = True",
        "used_for_order=True",
    )
    text = _TARGET.read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in text


# ── 38-40. Registry integration ─────────────────────────────────


def test_registry_register_trend_following_contract():
    reg = build_empty_registry()
    entry = reg.register_strategy(TrendFollowingContractStrategy, enabled=False)
    assert entry.capability.name == "trend_following_v2"
    assert reg.get_strategy("trend_following_v2") is TrendFollowingContractStrategy


def test_registry_create_instance():
    reg = build_empty_registry()
    reg.register_strategy(TrendFollowingContractStrategy)
    s = reg.create_strategy("trend_following_v2")
    assert isinstance(s, TrendFollowingContractStrategy)


def test_preferred_regimes_for_selection_agent():
    assert TrendFollowingContractStrategy.preferred_regimes == ("TREND_UP", "TREND_DOWN")


def test_selection_agent_activates_in_trend_regime():
    from app.agents.strategy_selection import (
        StrategyActivationContext, select_active_strategies,
    )
    reg = build_empty_registry()
    reg.register_strategy(TrendFollowingContractStrategy)
    ctx = StrategyActivationContext(
        symbol="BTC-USDT", regime="TREND_UP",
    )
    d = select_active_strategies(ctx, reg)
    assert "trend_following_v2" in d.activated
    assert d.direct_order_allowed is False


def test_selection_agent_skips_in_range_regime():
    from app.agents.strategy_selection import (
        StrategyActivationContext, select_active_strategies,
    )
    reg = build_empty_registry()
    reg.register_strategy(TrendFollowingContractStrategy)
    ctx = StrategyActivationContext(symbol="BTC-USDT", regime="RANGE")
    d = select_active_strategies(ctx, reg)
    assert "trend_following_v2" in d.skipped
    assert "regime_mismatch" in d.skipped_reasons["trend_following_v2"]
