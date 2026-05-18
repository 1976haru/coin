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


# ─────────────────────────────────────────────────────────────────
# 체크리스트 #37 — Agent Architecture (Observer / Analyst /
# Risk Auditor / Strategy Researcher / Report Writer /
# Execution Recommender) 구조적 layer
# ─────────────────────────────────────────────────────────────────

import json
from abc import ABC, abstractmethod
from dataclasses import field
from datetime import datetime, timezone
from enum import Enum
from typing import ClassVar, Mapping


class AgentArchitectureRole(str, Enum):
    """Agent 역할 분리 (6개).

    각 Agent 는 자신의 ``role_scope`` 안에서만 output 을 생성한다. 한 번에
    관찰→분석→리스크→주문까지 수행하는 구조는 금지된다 (CLAUDE.md §2.3).
    """

    OBSERVER = "OBSERVER"
    ANALYST = "ANALYST"
    RISK_AUDITOR = "RISK_AUDITOR"
    STRATEGY_RESEARCHER = "STRATEGY_RESEARCHER"
    REPORT_WRITER = "REPORT_WRITER"
    EXECUTION_RECOMMENDER = "EXECUTION_RECOMMENDER"


class AgentPermission(str, Enum):
    """Agent 가 요청할 수 있는 권한 카탈로그.

    FORBIDDEN 카탈로그는 *어떤 Agent 에도 부여되지 않는다*.
    ``StructuredAgentBase.validate_safety()`` 가 매 등록마다 검사한다.
    """

    # Allowed (역할별로 일부만 부여)
    READ_MARKET_DATA = "read_market_data"
    READ_FRESHNESS = "read_freshness"
    READ_DATA_QUALITY = "read_data_quality"
    READ_NOTICES = "read_notices"
    READ_THEMES = "read_themes"
    READ_KIMP = "read_kimp"
    READ_FUNDING = "read_funding"
    READ_RISK_STATE = "read_risk_state"
    READ_STRATEGY_CATALOG = "read_strategy_catalog"
    READ_PERFORMANCE_HISTORY = "read_performance_history"
    WRITE_FINDING = "write_finding"
    WRITE_RECOMMENDATION = "write_recommendation"
    WRITE_REPORT = "write_report"
    # FORBIDDEN — 정책상 어떤 Agent 도 받지 않음
    EXECUTE_ORDER = "execute_order"
    INVOKE_BROKER = "invoke_broker"
    INVOKE_ORDER_GATEWAY = "invoke_order_gateway"
    READ_SECRETS = "read_secrets"
    WRITE_ORDER_REQUEST = "write_order_request"
    PLACE_ORDER_PERMISSION = "place_order_permission"
    CANCEL_ORDER_PERMISSION = "cancel_order_permission"
    GET_BALANCE_PERMISSION = "get_balance_permission"


FORBIDDEN_AGENT_PERMISSIONS: frozenset[AgentPermission] = frozenset((
    AgentPermission.EXECUTE_ORDER,
    AgentPermission.INVOKE_BROKER,
    AgentPermission.INVOKE_ORDER_GATEWAY,
    AgentPermission.READ_SECRETS,
    AgentPermission.WRITE_ORDER_REQUEST,
    AgentPermission.PLACE_ORDER_PERMISSION,
    AgentPermission.CANCEL_ORDER_PERMISSION,
    AgentPermission.GET_BALANCE_PERMISSION,
))


class AgentSafetyViolation(ValueError):
    """Agent 등록/구성 시 안전 정책 위반."""


@dataclass(frozen=True)
class AgentSafetyPolicy:
    """Agent 안전 정책 메타데이터. 영구 False 플래그를 묶는다."""

    direct_order_allowed: bool = False  # 영구
    can_invoke_broker: bool = False     # 영구
    can_invoke_order_gateway: bool = False  # 영구
    used_for_order: bool = False        # 영구


# ── 입력 / 발견 / 추천 / 결정 / 출력 ─────────────────────────────


@dataclass(frozen=True)
class AgentInput:
    """Agent 입력 (JSON 직렬화 가능). 절대 secret 키를 담지 않는다."""

    role: str
    task: str
    payload: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "task": self.task,
            "payload": dict(self.payload),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, sort_keys=True)


