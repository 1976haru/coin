"""체크리스트 #58 AI Assist + #59 AI Execution Gate — 회귀 테스트.

검증:
  1. ApprovalItem 에 source / agent_explain 필드
  2. ApprovalQueue.add 가 source 패스스루
  3. ApprovalQueue.pending_by_source 필터
  4. AIExecutionGate — confidence/quality/daily/cooldown 임계값
  5. AIExecutionGate.record_executed 카운터/쿨다운 갱신
  6. AIExecutionGate.status 상태 보고
  7. OrderGateway 통합:
     - LIVE_AI_ASSIST 모드 + source='ai' → ApprovalQueue 에 source='ai' 기록
     - LIVE_AI_EXECUTION + source='ai' + 신뢰도 부족 → BLOCKED ai_gate
     - LIVE_AI_EXECUTION + 모든 통과 → live executor (현재 not_wired)
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.execution.approval_queue import ApprovalQueue, ApprovalItem
from app.risk.ai_execution_gate import AIExecutionGate, AIGateResult


# ── 1. ApprovalItem 신규 필드 ───────────────────────────────────

def test_approval_item_has_source_default_system():
    item = ApprovalItem(
        id="x", order={}, reason="r",
        created_at="2026-05-10T00:00:00+00:00",
        expires_at="2026-05-10T00:05:00+00:00",
    )
    assert item.source == "system"
    assert item.agent_explain == ""


def test_approval_item_source_persists():
    item = ApprovalItem(
        id="x", order={}, reason="r",
        created_at="2026-05-10T00:00:00+00:00",
        expires_at="2026-05-10T00:05:00+00:00",
        source="ai",
        agent_explain="AI suggested due to TREND_UP",
    )
    assert item.source == "ai"
    assert "TREND_UP" in item.agent_explain


# ── 2. ApprovalQueue.add 패스스루 ───────────────────────────────

def test_queue_add_with_source_ai():
    q = ApprovalQueue()
    item = q.add({"symbol": "BTC", "side": "BUY"}, "수동 승인",
                  source="ai", agent_explain="AI: 추세 정상")
    assert item.source == "ai"
    assert item.agent_explain == "AI: 추세 정상"


def test_queue_add_default_source_system():
    q = ApprovalQueue()
    item = q.add({"symbol": "BTC", "side": "BUY"}, "수동 승인")
    assert item.source == "system"


# ── 3. ApprovalQueue.pending_by_source ──────────────────────────

def test_pending_by_source_filters():
    q = ApprovalQueue()
    q.add({"symbol": "BTC"}, "r1", source="ai")
    q.add({"symbol": "ETH"}, "r2", source="manual")
    q.add({"symbol": "SOL"}, "r3", source="ai")

    ai_items = q.pending_by_source("ai")
    manual_items = q.pending_by_source("manual")
    assert len(ai_items) == 2
    assert len(manual_items) == 1


def test_pending_by_source_excludes_non_pending():
    q = ApprovalQueue()
    item = q.add({"symbol": "BTC"}, "r", source="ai")
    q.decide(item.id, approved=True)  # status=APPROVED → 더 이상 pending 아님
    assert q.pending_by_source("ai") == []


# ── 4. AIExecutionGate 임계값 ───────────────────────────────────

def test_ai_gate_passes_clean_order():
    g = AIExecutionGate()
    r = g.check({
        "symbol": "BTC", "side": "BUY",
        "confidence": 0.85, "quality_score": 90,
    })
    assert r.allowed is True
    assert r.reasons == ()


def test_ai_gate_blocks_low_confidence():
    g = AIExecutionGate(min_confidence=0.75)
    r = g.check({
        "symbol": "BTC", "side": "BUY",
        "confidence": 0.5, "quality_score": 90,
    })
    assert r.allowed is False
    assert any("신뢰도" in reason for reason in r.reasons)


def test_ai_gate_blocks_low_quality_score():
    g = AIExecutionGate(min_quality_score=80)
    r = g.check({
        "symbol": "BTC", "side": "BUY",
        "confidence": 0.9, "quality_score": 60,
    })
    assert r.allowed is False
    assert any("품질" in reason for reason in r.reasons)


def test_ai_gate_skips_quality_check_when_field_absent():
    """quality_score 가 없으면 그 검사 건너뜀."""
    g = AIExecutionGate(min_quality_score=80)
    r = g.check({"symbol": "BTC", "side": "BUY", "confidence": 0.9})
    assert r.allowed is True


# ── 5. 일일 한도 / 쿨다운 ───────────────────────────────────────

def test_ai_gate_daily_limit():
    fake_clock = [1000.0]
    def time_fn(): return fake_clock[0]
    g = AIExecutionGate(max_daily_orders=2, time_fn=time_fn)
    g.record_executed({"symbol": "BTC"})
    g.record_executed({"symbol": "ETH"})
    fake_clock[0] += 1.0  # 잠깐 후
    r = g.check({"symbol": "SOL", "side": "BUY", "confidence": 0.9})
    assert r.allowed is False
    assert any("일일" in reason for reason in r.reasons)


def test_ai_gate_daily_window_resets():
    """24h 후 카운터 리셋."""
    fake_clock = [1000.0]
    def time_fn(): return fake_clock[0]
    g = AIExecutionGate(max_daily_orders=1, time_fn=time_fn)
    g.record_executed({"symbol": "BTC"})
    # 24h + 1초 후
    fake_clock[0] += 24 * 3600 + 1
    r = g.check({"symbol": "ETH", "side": "BUY", "confidence": 0.9})
    assert r.allowed is True


def test_ai_gate_per_symbol_cooldown():
    fake_clock = [1000.0]
    def time_fn(): return fake_clock[0]
    g = AIExecutionGate(per_symbol_cooldown_sec=60, time_fn=time_fn)
    g.record_executed({"symbol": "BTC"})
    # 30초 후 같은 심볼 재실행 시도
    fake_clock[0] += 30
    r = g.check({"symbol": "BTC", "side": "BUY", "confidence": 0.9})
    assert r.allowed is False
    assert any("쿨다운" in reason for reason in r.reasons)


def test_ai_gate_cooldown_expires():
    fake_clock = [1000.0]
    def time_fn(): return fake_clock[0]
    g = AIExecutionGate(per_symbol_cooldown_sec=60, time_fn=time_fn)
    g.record_executed({"symbol": "BTC"})
    fake_clock[0] += 61   # 쿨다운 만료
    r = g.check({"symbol": "BTC", "side": "BUY", "confidence": 0.9})
    assert r.allowed is True


def test_ai_gate_different_symbols_independent_cooldown():
    fake_clock = [1000.0]
    def time_fn(): return fake_clock[0]
    g = AIExecutionGate(per_symbol_cooldown_sec=60, time_fn=time_fn)
    g.record_executed({"symbol": "BTC"})
    r = g.check({"symbol": "ETH", "side": "BUY", "confidence": 0.9})
    assert r.allowed is True


# ── 6. AIExecutionGate.status ───────────────────────────────────

def test_status_reports_state():
    g = AIExecutionGate(max_daily_orders=10)
    g.record_executed({"symbol": "BTC"})
    g.record_executed({"symbol": "ETH"})
    s = g.status
    assert s["daily_count"] == 2
    assert s["max_daily_orders"] == 10
    assert "BTC" in s["tracked_symbols"]
    assert "ETH" in s["tracked_symbols"]


# ── 7. 잘못된 인자 ──────────────────────────────────────────────

def test_invalid_min_confidence_raises():
    with pytest.raises(ValueError):
        AIExecutionGate(min_confidence=1.5)
    with pytest.raises(ValueError):
        AIExecutionGate(min_confidence=-0.1)


def test_invalid_min_quality_raises():
    with pytest.raises(ValueError):
        AIExecutionGate(min_quality_score=200)


def test_invalid_max_daily_raises():
    with pytest.raises(ValueError):
        AIExecutionGate(max_daily_orders=-1)


# ── 8. OrderGateway 통합 — LIVE_AI_ASSIST + source='ai' ─────────

def test_gateway_ai_assist_routes_to_approval_with_source(tmp_path: Path):
    from app.core.config import Settings
    from app.core.modes import TradingMode
    from app.execution.order_gateway import OrderGateway
    from app.market.freshness import check_timestamp_freshness
    from app.audit.audit_log import AuditLog

    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    settings = Settings(
        trading_mode=TradingMode.LIVE_AI_ASSIST,
        enable_live_trading=True,
    )
    gw = OrderGateway(settings, audit=audit)
    fresh = check_timestamp_freshness(datetime.now(timezone.utc), 5, label="quote")
    res = gw.submit(
        {"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 50,
         "leverage": 1, "agent_explain": "AI 추세"},
        {"open_positions": 0},
        [fresh],
        source="ai",
    )
    assert res["status"] == "PENDING_APPROVAL"
    # ApprovalQueue 에 source='ai' 로 기록되었는지
    pendings = gw.approvals.pending_by_source("ai")
    assert len(pendings) == 1
    assert "AI" in pendings[0].agent_explain or "추세" in pendings[0].agent_explain


# ── 9. OrderGateway 통합 — LIVE_AI_EXECUTION + AI gate ─────────

def test_gateway_ai_execution_blocks_low_confidence(tmp_path: Path):
    from app.core.config import Settings
    from app.core.modes import TradingMode
    from app.execution.order_gateway import OrderGateway
    from app.market.freshness import check_timestamp_freshness
    from app.audit.audit_log import AuditLog

    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    settings = Settings(
        trading_mode=TradingMode.LIVE_AI_EXECUTION,
        enable_live_trading=True,
        enable_ai_execution=True,
    )
    gw = OrderGateway(settings, audit=audit)
    fresh = check_timestamp_freshness(datetime.now(timezone.utc), 5, label="quote")
    res = gw.submit(
        {"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 50,
         "leverage": 1, "confidence": 0.3, "quality_score": 50},
        {"open_positions": 0},
        [fresh],
        source="ai",
    )
    assert res["status"] == "BLOCKED"
    assert res["route"] == "ai_gate"
    types = [e["event_type"] for e in audit.events]
    assert "ORDER_BLOCKED_BY_AI_GATE" in types


def test_gateway_ai_execution_passes_high_confidence_then_live_not_wired(tmp_path: Path):
    """AI gate 통과 후 LiveExecutor 는 not_wired 로 차단."""
    from app.core.config import Settings
    from app.core.modes import TradingMode
    from app.execution.order_gateway import OrderGateway
    from app.market.freshness import check_timestamp_freshness
    from app.audit.audit_log import AuditLog

    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    settings = Settings(
        trading_mode=TradingMode.LIVE_AI_EXECUTION,
        enable_live_trading=True,
        enable_ai_execution=True,
    )
    gw = OrderGateway(settings, audit=audit)
    fresh = check_timestamp_freshness(datetime.now(timezone.utc), 5, label="quote")
    res = gw.submit(
        {"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 50,
         "leverage": 1, "confidence": 0.85, "quality_score": 90},
        {"open_positions": 0},
        [fresh],
        source="ai",
    )
    # AI gate 통과 → live executor 호출 → not wired
    assert res["status"] == "BLOCKED"
    assert res["route"] == "live_not_wired"


def test_gateway_non_ai_source_skips_ai_gate(tmp_path: Path):
    """source='manual' 은 AI gate 무관하게 통과."""
    from app.core.config import Settings
    from app.core.modes import TradingMode
    from app.execution.order_gateway import OrderGateway
    from app.market.freshness import check_timestamp_freshness

    settings = Settings(
        trading_mode=TradingMode.LIVE_AI_EXECUTION,
        enable_live_trading=True,
        enable_ai_execution=True,
    )
    gw = OrderGateway(settings)
    fresh = check_timestamp_freshness(datetime.now(timezone.utc), 5, label="quote")
    # confidence 0 인데 source='manual' → AI gate 안 거침 → live not wired
    res = gw.submit(
        {"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 50,
         "leverage": 1, "confidence": 0.0},
        {"open_positions": 0},
        [fresh],
        source="manual",
    )
    assert res["route"] == "live_not_wired"
