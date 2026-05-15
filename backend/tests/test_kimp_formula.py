"""체크리스트 #34 Kimp Formula — 회귀 테스트.

검증:
  1. compute_kimp_pct 공식 정확성 (정/역김프, 양/음 부호)
  2. strict=True 시 비정상 입력 ValueError
  3. strict=False (default) 시 비정상 입력 0.0
  4. assess_kimp — KimpResult 패키징
  5. 단일 진리 소스 — KimpSnapshot.compute_kimp / KimpMeanReversionStrategy.calculate_kimp 가 본 모듈로 위임
  6. breakeven_threshold_pct — 비용 합산
  7. expected_edge_pct — 거리 계산
  8. is_anomaly — 이상 범위 판정
  9. 기존 동작 회귀 — 두 위임 함수가 분리 전과 동일 결과
"""
from __future__ import annotations
import pytest

from app.market.kimp import (
    compute_kimp_pct, assess_kimp, KimpResult,
    breakeven_threshold_pct, expected_edge_pct, is_anomaly,
)


# ── 1. 공식 정확성 ───────────────────────────────────────────────

def test_kimp_zero_when_prices_match():
    """upbit_krw == okx_usdt × fx 면 김프율 0%."""
    assert compute_kimp_pct(1000.0, 1.0, 1000.0) == pytest.approx(0.0, abs=1e-9)


def test_kimp_positive_when_korea_more_expensive():
    """한국이 비싸면 정김프 (양수)."""
    # upbit=1100, okx*fx=1000 → 10% 정김프
    pct = compute_kimp_pct(1100.0, 1.0, 1000.0)
    assert pct == pytest.approx(10.0, abs=1e-9)


def test_kimp_negative_when_korea_cheaper():
    """한국이 싸면 역김프 (음수)."""
    # upbit=980, okx*fx=1000 → -2% 역김프
    pct = compute_kimp_pct(980.0, 1.0, 1000.0)
    assert pct == pytest.approx(-2.0, abs=1e-9)


def test_kimp_realistic_btc_scenario():
    """BTC: upbit 138M KRW, okx 100k USDT, fx 1380 → 0% (정합)."""
    pct = compute_kimp_pct(138_000_000.0, 100_000.0, 1380.0)
    assert pct == pytest.approx(0.0, abs=1e-6)


def test_kimp_realistic_reverse_kimp_scenario():
    """역김프 -1.8% 시나리오."""
    upbit = 100_000_000.0 * (1 - 0.018)  # 98.2M
    pct = compute_kimp_pct(upbit, 100_000.0, 1000.0)
    assert pct == pytest.approx(-1.8, abs=1e-6)


# ── 2. strict=True ───────────────────────────────────────────────

@pytest.mark.parametrize("upbit,okx,fx", [
    (0.0, 1.0, 1000.0),
    (-1.0, 1.0, 1000.0),
    (1000.0, 0.0, 1000.0),
    (1000.0, -1.0, 1000.0),
    (1000.0, 1.0, 0.0),
    (1000.0, 1.0, -1000.0),
])
def test_compute_kimp_strict_raises_on_invalid_input(upbit, okx, fx):
    with pytest.raises(ValueError):
        compute_kimp_pct(upbit, okx, fx, strict=True)


# ── 3. strict=False default ─────────────────────────────────────

@pytest.mark.parametrize("upbit,okx,fx", [
    (0.0, 1.0, 1000.0),
    (1000.0, 0.0, 1000.0),
    (1000.0, 1.0, 0.0),
    (-1.0, 1.0, 1000.0),
])
def test_compute_kimp_default_returns_zero_on_invalid_input(upbit, okx, fx):
    assert compute_kimp_pct(upbit, okx, fx) == 0.0
    assert compute_kimp_pct(upbit, okx, fx, strict=False) == 0.0


# ── 4. assess_kimp ───────────────────────────────────────────────

def test_assess_kimp_valid_normal_case():
    r = assess_kimp(1100.0, 1.0, 1000.0)
    assert isinstance(r, KimpResult)
    assert r.valid is True
    assert r.kimp_pct == pytest.approx(10.0, abs=1e-9)
    assert "정상" in r.reason


def test_assess_kimp_invalid_returns_unflagged_zero():
    r = assess_kimp(0.0, 1.0, 1000.0)
    assert r.valid is False
    assert r.kimp_pct == 0.0
    assert "비정상" in r.reason


def test_assess_kimp_preserves_inputs():
    r = assess_kimp(1100.0, 1.0, 1000.0)
    assert r.upbit_krw == 1100.0
    assert r.okx_usdt == 1.0
    assert r.fx == 1000.0


# ── 5. 단일 진리 소스 위임 검증 ─────────────────────────────────

def test_kimp_snapshot_compute_kimp_delegates_to_canonical():
    from app.schemas.market import KimpSnapshot
    # 동일 입력에 동일 결과
    a = KimpSnapshot.compute_kimp(1100.0, 1.0, 1000.0)
    b = compute_kimp_pct(1100.0, 1.0, 1000.0)
    assert a == b == pytest.approx(10.0, abs=1e-9)


