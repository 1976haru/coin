"""ExplainAgent — 체크리스트 #41 Explain Agent.

Agent 결정/파이프라인 결과를 인간 가독 설명 문자열로 변환. 결정론 — LLM 사용 안 함.
ENABLE_AI_AGENTS=true 시 LLM 강화 옵션 (현재 결정론 유지, LLM 통합은 #46 후속).

설계 원칙 (CLAUDE.md §2.3):
  - 분석/설명만. 직접 주문 금지.
  - is_order_intent 기본 False.
  - 외부 시스템 (UI / Telegram / Audit log) 이 본 설명을 그대로 표시 가능.
"""
from __future__ import annotations
from typing import Any, Literal

from .base import AgentCapability


ExplainFormat = Literal["short", "full", "markdown"]


_ACTION_TEMPLATES = {
    "BUY":              "매수 신호",
    "SELL":             "매도/숏 신호",
    "HOLD":             "관망",
    "BLOCKED":          "차단",
    "CLOSE":            "청산",
    "OPEN_REVERSE_KIMP": "역김프 진입 후보",
    "OPEN_LONG_A_SHORT_B": "페어 Long A / Short B",
    "OPEN_SHORT_A_LONG_B": "페어 Short A / Long B",
    "WATCH_ONLY":       "관찰 모드 (저신뢰도)",
}


