"""Logs 라우터 — /api/audit."""
from fastapi import APIRouter, Depends

from .deps import audit, verify_admin

router = APIRouter()


@router.get("/api/audit")
def audit_tail(limit: int = 100, _=Depends(verify_admin)):
    return {"events": audit.tail(limit), "total": audit.count()}
