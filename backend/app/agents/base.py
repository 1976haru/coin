"""Agent 공통 인터페이스 — 체크리스트 #37 Agent Architecture.

각 Agent (signal_quality / anomaly / risk_officer / explain / daily_report 등)
가 따라야 할 메타데이터·contract 를 정의한다. Strategy(#29) 와 동일 패턴.

설계 원칙 (CLAUDE.md §2.3):
  - Agent 는 분석·추천·설명만 한다. 직접 주문 금지.
  - AgentDecision.is_order_intent 기본 False (영구).
  - Agent / Strategy / Frontend 는 BrokerAdapter / OrderExecutor 직접 import 금지.
  - RiskOfficerAgent 가 최종 거부권 (REJECT 시 어떤 주문 후보도 생성 안 함).
  - 낮은 confidence 는 WATCH_ONLY 처리 (별도 액션).

`AgentBase` 는 Protocol 로 정의되어 기존 클래스가 inheritance 변경 없이 duck typing
으로 만족할 수 있다. AgentDecision 자체는 ``app.agents.orchestrator`` 의 정규
정의를 그대로 사용.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Literal, Protocol, runtime_checkable


AgentRole = Literal[
    "orchestrator",
    "signal_quality",
    "risk_officer",
    "anomaly",
    "explain",
    "daily_report",
]


@dataclass(frozen=True)
class AgentCapability:
    """Agent 의 역할·동작 카탈로그.

    Attributes
    ----------
    name: snake_case 식별자 (예: "signal_quality", "risk_officer")
    role: AgentRole — UI/감사 로그 그룹핑용
    description: 한 줄 설명
    has_veto_power: 단독 거부권 보유 여부 (RiskOfficer 전용)
    is_deterministic: rule-based 결정론. False 면 LLM 호출 가능.
    requires_llm: ENABLE_AI_AGENTS=true 가 필요한 Agent
    inputs: ``decide`` 호출 시 필요한 컨텍스트 키 (hint)
    """

    name: str
    role: AgentRole
    description: str
    has_veto_power: bool = False
    is_deterministic: bool = True
    requires_llm: bool = False
    inputs: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@runtime_checkable
class AgentBase(Protocol):
    """Agent 공통 contract.

    ``capability`` 속성과 ``decide`` 메서드를 가진다. ``decide`` 시그니처는
    ``(input_signal: dict, context: dict) -> AgentDecision``.
    """

    capability: AgentCapability

    def decide(self, input_signal: dict, context: dict | None = None) -> Any: ...


# ── Registry ─────────────────────────────────────────────────────

class AgentRegistry:
    """Agent 인스턴스 등록소. Strategy registry 와 동일 패턴 (#29)."""

    def __init__(self):
        self._items: dict[str, Any] = {}

    def register(self, agent: Any, name: str | None = None) -> None:
        cap = self._extract_capability(agent)
        key = name or cap.name
        if not key:
            raise ValueError("AgentRegistry: name 또는 capability.name 필요")
        self._items[key] = agent

    def get(self, name: str) -> Any | None:
        return self._items.get(name)

    def all(self) -> list[Any]:
        return list(self._items.values())

    def names(self) -> list[str]:
        return sorted(self._items.keys())

    def by_role(self, role: AgentRole) -> list[Any]:
        return [a for a in self._items.values()
                if self._extract_capability(a).role == role]

    def capabilities(self) -> list[AgentCapability]:
        return [self._extract_capability(a) for a in self._items.values()]

    def catalog(self) -> list[dict]:
        return [c.to_dict() for c in self.capabilities()]

    def remove(self, name: str) -> bool:
        return self._items.pop(name, None) is not None

    def clear(self) -> None:
        self._items.clear()

    @staticmethod
    def _extract_capability(agent: Any) -> AgentCapability:
        cap = getattr(agent, "capability", None)
        if cap is None:
            raise TypeError(
                f"{agent!r} 에 'capability' 속성이 없음 — AgentBase Protocol 미준수"
            )
        if not isinstance(cap, AgentCapability):
            raise TypeError(
                f"{agent!r}.capability 가 AgentCapability 가 아님 (got {type(cap).__name__})"
            )
        return cap


def collect_default_agents() -> AgentRegistry:
    """기본 Agent 셋(Anomaly / SignalQuality / RiskOfficer / Orchestrator)을 등록."""
    from app.agents.anomaly import AnomalyAgent
    from app.agents.signal_quality import SignalQualityAgent
    from app.agents.risk_officer import RiskOfficerAgent
    from app.agents.orchestrator import AgentOrchestrator

    r = AgentRegistry()
    r.register(AnomalyAgent())
    r.register(SignalQualityAgent())
    r.register(RiskOfficerAgent())
    r.register(AgentOrchestrator())
    return r
