"""체크리스트 #11 Audit Foundation — 통합 이벤트 facade.

본 모듈은 기존 `AuditLog` (메모리 + CSV) 위에 얹는 *상위* facade 이다.
기존 구현(`AuditLog`, `OrderAuditLog`, `AgentDecisionLog`, `redact`) 는 그대로
유지되며, 본 모듈은 다음 4가지를 추가한다:

  1. `EventType` / `Severity` / `SourceKind` — enum 분류 체계
  2. `AuditEventInput` — helper 들의 표준 출력 형식
  3. `build_*_event()` — 신호/주문/승인/거절/Agent 판단/Risk/Feature Flag/
     Emergency Stop/Settings 변경 이벤트 빌더
  4. `log_audit_event()` — secret 누설 fail-closed 검사 + `AuditLog.record()` 위임

설계 원칙 (CLAUDE.md §2 / 체크리스트 #11):
  - 본 모듈은 `app.brokers.*`, `app.execution.order_executor`, `route_order` 를
    절대 import 하지 않는다. 단일 주문 경로 우회 금지.
  - secret 패턴이 발견되면 `SecretLeakError` 로 즉시 raise (fail-closed).
    기존 `redact()` 는 사후 마스킹이지만 본 helper 계층은 *입력 검증* 단계에서
    secret 진입을 차단한다.
  - Settings 변경 이벤트에서 secret 계열 key 는 값 자체를 저장하지 않고
    `"SECRET_VALUE_OMITTED"` 마커만 남긴다.
  - Agent 판단은 `is_order_intent=False` 를 details 에 명시한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional

from .audit_log import AuditLog
from .redaction import SECRET_KEY_TOKENS


# ─────────────────────────────────────────────────────────────────
# 1. 분류 enum
# ─────────────────────────────────────────────────────────────────

class EventType(str, Enum):
    """통합 timeline 이벤트 분류.

    기존 string event_type (예: "ORDER_INTENT", "AGENT_DECISION") 과 공존한다.
    본 enum 은 *상위* helper 가 생성하는 정규 이벤트만 다룬다.
    """

    SIGNAL              = "SIGNAL"
    ORDER_REQUEST       = "ORDER_REQUEST"
    ORDER_BLOCKED       = "ORDER_BLOCKED"
    APPROVAL_DECISION   = "APPROVAL_DECISION"
    RISK_BLOCK          = "RISK_BLOCK"
    AI_PROPOSAL         = "AI_PROPOSAL"
    AGENT_DECISION      = "AGENT_DECISION"
    FEATURE_FLAG_BLOCK  = "FEATURE_FLAG_BLOCK"
    EMERGENCY_STOP      = "EMERGENCY_STOP"
    SETTINGS_CHANGE     = "SETTINGS_CHANGE"
    VIRTUAL_ORDER       = "VIRTUAL_ORDER"
    FUTURES_RISK        = "FUTURES_RISK"
    NOTIFICATION        = "NOTIFICATION"
    OPERATOR_NOTE       = "OPERATOR_NOTE"
    STRATEGY_CHANGE     = "STRATEGY_CHANGE"
    DATA_QUALITY        = "DATA_QUALITY"
    SYSTEM              = "SYSTEM"


class Severity(str, Enum):
    INFO     = "INFO"
    WARNING  = "WARNING"
    SECURITY = "SECURITY"
    CRITICAL = "CRITICAL"


class SourceKind(str, Enum):
    """이벤트 발신 모듈 분류."""

    SYSTEM    = "system"
    STRATEGY  = "strategy"
    AGENT     = "agent"
    AI        = "ai"
    RISK      = "risk"
    GOVERNANCE = "governance"
    OPERATOR  = "operator"
    EXECUTION = "execution"


# ─────────────────────────────────────────────────────────────────
# 2. SecretLeakError + fail-closed scan
# ─────────────────────────────────────────────────────────────────

class SecretLeakError(RuntimeError):
    """감사 이벤트 입력에 secret 류 값이 포함됐을 때 raise.

    기존 `redact()` 가 사후 마스킹이라면, 본 정책은 *사전 차단* 이다.
    감사 로그 호출자는 secret 진입 자체를 막아야 한다 (CLAUDE.md §2.1.3).
    """


# 값 자체에서 secret 형식을 탐지하는 패턴.
# 길이 임계값(20+) 은 일반 사용자 문자열과의 false-positive 를 최소화.
_VALUE_PATTERNS: tuple[re.Pattern, ...] = (
    # Bearer / Basic 헤더
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-]{12,}\b"),
    # Anthropic / OpenAI key
    re.compile(r"\bsk-(ant-)?[A-Za-z0-9_\-]{20,}\b"),
    # Telegram bot token (1234567890:AA…)
    re.compile(r"\b\d{8,12}:AA[A-Za-z0-9_\-]{30,}\b"),
    # 한국 계좌번호 (10-14자리 숫자, 하이픈 포함 가능)
    re.compile(r"\b\d{3,6}-\d{2,6}-\d{2,8}\b"),
    # PEM private key header
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
)

# Settings 변경 이벤트에서 값을 통째로 생략해야 하는 key 패턴.
_SETTING_SECRET_KEY_TOKENS = SECRET_KEY_TOKENS + (
    "kis_app_key",
    "kis_app_secret",
    "kis_account_no",
)

SECRET_VALUE_OMITTED = "SECRET_VALUE_OMITTED"


def _is_secret_key_name(key: str) -> bool:
    if not isinstance(key, str):
        return False
    k = key.lower().replace("-", "_")
    return any(tok in k for tok in _SETTING_SECRET_KEY_TOKENS)


def _scan_for_secrets(node: Any, path: str = "") -> None:
    """입력 트리에서 secret key/value 패턴 발견 시 SecretLeakError raise.

    검사 대상:
      - dict key 가 SECRET_KEY_TOKENS 에 매칭되고 그 값이 비어 있지 않으면 fail.
      - 모든 문자열 값을 _VALUE_PATTERNS 로 검사. 매칭되면 fail.
    """
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{path}.{k}" if path else str(k)
            if _is_secret_key_name(str(k)) and v not in (None, "", {}, []):
                raise SecretLeakError(
                    f"audit event 입력에 secret 류 key 가 들어있음: {here!r} "
                    "(fail-closed). 호출자는 sanitize 후 다시 시도해야 합니다."
                )
            _scan_for_secrets(v, here)
    elif isinstance(node, (list, tuple)):
        for i, item in enumerate(node):
            _scan_for_secrets(item, f"{path}[{i}]")
    elif isinstance(node, str):
        for pat in _VALUE_PATTERNS:
            if pat.search(node):
                raise SecretLeakError(
                    f"audit event 입력에 secret 형식 값이 들어있음 "
                    f"(path={path or '<root>'}, pattern={pat.pattern!r})"
                )


# ─────────────────────────────────────────────────────────────────
# 3. AuditEventInput dataclass
# ─────────────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AuditEventInput:
    """`build_*_event()` 들의 표준 반환 / `log_audit_event()` 입력 형식.

    필드 매핑은 통합 timeline `AuditEvent` 의 권장 컬럼명을 따른다.
    실제 DB schema 에 모든 컬럼이 없어도 무방 — 본 dataclass 는 in-memory
    표현이며, payload 는 `AuditLog.record()` 의 dict 로 직렬화된다.

    필드:
      event_type   : EventType (또는 호환 string)
      severity     : Severity
      source       : SourceKind
      summary      : 짧은 요약 (255자 권장)
      reason       : 상세 사유 (255자 권장)
      details      : 구조화 payload (JSON 직렬화 가능해야 함)
      actor        : 발신자 식별자 (사람 id 또는 module 명)
      symbol       : 거래 심볼 (선택)
      strategy     : 전략 이름 (선택)
      mode         : 운용 모드 ("paper"/"mock"/"live" 등)
      target_kind  : 참조 도메인 row 종류 (예: "OrderAuditLog")
      target_id    : 참조 도메인 row id
      chain_id     : 동일 의사결정 chain 식별자 (signal→order→approval 연결용)
      created_at   : timestamp
    """

    event_type:  str  # EventType.value
    severity:    str = Severity.INFO.value
    source:      str = SourceKind.SYSTEM.value
    summary:     str = ""
    reason:      str = ""
    details:     dict = field(default_factory=dict)
    actor:       Optional[str] = None
    symbol:      Optional[str] = None
    strategy:    Optional[str] = None
    mode:        Optional[str] = None
    target_kind: Optional[str] = None
    target_id:   Optional[int] = None
    chain_id:    Optional[str] = None
    created_at:  datetime = field(default_factory=_utc_now)

    def to_payload(self) -> dict:
        """`AuditLog.record()` 의 payload 로 사용할 dict."""
        return {
            "severity":    self.severity,
            "source":      self.source,
            "summary":     self.summary,
            "reason":      self.reason,
            "details":     self.details,
            "actor":       self.actor,
            "symbol":      self.symbol,
            "strategy":    self.strategy,
            "mode":        self.mode,
            "target_kind": self.target_kind,
            "target_id":   self.target_id,
            "chain_id":    self.chain_id,
            "created_at":  self.created_at.isoformat(),
        }


# ─────────────────────────────────────────────────────────────────
# 4. log_audit_event — fail-closed + 위임
# ─────────────────────────────────────────────────────────────────

# 검사 대상 필드 (체크리스트 #11 §9단계).
_SCAN_FIELDS = ("summary", "reason", "actor", "symbol", "strategy")


def log_audit_event(event: AuditEventInput, audit: AuditLog) -> dict:
    """secret 패턴 검사 후 `AuditLog.record()` 위임.

    검사 대상: summary, reason, details, actor, symbol, strategy.
    매칭 시 `SecretLeakError` 로 fail-closed.
    """
    # 1) 스칼라 필드 검사
    for fname in _SCAN_FIELDS:
        val = getattr(event, fname, None)
        if val is None:
            continue
        _scan_for_secrets(val, path=fname)

    # 2) details (재귀 검사)
    _scan_for_secrets(event.details, path="details")

    # 3) 검사 통과 → 위임. `AuditLog.record()` 가 redaction 도 추가로 적용한다
    #    (이중 안전: helper 단계는 fail-closed, 저장 단계는 마스킹).
    return audit.record(event.event_type, event.to_payload())


# ─────────────────────────────────────────────────────────────────
# 5. build_*_event helpers
# ─────────────────────────────────────────────────────────────────

def build_signal_event(
    *,
    symbol: str,
    action: str,
    strategy: str,
    confidence: float,
    reason: str = "",
    mode: Optional[str] = None,
    chain_id: Optional[str] = None,
    details: Optional[dict] = None,
) -> AuditEventInput:
    """전략 신호 발생 이벤트."""
    payload = dict(details or {})
    payload.setdefault("action", action)
    payload.setdefault("confidence", float(confidence))
    return AuditEventInput(
        event_type=EventType.SIGNAL.value,
        severity=Severity.INFO.value,
        source=SourceKind.STRATEGY.value,
        summary=(f"[signal] {strategy} {action} {symbol}")[:255],
        reason=reason[:255],
        details=payload,
        symbol=symbol,
        strategy=strategy,
        mode=mode,
        chain_id=chain_id,
    )


def build_order_request_event(
    *,
    symbol: str,
    side: str,
    quantity: Any,
    order_type: str = "market",
    mode: Optional[str] = None,
    actor: Optional[str] = None,
    strategy: Optional[str] = None,
    target_id: Optional[int] = None,
    chain_id: Optional[str] = None,
    reason: str = "",
    details: Optional[dict] = None,
) -> AuditEventInput:
    """주문 요청 발생 이벤트.

    주의: API Key / Secret / 계좌번호 / 토큰을 details 에 넣지 말 것.
    SecretLeakError 가 fail-closed 로 차단한다.
    """
    payload = dict(details or {})
    payload.update({
        "side":       side,
        "quantity":   str(quantity),
        "order_type": order_type,
    })
    return AuditEventInput(
        event_type=EventType.ORDER_REQUEST.value,
        severity=Severity.INFO.value,
        source=SourceKind.SYSTEM.value,
        summary=(f"[order] request {side} {symbol} qty={quantity}")[:255],
        reason=reason[:255],
        details=payload,
        actor=actor,
        symbol=symbol,
        strategy=strategy,
        mode=mode,
        target_kind="OrderAuditLog" if target_id else None,
        target_id=target_id,
        chain_id=chain_id,
    )


def build_order_blocked_event(
    *,
    symbol: str,
    blocked_by: str,
    reason: str,
    mode: Optional[str] = None,
    actor: Optional[str] = None,
    target_id: Optional[int] = None,
    chain_id: Optional[str] = None,
    details: Optional[dict] = None,
) -> AuditEventInput:
    """주문 차단 이벤트 (RiskManager/PermissionGate/Feature Flag 어디서 막혔는지 명시)."""
    payload = dict(details or {})
    payload.setdefault("blocked_by", blocked_by)
    return AuditEventInput(
        event_type=EventType.ORDER_BLOCKED.value,
        severity=Severity.WARNING.value,
        source=SourceKind.EXECUTION.value,
        summary=(f"[order] blocked by {blocked_by} for {symbol}")[:255],
        reason=reason[:255],
        details=payload,
        actor=actor,
        symbol=symbol,
        mode=mode,
        target_kind="OrderAuditLog" if target_id else None,
        target_id=target_id,
        chain_id=chain_id,
    )


def build_approval_decision_event(
    *,
    approval_id: str,
    approved: bool,
    approver: str,
    symbol: Optional[str] = None,
    mode: Optional[str] = None,
    target_id: Optional[int] = None,
    chain_id: Optional[str] = None,
    reason: str = "",
    details: Optional[dict] = None,
) -> AuditEventInput:
    """승인 또는 거절 이벤트."""
    payload = dict(details or {})
    payload.update({
        "approval_id": approval_id,
        "approved":    bool(approved),
        "approver":    approver,
    })
    outcome = "approved" if approved else "rejected"
    return AuditEventInput(
        event_type=EventType.APPROVAL_DECISION.value,
        severity=Severity.INFO.value if approved else Severity.WARNING.value,
        source=SourceKind.GOVERNANCE.value,
        summary=(f"[approval] {outcome} {approval_id} by {approver}")[:255],
        reason=reason[:255],
        details=payload,
        actor=approver,
        symbol=symbol,
        mode=mode,
        target_kind="PendingApproval" if target_id else None,
        target_id=target_id,
        chain_id=chain_id,
    )


def build_risk_block_event(
    *,
    symbol: str,
    reasons: Iterable[str],
    mode: Optional[str] = None,
    target_id: Optional[int] = None,
    chain_id: Optional[str] = None,
    details: Optional[dict] = None,
) -> AuditEventInput:
    """RiskManager 차단 이벤트."""
    reason_list = list(reasons)
    payload = dict(details or {})
    payload.setdefault("reasons", reason_list)
    return AuditEventInput(
        event_type=EventType.RISK_BLOCK.value,
        severity=Severity.WARNING.value,
        source=SourceKind.RISK.value,
        summary=(f"[risk] block {symbol}")[:255],
        reason=("; ".join(reason_list))[:255],
        details=payload,
        symbol=symbol,
        mode=mode,
        target_kind="OrderAuditLog" if target_id else None,
        target_id=target_id,
        chain_id=chain_id,
    )


def build_feature_flag_blocked_event(
    *,
    feature_name: str,
    reason: str = "",
    mode: Optional[str] = None,
    actor: Optional[str] = None,
    symbol: Optional[str] = None,
    chain_id: Optional[str] = None,
    details: Optional[dict] = None,
) -> AuditEventInput:
    """Feature Flag 차단 이벤트 (체크리스트 #10 연계).

    위험 플래그가 꺼져 있어 기능 호출이 차단됐을 때 기록.
    """
    payload = dict(details or {})
    payload.setdefault("feature", feature_name)
    return AuditEventInput(
        event_type=EventType.FEATURE_FLAG_BLOCK.value,
        severity=Severity.SECURITY.value,
        source=SourceKind.SYSTEM.value,
        summary=(f"[flag] '{feature_name}' blocked")[:255],
        reason=reason[:255],
        details=payload,
        actor=actor,
        symbol=symbol,
        mode=mode,
        chain_id=chain_id,
    )


def build_emergency_stop_event(
    *,
    activated: bool,
    reason: str = "",
    actor: Optional[str] = None,
    mode: Optional[str] = None,
    details: Optional[dict] = None,
) -> AuditEventInput:
    """Emergency Stop 토글 이벤트 (사고 분석 핵심)."""
    payload = dict(details or {})
    payload.setdefault("activated", bool(activated))
    state = "ON" if activated else "OFF"
    return AuditEventInput(
        event_type=EventType.EMERGENCY_STOP.value,
        severity=Severity.CRITICAL.value if activated else Severity.WARNING.value,
        source=SourceKind.OPERATOR.value,
        summary=(f"[emergency_stop] {state}")[:255],
        reason=reason[:255],
        details=payload,
        actor=actor,
        mode=mode,
        target_kind="EmergencyStopEvent",
    )


def build_ai_proposal_event(
    *,
    agent_name: str,
    proposal: str,
    confidence: float,
    symbol: Optional[str] = None,
    mode: Optional[str] = None,
    chain_id: Optional[str] = None,
    details: Optional[dict] = None,
) -> AuditEventInput:
    """AI 제안 이벤트 — 직접 실행 권한 아님 (단순 제안)."""
    payload = dict(details or {})
    payload.update({
        "agent":      agent_name,
        "proposal":   proposal,
        "confidence": float(confidence),
        # CLAUDE.md §2.3: AI 제안은 그 자체로 주문 의도가 아니다.
        "is_order_intent": False,
    })
    return AuditEventInput(
        event_type=EventType.AI_PROPOSAL.value,
        severity=Severity.INFO.value,
        source=SourceKind.AI.value,
        summary=(f"[ai] {agent_name} {proposal}")[:255],
        details=payload,
        symbol=symbol,
        mode=mode,
        chain_id=chain_id,
    )


def build_agent_decision_event(
    *,
    agent_name: str,
    decision: str,
    confidence: Optional[float] = None,
    reasons: Optional[Iterable[str]] = None,
    symbol: Optional[str] = None,
    mode: Optional[str] = None,
    chain_id: Optional[str] = None,
    target_id: Optional[int] = None,
    details: Optional[dict] = None,
) -> AuditEventInput:
    """Agent 판단 이벤트 — 분석/추천 기록 (주문 실행 아님)."""
    payload = dict(details or {})
    payload.update({
        "agent":      agent_name,
        "decision":   decision,
        "confidence": float(confidence) if confidence is not None else None,
        "reasons":    list(reasons or []),
        # CLAUDE.md §2.3: Agent 판단은 직접 주문이 아니다.
        "is_order_intent": False,
    })
    return AuditEventInput(
        event_type=EventType.AGENT_DECISION.value,
        severity=Severity.INFO.value,
        source=SourceKind.AGENT.value,
        summary=(f"[agent] {agent_name} {decision}")[:255],
        details=payload,
        symbol=symbol,
        mode=mode,
        target_kind="AgentDecisionLog" if target_id else None,
        target_id=target_id,
        chain_id=chain_id,
    )


def build_settings_change_event(
    *,
    setting_key: str,
    old_value: Any = None,
    new_value: Any = None,
    actor: Optional[str] = None,
    mode: Optional[str] = None,
    reason: str = "",
    details: Optional[dict] = None,
) -> AuditEventInput:
    """설정 변경 이벤트.

    중요:
      - setting_key 가 secret 계열이면 old_value / new_value 는 통째로
        `SECRET_VALUE_OMITTED` 마커로 대체 (CLAUDE.md §2.1.3).
      - live / ai / futures 관련 설정 변경은 Severity.SECURITY.
    """
    payload = dict(details or {})
    if _is_secret_key_name(setting_key):
        payload.update({
            "setting_key": setting_key,
            "old_value":   SECRET_VALUE_OMITTED,
            "new_value":   SECRET_VALUE_OMITTED,
            "changed_by":  actor,
        })
    else:
        payload.update({
            "setting_key": setting_key,
            "old_value":   old_value,
            "new_value":   new_value,
            "changed_by":  actor,
        })

    high_risk = any(tok in setting_key.lower()
                    for tok in ("live", "ai_execution", "futures"))
    severity = Severity.SECURITY.value if high_risk else Severity.INFO.value

    return AuditEventInput(
        event_type=EventType.SETTINGS_CHANGE.value,
        severity=severity,
        source=SourceKind.OPERATOR.value,
        summary=(f"[settings] {setting_key} changed")[:255],
        reason=reason[:255],
        details=payload,
        actor=actor,
        mode=mode,
    )


__all__ = [
    # enums / errors
    "EventType", "Severity", "SourceKind",
    "SecretLeakError", "SECRET_VALUE_OMITTED",
    # io
    "AuditEventInput", "log_audit_event",
    # builders
    "build_signal_event",
    "build_order_request_event",
    "build_order_blocked_event",
    "build_approval_decision_event",
    "build_risk_block_event",
    "build_feature_flag_blocked_event",
    "build_emergency_stop_event",
    "build_ai_proposal_event",
    "build_agent_decision_event",
    "build_settings_change_event",
]
