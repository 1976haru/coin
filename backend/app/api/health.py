"""Health & Status 라우터 — /, /api/health, /api/status.

체크리스트 #6 Backend Skeleton: 단순 헬스체크 `/api/health` 는
status / service / mode 만 노출 (load balancer / probe 용도).
`/api/status` 는 운영자용 상세 스냅샷 (기존 유지).
"""
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


@router.get("/api/health")
def health():
    """간단 헬스체크 — 체크리스트 #6 검증 기준.

    mode 는 lowercase 로 노출 (paper/simulation/live_*) — 외부 probe 가
    case-insensitive 비교 없이 사용 가능하게 한다. 상세는 `/api/status`.
    """
    return {
        "status":  "ok",
        "service": "autotrade-backend",
        "mode":    settings.trading_mode.value.lower(),
    }


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
