"""체크리스트 #35 Kimp Guards — 회귀 테스트.

검증:
  1. 개별 가드 7종 (entry_threshold, deposit_withdrawal, fx_anomaly,
     kimp_anomaly, liquidity, bull_market, cost_vs_edge)
  2. evaluate_entry_guards — 평가 순서 + 첫 실패 + severity
  3. EntryGuardsReport — to_dict / reason / passed
  4. KimpStrategy 위임 후 동작 회귀 — 기존 시나리오 모두 동일
  5. 새 kimp_anomaly 가드가 strategy 에 추가되어 이상 김프 차단
"""
from __future__ import annotations
import pytest

from app.strategies.kimp_guards import (
    GuardResult, EntryGuardsReport,
    guard_entry_threshold, guard_deposit_withdrawal, guard_fx_anomaly,
    guard_kimp_anomaly, guard_liquidity, guard_bull_market, guard_cost_vs_edge,
    evaluate_entry_guards,
)


# ── 1. 개별 가드 ─────────────────────────────────────────────────

def test_guard_entry_threshold_passes_when_below_threshold():
    r = guard_entry_threshold(kimp_pct=-2.0, entry_threshold_pct=-1.8)
    assert r.passed is True
    assert r.severity == "pass"
    assert r.name == "entry_threshold"


def test_guard_entry_threshold_holds_when_above_threshold():
    r = guard_entry_threshold(kimp_pct=-1.0, entry_threshold_pct=-1.8)
    assert r.passed is False
    assert r.severity == "hold"
    assert "미달" in r.reason


def test_guard_entry_threshold_passes_at_exact_threshold():
    r = guard_entry_threshold(kimp_pct=-1.8, entry_threshold_pct=-1.8)
    assert r.passed is True


def test_guard_deposit_withdrawal_blocks_when_false():
    r = guard_deposit_withdrawal(False)
    assert r.passed is False
    assert r.severity == "block"
    assert "입출금" in r.reason


def test_guard_deposit_withdrawal_passes_when_true():
    r = guard_deposit_withdrawal(True)
    assert r.passed is True
    assert r.severity == "pass"


def test_guard_fx_anomaly_blocks_when_false():
    r = guard_fx_anomaly(False)
    assert r.passed is False
    assert r.severity == "block"
    assert "환율 이상치" in r.reason


def test_guard_fx_anomaly_passes_when_true():
    r = guard_fx_anomaly(True)
    assert r.passed is True


@pytest.mark.parametrize("pct", [-15.0, -11.0, 11.0, 50.0])
def test_guard_kimp_anomaly_blocks_when_outside_default_range(pct):
    r = guard_kimp_anomaly(pct)
    assert r.passed is False
    assert r.severity == "block"


@pytest.mark.parametrize("pct", [-9.5, -2.0, 0.0, 5.0, 9.5])
def test_guard_kimp_anomaly_passes_inside_default_range(pct):
    r = guard_kimp_anomaly(pct)
    assert r.passed is True


def test_guard_kimp_anomaly_custom_range():
    r = guard_kimp_anomaly(3.0, abnormal_min=-2.0, abnormal_max=2.0)
    assert r.passed is False


def test_guard_liquidity_blocks_when_false():
    r = guard_liquidity(False)
    assert r.passed is False
    assert r.severity == "block"
    assert "유동성" in r.reason or "거래량" in r.reason


def test_guard_bull_market_blocks_when_true():
    r = guard_bull_market(True)
    assert r.passed is False
    assert r.severity == "block"
    assert "강등" in r.reason or "강등장" in r.reason or "강세" in r.reason or "급등장" in r.reason


def test_guard_bull_market_passes_when_false():
    r = guard_bull_market(False)
    assert r.passed is True


def test_guard_cost_vs_edge_blocks_when_cost_exceeds_edge():
    r = guard_cost_vs_edge(expected_edge_pct=0.1, total_cost_pct=0.3)
    assert r.passed is False
    assert r.severity == "block"
    assert "비용" in r.reason


