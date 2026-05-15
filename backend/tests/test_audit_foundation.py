"""체크리스트 #11 Audit Foundation — 회귀 테스트.

검증 범위:
  1. redaction — secret/PII 키 자동 마스킹, 원본 mutate 없음
  2. AuditLog.record() — redaction이 메모리·CSV 양쪽에 반영
  3. OrderAuditLog — 주문 lifecycle 이벤트 매핑 + 검색
  4. AgentDecisionLog — AgentDecision dataclass / dict 양방향 정규화
  5. CLAUDE.md §2.3: AgentDecisionLog는 is_order_intent를 항상 기록
"""
import csv
import json
from pathlib import Path

import pytest

from app.audit import (
    AuditLog, OrderAuditLog, AgentDecisionLog,
    redact, REDACTED,
)


# ── 1. redaction ──────────────────────────────────────────────────

def test_redact_masks_api_key_field():
    payload = {"upbit_api_key": "ABC-SECRET-123", "symbol": "BTC"}
    out = redact(payload)
    assert out["upbit_api_key"] == REDACTED
    assert out["symbol"] == "BTC"


def test_redact_handles_dash_and_case_variants():
    payload = {
        "API-KEY": "x", "ApiKey": "y", "secret_key": "z",
        "PASSWORD": "p", "okx_passphrase": "q",
        "telegram_token": "t", "anthropic_api_key": "k",
        "chat_id": "12345",
    }
    out = redact(payload)
    for k in payload:
        assert out[k] == REDACTED, f"키 {k!r} 마스킹 누락"


def test_redact_does_not_mutate_original():
    original = {"api_key": "secret123", "nested": {"token": "tok456"}}
    snapshot = json.dumps(original)
    _ = redact(original)
    assert json.dumps(original) == snapshot, "redact가 원본을 변경했음"


def test_redact_recurses_into_nested_dict_and_list():
    payload = {
        "order": {"symbol": "BTC", "api_key": "k1"},
        "history": [{"secret": "s1"}, {"value": 100}],
    }
    out = redact(payload)
    assert out["order"]["api_key"] == REDACTED
    assert out["order"]["symbol"] == "BTC"
    assert out["history"][0]["secret"] == REDACTED
    assert out["history"][1]["value"] == 100


def test_redact_masks_bearer_token_in_string():
    payload = {"headers": "Authorization: Bearer abcdef.tokenvalue.xyz"}  # noqa: security-scan (fake bearer for redactor test)
    out = redact(payload)
    assert REDACTED in out["headers"]
    assert "abcdef" not in out["headers"]


def test_redact_passes_through_non_secret_payload():
    payload = {"symbol": "BTC", "side": "BUY", "notional_usdt": 50}
    out = redact(payload)
    assert out == payload


def test_redact_handles_non_dict_input():
    assert redact("hello") == "hello"
    assert redact(42) == 42
    assert redact(None) is None
    assert redact(["Bearer secret_xyz"]) == [f"Bearer {REDACTED}"]


# ── 2. AuditLog redaction 통합 ────────────────────────────────────

def test_audit_log_record_applies_redaction(tmp_path: Path):
    csv_path = tmp_path / "audit.csv"
    log = AuditLog(csv_path=str(csv_path))
    ev = log.record("ORDER_SUBMITTED", {
        "order": {"symbol": "BTC", "api_key": "leak-me"},
        "telegram_token": "tg-secret",
    })
    # 메모리
    assert ev["payload"]["telegram_token"] == REDACTED
    assert ev["payload"]["order"]["api_key"] == REDACTED
    assert ev["payload"]["order"]["symbol"] == "BTC"
    # CSV — secret 문자열이 파일에 절대 들어가지 않아야 함
    raw = csv_path.read_text(encoding="utf-8-sig")
    assert "leak-me" not in raw
    assert "tg-secret" not in raw
    assert REDACTED in raw


def test_audit_log_does_not_mutate_caller_payload(tmp_path: Path):
    log = AuditLog(csv_path=str(tmp_path / "a.csv"))
    payload = {"api_key": "abc", "symbol": "BTC"}
    log.record("X", payload)
    assert payload["api_key"] == "abc", "AuditLog가 호출자 payload를 mutate함"


# ── 3. OrderAuditLog ──────────────────────────────────────────────

def test_order_audit_records_intent_submitted_and_result(tmp_path: Path):
    base = AuditLog(csv_path=str(tmp_path / "a.csv"))
    oa = OrderAuditLog(audit=base)

    oa.record_intent({"action": "BUY", "symbol": "BTC", "confidence": 0.8})
    oa.record_submitted({"symbol": "BTC", "side": "BUY", "notional_usdt": 50,
                         "idempotency_key": "k-001"})
    oa.record_result(
        {"status": "ACCEPTED", "route": "paper",
         "result": {"status": "FILLED", "order_id": "p-1"}},
        order={"idempotency_key": "k-001", "symbol": "BTC", "side": "BUY"},
    )
    types = [e["event_type"] for e in base.events]
    assert types == ["ORDER_INTENT", "ORDER_SUBMITTED", "PAPER_ORDER_FILLED"]