@dataclass(frozen=True)
class AgentFinding:
    """Agent 가 보고하는 관찰/감사 결과."""

    kind: str
    severity: str  # INFO/WARNING/HIGH/CRITICAL
    message: str
    evidence: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class AgentRecommendation:
    """Agent 의 *추천*. 실제 주문 명령이 아님 (CLAUDE.md §2.3).

    - ``is_order_request`` 는 영구 False. 호출자가 True 로 설정하려 하면 검증 단계
      에서 실패한다.
    - ``requires_review`` 는 기본 True — RiskManager / OrderGuard / PermissionGate
      / ApprovalQueue / OrderGateway 경로의 검토를 명시.
    """

    kind: str
    summary: str
    evidence: Mapping[str, object] = field(default_factory=dict)
    requires_review: bool = True
    is_order_request: bool = False  # 영구 False

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "summary": self.summary,
            "evidence": dict(self.evidence),
            "requires_review": self.requires_review,
            "is_order_request": self.is_order_request,
        }


@dataclass(frozen=True)
class AgentDecision:
    """Agent 의 결정 묶음.

    *주문 명령이 아님*. ``is_executable=False`` 영구. 호출자가 True 로 설정하려
    하면 검증 단계에서 실패한다.
    """

    role: str
    summary: str
    findings: tuple[AgentFinding, ...] = ()
    recommendations: tuple[AgentRecommendation, ...] = ()
    is_executable: bool = False  # 영구 False

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "is_executable": self.is_executable,
        }


@dataclass(frozen=True)
class AgentOutput:
    """Agent 의 JSON structured output.

    - ``direct_order_allowed`` / ``used_for_order`` 영구 False.
    - 호출자가 True 로 설정하려 하면 검증 단계에서 실패한다.
    """

    role: str
    version: str
    generated_at: datetime
    decision: AgentDecision
    direct_order_allowed: bool = False  # 영구 False
    used_for_order: bool = False        # 영구 False

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "version": self.version,
            "generated_at": self.generated_at.isoformat(),
            "decision": self.decision.to_dict(),
            "direct_order_allowed": self.direct_order_allowed,
            "used_for_order": self.used_for_order,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, sort_keys=True)


# ── MOCA 카드 ───────────────────────────────────────────────────


@dataclass(frozen=True)
class AgentCard:
    """MOCA 모듈 카드 — UI/문서에 표시할 Agent 역할 카탈로그.

    ``allowed_permissions`` 는 FORBIDDEN 카탈로그와 교집합이 없어야 한다.
    ``direct_order_allowed`` / ``can_invoke_broker`` / ``can_invoke_order_gateway``
    는 영구 False.
    """

    role: AgentArchitectureRole
    title: str
    description: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    allowed_permissions: frozenset[AgentPermission] = field(
        default_factory=frozenset,
    )
    direct_order_allowed: bool = False  # 영구
    can_invoke_broker: bool = False     # 영구
    can_invoke_order_gateway: bool = False  # 영구

    def to_dict(self) -> dict:
        return {
            "role": self.role.value,
            "title": self.title,
            "description": self.description,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "forbidden_actions": list(self.forbidden_actions),
            "allowed_permissions": sorted(p.value for p in self.allowed_permissions),
            "direct_order_allowed": self.direct_order_allowed,
            "can_invoke_broker": self.can_invoke_broker,
            "can_invoke_order_gateway": self.can_invoke_order_gateway,
        }

    @property
    def grants_any_forbidden(self) -> bool:
        return bool(self.allowed_permissions & FORBIDDEN_AGENT_PERMISSIONS)


# ── 구조적 Agent ABC ────────────────────────────────────────────


