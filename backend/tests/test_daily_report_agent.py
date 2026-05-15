"""체크리스트 #42 Daily Report Agent — 회귀 테스트.

검증:
  1. capability + AgentBase Protocol
  2. 빈 audit → 0 카운트 리포트
  3. 주문 이벤트 카테고리 매핑 (intent/submitted/filled/rejected/blocked/pending/shadow)
  4. AGENT_DECISION 집계 (by_role/by_action/veto/watch_only)
  5. key_events 필터 (kill_switch/BLOCKED 등)
  6. 시간 범위 필터링 (since/until)
  7. render_text — markdown / plain
  8. AgentBase decide — explain_text 에 리포트
  9. AuditLog 통합 e2e
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.agents.daily_report import (
    DailyReportAgent, DailyReport, OrderSummary, AgentSummary,
)
from app.audit import AuditLog, OrderAuditLog, AgentDecisionLog
from app.agents.orchestrator import AgentDecision


# ── 1. Capability + Protocol ────────────────────────────────────

def test_capability_metadata():
    cap = DailyReportAgent.capability
    assert cap.name == "daily_report"
    assert cap.role == "daily_report"
    assert cap.has_veto_power is False
    assert cap.is_deterministic is True


def test_satisfies_agent_base_protocol():
    from app.agents.base import AgentBase
    assert isinstance(DailyReportAgent(), AgentBase)


# ── 2. 빈 audit ─────────────────────────────────────────────────

def test_empty_audit_returns_zero_counts(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    a = DailyReportAgent()
    r = a.generate_report(audit)
    assert r.total_events == 0
    assert r.order_summary.submitted == 0
    assert r.agent_summary.total_decisions == 0
    assert r.key_events == ()


# ── 3. 주문 이벤트 카테고리 ─────────────────────────────────────

def test_order_intent_counted(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    oa = OrderAuditLog(audit=audit)
    oa.record_intent({"action": "BUY"})
    r = DailyReportAgent().generate_report(audit)
    assert r.order_summary.intents == 1


def test_paper_filled_counted(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    oa = OrderAuditLog(audit=audit)
    oa.record_result(
        {"status": "ACCEPTED", "route": "paper",
         "result": {"status": "FILLED"}},
    )
    r = DailyReportAgent().generate_report(audit)
    assert r.order_summary.filled_paper == 1


def test_rejected_by_risk_counted(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    oa = OrderAuditLog(audit=audit)
    oa.record_result({"status": "REJECTED", "route": "risk"})
    r = DailyReportAgent().generate_report(audit)
    assert r.order_summary.rejected == 1


def test_blocked_counted(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    oa = OrderAuditLog(audit=audit)
    oa.record_result({"status": "BLOCKED", "route": "live_not_wired"})
    r = DailyReportAgent().generate_report(audit)
    assert r.order_summary.blocked == 1


def test_pending_approval_counted(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    oa = OrderAuditLog(audit=audit)
    oa.record_result({"status": "PENDING_APPROVAL", "route": "approval_queue"})
    r = DailyReportAgent().generate_report(audit)
    assert r.order_summary.pending_approval == 1


def test_shadow_logged_counted(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    oa = OrderAuditLog(audit=audit)
    oa.record_result({"status": "SHADOW_LOGGED", "route": "shadow"})
    r = DailyReportAgent().generate_report(audit)
    assert r.order_summary.shadow_logged == 1


def test_multiple_categories_aggregate_correctly(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    oa = OrderAuditLog(audit=audit)
    oa.record_intent({})
    oa.record_intent({})
    oa.record_result({"status": "ACCEPTED", "route": "paper"})
    oa.record_result({"status": "REJECTED", "route": "risk"})
    oa.record_result({"status": "BLOCKED", "route": "live_not_wired"})

    r = DailyReportAgent().generate_report(audit)
    assert r.order_summary.intents == 2
    assert r.order_summary.filled_paper == 1
    assert r.order_summary.rejected == 1
    assert r.order_summary.blocked == 1


# ── 4. AGENT_DECISION 집계 ──────────────────────────────────────

def test_agent_decision_counted_by_role(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    log = AgentDecisionLog(audit=audit)
    log.record({"action": "BUY", "confidence": 0.9, "reason": ""},
                agent_role="orchestrator")
    log.record({"action": "HOLD", "confidence": 0.0, "reason": ""},
                agent_role="risk_officer")
    log.record({"action": "BUY", "confidence": 0.85, "reason": ""},
                agent_role="orchestrator")

    r = DailyReportAgent().generate_report(audit)
    assert r.agent_summary.total_decisions == 3
    assert r.agent_summary.by_role["orchestrator"] == 2
    assert r.agent_summary.by_role["risk_officer"] == 1


def test_agent_decision_counted_by_action(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    log = AgentDecisionLog(audit=audit)
    log.record({"action": "BUY", "confidence": 0.9, "reason": ""})
    log.record({"action": "BUY", "confidence": 0.85, "reason": ""})
    log.record({"action": "HOLD", "confidence": 0.0, "reason": ""})

    r = DailyReportAgent().generate_report(audit)
    assert r.agent_summary.by_action["BUY"] == 2
    assert r.agent_summary.by_action["HOLD"] == 1


def test_agent_decision_veto_counted(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    log = AgentDecisionLog(audit=audit)
    log.record(AgentDecision("HOLD", 0.0, "Kill Switch",
                              risk_veto=True))
    log.record(AgentDecision("BUY", 0.85, "추세", risk_veto=False))

    r = DailyReportAgent().generate_report(audit)
    assert r.agent_summary.veto_count == 1


def test_agent_decision_watch_only_counted(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    log = AgentDecisionLog(audit=audit)
    log.record({"action": "WATCH_ONLY", "confidence": 0.3, "reason": ""})

    r = DailyReportAgent().generate_report(audit)
    assert r.agent_summary.watch_only_count == 1
    assert r.agent_summary.by_action["WATCH_ONLY"] == 1


# ── 5. key_events 필터 ──────────────────────────────────────────

def test_key_events_includes_kill_switch(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    audit.record("KILL_SWITCH_ACTIVATED", {"reason": "test"})
    r = DailyReportAgent().generate_report(audit)
    assert len(r.key_events) == 1
    assert r.key_events[0]["event_type"] == "KILL_SWITCH_ACTIVATED"


def test_key_events_excludes_routine_events(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    audit.record("PAPER_ORDER_FILLED", {})
    audit.record("ORDER_INTENT", {})
    r = DailyReportAgent().generate_report(audit)
    assert r.key_events == ()


def test_key_events_includes_blocked_orders(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    audit.record("ORDER_BLOCKED_BY_PERMISSION", {})
    audit.record("ORDER_REJECTED_BY_RISK", {})
    r = DailyReportAgent().generate_report(audit)
    assert len(r.key_events) == 2


# ── 6. 시간 범위 필터 ───────────────────────────────────────────

def test_filter_excludes_events_before_since(tmp_path: Path, monkeypatch):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    # 옛날 이벤트
    audit.events.append({
        "ts": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        "event_type": "PAPER_ORDER_FILLED",
        "payload": {},
    })
    # 오늘 이벤트
    audit.record("PAPER_ORDER_FILLED", {})

    a = DailyReportAgent()
    r = a.generate_report(audit)  # 기본 since=오늘 자정
    # 옛날 건 제외
    assert r.order_summary.filled_paper == 1


def test_explicit_since_until_filter(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    base = datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc)
    for i in range(5):
        audit.events.append({
            "ts": (base + timedelta(days=i)).isoformat(),
            "event_type": "PAPER_ORDER_FILLED",
            "payload": {},
        })
    a = DailyReportAgent()
    r = a.generate_report(
        audit,
        since=base + timedelta(days=1),
        until=base + timedelta(days=3),
    )
    # day 1, 2, 3 → 3건
    assert r.total_events == 3
    assert r.order_summary.filled_paper == 3


# ── 7. render_text ──────────────────────────────────────────────

def test_render_markdown_includes_sections(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    OrderAuditLog(audit=audit).record_result(
        {"status": "ACCEPTED", "route": "paper"},
    )
    a = DailyReportAgent()
    r = a.generate_report(audit)
    text = a.render_text(r, format="markdown")
    assert "## 일일 거래 리포트" in text
    assert "### 주문 요약" in text
    assert "### Agent 결정 요약" in text


def test_render_markdown_lists_action_breakdown(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    log = AgentDecisionLog(audit=audit)
    log.record({"action": "BUY", "confidence": 0.9, "reason": ""})
    log.record({"action": "HOLD", "confidence": 0.0, "reason": ""})

    a = DailyReportAgent()
    r = a.generate_report(audit)
    text = a.render_text(r, format="markdown")
    assert "BUY: 1" in text
    assert "HOLD: 1" in text


def test_render_plain_includes_summary(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    OrderAuditLog(audit=audit).record_result(
        {"status": "ACCEPTED", "route": "paper"},
    )
    a = DailyReportAgent()
    r = a.generate_report(audit)
    text = a.render_text(r, format="plain")
    assert "일일 거래 리포트" in text
    assert "주문 요약" in text


# ── 8. AgentBase decide ─────────────────────────────────────────

def test_decide_with_audit_log_returns_report_in_explain_text(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    OrderAuditLog(audit=audit).record_intent({"action": "BUY"})
    a = DailyReportAgent()
    d = a.decide({}, {"audit_log": audit})
    assert d.action == "HOLD"
    assert d.is_order_intent is False
    assert "일일 거래 리포트" in d.explain_text or "주문" in d.explain_text


def test_decide_without_audit_log_returns_error_message():
    a = DailyReportAgent()
    d = a.decide({}, {})
    assert "미제공" in d.reason
    assert "audit_log" in d.reason


# ── 9. DailyReport 직렬화 ───────────────────────────────────────

def test_report_to_dict_structure(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    OrderAuditLog(audit=audit).record_intent({})
    a = DailyReportAgent()
    r = a.generate_report(audit)
    d = r.to_dict()
    for k in ("since", "until", "total_events", "order_summary",
              "agent_summary", "key_events"):
        assert k in d
    assert isinstance(d["order_summary"], dict)
    assert isinstance(d["agent_summary"]["by_role"], dict)


# ── 10. e2e — 종합 시나리오 ────────────────────────────────────

def test_e2e_full_day_simulation(tmp_path: Path):
    """하루 시나리오: 의도 5건 → 체결 3건, 거부 1건, 차단 1건. Agent 결정 7건."""
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    oa = OrderAuditLog(audit=audit)
    log = AgentDecisionLog(audit=audit)

    # 의도 5건
    for _ in range(5):
        oa.record_intent({"action": "BUY"})

    # 체결 3건
    for _ in range(3):
        oa.record_result({"status": "ACCEPTED", "route": "paper"})

    # 거부 1건
    oa.record_result({"status": "REJECTED", "route": "risk"})

    # 차단 2건 — Permission 차단 + LIVE not wired
    oa.record_result({"status": "BLOCKED", "route": "blocked"})  # ORDER_BLOCKED_BY_PERMISSION
    oa.record_result({"status": "BLOCKED", "route": "live_not_wired"})  # LIVE_EXECUTOR_NOT_WIRED

    # Agent 결정 7건
    log.record({"action": "BUY", "confidence": 0.9, "reason": ""},
                agent_role="orchestrator")
    log.record({"action": "BUY", "confidence": 0.85, "reason": ""},
                agent_role="orchestrator")
    log.record({"action": "HOLD", "confidence": 0.0, "reason": "Kill Switch"},
                agent_role="risk_officer")
    log.record({"action": "WATCH_ONLY", "confidence": 0.2, "reason": ""},
                agent_role="risk_officer")
    log.record({"action": "HOLD", "confidence": 0.0, "reason": "anomaly"},
                agent_role="anomaly")
    log.record({"action": "BUY", "confidence": 0.85, "reason": ""},
                agent_role="orchestrator")
    log.record({"action": "HOLD", "confidence": 0.0, "reason": "low quality"},
                agent_role="signal_quality")

    # Kill switch 이벤트
    audit.record("KILL_SWITCH_ACTIVATED", {})

    a = DailyReportAgent()
    r = a.generate_report(audit)

    assert r.order_summary.intents == 5
    assert r.order_summary.filled_paper == 3
    assert r.order_summary.rejected == 1
    assert r.order_summary.blocked == 2  # ORDER_BLOCKED_BY_PERMISSION + LIVE_NOT_WIRED 둘 다 'blocked'
    assert r.agent_summary.total_decisions == 7
    assert r.agent_summary.by_role["orchestrator"] == 3
    assert r.agent_summary.watch_only_count == 1
    assert any(ev["event_type"] == "KILL_SWITCH_ACTIVATED" for ev in r.key_events)


# ── 11. AuditLog 통합 — events 속성 부재 처리 ──────────────────

def test_handles_audit_without_events_attr_gracefully():
    """events 속성이 없는 객체도 빈 리포트로 처리."""

    class NoEvents:
        pass

    a = DailyReportAgent()
    r = a.generate_report(NoEvents())
    assert r.total_events == 0


# ── 12. is_order_intent=False 보장 ──────────────────────────────

def test_decide_is_order_intent_false(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    a = DailyReportAgent()
    d = a.decide({}, {"audit_log": audit})
    assert d.is_order_intent is False
