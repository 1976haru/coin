"""
AgentOrchestrator — 결정론적 4단계 파이프라인 + 선택적 LLM 강화 — 체크리스트 #37.

파이프라인 (각 단계는 별도 Agent 클래스, capability 보유):
  1. AnomalyAgent       — 이상 데이터 차단
  2. (BLOCKED/HOLD 단축)
  3. SignalQualityAgent — 품질 점수 + 임계값 미달 HOLD
  4. RiskOfficerAgent   — 최종 거부권 (kill_switch / 연속손실 / 일손실 / 저신뢰)
  5. (옵션) LLM 강화    — ENABLE_AI_AGENTS=true 시

CLAUDE.md §2.3:
  - Agent 는 분석/추천/설명만. 직접 주문 금지.
  - AgentDecision.is_order_intent 기본 False (영구).
"""
from __future__ import annotations
import os
import json
import logging
from dataclasses import dataclass

from .base import AgentCapability

logger = logging.getLogger("agent_trader")


@dataclass(frozen=True)
class AgentDecision:
    action: str          # HOLD | BUY | SELL | OPEN_REVERSE_KIMP | CLOSE | WATCH_ONLY
    confidence: float
    reason: str
    quality_score: float = 0.0     # 0~100 (70점 미만 → HOLD)
    risk_veto: bool = False
    explain_text: str = ""
    is_order_intent: bool = False  # CLAUDE.md §2.3: 기본 false. AI는 직접 주문하지 않는다.


