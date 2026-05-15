"""체크리스트 #43 Theme Insight Agent — 회귀 테스트.

검증:
  1. capability + AgentBase Protocol
  2. 빈 source — neutral outlook
  3. 테마 수집 (와일드카드 거래소 포함)
  4. 뉴스 수집 (severity 합산 + 헤드라인)
  5. 공지 통합 (delisting/deposit_suspended/warning)
  6. 김프 이상치
  7. overall_outlook 결정 로직 (block/caution/neutral)
  8. render_text — markdown/plain
  9. decide — AgentDecision (HOLD + explain_text)
 10. is_order_intent=False / Protocol
"""
from __future__ import annotations
import pytest

from app.agents.theme_insight import ThemeInsightAgent, SymbolBriefing
from app.market.themes import ThemeRegistry, NewsRegistry
from app.market.notices import NoticeRegistry


# ── 1. Capability + Protocol ────────────────────────────────────

def test_capability_metadata():
    cap = ThemeInsightAgent.capability
    assert cap.name == "theme_insight"
    assert cap.has_veto_power is False
    assert cap.is_deterministic is True


def test_satisfies_agent_base_protocol():
    from app.agents.base import AgentBase
    assert isinstance(ThemeInsightAgent(), AgentBase)


# ── 2. 빈 source ────────────────────────────────────────────────

def test_empty_sources_returns_neutral_briefing():
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit")
    assert b.overall_outlook == "neutral"
    assert b.themes == ()
    assert b.news_count == 0
    assert b.tradable is True
    assert b.deposit_withdrawal_ok is True


def test_empty_briefing_has_no_kimp():
    b = ThemeInsightAgent().briefing(symbol="BTC", exchange="upbit")
    assert b.kimp_pct is None
    assert b.kimp_anomaly is False


# ── 3. 테마 수집 ────────────────────────────────────────────────

def test_themes_collected_from_registry():
    th = ThemeRegistry()
    th.tag("AI", "FET", "upbit")
    th.tag("L1", "FET", "*")
    a = ThemeInsightAgent()
    b = a.briefing(symbol="FET", exchange="upbit", themes=th)
    assert "AI" in b.themes
    assert "L1" in b.themes


def test_themes_empty_when_no_match():
    th = ThemeRegistry()
    th.tag("AI", "FET", "upbit")
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", themes=th)
    assert b.themes == ()


# ── 4. 뉴스 수집 ────────────────────────────────────────────────

def test_news_collected_with_severity_max():
    nw = NewsRegistry()
    nw.add("FOMC", "금리 발표", severity="warn", related_symbols=("BTC",))
    nw.add("MACRO", "BTC dominance 60%", severity="info")
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", news=nw)
    assert b.news_severity == "warn"
    assert b.news_count == 2
    assert "금리 발표" in b.news_headlines


def test_news_block_severity_propagates():
    nw = NewsRegistry()
    nw.add("HACK", "거래소 해킹", severity="block", related_symbols=("BTC",))
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", news=nw)
    assert b.news_severity == "block"
    assert b.overall_outlook == "block"


def test_market_wide_news_applies_to_all_symbols():
    nw = NewsRegistry()
    nw.add("MACRO", "전체 영향 뉴스", severity="warn")  # related_symbols=()
    a = ThemeInsightAgent()
    b = a.briefing(symbol="ETH", exchange="upbit", news=nw)
    assert b.news_count == 1


# ── 5. 공지 통합 ────────────────────────────────────────────────

def test_delisting_marks_not_tradable():
    nr = NoticeRegistry()
    nr.add("upbit", "BTC", "DELISTING", "상장폐지")
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", notices=nr)
    assert b.tradable is False
    assert b.overall_outlook == "block"


def test_deposit_suspended_marks_dwd_false():
    nr = NoticeRegistry()
    nr.add("upbit", "BTC", "DEPOSIT_SUSPENDED", "입금 중단")
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", notices=nr)
    assert b.deposit_withdrawal_ok is False
    assert b.overall_outlook == "block"


def test_warning_only_does_not_block_but_caution():
    nr = NoticeRegistry()
    nr.add("upbit", "BTC", "WARNING", "유의종목")
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", notices=nr)
    assert b.tradable is True
    assert b.has_warning is True
    assert b.overall_outlook == "caution"


def test_notice_reasons_collected():
    nr = NoticeRegistry()
    nr.add("upbit", "BTC", "WARNING", "유의종목 지정")
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", notices=nr)
    assert any("유의" in r for r in b.notice_reasons)


# ── 6. 김프 이상치 ──────────────────────────────────────────────

def test_kimp_normal_ok():
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", kimp_pct=-2.0)
    assert b.kimp_pct == -2.0
    assert b.kimp_anomaly is False


def test_kimp_extreme_marks_anomaly_and_block_outlook():
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", kimp_pct=-15.0)
    assert b.kimp_anomaly is True
    assert b.overall_outlook == "block"


# ── 7. overall_outlook 결정 ─────────────────────────────────────

