"""체크리스트 #54 OrderExecutor + #57 Live Shadow — 회귀 테스트.

검증:
  1. ExecutorResult / OrderExecutor Protocol
  2. PaperExecutor — PaperBroker 호출 + 결과 매핑
  3. ShadowExecutor — 주문 송신 없이 audit 만
  4. LiveExecutor — 항상 BLOCKED + LIVE_EXECUTOR_NOT_WIRED 이벤트
  5. OrderGateway 가 route 별로 위임
  6. shadow route audit 로그
  7. live route 차단
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.execution.order_executor import (
    OrderExecutor, ExecutorResult,
    PaperExecutor, ShadowExecutor, LiveExecutor,
)
from app.audit.audit_log import AuditLog
from app.brokers.paper_broker import PaperBroker


# ── 1. Protocol ─────────────────────────────────────────────────

def test_executor_result_default_fields():
    r = ExecutorResult(status="FILLED", route="paper")
    assert r.reason == ""
    assert r.result == {}
    assert r.audit_event == {}


def test_paper_executor_satisfies_protocol():
    e = PaperExecutor()
    assert isinstance(e, OrderExecutor)
    assert e.name == "paper"


def test_shadow_executor_satisfies_protocol():
    e = ShadowExecutor()
    assert isinstance(e, OrderExecutor)
    assert e.name == "shadow"


def test_live_executor_satisfies_protocol():
    e = LiveExecutor()
    assert isinstance(e, OrderExecutor)
    assert e.name == "live"


# ── 2. PaperExecutor ────────────────────────────────────────────

def test_paper_executor_calls_broker_and_returns_filled(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    broker = PaperBroker(fill_chance=1.0)
    e = PaperExecutor(broker=broker)
    r = e.execute(
        {"symbol": "BTC/USDT", "side": "BUY",
         "notional_usdt": 50, "price": 100000},
        audit_log=audit,
    )
    assert r.status == "ACCEPTED"
    assert r.route == "paper"
    assert r.result.get("status") == "FILLED"
    # Audit 이벤트 기록됨
    assert any(e["event_type"] == "PAPER_ORDER_FILLED" for e in audit.events)


def test_paper_executor_returns_accepted_even_on_timeout(tmp_path: Path):
    """OrderGateway 외부 status 는 ACCEPTED, broker raw status 는 result 안에."""
    broker = PaperBroker(fill_chance=0.0)  # 항상 미체결
    e = PaperExecutor(broker=broker)
    r = e.execute(
        {"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 50, "price": 100},
        audit_log=AuditLog(csv_path=str(tmp_path / "a.csv")),
    )
    # gateway-level status 는 ACCEPTED, broker status 는 TIMEOUT
    assert r.status == "ACCEPTED"
    assert r.result.get("status") == "TIMEOUT"


# ── 3. ShadowExecutor ───────────────────────────────────────────

def test_shadow_executor_returns_shadow_logged(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    e = ShadowExecutor()
    r = e.execute(
        {"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 50},
        audit_log=audit,
    )
    assert r.status == "SHADOW_LOGGED"
    assert r.route == "shadow"
    assert "shadow" in r.reason.lower()


def test_shadow_executor_does_not_call_broker(tmp_path: Path):
    """Shadow 는 어떤 broker 도 호출하지 않음 — audit 만."""
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    e = ShadowExecutor()
    e.execute({"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 50},
              audit_log=audit)
    types = [ev["event_type"] for ev in audit.events]
    assert "SHADOW_SIGNAL_LOGGED" in types
    assert "PAPER_ORDER_FILLED" not in types


def test_shadow_executor_works_without_audit():
    """audit_log=None 이어도 동작 — audit_event 는 빈 dict."""
    r = ShadowExecutor().execute(
        {"symbol": "BTC", "side": "BUY", "notional_usdt": 50},
    )
    assert r.status == "SHADOW_LOGGED"
    assert r.audit_event == {}


# ── 4. LiveExecutor ─────────────────────────────────────────────

def test_live_executor_always_blocks(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    e = LiveExecutor()
    r = e.execute({"symbol": "BTC", "side": "BUY", "notional_usdt": 50},
                   audit_log=audit)
    assert r.status == "BLOCKED"
    assert r.route == "live_not_wired"
    assert "not wired" in r.reason or "검증" in r.reason


def test_live_executor_records_not_wired_event(tmp_path: Path):
    audit = AuditLog(csv_path=str(tmp_path / "a.csv"))
    e = LiveExecutor()
    e.execute({"symbol": "BTC", "side": "BUY", "notional_usdt": 50},
              audit_log=audit)
    types = [ev["event_type"] for ev in audit.events]
    assert "LIVE_EXECUTOR_NOT_WIRED" in types


# ── 5. OrderGateway 통합 ────────────────────────────────────────

def test_gateway_paper_route_uses_paper_executor():
    """PAPER 모드 + 정상 주문 → paper 실행기 호출."""
    from app.core.config import Settings
    from app.core.modes import TradingMode
    from app.execution.order_gateway import OrderGateway
    from app.market.freshness import check_timestamp_freshness
    settings = Settings(trading_mode=TradingMode.PAPER)
    gw = OrderGateway(settings)
    fresh = check_timestamp_freshness(datetime.now(timezone.utc), 5, label="quote")
    res = gw.submit(
        {"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 50, "leverage": 1},
        {"open_positions": 0},
        [fresh],
    )
    assert res["status"] == "ACCEPTED"
    assert res["route"] == "paper"
    assert "result" in res


def test_gateway_shadow_route_uses_shadow_executor():
    """LIVE_SHADOW 모드 → shadow 실행기 호출 + SHADOW_SIGNAL_LOGGED 이벤트."""
    from app.core.config import Settings
    from app.core.modes import TradingMode
    from app.execution.order_gateway import OrderGateway
    from app.market.freshness import check_timestamp_freshness
    from app.audit.audit_log import AuditLog

    audit = AuditLog(csv_path=":memory:") if False else AuditLog()
    settings = Settings(trading_mode=TradingMode.LIVE_SHADOW,
                         enable_live_trading=False)
    gw = OrderGateway(settings, audit=audit)
    fresh = check_timestamp_freshness(datetime.now(timezone.utc), 5, label="quote")
    res = gw.submit(
        {"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 10, "leverage": 1},
        {"open_positions": 0},
        [fresh],
    )
    assert res["status"] == "SHADOW_LOGGED"
    assert res["route"] == "shadow"
    types = [ev["event_type"] for ev in audit.events]
    assert "SHADOW_SIGNAL_LOGGED" in types


def test_gateway_executors_dict_can_be_replaced():
    """OrderGateway.executors 가 외부 주입 가능 (테스트 mock)."""
    from app.core.config import Settings
    from app.core.modes import TradingMode
    from app.execution.order_gateway import OrderGateway

    class CustomExecutor:
        name = "custom"
        called_with = None
        def execute(self, order, *, audit_log=None):
            CustomExecutor.called_with = order
            return ExecutorResult(status="ACCEPTED", route="paper")

    settings = Settings(trading_mode=TradingMode.PAPER)
    gw = OrderGateway(settings)
    custom = CustomExecutor()
    gw.executors["paper"] = custom
    from app.market.freshness import check_timestamp_freshness
    fresh = check_timestamp_freshness(datetime.now(timezone.utc), 5, label="quote")
    res = gw.submit(
        {"symbol": "BTC/USDT", "side": "BUY", "notional_usdt": 50, "leverage": 1},
        {"open_positions": 0},
        [fresh],
    )
    assert CustomExecutor.called_with is not None


# ── 6. 모듈 경계 — OrderExecutor 가 brokers/agents 직접 사용 패턴 ──

def test_order_executor_module_does_not_depend_on_agents_or_strategies():
    """OrderExecutor 는 agents/strategies 를 import 안 함."""
    repo_root = Path(__file__).resolve().parents[2]
    text = (repo_root / "backend" / "app" / "execution" / "order_executor.py"
            ).read_text(encoding="utf-8")
    for line in text.splitlines():
        s = line.strip()
        if not (s.startswith("import ") or s.startswith("from ")):
            continue
        for forbidden in ("app.agents", "app.strategies"):
            assert forbidden not in s, \
                f"order_executor.py forbidden import: {s}"
