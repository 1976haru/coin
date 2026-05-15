"""Notices 라우터 — 체크리스트 #18.

GET 은 공개, 변경(POST/DELETE)은 admin 토큰 필요.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.market.notices import (
    NoticeRegistry, NoticeKind, assess_symbol_notices,
)

from .deps import get_notices, verify_admin


router = APIRouter()


class NoticeAddRequest(BaseModel):
    exchange:   str = Field(..., max_length=16)
    symbol:     str = Field(..., max_length=32)
    kind:       str
    message:    str = ""
    starts_at:  Optional[datetime] = None
    ends_at:    Optional[datetime] = None
    source_url: str = ""


@router.get("/api/notices")
def list_notices(
    active_only: bool = True,
    exchange: Optional[str] = None,
    symbol: Optional[str] = None,
    registry: NoticeRegistry = Depends(get_notices),
):
    items = registry.active() if active_only else registry.all()
    if exchange is not None:
        items = [n for n in items if n.exchange == exchange]
    if symbol is not None:
        items = [n for n in items if n.symbol == symbol]
    return {
        "notices": [n.to_dict() for n in items],
        "count": len(items),
    }


@router.get("/api/notices/symbol/{exchange}/{symbol}")
def get_symbol_status(
    exchange: str,
    symbol: str,
    registry: NoticeRegistry = Depends(get_notices),
):
    """특정 (exchange, symbol)의 거래 가능성 요약 — KimpStrategy 입력 형식."""
    s = assess_symbol_notices(registry, symbol, exchange)
    return {
        "exchange": s.exchange,
        "symbol": s.symbol,
        "deposit_ok": s.deposit_ok,
        "withdrawal_ok": s.withdrawal_ok,
        "deposit_withdrawal_ok": s.deposit_withdrawal_ok,
        "tradable": s.tradable,
        "has_warning": s.has_warning,
        "reasons": s.reasons(),
    }


@router.post("/api/notices", status_code=201)
def add_notice(
    body: NoticeAddRequest,
    registry: NoticeRegistry = Depends(get_notices),
    _=Depends(verify_admin),
):
    try:
        notice = registry.add(
            exchange=body.exchange,
            symbol=body.symbol,
            kind=body.kind,  # type: ignore[arg-type]
            message=body.message,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            source_url=body.source_url,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return notice.to_dict()


@router.delete("/api/notices/{notice_id}", status_code=204)
def remove_notice(
    notice_id: int,
    registry: NoticeRegistry = Depends(get_notices),
    _=Depends(verify_admin),
):
    if not registry.remove(notice_id):
        raise HTTPException(404, f"notice id={notice_id} not found")
