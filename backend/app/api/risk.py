"""Risk 라우터 — /api/kill-switch + /api/promotion/*."""
from dataclasses import asdict
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.governance.promotion_gates import check_paper_gate, check_shadow_gate
from .deps import gateway, verify_admin

router = APIRouter()


class KillSwitchRequest(BaseModel):
    active: bool
    reason: str = ""


class PromotionMetrics(BaseModel):
    sharpe: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    total_trades: int = 0
    weeks_run: int = 0
    shadow_weeks: int = 0
    p95_latency_ms: float = 0.0
    failure_drills_done: int = 0


@router.post("/api/kill-switch")
def kill_switch(body: KillSwitchRequest, _=Depends(verify_admin)):
    gateway.kill_switch(body.active, body.reason)
    return {"kill_switch": body.active, "reason": body.reason}


@router.post("/api/promotion/paper-gate")
def paper_gate(metrics: PromotionMetrics, _=Depends(verify_admin)):
    return asdict(check_paper_gate(metrics.model_dump()))


@router.post("/api/promotion/shadow-gate")
def shadow_gate(metrics: PromotionMetrics, _=Depends(verify_admin)):
    return asdict(check_shadow_gate(metrics.model_dump()))
