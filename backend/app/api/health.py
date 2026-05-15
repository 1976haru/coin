"""Health & Status 라우터 — /, /api/status."""
import os
from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.core.app_info import app_info
from .deps import settings, gateway, approvals, audit

router = APIRouter()


@router.get("/")
def root():
    if os.path.exists("frontend/index.html"):
        return FileResponse("frontend/index.html")
    info = app_info()
    info["docs"] = "/docs"
    info["warning"] = "research/paper-first system — LIVE 기본 비활성"
    return info


@router.get("/api/status")
def status():
    info = app_info()
    return {
        "app":                  info,
        "trading_mode":         settings.trading_mode.value,
        "mode_label":           settings.trading_mode.label,
        "demo_mode":            settings.demo_mode,
        "enable_live_trading":  settings.enable_live_trading,
        "enable_ai_execution":  settings.enable_ai_execution,
        "enable_kimp_strategy": settings.enable_kimp_strategy,
        "enable_ai_agents":     settings.enable_ai_agents,
        "risk_status":          gateway.risk.status(),
        "pending_approvals":    approvals.count_pending(),
        "audit_events":         audit.count(),
        "safety_warnings":      settings.validate(),  # #9 Config Layer
    }
