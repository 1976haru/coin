"""체크리스트 #35 Kimp Risk Guards — 회귀 테스트.

본 테스트는 ``app.strategies.kimp_risk_guards`` (구조적 KimpGuardDecision API)
검증. 기존 float 기반 ``app.strategies.kimp_guards`` (#35 1차) 와
``test_kimp_guards.py`` 는 변경 없음.

검증:
  공식 / 합성:
    1. 사유 없음 → ALLOW_CANDIDATE / allowed=True / required_review=False
    2. CRITICAL/HIGH 사유 하나 → BLOCK_CANDIDATE / allowed=False
    3. WARNING/INFO 만 → REVIEW_REQUIRED / allowed=True / required_review=True
  공지 (notice) 가드:
    4. 입출금 중단 (DEPOSIT_WITHDRAWAL_SUSPENSION) → CRITICAL block
    5. 상장폐지 (DELISTING) → CRITICAL block
    6. 유의종목 (CAUTION) → HIGH block
    7. 거래중단 (TRADING_SUSPENSION) → CRITICAL block
    8. notice_context 미수신 + require_notice_context=True → HIGH block
    9. notice_context 미수신 + require_notice_context=False → WARNING review
   10. 미매칭 심볼 공지 → 통과
   11. 전역 공지 (symbols 비어있음) + 거래소 일치 → 적용됨
   12. HIGH severity 의 미매핑 notice_type → HIGH block
  FX 가드:
   13. fx_rate ≤ 0 → CRITICAL block (조기 반환)
   14. fx_source 미설정 → WARNING
   15. fx_timestamp stale → HIGH
   16. fx_timestamp 없음 → HIGH
   17. KimpResult.fx_anomaly=True → HIGH
  Liquidity 가드:
   18. require_orderbook_context=True + 호가 없음 → HIGH
   19. require_orderbook_context=False + 호가 없음 → 통과
   20. bid ≤ 0 → CRITICAL
   21. bid_size < min_bid_size → HIGH
   22. spread > max_spread_bps → HIGH
   23. orderbook stale → HIGH
  Bull market short 가드:
   24. 강세장 regime + REVERSE_KIMP + short_leg → HIGH block
   25. 강세장 regime + KIMP_CANDIDATE → 통과
   26. 강세장 regime + short_leg=False → 통과
   27. block_reverse_kimp_short_in_bull_market=False → 통과
   28. 강세 테마 (ETF_INFLOW) → HIGH block
  Funding 가드:
   29. funding_rate 절대값 > threshold → HIGH
   30. funding 방향 불리 (short 가 비용) → WARNING
   31. funding stale → WARNING
   32. funding 미수신 + require=True → HIGH
   33. funding 미수신 + require=False → 통과 (옵션)
  Freshness 가드:
   34. domestic price stale → HIGH
   35. foreign price stale → HIGH
   36. 둘 다 None → WARNING
  Data quality 가드:
   37. EXCLUDE + block_on_data_quality_exclude=True → CRITICAL
   38. WARNING + block_on_data_quality_warning=False → WARNING (review)
   39. WARNING + block_on_data_quality_warning=True → HIGH block
  Missing critical context:
   40. fx_rate_krw + kimp_result 누락 → HIGH
   41. intended_kimp_state UNKNOWN → WARNING (외 필드 있을 때)
  KimpAgent hook:
   42. build_kimp_guard_context 출력 형태 + direct_order_allowed=False
   43. action 토큰 (BUY/SELL/ENTER/EXIT) 누설 없음
  Static guards (CLAUDE.md §3.1):
   44. broker / execution import 부재
   45. order_gateway / adapter import 부재
   46. network SDK import 부재
   47. order method 호출 부재
   48. forbidden literal 부재
   49. direct_order_allowed / used_for_order 영구 False (Config + Decision)
   50. frozen dataclass — 모두 mutation 불가
  Backward compat:
   51. 기존 app.strategies.kimp_guards (#35 1차) 회귀 — import + evaluate 동작
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.market.kimp_calculator import (
    KimpInputs,
    compute_kimp,
)
from app.strategies import kimp_risk_guards as krg
from app.strategies.kimp_risk_guards import (
    GuardCode,
    GuardSeverity,
    GuardSource,
    KimpCandidateState,
    KimpGuardConfig,
    KimpGuardDecision,
    KimpGuardInput,
    KimpGuardReason,
    RecommendedAction,
    build_kimp_guard_context,
    check_bull_market_short_risk,
    check_data_quality_risk,
    check_freshness_risk,
    check_funding_risk,
    check_fx_risk,
    check_liquidity_risk,
    check_notice_risk,
    evaluate_kimp_guards,
)


_TARGET = Path(krg.__file__)


# ── helpers ──────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def _full_safe_input(**overrides) -> KimpGuardInput:
    """모든 가드를 통과하는 baseline input."""
    now = overrides.pop("now", _now())
    defaults = dict(
        symbol="BTC",
        intended_kimp_state=KimpCandidateState.REVERSE_KIMP_CANDIDATE,
        domestic_exchange="upbit",
        foreign_exchange="okx",
        notices=(),
        notice_context_available=True,
        fx_rate_krw=Decimal("1380"),
        fx_timestamp=now - timedelta(seconds=10),
        fx_source="upbit_quote",
        kimp_result=compute_kimp(KimpInputs(
            domestic_price_krw=Decimal("138000000"),
            foreign_price_quote=Decimal("100000"),
            fx_rate_krw=Decimal("1380"),
        )),
        domestic_price_timestamp=now - timedelta(seconds=5),
        foreign_price_timestamp=now - timedelta(seconds=5),
        short_leg_implied=False,
        data_quality_grade="GOOD",
        now=now,
    )
    defaults.update(overrides)
    return KimpGuardInput(**defaults)


# ── 1-3. 합성 정책 ──────────────────────────────────────────────


def test_no_reasons_yields_allow_candidate():
    d = evaluate_kimp_guards(_full_safe_input())
    assert d.allowed is True
    assert d.required_review is False
    assert d.recommended_action == RecommendedAction.ALLOW_CANDIDATE
    assert d.blocked_by == ()
    assert d.review_codes == ()


def test_critical_or_high_yields_block_candidate():
    # FX rate 0 → CRITICAL
    d = evaluate_kimp_guards(_full_safe_input(fx_rate_krw=Decimal("0")))
    assert d.allowed is False
    assert d.recommended_action == RecommendedAction.BLOCK_CANDIDATE
    assert GuardCode.FX_INVALID in d.blocked_by


def test_warning_only_yields_review_required():
    # data quality WARNING + nothing else → review only
    d = evaluate_kimp_guards(_full_safe_input(data_quality_grade="WARNING"))
    assert d.allowed is True
    assert d.required_review is True
    assert d.recommended_action == RecommendedAction.REVIEW_REQUIRED
    assert GuardCode.DATA_QUALITY_WARNING in d.review_codes


# ── 4-12. Notice guard ─────────────────────────────────────────


def test_notice_deposit_withdrawal_suspension_is_critical():
    notice = {
        "notice_type": "DEPOSIT_WITHDRAWAL_SUSPENSION",
        "severity": "HIGH",
        "symbols": ["BTC"],
        "exchange": "upbit",
        "title": "BTC 입출금 일시 중단",
    }
    d = evaluate_kimp_guards(_full_safe_input(notices=(notice,)))
    assert d.allowed is False
    assert GuardCode.DEPOSIT_WITHDRAWAL_SUSPENDED in d.blocked_by


def test_notice_delisting_is_critical():
    notice = {
        "notice_type": "DELISTING",
        "severity": "CRITICAL",
        "symbols": ["BTC"],
        "exchange": "okx",
        "title": "BTC 거래지원 종료",
    }
    d = evaluate_kimp_guards(_full_safe_input(notices=(notice,)))
    assert d.allowed is False
    assert GuardCode.DELISTING in d.blocked_by


def test_notice_caution_blocks_at_high():
    notice = {
        "notice_type": "CAUTION",
        "severity": "WARNING",
        "symbols": ["BTC"],
        "exchange": "upbit",
        "title": "유의종목 지정",
    }
    d = evaluate_kimp_guards(_full_safe_input(notices=(notice,)))
    assert d.allowed is False
    assert GuardCode.CAUTION_NOTICE in d.blocked_by
    matched = [r for r in d.reasons if r.code == GuardCode.CAUTION_NOTICE]
    assert matched[0].severity == GuardSeverity.HIGH


def test_notice_trading_suspension_is_critical():
    notice = {
        "notice_type": "TRADING_SUSPENSION",
        "severity": "HIGH",
        "symbols": ["BTC"],
        "exchange": "okx",
        "title": "거래 중단",
    }
    d = evaluate_kimp_guards(_full_safe_input(notices=(notice,)))
    assert d.allowed is False
    assert GuardCode.TRADING_SUSPENSION in d.blocked_by


def test_notice_context_missing_blocks_when_required():
    cfg = KimpGuardConfig(require_notice_context=True)
    d = evaluate_kimp_guards(
        _full_safe_input(notice_context_available=False),
        config=cfg,
    )
    assert d.allowed is False
    assert GuardCode.NOTICE_CONTEXT_MISSING in d.blocked_by


def test_notice_context_missing_warns_when_not_required():
    cfg = KimpGuardConfig(require_notice_context=False)
    d = evaluate_kimp_guards(
        _full_safe_input(notice_context_available=False),
        config=cfg,
    )
    # WARNING 만이라 review_required, allowed=True
    assert d.allowed is True
    assert d.required_review is True
    assert GuardCode.NOTICE_CONTEXT_MISSING in d.review_codes


def test_notice_unrelated_symbol_does_not_block():
    notice = {
        "notice_type": "DELISTING",
        "severity": "CRITICAL",
        "symbols": ["XRP"],
        "exchange": "upbit",
        "title": "XRP 거래지원 종료",
    }
    d = evaluate_kimp_guards(_full_safe_input(notices=(notice,)))
    # symbol BTC 는 매칭 안 됨 → 차단되지 않음
    assert d.allowed is True
    assert d.recommended_action == RecommendedAction.ALLOW_CANDIDATE


def test_notice_global_exchange_notice_applies():
    """symbols=[] 이고 거래소만 일치하면 전역 공지로 적용."""
    notice = {
        "notice_type": "TRADING_SUSPENSION",
        "severity": "HIGH",
        "symbols": [],
        "exchange": "upbit",
        "title": "Upbit 점검",
    }
    d = evaluate_kimp_guards(_full_safe_input(notices=(notice,)))
    assert d.allowed is False
    assert GuardCode.TRADING_SUSPENSION in d.blocked_by


def test_notice_unmapped_high_severity_blocks():
    """미매핑 notice_type 이라도 severity HIGH/CRITICAL 이면 차단."""
    notice = {
        "notice_type": "OTHER",
        "severity": "HIGH",
        "symbols": ["BTC"],
        "exchange": "upbit",
        "title": "기타 위험 공지",
    }
    d = evaluate_kimp_guards(_full_safe_input(notices=(notice,)))
    assert d.allowed is False
    assert GuardCode.HIGH_SEVERITY_NOTICE in d.blocked_by


# ── 13-17. FX guard ────────────────────────────────────────────


def test_fx_invalid_when_zero_or_negative():
    d = evaluate_kimp_guards(_full_safe_input(fx_rate_krw=Decimal("0")))
    assert any(r.code == GuardCode.FX_INVALID
               and r.severity == GuardSeverity.CRITICAL
               for r in d.reasons)


def test_fx_source_missing_is_warning():
    d = evaluate_kimp_guards(_full_safe_input(fx_source=None))
    fx_reasons = [r for r in d.reasons
                  if r.code == GuardCode.FX_SOURCE_MISSING]
    assert len(fx_reasons) == 1
    assert fx_reasons[0].severity == GuardSeverity.WARNING


def test_fx_stale_when_timestamp_old():
    now = _now()
    d = evaluate_kimp_guards(_full_safe_input(
        fx_timestamp=now - timedelta(seconds=120),  # > max_fx_age_seconds=60
        now=now,
    ))
    stale = [r for r in d.reasons if r.code == GuardCode.FX_STALE]
    assert stale and stale[0].severity == GuardSeverity.HIGH


def test_fx_stale_when_timestamp_missing():
    d = evaluate_kimp_guards(_full_safe_input(fx_timestamp=None))
    stale = [r for r in d.reasons if r.code == GuardCode.FX_STALE]
    assert stale and stale[0].severity == GuardSeverity.HIGH


def test_fx_anomaly_from_kimp_result_blocks():
    bad = compute_kimp(KimpInputs(
        domestic_price_krw=Decimal("100"),
        foreign_price_quote=Decimal("1"),
        fx_rate_krw=Decimal("100"),  # 100 < fx_rate_min(500) → fx_anomaly
    ))
    d = evaluate_kimp_guards(_full_safe_input(
        fx_rate_krw=Decimal("100"),
        kimp_result=bad,
    ))
    fx_anom = [r for r in d.reasons if r.code == GuardCode.FX_ANOMALY]
    assert fx_anom and fx_anom[0].severity == GuardSeverity.HIGH


# ── 18-23. Liquidity guard ─────────────────────────────────────


def test_orderbook_missing_blocks_when_required():
    cfg = KimpGuardConfig(require_orderbook_context=True)
    d = evaluate_kimp_guards(_full_safe_input(), config=cfg)
    assert GuardCode.ORDERBOOK_MISSING in d.blocked_by


def test_orderbook_missing_passes_when_not_required():
    d = evaluate_kimp_guards(_full_safe_input())
    # 기본 cfg.require_orderbook_context=False 이므로 호가 미수신은 사유 생성 안 함
    assert all(r.code != GuardCode.ORDERBOOK_MISSING for r in d.reasons)


def test_liquidity_invalid_bid_is_critical():
    d = evaluate_kimp_guards(_full_safe_input(
        domestic_bid=Decimal("0"),
        domestic_ask=Decimal("100"),
    ))
    invalid = [r for r in d.reasons
               if r.code == GuardCode.ORDERBOOK_INVALID]
    assert invalid and invalid[0].severity == GuardSeverity.CRITICAL


def test_liquidity_thin_size_blocks_when_min_set():
    cfg = KimpGuardConfig(min_bid_size=Decimal("10"))
    d = evaluate_kimp_guards(
        _full_safe_input(
            domestic_bid=Decimal("100"),
            domestic_ask=Decimal("101"),
            domestic_bid_size=Decimal("3"),
            domestic_ask_size=Decimal("100"),
        ),
        config=cfg,
    )
    thin = [r for r in d.reasons if r.code == GuardCode.LIQUIDITY_THIN]
    assert thin and thin[0].severity == GuardSeverity.HIGH


def test_liquidity_spread_too_wide_blocks():
    # bid=100, ask=110 → spread=10/(105) ≈ 952 bps > default 50
    d = evaluate_kimp_guards(_full_safe_input(
        domestic_bid=Decimal("100"),
        domestic_ask=Decimal("110"),
    ))
    wide = [r for r in d.reasons if r.code == GuardCode.SPREAD_WIDE]
    assert wide and wide[0].severity == GuardSeverity.HIGH


def test_orderbook_stale_when_timestamp_old():
    now = _now()
    d = evaluate_kimp_guards(_full_safe_input(
        domestic_bid=Decimal("100"),
        domestic_ask=Decimal("100.01"),
        orderbook_timestamp=now - timedelta(seconds=60),  # > 10s default
        now=now,
    ))
    stale = [r for r in d.reasons if r.code == GuardCode.ORDERBOOK_STALE]
    assert stale and stale[0].severity == GuardSeverity.HIGH


# ── 24-28. Bull market short guard ─────────────────────────────


def test_bull_market_short_blocks_reverse_kimp_short_leg():
    d = evaluate_kimp_guards(_full_safe_input(
        intended_kimp_state=KimpCandidateState.REVERSE_KIMP_CANDIDATE,
        short_leg_implied=True,
        market_regime="STRONG_BULL",
    ))
    assert GuardCode.BULL_MARKET_SHORT_BLOCKED in d.blocked_by


def test_bull_market_does_not_block_kimp_candidate():
    d = evaluate_kimp_guards(_full_safe_input(
        intended_kimp_state=KimpCandidateState.KIMP_CANDIDATE,
        short_leg_implied=True,
        market_regime="STRONG_BULL",
    ))
    assert all(r.code != GuardCode.BULL_MARKET_SHORT_BLOCKED
               for r in d.reasons)


def test_bull_market_does_not_block_when_no_short_leg():
    d = evaluate_kimp_guards(_full_safe_input(
        intended_kimp_state=KimpCandidateState.REVERSE_KIMP_CANDIDATE,
        short_leg_implied=False,
        market_regime="STRONG_BULL",
    ))
    assert all(r.code != GuardCode.BULL_MARKET_SHORT_BLOCKED
               for r in d.reasons)


def test_bull_market_disabled_by_config():
    cfg = KimpGuardConfig(block_reverse_kimp_short_in_bull_market=False)
    d = evaluate_kimp_guards(
        _full_safe_input(
            intended_kimp_state=KimpCandidateState.REVERSE_KIMP_CANDIDATE,
            short_leg_implied=True,
            market_regime="STRONG_BULL",
        ),
        config=cfg,
    )
    assert all(r.code != GuardCode.BULL_MARKET_SHORT_BLOCKED
               for r in d.reasons)


def test_bull_market_theme_blocks():
    d = evaluate_kimp_guards(_full_safe_input(
        intended_kimp_state=KimpCandidateState.REVERSE_KIMP_CANDIDATE,
        short_leg_implied=True,
        theme_tags=("ETF_INFLOW",),
    ))
    assert GuardCode.BULL_MARKET_SHORT_BLOCKED in d.blocked_by


# ── 29-33. Funding guard ───────────────────────────────────────


def test_funding_high_rate_blocks():
    # 2% per period → 200 bps > default 100 bps
    d = evaluate_kimp_guards(_full_safe_input(
        funding_rate_pct=Decimal("2.0"),
        funding_timestamp=_now() - timedelta(seconds=30),
    ))
    fr = [r for r in d.reasons if r.code == GuardCode.FUNDING_RISK_HIGH]
    assert fr and fr[0].severity == GuardSeverity.HIGH


def test_funding_direction_adverse_is_warning():
    d = evaluate_kimp_guards(_full_safe_input(
        funding_rate_pct=Decimal("-0.5"),
        funding_timestamp=_now() - timedelta(seconds=30),
        funding_position_side="short",
    ))
    fd = [r for r in d.reasons
          if r.code == GuardCode.FUNDING_DIRECTION_ADVERSE]
    assert fd and fd[0].severity == GuardSeverity.WARNING


def test_funding_stale_is_warning():
    now = _now()
    d = evaluate_kimp_guards(_full_safe_input(
        funding_rate_pct=Decimal("0.01"),
        funding_timestamp=now - timedelta(seconds=3600),
        now=now,
    ))
    fs = [r for r in d.reasons if r.code == GuardCode.FUNDING_STALE]
    assert fs and fs[0].severity == GuardSeverity.WARNING


def test_funding_missing_blocks_when_required():
    cfg = KimpGuardConfig(require_funding_context=True)
    d = evaluate_kimp_guards(_full_safe_input(), config=cfg)
    assert GuardCode.FUNDING_CONTEXT_MISSING in d.blocked_by


def test_funding_missing_silent_when_not_required():
    d = evaluate_kimp_guards(_full_safe_input())  # default require=False
    assert all(r.code != GuardCode.FUNDING_CONTEXT_MISSING
               for r in d.reasons)


# ── 34-36. Freshness guard ─────────────────────────────────────


def test_domestic_price_stale_blocks():
    now = _now()
    d = evaluate_kimp_guards(_full_safe_input(
        domestic_price_timestamp=now - timedelta(seconds=120),
        now=now,
    ))
    stale = [r for r in d.reasons
             if r.code == GuardCode.DOMESTIC_PRICE_STALE]
    assert stale and stale[0].severity == GuardSeverity.HIGH


def test_foreign_price_stale_blocks():
    now = _now()
    d = evaluate_kimp_guards(_full_safe_input(
        foreign_price_timestamp=now - timedelta(seconds=120),
        now=now,
    ))
    stale = [r for r in d.reasons
             if r.code == GuardCode.FOREIGN_PRICE_STALE]
    assert stale and stale[0].severity == GuardSeverity.HIGH


def test_price_timestamp_missing_is_warning():
    d = evaluate_kimp_guards(_full_safe_input(
        domestic_price_timestamp=None,
        foreign_price_timestamp=None,
    ))
    miss = [r for r in d.reasons
            if r.code == GuardCode.PRICE_TIMESTAMP_MISSING]
    assert miss and miss[0].severity == GuardSeverity.WARNING


# ── 37-39. Data quality guard ──────────────────────────────────


def test_data_quality_exclude_is_critical_by_default():
    d = evaluate_kimp_guards(_full_safe_input(data_quality_grade="EXCLUDE"))
    dq = [r for r in d.reasons if r.code == GuardCode.DATA_QUALITY_EXCLUDE]
    assert dq and dq[0].severity == GuardSeverity.CRITICAL
    assert d.allowed is False


def test_data_quality_warning_review_only_by_default():
    d = evaluate_kimp_guards(_full_safe_input(data_quality_grade="WARNING"))
    dq = [r for r in d.reasons if r.code == GuardCode.DATA_QUALITY_WARNING]
    assert dq and dq[0].severity == GuardSeverity.WARNING
    assert d.allowed is True
    assert d.required_review is True


def test_data_quality_warning_can_be_promoted_to_high():
    cfg = KimpGuardConfig(block_on_data_quality_warning=True)
    d = evaluate_kimp_guards(
        _full_safe_input(data_quality_grade="WARNING"),
        config=cfg,
    )
    dq = [r for r in d.reasons if r.code == GuardCode.DATA_QUALITY_WARNING]
    assert dq and dq[0].severity == GuardSeverity.HIGH
    assert d.allowed is False


# ── 40-41. Missing critical context ───────────────────────────


def test_missing_critical_context_when_fx_and_kimp_result_absent():
    # 다른 가드와 별개로 missing context 가드가 사유 추가
    d = evaluate_kimp_guards(KimpGuardInput(
        symbol="BTC",
        intended_kimp_state=KimpCandidateState.REVERSE_KIMP_CANDIDATE,
        notice_context_available=True,
        fx_source="upbit_quote",
        fx_timestamp=_now(),
        # fx_rate_krw=None, kimp_result=None
        data_quality_grade="GOOD",
        now=_now(),
    ))
    miss = [r for r in d.reasons
            if r.code == GuardCode.MISSING_CRITICAL_CONTEXT]
    assert miss and miss[0].severity == GuardSeverity.HIGH


def test_unknown_intended_state_is_warning_only_when_others_present():
    d = evaluate_kimp_guards(_full_safe_input(
        intended_kimp_state=KimpCandidateState.UNKNOWN,
    ))
    miss = [r for r in d.reasons
            if r.code == GuardCode.MISSING_CRITICAL_CONTEXT]
    assert miss and miss[0].severity == GuardSeverity.WARNING


# ── 42-43. KimpAgent hook ──────────────────────────────────────


def test_build_kimp_guard_context_shape():
    d = evaluate_kimp_guards(_full_safe_input(fx_rate_krw=Decimal("0")))
    ctx = build_kimp_guard_context(d)
    assert ctx["kind"] == "kimp_guard_context"
    assert ctx["direct_order_allowed"] is False
    assert ctx["used_for_order"] is False
    assert ctx["allowed"] is False
    assert ctx["recommended_action"] == RecommendedAction.BLOCK_CANDIDATE
    assert isinstance(ctx["blocked_by"], list)
    assert isinstance(ctx["reasons"], list)
    assert all(set(r.keys()) >= {"code", "severity", "source", "message"}
               for r in ctx["reasons"])


def test_build_kimp_guard_context_no_action_tokens():
    d = evaluate_kimp_guards(_full_safe_input())
    ctx = build_kimp_guard_context(d)
    blob = str(ctx)
    # recommended_action 라벨은 ALLOW_CANDIDATE/BLOCK_CANDIDATE/...
    # BUY/SELL/ENTER/EXIT 토큰이 단어 단위로 등장하면 안 됨.
    for tok in ("BUY", "SELL", "ENTER", "EXIT"):
        assert not re.search(rf"\b{tok}\b", blob), (
            f"{tok} token leaked into KimpGuardContext: {blob}"
        )


# ── 44-50. Static guards ───────────────────────────────────────


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
        r"withdraw|deposit)\s*\(",
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


def test_module_no_recommended_action_buy_sell_enter_exit():
    """recommended_action 값으로 BUY/SELL/ENTER/EXIT 문자열 리터럴 사용 금지.

    설명 목적의 docstring/주석 노출은 허용되지만 (사용자 지침: 본 모듈은 사용
    금지를 *명시* 한다), **따옴표로 감싼 문자열 리터럴** 로 등장하면 실패. 가드
    모듈은 ``RecommendedAction`` 의 ``ALLOW_CANDIDATE`` / ``BLOCK_CANDIDATE``
    류 라벨만 반환해야 한다.
    """
    text = _TARGET.read_text(encoding="utf-8")
    quoted_tokens = (
        r'"BUY"', r"'BUY'", r'"SELL"', r"'SELL'",
        r'"ENTER"', r"'ENTER'", r'"EXIT"', r"'EXIT'",
    )
    for needle in quoted_tokens:
        assert needle not in text, (
            f"forbidden action literal {needle} in production module"
        )


def test_direct_order_allowed_permanently_false_on_config_and_decision():
    cfg = KimpGuardConfig()
    assert cfg.direct_order_allowed is False
    assert cfg.used_for_order is False
    d = evaluate_kimp_guards(_full_safe_input())
    assert d.direct_order_allowed is False
    assert d.used_for_order is False


def test_dataclasses_are_frozen():
    cfg = KimpGuardConfig()
    d = evaluate_kimp_guards(_full_safe_input())
    with pytest.raises(Exception):
        cfg.direct_order_allowed = True  # type: ignore[misc]
    with pytest.raises(Exception):
        d.allowed = False  # type: ignore[misc]


# ── 51. Backward compat (#35 1차 보존) ─────────────────────────


def test_existing_kimp_guards_still_works():
    """기존 float 기반 #35 1차 모듈이 그대로 동작하는지 회귀."""
    from app.strategies.kimp_guards import evaluate_entry_guards
    report = evaluate_entry_guards(
        kimp_pct=-2.0,
        entry_threshold_pct=-1.8,
        deposit_withdrawal_ok=True,
        fx_anomaly_ok=True,
        liquidity_ok=True,
        bull_market_block=False,
        expected_edge_pct=1.0,
        total_cost_pct=0.2,
    )
    assert report.passed is True
    assert report.severity == "pass"
