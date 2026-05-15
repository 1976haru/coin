"""체크리스트 #44 Loss Tagging Agent — 회귀 테스트.

검증:
  1. capability + AgentBase Protocol
  2. 수익 거래 — is_loss=False
  3. STOP_LOSS / TIME_STOP exit_reason 매칭
  4. SLIPPAGE / SPREAD 임계값
  5. REGIME_CHANGE 전환 감지
  6. KIMP_DIVERGENCE 확대 분류
  7. NEWS_SHOCK 보유 중 block 뉴스
  8. FUNDING_BURN 펀딩 비용 비중
  9. FEE_HEAVY 수수료 비중
 10. UNKNOWN fallback
 11. primary + contributing 분류
 12. render_text markdown/plain
 13. decide AgentBase contract
 14. is_order_intent=False
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import pytest

from app.agents.loss_tagging import (
    LossTaggingAgent, TradeOutcome, LossAnalysis, LossTag,
)


# ── 헬퍼 ─────────────────────────────────────────────────────────

def make_outcome(**kwargs) -> TradeOutcome:
    defaults = dict(
        symbol="BTC", side="BUY",
        entry_price=100.0, exit_price=99.0,
        qty=1.0, notional_usdt=100.0,
        pnl_pct=-1.0,
    )
    defaults.update(kwargs)
    return TradeOutcome(**defaults)


# ── 1. capability + Protocol ────────────────────────────────────

def test_capability_metadata():
    cap = LossTaggingAgent.capability
    assert cap.name == "loss_tagging"
    assert cap.has_veto_power is False
    assert cap.is_deterministic is True


def test_satisfies_agent_base_protocol():
    from app.agents.base import AgentBase
    assert isinstance(LossTaggingAgent(), AgentBase)


# ── 2. 수익 거래 ────────────────────────────────────────────────

def test_profit_returns_no_loss_analysis():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=2.5)
    r = a.analyze(o)
    assert r.is_loss is False
    assert r.primary_tag is None
    assert r.category == "UNKNOWN"  # 손실 아니므로 의미 없음


def test_zero_pnl_treated_as_no_loss():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=0.0)
    r = a.analyze(o)
    assert r.is_loss is False


# ── 3. STOP_LOSS / TIME_STOP ────────────────────────────────────

def test_stop_loss_classified_from_korean_reason():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-3.0, exit_reason="역김프 확대 손절")
    r = a.analyze(o)
    assert r.primary_tag.category == "STOP_LOSS"


def test_stop_loss_classified_from_english_reason():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-1.0, exit_reason="stop_loss triggered")
    r = a.analyze(o)
    assert r.primary_tag.category == "STOP_LOSS"


def test_time_stop_classified():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-0.5, exit_reason="시간 청산 (15분 경과)")
    r = a.analyze(o)
    assert r.primary_tag.category == "TIME_STOP"


# ── 4. SLIPPAGE / SPREAD ────────────────────────────────────────

def test_slippage_added_as_contributing():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-1.0, slippage_pct=0.8)
    r = a.analyze(o)
    cats = [t.category for t in (r.primary_tag, *r.contributing_tags) if t]
    assert "SLIPPAGE" in cats


def test_slippage_below_threshold_not_added():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-1.0, slippage_pct=0.1)
    r = a.analyze(o)
    cats = [t.category for t in (r.primary_tag, *r.contributing_tags) if t]
    assert "SLIPPAGE" not in cats


def test_spread_added_as_contributing():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-1.0, spread_pct=1.0)
    r = a.analyze(o)
    cats = [t.category for t in (r.primary_tag, *r.contributing_tags) if t]
    assert "SPREAD" in cats


# ── 5. REGIME_CHANGE ────────────────────────────────────────────

def test_regime_change_classified_when_different():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-2.0,
                      entry_regime="TREND_UP", exit_regime="TREND_DOWN")
    r = a.analyze(o)
    cats = [t.category for t in (r.primary_tag, *r.contributing_tags) if t]
    assert "REGIME_CHANGE" in cats


def test_regime_same_not_added():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-1.0,
                      entry_regime="TREND_UP", exit_regime="TREND_UP")
    r = a.analyze(o)
    cats = [t.category for t in (r.primary_tag, *r.contributing_tags) if t]
    assert "REGIME_CHANGE" not in cats


# ── 6. KIMP_DIVERGENCE ──────────────────────────────────────────

def test_kimp_divergence_classified_as_primary():
    """역김프 -1.8 진입 → -3.0 확대 (Δ 1.2) → primary."""
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-1.5,
                      entry_kimp_pct=-1.8, exit_kimp_pct=-3.0)
    r = a.analyze(o)
    assert r.primary_tag.category == "KIMP_DIVERGENCE"


def test_kimp_convergence_not_classified():
    """진입 -2.0 → 청산 -0.5 (수렴) — divergence 아님."""
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-1.0,
                      entry_kimp_pct=-2.0, exit_kimp_pct=-0.5)
    r = a.analyze(o)
    cats = [t.category for t in (r.primary_tag, *r.contributing_tags) if t]
    assert "KIMP_DIVERGENCE" not in cats


def test_kimp_small_change_not_classified():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-1.0,
                      entry_kimp_pct=-1.8, exit_kimp_pct=-2.0)  # Δ 0.2 < 0.5
    r = a.analyze(o)
    cats = [t.category for t in (r.primary_tag, *r.contributing_tags) if t]
    assert "KIMP_DIVERGENCE" not in cats


# ── 7. NEWS_SHOCK ───────────────────────────────────────────────

def test_news_block_during_hold_classified_primary():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-2.0, news_severity_during_hold="block")
    r = a.analyze(o)
    assert r.primary_tag.category == "NEWS_SHOCK"


def test_news_warn_during_hold_not_block_classified():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-1.0, news_severity_during_hold="warn")
    r = a.analyze(o)
    cats = [t.category for t in (r.primary_tag, *r.contributing_tags) if t]
    assert "NEWS_SHOCK" not in cats


# ── 8. FUNDING_BURN ─────────────────────────────────────────────

def test_funding_burn_classified():
    """funding 0.6% / pnl 1.0% = 60% > 50% → FUNDING_BURN."""
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-1.0, funding_cost_pct=0.6)
    r = a.analyze(o)
    cats = [t.category for t in (r.primary_tag, *r.contributing_tags) if t]
    assert "FUNDING_BURN" in cats


def test_funding_low_not_classified():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-1.0, funding_cost_pct=0.1)
    r = a.analyze(o)
    cats = [t.category for t in (r.primary_tag, *r.contributing_tags) if t]
    assert "FUNDING_BURN" not in cats


# ── 9. FEE_HEAVY ────────────────────────────────────────────────

def test_fee_heavy_classified():
    """수수료 1 USDT / 100 notional = 1% / pnl 1% = 100% > 50%."""
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-1.0, fee_usdt=1.0, notional_usdt=100.0)
    r = a.analyze(o)
    cats = [t.category for t in (r.primary_tag, *r.contributing_tags) if t]
    assert "FEE_HEAVY" in cats


def test_fee_low_not_classified():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-2.0, fee_usdt=0.1, notional_usdt=100.0)
    r = a.analyze(o)
    cats = [t.category for t in (r.primary_tag, *r.contributing_tags) if t]
    assert "FEE_HEAVY" not in cats


# ── 10. UNKNOWN fallback ────────────────────────────────────────

def test_unknown_when_no_specific_signal():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-0.5)  # 모든 signal 0 / 미설정
    r = a.analyze(o)
    assert r.primary_tag.category == "UNKNOWN"


def test_unknown_fallback_when_only_contributing():
    """slippage 만 있고 primary 후보 없으면 UNKNOWN 으로 fallback."""
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-0.5, slippage_pct=0.8)
    r = a.analyze(o)
    assert r.primary_tag.category == "UNKNOWN"
    assert any(t.category == "SLIPPAGE" for t in r.contributing_tags)


# ── 11. primary + contributing 조합 ─────────────────────────────

def test_stop_loss_primary_with_slippage_contributing():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-3.0, exit_reason="역김프 확대 손절",
                      slippage_pct=0.8)
    r = a.analyze(o)
    assert r.primary_tag.category == "STOP_LOSS"
    assert any(t.category == "SLIPPAGE" for t in r.contributing_tags)


def test_kimp_divergence_primary_overrides_unknown():
    """divergence 가 primary 후보로 등록되어 UNKNOWN 대신 사용됨."""
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-2.0,
                      entry_kimp_pct=-1.8, exit_kimp_pct=-3.5)
    r = a.analyze(o)
    assert r.primary_tag.category == "KIMP_DIVERGENCE"


def test_first_primary_wins_when_multiple():
    """STOP_LOSS 가 KIMP_DIVERGENCE 보다 먼저 등록 — exit_reason 우선."""
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-3.0, exit_reason="손절",
                      entry_kimp_pct=-1.8, exit_kimp_pct=-3.5)
    r = a.analyze(o)
    assert r.primary_tag.category == "STOP_LOSS"
    # KIMP_DIVERGENCE 는 contributing 으로 강등
    assert any(t.category == "KIMP_DIVERGENCE" for t in r.contributing_tags)


# ── 12. render_text ─────────────────────────────────────────────

def test_render_markdown_for_loss():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-2.5, exit_reason="손절", slippage_pct=0.7)
    r = a.analyze(o)
    text = a.render_text(r, format="markdown")
    assert "## 손실 분석" in text
    assert "STOP_LOSS" in text
    assert "SLIPPAGE" in text


def test_render_plain_for_loss():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-2.5, exit_reason="손절")
    r = a.analyze(o)
    text = a.render_text(r, format="plain")
    assert "손실 분석" in text
    assert "STOP_LOSS" in text


def test_render_for_profit_returns_brief_string():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=2.5)
    r = a.analyze(o)
    text = a.render_text(r)
    assert "수익" in text


# ── 13. decide AgentBase contract ───────────────────────────────

def test_decide_with_outcome_returns_analysis_in_explain():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-2.0, exit_reason="손절")
    d = a.decide({}, {"outcome": o})
    assert d.action == "HOLD"
    assert "STOP_LOSS" in d.reason or "STOP_LOSS" in d.explain_text


def test_decide_without_outcome_returns_message():
    a = LossTaggingAgent()
    d = a.decide({}, {})
    assert "outcome" in d.reason or "outcome" in d.explain_text


def test_decide_with_wrong_type_outcome():
    a = LossTaggingAgent()
    d = a.decide({}, {"outcome": "not a TradeOutcome"})
    assert "outcome" in d.reason or "outcome" in d.explain_text


# ── 14. is_order_intent=False ──────────────────────────────────

def test_decision_is_order_intent_false():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-1.0)
    d = a.decide({}, {"outcome": o})
    assert d.is_order_intent is False


# ── 15. 직렬화 ──────────────────────────────────────────────────

def test_loss_analysis_to_dict_structure():
    a = LossTaggingAgent()
    o = make_outcome(pnl_pct=-2.0, exit_reason="손절", slippage_pct=0.8)
    r = a.analyze(o)
    d = r.to_dict()
    assert d["is_loss"] is True
    assert d["category"] == "STOP_LOSS"
    assert d["primary_tag"]["category"] == "STOP_LOSS"
    assert isinstance(d["contributing_tags"], list)


# ── 16. e2e — 종합 시나리오 ────────────────────────────────────

def test_e2e_kimp_loss_with_multiple_factors():
    """역김프 진입 → 손절 + 슬리피지 + funding burn."""
    a = LossTaggingAgent()
    o = TradeOutcome(
        symbol="BTC", side="OPEN_REVERSE_KIMP",
        entry_price=98_000_000, exit_price=95_000_000,
        qty=0.001, notional_usdt=100.0,
        pnl_pct=-3.0,
        fee_usdt=0.05, slippage_pct=0.6, spread_pct=0.1,
        exit_reason="역김프 확대 손절",
        entry_kimp_pct=-1.8, exit_kimp_pct=-3.5,
        funding_cost_pct=2.0,
    )
    r = a.analyze(o)
    cats_all = {r.primary_tag.category} | {t.category for t in r.contributing_tags}
    assert r.primary_tag.category == "STOP_LOSS"
    assert {"SLIPPAGE", "KIMP_DIVERGENCE", "FUNDING_BURN"}.issubset(cats_all)
