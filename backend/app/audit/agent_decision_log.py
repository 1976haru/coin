"""AgentDecisionLog — Agent 판단 전용 감사 facade — 체크리스트 #11.

AgentOrchestrator.decide() 결과를 표준화된 dict로 AuditLog에 기록한다.
CLAUDE.md §2.3: is_order_intent 필드를 항상 함께 기록해 사후 검증을 가능케 한다.

이벤트 타입: ``AGENT_DECISION``
"""
from __future__ import annotations
from typing import Any

from .audit_log import AuditLog


class AgentDecisionLog:
    """AgentDecision을 정형화해 기록하는 facade.

    AgentDecision dataclass(또는 동일 필드를 가진 객체)를 받아 dict로 변환 후
    AuditLog에 위임한다. dict 입력도 수용해 외부 통합 지점에서 유연하다.
    """

    EVENT_TYPE = "AGENT_DECISION"

    def __init__(self, audit: AuditLog | None = None):
        self.audit = audit or AuditLog()

    def record(self, decision: Any, context: dict | None = None,
               agent_role: str = "orchestrator") -> dict:
        """AgentDecision(또는 동일 필드 dict)을 표준 페이로드로 기록."""
        return self.audit.record(self.EVENT_TYPE, {
            "agent_role": agent_role,
            "decision": self._normalize(decision),
            "context": context or {},
        })

    def tail(self, limit: int = 100) -> list[dict]:
        return [e for e in self.audit.events
                if e["event_type"] == self.EVENT_TYPE][-limit:]

    def count(self) -> int:
        return sum(1 for e in self.audit.events
                   if e["event_type"] == self.EVENT_TYPE)

    def filter_by_role(self, agent_role: str) -> list[dict]:
        return [e for e in self.audit.events
                if e["event_type"] == self.EVENT_TYPE
                and (e.get("payload") or {}).get("agent_role") == agent_role]

    @staticmethod
    def _normalize(decision: Any) -> dict:
        """AgentDecision dataclass / dict / 임의 객체 → 표준 dict.

        is_order_intent는 명시적으로 채워 누락 시에도 False가 보장되도록 한다
        (CLAUDE.md §2.3).
        """
        if isinstance(decision, dict):
            d = decision
            getter = d.get
        else:
            getter = lambda k, default=None: getattr(decision, k, default)

        return {
            "action":          getter("action", ""),
            "confidence":      float(getter("confidence", 0.0) or 0.0),
            "reason":          getter("reason", ""),
            "quality_score":   float(getter("quality_score", 0.0) or 0.0),
            "risk_veto":       bool(getter("risk_veto", False)),
            "is_order_intent": bool(getter("is_order_intent", False)),
            "explain_text":    getter("explain_text", ""),
        }
