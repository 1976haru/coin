"""체크리스트 #45 Performance Agent — 회귀 테스트.

검증:
  1. capability + AgentBase Protocol
  2. 빈 거래 → 0 metrics
  3. 단일 거래 (수익/손실)
  4. 다수 거래 — 승률 / PnL 합산
  5. profit factor (수익/손실/inf 케이스)
  6. max drawdown
  7. window 슬라이싱
  8. by_strategy 분해
  9. by_loss_category (LossTaggingAgent 통합)
 10. render_text markdown/plain
 11. decide AgentBase contract
 12. is_order_intent=False
"""
from __future__ import annotations
import pytest

from app.agents.performance import (
    PerformanceAgent, PerformanceMetrics, StrategyStats,
)
from app.agents.loss_tagging import TradeOutcome


# ── 헬퍼 ─────────────────────────────────────────────────────────

def make(pnl_pct: float, **kwargs) -> TradeOutcome:
    defaults = dict(
        symbol="BTC", side="BUY",
        entry_price=100.0, exit_price=100.0 * (1 + pnl_pct / 100),
        qty=1.0, notional_usdt=100.0, pnl_pct=pnl_pct,
    )
    defaults.update(kwargs)
    return TradeOutcome(**defaults)


# ── 1. capability + Protocol ────────────────────────────────────

def test_capability_metadata():
    cap = PerformanceAgent.capability
    assert cap.name == "performance"
    assert cap.has_veto_power is False
    assert cap.is_deterministic is True


def test_satisfies_agent_base_protocol():
    from app.agents.base import AgentBase
    assert isinstance(PerformanceAgent(), AgentBase)


# ── 2. 빈 거래 ──────────────────────────────────────────────────

def test_empty_trades_returns_zero_metrics():
    a = PerformanceAgent()
    m = a.analyze([])
    assert m.total_trades == 0
    assert m.win_rate == 0.0
    assert m.total_pnl_pct == 0.0
    assert m.profit_factor == 0.0
    assert m.max_drawdown_pct == 0.0


# ── 3. 단일 거래 ────────────────────────────────────────────────

def test_single_winning_trade():
    a = PerformanceAgent()
    m = a.analyze([make(2.5)])
    assert m.total_trades == 1
    assert m.wins == 1
    assert m.losses == 0
    assert m.win_rate == 1.0
    assert m.total_pnl_pct == 2.5
    assert m.avg_pnl_pct == 2.5
    assert m.profit_factor == float("inf")  # no losses
    assert m.max_drawdown_pct == 0.0


def test_single_losing_trade():
    a = PerformanceAgent()
    m = a.analyze([make(-1.0)])
    assert m.wins == 0
    assert m.losses == 1
    assert m.win_rate == 0.0
    assert m.profit_factor == 0.0  # no wins
    assert m.max_drawdown_pct == 1.0


# ── 4. 다수 거래 ────────────────────────────────────────────────

def test_multiple_trades_aggregate():
    a = PerformanceAgent()
    pnls = [1.0, -0.5, 2.0, -1.0, 0.8]
    m = a.analyze([make(p) for p in pnls])
    assert m.total_trades == 5
    assert m.wins == 3
    assert m.losses == 2
    assert m.win_rate == pytest.approx(0.6, abs=1e-6)
    assert m.total_pnl_pct == pytest.approx(2.3, abs=1e-6)
    assert m.avg_pnl_pct == pytest.approx(0.46, abs=1e-6)
    assert m.best_trade_pct == 2.0
    assert m.worst_trade_pct == -1.0


def test_breakeven_counted_separately():
    a = PerformanceAgent()
    m = a.analyze([make(0.0), make(1.0), make(-1.0)])
    assert m.breakevens == 1
    assert m.wins == 1
    assert m.losses == 1


def test_avg_win_loss_calculated():
    a = PerformanceAgent()
    m = a.analyze([make(1.0), make(3.0), make(-0.5), make(-1.5)])
    assert m.avg_win_pct == pytest.approx(2.0, abs=1e-6)
    assert m.avg_loss_pct == pytest.approx(-1.0, abs=1e-6)


# ── 5. profit factor ───────────────────────────────────────────

def test_profit_factor_normal_case():
    """gross_profit=4.0, gross_loss=2.0 → PF=2.0."""
    a = PerformanceAgent()
    m = a.analyze([make(1.0), make(3.0), make(-1.0), make(-1.0)])
    assert m.profit_factor == pytest.approx(2.0, abs=1e-6)


def test_profit_factor_inf_when_no_losses():
    a = PerformanceAgent()
    m = a.analyze([make(1.0), make(2.0)])
    assert m.profit_factor == float("inf")


