"""체크리스트 #32 Pair Trading — 분리 + 동작 회귀 테스트.

검증:
  1. PairTradingStrategy/PairSignal 정규/레거시 경로 모두 import (모두 동일 객체)
  2. capability + StrategyBase Protocol 만족
  3. 동작 회귀 — z-score 시나리오별 action
  4. 모듈 경계
  5. catalog API 4개 유지 + supports_pair=True 표시
  6. strategies.py 가 단순 재export 허브로 축소되었는지
"""
from __future__ import annotations
from pathlib import Path
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


# ── 1. 분리 / Backward compat — 5경로 동일 객체 ──────────────────

def test_pair_trading_canonical_import():
    from app.strategies.pair_trading import PairTradingStrategy
    assert PairTradingStrategy is not None


def test_pair_strategy_paths_resolve_to_same_class():
    from app.strategies.pair_trading import PairTradingStrategy as A
    from app.strategies.strategies   import PairTradingStrategy as B
    from app.strategies              import PairTradingStrategy as C
    assert A is B is C


def test_pair_signal_canonical_location_is_signals():
    from app.strategies._signals    import PairSignal as A
    from app.strategies.pair_trading import PairSignal as B
    from app.strategies.strategies   import PairSignal as C
    from app.schemas                  import PairSignal as D
    assert A is B is C is D


# ── 2. capability + Protocol ─────────────────────────────────────

def test_pair_strategy_capability_metadata():
    from app.strategies.pair_trading import PairTradingStrategy
    cap = PairTradingStrategy.capability
    assert cap.name == "pair_trading"
    assert cap.supports_pair is True
    assert cap.output_signal_class == "PairSignal"


def test_satisfies_strategy_base_protocol():
    from app.strategies import StrategyBase
    from app.strategies.pair_trading import PairTradingStrategy
    assert isinstance(PairTradingStrategy(), StrategyBase)


# ── 3. 동작 회귀 ─────────────────────────────────────────────────

def test_pair_returns_hold_when_data_insufficient():
    from app.strategies.pair_trading import PairTradingStrategy
    s = PairTradingStrategy()
    sig = s.generate([100.0] * 10, [100.0] * 10)
    assert sig.action == "HOLD"
    assert "부족" in sig.reason


def test_pair_close_when_z_within_exit_band():
    """z 가 exit_z 이내 → CLOSE (평균회귀 달성)."""
    from app.strategies.pair_trading import PairTradingStrategy
    s = PairTradingStrategy(entry_z=2.0, exit_z=0.5)
    # 거의 동일한 두 시리즈 → spread 분산 ≈ 0, z ≈ 0
    a = [100.0 + 0.01 * i for i in range(60)]
    b = [a[i] + 0.001 for i in range(60)]
    sig = s.generate(a, b)
    assert sig.action == "CLOSE"


def test_pair_open_long_a_short_b_when_z_negative():
    """A 가 매우 침체(z << -entry_z) → OPEN_LONG_A_SHORT_B."""
    from app.strategies.pair_trading import PairTradingStrategy
    s = PairTradingStrategy(entry_z=2.0, exit_z=0.5, window=60)
    # B 는 일정, A 는 마지막 봉 급락
    a = [100.0] * 59 + [50.0]
    b = [100.0] * 60
    sig = s.generate(a, b)
    assert sig.action == "OPEN_LONG_A_SHORT_B"
    assert sig.z_score < 0
    assert sig.is_order_intent is False


def test_pair_open_short_a_long_b_when_z_positive():
    """A 가 매우 과열 → OPEN_SHORT_A_LONG_B."""
    from app.strategies.pair_trading import PairTradingStrategy
    s = PairTradingStrategy(entry_z=2.0, exit_z=0.5, window=60)
    a = [100.0] * 59 + [200.0]
    b = [100.0] * 60
    sig = s.generate(a, b)
    assert sig.action == "OPEN_SHORT_A_LONG_B"
    assert sig.z_score > 0


# ── 4. 모듈 경계 ─────────────────────────────────────────────────

def test_pair_trading_module_does_not_import_brokers_or_execution():
    text = (REPO_ROOT / "backend" / "app" / "strategies" / "pair_trading.py"
            ).read_text(encoding="utf-8")
    for line in text.splitlines():
        s = line.strip()
        if not (s.startswith("import ") or s.startswith("from ")):
            continue
        for forbidden in ("app.brokers", "app.execution"):
            assert forbidden not in s, \
                f"pair_trading.py forbidden import: {s}"


# ── 5. catalog 4개 유지 ──────────────────────────────────────────

def test_strategies_catalog_lists_pair_trading():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    body = client.get("/api/strategies/catalog").json()
    assert body["count"] == 4
    pair = next(s for s in body["strategies"] if s["name"] == "pair_trading")
    assert pair["supports_pair"] is True
    assert pair["output_signal_class"] == "PairSignal"


# ── 6. strategies.py 가 단순 재export 허브 ──────────────────────

def test_strategies_py_is_thin_reexport_hub():
    """체크리스트 #30/#31/#32 완료 후 strategies.py 는 본문 정의가 없어야 함."""
    text = (REPO_ROOT / "backend" / "app" / "strategies" / "strategies.py"
            ).read_text(encoding="utf-8")
    # 클래스/dataclass 정의 부재
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("class ") or stripped.startswith("@dataclass"):
            assert False, f"strategies.py 에 정의 잔존: {stripped}"
    # def 정의도 없어야 함
    for line in text.splitlines():
        if line.strip().startswith("def "):
            assert False, f"strategies.py 에 def 잔존: {line.strip()}"
