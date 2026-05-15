"""체크리스트 #60 Backtest Engine — 회귀 테스트.

검증:
  1. BacktestRunner 빈 bars 처리
  2. 단일 BUY → CLOSE 시나리오
  3. 슬리피지/수수료 적용
  4. 포지션 보유 중 BUY 무시 (단일 포지션 가정)
  5. equity curve 길이 = bars 길이
  6. TradeOutcome 필드 채워짐 (entry/exit/pnl/strategy 등)
  7. 결정론 — 같은 입력 → 같은 결과
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.engine import (
    BacktestBar, BacktestSignal, BacktestRunner,
)


# ── 헬퍼 ─────────────────────────────────────────────────────────

def make_bars(prices: list[float]) -> list[BacktestBar]:
    base = datetime(2026, 5, 10, tzinfo=timezone.utc)
    return [
        BacktestBar(ts=base + timedelta(minutes=i),
                    open=p, high=p, low=p, close=p, volume=100.0)
        for i, p in enumerate(prices)
    ]


def buy_then_close_after(n_bars_held: int):
    """첫 봉에 BUY, n_bars 후 CLOSE 하는 단순 strategy_fn."""
    state = {"entry_index": None}

    def fn(bars, position):
        idx = len(bars) - 1
        if position is None:
            state["entry_index"] = idx
            return BacktestSignal(action="BUY", confidence=0.8)
        if idx - state["entry_index"] >= n_bars_held:
            return BacktestSignal(action="CLOSE", confidence=0.8, reason="시간 청산")
        return BacktestSignal(action="HOLD")

    return fn


def hold_forever(bars, position):
    return BacktestSignal(action="HOLD")


# ── 1. 빈 bars ──────────────────────────────────────────────────

def test_empty_bars_returns_zero_trades():
    r = BacktestRunner().run(hold_forever, [])
    assert r.trades == ()
    assert r.equity_curve == ()
    assert r.bars_processed == 0


# ── 2. 단일 진입 → 청산 ─────────────────────────────────────────

def test_single_buy_close_creates_one_trade():
    bars = make_bars([100, 101, 102, 105])  # 5% 상승
    runner = BacktestRunner(initial_equity=1000, fee_rate=0, slippage_rate=0)
    r = runner.run(buy_then_close_after(2), bars, symbol="BTC")
    assert len(r.trades) == 1
    t = r.trades[0]
    assert t.symbol == "BTC"
    assert t.entry_price == pytest.approx(100, abs=1e-6)


def test_uptrend_produces_positive_pnl():
    bars = make_bars([100, 105, 110, 115])
    runner = BacktestRunner(initial_equity=1000, fee_rate=0, slippage_rate=0)
    r = runner.run(buy_then_close_after(2), bars)
    assert r.trades[0].pnl_pct > 0
    assert r.final_equity > r.initial_equity


def test_downtrend_produces_loss():
    bars = make_bars([100, 95, 90, 85])
    runner = BacktestRunner(initial_equity=1000, fee_rate=0, slippage_rate=0)
    r = runner.run(buy_then_close_after(2), bars)
    assert r.trades[0].pnl_pct < 0
    assert r.final_equity < r.initial_equity


# ── 3. 슬리피지/수수료 ─────────────────────────────────────────

def test_slippage_makes_entry_more_expensive():
    bars = make_bars([100, 100, 100, 100])
    no_slip = BacktestRunner(slippage_rate=0, fee_rate=0)
    with_slip = BacktestRunner(slippage_rate=0.01, fee_rate=0)
    r1 = no_slip.run(buy_then_close_after(2), bars)
    r2 = with_slip.run(buy_then_close_after(2), bars)
    # 진입은 비싸게, 청산은 싸게 → 슬리피지 있는 쪽이 손실
    assert r2.trades[0].pnl_pct < r1.trades[0].pnl_pct


def test_fee_reduces_pnl():
    bars = make_bars([100, 105, 110, 115])
    no_fee = BacktestRunner(fee_rate=0, slippage_rate=0)
    with_fee = BacktestRunner(fee_rate=0.01, slippage_rate=0)
    r1 = no_fee.run(buy_then_close_after(2), bars)
    r2 = with_fee.run(buy_then_close_after(2), bars)
    assert r2.final_equity < r1.final_equity


# ── 4. 단일 포지션 ──────────────────────────────────────────────

def test_buy_ignored_while_in_position():
    """포지션 보유 중 BUY 신호는 무시 (단일 포지션 가정)."""
    bars = make_bars([100, 101, 102, 103, 104, 100])

    # 매 봉마다 BUY 신호
    def always_buy_until_close(bars, position):
        if len(bars) >= 5:
            return BacktestSignal(action="CLOSE", reason="end")
        return BacktestSignal(action="BUY")

    r = BacktestRunner(fee_rate=0, slippage_rate=0).run(always_buy_until_close, bars)
    # 5개 BUY 시도지만 trade 1건만
    assert len(r.trades) == 1


# ── 5. equity curve 길이 ────────────────────────────────────────

def test_equity_curve_length_matches_bars():
    bars = make_bars([100, 101, 102])
    r = BacktestRunner().run(hold_forever, bars)
    assert len(r.equity_curve) == 3


# ── 6. TradeOutcome 필드 ────────────────────────────────────────

def test_trade_outcome_has_strategy_name_and_timestamps():
    bars = make_bars([100, 110, 120])
    r = BacktestRunner(fee_rate=0, slippage_rate=0).run(
        buy_then_close_after(1), bars,
        symbol="BTC/USDT", strategy_name="trend_following",
    )
    t = r.trades[0]
    assert t.strategy == "trend_following"
    assert t.symbol == "BTC/USDT"
    assert t.entry_ts is not None
    assert t.exit_ts is not None
    assert t.exit_ts > t.entry_ts


# ── 7. 결정론 ───────────────────────────────────────────────────

def test_determinism():
    bars = make_bars([100, 105, 110, 115, 120, 100, 90, 95])
    runner = BacktestRunner(initial_equity=1000)
    r1 = runner.run(buy_then_close_after(3), bars)
    r2 = runner.run(buy_then_close_after(3), bars)
    assert r1.final_equity == r2.final_equity
    assert len(r1.trades) == len(r2.trades)


# ── 8. 잘못된 인자 ──────────────────────────────────────────────

def test_invalid_initial_equity_raises():
    with pytest.raises(ValueError):
        BacktestRunner(initial_equity=0)


def test_invalid_size_pct_raises():
    with pytest.raises(ValueError):
        BacktestRunner(size_pct=0)
    with pytest.raises(ValueError):
        BacktestRunner(size_pct=1.5)
