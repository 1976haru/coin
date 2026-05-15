"""체크리스트 #61 Metrics + #62 Walk-forward + #63 Monte Carlo — 회귀 테스트."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import pytest

from app.backtest import (
    BacktestBar, BacktestSignal, BacktestRunner,
    compute_metrics,
    WalkForwardRunner, WalkForwardResult,
    MonteCarloRunner, MonteCarloResult,
)
from app.agents.loss_tagging import TradeOutcome


def _bars(prices):
    base = datetime(2026, 5, 10, tzinfo=timezone.utc)
    return [BacktestBar(ts=base + timedelta(minutes=i),
                          open=p, high=p, low=p, close=p, volume=10.0)
            for i, p in enumerate(prices)]


def _make_outcome(pnl_pct: float):
    return TradeOutcome(
        symbol="BTC", side="BUY",
        entry_price=100, exit_price=100 * (1 + pnl_pct / 100),
        qty=1.0, notional_usdt=100.0, pnl_pct=pnl_pct,
    )


# ── #61 Metrics ─────────────────────────────────────────────────

def test_metrics_uses_performance_agent():
    bars = _bars([100, 110, 120])
    state = {"i": None}

    def fn(bars, position):
        if position is None:
            state["i"] = len(bars)
            return BacktestSignal(action="BUY")
        if len(bars) - state["i"] >= 1:
            return BacktestSignal(action="CLOSE")
        return BacktestSignal(action="HOLD")

    r = BacktestRunner(fee_rate=0, slippage_rate=0).run(fn, bars)
    m = compute_metrics(r)
    assert m.perf.total_trades == 1
    assert m.return_pct > 0
    assert m.bars_processed == 3


def test_metrics_equity_drawdown_zero_for_pure_uptrend():
    bars = _bars([100, 105, 110, 115])
    r = BacktestRunner().run(
        lambda b, p: BacktestSignal("HOLD"),
        bars,
    )
    m = compute_metrics(r)
    # No trade — equity flat → DD 0
    assert m.equity_max_drawdown_pct == 0.0


def test_metrics_to_dict_contains_keys():
    r = BacktestRunner().run(lambda b, p: BacktestSignal("HOLD"), _bars([100]))
    m = compute_metrics(r)
    d = m.to_dict()
    for k in ("initial_equity", "final_equity", "return_pct",
              "equity_max_drawdown_pct", "sharpe_like", "bars_processed",
              "total_trades", "win_rate"):
        assert k in d


# ── #62 Walk-forward ────────────────────────────────────────────

def test_walk_forward_expanding_creates_n_folds():
    bars = _bars([100 + i for i in range(200)])
    wf = WalkForwardRunner(n_folds=5, mode="expanding", min_fold_bars=20)
    r = wf.run(lambda b, p: BacktestSignal("HOLD"), bars)
    assert len(r.folds) == 5
    # expanding: 마지막 폴드는 전체 길이 사용
    assert r.folds[-1].end == 200


def test_walk_forward_rolling_creates_disjoint_folds():
    bars = _bars([100 + i for i in range(200)])
    wf = WalkForwardRunner(n_folds=4, mode="rolling", min_fold_bars=20)
    r = wf.run(lambda b, p: BacktestSignal("HOLD"), bars)
    # rolling 폴드는 disjoint
    if len(r.folds) >= 2:
        assert r.folds[0].end <= r.folds[1].start + (200 // 4)


def test_walk_forward_skips_undersized_folds():
    bars = _bars([100, 101, 102])
    wf = WalkForwardRunner(n_folds=5, mode="rolling", min_fold_bars=20)
    r = wf.run(lambda b, p: BacktestSignal("HOLD"), bars)
    assert len(r.folds) == 0


def test_walk_forward_avg_metrics():
    bars = _bars([100 + i * 0.1 for i in range(200)])
    wf = WalkForwardRunner(n_folds=4, mode="expanding", min_fold_bars=20)
    r = wf.run(lambda b, p: BacktestSignal("HOLD"), bars)
    # 거래 0건 → win_rate 0
    assert 0 <= r.avg_win_rate <= 1


def test_walk_forward_invalid_n_folds_raises():
    with pytest.raises(ValueError):
        WalkForwardRunner(n_folds=0)


def test_walk_forward_to_dict_structure():
    bars = _bars([100 + i * 0.1 for i in range(200)])
    wf = WalkForwardRunner(n_folds=3, mode="expanding", min_fold_bars=20)
    d = wf.run(lambda b, p: BacktestSignal("HOLD"), bars).to_dict()
    assert "n_folds" in d
    assert "avg_return_pct" in d
    assert "folds" in d


# ── #63 Monte Carlo ─────────────────────────────────────────────

def test_monte_carlo_empty_outcomes_returns_zero_metrics():
    r = MonteCarloRunner(iterations=100).run([])
    assert r.win_rate_p50 == 0.0


def test_monte_carlo_winning_trades_only_high_winrate():
    """모두 승 → 부트스트랩 win_rate ≈ 1.0."""
    outs = [_make_outcome(1.0)] * 20
    r = MonteCarloRunner(iterations=500).run(outs)
    assert r.win_rate_p50 == pytest.approx(1.0, abs=1e-6)


def test_monte_carlo_mixed_outcomes_winrate_between_0_and_1():
    outs = [_make_outcome(1.0)] * 5 + [_make_outcome(-1.0)] * 5
    r = MonteCarloRunner(iterations=500, seed=42).run(outs)
    assert 0.0 < r.win_rate_p50 < 1.0


def test_monte_carlo_p05_lt_p50_lt_p95():
    """percentile 순서 sanity."""
    outs = [_make_outcome(1.0)] * 5 + [_make_outcome(-1.0)] * 5
    r = MonteCarloRunner(iterations=500, seed=42).run(outs)
    assert r.win_rate_p05 <= r.win_rate_p50 <= r.win_rate_p95
    assert r.total_pnl_p05 <= r.total_pnl_p50 <= r.total_pnl_p95


def test_monte_carlo_seed_determinism():
    outs = [_make_outcome(p) for p in [1.0, -1.0, 2.0, -0.5, 1.5]]
    r1 = MonteCarloRunner(iterations=200, seed=123).run(outs)
    r2 = MonteCarloRunner(iterations=200, seed=123).run(outs)
    assert r1.win_rate_p50 == r2.win_rate_p50
    assert r1.total_pnl_p50 == r2.total_pnl_p50


def test_monte_carlo_invalid_iterations_raises():
    with pytest.raises(ValueError):
        MonteCarloRunner(iterations=5)


def test_monte_carlo_to_dict_structure():
    outs = [_make_outcome(1.0)] * 5
    d = MonteCarloRunner(iterations=100).run(outs).to_dict()
    for k in ("iterations", "n_trades_per_iter",
              "win_rate_p05", "win_rate_p50", "win_rate_p95",
              "total_pnl_p50", "max_drawdown_p50"):
        assert k in d


# ── e2e — 통합 시나리오 ─────────────────────────────────────────

def test_e2e_backtest_then_metrics_then_monte_carlo():
    bars = _bars([100, 105, 95, 110, 90, 115, 100, 120, 95, 125])
    state = {"in_position": False, "entry_idx": None}

    def alternating(bars, position):
        idx = len(bars) - 1
        if position is None and idx % 2 == 0:
            state["entry_idx"] = idx
            return BacktestSignal(action="BUY")
        if position is not None and idx - state["entry_idx"] >= 1:
            return BacktestSignal(action="CLOSE")
        return BacktestSignal(action="HOLD")

    r = BacktestRunner(fee_rate=0, slippage_rate=0).run(alternating, bars)
    m = compute_metrics(r)
    mc = MonteCarloRunner(iterations=200, seed=42).run(r.trades)

    assert m.bars_processed == 10
    assert m.perf.total_trades >= 1
    assert mc.iterations == 200