class AgentOrchestrator:
    """결정론적 4단계 파이프라인 + 선택적 LLM 강화.

    ``decide()`` 는 backward compat 한 단일 결정. 단계별 보고는
    ``decide_with_pipeline()``.
    """

    capability = AgentCapability(
        name="orchestrator",
        role="orchestrator",
        description="Anomaly → SignalQuality → RiskOfficer 파이프라인 + LLM 강화 옵션.",
        has_veto_power=False,
        is_deterministic=True,
        requires_llm=False,
        inputs=("anomaly", "volume_surge", "regime",
                "kill_switch", "consecutive_losses", "daily_loss_pct"),
    )

    MIN_QUALITY_SCORE = 70.0   # backward compat — 외부 테스트가 직접 참조

    def __init__(
        self,
        anomaly_agent=None,
        signal_quality_agent=None,
        risk_officer_agent=None,
    ):
        # Lazy import 로 sub-agent 모듈이 본 모듈을 import 하는 순환 방지
        from .anomaly import AnomalyAgent
        from .signal_quality import SignalQualityAgent
        from .risk_officer import RiskOfficerAgent
        self.anomaly_agent        = anomaly_agent        or AnomalyAgent()
        self.signal_quality_agent = signal_quality_agent or SignalQualityAgent()
        self.risk_officer_agent   = risk_officer_agent   or RiskOfficerAgent()

    # ── public: backward compat 단일 결정 ────────────────────────

    def decide(self, strategy_signal: dict, context: dict | None = None) -> AgentDecision:
        ctx = context or {}

        # 1. Anomaly Veto
        anomaly_decision = self.anomaly_agent.decide(strategy_signal, ctx)
        if anomaly_decision.risk_veto:
            return anomaly_decision

        action     = strategy_signal.get("action", "HOLD")
        confidence = float(strategy_signal.get("confidence", 0.0))
        reason     = strategy_signal.get("reason", "")

        # 2. BLOCKED / HOLD 즉시 반환 (전략이 이미 차단)
        if action in {"BLOCKED", "HOLD"}:
            return AgentDecision(
                "HOLD", confidence, reason,
                risk_veto=(action == "BLOCKED"),
                explain_text=reason,
            )

        # 3. SignalQuality
        sq_decision = self.signal_quality_agent.decide(strategy_signal, ctx)
        if sq_decision.action == "HOLD":
            return sq_decision
        quality = sq_decision.quality_score

        # 4. RiskOfficer (최종 거부권)
        ro_decision = self.risk_officer_agent.decide(
            {**strategy_signal, "quality_score": quality}, ctx,
        )
        if ro_decision.risk_veto or ro_decision.action in {"HOLD", "WATCH_ONLY"}:
            return ro_decision

        # 5. LLM 강화 (옵션)
        if os.getenv("ENABLE_AI_AGENTS", "false").lower() in ("1", "true", "yes"):
            try:
                return self._llm_enhance(action, confidence, quality, strategy_signal, ctx)
            except Exception as e:
                logger.warning(f"LLM fallback: {e}")

        return AgentDecision(
            action, confidence,
            f"결정론적 Agent 승인: {reason}",
            quality_score=quality,
            explain_text=self._explain(action, strategy_signal),
        )

    # ── 단계별 보고 (감사/디버그) ────────────────────────────────

    def decide_with_pipeline(
        self, strategy_signal: dict, context: dict | None = None,
    ) -> dict:
        """각 단계별 AgentDecision 을 모두 반환 (감사 가시성)."""
        ctx = context or {}
        stages = []
        for agent in (self.anomaly_agent,
                       self.signal_quality_agent,
                       self.risk_officer_agent):
            d = agent.decide(strategy_signal, ctx)
            stages.append({
                "agent": agent.capability.name,
                "decision": {
                    "action": d.action,
                    "confidence": d.confidence,
                    "reason": d.reason,
                    "quality_score": d.quality_score,
                    "risk_veto": d.risk_veto,
                    "is_order_intent": d.is_order_intent,
                },
            })
        final = self.decide(strategy_signal, ctx)
        return {
            "final": {
                "action": final.action,
                "confidence": final.confidence,
                "reason": final.reason,
                "quality_score": final.quality_score,
                "risk_veto": final.risk_veto,
                "is_order_intent": final.is_order_intent,
                "explain_text": final.explain_text,
            },
            "stages": stages,
        }

    # ── 내부 헬퍼 ─────────────────────────────────────────────────

    def _calc_quality(self, signal: dict, ctx: dict) -> float:
        """backward compat — SignalQualityAgent 위임."""
        return self.signal_quality_agent.calc_quality(signal, ctx)

    def _explain(self, action: str, signal: dict) -> str:
        templates = {
            "BUY":              "추세·돌파 조건 충족. 진입 후보.",
            "SELL":             "하락 추세 조건 충족. 숏/매도 후보.",
            "OPEN_REVERSE_KIMP": "역김프 수렴 기대. 소액 테스트 진입.",
            "CLOSE":            "목표 달성 또는 손절 조건. 청산.",
        }
        base = templates.get(action, action)
        reason = signal.get("reason", "")
        return f"{base} ({reason})"

    def _llm_enhance(self, action, confidence, quality, signal, ctx) -> AgentDecision:
        """Claude API 호출 — ENABLE_AI_AGENTS=true 시에만."""
        import anthropic  # type: ignore[import-not-found]
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
        prompt = f"""신호 분석:
action={action}, confidence={confidence:.2f}, quality={quality:.1f}
context={json.dumps(ctx, ensure_ascii=False)}
reason={signal.get('reason','')}

위 신호가 진입 적합한지 판단하고 JSON으로 응답:
{{"action":"HOLD 또는 원래 action","confidence":float,"reason":"string"}}"""
        resp = client.messages.create(
            model=os.getenv("AI_MODEL", "claude-sonnet-4-5"),
            max_tokens=300,
            temperature=0.0,
            messages=[{"role":"user","content":prompt}],
        )
        data = json.loads(resp.content[0].text)
        return AgentDecision(
            data.get("action", action),
            float(data.get("confidence", confidence)),
            data.get("reason", "AI 강화 판단"),
            quality,
            explain_text=data.get("reason", ""),
        )