class StructuredAgentBase(ABC):
    """6-role Agent Architecture 의 추상 base.

    sub-class 는 ``role`` ClassVar 와 ``card`` ClassVar 를 반드시 정의한다.
    ``evaluate(input)`` 가 유일한 abstract 메서드.

    안전 정책:
      - validate_safety() — 인스턴스 생성 또는 등록 직후 호출. ``card.grants_any_forbidden``
        또는 영구 False 플래그 위반 시 ``AgentSafetyViolation`` raise.
      - direct_order_allowed / can_invoke_broker / can_invoke_order_gateway 영구 False.
    """

    role: ClassVar[AgentArchitectureRole]
    card: ClassVar[AgentCard]
    safety: ClassVar[AgentSafetyPolicy] = AgentSafetyPolicy()

    @abstractmethod
    def evaluate(self, input: AgentInput) -> AgentOutput:
        """입력을 받아 ``AgentOutput`` 반환. 절대 주문하지 않는다."""

    def validate_safety(self) -> None:
        """안전 정책 회귀 검사. 위반 시 AgentSafetyViolation."""
        if self.card.grants_any_forbidden:
            forbidden = sorted(
                p.value for p in (
                    self.card.allowed_permissions & FORBIDDEN_AGENT_PERMISSIONS
                )
            )
            raise AgentSafetyViolation(
                f"{type(self).__name__} 가 FORBIDDEN 권한 요청: {forbidden}"
            )
        if self.safety.direct_order_allowed:
            raise AgentSafetyViolation(
                f"{type(self).__name__}.safety.direct_order_allowed must be False"
            )
        if self.safety.can_invoke_broker:
            raise AgentSafetyViolation(
                f"{type(self).__name__}.safety.can_invoke_broker must be False"
            )
        if self.safety.can_invoke_order_gateway:
            raise AgentSafetyViolation(
                f"{type(self).__name__}.safety.can_invoke_order_gateway must be False"
            )
        if self.card.role != self.role:
            raise AgentSafetyViolation(
                f"{type(self).__name__} 의 card.role 과 role ClassVar 불일치"
            )

    def make_output(
        self,
        decision: AgentDecision,
        *,
        version: str = "v1",
    ) -> AgentOutput:
        """공통 출력 헬퍼. direct_order_allowed/used_for_order 자동 False."""
        return AgentOutput(
            role=self.role.value,
            version=version,
            generated_at=datetime.now(timezone.utc),
            decision=decision,
        )


# ── 6 role skeleton agents ──────────────────────────────────────


class ObserverAgent(StructuredAgentBase):
    """시장 데이터 / freshness / data quality / notices / theme 관찰.

    판단 결론을 내리지 않고 *관찰 요약* 만 만든다.
    """

    role: ClassVar[AgentArchitectureRole] = AgentArchitectureRole.OBSERVER
    card: ClassVar[AgentCard] = AgentCard(
        role=AgentArchitectureRole.OBSERVER,
        title="Observer Agent",
        description=(
            "시장 데이터·freshness·data_quality·notices·theme_context 를 관찰한다. "
            "판단 결론은 내리지 않고 관찰 요약만 만든다."
        ),
        inputs=(
            "market_data", "freshness_state", "data_quality_grade",
            "notice_context", "theme_context",
        ),
        outputs=("observation_summary", "observed_findings"),
        forbidden_actions=(
            "execute_order", "invoke_broker", "invoke_order_gateway",
            "write_order_request", "build_recommendation",
        ),
        allowed_permissions=frozenset((
            AgentPermission.READ_MARKET_DATA,
            AgentPermission.READ_FRESHNESS,
            AgentPermission.READ_DATA_QUALITY,
            AgentPermission.READ_NOTICES,
            AgentPermission.READ_THEMES,
            AgentPermission.WRITE_FINDING,
        )),
    )

    def evaluate(self, input: AgentInput) -> AgentOutput:
        payload = input.payload
        findings: list[AgentFinding] = []
        for key, label in (
            ("freshness_state", "freshness"),
            ("data_quality_grade", "data_quality"),
            ("notice_context", "notices"),
            ("theme_context", "themes"),
        ):
            if key in payload:
                findings.append(AgentFinding(
                    kind=f"observed_{label}",
                    severity="INFO",
                    message=f"{label} context observed",
                    evidence={key: payload[key]},
                ))
        symbol = str(payload.get("symbol", "unknown"))
        decision = AgentDecision(
            role=self.role.value,
            summary=f"observation for {symbol}",
            findings=tuple(findings),
            recommendations=(),
        )
        return self.make_output(decision)