def test_guard_cost_vs_edge_passes_when_edge_exceeds_cost():
    r = guard_cost_vs_edge(expected_edge_pct=0.5, total_cost_pct=0.3)
    assert r.passed is True


def test_guard_cost_vs_edge_blocks_when_equal():
    """엣지 == 비용 도 차단 (수익 0)."""
    r = guard_cost_vs_edge(expected_edge_pct=0.3, total_cost_pct=0.3)
    assert r.passed is False


# ── 2. evaluate_entry_guards 집계 ─────────────────────────────────

def test_all_guards_pass_returns_report_passed():
    r = evaluate_entry_guards(
        kimp_pct=-2.0, entry_threshold_pct=-1.8,
        expected_edge_pct=1.0, total_cost_pct=0.2,
    )
    assert r.passed is True
    assert r.severity == "pass"
    assert r.first_failure is None
    assert "통과" in r.reason
    assert len(r.results) == 8   # entry+deposit+fx+kimp+liq+bull+funding+cost (#36)


def test_first_failure_is_entry_threshold_when_kimp_above():
    r = evaluate_entry_guards(
        kimp_pct=-1.0, entry_threshold_pct=-1.8,
    )
    assert r.passed is False
    assert r.severity == "hold"
    assert r.first_failure.name == "entry_threshold"


def test_first_failure_is_deposit_when_blocked_after_threshold_pass():
    """entry_threshold 통과 후 deposit 차단."""
    r = evaluate_entry_guards(
        kimp_pct=-2.0, entry_threshold_pct=-1.8,
        deposit_withdrawal_ok=False,
    )
    assert r.passed is False
    assert r.severity == "block"
    assert r.first_failure.name == "deposit_withdrawal"


def test_evaluate_short_circuits_at_first_failure_severity():
    """여러 실패가 있어도 첫 실패의 severity 가 보고."""
    r = evaluate_entry_guards(
        kimp_pct=-2.0, entry_threshold_pct=-1.8,
        deposit_withdrawal_ok=False,
        fx_anomaly_ok=False,            # 두 번째 실패
        liquidity_ok=False,             # 세 번째 실패
    )
    assert r.first_failure.name == "deposit_withdrawal"


def test_results_always_contains_eight_evaluations():
    """단락 없이 모두 평가 — 감사 가시성. 8단계 (#36 funding 추가)."""
    r = evaluate_entry_guards(
        kimp_pct=-2.0, entry_threshold_pct=-1.8,
        deposit_withdrawal_ok=False,
    )
    assert len(r.results) == 8
    pass_count = sum(1 for x in r.results if x.passed)
    fail_count = sum(1 for x in r.results if not x.passed)
    assert pass_count + fail_count == 8


def test_evaluate_blocks_on_kimp_anomaly_even_with_other_ok():
    r = evaluate_entry_guards(
        kimp_pct=-15.0,             # 이상치
        entry_threshold_pct=-1.8,    # 통과 (-15 < -1.8)
        expected_edge_pct=1.0, total_cost_pct=0.2,
    )
    assert r.passed is False
    assert r.first_failure.name == "kimp_anomaly"
    assert r.severity == "block"


def test_evaluate_blocks_on_cost_vs_edge_last_check():
    """비용/엣지가 마지막 가드 — 다른 가드 모두 통과 후에만 평가."""
    r = evaluate_entry_guards(
        kimp_pct=-2.0, entry_threshold_pct=-1.8,
        expected_edge_pct=0.1, total_cost_pct=0.5,
    )
    assert r.passed is False
    assert r.first_failure.name == "cost_vs_edge"


# ── 3. EntryGuardsReport 직렬화 ─────────────────────────────────

