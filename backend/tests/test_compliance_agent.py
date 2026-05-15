"""체크리스트 #46 Compliance Agent — 회귀 테스트.

검증:
  1. capability + AgentBase Protocol
  2. ComplianceCheck/Report 데이터 구조
  3. 깨끗한 코드베이스 → 모든 fatal check 통과
  4. ENABLE_WITHDRAWAL 영구 false
  5. 어댑터 출금 메서드 부재
  6. redaction 동작
  7. AgentDecision 기본 is_order_intent=False
  8. RiskOfficer has_veto_power=True
  9. strategies/ 가 brokers/execution import 안 함
 10. active code 가 legacy/ccxt/pyupbit 직접 import 안 함 (어댑터 제외)
 11. frontend/src 에 secret 부재
 12. feature flag 기본 false
 13. Settings.validate() 통합
 14. render_text + decide
 15. is_order_intent=False
"""
from __future__ import annotations
from pathlib import Path

import pytest

from app.agents.compliance import (
    ComplianceAgent, ComplianceCheck, ComplianceReport,
)
from app.core.config import Settings
from app.core.modes import TradingMode


REPO_ROOT = Path(__file__).resolve().parents[2]


# ── 1. capability + Protocol ────────────────────────────────────

def test_capability_metadata():
    cap = ComplianceAgent.capability
    assert cap.name == "compliance"
    assert cap.has_veto_power is False
    assert cap.is_deterministic is True


def test_satisfies_agent_base_protocol():
    from app.agents.base import AgentBase
    assert isinstance(ComplianceAgent(), AgentBase)


# ── 2. 데이터 구조 ──────────────────────────────────────────────

def test_compliance_check_to_dict():
    c = ComplianceCheck("test", True, "fatal", "ok", "§2.1")
    d = c.to_dict()
    assert {"name", "passed", "severity", "message", "rule_ref"}.issubset(d.keys())


def test_compliance_report_properties():
    a = ComplianceAgent()
    r = a.audit()
    d = r.to_dict()
    for k in ("total", "passed", "failed", "fatal_failures",
              "warning_failures", "has_fatal", "all_passed", "checks"):
        assert k in d


def test_report_has_fatal_when_fatal_failures():
    fatal_fail = ComplianceCheck("x", False, "fatal", "msg", "§y")
    pass_check = ComplianceCheck("y", True, "fatal", "ok", "§y")
    r = ComplianceReport(
        total=2, passed=1, failed=1,
        fatal_failures=1, warning_failures=0,
        checks=(fatal_fail, pass_check),
    )
    assert r.has_fatal is True
    assert r.all_passed is False


# ── 3. 깨끗한 코드베이스 fatal 통과 ────────────────────────────

def test_clean_codebase_no_fatal_failures():
    """현재 코드베이스에서 모든 fatal check 가 통과해야 함."""
    a = ComplianceAgent()
    r = a.audit()
    fatals = [c for c in r.checks if c.severity == "fatal" and not c.passed]
    assert not fatals, f"Fatal 위반 발견: {[(c.name, c.message) for c in fatals]}"


# ── 4. ENABLE_WITHDRAWAL ────────────────────────────────────────

def test_enable_withdrawal_check_passes():
    a = ComplianceAgent()
    r = a.audit()
    chk = next(c for c in r.checks if c.name == "enable_withdrawal_permanently_false")
    assert chk.passed is True
    assert chk.severity == "fatal"
    assert chk.rule_ref == "§2.1.2"


# ── 5. 어댑터 출금 메서드 부재 ──────────────────────────────────

def test_no_withdrawal_methods_check_passes():
    a = ComplianceAgent()
    r = a.audit()
    chk = next(c for c in r.checks if c.name == "adapters_no_withdrawal_methods")
    assert chk.passed is True
    assert chk.severity == "fatal"


# ── 6. redaction ────────────────────────────────────────────────

def test_redaction_check_passes():
    a = ComplianceAgent()
    r = a.audit()
    chk = next(c for c in r.checks if c.name == "audit_redaction_active")
    assert chk.passed is True
    assert chk.severity == "fatal"


# ── 7. AgentDecision 기본값 ─────────────────────────────────────

def test_agent_decision_default_check_passes():
    a = ComplianceAgent()
    r = a.audit()
    chk = next(c for c in r.checks if c.name == "agent_decision_is_order_intent_default_false")
    assert chk.passed is True


# ── 8. RiskOfficer veto ─────────────────────────────────────────

def test_risk_officer_veto_check_passes():
    a = ComplianceAgent()
    r = a.audit()
    chk = next(c for c in r.checks if c.name == "risk_officer_has_veto_power")
    assert chk.passed is True


# ── 9. strategies/ 모듈 경계 ────────────────────────────────────

def test_strategies_module_boundary_passes():
    a = ComplianceAgent()
    r = a.audit()
    chk = next(c for c in r.checks if c.name == "strategies_module_boundary")
    assert chk.passed is True
    assert chk.severity == "fatal"


# ── 10. active code legacy import ───────────────────────────────

def test_active_code_no_legacy_imports_passes():
    a = ComplianceAgent()
    r = a.audit()
    chk = next(c for c in r.checks if c.name == "active_code_no_legacy_imports")
    assert chk.passed is True
    assert chk.severity == "fatal"


