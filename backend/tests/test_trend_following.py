"""체크리스트 #30 Trend Following — 분리 + 동작 회귀 테스트.

검증:
  1. TrendFollowingStrategy 가 ``app.strategies.trend_following`` 에서 import 가능 (정규 위치)
  2. ``app.strategies.strategies`` 에서도 import 가능 (backward compat 재export)
  3. 두 경로의 클래스 객체가 동일 (alias 가 아닌 동일 객체)
  4. ``StrategySignal`` 이 ``_signals`` 에 위치하고 schemas 에서도 동일
  5. ``_indicators`` 의 ema/sma/atr 가 분리되었고 결정론적
  6. capability + 신호 contract 유지 (#29)
  7. 동작 동일성 — 분리 전후 generate 출력 동일
"""
from __future__ import annotations
import pytest


# ── 1. 분리 / Backward compat ────────────────────────────────────

def test_trend_following_importable_from_canonical_location():
    from app.strategies.trend_following import TrendFollowingStrategy
    assert TrendFollowingStrategy is not None


def test_trend_following_importable_from_strategies_module():
    from app.strategies.strategies import TrendFollowingStrategy
    assert TrendFollowingStrategy is not None


def test_canonical_and_legacy_paths_resolve_to_same_class():
    from app.strategies.trend_following import TrendFollowingStrategy as A
    from app.strategies.strategies   import TrendFollowingStrategy as B
    assert A is B


def test_trend_following_importable_from_package():
    from app.strategies import TrendFollowingStrategy
    assert TrendFollowingStrategy is not None


# ── 2. _signals 위치 ────────────────────────────────────────────

def test_strategy_signal_canonical_location_is_signals():
    from app.strategies._signals import StrategySignal as A
    from app.strategies.strategies import StrategySignal as B  # 재export
    from app.schemas import StrategySignal as C                  # schemas 진입점
    assert A is B
    assert A is C


# ── 3. _indicators 분리 ─────────────────────────────────────────

def test_indicators_module_provides_public_functions():
    from app.strategies._indicators import ema, sma, atr
    # 결정론
    assert ema([1, 2, 3, 4], 2) == ema([1, 2, 3, 4], 2)
    assert sma([1, 2, 3, 4, 5], 3) == 4.0
    # 빈 입력 안전
    assert ema([], 5) == 0.0
    assert sma([], 5) == 0.0
    assert atr([], [], [], 14) == 0.0


def test_indicators_module_keeps_legacy_underscore_aliases():
    """기존 strategies.py 내부의 _ema/_sma/_atr 호환 alias."""
    from app.strategies._indicators import _ema, _sma, _atr, ema, sma, atr
    assert _ema is ema
    assert _sma is sma
    assert _atr is atr


# ── 4. capability 유지 (체크리스트 #29 contract) ─────────────────

def test_trend_following_keeps_capability():
    from app.strategies.trend_following import TrendFollowingStrategy
    cap = TrendFollowingStrategy.capability
    assert cap.name == "trend_following"
    assert "EMA" in cap.description or "추세" in cap.description
    assert cap.output_signal_class == "StrategySignal"


def test_trend_following_satisfies_strategy_base_protocol():
    from app.strategies import StrategyBase
    from app.strategies.trend_following import TrendFollowingStrategy
    assert isinstance(TrendFollowingStrategy(), StrategyBase)


# ── 5. 동작 회귀 — 분리 전 동일 입력에 동일 출력 ────────────────

def test_trend_following_uptrend_buy_signal():
    from app.strategies.trend_following import TrendFollowingStrategy
    s = TrendFollowingStrategy()
    closes = list(range(100, 200))  # 강한 상승
    sig = s.generate(closes, adx=25.0, volume_ratio=1.5)
    assert sig.action == "BUY"
    assert sig.confidence > 0.5
    assert sig.entry_price > 0
    assert sig.is_order_intent is False


def test_trend_following_low_adx_returns_hold():
    from app.strategies.trend_following import TrendFollowingStrategy
    s = TrendFollowingStrategy(adx_min=18.0)
    closes = list(range(100, 200))
    sig = s.generate(closes, adx=10.0)  # 횡보장
    assert sig.action == "HOLD"
    assert "횡보장" in sig.reason or "ADX" in sig.reason


def test_trend_following_insufficient_data_returns_hold():
    from app.strategies.trend_following import TrendFollowingStrategy
    s = TrendFollowingStrategy()
    sig = s.generate([100, 101, 102])  # 데이터 부족
    assert sig.action == "HOLD"
    assert "부족" in sig.reason


def test_trend_following_signal_has_is_order_intent_false():
    from app.strategies.trend_following import TrendFollowingStrategy
    s = TrendFollowingStrategy()
    sig = s.generate(list(range(100, 200)), adx=25.0, volume_ratio=1.5)
    assert sig.is_order_intent is False  # CLAUDE.md §3.2


# ── 6. 모듈 경계 ─────────────────────────────────────────────────

def test_trend_following_module_does_not_import_brokers_or_execution():
    from pathlib import Path
    REPO_ROOT = Path(__file__).resolve().parents[2]
    text = (REPO_ROOT / "backend" / "app" / "strategies" / "trend_following.py"
            ).read_text(encoding="utf-8")
    for line in text.splitlines():
        s = line.strip()
        if not (s.startswith("import ") or s.startswith("from ")):
            continue
        for forbidden in ("app.brokers", "app.execution"):
            assert forbidden not in s, \
                f"trend_following.py 에 forbidden import: {s}"
