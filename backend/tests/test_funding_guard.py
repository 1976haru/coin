"""체크리스트 #36 Funding Cost Guard — 회귀 테스트.

본 테스트는 ``app.risk.funding`` (Decimal 기반 구조적 FundingGuardDecision API)
검증. 기존 float 기반 ``app.market.funding`` (#36 1차) 와
``tests/test_funding.py`` 는 변경 없음.

검증:
  합성 정책:
    1. 사유 없음 → ALLOW_NEW_CANDIDATE / allowed=True
    2. HIGH/CRITICAL → BLOCK_NEW_CANDIDATE / allowed=False
    3. WARNING 만 → REVIEW_REQUIRED / required_review=True
    4. hold blocked → REDUCE_CANDIDATE
    5. hold no reason → HOLD_CANDIDATE
  데이터:
    6. snapshot 없음 + require=True → HIGH block
    7. snapshot 없음 + require=False → WARNING review
    8. interval ≤ 0 → HIGH block
    9. timestamp None → HIGH stale
   10. age > max_funding_age_seconds → HIGH stale
   11. age 적정 → 사유 없음
  Extreme:
   12. |rate × 100| > extreme_threshold_bps → HIGH block
   13. 정상 rate → 사유 없음
  Direction:
   14. short + 양의 funding → 수취 → is_unfavorable=False
   15. short + 음의 funding → 지불 → is_unfavorable=True + WARNING
   16. long + 양의 funding → 지불 → is_unfavorable=True + WARNING
   17. long + 음의 funding → 수취 → is_unfavorable=False
   18. unknown side → conservative abs (보수적 비용)
  비용 계산:
   19. num_funding_events 분수 (24h / 8h interval = 3)
   20. annualized_pct (rate × 24/interval × 365)
   21. cost_pct signed 부호 보존
   22. cost_bps = abs_cost_pct × 100
   23. cost_to_edge_ratio = abs_cost_pct / |edge|
  cost_to_edge 정책:
   24. ratio ≥ block_ratio (0.8) → HIGH FUNDING_COST_EXCEEDS_EDGE
   25. review_ratio (0.4) ≤ ratio < block_ratio → WARNING FUNDING_COST_NEAR_EDGE
   26. ratio < review_ratio → 사유 없음
   27. is_unfavorable=False 면 ratio 정책 적용 안 함
  Hold 평가:
   28. accumulated ≥ reduce_pct (2.0) → HIGH FUNDING_ACCUMULATED_REDUCE
   29. warning_pct (1.0) ≤ accumulated < reduce_pct → WARNING ACCUMULATED_HIGH
   30. accumulated < warning_pct → 사유 없음
   31. accumulated None → 사유 없음
  Missing context:
   32. side 누락 → HIGH MISSING_CRITICAL_CONTEXT
   33. symbol 누락 → HIGH MISSING_CRITICAL_CONTEXT
  FundingCostGuard 클래스:
   34. estimate / evaluate_entry / evaluate_hold 메서드 동작
  Hook:
   35. build_funding_guard_context 출력 형태 + direct_order_allowed=False
   36. action 토큰 (BUY/SELL/ENTER/EXIT) 누설 없음
  Static guards:
   37. broker/execution import 부재
   38. order_gateway/adapter import 부재
   39. network SDK import 부재
   40. order method 호출 부재
   41. forbidden literal 부재
   42. "BUY"/"SELL"/"ENTER"/"EXIT" quoted 리터럴 부재
   43. direct_order_allowed/used_for_order 영구 False
   44. frozen dataclass
  Backward compat:
   45. 기존 app.market.funding 회귀
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.risk import funding as risk_funding
from app.risk.funding import (
    FundingCostEstimate,
    FundingCostGuard,
    FundingCostInput,
    FundingGuardConfig,
    FundingGuardDecision,
    FundingGuardReason,
    FundingRateSnapshot,
    GuardCode,
    GuardSeverity,
    GuardSource,
    RecommendedAction,
    build_funding_guard_context,
    compute_funding_estimate,
    evaluate_funding_entry,
    evaluate_funding_hold,
)


_TARGET = Path(risk_funding.__file__)


def _now() -> datetime:
    return datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def _snap(rate="0.01", age_s=60, interval="8") -> FundingRateSnapshot:
    return FundingRateSnapshot(
        rate_pct=Decimal(rate),
        timestamp=_now() - timedelta(seconds=age_s),
        interval_hours=Decimal(interval),
        exchange="okx",
        symbol="BTC",
    )


def _input(**overrides) -> FundingCostInput:
    defaults = dict(
        symbol="BTC",
        side="short",
        snapshot=_snap(),
        intended_hours_held=Decimal("8"),
        expected_edge_pct=Decimal("0.5"),
        now=_now(),
    )
    defaults.update(overrides)
    return FundingCostInput(**defaults)


# ── 1-5. 합성 정책 ──────────────────────────────────────────────


def test_no_reasons_allow_new_candidate():
    d = evaluate_funding_entry(_input())
    assert d.allowed is True
    assert d.required_review is False
    assert d.recommended_action == RecommendedAction.ALLOW_NEW_CANDIDATE
    assert d.blocked_by == ()


def test_high_severity_blocks_new_candidate():
    # extreme funding
    d = evaluate_funding_entry(_input(snapshot=_snap(rate="5.0")))
    assert d.allowed is False
    assert d.recommended_action == RecommendedAction.BLOCK_NEW_CANDIDATE
    assert GuardCode.FUNDING_EXTREME in d.blocked_by


def test_warning_only_yields_review_required():
    # long + positive funding → unfavorable WARNING, no block-level reason
    d = evaluate_funding_entry(_input(
        side="long",
        snapshot=_snap(rate="0.01"),
        expected_edge_pct=Decimal("100"),  # huge edge → ratio negligible
    ))
    assert d.allowed is True
    assert d.required_review is True
    assert d.recommended_action == RecommendedAction.REVIEW_REQUIRED
    assert GuardCode.FUNDING_DIRECTION_ADVERSE in d.review_codes


def test_hold_blocking_yields_reduce_candidate():
    d = evaluate_funding_hold(_input(
        is_held=True,
        accumulated_funding_cost_pct=Decimal("3.0"),  # ≥ 2.0 reduce
    ))
    assert d.allowed is False
    assert d.recommended_action == RecommendedAction.REDUCE_CANDIDATE
    assert GuardCode.FUNDING_ACCUMULATED_REDUCE in d.blocked_by


def test_hold_no_reason_yields_hold_candidate():
    d = evaluate_funding_hold(_input(is_held=True))
    assert d.allowed is True
    assert d.recommended_action == RecommendedAction.HOLD_CANDIDATE


# ── 6-11. 데이터 가드 ───────────────────────────────────────────


def test_snapshot_missing_blocks_when_required():
    d = evaluate_funding_entry(_input(snapshot=None))
    assert d.allowed is False
    assert GuardCode.FUNDING_DATA_MISSING in d.blocked_by


def test_snapshot_missing_warns_when_not_required():
    cfg = FundingGuardConfig(require_funding_context=False)
    d = evaluate_funding_entry(_input(snapshot=None), config=cfg)
    assert d.allowed is True
    assert d.required_review is True
    assert GuardCode.FUNDING_DATA_MISSING in d.review_codes


def test_invalid_interval_blocks():
    d = evaluate_funding_entry(_input(snapshot=FundingRateSnapshot(
        rate_pct=Decimal("0.01"),
        timestamp=_now(),
        interval_hours=Decimal("0"),  # invalid
    )))
    assert GuardCode.FUNDING_INVALID_INTERVAL in d.blocked_by


def test_stale_timestamp_none_blocks():
    d = evaluate_funding_entry(_input(snapshot=FundingRateSnapshot(
        rate_pct=Decimal("0.01"),
        timestamp=None,
        interval_hours=Decimal("8"),
    )))
    assert GuardCode.FUNDING_DATA_STALE in d.blocked_by


def test_stale_old_timestamp_blocks():
    d = evaluate_funding_entry(_input(
        snapshot=_snap(age_s=7200),  # > 600 default
    ))
    assert GuardCode.FUNDING_DATA_STALE in d.blocked_by


def test_recent_timestamp_passes():
    d = evaluate_funding_entry(_input(snapshot=_snap(age_s=30)))
    assert all(r.code != GuardCode.FUNDING_DATA_STALE for r in d.reasons)


# ── 12-13. Extreme ─────────────────────────────────────────────


def test_extreme_funding_blocks():
    # rate=2.0% → 200 bps > 100 bps default
    d = evaluate_funding_entry(_input(snapshot=_snap(rate="2.0")))
    assert GuardCode.FUNDING_EXTREME in d.blocked_by


def test_normal_funding_no_extreme_reason():
    d = evaluate_funding_entry(_input(snapshot=_snap(rate="0.01")))
    assert all(r.code != GuardCode.FUNDING_EXTREME for r in d.reasons)


# ── 14-18. Direction / signed cost ─────────────────────────────


def test_short_positive_funding_receives():
    """short + positive rate → 수취 → cost_pct < 0 → favorable."""
    est = compute_funding_estimate(_input(
        side="short", snapshot=_snap(rate="0.01"),
        intended_hours_held=Decimal("8"),
    ))
    assert est is not None
    assert est.cost_pct == Decimal("-0.01")  # -rate × 1 event
    assert est.is_unfavorable is False


def test_short_negative_funding_pays():
    est = compute_funding_estimate(_input(
        side="short", snapshot=_snap(rate="-0.01"),
        intended_hours_held=Decimal("8"),
    ))
    assert est is not None
    assert est.cost_pct == Decimal("0.01")  # short pays
    assert est.is_unfavorable is True


def test_long_positive_funding_pays():
    est = compute_funding_estimate(_input(
        side="long", snapshot=_snap(rate="0.01"),
        intended_hours_held=Decimal("8"),
    ))
    assert est is not None
    assert est.cost_pct == Decimal("0.01")  # long pays
    assert est.is_unfavorable is True


def test_long_negative_funding_receives():
    est = compute_funding_estimate(_input(
        side="long", snapshot=_snap(rate="-0.01"),
        intended_hours_held=Decimal("8"),
    ))
    assert est is not None
    assert est.cost_pct == Decimal("-0.01")
    assert est.is_unfavorable is False


def test_unknown_side_conservative_abs():
    # side="" → conservative — abs(rate) for cost
    est = compute_funding_estimate(_input(
        side="", snapshot=_snap(rate="-0.01"),
        intended_hours_held=Decimal("8"),
    ))
    assert est is not None
    # abs(-0.01) × 1 event = 0.01
    assert est.cost_pct == Decimal("0.01")
    assert est.is_unfavorable is True


# ── 19-23. 비용 계산 ───────────────────────────────────────────


def test_num_funding_events_fractional():
    est = compute_funding_estimate(_input(
        snapshot=_snap(interval="8"),
        intended_hours_held=Decimal("24"),  # 24/8 = 3
    ))
    assert est is not None
    assert est.num_funding_events == Decimal("3")


def test_annualized_funding_apr():
    # 0.01% / 8h → 0.01 × (24/8 × 365) = 10.95%
    est = compute_funding_estimate(_input(snapshot=_snap(rate="0.01")))
    assert est is not None
    assert est.annualized_pct == Decimal("10.95")


def test_cost_bps_equals_abs_cost_pct_times_100():
    est = compute_funding_estimate(_input(
        side="long", snapshot=_snap(rate="0.01"),
        intended_hours_held=Decimal("8"),
    ))
    assert est is not None
    assert est.cost_bps == est.abs_cost_pct * Decimal("100")


def test_cost_to_edge_ratio_computed():
    # long + 0.01% × 24h = 0.03% cost, edge 0.5% → ratio 0.06
    est = compute_funding_estimate(_input(
        side="long", snapshot=_snap(rate="0.01"),
        intended_hours_held=Decimal("24"),
        expected_edge_pct=Decimal("0.5"),
    ))
    assert est is not None
    assert est.cost_to_edge_ratio == Decimal("0.06")


def test_cost_to_edge_ratio_none_when_no_edge():
    est = compute_funding_estimate(_input(expected_edge_pct=None))
    assert est is not None
    assert est.cost_to_edge_ratio is None


# ── 24-27. cost_to_edge 정책 ───────────────────────────────────


def test_cost_to_edge_block_ratio_blocks():
    # long + 0.5% rate × 8h (1 event) = 0.5% cost; edge 0.5% → ratio 1.0
    d = evaluate_funding_entry(_input(
        side="long", snapshot=_snap(rate="0.5"),
        intended_hours_held=Decimal("8"),
        expected_edge_pct=Decimal("0.5"),
    ))
    assert GuardCode.FUNDING_COST_EXCEEDS_EDGE in d.blocked_by


def test_cost_to_edge_review_ratio_warns():
    # long + 0.25% × 8h = 0.25% cost; edge 0.5% → ratio 0.5 (>= 0.4, < 0.8)
    d = evaluate_funding_entry(_input(
        side="long", snapshot=_snap(rate="0.25"),
        intended_hours_held=Decimal("8"),
        expected_edge_pct=Decimal("0.5"),
    ))
    assert GuardCode.FUNDING_COST_NEAR_EDGE in d.review_codes
    assert all(r.code != GuardCode.FUNDING_COST_EXCEEDS_EDGE
               for r in d.reasons)


def test_cost_to_edge_below_review_ratio_silent():
    # long + 0.01% × 24h = 0.03% cost; edge 1.0 → ratio 0.03 (< 0.4)
    d = evaluate_funding_entry(_input(
        side="long", snapshot=_snap(rate="0.01"),
        intended_hours_held=Decimal("24"),
        expected_edge_pct=Decimal("1.0"),
    ))
    assert all(r.code != GuardCode.FUNDING_COST_NEAR_EDGE for r in d.reasons)
    assert all(r.code != GuardCode.FUNDING_COST_EXCEEDS_EDGE for r in d.reasons)


def test_favorable_direction_skips_cost_to_edge():
    # short + 0.5% rate (favorable for short — receives) → ratio policy 적용 안 함
    d = evaluate_funding_entry(_input(
        side="short", snapshot=_snap(rate="0.5"),
        intended_hours_held=Decimal("8"),
        expected_edge_pct=Decimal("0.5"),
    ))
    # extreme(0.5×100=50 bps) < 100 default 이므로 extreme 도 없음
    assert all(r.code != GuardCode.FUNDING_COST_EXCEEDS_EDGE
               for r in d.reasons)
    assert all(r.code != GuardCode.FUNDING_COST_NEAR_EDGE
               for r in d.reasons)


# ── 28-31. Hold 평가 ───────────────────────────────────────────


def test_hold_accumulated_reduce_threshold_blocks():
    d = evaluate_funding_hold(_input(
        is_held=True,
        accumulated_funding_cost_pct=Decimal("2.5"),  # ≥ 2.0
    ))
    assert GuardCode.FUNDING_ACCUMULATED_REDUCE in d.blocked_by
    assert d.recommended_action == RecommendedAction.REDUCE_CANDIDATE


def test_hold_accumulated_warning_threshold_reviews():
    d = evaluate_funding_hold(_input(
        is_held=True,
        accumulated_funding_cost_pct=Decimal("1.5"),  # ≥ 1.0, < 2.0
    ))
    assert GuardCode.FUNDING_ACCUMULATED_HIGH in d.review_codes
    assert all(r.code != GuardCode.FUNDING_ACCUMULATED_REDUCE
               for r in d.reasons)


def test_hold_accumulated_below_thresholds_silent():
    d = evaluate_funding_hold(_input(
        is_held=True,
        accumulated_funding_cost_pct=Decimal("0.3"),
    ))
    assert all(r.code != GuardCode.FUNDING_ACCUMULATED_HIGH
               for r in d.reasons)
    assert all(r.code != GuardCode.FUNDING_ACCUMULATED_REDUCE
               for r in d.reasons)


def test_hold_accumulated_none_silent():
    d = evaluate_funding_hold(_input(
        is_held=True,
        accumulated_funding_cost_pct=None,
    ))
    assert all(r.code != GuardCode.FUNDING_ACCUMULATED_HIGH
               for r in d.reasons)


# ── 32-33. Missing critical context ────────────────────────────


def test_missing_side_blocks():
    d = evaluate_funding_entry(_input(side="invalid_side"))
    miss = [r for r in d.reasons
            if r.code == GuardCode.MISSING_CRITICAL_CONTEXT]
    assert miss and miss[0].severity == GuardSeverity.HIGH
    assert "side" in miss[0].evidence.get("missing", [])


def test_missing_symbol_blocks():
    d = evaluate_funding_entry(_input(symbol=""))
    miss = [r for r in d.reasons
            if r.code == GuardCode.MISSING_CRITICAL_CONTEXT]
    assert miss and miss[0].severity == GuardSeverity.HIGH


# ── 34. FundingCostGuard 클래스 ────────────────────────────────


def test_funding_cost_guard_class_methods():
    g = FundingCostGuard(FundingGuardConfig())
    inp = _input()
    est = g.estimate(inp)
    assert isinstance(est, FundingCostEstimate)
    entry = g.evaluate_entry(inp)
    assert isinstance(entry, FundingGuardDecision)
    assert entry.mode == "entry"
    hold = g.evaluate_hold(inp)
    assert isinstance(hold, FundingGuardDecision)
    assert hold.mode == "hold"


# ── 35-36. KimpAgent / RiskManager hook ────────────────────────


def test_build_funding_guard_context_shape():
    d = evaluate_funding_entry(_input(snapshot=_snap(rate="5.0")))  # extreme
    ctx = build_funding_guard_context(d)
    assert ctx["kind"] == "funding_guard_context"
    assert ctx["direct_order_allowed"] is False
    assert ctx["used_for_order"] is False
    assert ctx["allowed"] is False
    assert ctx["recommended_action"] == RecommendedAction.BLOCK_NEW_CANDIDATE
    assert ctx["mode"] == "entry"
    assert isinstance(ctx["reasons"], list)
    assert ctx["estimate"] is not None
    # Decimal → str 직렬화 (JSON 호환)
    assert isinstance(ctx["estimate"]["cost_pct"], str)


def test_build_funding_guard_context_no_action_tokens():
    d = evaluate_funding_entry(_input())
    ctx = build_funding_guard_context(d)
    blob = str(ctx)
    for tok in ("BUY", "SELL", "ENTER", "EXIT"):
        assert not re.search(rf"\b{tok}\b", blob), (
            f"{tok} token leaked into FundingGuardContext: {blob}"
        )


# ── 37-44. Static guards ───────────────────────────────────────


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
        r"\.(?:place_order|cancel_order|get_balance|submit_order|"
        r"withdraw|deposit|set_leverage|set_margin)\s*\(",
    )
    text = _TARGET.read_text(encoding="utf-8")
    assert not pat.search(text)


def test_module_no_forbidden_substrings():
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
        "used_for_order: bool = True",
        "direct_order_allowed=True",
        "direct_order_allowed: bool = True",
    )
    text = _TARGET.read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in text, f"forbidden literal present: {needle}"


def test_module_no_quoted_action_tokens():
    """recommended_action 또는 어떤 반환값에도 BUY/SELL/ENTER/EXIT 따옴표 리터럴
    부재. docstring 의 설명 단어 노출은 허용 (사용자 지침 13단계).
    """
    text = _TARGET.read_text(encoding="utf-8")
    for needle in (r'"BUY"', r"'BUY'", r'"SELL"', r"'SELL'",
                   r'"ENTER"', r"'ENTER'", r'"EXIT"', r"'EXIT'"):
        assert needle not in text, (
            f"forbidden action literal {needle} in production module"
        )


def test_direct_order_allowed_permanently_false():
    cfg = FundingGuardConfig()
    assert cfg.direct_order_allowed is False
    assert cfg.used_for_order is False
    d = evaluate_funding_entry(_input())
    assert d.direct_order_allowed is False
    assert d.used_for_order is False


def test_dataclasses_are_frozen():
    cfg = FundingGuardConfig()
    d = evaluate_funding_entry(_input())
    with pytest.raises(Exception):
        cfg.direct_order_allowed = True  # type: ignore[misc]
    with pytest.raises(Exception):
        d.allowed = False  # type: ignore[misc]


# ── 45. Backward compat (기존 #36 1차 보존) ────────────────────


def test_existing_market_funding_still_works():
    """기존 float 기반 app.market.funding 모듈 회귀."""
    from app.market.funding import (
        annualized_funding_rate_pct,
        is_extreme_funding,
        is_funding_unfavorable,
    )
    assert annualized_funding_rate_pct(0.01) == pytest.approx(10.95, rel=1e-6)
    assert is_extreme_funding(2.0) is True
    assert is_extreme_funding(0.01) is False
    assert is_funding_unfavorable(-0.5, side="short") is True
    assert is_funding_unfavorable(0.5, side="short") is False