def test_profit_factor_zero_when_no_wins():
    a = PerformanceAgent()
    m = a.analyze([make(-1.0), make(-2.0)])
    assert m.profit_factor == 0.0


# ── 6. max drawdown ────────────────────────────────────────────

def test_drawdown_zero_when_only_wins():
    a = PerformanceAgent()
    m = a.analyze([make(1.0), make(2.0), make(0.5)])
    assert m.max_drawdown_pct == 0.0


def test_drawdown_simple():
    """누적: 1, 3, 2, 4 — peak 3 → 2 = DD 1, peak 4 → final 4 = DD 0. max DD = 1."""
    a = PerformanceAgent()
    m = a.analyze([make(1.0), make(2.0), make(-1.0), make(2.0)])
    assert m.max_drawdown_pct == pytest.approx(1.0, abs=1e-6)


def test_drawdown_largest_streak():
    """Hard: cum 5, then -2, -2 = 1. DD = 4. then +3 = 4. peak = 5, final 4. DD still 4."""
    a = PerformanceAgent()
    m = a.analyze([make(5.0), make(-2.0), make(-2.0), make(3.0)])
    assert m.max_drawdown_pct == pytest.approx(4.0, abs=1e-6)


def test_drawdown_only_losses():
    a = PerformanceAgent()
    m = a.analyze([make(-1.0), make(-2.0), make(-3.0)])
    # cum: -1, -3, -6, peak=0 throughout → DD = 6
    assert m.max_drawdown_pct == pytest.approx(6.0, abs=1e-6)


# ── 7. window 슬라이싱 ─────────────────────────────────────────

def test_window_uses_last_n_trades():
    a = PerformanceAgent()
    pnls = [1.0, 2.0, 3.0, 4.0, -10.0]  # 마지막 큰 손실
    m_all = a.analyze([make(p) for p in pnls])
    m_recent = a.analyze([make(p) for p in pnls], window=2)
    assert m_all.total_trades == 5
    assert m_recent.total_trades == 2
    # 마지막 2건: 4.0, -10.0
    assert m_recent.total_pnl_pct == pytest.approx(-6.0, abs=1e-6)


def test_window_larger_than_data_returns_all():
    a = PerformanceAgent()
    m = a.analyze([make(1.0), make(2.0)], window=10)
    assert m.total_trades == 2


def test_window_zero_or_none_uses_all():
    a = PerformanceAgent()
    m1 = a.analyze([make(1.0), make(2.0)], window=None)
    m2 = a.analyze([make(1.0), make(2.0)], window=0)
    assert m1.total_trades == 2
    assert m2.total_trades == 2


# ── 8. by_strategy ─────────────────────────────────────────────

def test_by_strategy_groups_correctly():
    a = PerformanceAgent()
    trades = [
        make(1.0, strategy="trend"),
        make(2.0, strategy="trend"),
        make(-1.0, strategy="kimp"),
        make(0.5, strategy="kimp"),
    ]
    m = a.analyze(trades)
    by_strategy = {s.name: s for s in m.by_strategy}
    assert "trend" in by_strategy
    assert "kimp" in by_strategy
    assert by_strategy["trend"].trades == 2
    assert by_strategy["trend"].wins == 2
    assert by_strategy["kimp"].trades == 2
    assert by_strategy["kimp"].wins == 1


def test_by_strategy_includes_unknown_for_blank_strategy():
    a = PerformanceAgent()
    m = a.analyze([make(1.0), make(-0.5, strategy="kimp")])
    names = {s.name for s in m.by_strategy}
    assert "(unknown)" in names
    assert "kimp" in names


# ── 9. by_loss_category ────────────────────────────────────────

def test_by_loss_category_only_for_losses():
    a = PerformanceAgent()
    trades = [
        make(1.0),  # 수익 — 무시
        make(-1.0, exit_reason="역김프 확대 손절"),  # STOP_LOSS
        make(-0.5, exit_reason="시간 청산"),         # TIME_STOP
        make(-2.0,
             entry_kimp_pct=-1.8, exit_kimp_pct=-3.5),  # KIMP_DIVERGENCE
    ]
    m = a.analyze(trades)
    assert m.by_loss_category.get("STOP_LOSS") == 1
    assert m.by_loss_category.get("TIME_STOP") == 1
    assert m.by_loss_category.get("KIMP_DIVERGENCE") == 1


def test_by_loss_category_empty_when_no_losses():
    a = PerformanceAgent()
    m = a.analyze([make(1.0), make(2.0)])
    assert m.by_loss_category == {}


# ── 10. render_text ────────────────────────────────────────────