def test_kimp_snapshot_compute_kimp_silent_on_invalid():
    """legacy 동작 유지 — 비정상 입력 시 0.0 (raise 안 함)."""
    from app.schemas.market import KimpSnapshot
    assert KimpSnapshot.compute_kimp(0.0, 1.0, 1000.0) == 0.0
    assert KimpSnapshot.compute_kimp(1000.0, 0.0, 1000.0) == 0.0


def test_kimp_strategy_calculate_kimp_delegates_strict():
    from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
    a = KimpMeanReversionStrategy.calculate_kimp(1100.0, 1.0, 1000.0)
    b = compute_kimp_pct(1100.0, 1.0, 1000.0, strict=True)
    assert a == b


def test_kimp_strategy_calculate_kimp_raises_on_invalid():
    """legacy 동작 유지 — 비정상 입력 시 ValueError."""
    from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
    with pytest.raises(ValueError):
        KimpMeanReversionStrategy.calculate_kimp(0.0, 1.0, 1000.0)
    with pytest.raises(ValueError):
        KimpMeanReversionStrategy.calculate_kimp(1000.0, 0.0, 1000.0)


# ── 6. breakeven_threshold_pct ───────────────────────────────────

def test_breakeven_default_sums_components():
    """기본값: 0.05 × 4 + 0 + 0 = 0.2."""
    bt = breakeven_threshold_pct()
    assert bt == pytest.approx(0.2, abs=1e-9)


def test_breakeven_with_funding_uses_absolute_value():
    """음수 funding 도 비용으로 취급."""
    bt_pos = breakeven_threshold_pct(funding_pct=0.5)
    bt_neg = breakeven_threshold_pct(funding_pct=-0.5)
    assert bt_pos == bt_neg


def test_breakeven_includes_slippage():
    bt0 = breakeven_threshold_pct(slippage_pct=0.0)
    bt1 = breakeven_threshold_pct(slippage_pct=0.1)
    assert bt1 - bt0 == pytest.approx(0.1, abs=1e-9)


def test_breakeven_zero_costs():
    bt = breakeven_threshold_pct(
        upbit_spread_pct=0, okx_spread_pct=0,
        upbit_fee_pct=0, okx_fee_pct=0,
        funding_pct=0, slippage_pct=0,
    )
    assert bt == 0.0


# ── 7. expected_edge_pct ─────────────────────────────────────────

def test_expected_edge_when_kimp_below_exit():
    """역김프 -1.8% 가 청산 -1.0% 까지 도달 → edge 0.8%."""
    e = expected_edge_pct(kimp_pct=-1.8, exit_threshold_pct=-1.0)
    assert e == pytest.approx(0.8, abs=1e-9)


def test_expected_edge_when_kimp_above_exit():
    """정김프 +2.5% 가 청산 +1.0% 까지 도달 → edge 1.5%."""
    e = expected_edge_pct(kimp_pct=2.5, exit_threshold_pct=1.0)
    assert e == pytest.approx(1.5, abs=1e-9)


def test_expected_edge_zero_at_exit():
    e = expected_edge_pct(kimp_pct=-1.0, exit_threshold_pct=-1.0)
    assert e == 0.0


# ── 8. is_anomaly ────────────────────────────────────────────────

@pytest.mark.parametrize("pct", [-15.0, -10.5, 10.5, 20.0, 100.0])
def test_is_anomaly_outside_default_range(pct):
    assert is_anomaly(pct) is True


@pytest.mark.parametrize("pct", [-9.5, -1.5, 0.0, 5.0, 9.5])
def test_is_anomaly_inside_default_range(pct):
    assert is_anomaly(pct) is False


def test_is_anomaly_custom_range():
    """tighter range 로 ±2% 밖이면 anomaly."""
    assert is_anomaly(3.0, abnormal_min=-2.0, abnormal_max=2.0) is True
    assert is_anomaly(1.5, abnormal_min=-2.0, abnormal_max=2.0) is False


# ── 9. 기존 동작 회귀 — kimp strategy 통합 ──────────────────────

def test_kimp_strategy_signal_unchanged_after_delegation():
    """위임 후에도 KimpMeanReversionStrategy.generate_signal 동작 동일."""
    from datetime import datetime, timezone
    from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
    s = KimpMeanReversionStrategy(entry_threshold=-1.8, exit_threshold=-1.0)
    sig = s.generate_signal("BTC", 980, 1, 1000, now=datetime.now(timezone.utc))
    # 980 / (1 × 1000) - 1 = -0.02 = -2.0% → entry 충족
    assert sig.action == "OPEN_REVERSE_KIMP"
    assert sig.kimp_pct == pytest.approx(-2.0, abs=1e-6)


def test_kimp_snapshot_field_initialization_unchanged():
    """KimpSnapshot 의 dataclass 동작은 변경 없음."""
    from datetime import datetime, timezone
    from app.schemas.market import KimpSnapshot
    snap = KimpSnapshot(
        symbol="BTC",
        upbit_price_krw=98_000_000,
        okx_price_usdt=100_000,
        usdt_krw=1000,
        kimp_pct=KimpSnapshot.compute_kimp(98_000_000, 100_000, 1000),
        ts=datetime.now(timezone.utc),
    )
    assert snap.kimp_pct == pytest.approx(-2.0, abs=1e-6)
    assert snap.deposit_ok is True
