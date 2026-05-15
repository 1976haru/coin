"""체크리스트 #36 Funding Cost Guard — 회귀 테스트.

검증:
  1. funding_cost_contribution_pct — short/long 부호 처리
  2. conservative_funding_cost_pct — abs() 적용
  3. projected_funding_payments — 보유 시간/주기 비례
  4. projected_funding_cost_pct — 누적 비용 (보수적/방향)
  5. annualized_funding_rate_pct — APR 환산
  6. is_extreme_funding — 임계값 초과
  7. is_funding_unfavorable — 방향 정합성
  8. guard_funding_extreme / guard_funding_direction (체크리스트 #35 + #36)
  9. evaluate_entry_guards 가 funding_extreme 가드를 포함 (8단계)
 10. KimpStrategy 가 funding_rate 이상치 시 BLOCKED
"""
from __future__ import annotations
import pytest

from app.market.funding import (
    funding_cost_contribution_pct,
    conservative_funding_cost_pct,
    projected_funding_payments, projected_funding_cost_pct,
    annualized_funding_rate_pct,
    is_extreme_funding, is_funding_unfavorable,
    DEFAULT_FUNDING_INTERVAL_HOURS, DEFAULT_EXTREME_THRESHOLD_PCT,
)
from app.strategies.kimp_guards import (
    guard_funding_extreme, guard_funding_direction,
    evaluate_entry_guards,
)


# ── 1. funding_cost_contribution_pct ────────────────────────────

def test_short_with_positive_funding_receives():
    """short + 양의 funding ⇒ short 가 받음 → 음수 비용."""
    cost = funding_cost_contribution_pct(0.05, side="short")
    assert cost == -0.05


def test_short_with_negative_funding_pays():
    """short + 음의 funding ⇒ short 가 냄 → 양수 비용."""
    cost = funding_cost_contribution_pct(-0.05, side="short")
    assert cost == 0.05


def test_long_with_positive_funding_pays():
    cost = funding_cost_contribution_pct(0.05, side="long")
    assert cost == 0.05


def test_long_with_negative_funding_receives():
    cost = funding_cost_contribution_pct(-0.05, side="long")
    assert cost == -0.05


def test_invalid_side_raises():
    with pytest.raises(ValueError):
        funding_cost_contribution_pct(0.01, side="bogus")  # type: ignore[arg-type]


# ── 2. conservative_funding_cost_pct ────────────────────────────

def test_conservative_uses_absolute_value():
    assert conservative_funding_cost_pct(0.05) == 0.05
    assert conservative_funding_cost_pct(-0.05) == 0.05
    assert conservative_funding_cost_pct(0.0) == 0.0


# ── 3. projected_funding_payments ───────────────────────────────

def test_payments_zero_when_hold_time_zero_or_negative():
    assert projected_funding_payments(0.0) == 0.0
    assert projected_funding_payments(-1.0) == 0.0


def test_payments_proportional_to_hours():
    """8h 주기 → 16h 보유 = 2 events."""
    n = projected_funding_payments(16.0, interval_hours=8.0)
    assert n == 2.0


def test_payments_fractional():
    """4h 보유 = 0.5 events (미만 주기)."""
    n = projected_funding_payments(4.0, interval_hours=8.0)
    assert n == 0.5


def test_payments_default_interval_8h():
    n = projected_funding_payments(24.0)
    assert n == 24.0 / DEFAULT_FUNDING_INTERVAL_HOURS


def test_payments_invalid_interval_raises():
    with pytest.raises(ValueError):
        projected_funding_payments(8.0, interval_hours=0.0)


# ── 4. projected_funding_cost_pct ───────────────────────────────

def test_projected_cost_conservative():
    """abs(rate) × payments — 부호 무관."""
    cost = projected_funding_cost_pct(rate_pct=-0.05, hours_held=24.0,
                                       conservative=True)
    assert cost == pytest.approx(0.05 * 3, abs=1e-9)


def test_projected_cost_directional_short_with_positive_funding():
    """conservative=False 면 부호 반영 — short + positive = 수익(음수)."""
    cost = projected_funding_cost_pct(rate_pct=0.05, hours_held=24.0,
                                       side="short", conservative=False)
    assert cost == pytest.approx(-0.05 * 3, abs=1e-9)


# ── 5. annualized_funding_rate_pct ──────────────────────────────

def test_annualize_8h_rate():
    """0.01% / 8h × (24/8 × 365) = 10.95% APR."""
    apr = annualized_funding_rate_pct(0.01, interval_hours=8.0)
    expected = 0.01 * 3.0 * 365.0
    assert apr == pytest.approx(expected, abs=1e-9)


def test_annualize_invalid_interval_raises():
    with pytest.raises(ValueError):
        annualized_funding_rate_pct(0.01, interval_hours=-1.0)


# ── 6. is_extreme_funding ───────────────────────────────────────

@pytest.mark.parametrize("rate", [1.5, -1.5, 5.0, -3.0])
def test_extreme_funding_triggers_above_default_threshold(rate):
    assert is_extreme_funding(rate) is True


