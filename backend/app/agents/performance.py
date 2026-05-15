"""PerformanceAgent — 체크리스트 #45 Performance Agent.

청산된 거래 시퀀스(``TradeOutcome``)로부터 성과 지표를 계산.
결정론 — LLM 사용 안 함. 거래 결정 없음 (분석 only).

집계 지표:
  - total_trades / wins / losses / breakevens
  - win_rate
  - total_pnl_pct / avg_pnl_pct
  - avg_win_pct / avg_loss_pct
  - profit_factor (gross_profit / gross_loss)
  - max_drawdown_pct (cumulative pnl 기준)
  - best_trade_pct / worst_trade_pct
  - by_strategy (전략별 분해)
  - by_loss_category (LossTaggingAgent 통합)
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

from .base import AgentCapability
from .loss_tagging import LossTaggingAgent, TradeOutcome


@dataclass(frozen=True)
class StrategyStats:
    name: str
    trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl_pct: float


@dataclass(frozen=True)
class PerformanceMetrics:
    total_trades: int
    wins: int
    losses: int
    breakevens: int
    win_rate: float
    total_pnl_pct: float
    avg_pnl_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    profit_factor: float          # inf 가능 (loss 0 시)
    max_drawdown_pct: float       # 양수 (음의 누적 변화)
    best_trade_pct: float
    worst_trade_pct: float
    by_strategy: tuple[StrategyStats, ...] = field(default_factory=tuple)
    by_loss_category: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["by_strategy"] = [asdict(s) for s in self.by_strategy]
        # profit_factor inf → 문자열로 직렬화 가능하도록
        if self.profit_factor == float("inf"):
            d["profit_factor"] = "inf"
        return d


# ── Agent ────────────────────────────────────────────────────────

class PerformanceAgent:
    """거래 성과 분석 Agent."""

    capability = AgentCapability(
        name="performance",
        role="explain",
        description="청산된 거래 시퀀스 → 성과 지표 (승률/PnL/MDD/PF + 전략별·손실 카테고리별 분해).",
        has_veto_power=False,
        is_deterministic=True,
        requires_llm=False,
        inputs=("outcomes", "window"),
    )

    BREAKEVEN_TOLERANCE = 1e-9

    # ── 핵심 분석 ─────────────────────────────────────────────────

    def analyze(
        self,
        outcomes: Iterable[TradeOutcome],
        *,
        window: int | None = None,
        loss_tagger: LossTaggingAgent | None = None,
    ) -> PerformanceMetrics:
        all_trades = list(outcomes)
        if window is not None and window > 0:
            trades = all_trades[-window:]
        else:
            trades = all_trades

        if not trades:
            return PerformanceMetrics(
                total_trades=0, wins=0, losses=0, breakevens=0,
                win_rate=0.0,
                total_pnl_pct=0.0, avg_pnl_pct=0.0,
                avg_win_pct=0.0, avg_loss_pct=0.0,
                profit_factor=0.0,
                max_drawdown_pct=0.0,
                best_trade_pct=0.0, worst_trade_pct=0.0,
            )

        wins = [t for t in trades if t.pnl_pct > self.BREAKEVEN_TOLERANCE]
        losses = [t for t in trades if t.pnl_pct < -self.BREAKEVEN_TOLERANCE]
        breakevens = len(trades) - len(wins) - len(losses)

        total_pnl = sum(t.pnl_pct for t in trades)
        avg_pnl = total_pnl / len(trades)

        avg_win = (sum(t.pnl_pct for t in wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(t.pnl_pct for t in losses) / len(losses)) if losses else 0.0

        gross_profit = sum(t.pnl_pct for t in wins)
        gross_loss = abs(sum(t.pnl_pct for t in losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (
            float("inf") if gross_profit > 0 else 0.0
        )

        max_dd = self._max_drawdown([t.pnl_pct for t in trades])
        best = max(trades, key=lambda t: t.pnl_pct).pnl_pct
        worst = min(trades, key=lambda t: t.pnl_pct).pnl_pct

        by_strategy = self._aggregate_by_strategy(trades)
        by_loss_cat = self._aggregate_loss_categories(losses, loss_tagger)

        return PerformanceMetrics(
            total_trades=len(trades),
            wins=len(wins), losses=len(losses), breakevens=breakevens,
            win_rate=(len(wins) / len(trades)) if trades else 0.0,
            total_pnl_pct=round(total_pnl, 4),
            avg_pnl_pct=round(avg_pnl, 4),
            avg_win_pct=round(avg_win, 4),
            avg_loss_pct=round(avg_loss, 4),
            profit_factor=(profit_factor if profit_factor == float("inf")
                           else round(profit_factor, 4)),
            max_drawdown_pct=round(max_dd, 4),
            best_trade_pct=round(best, 4),
            worst_trade_pct=round(worst, 4),
            by_strategy=tuple(by_strategy),
            by_loss_category=by_loss_cat,
        )

    # ── 렌더링 ────────────────────────────────────────────────────

    def render_text(
        self,
        metrics: PerformanceMetrics,
        *,
        format: str = "markdown",
    ) -> str:
        if metrics.total_trades == 0:
            return "거래 없음"

        pf_str = ("∞" if metrics.profit_factor == float("inf")
                  else f"{metrics.profit_factor:.2f}")

        if format == "markdown":
            lines = [
                "## 성과 지표",
                f"- **거래 수**: {metrics.total_trades} (승 {metrics.wins} / 패 {metrics.losses} / 무 {metrics.breakevens})",
                f"- **승률**: {metrics.win_rate * 100:.1f}%",
                f"- **누적 PnL**: {metrics.total_pnl_pct:+.2f}%",
                f"- **평균 PnL**: {metrics.avg_pnl_pct:+.2f}%",
                f"- **평균 승**: {metrics.avg_win_pct:+.2f}% / **평균 패**: {metrics.avg_loss_pct:+.2f}%",
                f"- **Profit Factor**: {pf_str}",
                f"- **Max Drawdown**: {metrics.max_drawdown_pct:.2f}%",
                f"- **베스트**: {metrics.best_trade_pct:+.2f}% / **워스트**: {metrics.worst_trade_pct:+.2f}%",
            ]
            if metrics.by_strategy:
                lines.append("\n### 전략별")
                for s in metrics.by_strategy:
                    lines.append(
                        f"- `{s.name}`: 거래 {s.trades} (승 {s.wins} / 패 {s.losses}) "
                        f"승률 {s.win_rate * 100:.1f}% / 누적 {s.total_pnl_pct:+.2f}%"
                    )
            if metrics.by_loss_category:
                lines.append("\n### 손실 카테고리")
                for cat, n in sorted(metrics.by_loss_category.items(),
                                      key=lambda kv: -kv[1]):
                    lines.append(f"- `{cat}`: {n}")
            return "\n".join(lines)

        # plain
        lines = [
            "=== 성과 지표 ===",
            f"  거래 {metrics.total_trades}: 승 {metrics.wins} / 패 {metrics.losses} / 무 {metrics.breakevens}",
            f"  승률 {metrics.win_rate * 100:.1f}% / 누적 {metrics.total_pnl_pct:+.2f}%",
            f"  평균 PnL {metrics.avg_pnl_pct:+.2f}% (승 {metrics.avg_win_pct:+.2f}, 패 {metrics.avg_loss_pct:+.2f})",
            f"  PF {pf_str} / MDD {metrics.max_drawdown_pct:.2f}%",
            f"  베스트 {metrics.best_trade_pct:+.2f}% / 워스트 {metrics.worst_trade_pct:+.2f}%",
        ]
        return "\n".join(lines)

    # ── AgentBase contract ────────────────────────────────────────

    def decide(self, input_signal: dict, context: dict | None = None) -> Any:
        from .orchestrator import AgentDecision
        ctx = context or {}
        outcomes = ctx.get("outcomes")
        if outcomes is None:
            return AgentDecision(
                "HOLD", 0.0,
                "PerformanceAgent: outcomes 미제공",
                explain_text="ctx['outcomes'] 에 TradeOutcome 시퀀스 필요",
            )
        metrics = self.analyze(outcomes, window=ctx.get("window"))
        return AgentDecision(
            "HOLD", 0.0,
            f"PerformanceAgent: {metrics.total_trades}건 분석",
            explain_text=self.render_text(metrics, format="markdown"),
        )

    # ── 내부 헬퍼 ─────────────────────────────────────────────────

    @staticmethod
    def _max_drawdown(pnls: list[float]) -> float:
        """누적 PnL 기준 최대 drawdown (%, 양수)."""
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cum += p
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def _aggregate_by_strategy(
        self, trades: list[TradeOutcome],
    ) -> list[StrategyStats]:
        groups: dict[str, list[TradeOutcome]] = {}
        for t in trades:
            key = t.strategy or "(unknown)"
            groups.setdefault(key, []).append(t)
        out: list[StrategyStats] = []
        for name, items in sorted(groups.items()):
            wins = sum(1 for x in items if x.pnl_pct > self.BREAKEVEN_TOLERANCE)
            losses = sum(1 for x in items if x.pnl_pct < -self.BREAKEVEN_TOLERANCE)
            total_pnl = sum(x.pnl_pct for x in items)
            out.append(StrategyStats(
                name=name,
                trades=len(items),
                wins=wins, losses=losses,
                win_rate=(wins / len(items)) if items else 0.0,
                total_pnl_pct=round(total_pnl, 4),
            ))
        return out

    @staticmethod
    def _aggregate_loss_categories(
        losses: list[TradeOutcome],
        tagger: LossTaggingAgent | None,
    ) -> dict[str, int]:
        if not losses:
            return {}
        tagger = tagger or LossTaggingAgent()
        counts: dict[str, int] = {}
        for t in losses:
            analysis = tagger.analyze(t)
            cat = analysis.category
            counts[cat] = counts.get(cat, 0) + 1
        return counts
