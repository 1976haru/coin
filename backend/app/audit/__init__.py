"""감사 로그 패키지 — 모든 주문/Agent 판단/리스크 이벤트를 영구 기록.

체크리스트 #11 Audit Foundation, #87 Audit Log.

구성:
  - AuditLog          : 메모리 + CSV 베이스 저장소 (자동 redaction 적용)
  - OrderAuditLog     : 주문 lifecycle 이벤트 facade
  - AgentDecisionLog  : Agent 판단 이벤트 facade
  - redact / REDACTED : secret·PII 마스킹 유틸
  - events            : 통합 timeline 이벤트 빌더 (체크리스트 #11)
  - archive           : 감사 이벤트 archive (삭제 금지)
"""
from .audit_log import AuditLog, InMemoryAuditLog
from .redaction import redact, REDACTED
from .order_audit import OrderAuditLog
from .agent_decision_log import AgentDecisionLog
from .events import (
    EventType, Severity, SourceKind,
    SecretLeakError, SECRET_VALUE_OMITTED,
    AuditEventInput, log_audit_event,
    build_signal_event,
    build_order_request_event,
    build_order_blocked_event,
    build_approval_decision_event,
    build_risk_block_event,
    build_feature_flag_blocked_event,
    build_emergency_stop_event,
    build_ai_proposal_event,
    build_agent_decision_event,
    build_settings_change_event,
)
from .archive import (
    AuditEventNotFoundError,
    archive_event,
    is_archived,
    list_active,
)

__all__ = [
    # base
    "AuditLog", "InMemoryAuditLog",
    "redact", "REDACTED",
    "OrderAuditLog",
    "AgentDecisionLog",
    # events
    "EventType", "Severity", "SourceKind",
    "SecretLeakError", "SECRET_VALUE_OMITTED",
    "AuditEventInput", "log_audit_event",
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
    # archive
    "AuditEventNotFoundError",
    "archive_event",
    "is_archived",
    "list_active",
]