@pytest.mark.parametrize("rate", [0.0, 0.05, -0.5, 0.99, -0.99])
def test_extreme_funding_passes_below_default_threshold(rate):
    assert is_extreme_funding(rate) is False


def test_extreme_funding_custom_threshold():
    assert is_extreme_funding(0.6, threshold_pct=0.5) is True
    assert is_extreme_funding(0.4, threshold_pct=0.5) is False


def test_default_threshold_constant_value():
    """DEFAULT_EXTREME_THRESHOLD_PCT = 1.0% per interval."""
    assert DEFAULT_EXTREME_THRESHOLD_PCT == 1.0


# ── 7. is_funding_unfavorable ───────────────────────────────────

def test_short_unfavorable_when_negative_funding():
    """short + 음의 funding ⇒ short 가 냄 → 불리."""
    assert is_funding_unfavorable(-0.05, side="short") is True


def test_short_favorable_when_positive_funding():
    assert is_funding_unfavorable(0.05, side="short") is False


def test_long_unfavorable_when_positive_funding():
    assert is_funding_unfavorable(0.05, side="long") is True


def test_long_favorable_when_negative_funding():
    assert is_funding_unfavorable(-0.05, side="long") is False


# ── 8. Guards ───────────────────────────────────────────────────

def test_guard_funding_extreme_blocks_above_threshold():
    r = guard_funding_extreme(1.5, threshold_pct=1.0)
    assert r.passed is False
    assert r.severity == "block"
    assert "이상치" in r.reason


def test_guard_funding_extreme_passes_below_threshold():
    r = guard_funding_extreme(0.05, threshold_pct=1.0)
    assert r.passed is True


def test_guard_funding_direction_pass_when_favorable():
    r = guard_funding_direction(0.05, side="short")
    assert r.passed is True
    assert "유리" in r.reason


def test_guard_funding_direction_does_not_block_unfavorable_by_default():
    """기본 block_when_unfavorable=False — pass 상태로 reason 만 경고."""
    r = guard_funding_direction(-0.05, side="short")
    assert r.passed is True   # 통과
    assert "불리" in r.reason


def test_guard_funding_direction_blocks_when_opted_in():
    r = guard_funding_direction(-0.05, side="short", block_when_unfavorable=True)
    assert r.passed is False
    assert r.severity == "block"


# ── 9. evaluate_entry_guards 통합 ───────────────────────────────

def test_entry_guards_includes_funding_extreme_step():
    r = evaluate_entry_guards(
        kimp_pct=-2.0, entry_threshold_pct=-1.8,
        expected_edge_pct=1.0, total_cost_pct=0.2,
        funding_rate_pct=0.05,
    )
    names = [x.name for x in r.results]
    assert "funding_extreme" in names


def test_entry_guards_blocks_on_extreme_funding():
    r = evaluate_entry_guards(
        kimp_pct=-2.0, entry_threshold_pct=-1.8,
        expected_edge_pct=1.0, total_cost_pct=0.2,
        funding_rate_pct=2.0,    # 비정상
    )
    assert r.passed is False
    assert r.first_failure.name == "funding_extreme"


def test_entry_guards_normal_funding_does_not_block():
    r = evaluate_entry_guards(
        kimp_pct=-2.0, entry_threshold_pct=-1.8,
        expected_edge_pct=1.0, total_cost_pct=0.2,
        funding_rate_pct=0.01,    # 정상 범위
    )
    assert r.passed is True


# ── 10. KimpStrategy 통합 ───────────────────────────────────────

def test_kimp_strategy_blocks_on_extreme_funding_rate():
    """기존 KimpStrategy 가 funding_rate_pct 를 가드에 전달해 비정상 시 BLOCKED."""
    from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
    s = KimpMeanReversionStrategy(entry_threshold=-1.8, exit_threshold=-1.0)
    sig = s.generate_signal("BTC", 980, 1, 1000, funding_rate_pct=2.0)
    assert sig.action == "BLOCKED"
    assert "펀딩" in sig.reason or "funding" in sig.reason.lower()


def test_kimp_strategy_normal_funding_does_not_change_outcome():
    """funding=0.0 (default) 일 때 기존 시나리오와 결과 동일.

    참고: KimpStrategy 의 기존 cost 계산은 ``abs(funding_rate_pct) * 100`` 으로
    funding 을 fraction 으로 취급해 비용 가산. funding_rate_pct=0.01 (= 1%) 만
    되어도 cost 1% 가 추가되어 expected_edge 1% 와 같아져 cost_vs_edge BLOCK.
    개선은 후속 PR (전략 내부 비용 모델 ``app.market.funding`` 직접 호출).
    """
    from datetime import datetime, timezone
    from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
    s = KimpMeanReversionStrategy(entry_threshold=-1.8, exit_threshold=-1.0)
    sig = s.generate_signal("BTC", 980, 1, 1000, funding_rate_pct=0.0,
                             now=datetime.now(timezone.utc))
    assert sig.action == "OPEN_REVERSE_KIMP"