def test_order_audit_maps_rejection_routes(tmp_path: Path):
    oa = OrderAuditLog(audit=AuditLog(csv_path=str(tmp_path / "a.csv")))
    oa.record_result({"status": "REJECTED", "route": "risk", "reasons": ["limit"]})
    oa.record_result({"status": "PENDING_APPROVAL", "route": "approval_queue"})
    oa.record_result({"status": "SHADOW_LOGGED", "route": "shadow"})
    oa.record_result({"status": "BLOCKED", "route": "live_not_wired"})
    oa.record_result({"status": "BLOCKED", "route": "blocked"})
    types = [e["event_type"] for e in oa.audit.events]
    assert types == [
        "ORDER_REJECTED_BY_RISK",
        "ORDER_QUEUED_FOR_APPROVAL",
        "SHADOW_SIGNAL_LOGGED",
        "LIVE_EXECUTOR_NOT_WIRED",
        "ORDER_BLOCKED_BY_PERMISSION",
    ]


def test_order_audit_lifecycle_filters_by_idempotency_key(tmp_path: Path):
    oa = OrderAuditLog(audit=AuditLog(csv_path=str(tmp_path / "a.csv")))
    oa.record_submitted({"idempotency_key": "k-A", "symbol": "BTC"})
    oa.record_submitted({"idempotency_key": "k-B", "symbol": "ETH"})
    oa.record_result(
        {"status": "ACCEPTED", "route": "paper"},
        order={"idempotency_key": "k-A"},
    )
    a_events = oa.lifecycle("k-A")
    b_events = oa.lifecycle("k-B")
    assert len(a_events) == 2
    assert len(b_events) == 1


def test_order_audit_approval_outcome(tmp_path: Path):
    oa = OrderAuditLog(audit=AuditLog(csv_path=str(tmp_path / "a.csv")))
    oa.record_approval_outcome("ap-1", approved=True)
    oa.record_approval_outcome("ap-2", approved=False, approver="admin")
    types = [e["event_type"] for e in oa.audit.events]
    assert types == ["ORDER_APPROVED", "ORDER_DENIED"]


# ── 4. AgentDecisionLog ───────────────────────────────────────────

def test_agent_decision_log_records_dataclass(tmp_path: Path):
    from app.agents.orchestrator import AgentDecision
    base = AuditLog(csv_path=str(tmp_path / "a.csv"))
    log = AgentDecisionLog(audit=base)

    decision = AgentDecision(action="BUY", confidence=0.8, reason="추세",
                             quality_score=85, risk_veto=False,
                             explain_text="강한 추세")
    ev = log.record(decision, context={"regime": "TREND_UP"})

    p = ev["payload"]
    assert p["agent_role"] == "orchestrator"
    assert p["decision"]["action"] == "BUY"
    assert p["decision"]["confidence"] == 0.8
    assert p["decision"]["is_order_intent"] is False, \
        "AgentDecision.is_order_intent 기본 False가 기록되어야 함 (CLAUDE.md §2.3)"
    assert p["context"]["regime"] == "TREND_UP"


def test_agent_decision_log_records_dict_input(tmp_path: Path):
    log = AgentDecisionLog(audit=AuditLog(csv_path=str(tmp_path / "a.csv")))
    ev = log.record({"action": "HOLD", "confidence": 0.0, "reason": "noise"})
    p = ev["payload"]["decision"]
    assert p["action"] == "HOLD"
    assert p["is_order_intent"] is False  # 누락 시 False 보장


def test_agent_decision_log_filter_by_role(tmp_path: Path):
    log = AgentDecisionLog(audit=AuditLog(csv_path=str(tmp_path / "a.csv")))
    log.record({"action": "BUY", "confidence": 0.9, "reason": ""},
               agent_role="orchestrator")
    log.record({"action": "HOLD", "confidence": 0.1, "reason": ""},
               agent_role="risk_officer")
    log.record({"action": "BUY", "confidence": 0.85, "reason": ""},
               agent_role="orchestrator")
    assert log.count() == 3
    assert len(log.filter_by_role("orchestrator")) == 2
    assert len(log.filter_by_role("risk_officer")) == 1


def test_agent_decision_log_redacts_secret_in_context(tmp_path: Path):
    """context에 실수로 secret이 들어가도 AuditLog의 redaction이 동작해야 함."""
    log = AgentDecisionLog(audit=AuditLog(csv_path=str(tmp_path / "a.csv")))
    ev = log.record(
        {"action": "BUY", "confidence": 0.9, "reason": ""},
        context={"api_key": "should-not-leak", "regime": "TREND_UP"},
    )
    assert ev["payload"]["context"]["api_key"] == REDACTED
    assert ev["payload"]["context"]["regime"] == "TREND_UP"


# ── 5. 기존 AuditLog 회귀 ─────────────────────────────────────────

def test_existing_audit_log_record_still_returns_event_dict(tmp_path: Path):
    """기존 OrderGateway 호출 패턴 호환 — record가 ev dict를 반환"""
    log = AuditLog(csv_path=str(tmp_path / "a.csv"))
    ev = log.record("PAPER_ORDER_FILLED", {"symbol": "BTC", "filled_price": 100})
    assert ev["event_type"] == "PAPER_ORDER_FILLED"
    assert ev["payload"]["symbol"] == "BTC"
    assert "ts" in ev