class AnalystAgent(StructuredAgentBase):
    """전략 Signal / 시장 상태 / 지표 분석. 직접 주문 금지."""

    role: ClassVar[AgentArchitectureRole] = AgentArchitectureRole.ANALYST
    card: ClassVar[AgentCard] = AgentCard(
        role=AgentArchitectureRole.ANALYST,
        title="Analyst Agent",
        description=(
            "전략 Signal·시장 상태·지표를 분석한다. 후보의 장단점과 근거를 요약하지만 "
            "직접 주문하지 않는다."
        ),
        inputs=("strategy_signal", "regime", "indicators", "kimp_result"),
        outputs=("analysis_findings", "candidate_summary"),
        forbidden_actions=(
            "execute_order", "invoke_broker", "invoke_order_gateway",
            "write_order_request",
        ),
        allowed_permissions=frozenset((
            AgentPermission.READ_MARKET_DATA,
            AgentPermission.READ_KIMP,
            AgentPermission.READ_STRATEGY_CATALOG,
            AgentPermission.WRITE_FINDING,
        )),
    )

    def evaluate(self, input: AgentInput) -> AgentOutput:
        payload = input.payload
        findings: list[AgentFinding] = []
        if "strategy_signal" in payload:
            findings.append(AgentFinding(
                kind="signal_analysis",
                severity="INFO",
                message="strategy signal analyzed",
                evidence={"signal": payload["strategy_signal"]},
            ))
        if "regime" in payload:
            findings.append(AgentFinding(
                kind="regime_analysis",
                severity="INFO",
                message=f"regime={payload.get('regime')}",
                evidence={"regime": payload["regime"]},
            ))
        decision = AgentDecision(
            role=self.role.value,
            summary="candidate analysis summary",
            findings=tuple(findings),
            recommendations=(),
        )
        return self.make_output(decision)


class RiskAuditorAgent(StructuredAgentBase):
    """리스크 / stale data / funding / kimp guards / permission 상태 감사.

    차단 사유와 review_required 를 산출하지만 직접 주문하지 않는다.
    """

    role: ClassVar[AgentArchitectureRole] = AgentArchitectureRole.RISK_AUDITOR
    card: ClassVar[AgentCard] = AgentCard(
        role=AgentArchitectureRole.RISK_AUDITOR,
        title="Risk Auditor Agent",
        description=(
            "리스크·stale data·data quality·funding cost·kimp guards·permission "
            "상태를 감사한다. 차단 사유와 review_required 를 산출한다."
        ),
        inputs=(
            "freshness_state", "data_quality_grade", "kimp_guard_decision",
            "funding_guard_decision", "permission_state",
        ),
        outputs=("risk_findings", "blocked_by", "review_codes"),
        forbidden_actions=(
            "execute_order", "invoke_broker", "invoke_order_gateway",
            "write_order_request", "place_order",
        ),
        allowed_permissions=frozenset((
            AgentPermission.READ_FRESHNESS,
            AgentPermission.READ_DATA_QUALITY,
            AgentPermission.READ_KIMP,
            AgentPermission.READ_FUNDING,
            AgentPermission.READ_RISK_STATE,
            AgentPermission.WRITE_FINDING,
        )),
    )

    def evaluate(self, input: AgentInput) -> AgentOutput:
        payload = input.payload
        findings: list[AgentFinding] = []
        for key, source in (
            ("kimp_guard_decision", "kimp_guard"),
            ("funding_guard_decision", "funding_guard"),
        ):
            decision_payload = payload.get(key)
            if isinstance(decision_payload, dict):
                blocked = decision_payload.get("blocked_by") or []
                review = decision_payload.get("review_codes") or []
                if blocked:
                    findings.append(AgentFinding(
                        kind=f"{source}_blocked",
                        severity="HIGH",
                        message=f"{source} blocked: {blocked}",
                        evidence={"blocked_by": list(blocked)},
                    ))
                if review:
                    findings.append(AgentFinding(
                        kind=f"{source}_review",
                        severity="WARNING",
                        message=f"{source} review codes: {review}",
                        evidence={"review_codes": list(review)},
                    ))
        decision = AgentDecision(
            role=self.role.value,
            summary="risk audit summary",
            findings=tuple(findings),
            recommendations=(),
        )
        return self.make_output(decision)


