"""체크리스트 #64 Promotion Gate + #65 Paper Gate + #66 AI Assist Gate.

기존 #65 회귀 + 신규 #64/#66 게이트 검증.
"""
from __future__ import annotations
import pytest

from app.governance.promotion_gates import (
    GateResult,
    check_paper_gate, check_shadow_gate,
    check_manual_approval_gate, check_ai_execution_gate,
    check_reversion,
)
from app.core.modes import TradingMode


# ── #65 Paper Gate (기존 회귀) ──────────────────────────────────

def test_paper_gate_passes_clean_metrics():
    r = check_paper_gate({
        "sharpe": 1.5, "max_drawdown_pct": 8, "win_rate_pct": 55,
        "total_trades": 250, "weeks_run": 6,
    })
    assert r.passed is True
    assert r.from_mode == TradingMode.PAPER
    assert r.to_mode == TradingMode.LIVE_SHADOW


def test_paper_gate_fails_low_sharpe():
    r = check_paper_gate({
        "sharpe": 0.3, "max_drawdown_pct": 8, "win_rate_pct": 55,
        "total_trades": 250, "weeks_run": 6,
    })
    assert r.passed is False
    assert "Sharpe" in r.reason


def test_paper_gate_fails_high_mdd():
    r = check_paper_gate({
        "sharpe": 1.5, "max_drawdown_pct": 30, "win_rate_pct": 55,
        "total_trades": 250, "weeks_run": 6,
    })
    assert r.passed is False
    assert "MDD" in r.reason


def test_paper_gate_fails_insufficient_trades():
    r = check_paper_gate({
        "sharpe": 1.5, "max_drawdown_pct": 8, "win_rate_pct": 55,
        "total_trades": 50, "weeks_run": 6,
    })
    assert r.passed is False


# ── Shadow Gate ────────────────────────────────────────────────

def test_shadow_gate_passes():
    r = check_shadow_gate({
        "shadow_weeks": 3, "p95_latency_ms": 200, "failure_drills_done": 5,
    })
    assert r.passed is True
    assert r.to_mode == TradingMode.LIVE_MANUAL_APPROVAL


def test_shadow_gate_fails_high_latency():
    r = check_shadow_gate({
        "shadow_weeks": 3, "p95_latency_ms": 800, "failure_drills_done": 5,
    })
    assert r.passed is False
    assert "P95" in r.reason or "지연" in r.reason


# ── #64 / #66 — Manual Approval Gate (LIVE_MANUAL → LIVE_AI_ASSIST) ──

def test_manual_approval_gate_passes_clean():
    r = check_manual_approval_gate({
        "manual_weeks": 3, "approval_count": 150,
        "approval_p95_response_sec": 30, "rejection_rate": 0.2,
        "daily_loss_streak": 0, "compliance_fatal_count": 0,
    })
    assert r.passed is True
    assert r.from_mode == TradingMode.LIVE_MANUAL_APPROVAL
    assert r.to_mode == TradingMode.LIVE_AI_ASSIST


def test_manual_approval_gate_fails_low_count():
    r = check_manual_approval_gate({
        "manual_weeks": 3, "approval_count": 50,
        "approval_p95_response_sec": 30, "rejection_rate": 0.2,
        "daily_loss_streak": 0, "compliance_fatal_count": 0,
    })
    assert r.passed is False
    assert "승인 처리" in r.reason


def test_manual_approval_gate_fails_high_rejection_rate():
    r = check_manual_approval_gate({
        "manual_weeks": 3, "approval_count": 150,
        "approval_p95_response_sec": 30, "rejection_rate": 0.6,
        "daily_loss_streak": 0, "compliance_fatal_count": 0,
    })
    assert r.passed is False
    assert "거부율" in r.reason


def test_manual_approval_gate_fails_compliance_fatal():
    r = check_manual_approval_gate({
        "manual_weeks": 3, "approval_count": 150,
        "approval_p95_response_sec": 30, "rejection_rate": 0.2,
        "daily_loss_streak": 0, "compliance_fatal_count": 2,
    })
    assert r.passed is False
    assert "Compliance" in r.reason or "fatal" in r.reason


def test_manual_approval_gate_fails_consecutive_loss_days():
    r = check_manual_approval_gate({
        "manual_weeks": 3, "approval_count": 150,
        "approval_p95_response_sec": 30, "rejection_rate": 0.2,
        "daily_loss_streak": 5, "compliance_fatal_count": 0,
    })
    assert r.passed is False
    assert "연속 손실" in r.reason


# ── AI Execution Gate (LIVE_AI_ASSIST → LIVE_AI_EXECUTION) ──────

def test_ai_execution_gate_passes_clean():
    r = check_ai_execution_gate({
        "ai_assist_weeks": 6, "approval_count_ai": 250,
        "human_override_rate": 0.1, "ai_signal_sharpe": 1.5,
        "max_drawdown_pct": 5, "compliance_fatal_count": 0,
    })
    assert r.passed is True
    assert r.from_mode == TradingMode.LIVE_AI_ASSIST
    assert r.to_mode == TradingMode.LIVE_AI_EXECUTION


def test_ai_execution_gate_fails_high_override_rate():
    r = check_ai_execution_gate({
        "ai_assist_weeks": 6, "approval_count_ai": 250,
        "human_override_rate": 0.5, "ai_signal_sharpe": 1.5,
        "max_drawdown_pct": 5, "compliance_fatal_count": 0,
    })
    assert r.passed is False
    assert "오버라이드" in r.reason


def test_ai_execution_gate_fails_low_sharpe():
    r = check_ai_execution_gate({
        "ai_assist_weeks": 6, "approval_count_ai": 250,
        "human_override_rate": 0.1, "ai_signal_sharpe": 0.5,
        "max_drawdown_pct": 5, "compliance_fatal_count": 0,
    })
    assert r.passed is False
    assert "Sharpe" in r.reason


def test_ai_execution_gate_fails_compliance():
    r = check_ai_execution_gate({
        "ai_assist_weeks": 6, "approval_count_ai": 250,
        "human_override_rate": 0.1, "ai_signal_sharpe": 1.5,
        "max_drawdown_pct": 5, "compliance_fatal_count": 1,
    })
    assert r.passed is False


# ── 강등 ────────────────────────────────────────────────────────

def test_reversion_on_daily_loss():
    should_revert, target, reason = check_reversion(
        TradingMode.LIVE_AI_EXECUTION,
        {"daily_pnl_pct": -5.0},
    )
    assert should_revert is True
    assert target == TradingMode.LIVE_AI_ASSIST
    assert "손실" in reason


def test_reversion_on_consecutive_errors():
    should_revert, target, _ = check_reversion(
        TradingMode.LIVE_AI_ASSIST,
        {"consecutive_errors": 10},
    )
    assert should_revert is True
    assert target == TradingMode.LIVE_MANUAL_APPROVAL


def test_no_reversion_on_clean_state():
    should_revert, target, _ = check_reversion(
        TradingMode.LIVE_MANUAL_APPROVAL,
        {"daily_pnl_pct": -0.5, "consecutive_errors": 0},
    )
    assert should_revert is False
    assert target == TradingMode.LIVE_MANUAL_APPROVAL


# ── GateResult 직렬화 ──────────────────────────────────────────

def test_gate_result_metrics_preserved():
    metrics = {"sharpe": 1.5, "max_drawdown_pct": 8, "win_rate_pct": 55,
               "total_trades": 250, "weeks_run": 6}
    r = check_paper_gate(metrics)
    assert r.metrics == metrics
