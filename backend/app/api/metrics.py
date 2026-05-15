"""Metrics 라우터 — 체크리스트 #89 Monitoring.

운영 모니터링용 메트릭 endpoint. Prometheus-style key=value 또는 JSON.
시크릿 노출 없이 시스템 상태를 외부 모니터링 시스템이 수집할 수 있게 한다.
"""
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Response

from .deps import (
    settings, gateway, approvals, audit, collector, notices,
)


router = APIRouter()


def _safe_count(obj, attr: str, default: int = 0) -> int:
    try:
        v = getattr(obj, attr)
        if callable(v):
            v = v()
        return int(v)
    except Exception:
        return default


@router.get("/api/metrics")
def metrics_json():
    """JSON 형식 시스템 메트릭."""
    risk = gateway.risk.status()
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "trading_mode": settings.trading_mode.value,
        "kill_switch_active": int(risk.get("kill_switch", False)),
        "daily_pnl_pct": risk.get("daily_pnl_pct", 0.0),
        "consecutive_losses": risk.get("consecutive_losses", 0),
        "audit_events": _safe_count(audit, "count"),
        "pending_approvals": _safe_count(approvals, "count_pending"),
        "ai_pending_approvals": len(approvals.pending_by_source("ai")),
        "ticker_cache_size": _safe_count(collector, "cache_size"),
        "active_notices": len(notices.active()),
        "ai_gate_daily_count": getattr(gateway.ai_gate, "_daily_count", 0),
    }


@router.get("/api/metrics/prom", response_class=Response)
def metrics_prometheus():
    """Prometheus text exposition format."""
    m = metrics_json()
    lines = [
        "# HELP agent_trader_kill_switch_active Kill switch state (1=active).",
        "# TYPE agent_trader_kill_switch_active gauge",
        f"agent_trader_kill_switch_active {m['kill_switch_active']}",
        "",
        "# HELP agent_trader_daily_pnl_pct Cumulative daily PnL %.",
        "# TYPE agent_trader_daily_pnl_pct gauge",
        f"agent_trader_daily_pnl_pct {m['daily_pnl_pct']}",
        "",
        "# HELP agent_trader_consecutive_losses Consecutive losing trades.",
        "# TYPE agent_trader_consecutive_losses gauge",
        f"agent_trader_consecutive_losses {m['consecutive_losses']}",
        "",
        "# HELP agent_trader_audit_events_total Total audit events.",
        "# TYPE agent_trader_audit_events_total counter",
        f"agent_trader_audit_events_total {m['audit_events']}",
        "",
        "# HELP agent_trader_pending_approvals Pending approval count.",
        "# TYPE agent_trader_pending_approvals gauge",
        f"agent_trader_pending_approvals {m['pending_approvals']}",
        "",
        "# HELP agent_trader_ai_pending_approvals AI-sourced pending approvals.",
        "# TYPE agent_trader_ai_pending_approvals gauge",
        f"agent_trader_ai_pending_approvals {m['ai_pending_approvals']}",
        "",
        "# HELP agent_trader_ticker_cache_size Cached ticker count.",
        "# TYPE agent_trader_ticker_cache_size gauge",
        f"agent_trader_ticker_cache_size {m['ticker_cache_size']}",
        "",
        "# HELP agent_trader_active_notices Active exchange notices.",
        "# TYPE agent_trader_active_notices gauge",
        f"agent_trader_active_notices {m['active_notices']}",
        "",
        "# HELP agent_trader_ai_gate_daily_count AI gate daily executions.",
        "# TYPE agent_trader_ai_gate_daily_count counter",
        f"agent_trader_ai_gate_daily_count {m['ai_gate_daily_count']}",
        "",
    ]
    return Response("\n".join(lines), media_type="text/plain; version=0.0.4")


@router.get("/api/healthz")
def healthz():
    """Liveness probe — 항상 200. 컨테이너 헬스체크용."""
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}