class StrategyResearcherAgent(StructuredAgentBase):
    """전략별 성능 / 장세 적합성 / 후보 전략 조사.

    StrategySelectionAgent 가 사용할 context 를 제공한다.
    """

    role: ClassVar[AgentArchitectureRole] = AgentArchitectureRole.STRATEGY_RESEARCHER
    card: ClassVar[AgentCard] = AgentCard(
        role=AgentArchitectureRole.STRATEGY_RESEARCHER,
        title="Strategy Researcher Agent",
        description=(
            "전략별 성능·장세 적합성·후보 전략을 조사한다. StrategySelectionAgent "
            "가 사용할 context 를 제공한다. 직접 주문하지 않는다."
        ),
        inputs=("regime", "strategy_catalog", "performance_history"),
        outputs=("strategy_candidates", "regime_fit_findings"),
        forbidden_actions=(
            "execute_order", "invoke_broker", "invoke_order_gateway",
            "write_order_request",
        ),
        allowed_permissions=frozenset((
            AgentPermission.READ_STRATEGY_CATALOG,
            AgentPermission.READ_PERFORMANCE_HISTORY,
            AgentPermission.READ_MARKET_DATA,
            AgentPermission.WRITE_FINDING,
        )),
    )

    def evaluate(self, input: AgentInput) -> AgentOutput:
        payload = input.payload
        findings: list[AgentFinding] = []
        regime = payload.get("regime")
        if regime is not None:
            findings.append(AgentFinding(
                kind="regime_research",
                severity="INFO",
                message=f"regime={regime}",
                evidence={"regime": regime},
            ))
        catalog = payload.get("strategy_catalog") or []
        if catalog:
            findings.append(AgentFinding(
                kind="catalog_research",
                severity="INFO",
                message=f"{len(catalog)} strategies in catalog",
                evidence={"count": len(catalog)},
            ))
        decision = AgentDecision(
            role=self.role.value,
            summary="strategy research summary",
            findings=tuple(findings),
            recommendations=(),
        )
        return self.make_output(decision)


class ReportWriterAgent(StructuredAgentBase):
    """사람이 읽을 수 있는 보고서 / 로그 요약 생성.

    결론은 *설명용* 이며 주문 지시가 아니다.
    """

    role: ClassVar[AgentArchitectureRole] = AgentArchitectureRole.REPORT_WRITER
    card: ClassVar[AgentCard] = AgentCard(
        role=AgentArchitectureRole.REPORT_WRITER,
        title="Report Writer Agent",
        description=(
            "사람이 읽을 수 있는 보고서·로그 요약을 만든다. 결론은 설명용이며 "
            "주문 지시가 아니다."
        ),
        inputs=("findings_bundle", "audit_log", "performance_history"),
        outputs=("human_readable_report",),
        forbidden_actions=(
            "execute_order", "invoke_broker", "invoke_order_gateway",
            "write_order_request",
        ),
        allowed_permissions=frozenset((
            AgentPermission.READ_PERFORMANCE_HISTORY,
            AgentPermission.WRITE_REPORT,
        )),
    )

    def evaluate(self, input: AgentInput) -> AgentOutput:
        payload = input.payload
        bundle = payload.get("findings_bundle") or []
        summary = f"{len(bundle)} findings collated"
        decision = AgentDecision(
            role=self.role.value,
            summary=summary,
            findings=(),
            recommendations=(AgentRecommendation(
                kind="report",
                summary=summary,
                evidence={"finding_count": len(bundle)},
                requires_review=True,
            ),),
        )
        return self.make_output(decision)