def test_render_markdown_includes_metrics():
    a = PerformanceAgent()
    m = a.analyze([make(1.0, strategy="trend"), make(-0.5, strategy="trend")])
    text = a.render_text(m, format="markdown")
    assert "## 성과 지표" in text
    assert "거래 수" in text
    assert "승률" in text
    assert "Max Drawdown" in text


def test_render_markdown_shows_strategy_section_when_present():
    a = PerformanceAgent()
    m = a.analyze([make(1.0, strategy="trend"), make(2.0, strategy="kimp")])
    text = a.render_text(m, format="markdown")
    assert "### 전략별" in text
    assert "trend" in text
    assert "kimp" in text


def test_render_markdown_shows_loss_categories_when_present():
    a = PerformanceAgent()
    m = a.analyze([make(-1.0, exit_reason="손절")])
    text = a.render_text(m, format="markdown")
    assert "### 손실 카테고리" in text
    assert "STOP_LOSS" in text


def test_render_no_trades_returns_brief():
    a = PerformanceAgent()
    m = a.analyze([])
    text = a.render_text(m)
    assert "거래 없음" in text


def test_render_inf_profit_factor_shown_as_symbol():
    a = PerformanceAgent()
    m = a.analyze([make(1.0), make(2.0)])  # no losses → PF=inf
    text = a.render_text(m, format="markdown")
    assert "∞" in text


def test_render_plain_format():
    a = PerformanceAgent()
    m = a.analyze([make(1.0), make(-0.5)])
    text = a.render_text(m, format="plain")
    assert "성과 지표" in text
    assert "승률" in text


# ── 11. decide ──────────────────────────────────────────────────

def test_decide_with_outcomes_returns_metrics_in_explain():
    a = PerformanceAgent()
    d = a.decide({}, {"outcomes": [make(1.0), make(-0.5)]})
    assert d.action == "HOLD"
    assert "성과" in d.explain_text


def test_decide_without_outcomes_returns_message():
    a = PerformanceAgent()
    d = a.decide({}, {})
    assert "outcomes" in d.reason or "outcomes" in d.explain_text


def test_decide_with_window_param():
    a = PerformanceAgent()
    d = a.decide({}, {"outcomes": [make(1.0), make(2.0), make(-3.0)],
                       "window": 1})
    assert "1건" in d.reason


# ── 12. is_order_intent=False ──────────────────────────────────

def test_decision_is_order_intent_false():
    a = PerformanceAgent()
    d = a.decide({}, {"outcomes": [make(1.0)]})
    assert d.is_order_intent is False


# ── 13. 직렬화 ──────────────────────────────────────────────────

def test_metrics_to_dict_structure():
    a = PerformanceAgent()
    m = a.analyze([make(1.0, strategy="trend"), make(-0.5, strategy="trend")])
    d = m.to_dict()
    for k in ("total_trades", "wins", "losses", "win_rate",
              "total_pnl_pct", "profit_factor", "max_drawdown_pct",
              "by_strategy", "by_loss_category"):
        assert k in d
    assert isinstance(d["by_strategy"], list)


def test_inf_profit_factor_serialized_as_string():
    a = PerformanceAgent()
    m = a.analyze([make(1.0), make(2.0)])
    d = m.to_dict()
    assert d["profit_factor"] == "inf"


# ── 14. e2e — 종합 시나리오 ────────────────────────────────────

def test_e2e_realistic_trading_history():
    """50% 승률, 일부 손실은 KIMP_DIVERGENCE/STOP_LOSS, 전략 2개 mix."""
    a = PerformanceAgent()
    trades = [
        make(1.5, strategy="trend"),
        make(-0.8, strategy="trend", exit_reason="손절"),
        make(2.0, strategy="trend"),
        make(-1.2, strategy="kimp",
             entry_kimp_pct=-1.8, exit_kimp_pct=-3.5),
        make(0.6, strategy="kimp"),
        make(-0.5, strategy="kimp", exit_reason="시간 청산"),
    ]
    m = a.analyze(trades)

    assert m.total_trades == 6
    assert m.wins == 3
    assert m.losses == 3
    assert m.win_rate == 0.5
    # gross profit = 1.5+2.0+0.6=4.1, gross loss = 0.8+1.2+0.5=2.5 → PF ≈ 1.64
    assert m.profit_factor == pytest.approx(1.64, abs=0.01)
    # 손실 카테고리
    assert m.by_loss_category.get("STOP_LOSS") == 1
    assert m.by_loss_category.get("KIMP_DIVERGENCE") == 1
    assert m.by_loss_category.get("TIME_STOP") == 1
    # 전략별
    by_strat = {s.name: s for s in m.by_strategy}
    assert by_strat["trend"].trades == 3
    assert by_strat["kimp"].trades == 3