def test_outlook_block_takes_priority_over_caution():
    nr = NoticeRegistry()
    nr.add("upbit", "BTC", "WARNING", "유의")
    nw = NewsRegistry()
    nw.add("HACK", "해킹", severity="block", related_symbols=("BTC",))
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit",
                    notices=nr, news=nw)
    assert b.overall_outlook == "block"


def test_outlook_caution_when_warn_news_only():
    nw = NewsRegistry()
    nw.add("FOMC", "금리", severity="warn", related_symbols=("BTC",))
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", news=nw)
    assert b.overall_outlook == "caution"


def test_outlook_neutral_clean():
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", kimp_pct=-2.0)
    assert b.overall_outlook == "neutral"


# ── 8. render_text ──────────────────────────────────────────────

def test_render_markdown_includes_outlook_emoji():
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", kimp_pct=-15.0)
    text = a.render_text(b, format="markdown")
    assert "🔴" in text  # block outlook
    assert "BTC" in text
    assert "UPBIT" in text


def test_render_markdown_neutral_emoji():
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit")
    text = a.render_text(b, format="markdown")
    assert "⚪" in text


def test_render_markdown_includes_themes():
    th = ThemeRegistry()
    th.tag("AI", "FET", "upbit")
    a = ThemeInsightAgent()
    b = a.briefing(symbol="FET", exchange="upbit", themes=th)
    text = a.render_text(b, format="markdown")
    assert "AI" in text


def test_render_markdown_no_themes_shows_none():
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit")
    text = a.render_text(b, format="markdown")
    assert "(없음)" in text


def test_render_markdown_includes_news_headlines():
    nw = NewsRegistry()
    nw.add("FOMC", "금리 인상 발표", severity="warn",
           related_symbols=("BTC",))
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", news=nw)
    text = a.render_text(b, format="markdown")
    assert "금리 인상 발표" in text


def test_render_plain_format():
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", kimp_pct=-2.0)
    text = a.render_text(b, format="plain")
    assert "BTC" in text
    assert "UPBIT" in text
    assert "neutral" in text


def test_render_includes_kimp_anomaly_warning():
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", kimp_pct=-15.0)
    text = a.render_text(b, format="markdown")
    assert "이상치" in text


# ── 9. decide ───────────────────────────────────────────────────

def test_decide_returns_hold_with_briefing():
    th = ThemeRegistry(); nw = NewsRegistry(); nr = NoticeRegistry()
    a = ThemeInsightAgent()
    d = a.decide(
        {"symbol": "BTC"},
        {"exchange": "upbit",
         "themes_registry": th, "news_registry": nw, "notices_registry": nr,
         "kimp_pct": -2.0},
    )
    assert d.action == "HOLD"
    assert d.is_order_intent is False
    assert "BTC" in d.explain_text


def test_decide_passes_kimp_anomaly_to_briefing():
    a = ThemeInsightAgent()
    d = a.decide({"symbol": "BTC"},
                  {"exchange": "upbit", "kimp_pct": -15.0})
    assert "이상치" in d.explain_text


# ── 10. is_order_intent=False ──────────────────────────────────

def test_decision_is_order_intent_always_false():
    a = ThemeInsightAgent()
    d = a.decide({"symbol": "BTC"}, {"exchange": "upbit"})
    assert d.is_order_intent is False


# ── 11. 직렬화 ──────────────────────────────────────────────────

def test_briefing_to_dict_structure():
    a = ThemeInsightAgent()
    b = a.briefing(symbol="BTC", exchange="upbit", kimp_pct=-2.0)
    d = b.to_dict()
    for k in ("symbol", "exchange", "themes", "news_severity", "news_count",
              "tradable", "deposit_withdrawal_ok", "kimp_pct", "kimp_anomaly",
              "overall_outlook"):
        assert k in d


# ── 12. e2e — 통합 시나리오 ────────────────────────────────────

def test_e2e_block_scenario_combines_everything():
    """delisting + block 뉴스 + kimp 이상 → block outlook + 모든 요소 표시."""
    th = ThemeRegistry()
    th.tag("memecoin", "DOGE", "upbit")
    nr = NoticeRegistry()
    nr.add("upbit", "DOGE", "DELISTING", "상폐")
    nw = NewsRegistry()
    nw.add("REGULATION", "규제 강화", severity="block",
           related_symbols=("DOGE",))
    a = ThemeInsightAgent()
    b = a.briefing(symbol="DOGE", exchange="upbit",
                    themes=th, news=nw, notices=nr,
                    kimp_pct=-15.0)
    assert b.overall_outlook == "block"
    assert b.tradable is False
    assert b.kimp_anomaly is True
    assert "memecoin" in b.themes


def test_e2e_clean_positive_scenario():
    th = ThemeRegistry()
    th.tag("AI", "FET", "upbit")
    nr = NoticeRegistry()
    nw = NewsRegistry()
    a = ThemeInsightAgent()
    b = a.briefing(symbol="FET", exchange="upbit",
                    themes=th, news=nw, notices=nr,
                    kimp_pct=-1.0)
    assert b.overall_outlook == "neutral"
    assert b.tradable is True
    assert "AI" in b.themes