# ── 11. frontend secrets ────────────────────────────────────────

def test_frontend_no_secrets_passes():
    a = ComplianceAgent()
    r = a.audit()
    chk = next(c for c in r.checks if c.name == "frontend_no_secrets")
    assert chk.passed is True


# ── 12. feature flag 기본 false ─────────────────────────────────

def test_feature_flags_default_false_passes():
    a = ComplianceAgent()
    r = a.audit()
    chk = next(c for c in r.checks if c.name == "feature_flags_default_false")
    assert chk.passed is True
    assert chk.severity == "warning"


# ── 13. Settings.validate() 통합 ────────────────────────────────

def test_audit_with_clean_settings_no_warnings():
    a = ComplianceAgent()
    s = Settings(
        trading_mode=TradingMode.PAPER,
        admin_token="strong-not-default",
    )
    r = a.audit(settings=s)
    settings_check = next(c for c in r.checks if c.name == "settings_validate")
    assert settings_check.passed is True


def test_audit_with_default_admin_token_creates_warning():
    a = ComplianceAgent()
    s = Settings(admin_token="change-me-local-only")
    r = a.audit(settings=s)
    # settings_warning_* 중에 ADMIN 키워드 포함된 경고 있어야 함
    warnings = [c for c in r.checks if c.name.startswith("settings_warning_")]
    assert any("ADMIN_TOKEN" in c.message for c in warnings)


def test_audit_with_live_keys_in_paper_mode_warns():
    a = ComplianceAgent()
    s = Settings(
        trading_mode=TradingMode.PAPER,
        admin_token="strong",
        okx_api_key="LEAK-LIVE-KEY",
    )
    r = a.audit(settings=s)
    warnings = [c for c in r.checks if c.name.startswith("settings_warning_")]
    assert any("LIVE" in c.message for c in warnings)


def test_audit_without_settings_skips_settings_checks():
    a = ComplianceAgent()
    r = a.audit()
    # settings_validate 또는 settings_warning_* 없음
    setting_checks = [c for c in r.checks if "settings" in c.name.lower()]
    assert setting_checks == []


# ── 14. render_text ─────────────────────────────────────────────

def test_render_markdown_includes_summary():
    a = ComplianceAgent()
    r = a.audit()
    text = a.render_text(r, format="markdown")
    assert "## " in text
    assert "컴플라이언스 점검 결과" in text
    assert "Fatal" in text


def test_render_markdown_uses_green_emoji_when_all_passed():
    """깨끗한 코드베이스 + clean settings → 🟢."""
    a = ComplianceAgent()
    r = a.audit(settings=Settings(admin_token="strong"))
    text = a.render_text(r, format="markdown")
    if r.all_passed:
        assert "🟢" in text


def test_render_markdown_uses_red_emoji_when_fatal():
    """artificial fatal failure 로 🔴 emoji 검증."""
    a = ComplianceAgent()
    fatal = ComplianceCheck("test_fatal", False, "fatal", "msg", "§x")
    r = ComplianceReport(
        total=1, passed=0, failed=1, fatal_failures=1, warning_failures=0,
        checks=(fatal,),
    )
    text = a.render_text(r, format="markdown")
    assert "🔴" in text


def test_render_plain_format():
    a = ComplianceAgent()
    r = a.audit()
    text = a.render_text(r, format="plain")
    assert "컴플라이언스 점검" in text


# ── 15. AgentBase decide ────────────────────────────────────────

def test_decide_returns_hold_with_compliance_text():
    a = ComplianceAgent()
    d = a.decide({}, {})
    assert d.action == "HOLD"
    assert "컴플라이언스" in d.explain_text


def test_decide_with_settings_runs_settings_checks():
    a = ComplianceAgent()
    s = Settings(admin_token="change-me-local-only")
    d = a.decide({}, {"settings": s})
    assert "ADMIN_TOKEN" in d.explain_text or "위반" in d.reason


# ── 16. is_order_intent=False ──────────────────────────────────

def test_decision_is_order_intent_false():
    a = ComplianceAgent()
    d = a.decide({}, {})
    assert d.is_order_intent is False


# ── 17. e2e 시나리오 ────────────────────────────────────────────

def test_e2e_full_audit_returns_report_with_correct_counts():
    a = ComplianceAgent()
    r = a.audit()
    # 9개 표준 체크 (settings 없을 때)
    assert r.total >= 8
    assert r.passed + r.failed == r.total


def test_audit_uses_explicit_repo_root():
    """custom repo_root 가 적용되는지 — 빈 디렉토리에서는 일부 path 검사 skip."""
    import tempfile
    a = ComplianceAgent()
    with tempfile.TemporaryDirectory() as tmp:
        r = a.audit(repo_root=Path(tmp))
        # strategies / app / frontend 부재 — 해당 path 검사는 skip 메시지로 통과
        # 그래도 enable_withdrawal / redaction / agent_decision 같은 코드 기반 검사는 통과
        # fatal 위반 0
        fatals = [c for c in r.checks
                  if c.severity == "fatal" and not c.passed]
        assert not fatals