def test_report_to_dict_structure():
    r = evaluate_entry_guards(
        kimp_pct=-1.0, entry_threshold_pct=-1.8,
    )
    d = r.to_dict()
    assert d["passed"] is False
    assert d["severity"] == "hold"
    assert d["first_failure"]["name"] == "entry_threshold"
    assert len(d["all_results"]) == 8
    for x in d["all_results"]:
        assert {"name", "passed", "severity", "reason"}.issubset(x.keys())


def test_report_reason_is_first_failure_or_pass_text():
    r_fail = evaluate_entry_guards(kimp_pct=-1.0, entry_threshold_pct=-1.8)
    assert "미달" in r_fail.reason
    r_pass = evaluate_entry_guards(
        kimp_pct=-2.0, entry_threshold_pct=-1.8,
        expected_edge_pct=1.0, total_cost_pct=0.2,
    )
    assert "통과" in r_pass.reason


# ── 4. Strategy 위임 후 동작 회귀 ────────────────────────────────

def test_kimp_strategy_open_signal_unchanged_after_refactor():
    from datetime import datetime, timezone
    from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
    s = KimpMeanReversionStrategy(entry_threshold=-1.8, exit_threshold=-1.0)
    sig = s.generate_signal("BTC", 980, 1, 1000, now=datetime.now(timezone.utc))
    assert sig.action == "OPEN_REVERSE_KIMP"
    assert sig.kimp_pct == pytest.approx(-2.0, abs=1e-6)


def test_kimp_strategy_hold_when_threshold_not_met():
    from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
    s = KimpMeanReversionStrategy(entry_threshold=-1.8)
    # kimp = -1.0% (entry -1.8 미달)
    sig = s.generate_signal("BTC", 990, 1, 1000)
    assert sig.action == "HOLD"
    assert "미달" in sig.reason


def test_kimp_strategy_blocks_when_deposit_off():
    from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
    s = KimpMeanReversionStrategy(entry_threshold=-1.8)
    sig = s.generate_signal("BTC", 980, 1, 1000, deposit_withdrawal_ok=False)
    assert sig.action == "BLOCKED"
    assert "입출금" in sig.reason


def test_kimp_strategy_blocks_when_fx_anomaly():
    from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
    s = KimpMeanReversionStrategy(entry_threshold=-1.8)
    sig = s.generate_signal("BTC", 980, 1, 1000, fx_anomaly_ok=False)
    assert sig.action == "BLOCKED"


def test_kimp_strategy_blocks_when_liquidity_off():
    from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
    s = KimpMeanReversionStrategy(entry_threshold=-1.8)
    sig = s.generate_signal("BTC", 980, 1, 1000, liquidity_ok=False)
    assert sig.action == "BLOCKED"


def test_kimp_strategy_blocks_when_bull_market():
    from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
    s = KimpMeanReversionStrategy(entry_threshold=-1.8)
    sig = s.generate_signal("BTC", 980, 1, 1000, bull_market_block=True)
    assert sig.action == "BLOCKED"


def test_kimp_strategy_blocks_when_cost_exceeds_edge():
    """기존 회귀 — 비용 ≥ 엣지 시 BLOCKED."""
    from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
    s = KimpMeanReversionStrategy(entry_threshold=-1.8, exit_threshold=-1.0)
    sig = s.generate_signal("BTC", 980, 1, 1000,
                             upbit_spread_pct=0.02, okx_spread_pct=0.02)
    assert sig.action == "BLOCKED"


# ── 5. 새 kimp_anomaly 가드 효과 ────────────────────────────────

def test_kimp_strategy_blocks_when_kimp_is_anomalous():
    """새로 추가된 #35 가드 — 김프율 이상치 (>±10%)."""
    from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
    s = KimpMeanReversionStrategy(entry_threshold=-1.8)
    # upbit=500, okx=1, fx=1000 → kimp = -50% (이상치)
    sig = s.generate_signal("BTC", 500, 1, 1000)
    assert sig.action == "BLOCKED"
    assert "이상치" in sig.reason
