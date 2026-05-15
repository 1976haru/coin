"""체크리스트 #40 Anomaly Agent (boosted) — 회귀 테스트.

검증:
  1. 기존 3개 차단 키 (anomaly / data_quality_alarm / freshness_stale)
  2. QualityReport (#17) 통합 — has_blocking 시 차단
  3. NoticeRegistry SymbolNoticeStatus (#18) — !tradable / !deposit_withdrawal_ok
  4. news_severity == "block" (#19) — hard veto
  5. kimp_anomaly_hint — hard veto
  6. anomaly_context_for 헬퍼 — 다중 source 통합
  7. clean ctx → 통과
  8. is_order_intent=False / AgentBase Protocol
"""
from __future__ import annotations
from datetime import datetime, timezone

import pytest

from app.agents.anomaly import AnomalyAgent
from app.agents.anomaly_context import anomaly_context_for
from app.agents.orchestrator import AgentDecision


# ── 1. 기존 차단 키 (회귀) ──────────────────────────────────────

def test_blocks_on_anomaly_flag():
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"anomaly": True})
    assert d.risk_veto is True
    assert "이상 데이터" in d.reason


def test_blocks_on_data_quality_alarm():
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"data_quality_alarm": True})
    assert d.risk_veto is True


def test_blocks_on_freshness_stale():
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"freshness_stale": True})
    assert d.risk_veto is True


# ── 2. QualityReport 통합 ───────────────────────────────────────

def test_quality_report_with_blocks_triggers_veto():
    """app.market.quality.QualityReport 가 has_blocking=True 일 때 차단."""
    from app.market.quality import QualityReport, QualityCheck
    qr = QualityReport(
        label="BTC@upbit",
        checks=(
            QualityCheck("spread", False, "block",
                         "spread 5.0% > 한도 0.5%", value=5.0),
        ),
    )
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"quality_report": qr})
    assert d.risk_veto is True
    assert "spread" in d.reason or "품질" in d.reason


def test_quality_report_dict_with_blocks_triggers_veto():
    """dict 형식도 지원."""
    qr_dict = {
        "has_blocking": True,
        "blocks": [{"reason": "spread 너무 넓음"}],
    }
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"quality_report": qr_dict})
    assert d.risk_veto is True


def test_quality_report_no_blocks_passes():
    from app.market.quality import QualityReport, QualityCheck
    qr = QualityReport(
        label="BTC@upbit",
        checks=(QualityCheck("spread", True, "ok", "0.1%"),),
    )
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"quality_report": qr})
    assert d.risk_veto is False


# ── 3. NoticeRegistry 통합 ──────────────────────────────────────

def test_notice_status_delisting_triggers_veto():
    from app.market.notices import NoticeRegistry, assess_symbol_notices
    r = NoticeRegistry()
    r.add("upbit", "BTC", "DELISTING", "상폐")
    ns = assess_symbol_notices(r, "BTC", "upbit")
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"notice_status": ns})
    assert d.risk_veto is True
    assert "거래 불가" in d.reason or "상폐" in d.reason


def test_notice_status_deposit_suspended_triggers_veto():
    from app.market.notices import NoticeRegistry, assess_symbol_notices
    r = NoticeRegistry()
    r.add("upbit", "BTC", "DEPOSIT_SUSPENDED", "입금 중단")
    ns = assess_symbol_notices(r, "BTC", "upbit")
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"notice_status": ns})
    assert d.risk_veto is True
    assert "입출금" in d.reason


def test_notice_status_clean_passes():
    from app.market.notices import NoticeRegistry, assess_symbol_notices
    r = NoticeRegistry()
    ns = assess_symbol_notices(r, "BTC", "upbit")
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"notice_status": ns})
    assert d.risk_veto is False


def test_notice_status_warning_only_does_not_block():
    """유의종목(WARNING)은 has_warning=True 이지만 tradable=True → 통과."""
    from app.market.notices import NoticeRegistry, assess_symbol_notices
    r = NoticeRegistry()
    r.add("upbit", "BTC", "WARNING", "유의")
    ns = assess_symbol_notices(r, "BTC", "upbit")
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"notice_status": ns})
    assert d.risk_veto is False


# ── 4. news_severity == "block" ─────────────────────────────────

def test_news_severity_block_triggers_veto():
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"news_severity": "block"})
    assert d.risk_veto is True
    assert "뉴스" in d.reason or "block" in d.reason.lower()


def test_news_severity_warn_does_not_block():
    """warn 은 SignalQuality 가 페널티만 부여. Anomaly 는 차단 안 함."""
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"news_severity": "warn"})
    assert d.risk_veto is False


def test_news_severity_info_passes():
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"news_severity": "info"})
    assert d.risk_veto is False


# ── 5. kimp_anomaly_hint ────────────────────────────────────────

