"""Watchlist 라우터 — /api/watchlist. 체크리스트 #14.

GET 은 공개, 변경(POST/DELETE/PATCH)은 admin 토큰 필요.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.market.watchlist import (
    WatchlistService, WatchlistDuplicateError, WatchlistNotFoundError,
)

from .deps import get_db, verify_admin


router = APIRouter()


class WatchlistAddRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    exchange: str = Field("upbit", max_length=16)
    list_name: str = Field("default", max_length=32)
    enabled: bool = True
    max_notional_usdt_override: Optional[float] = None
    tags: list[str] = Field(default_factory=list)
    note: str = ""


@router.get("/api/watchlist")
def list_watchlist(
    list_name: Optional[str] = None,
    exchange: Optional[str] = None,
    enabled_only: bool = False,
    db: Session = Depends(get_db),
):
    svc = WatchlistService(db)
    return {
        "entries": svc.list_entries(list_name=list_name, exchange=exchange,
                                    enabled_only=enabled_only),
        "lists": svc.list_names(),
    }


@router.post("/api/watchlist", status_code=201)
def add_watchlist(
    body: WatchlistAddRequest,
    db: Session = Depends(get_db),
    _=Depends(verify_admin),
):
    svc = WatchlistService(db)
    try:
        return svc.add(
            symbol=body.symbol,
            exchange=body.exchange,
            list_name=body.list_name,
            enabled=body.enabled,
            max_notional_usdt_override=body.max_notional_usdt_override,
            tags=body.tags,
            note=body.note,
        )
    except WatchlistDuplicateError as e:
        raise HTTPException(409, str(e))


@router.delete("/api/watchlist/{entry_id}", status_code=204)
def remove_watchlist(
    entry_id: int,
    db: Session = Depends(get_db),
    _=Depends(verify_admin),
):
    svc = WatchlistService(db)
    try:
        svc.remove(entry_id)
    except WatchlistNotFoundError as e:
        raise HTTPException(404, str(e))


@router.patch("/api/watchlist/{entry_id}/enable")
def enable_watchlist(
    entry_id: int,
    db: Session = Depends(get_db),
    _=Depends(verify_admin),
):
    svc = WatchlistService(db)
    try:
        return svc.set_enabled(entry_id, True)
    except WatchlistNotFoundError as e:
        raise HTTPException(404, str(e))


@router.patch("/api/watchlist/{entry_id}/disable")
def disable_watchlist(
    entry_id: int,
    db: Session = Depends(get_db),
    _=Depends(verify_admin),
):
    svc = WatchlistService(db)
    try:
        return svc.set_enabled(entry_id, False)
    except WatchlistNotFoundError as e:
        raise HTTPException(404, str(e))
