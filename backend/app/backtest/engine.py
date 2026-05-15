"""BacktestRunner — 체크리스트 #60.

이벤트 루프 기반 단순 백테스트 엔진. 한 번에 한 심볼/한 포지션.

설계 원칙:
  - 결정론적 — 같은 입력 + 같은 strategy_fn → 같은 결과
  - LIVE 시스템과 분리 — Strategy 가 OrderGateway 를 거치지 않고 직접 호출됨.
    백테스트는 시뮬레이션이며 실전 안전 체인을 우회한다 (의도적).
  - 포지션 1개 가정 — 진입(BUY) 후 청산(SELL/CLOSE) 까지 추가 진입 무시.
  - 슬리피지/수수료는 ratio 로 적용. PaperBroker 와 동일 모델.

Strategy contract (콜러블):
    strategy_fn(bars_so_far: list[BacktestBar], position: dict | None)
        -> BacktestSignal
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Sequence

from app.agents.loss_tagging import TradeOutcome


@dataclass(frozen=True)
class BacktestBar:
    """OHLCV 봉 한 건."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class BacktestSignal:
    """Strategy 가 백테스트 엔진에 반환할 결정."""

    action: str            # BUY / SELL / CLOSE / HOLD / BLOCKED
    confidence: float = 0.5
    reason: str = ""


@dataclass(frozen=True)
class BacktestResult:
    """백테스트 1회 실행 결과."""

    trades: tuple[TradeOutcome, ...] = field(default_factory=tuple)
    equity_curve: tuple[float, ...] = field(default_factory=tuple)  # 봉별 누적
    initial_equity: float = 0.0
    final_equity: float = 0.0
    bars_processed: int = 0


# Strategy callable signature
StrategyFn = Callable[[list[BacktestBar], dict | None], BacktestSignal]


class BacktestRunner:
    """단순 이벤트 루프 백테스트 엔진."""

    def __init__(
        self,
        *,
        initial_equity: float = 10_000.0,
        fee_rate: float = 0.0005,        # taker 0.05%
        slippage_rate: float = 0.0005,
        size_pct: float = 1.0,            # 포지션당 자본의 비율
    ):
        if initial_equity <= 0:
            raise ValueError("initial_equity must be > 0")
        if not (0.0 < size_pct <= 1.0):
            raise ValueError("size_pct must be in (0, 1]")
        self.initial_equity = float(initial_equity)
        self.fee_rate = float(fee_rate)
        self.slippage_rate = float(slippage_rate)
        self.size_pct = float(size_pct)

    def run(
        self,
        strategy_fn: StrategyFn,
        bars: Sequence[BacktestBar],
        *,
        symbol: str = "TEST",
        strategy_name: str = "",
    ) -> BacktestResult:
        equity = self.initial_equity
        equity_curve: list[float] = []
        trades: list[TradeOutcome] = []
        position: dict | None = None  # {entry_price, qty, notional, entry_ts, side}
        bars_list: list[BacktestBar] = []

        for bar in bars:
            bars_list.append(bar)
            sig = strategy_fn(list(bars_list), position)

            # 포지션 보유 중 청산 신호?
            if position is not None and sig.action in {"SELL", "CLOSE"}:
                exit_price = self._apply_slippage(bar.close, side="exit_long")
                pnl_pct = (
                    (exit_price - position["entry_price"]) / position["entry_price"]
                ) * 100.0
                # 수수료: 진입 + 청산 양쪽에 적용
                fee_total = position["notional"] * self.fee_rate * 2
                pnl_usdt = position["notional"] * pnl_pct / 100.0 - fee_total
                equity += pnl_usdt
                trades.append(TradeOutcome(
                    symbol=symbol, side=position["side"],
                    entry_price=position["entry_price"], exit_price=exit_price,
                    qty=position["qty"], notional_usdt=position["notional"],
                    pnl_pct=round(pnl_pct, 6),
                    fee_usdt=round(fee_total, 6),
                    slippage_pct=round(self.slippage_rate * 100, 4),
                    entry_ts=position["entry_ts"], exit_ts=bar.ts,
                    strategy=strategy_name,
                    exit_reason=sig.reason or sig.action,
                ))
                position = None

            # 신규 진입 신호?
            elif position is None and sig.action == "BUY":
                entry_price = self._apply_slippage(bar.close, side="entry_long")
                notional = equity * self.size_pct
                qty = notional / entry_price if entry_price > 0 else 0
                position = {
                    "entry_price": entry_price,
                    "qty": qty,
                    "notional": notional,
                    "entry_ts": bar.ts,
                    "side": "BUY",
                }

            # equity curve 기록 (보유 중이면 mark-to-market)
            if position is not None:
                mark = (
                    (bar.close - position["entry_price"])
                    / position["entry_price"]
                ) * 100.0
                marked = equity + position["notional"] * mark / 100.0
                equity_curve.append(marked)
            else:
                equity_curve.append(equity)

        return BacktestResult(
            trades=tuple(trades),
            equity_curve=tuple(equity_curve),
            initial_equity=self.initial_equity,
            final_equity=equity,
            bars_processed=len(bars_list),
        )

    # ── 내부 ──────────────────────────────────────────────────────

    def _apply_slippage(self, price: float, *, side: str) -> float:
        """side='entry_long' 또는 'exit_long' — 진입은 더 비싸게, 청산은 더 싸게."""
        if side == "entry_long":
            return price * (1 + self.slippage_rate)
        return price * (1 - self.slippage_rate)
