"""체크리스트 #31 Volatility Breakout — 분리 + 동작 회귀 테스트.

검증:
  1. VolatilityBreakoutStrategy 정규/레거시 경로 모두 import 가능 + 동일 객체
  2. capability + StrategyBase Protocol 만족 (#29)
  3. 동작 회귀 — 분리 전 동일 입력에 동일 출력
  4. 모듈 경계 — brokers/execution import 금지
"""
from __future__ import annotations
from pathlib import Path
import pytest


# ── 1. 분리 / Backward compat ────────────────────────────────────

def test_volatility_breakout_canonical_import():
    from app.strategies.volatility_breakout import VolatilityBreakoutStrategy
    assert VolatilityBreakoutStrategy is not None


def test_volatility_breakout_legacy_import_still_works():
    from app.strategies.strategies import VolatilityBreakoutStrategy
    assert VolatilityBreakoutStrategy is not None


def test_canonical_and_legacy_paths_resolve_to_same_class():
    from app.strategies.volatility_breakout import VolatilityBreakoutStrategy as A
    from app.strategies.strategies         import VolatilityBreakoutStrategy as B
    assert A is B


def test_volatility_breakout_importable_from_package():
    from app.strategies import VolatilityBreakoutStrategy
    assert VolatilityBreakoutStrategy is not None


# ── 2. capability + Protocol ─────────────────────────────────────

def test_capability_metadata():
    from app.strategies.volatility_breakout import VolatilityBreakoutStrategy
    cap = VolatilityBreakoutStrategy.capability
    assert cap.name == "volatility_breakout"
    assert cap.output_signal_class == "StrategySignal"
    assert "돌파" in cap.description or "breakout" in cap.description.lower()


def test_satisfies_strategy_base_protocol():
    from app.strategies import StrategyBase
    from app.strategies.volatility_breakout import VolatilityBreakoutStrategy
    assert isinstance(VolatilityBreakoutStrategy(), StrategyBase)


# ── 3. 동작 회귀 ─────────────────────────────────────────────────

def _make_synthetic_breakout_data(n: int = 60, breakout_factor: float = 1.05):
    """마지막 봉만 큰 양봉 — 명확한 BUY 시나리오."""
    closes = [100.0 + 0.1 * i for i in range(n - 1)]
    highs  = [c + 0.5 for c in closes]
    lows   = [c - 0.5 for c in closes]
    last_close = closes[-1] * breakout_factor
    closes.append(last_close)
    highs.append(last_close + 1)
    lows.append(last_close - 1)
    return closes, highs, lows


def test_breakout_buy_when_volume_surges_and_price_breaks():
    from app.strategies.volatility_breakout import VolatilityBreakoutStrategy
    s = VolatilityBreakoutStrategy()
    closes, highs, lows = _make_synthetic_breakout_data()
    sig = s.generate(closes, highs, lows, volume_ratio=1.5)
    assert sig.action == "BUY"
    assert sig.confidence > 0
    assert sig.is_order_intent is False  # CLAUDE.md §3.2


def test_breakdown_sell_when_price_drops_below_prev_low():
    from app.strategies.volatility_breakout import VolatilityBreakoutStrategy
    s = VolatilityBreakoutStrategy()
    closes = [100.0 + 0.1 * i for i in range(59)]
    highs  = [c + 0.5 for c in closes]
    lows   = [c - 0.5 for c in closes]
    # 마지막 봉을 전일 저점 아래로
    last = min(lows[-26:-1]) * 0.95
    closes.append(last)
    highs.append(last + 0.5)
    lows.append(last - 0.5)
    sig = s.generate(closes, highs, lows, volume_ratio=1.5)
    assert sig.action == "SELL"


def test_hold_when_volume_too_low():
    from app.strategies.volatility_breakout import VolatilityBreakoutStrategy
    s = VolatilityBreakoutStrategy()
    closes, highs, lows = _make_synthetic_breakout_data()
    sig = s.generate(closes, highs, lows, volume_ratio=0.8)  # surge 미달
    assert sig.action == "HOLD"
    assert "거래량" in sig.reason


def test_hold_on_insufficient_data():
    from app.strategies.volatility_breakout import VolatilityBreakoutStrategy
    s = VolatilityBreakoutStrategy()
    sig = s.generate([100.0] * 5, [101.0] * 5, [99.0] * 5, volume_ratio=2.0)
    assert sig.action == "HOLD"
    assert "부족" in sig.reason


def test_signal_is_order_intent_false_by_default():
    from app.strategies.volatility_breakout import VolatilityBreakoutStrategy
    s = VolatilityBreakoutStrategy()
    closes, highs, lows = _make_synthetic_breakout_data()
    sig = s.generate(closes, highs, lows, volume_ratio=1.5)
    assert sig.is_order_intent is False


# ── 4. 모듈 경계 ─────────────────────────────────────────────────

def test_module_does_not_import_brokers_or_execution():
    repo_root = Path(__file__).resolve().parents[2]
    text = (repo_root / "backend" / "app" / "strategies" / "volatility_breakout.py"
            ).read_text(encoding="utf-8")
    for line in text.splitlines():
        s = line.strip()
        if not (s.startswith("import ") or s.startswith("from ")):
            continue
        for forbidden in ("app.brokers", "app.execution"):
            assert forbidden not in s, \
                f"volatility_breakout.py 에 forbidden import: {s}"


# ── 5. catalog API 가 여전히 4개 ─────────────────────────────────

def test_strategies_catalog_still_lists_four_after_split():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.get("/api/strategies/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 4
    names = {s["name"] for s in body["strategies"]}
    assert "volatility_breakout" in names
