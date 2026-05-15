"""Approvals 라우터 — /api/approval/*."""
from dataclasses import asdict
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .deps import approvals, verify_admin

router = APIRouter()


class ApprovalDecision(BaseModel):
    approved: bool


@router.get("/api/approval/queue")
def approval_queue_list():
    return {"items": approvals.list(), "pending": approvals.count_pending()}


@router.post("/api/approval/{item_id}")
def approval_decide(item_id: str, body: ApprovalDecision, _=Depends(verify_admin)):
    try:
        item = approvals.decide(item_id, body.approved)
    except KeyError:
        raise HTTPException(404, "approval item not found")
    return asdict(item)
