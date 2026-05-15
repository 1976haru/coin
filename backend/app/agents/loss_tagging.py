"""LossTaggingAgent — 체크리스트 #44 Loss Tagging Agent.

청산된 거래(TradeOutcome)를 받아 **왜 손실이 발생했는지** 카테고리로 분류한다.
회고/리포트/리스크 모델 학습 input 으로 활용.

결정론 — LLM 사용 안 함. 거래 결정 없음 (분석 only).

카테고리:
  - STOP_LOSS       : 손절 트리거 (가격이 stop 도달)
  - TIME_STOP       : 시간 청산 (보유 시간 한도)
  - SLIPPAGE        : 슬리피지가 임계값 초과 (체결가 ≠ 호가)
  - SPREAD          : 진입 시 스프레드 비용 과대
  - REGIME_CHANGE   : 진입 ↔ 청산 사이 regime 전환
  - KIMP_DIVERGENCE : 김프가 수렴 대신 확대
  - NEWS_SHOCK      : 보유 중 block 등급 뉴스
  - FUNDING_BURN    : 펀딩 비용이 엣지를 초과
  - FEE_HEAVY       : 수수료가 손실의 큰 비중
  - UNKNOWN         : 위 어디에도 분류 불가
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Literal

from .base import AgentCapability


LossCategory = Literal[
    "STOP_LOSS", "TIME_STOP", "SLIPPAGE", "SPREAD",
    "REGIME_CHANGE", "KIMP_DIVERGENCE",
    "NEWS_SHOCK", "FUNDING_BURN", "FEE_HEAVY", "UNKNOWN",
]
TagSeverity = Literal["primary", "contributing"]


# ── 데이터 타입 ──────────────────────────────────────────────────

@dataclass(frozen=True)
class TradeOutcome:
    """청산된 거래 한 건의 사후 데이터."""

    symbol: str
    side: str                            # BUY/SELL/OPEN_REVERSE_KIMP/CLOSE 등
    entry_price: float
    exit_price: float
    qty: float
    notional_usdt: float
    pnl_pct: float                        # 음수면 손실
    fee_usdt: float = 0.0
    slippage_pct: float = 0.0             # 진입 + 청산 평균 (%, e.g. 0.5 = 0.5%)
    spread_pct: float = 0.0               # 진입 시 스프레드 (%)
    entry_ts: datetime | None = None
    exit_ts: datetime | None = None
    strategy: str = ""
    exit_reason: str = ""                 # KimpSignal/Trend 등이 남긴 reason
    # 컨텍스트 — 진입/청산 시점
    entry_kimp_pct: float | None = None
    exit_kimp_pct: float | None = None
    entry_regime: str | None = None
    exit_regime: str | None = None
    funding_cost_pct: float = 0.0
    news_severity_during_hold: str = "info"  # 보유 기간 최고 severity


@dataclass(frozen=True)
class LossTag:
    category: LossCategory
    severity: TagSeverity                # primary 1개, contributing 다수
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LossAnalysis:
    """청산된 거래의 분류 결과."""

    pnl_pct: float
    is_loss: bool
    primary_tag: LossTag | None
    contributing_tags: tuple[LossTag, ...] = field(default_factory=tuple)

    @property
    def category(self) -> LossCategory:
        return self.primary_tag.category if self.primary_tag else "UNKNOWN"

    def to_dict(self) -> dict:
        return {
            "pnl_pct": self.pnl_pct,
            "is_loss": self.is_loss,
            "primary_tag": self.primary_tag.to_dict() if self.primary_tag else None,
            "contributing_tags": [t.to_dict() for t in self.contributing_tags],
            "category": self.category,
        }


# ── Agent ────────────────────────────────────────────────────────

class LossTaggingAgent:
    """손실 거래 원인 분류 Agent."""

    SLIPPAGE_THRESHOLD_PCT = 0.5
    SPREAD_THRESHOLD_PCT = 0.5
    KIMP_DIVERGENCE_THRESHOLD_PCT = 0.5
    FEE_HEAVY_RATIO = 0.5         # fee/abs(pnl_usdt) > 0.5 면 fee_heavy
    FUNDING_BURN_RATIO = 0.5      # funding_cost / abs(pnl) > 0.5

    capability = AgentCapability(
        name="loss_tagging",
        role="explain",
        description="청산된 거래의 손실 원인 분류 (10개 카테고리, primary + contributing).",
        has_veto_power=False,
        is_deterministic=True,
        requires_llm=False,
        inputs=("outcome",),
    )

    # ── 핵심 분석 ─────────────────────────────────────────────────

    def analyze(self, outcome: TradeOutcome) -> LossAnalysis:
        """TradeOutcome → LossAnalysis. 손실이 아니면 빈 분석."""
        if outcome.pnl_pct >= 0:
            return LossAnalysis(
                pnl_pct=outcome.pnl_pct, is_loss=False,
                primary_tag=None, contributing_tags=(),
            )

        all_tags: list[LossTag] = []

        # 1. exit_reason 키워드 — 가장 신뢰도 높음 (primary 후보)
        reason_lower = (outcome.exit_reason or "").lower()
        if "손절" in (outcome.exit_reason or "") or "stop_loss" in reason_lower or "stop loss" in reason_lower:
            all_tags.append(LossTag("STOP_LOSS", "primary",
                                     f"손절 트리거: {outcome.exit_reason}"))
        elif "시간 청산" in (outcome.exit_reason or "") or "time_stop" in reason_lower or "time stop" in reason_lower:
            all_tags.append(LossTag("TIME_STOP", "primary",
                                     f"시간 청산: {outcome.exit_reason}"))

        # 2. 슬리피지 — contributing 후보
        if outcome.slippage_pct > self.SLIPPAGE_THRESHOLD_PCT:
            all_tags.append(LossTag(
                "SLIPPAGE", "contributing",
                f"슬리피지 {outcome.slippage_pct:.2f}% > 한도 {self.SLIPPAGE_THRESHOLD_PCT}%",
            ))

        # 3. 스프레드 — contributing 후보
        if outcome.spread_pct > self.SPREAD_THRESHOLD_PCT:
            all_tags.append(LossTag(
                "SPREAD", "contributing",
                f"진입 스프레드 {outcome.spread_pct:.2f}% > 한도 {self.SPREAD_THRESHOLD_PCT}%",
            ))

        # 4. Regime change — contributing
        if outcome.entry_regime and outcome.exit_regime \
                and outcome.entry_regime != outcome.exit_regime:
            all_tags.append(LossTag(
                "REGIME_CHANGE", "contributing",
                f"regime 전환: {outcome.entry_regime} → {outcome.exit_regime}",
            ))

        # 5. Kimp divergence — primary 후보 (kimp 전략 한정)
        if outcome.entry_kimp_pct is not None and outcome.exit_kimp_pct is not None:
            divergence = outcome.entry_kimp_pct - outcome.exit_kimp_pct
            # 역김프 진입(entry는 음수): exit 가 더 음수 → divergence > 0 = 확대
            if divergence > self.KIMP_DIVERGENCE_THRESHOLD_PCT:
                all_tags.append(LossTag(
                    "KIMP_DIVERGENCE", "primary",
                    f"김프 확대: {outcome.entry_kimp_pct:+.2f}% → "
                    f"{outcome.exit_kimp_pct:+.2f}% (Δ {divergence:.2f}%)",
                ))

        # 6. News shock — primary 후보
        if outcome.news_severity_during_hold == "block":
            all_tags.append(LossTag(
                "NEWS_SHOCK", "primary",
                "보유 기간 block 등급 뉴스 발생",
            ))

        # 7. Funding burn — contributing
        pnl_abs = abs(outcome.pnl_pct) or 1e-9
        if outcome.funding_cost_pct > 0 \
                and outcome.funding_cost_pct / pnl_abs > self.FUNDING_BURN_RATIO:
            all_tags.append(LossTag(
                "FUNDING_BURN", "contributing",
                f"펀딩 비용 {outcome.funding_cost_pct:.3f}% (손실의 "
                f"{outcome.funding_cost_pct / pnl_abs * 100:.0f}%)",
            ))

        # 8. Fee heavy — contributing
        notional = max(outcome.notional_usdt, 1e-9)
        fee_pct = outcome.fee_usdt / notional * 100.0
        if fee_pct > 0 and fee_pct / pnl_abs > self.FEE_HEAVY_RATIO:
            all_tags.append(LossTag(
                "FEE_HEAVY", "contributing",
                f"수수료 {fee_pct:.3f}% (손실의 {fee_pct / pnl_abs * 100:.0f}%)",
            ))

        # primary 하나 선택. 없으면 UNKNOWN.
        primary = next((t for t in all_tags if t.severity == "primary"), None)
        if primary is None:
            primary = LossTag(
                "UNKNOWN", "primary",
                f"분류 불가 — pnl={outcome.pnl_pct:.2f}%, exit_reason='{outcome.exit_reason}'",
            )

        contributing = tuple(t for t in all_tags if t is not primary)

        return LossAnalysis(
            pnl_pct=outcome.pnl_pct,
            is_loss=True,
            primary_tag=primary,
            contributing_tags=contributing,
        )

    # ── 렌더링 ────────────────────────────────────────────────────

    def render_text(self, analysis: LossAnalysis, *, format: str = "markdown") -> str:
        if not analysis.is_loss:
            return f"수익 거래 (pnl={analysis.pnl_pct:+.2f}%)"

        if format == "markdown":
            lines = [
                "## 손실 분석",
                f"- **손실**: {analysis.pnl_pct:.2f}%",
            ]
            if analysis.primary_tag:
                pt = analysis.primary_tag
                lines.append(f"- **주 원인** (`{pt.category}`): {pt.reason}")
            if analysis.contributing_tags:
                lines.append("- **기여 요인**:")
                for ct in analysis.contributing_tags:
                    lines.append(f"  - `{ct.category}`: {ct.reason}")
            return "\n".join(lines)

        # plain
        lines = [
            f"=== 손실 분석 (pnl={analysis.pnl_pct:.2f}%) ===",
        ]
        if analysis.primary_tag:
            pt = analysis.primary_tag
            lines.append(f"  [{pt.category}] {pt.reason}")
        for ct in analysis.contributing_tags:
            lines.append(f"  + ({ct.category}) {ct.reason}")
        return "\n".join(lines)

    # ── AgentBase contract ────────────────────────────────────────

    def decide(self, input_signal: dict, context: dict | None = None) -> Any:
        from .orchestrator import AgentDecision
        ctx = context or {}
        outcome = ctx.get("outcome")
        if outcome is None or not isinstance(outcome, TradeOutcome):
            return AgentDecision(
                "HOLD", 0.0,
                "LossTaggingAgent: outcome 미제공",
                explain_text="ctx['outcome'] 에 TradeOutcome 인스턴스 필요",
            )
        analysis = self.analyze(outcome)
        return AgentDecision(
            "HOLD", 0.0,
            f"LossTaggingAgent: {analysis.category}",
            explain_text=self.render_text(analysis, format="markdown"),
        )
