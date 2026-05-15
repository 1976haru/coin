"""SignalQualityAgent — 체크리스트 #37 + #39 (Signal Quality Agent boosted).

신호 품질 0~100 점수를 다중 신호로 산출. 결정론적 — LLM 사용 안 함.

체크리스트 #39 보강 항목 (#17 quality / #18 notices / #19 themes / #16 freshness 통합):
  - confidence × 30 + 기본 50 + 보너스/페널티
  - QualityReport (liquidity_ok / fx_anomaly_ok) 가산/감점
  - news_severity 감점 (block=-20, warn=-5)
  - freshness_stale 감점 (-10)
  - kimp_anomaly_hint 감점 (-10)
  - regime / vol_band 보너스
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from .base import AgentCapability


@dataclass(frozen=True)
class QualityBreakdown:
    """품질 점수 산정의 구성요소 (감사·디버그 가시성)."""

    base: float
    confidence_pts: float
    valid_action_pts: float
    volume_surge_pts: float
    regime_pts: float
    quality_report_pts: float
    news_penalty: float
    freshness_penalty: float
    kimp_anomaly_penalty: float
    total: float
    components: tuple[tuple[str, float], ...] = field(default_factory=tuple)


class SignalQualityAgent:
    """신호 품질 점수 산출 + MIN_QUALITY_SCORE(70) 미만은 HOLD.

    backward-compat:
      - ``calc_quality(signal, ctx)`` 는 boosted 점수 반환. 기존 호출자(#37 Orchestrator)
        가 이 메서드를 사용하므로 시그니처 유지.
      - ``MIN_QUALITY_SCORE`` 임계값 동일 (70).
    """

    MIN_QUALITY_SCORE = 70.0

    capability = AgentCapability(
        name="signal_quality",
        role="signal_quality",
        description=(
            "신호 품질 0~100 + 임계값 미만 HOLD. "
            "QualityReport / news_severity / freshness / regime 통합 (#39)."
        ),
        has_veto_power=False,
        is_deterministic=True,
        requires_llm=False,
        inputs=(
            "confidence", "action",
            "volume_surge", "regime", "vol_band",
            "quality_report", "news_severity",
            "freshness_stale", "kimp_anomaly_hint",
        ),
    )

    # ── 계산 ──────────────────────────────────────────────────────

    def breakdown(self, signal: dict, ctx: dict | None = None) -> QualityBreakdown:
        """점수 산정의 분해. ``calc_quality`` 가 내부적으로 호출."""
        ctx = ctx or {}

        base = 50.0

        # confidence (0~1) × 30
        conf = float(signal.get("confidence", 0))
        confidence_pts = max(0.0, min(1.0, conf)) * 30.0

        # 유효 액션
        valid_action_pts = (
            10.0 if signal.get("action") not in {"BLOCKED", "HOLD"} else 0.0
        )

        # 거래량 surge
        volume_surge_pts = 5.0 if float(ctx.get("volume_surge", 1.0)) >= 1.2 else 0.0

        # regime/vol_band — TREND 좋은 vol_band 가산
        regime = ctx.get("regime")
        vol_band = ctx.get("vol_band")
        regime_pts = 0.0
        if regime in {"TREND_UP", "TREND_DOWN"}:
            regime_pts += 5.0
        if vol_band == "NORMAL":
            regime_pts += 2.0
        elif vol_band == "HIGH":
            regime_pts -= 2.0  # 고변동은 감점

        # QualityReport (#17) 통합 — liquidity_ok / fx_anomaly_ok
        quality_report = ctx.get("quality_report")
        quality_report_pts = 0.0
        if quality_report is not None:
            # dict 또는 QualityReport 객체 모두 지원
            liq = self._read_attr(quality_report, "liquidity_ok", default=True)
            fx  = self._read_attr(quality_report, "fx_anomaly_ok", default=True)
            if liq:
                quality_report_pts += 5.0
            if fx:
                quality_report_pts += 5.0

        # 뉴스 severity 감점 (#19 themes)
        # block 은 강한 신호(confidence 1.0 + 모든 보너스 ~95)도 임계값(70) 미만으로 만들 수 있게.
        news_severity = ctx.get("news_severity", "info")
        if news_severity == "block":
            news_penalty = -30.0
        elif news_severity == "warn":
            news_penalty = -10.0
        else:
            news_penalty = 0.0

        # Freshness stale 감점 (#16)
        freshness_penalty = -10.0 if ctx.get("freshness_stale") else 0.0

        # 김프 이상치 hint 감점 (#34/#35 결과를 ctx 로 전달 가능)
        kimp_anomaly_penalty = -10.0 if ctx.get("kimp_anomaly_hint") else 0.0

        total = (
            base
            + confidence_pts
            + valid_action_pts
            + volume_surge_pts
            + regime_pts
            + quality_report_pts
            + news_penalty
            + freshness_penalty
            + kimp_anomaly_penalty
        )
        total = max(0.0, min(100.0, total))

        components: list[tuple[str, float]] = [
            ("base", base),
            ("confidence", confidence_pts),
            ("valid_action", valid_action_pts),
            ("volume_surge", volume_surge_pts),
            ("regime+vol_band", regime_pts),
            ("quality_report", quality_report_pts),
            ("news", news_penalty),
            ("freshness", freshness_penalty),
            ("kimp_anomaly", kimp_anomaly_penalty),
        ]

        return QualityBreakdown(
            base=base,
            confidence_pts=confidence_pts,
            valid_action_pts=valid_action_pts,
            volume_surge_pts=volume_surge_pts,
            regime_pts=regime_pts,
            quality_report_pts=quality_report_pts,
            news_penalty=news_penalty,
            freshness_penalty=freshness_penalty,
            kimp_anomaly_penalty=kimp_anomaly_penalty,
            total=total,
            components=tuple(components),
        )

    def calc_quality(self, signal: dict, ctx: dict | None = None) -> float:
        """0~100 품질 점수. backward compat (Orchestrator/RiskOfficer 가 사용)."""
        return self.breakdown(signal, ctx).total

    # ── decide ────────────────────────────────────────────────────

    def decide(self, input_signal: dict, context: dict | None = None) -> Any:
        from .orchestrator import AgentDecision
        ctx = context or {}
        bd = self.breakdown(input_signal, ctx)
        action = input_signal.get("action", "HOLD")
        confidence = float(input_signal.get("confidence", 0.0))

        if bd.total < self.MIN_QUALITY_SCORE:
            return AgentDecision(
                "HOLD", confidence,
                f"SignalQualityAgent: 품질 부족 ({bd.total:.1f} < {self.MIN_QUALITY_SCORE})",
                quality_score=bd.total,
                explain_text=self._explain_breakdown(bd),
            )

        return AgentDecision(
            action, confidence,
            f"SignalQualityAgent: 품질 통과 ({bd.total:.1f})",
            quality_score=bd.total,
            explain_text=self._explain_breakdown(bd),
        )

    # ── 헬퍼 ──────────────────────────────────────────────────────

    @staticmethod
    def _read_attr(obj: Any, name: str, *, default=None) -> Any:
        """dict 또는 객체 모두에서 속성 읽기."""
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    @staticmethod
    def _explain_breakdown(bd: QualityBreakdown) -> str:
        nonzero = [f"{name}{val:+.1f}"
                   for name, val in bd.components if val != 0.0]
        return f"신호 품질 {bd.total:.1f}/100 (" + ", ".join(nonzero) + ")"