def test_kimp_anomaly_hint_triggers_veto():
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"kimp_anomaly_hint": True})
    assert d.risk_veto is True
    assert "김프" in d.reason


def test_kimp_normal_passes():
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, {"kimp_anomaly_hint": False})
    assert d.risk_veto is False


# ── 6. anomaly_context_for 헬퍼 ─────────────────────────────────

def test_context_for_with_notices_returns_notice_status():
    from app.market.notices import NoticeRegistry
    r = NoticeRegistry()
    r.add("upbit", "BTC", "DELISTING", "상폐")
    ctx = anomaly_context_for(symbol="BTC", exchange="upbit", notices=r)
    assert "notice_status" in ctx
    assert ctx["notice_status"].tradable is False


def test_context_for_with_themes_news_returns_news_severity():
    from app.market.themes import ThemeRegistry, NewsRegistry
    th = ThemeRegistry()
    nw = NewsRegistry()
    nw.add("HACK", "거래소 해킹", severity="block")
    ctx = anomaly_context_for(
        symbol="BTC", exchange="upbit", themes=th, news=nw,
    )
    assert ctx.get("news_severity") == "block"


def test_context_for_with_quality_report():
    from app.market.quality import QualityReport, QualityCheck
    qr = QualityReport(
        label="BTC@upbit",
        checks=(QualityCheck("spread", False, "block", "wide spread"),),
    )
    ctx = anomaly_context_for(quality_report=qr)
    assert "quality_report" in ctx
    assert ctx["quality_report"].has_blocking is True


def test_context_for_with_freshness_stale():
    ctx = anomaly_context_for(freshness_stale=True)
    assert ctx.get("freshness_stale") is True


def test_context_for_with_kimp_anomaly():
    """kimp ±10% 초과 → kimp_anomaly_hint=True."""
    ctx = anomaly_context_for(kimp_pct=-15.0)
    assert ctx.get("kimp_anomaly_hint") is True


def test_context_for_with_kimp_normal():
    ctx = anomaly_context_for(kimp_pct=-2.0)
    assert "kimp_anomaly_hint" not in ctx


def test_context_for_empty_returns_empty_dict():
    assert anomaly_context_for() == {}


# ── 7. anomaly_context + decide e2e ─────────────────────────────

def test_e2e_clean_context_passes():
    from app.market.notices import NoticeRegistry
    from app.market.themes import ThemeRegistry, NewsRegistry
    ctx = anomaly_context_for(
        symbol="BTC", exchange="upbit",
        notices=NoticeRegistry(),
        themes=ThemeRegistry(), news=NewsRegistry(),
    )
    a = AnomalyAgent()
    d = a.decide({"action": "BUY", "confidence": 0.85}, ctx)
    assert d.risk_veto is False


def test_e2e_delisting_blocks():
    from app.market.notices import NoticeRegistry
    r = NoticeRegistry()
    r.add("upbit", "BTC", "DELISTING", "상폐")
    ctx = anomaly_context_for(symbol="BTC", exchange="upbit", notices=r)
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, ctx)
    assert d.risk_veto is True


def test_e2e_block_news_blocks():
    from app.market.themes import ThemeRegistry, NewsRegistry
    th = ThemeRegistry()
    nw = NewsRegistry()
    nw.add("REGULATION", "강력 규제 발표", severity="block",
           related_symbols=("BTC",))
    ctx = anomaly_context_for(
        symbol="BTC", exchange="upbit", themes=th, news=nw,
    )
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, ctx)
    assert d.risk_veto is True


def test_e2e_kimp_anomaly_blocks():
    ctx = anomaly_context_for(kimp_pct=-15.0)
    a = AnomalyAgent()
    d = a.decide({"action": "BUY"}, ctx)
    assert d.risk_veto is True


# ── 8. Orchestrator 통합 ────────────────────────────────────────

def test_orchestrator_uses_boosted_anomaly_agent():
    """Orchestrator 가 이미 AnomalyAgent 를 쓴다 — boosted 통합 검증."""
    from app.agents.orchestrator import AgentOrchestrator
    o = AgentOrchestrator()
    d = o.decide(
        {"action": "BUY", "confidence": 0.9},
        {"news_severity": "block"},
    )
    assert d.action == "HOLD"
    assert d.risk_veto is True


# ── 9. is_order_intent / Protocol ──────────────────────────────

@pytest.mark.parametrize("ctx", [
    {},
    {"anomaly": True},
    {"news_severity": "block"},
    {"kimp_anomaly_hint": True},
])
def test_is_order_intent_always_false(ctx):
    a = AnomalyAgent()
    d = a.decide({"action": "BUY", "confidence": 0.85}, ctx)
    assert d.is_order_intent is False


def test_satisfies_agent_base_protocol():
    from app.agents.base import AgentBase
    assert isinstance(AnomalyAgent(), AgentBase)
