"""체크리스트 #85 Integration Tests — KimpStrategy → AgentOrchestrator → OrderGateway → Audit e2e.

전체 안전 체인이 한 번의 흐름에서 제대로 작동하는지 검증.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.audit import AuditLog, OrderAuditLog, AgentDecisionLog
from app.core.config import Settings
from app.core.modes import TradingMode
from app.execution.order_gateway import OrderGateway
from app.market.freshness import check_timestamp_freshness
from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
from app.agents.orchestrator import AgentOrchestrator


def _settings(mode: TradingMode = TradingMode.PAPER) -> Settings:
    return Settings(
        trading_mode=mode,
        enable_live_trading=False,
        enable_ai_execution=False,
        admin_token="strong-not-default",
    )


def _fresh():
    return check_timestamp_freshness(datetime.now(timezone.utc), 5, label="quote")


# ── 1. 정상 경로 — 진입 신호 → Agent 통과 → Paper 체결 → Audit ──

def test_e2e_kimp_entry_paper_flow(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    gw = OrderGateway(_settings(), audit=audit)

    strategy = KimpMeanReversionStrategy(entry_threshold=-1.8, exit_threshold=-1.0)
    sig = strategy.generate_signal("BTC", 980, 1, 1000)
    assert sig.action == "OPEN_REVERSE_KIMP"

    agent = AgentOrchestrator()
    decision = agent.decide(
        {"action": sig.action, "confidence": sig.confidence, "reason": sig.reason},
        {"volume_surge": 1.5, "regime": "TREND_UP"},
    )
    # Agent 가 진입 결정에 동의 (HOLD/BLOCKED 아님)
    assert decision.action == sig.action
    assert decision.is_order_intent is False  # CLAUDE.md §2.3

    order = sig.to_order(notional_usdt=50)
    order["leverage"] = 1
    res = gw.submit(order, {"open_positions": 0}, [_fresh()])
    assert res["status"] == "ACCEPTED"
    assert res["route"] == "paper"
    # Audit 에 paper fill 기록
    types = [e["event_type"] for e in audit.events]
    assert "PAPER_ORDER_FILLED" in types


# ── 2. 안전 차단 — Strategy BLOCKED → Agent HOLD → Gateway 실행 안 됨 ──

def test_e2e_kimp_blocked_does_not_reach_executor(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    gw = OrderGateway(_settings(), audit=audit)

    # 비용 > 엣지 시나리오 — KimpStrategy 자체가 BLOCKED
    strategy = KimpMeanReversionStrategy(entry_threshold=-1.8, exit_threshold=-1.0)
    sig = strategy.generate_signal("BTC", 980, 1, 1000,
                                    upbit_spread_pct=0.02, okx_spread_pct=0.02)
    assert sig.action == "BLOCKED"

    agent = AgentOrchestrator()
    decision = agent.decide(
        {"action": sig.action, "confidence": sig.confidence, "reason": sig.reason},
        {},
    )
    # Agent 가 BLOCKED 신호를 그대로 HOLD 로 전환
    assert decision.action == "HOLD"
    assert decision.risk_veto is True

    # Gateway 에 보내지 않음 (정책 — BLOCKED 신호는 진입 의도 없음)
    types_before = [e["event_type"] for e in audit.events]
    assert "PAPER_ORDER_FILLED" not in types_before


# ── 3. Anomaly Veto — Agent 가 차단하면 진입 의도 없음 ──────────

def test_e2e_anomaly_veto_blocks_entry(tmp_path: Path):
    agent = AgentOrchestrator()
    decision = agent.decide(
        {"action": "OPEN_REVERSE_KIMP", "confidence": 0.85, "reason": "정상"},
        {"anomaly": True},
    )
    assert decision.action == "HOLD"
    assert decision.risk_veto is True
    assert decision.is_order_intent is False


# ── 4. RiskManager 한도 초과 → Gateway REJECTED ────────────────

def test_e2e_risk_manager_blocks_oversized_order(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    settings = Settings(trading_mode=TradingMode.PAPER,
                         max_order_notional_usdt=50,
                         admin_token="strong")
    gw = OrderGateway(settings, audit=audit)
    res = gw.submit(
        {"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 200, "leverage": 1},
        {"open_positions": 0},
        [_fresh()],
    )
    assert res["status"] == "REJECTED"
    assert res["route"] == "risk"


# ── 5. OrderGuard — 비정상 symbol 형식 차단 ─────────────────────

def test_e2e_order_guard_blocks_malformed_symbol(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    gw = OrderGateway(_settings(), audit=audit)
    res = gw.submit(
        {"symbol": "BTC@USDT", "side": "BUY", "notional_usdt": 50, "leverage": 1},
        {"open_positions": 0},
        [_fresh()],
    )
    assert res["status"] == "REJECTED"
    assert res["route"] == "guard"


# ── 6. AI Execution Gate — 저신뢰도 AI 주문 차단 ─────────────────

def test_e2e_ai_gate_blocks_low_confidence(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    settings = Settings(
        trading_mode=TradingMode.LIVE_AI_EXECUTION,
        enable_live_trading=True,
        enable_ai_execution=True,
        admin_token="strong",
    )
    gw = OrderGateway(settings, audit=audit)
    res = gw.submit(
        {"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 50,
         "leverage": 1, "confidence": 0.3, "quality_score": 50},
        {"open_positions": 0},
        [_fresh()],
        source="ai",
    )
    assert res["status"] == "BLOCKED"
    assert res["route"] == "ai_gate"


# ── 7. Live Shadow — 주문 송신 없이 audit 만 ────────────────────

def test_e2e_live_shadow_logs_only(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    settings = Settings(
        trading_mode=TradingMode.LIVE_SHADOW,
        enable_live_trading=False,
        admin_token="strong",
    )
    gw = OrderGateway(settings, audit=audit)
    res = gw.submit(
        {"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 50, "leverage": 1},
        {"open_positions": 0},
        [_fresh()],
    )
    assert res["status"] == "SHADOW_LOGGED"
    assert res["route"] == "shadow"
    types = [e["event_type"] for e in audit.events]
    assert "SHADOW_SIGNAL_LOGGED" in types
    assert "PAPER_ORDER_FILLED" not in types


# ── 8. Audit redaction — secret 누출 방지 (#11) ─────────────────

def test_e2e_audit_redacts_secrets(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    audit.record("SAMPLE_EVENT", {
        "okx_api_key": "should-not-leak",
        "symbol": "BTC",
        "telegram_token": "tg-secret-token",
    })
    raw_text = (tmp_path / "a.csv").read_text(encoding="utf-8-sig")
    assert "should-not-leak" not in raw_text
    assert "tg-secret-token" not in raw_text
    # 메모리도 마스킹
    assert audit.events[0]["payload"]["okx_api_key"] == "***REDACTED***"


# ── 9. Daily Report 통합 — Audit 이벤트 → Report ───────────────

def test_e2e_daily_report_aggregates_audit_events(tmp_path: Path):
    from app.agents.daily_report import DailyReportAgent

    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    OrderAuditLog(audit=audit).record_intent({"action": "BUY"})
    OrderAuditLog(audit=audit).record_result(
        {"status": "ACCEPTED", "route": "paper"},
    )
    AgentDecisionLog(audit=audit).record(
        {"action": "BUY", "confidence": 0.9, "reason": ""},
        agent_role="orchestrator",
    )

    report = DailyReportAgent().generate_report(audit)
    assert report.order_summary.intents == 1
    assert report.order_summary.filled_paper == 1
    assert report.agent_summary.total_decisions == 1


# ── 10. ComplianceAgent — fatal 0 in clean codebase ────────────

def test_e2e_compliance_no_fatal_in_clean_state():
    from app.agents.compliance import ComplianceAgent
    settings = _settings()
    report = ComplianceAgent().audit(settings=settings)
    assert report.fatal_failures == 0
