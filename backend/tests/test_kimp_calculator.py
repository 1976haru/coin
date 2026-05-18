"""체크리스트 #34 Kimp Formula Calculator — 회귀 테스트.

본 테스트는 ``app.market.kimp_calculator`` 의 Decimal 기반 표준 계산 모듈 검증.
기존 float 기반 ``app.market.kimp`` 와 ``test_kimp_formula.py`` 는 변경 없음.

검증:
  공식:
    1.  foreign_price_krw = foreign × fx
    2.  premium_ratio 정확성
    3.  premium_percent / premium_bps 일관성
    4.  부호: KIMP / REVERSE_KIMP / NEUTRAL
    5.  neutral band 안쪽 → NEUTRAL
  유효성:
    6.  domestic_price_krw ≤ 0 → invalid
    7.  foreign_price_quote ≤ 0 → invalid
    8.  fx_rate_krw ≤ 0 → invalid
  FX anomaly:
    9.  fx < min → fx_anomaly + reason
   10.  fx > max → fx_anomaly + reason
   11.  reference 대비 deviation_bps 큼 → fx_anomaly + deviation_bps 채워짐
   12.  reference 정상 → fx_anomaly=False, deviation_bps 계산됨
  수렴/확대:
   13.  previous None → UNKNOWN
   14.  |current| > |previous| + threshold → EXPANDING
   15.  |current| < |previous| - threshold → CONVERGING
   16.  threshold 이내 변화 → NEUTRAL
  fee-adjusted helper:
   17.  raw 부호 보존
   18.  costs > |raw| → 0 으로 clamp (부호 유지)
   19.  funding bps 는 abs 로 비용 합산
   20.  raw=None → 0
  KimpAgent hook:
   21.  build_kimp_context 출력 형태 + direct_order_allowed=False
   22.  classify dislocation STRUCTURAL (부호 일관 + 큰 magnitude)
   23.  classify dislocation TEMPORARY (부호 혼재)
   24.  classify dislocation MIXED (부호 일관 + 작은 magnitude)
   25.  classify dislocation UNKNOWN (sample 부족)
  Decimal 정밀도:
   26.  실제 BTC 시나리오 (1.4억 KRW × 100k USDT × 1380 KRW)
   27.  float Decimal 변환 정확성 (str() 경유)
  단일 진리 소스 호환:
   28.  app.market.kimp.compute_kimp_pct 와 동일 입력 → 동일 결과 (오차 허용)
   29.  KimpMeanReversionStrategy.calculate_kimp 회귀 — 변경 없음 확인
  Static guards (CLAUDE.md §3.1):
   30.  broker/execution import 부재
   31.  network SDK import 부재
   32.  order method 호출 부재 (place_order/cancel_order/get_balance/submit_order)
   33.  Signal import 부재 (전략 모듈 import 부재)
   34.  forbidden literal 부재 (ENABLE_LIVE_TRADING=True 등)
   35.  direct_order_allowed 영구 False (KimpCalculatorConfig + KimpResult)
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.market import kimp_calculator
from app.market.kimp_calculator import (
    ConvergenceState,
    Direction,
    DislocationKind,
    KimpCalculatorConfig,
    KimpInputs,
    KimpResult,
    build_kimp_context,
    calculate_fee_adjusted_premium_bps,
    classify_structural_vs_temporary_dislocation,
    compute_kimp,
)


_TARGET = Path(kimp_calculator.__file__)


# ── helpers ──────────────────────────────────────────────────────


def _inputs(
    domestic="100",
    foreign="1",
    fx="100",
    **kwargs,
) -> KimpInputs:
    return KimpInputs(
        domestic_price_krw=Decimal(str(domestic)),
        foreign_price_quote=Decimal(str(foreign)),
        fx_rate_krw=Decimal(str(fx)),
        **kwargs,
    )


# ── 1-5. 공식 정확성 ────────────────────────────────────────────


def test_foreign_price_krw_is_foreign_times_fx():
    r = compute_kimp(_inputs(domestic="1000", foreign="2", fx="500"))
    assert r.foreign_price_krw == Decimal("1000")


def test_premium_ratio_matches_formula():
    # domestic=1100, foreign_krw=1000 → ratio = 0.1
    r = compute_kimp(_inputs(domestic="1100", foreign="1", fx="1000"))
    assert r.premium_ratio == Decimal("0.1")


def test_premium_percent_and_bps_consistency():
    r = compute_kimp(_inputs(domestic="1100", foreign="1", fx="1000"))
    assert r.premium_percent == Decimal("10")
    assert r.premium_bps == Decimal("1000")
    # ratio × 100 == pct ; ratio × 10000 == bps
    assert r.premium_percent * Decimal("100") == r.premium_bps


def test_direction_kimp_when_domestic_more_expensive():
    r = compute_kimp(_inputs(domestic="1100", foreign="1", fx="1000"))
    assert r.direction == Direction.KIMP


def test_direction_reverse_kimp_when_domestic_cheaper():
    r = compute_kimp(_inputs(domestic="980", foreign="1", fx="1000"))
    assert r.direction == Direction.REVERSE_KIMP


def test_direction_neutral_when_within_band():
    # 1 bps premium → NEUTRAL (band default 5 bps)
    # domestic=1000.1, foreign_krw=1000 → ratio=0.0001 → 1 bps
    r = compute_kimp(_inputs(domestic="1000.1", foreign="1", fx="1000"))
    assert abs(r.premium_bps) <= KimpCalculatorConfig().neutral_band_bps
    assert r.direction == Direction.NEUTRAL


# ── 6-8. invalid inputs ─────────────────────────────────────────


@pytest.mark.parametrize("domestic", ["0", "-1", "-1000"])
def test_invalid_when_domestic_non_positive(domestic):
    r = compute_kimp(_inputs(domestic=domestic))
    assert r.is_valid is False
    assert "domestic_price_krw" in (r.invalid_reason or "")
    assert "invalid_input" in r.risk_flags
    assert r.premium_bps == Decimal("0")


@pytest.mark.parametrize("foreign", ["0", "-1"])
def test_invalid_when_foreign_non_positive(foreign):
    r = compute_kimp(_inputs(foreign=foreign))
    assert r.is_valid is False
    assert "foreign_price_quote" in (r.invalid_reason or "")


@pytest.mark.parametrize("fx", ["0", "-1"])
def test_invalid_when_fx_non_positive(fx):
    r = compute_kimp(_inputs(fx=fx))
    assert r.is_valid is False
    assert "fx_rate_krw" in (r.invalid_reason or "")


# ── 9-12. FX anomaly ────────────────────────────────────────────


def test_fx_anomaly_when_fx_below_min():
    # default fx_rate_min=500
    r = compute_kimp(_inputs(fx="100"))
    assert r.fx_anomaly is True
    assert "sanity range" in (r.fx_anomaly_reason or "")
    assert "fx_anomaly" in r.risk_flags


def test_fx_anomaly_when_fx_above_max():
    # default fx_rate_max=3000
    r = compute_kimp(_inputs(fx="5000"))
    assert r.fx_anomaly is True
    assert "sanity range" in (r.fx_anomaly_reason or "")


def test_fx_anomaly_when_reference_deviation_large():
    # fx=1500 vs ref=1300 → deviation ~1538 bps > default 500 bps
    r = compute_kimp(_inputs(
        domestic="1500", foreign="1", fx="1500",
        reference_fx_rate_krw=Decimal("1300"),
    ))
    assert r.fx_anomaly is True
    assert r.fx_deviation_bps is not None
    assert r.fx_deviation_bps > KimpCalculatorConfig().fx_anomaly_deviation_bps


def test_fx_anomaly_false_when_reference_within_band():
    # fx=1305 vs ref=1300 → deviation ~38 bps < 500
    r = compute_kimp(_inputs(
        domestic="1305", foreign="1", fx="1305",
        reference_fx_rate_krw=Decimal("1300"),
    ))
    assert r.fx_anomaly is False
    assert r.fx_deviation_bps is not None
    assert r.fx_deviation_bps < KimpCalculatorConfig().fx_anomaly_deviation_bps


# ── 13-16. convergence / expansion ──────────────────────────────


def test_convergence_unknown_without_previous():
    r = compute_kimp(_inputs(domestic="1100", foreign="1", fx="1000"))
    assert r.convergence_state == ConvergenceState.UNKNOWN
    assert r.delta_bps is None


def test_convergence_expanding():
    # current ratio=0.05 → 500 bps, previous 100 bps → delta=+400 bps > threshold(10)
    r = compute_kimp(_inputs(
        domestic="1050", foreign="1", fx="1000",
        previous_premium_bps=Decimal("100"),
    ))
    assert r.convergence_state == ConvergenceState.EXPANDING
    assert r.delta_bps is not None and r.delta_bps > Decimal("0")
    assert "expanding" in r.risk_flags


def test_convergence_converging():
    # current 100 bps, previous 500 bps → delta=-400 bps < -threshold(10)
    r = compute_kimp(_inputs(
        domestic="1010", foreign="1", fx="1000",
        previous_premium_bps=Decimal("500"),
    ))
    assert r.convergence_state == ConvergenceState.CONVERGING


def test_convergence_neutral_within_threshold():
    # current 100 bps, previous 95 bps → delta=5 bps < threshold(10)
    r = compute_kimp(_inputs(
        domestic="1010", foreign="1", fx="1000",
        previous_premium_bps=Decimal("95"),
    ))
    assert r.convergence_state == ConvergenceState.NEUTRAL


# ── 17-20. fee_adjusted ─────────────────────────────────────────


def test_fee_adjusted_preserves_positive_sign():
    adj = calculate_fee_adjusted_premium_bps(
        Decimal("200"),
        domestic_fee_bps=Decimal("10"),
        foreign_fee_bps=Decimal("10"),
        transfer_cost_bps=Decimal("30"),
    )
    assert adj == Decimal("150")


def test_fee_adjusted_preserves_negative_sign():
    adj = calculate_fee_adjusted_premium_bps(
        Decimal("-200"),
        domestic_fee_bps=Decimal("10"),
        foreign_fee_bps=Decimal("10"),
        transfer_cost_bps=Decimal("30"),
    )
    assert adj == Decimal("-150")


def test_fee_adjusted_clamped_to_zero_when_costs_exceed_raw():
    adj = calculate_fee_adjusted_premium_bps(
        Decimal("50"),
        domestic_fee_bps=Decimal("30"),
        foreign_fee_bps=Decimal("30"),
        transfer_cost_bps=Decimal("30"),
    )
    assert adj == Decimal("0")


def test_fee_adjusted_funding_uses_absolute_value():
    pos = calculate_fee_adjusted_premium_bps(
        Decimal("100"), funding_bps=Decimal("20"),
    )
    neg = calculate_fee_adjusted_premium_bps(
        Decimal("100"), funding_bps=Decimal("-20"),
    )
    assert pos == neg == Decimal("80")


def test_fee_adjusted_none_returns_zero():
    assert calculate_fee_adjusted_premium_bps(None) == Decimal("0")  # type: ignore[arg-type]


# ── 21. build_kimp_context ──────────────────────────────────────


def test_build_kimp_context_shape_and_no_order_allowed():
    r = compute_kimp(_inputs(
        symbol="BTC",
        domestic="100000000", foreign="100000", fx="1000",
    ))
    ctx = build_kimp_context(r)
    assert ctx["kind"] == "kimp_calculator_context"
    assert ctx["direct_order_allowed"] is False
    assert ctx["symbol"] == "BTC"
    assert ctx["direction"] in (
        Direction.KIMP, Direction.REVERSE_KIMP, Direction.NEUTRAL,
    )
    # Decimal 은 str 직렬화 (정밀도 보존, JSON 호환)
    assert isinstance(ctx["premium_bps"], str)
    assert isinstance(ctx["premium_percent"], str)
    # action token 누설 금지
    assert "action" not in ctx
    for forbidden in ("BUY", "SELL", "ENTER", "EXIT"):
        assert forbidden not in str(ctx).split()


# ── 22-25. classify_structural_vs_temporary_dislocation ─────────


def test_classify_structural_same_sign_large_magnitude():
    rs = [
        compute_kimp(_inputs(domestic="1100", foreign="1", fx="1000")),  # 1000 bps
        compute_kimp(_inputs(domestic="1090", foreign="1", fx="1000")),  # 900 bps
        compute_kimp(_inputs(domestic="1095", foreign="1", fx="1000")),  # 950 bps
    ]
    res = classify_structural_vs_temporary_dislocation(rs)
    assert res["dislocation_kind"] == DislocationKind.STRUCTURAL
    assert res["direct_order_allowed"] is False
    assert res["same_sign"] is True
    assert res["sample_count"] == 3


def test_classify_temporary_when_signs_mixed():
    rs = [
        compute_kimp(_inputs(domestic="1100", foreign="1", fx="1000")),  # +1000
        compute_kimp(_inputs(domestic="900", foreign="1", fx="1000")),   # -1000
        compute_kimp(_inputs(domestic="1050", foreign="1", fx="1000")),  # +500
    ]
    res = classify_structural_vs_temporary_dislocation(rs)
    assert res["dislocation_kind"] == DislocationKind.TEMPORARY
    assert res["same_sign"] is False


def test_classify_mixed_same_sign_small_magnitude():
    # 모두 정김프이지만 평균 |bps| 가 structural_min_abs_bps(80) 미만
    rs = [
        compute_kimp(_inputs(domestic="1001", foreign="1", fx="1000")),  # 10 bps
        compute_kimp(_inputs(domestic="1002", foreign="1", fx="1000")),  # 20 bps
        compute_kimp(_inputs(domestic="1003", foreign="1", fx="1000")),  # 30 bps
    ]
    res = classify_structural_vs_temporary_dislocation(rs)
    assert res["dislocation_kind"] == DislocationKind.MIXED


def test_classify_unknown_when_sample_insufficient():
    rs = [
        compute_kimp(_inputs(domestic="1100", foreign="1", fx="1000")),
    ]
    res = classify_structural_vs_temporary_dislocation(rs)
    assert res["dislocation_kind"] == DislocationKind.UNKNOWN
    assert res["direct_order_allowed"] is False


# ── 26-27. Decimal 정밀도 ───────────────────────────────────────


def test_realistic_btc_zero_premium_decimal_precision():
    # upbit=138_000_000, okx=100_000, fx=1380 → 정합 → 0 bps
    r = compute_kimp(_inputs(
        symbol="BTC",
        domestic="138000000", foreign="100000", fx="1380",
    ))
    assert r.is_valid is True
    assert r.premium_bps == Decimal("0")
    assert r.direction == Direction.NEUTRAL


def test_float_input_coerced_via_str_no_binary_drift():
    # Decimal(float) 은 부정확하지만 _to_decimal 은 str() 경유 → 정확.
    r = compute_kimp(KimpInputs(
        domestic_price_krw=1100.0,    # type: ignore[arg-type]
        foreign_price_quote=1.0,      # type: ignore[arg-type]
        fx_rate_krw=1000.0,           # type: ignore[arg-type]
    ))
    assert r.premium_bps == Decimal("1000")


# ── 28-29. 단일 진리 소스 호환 (기존 #34 float 모듈) ──────────


def test_calculator_matches_legacy_float_kimp_pct():
    """Decimal 계산 결과를 float 으로 환산하면 기존 ``compute_kimp_pct`` 와 일치."""
    from app.market.kimp import compute_kimp_pct
    r = compute_kimp(_inputs(domestic="1100", foreign="1", fx="1000"))
    legacy = compute_kimp_pct(1100.0, 1.0, 1000.0)
    assert float(r.premium_percent) == pytest.approx(legacy, abs=1e-9)


def test_kimp_strategy_signal_unchanged_after_calculator_added():
    """KimpMeanReversionStrategy 회귀 — calculator 모듈 추가가 기존 동작 영향 없음."""
    from datetime import datetime, timezone
    from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
    s = KimpMeanReversionStrategy(entry_threshold=-1.8, exit_threshold=-1.0)
    sig = s.generate_signal("BTC", 980, 1, 1000, now=datetime.now(timezone.utc))
    assert sig.action == "OPEN_REVERSE_KIMP"
    assert sig.is_order_intent is False  # CLAUDE.md §3.2


# ── 30-35. Static guards ────────────────────────────────────────


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


def test_module_no_strategy_or_signal_imports():
    """Calculator 는 Signal 을 생성하지 않으므로 전략 계층 import 부재."""
    pat = re.compile(
        r"^\s*(?:from\s+app\.strategies|"
        r"import\s+app\.strategies)",
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
    """주문 의도/실거래 활성화 리터럴이 *production* 모듈에 등장 금지."""
    forbidden = (
        "ENABLE_LIVE_TRADING = True",
        "ENABLE_LIVE_TRADING=True",
        "ENABLE_AI_EXECUTION = True",
        "ENABLE_AI_EXECUTION=True",
        "ENABLE_CRYPTO_FUTURES_LIVE = True",
        "ENABLE_CRYPTO_FUTURES_LIVE=True",
        "is_order_intent: bool = True",
        "is_order_intent=True",
        "used_for_order=True",
        "direct_order_allowed=True",
        "direct_order_allowed: bool = True",
    )
    text = _TARGET.read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in text, f"forbidden literal present: {needle}"


def test_direct_order_allowed_permanently_false_on_config_and_result():
    cfg = KimpCalculatorConfig()
    assert cfg.direct_order_allowed is False
    r = compute_kimp(_inputs())
    assert r.direct_order_allowed is False


def test_kimp_result_is_frozen_no_mutation():
    """KimpResult / KimpInputs / KimpCalculatorConfig 모두 frozen — 변경 불가."""
    r = compute_kimp(_inputs())
    with pytest.raises(Exception):
        r.direct_order_allowed = True  # type: ignore[misc]
    cfg = KimpCalculatorConfig()
    with pytest.raises(Exception):
        cfg.direct_order_allowed = True  # type: ignore[misc]