class ExplainAgent:
    """Agent 결정 설명 생성기.

    공개 메서드:
      - ``explain_signal(signal, ctx, format)``     — 전략 신호 설명
      - ``explain_decision(decision, format)``      — 단일 AgentDecision 설명
      - ``explain_pipeline(pipeline_result, format)`` — Orchestrator.decide_with_pipeline
      - ``decide(input, ctx)``                       — AgentBase contract (단일 결정 설명을 explain_text 로 반환)
    """

    capability = AgentCapability(
        name="explain",
        role="explain",
        description="Agent 결정/파이프라인의 자연어 설명 생성 (결정론 + 템플릿).",
        has_veto_power=False,
        is_deterministic=True,
        requires_llm=False,
        inputs=("action", "confidence", "reason", "quality_score",
                "regime", "vol_band"),
    )

    # ── AgentBase contract ────────────────────────────────────────

    def decide(self, input_signal: dict, context: dict | None = None) -> Any:
        from .orchestrator import AgentDecision
        ctx = context or {}
        narrative = self.explain_signal(input_signal, ctx, format="full")
        return AgentDecision(
            input_signal.get("action", "HOLD"),
            float(input_signal.get("confidence", 0.0)),
            "ExplainAgent: 설명 생성 완료",
            quality_score=float(input_signal.get("quality_score", 0.0)),
            explain_text=narrative,
        )

    # ── public 설명 API ───────────────────────────────────────────

    def explain_signal(
        self,
        signal: dict,
        context: dict | None = None,
        *,
        format: ExplainFormat = "short",
    ) -> str:
        """전략 신호 + ctx → 설명 문자열.

        format:
          - "short"    : 한 줄 — "[BUY] 추세 정상 (품질 85.0)"
          - "full"     : 여러 줄 plain text
          - "markdown" : 마크다운 글머리 기호
        """
        ctx = context or {}
        action = signal.get("action", "HOLD")
        confidence = float(signal.get("confidence", 0.0))
        reason = signal.get("reason", "")
        quality = float(signal.get("quality_score", 0.0))

        label = _ACTION_TEMPLATES.get(action, action)

        if format == "short":
            parts = [f"[{action}] {label}"]
            if reason:
                parts.append(f"({reason})")
            if quality > 0:
                parts.append(f"품질 {quality:.1f}")
            return " ".join(parts)

        # full / markdown 공통: 컨텍스트 보조 정보 수집
        ctx_lines = self._context_lines(ctx)
        if format == "markdown":
            lines = [f"### {action} — {label}"]
            if reason:
                lines.append(f"- **사유**: {reason}")
            lines.append(f"- **신뢰도**: {confidence:.2f}")
            if quality > 0:
                lines.append(f"- **품질 점수**: {quality:.1f}/100")
            if ctx_lines:
                lines.append("- **컨텍스트**:")
                lines.extend(f"  - {line}" for line in ctx_lines)
            return "\n".join(lines)

        # plain "full"
        lines = [f"{action} — {label}"]
        if reason:
            lines.append(f"  사유: {reason}")
        lines.append(f"  신뢰도: {confidence:.2f}")
        if quality > 0:
            lines.append(f"  품질: {quality:.1f}/100")
        if ctx_lines:
            lines.append("  컨텍스트:")
            lines.extend(f"    - {line}" for line in ctx_lines)
        return "\n".join(lines)

    def explain_decision(
        self,
        decision: Any,
        *,
        format: ExplainFormat = "short",
    ) -> str:
        """AgentDecision (객체 또는 dict) 설명."""
        if isinstance(decision, dict):
            d = decision
            getter = d.get
        else:
            getter = lambda k, default=None: getattr(decision, k, default)

        action = getter("action", "HOLD")
        confidence = float(getter("confidence", 0.0) or 0.0)
        reason = getter("reason", "") or ""
        quality = float(getter("quality_score", 0.0) or 0.0)
        veto = bool(getter("risk_veto", False))
        explain = getter("explain_text", "") or ""

        label = _ACTION_TEMPLATES.get(action, action)
        veto_tag = " ⛔" if veto else ""

        if format == "short":
            parts = [f"[{action}{veto_tag}] {label}"]
            if reason:
                parts.append(f"— {reason}")
            return " ".join(parts)

        if format == "markdown":
            lines = [f"### {action}{veto_tag} — {label}"]
            if reason:
                lines.append(f"- **사유**: {reason}")
            lines.append(f"- **신뢰도**: {confidence:.2f}")
            if quality > 0:
                lines.append(f"- **품질**: {quality:.1f}/100")
            if veto:
                lines.append("- **거부권 행사**: ✅")
            if explain:
                lines.append(f"- **추가 설명**: {explain}")
            return "\n".join(lines)

        # plain full
        lines = [f"{action}{veto_tag} — {label}"]
        if reason:
            lines.append(f"  사유: {reason}")
        lines.append(f"  신뢰도: {confidence:.2f}")
        if quality > 0:
            lines.append(f"  품질: {quality:.1f}/100")
        if veto:
            lines.append("  거부권 행사: 예")
        if explain:
            lines.append(f"  설명: {explain}")
        return "\n".join(lines)

    def explain_pipeline(
        self,
        pipeline_result: dict,
        *,
        format: ExplainFormat = "full",
    ) -> str:
        """``Orchestrator.decide_with_pipeline()`` 결과 → 단계별 설명.

        pipeline_result 형식:
          {"final": {...}, "stages": [{"agent": str, "decision": {...}}, ...]}
        """
        stages = pipeline_result.get("stages", [])
        final = pipeline_result.get("final", {})

        if format == "short":
            final_str = self.explain_decision(final, format="short")
            return f"파이프라인 결과 → {final_str}"

        if format == "markdown":
            lines = ["## 의사결정 파이프라인"]
            for st in stages:
                lines.append(f"\n#### {st['agent']}")
                lines.append(self.explain_decision(st["decision"], format="markdown"))
            lines.append("\n## 최종 결정")
            lines.append(self.explain_decision(final, format="markdown"))
            return "\n".join(lines)

        # plain full
        lines = ["── 파이프라인 단계 ─────────────"]
        for st in stages:
            lines.append(f"\n[{st['agent']}]")
            lines.append(self.explain_decision(st["decision"], format="full"))
        lines.append("\n── 최종 ─────────────────────")
        lines.append(self.explain_decision(final, format="full"))
        return "\n".join(lines)

    # ── 내부 ──────────────────────────────────────────────────────

    @staticmethod
    def _context_lines(ctx: dict) -> list[str]:
        out: list[str] = []
        if "regime" in ctx:
            out.append(f"regime={ctx['regime']}")
        if "vol_band" in ctx:
            out.append(f"vol_band={ctx['vol_band']}")
        if "volume_surge" in ctx:
            out.append(f"volume_surge×{float(ctx['volume_surge']):.2f}")
        if "news_severity" in ctx and ctx["news_severity"] != "info":
            out.append(f"news={ctx['news_severity']}")
        themes = ctx.get("themes") or []
        if themes:
            out.append(f"themes={','.join(themes)}")
        if ctx.get("freshness_stale"):
            out.append("⚠ freshness stale")
        if ctx.get("kimp_anomaly_hint"):
            out.append("⚠ kimp 이상치")
        return out
