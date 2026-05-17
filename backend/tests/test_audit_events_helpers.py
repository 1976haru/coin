"""체크리스트 #11 Audit Foundation 보강 — 이벤트 helper + archive 회귀.

검증 범위:
  1. build_*_event() 들이 AuditEventInput 을 반환하고 log_audit_event() 로
     기록되는지.
  2. SecretLeakError fail-closed 정책 (summary/reason/details/actor/symbol/strategy).
  3. archive_event() 가 row 를 삭제하지 않고 archived=True 만 표시하는지.
  4. 멱등 archive / 존재하지 않는 id 처리.
  5. events.py 가 broker/OrderExecutor/route_order 를 import 하지 않는지.
  6. build_agent_decision_event details 에 is_order_intent=False 가 기록되는지.
  7. build_settings_change_event 가 secret 계열 key 값을 통째로 생략하는지.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.audit import (
    AuditLog,
    AuditEventInput,
    AuditEventNotFoundError,
    EventType,
    SECRET_VALUE_OMITTED,
    SecretLeakError,
    Severity,
    SourceKind,
    archive_event,
    build_agent_decision_event,
    build_ai_proposal_event,
    build_approval_decision_event,
    build_emergency_stop_event,
    build_feature_flag_blocked_event,
    build_order_blocked_event,
    build_order_request_event,
    build_risk_block_event,
    build_settings_change_event,
    build_signal_event,
    is_archived,
    list_active,
    log_audit_event,
)


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(csv_path=str(tmp_path / "a.csv"))


# ── 1. helper 들이 AuditEventInput 을 반환하고 정상 기록되는지 ───

def test_build_signal_event_returns_input(audit: AuditLog):
    e = build_signal_event(symbol="BTC", action="BUY", strategy="mom",
                            confidence=0.7, reason="trend")
    assert isinstance(e, AuditEventInput)
    assert e.event_type == EventType.SIGNAL.value
    assert e.symbol == "BTC"
    assert e.strategy == "mom"
    rec = log_audit_event(e, audit)
    assert rec["event_type"] == "SIGNAL"
    assert audit.count() == 1


def test_build_order_request_event(audit: AuditLog):
    e = build_order_request_event(symbol="BTC", side="buy", quantity="1",
                                  mode="paper", actor="trader")
    log_audit_event(e, audit)
    rec = audit.events[-1]
    assert rec["event_type"] == "ORDER_REQUEST"
    assert rec["payload"]["mode"] == "paper"
    assert rec["payload"]["details"]["side"] == "buy"


def test_build_order_blocked_event(audit: AuditLog):
    e = build_order_blocked_event(
        symbol="BTC", blocked_by="permission_gate",
        reason="paper mode rejects live order",
    )
    log_audit_event(e, audit)
    assert audit.events[-1]["event_type"] == "ORDER_BLOCKED"
    assert audit.events[-1]["payload"]["severity"] == Severity.WARNING.value


def test_build_approval_decision_event_approved(audit: AuditLog):
    e = build_approval_decision_event(
        approval_id="ap-1", approved=True, approver="ops",
    )
    log_audit_event(e, audit)
    p = audit.events[-1]["payload"]
    assert p["details"]["approved"] is True
    assert p["details"]["approver"] == "ops"
    assert audit.events[-1]["event_type"] == "APPROVAL_DECISION"


def test_build_approval_decision_event_rejected_is_warning(audit: AuditLog):
    e = build_approval_decision_event(
        approval_id="ap-2", approved=False, approver="ops",
        reason="risk too high",
    )
    log_audit_event(e, audit)
    assert audit.events[-1]["payload"]["severity"] == Severity.WARNING.value
    assert audit.events[-1]["payload"]["details"]["approved"] is False


def test_build_risk_block_event(audit: AuditLog):
    e = build_risk_block_event(symbol="BTC",
                               reasons=["daily loss", "leverage exceeded"])
    log_audit_event(e, audit)
    p = audit.events[-1]["payload"]
    assert audit.events[-1]["event_type"] == "RISK_BLOCK"
    assert p["details"]["reasons"] == ["daily loss", "leverage exceeded"]
    assert p["source"] == SourceKind.RISK.value


def test_build_feature_flag_blocked_event(audit: AuditLog):
    e = build_feature_flag_blocked_event(
        feature_name="live_trading", reason="flag disabled",
    )
    log_audit_event(e, audit)
    p = audit.events[-1]["payload"]
    assert audit.events[-1]["event_type"] == "FEATURE_FLAG_BLOCK"
    assert p["severity"] == Severity.SECURITY.value
    assert p["details"]["feature"] == "live_trading"


def test_build_emergency_stop_event_activated_is_critical(audit: AuditLog):
    e = build_emergency_stop_event(activated=True, reason="market crash",
                                   actor="operator")
    log_audit_event(e, audit)
    p = audit.events[-1]["payload"]
    assert audit.events[-1]["event_type"] == "EMERGENCY_STOP"
    assert p["severity"] == Severity.CRITICAL.value
    assert p["details"]["activated"] is True


def test_build_ai_proposal_event_marks_is_order_intent_false(audit: AuditLog):
    e = build_ai_proposal_event(
        agent_name="signal_quality", proposal="BUY BTC", confidence=0.6,
    )
    log_audit_event(e, audit)
    p = audit.events[-1]["payload"]
    assert audit.events[-1]["event_type"] == "AI_PROPOSAL"
    assert p["details"]["is_order_intent"] is False


def test_build_agent_decision_event_carries_is_order_intent_false(audit: AuditLog):
    """CLAUDE.md §2.3 — Agent 판단은 직접 주문 아님."""
    e = build_agent_decision_event(
        agent_name="risk_officer", decision="HOLD", confidence=0.5,
        reasons=["low confidence"], symbol="BTC",
    )
    log_audit_event(e, audit)
    p = audit.events[-1]["payload"]
    assert p["details"]["is_order_intent"] is False
    assert p["details"]["decision"] == "HOLD"
    assert p["target_kind"] is None  # target_id 미지정 → None


# ── 2. SecretLeakError fail-closed 정책 ─────────────────────────

def test_secret_leak_error_on_bearer_in_summary(audit: AuditLog):
    bad = AuditEventInput(
        event_type="X",
        summary="Authorization: Bearer abcdefghijklmnop123456",  # noqa: security-scan (test fixture)
    )
    with pytest.raises(SecretLeakError):
        log_audit_event(bad, audit)


def test_secret_leak_error_on_sk_ant_key_in_reason(audit: AuditLog):
    # placeholder shape — not a real key
    bad = AuditEventInput(
        event_type="X",
        reason="leak: sk-ant-aaaaaaaaaaaaaaaaaaaaaaaa",  # noqa: security-scan (test fixture)
    )
    with pytest.raises(SecretLeakError):
        log_audit_event(bad, audit)


def test_secret_leak_error_on_secret_key_in_details(audit: AuditLog):
    bad = AuditEventInput(
        event_type="X",
        details={"upbit_api_key": "shouldfail"},
    )
    with pytest.raises(SecretLeakError):
        log_audit_event(bad, audit)


def test_secret_leak_error_on_korean_account_number(audit: AuditLog):
    # 한국 계좌번호 형태 (test fixture, not real)
    bad = AuditEventInput(event_type="X", reason="acct 12345-67-890123")
    with pytest.raises(SecretLeakError):
        log_audit_event(bad, audit)


def test_secret_leak_error_on_private_key_header(audit: AuditLog):
    # Build the PEM header at runtime to keep security_scan happy.
    pem_header = "-----" + "BEGIN RSA PRIVATE KEY" + "-----"
    bad = AuditEventInput(
        event_type="X",
        details={"k": pem_header + "\nMI..."},
    )
    with pytest.raises(SecretLeakError):
        log_audit_event(bad, audit)


def test_safe_inputs_pass_through(audit: AuditLog):
    """평범한 입력은 통과해야 한다 (false-positive 회귀)."""
    e = AuditEventInput(
        event_type="X",
        summary="user clicked approve",
        reason="ok",
        details={"symbol": "BTC", "qty": 1, "side": "buy"},
        actor="trader@example",
        symbol="BTC",
        strategy="momentum",
    )
    log_audit_event(e, audit)
    assert audit.count() == 1


# ── 3. Settings 변경: secret key 값 통째 생략 ────────────────────

def test_settings_change_secret_key_value_omitted(audit: AuditLog):
    e = build_settings_change_event(
        setting_key="broker.api_key",
        old_value="OLD-NEVER-LOG",
        new_value="NEW-NEVER-LOG",
        actor="operator",
    )
    p = e.details
    assert p["old_value"] == SECRET_VALUE_OMITTED
    assert p["new_value"] == SECRET_VALUE_OMITTED
    log_audit_event(e, audit)
    # CSV 에도 secret 가능 문자열이 들어가지 않았는지 확인
    raw = Path(audit.csv_path).read_text(encoding="utf-8-sig")
    assert "OLD-NEVER-LOG" not in raw
    assert "NEW-NEVER-LOG" not in raw


def test_settings_change_high_risk_severity():
    e = build_settings_change_event(
        setting_key="enable_live_trading",
        old_value=False, new_value=True,
        actor="operator",
    )
    assert e.severity == Severity.SECURITY.value


def test_settings_change_normal_severity_info():
    e = build_settings_change_event(
        setting_key="logging.level",
        old_value="INFO", new_value="DEBUG",
    )
    assert e.severity == Severity.INFO.value


def test_settings_change_secret_csv_no_leak(audit: AuditLog):
    """KIS / OPENAI / ANTHROPIC 등 secret-ish 키 변경도 값이 새지 않아야."""
    for key in ("kis_app_key", "kis_app_secret", "kis_account_no",
                "anthropic_api_key", "openai_api_key", "telegram_bot_token"):
        e = build_settings_change_event(
            setting_key=key, old_value="X-LEAK-X", new_value="Y-LEAK-Y",
        )
        log_audit_event(e, audit)
    raw = Path(audit.csv_path).read_text(encoding="utf-8-sig")
    assert "X-LEAK-X" not in raw
    assert "Y-LEAK-Y" not in raw


# ── 4. archive_event 정책 ───────────────────────────────────────

def test_archive_event_does_not_delete(audit: AuditLog):
    log_audit_event(build_signal_event(
        symbol="BTC", action="BUY", strategy="m", confidence=0.5,
    ), audit)
    before = len(audit.events)
    archive_event(audit, event_id=0, archived_by="ops",
                  archive_note="ticket #1")
    assert len(audit.events) == before  # row 보존
    ev = audit.events[0]
    assert ev["archived"] is True
    assert ev["archived_by"] == "ops"
    assert ev["archive_note"] == "ticket #1"
    assert is_archived(ev)


def test_archive_event_idempotent(audit: AuditLog):
    log_audit_event(build_signal_event(
        symbol="BTC", action="BUY", strategy="m", confidence=0.5,
    ), audit)
    archive_event(audit, event_id=0, archived_by="ops")
    first_at = audit.events[0]["archived_at"]
    # 다시 archive 해도 실패하지 않는다 (멱등).
    archive_event(audit, event_id=0, archived_by="someone_else",
                  archive_note="overwrite?")
    # 첫 archive 시점은 유지
    assert audit.events[0]["archived_at"] == first_at
    assert audit.events[0]["archived_by"] == "ops"


def test_archive_event_missing_id_raises(audit: AuditLog):
    with pytest.raises(AuditEventNotFoundError):
        archive_event(audit, event_id=999)


def test_archive_event_requires_identifier(audit: AuditLog):
    with pytest.raises(ValueError):
        archive_event(audit)


def test_list_active_excludes_archived(audit: AuditLog):
    log_audit_event(build_signal_event(
        symbol="BTC", action="BUY", strategy="m", confidence=0.5,
    ), audit)
    log_audit_event(build_signal_event(
        symbol="ETH", action="SELL", strategy="m", confidence=0.6,
    ), audit)
    archive_event(audit, event_id=0)
    active = list_active(audit)
    assert len(active) == 1
    assert active[0]["payload"]["symbol"] == "ETH"


# ── 5. events.py 가 금지 모듈을 import 하지 않는지 ───────────────

def test_events_module_does_not_import_broker_or_executor():
    """단일 주문 경로 우회 금지 (CLAUDE.md §2.4) — events.py 는 broker/
    OrderExecutor / route_order 어디도 import 하지 않아야 한다.
    """
    src = (Path(__file__).resolve().parents[1]
           / "app" / "audit" / "events.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = ("app.brokers", "app.execution.order_executor",
                 "route_order")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for bad in forbidden:
                assert bad not in mod, \
                    f"events.py imports forbidden module: {mod}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for bad in forbidden:
                    assert bad not in alias.name, \
                        f"events.py imports forbidden module: {alias.name}"


def test_audit_module_has_no_delete_function():
    """app/audit 어느 파일에도 delete/remove 류 API 가 없어야 한다."""
    audit_dir = Path(__file__).resolve().parents[1] / "app" / "audit"
    for p in audit_dir.glob("*.py"):
        src = p.read_text(encoding="utf-8")
        for forbidden in ("def delete_event", "def remove_event",
                          "def purge_event", "def drop_event"):
            assert forbidden not in src, (
                f"{p.name} contains forbidden delete API: {forbidden}"
            )


# ── 6. timeline 연결성 / chain_id ───────────────────────────────

def test_chain_id_propagates_across_event_chain(audit: AuditLog):
    """signal → order_request → risk_block 의 chain_id 가 일관되게 기록되는가."""
    cid = "chain-abc"
    log_audit_event(build_signal_event(
        symbol="BTC", action="BUY", strategy="m", confidence=0.8,
        chain_id=cid,
    ), audit)
    log_audit_event(build_order_request_event(
        symbol="BTC", side="buy", quantity="1", mode="paper", chain_id=cid,
    ), audit)
    log_audit_event(build_risk_block_event(
        symbol="BTC", reasons=["limit"], chain_id=cid,
    ), audit)
    chain_events = [e for e in audit.events
                    if e["payload"].get("chain_id") == cid]
    assert len(chain_events) == 3
    types = [e["event_type"] for e in chain_events]
    assert types == ["SIGNAL", "ORDER_REQUEST", "RISK_BLOCK"]


def test_agent_decision_event_links_to_target(audit: AuditLog):
    """target_id 가 있으면 target_kind 가 자동으로 채워진다."""
    e = build_agent_decision_event(
        agent_name="risk_officer", decision="WATCH_ONLY", target_id=42,
    )
    log_audit_event(e, audit)
    p = audit.events[-1]["payload"]
    assert p["target_kind"] == "AgentDecisionLog"
    assert p["target_id"] == 42


# ── 7. EventType / SourceKind 노출 점검 ─────────────────────────

def test_event_type_enum_has_required_values():
    required = {"SIGNAL", "ORDER_REQUEST", "ORDER_BLOCKED",
                "APPROVAL_DECISION", "RISK_BLOCK", "AI_PROPOSAL",
                "AGENT_DECISION", "FEATURE_FLAG_BLOCK", "EMERGENCY_STOP",
                "SETTINGS_CHANGE"}
    present = {e.value for e in EventType}
    assert required.issubset(present)