class ExecutionRecommenderAgent(StructuredAgentBase):
    """실행 후보에 대한 *권고* 만 만든다. 직접 주문하지 *않는다*.

    output 은 ``execution_recommendation`` 일 뿐 executable order 가 아니다.
    반드시 ``direct_order_allowed=False`` 를 포함한다. 최종 실행은 후속 Risk /
    OrderGuard / PermissionGate / ApprovalQueue / OrderGateway 에서만 가능.
    """

    role: ClassVar[AgentArchitectureRole] = (
        AgentArchitectureRole.EXECUTION_RECOMMENDER
    )
    card: ClassVar[AgentCard] = AgentCard(
        role=AgentArchitectureRole.EXECUTION_RECOMMENDER,
        title="Execution Recommender Agent",
        description=(
            "실행 후보에 대한 권고를 만든다. output 은 execution_recommendation "
            "이며 executable order 가 아니다. direct_order_allowed=False 영구. "
            "최종 실행은 RiskManager → OrderGuard → PermissionGate → "
            "ApprovalQueue → OrderGateway 경로에서만 가능."
        ),
        inputs=(
            "candidate_summary", "risk_findings", "kimp_guard_decision",
            "funding_guard_decision",
        ),
        outputs=("execution_recommendation",),
        forbidden_actions=(
            "execute_order", "invoke_broker", "invoke_order_gateway",
            "write_order_request", "place_order", "cancel_order",
            "get_balance",
        ),
        allowed_permissions=frozenset((
            AgentPermission.READ_KIMP,
            AgentPermission.READ_FUNDING,
            AgentPermission.READ_RISK_STATE,
            AgentPermission.WRITE_RECOMMENDATION,
        )),
    )

    def evaluate(self, input: AgentInput) -> AgentOutput:
        payload = input.payload
        summary = str(payload.get("candidate_summary") or "no candidate")
        rec = AgentRecommendation(
            kind="execution_recommendation",
            summary=summary,
            evidence={"candidate": payload.get("candidate_summary")},
            requires_review=True,
        )
        decision = AgentDecision(
            role=self.role.value,
            summary=f"execution recommendation: {summary}",
            findings=(),
            recommendations=(rec,),
        )
        return self.make_output(decision)


# ── 구조적 Registry (MOCA 카드 카탈로그) ────────────────────────


class StructuredAgentRegistry:
    """6-role Agent Architecture 의 등록소. role 단위 그룹핑."""

    def __init__(self):
        self._by_role: dict[AgentArchitectureRole, list[StructuredAgentBase]] = {}

    def register(self, agent: StructuredAgentBase) -> None:
        agent.validate_safety()
        self._by_role.setdefault(agent.role, []).append(agent)

    def all(self) -> list[StructuredAgentBase]:
        out: list[StructuredAgentBase] = []
        for role in AgentArchitectureRole:
            out.extend(self._by_role.get(role, []))
        return out

    def by_role(
        self, role: AgentArchitectureRole,
    ) -> list[StructuredAgentBase]:
        return list(self._by_role.get(role, []))

    def cards(self) -> list[AgentCard]:
        return [a.card for a in self.all()]

    def catalog(self) -> list[dict]:
        return [c.to_dict() for c in self.cards()]

    def roles_present(self) -> set[AgentArchitectureRole]:
        return set(self._by_role.keys())

    def clear(self) -> None:
        self._by_role.clear()


def collect_architecture_agents() -> StructuredAgentRegistry:
    """기본 6-role Agent 셋을 등록한 ``StructuredAgentRegistry`` 반환.

    각 Agent 는 deterministic skeleton — LLM 호출 없음. 후속 단계 (#38 이상) 에서
    역할별 본격 구현이 추가될 수 있다.
    """
    r = StructuredAgentRegistry()
    r.register(ObserverAgent())
    r.register(AnalystAgent())
    r.register(RiskAuditorAgent())
    r.register(StrategyResearcherAgent())
    r.register(ReportWriterAgent())
    r.register(ExecutionRecommenderAgent())
    return r


__all_v2__ = (
    "AgentArchitectureRole",
    "AgentPermission",
    "FORBIDDEN_AGENT_PERMISSIONS",
    "AgentSafetyPolicy",
    "AgentSafetyViolation",
    "AgentInput",
    "AgentFinding",
    "AgentRecommendation",
    "AgentDecision",
    "AgentOutput",
    "AgentCard",
    "StructuredAgentBase",
    "ObserverAgent",
    "AnalystAgent",
    "RiskAuditorAgent",
    "StrategyResearcherAgent",
    "ReportWriterAgent",
    "ExecutionRecommenderAgent",
    "StructuredAgentRegistry",
    "collect_architecture_agents",
)
