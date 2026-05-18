"""Paper Trading 라우터 — 체크리스트 #25.

- GET  /api/paper/status   (공개) — PaperTrader 상태
- GET  /api/paper/orders   (공개) — paper order logs
- GET  /api/paper/sources  (공개) — 사용 가능한 paper source 목록
- POST /api/paper/start    (admin) — paper mode 시작
- POST /api/paper/stop     (admin) — paper mode 중지
- POST /api/paper/reset    (admin) — paper state 초기화
- POST /api/paper/source   (admin) — paper source 변경

본 라우터는 실거래 주문을 받지 않는다 — 모든 응답에 ``is_real_trade=False``,
``mode="PAPER"``, ``execution_source="paper_trader"``, ``warning``,
``fill_quality_warning`` 가 포함된다.

paper *주문 송신* 은 기존 OrderGateway 단일 경로 (`POST /api/order/...`) 를 통해
이루어진다 — 본 라우터는 별도 submit endpoint 를 만들지 않는다 (CLAUDE.md §2.4).
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.brokers.paper_trader import (
    PaperTrader, PaperTraderError, AVAILABLE_PAPER_SOURCES,
)

from .deps import get_paper_trader, verify_admin


router = APIRouter()


_PAPER_ENVELOPE: dict = {
    "mode": "PAPER",
    "is_real_trade": False,
    "execution_source": "paper_trader",
    "warning": "Paper execution only. Not real profit or real trade.",
    "fill_quality_warning": (
        "Paper fills may differ from live execution (no real market impact, "
        "no real slippage, no real partial fills)."
    ),
}


class PaperSourceRequest(BaseModel):
    name: str = Field(..., max_length=64)


@router.get("/api/paper/status")
def paper_status(trader: PaperTrader = Depends(get_paper_trader)):
    return trader.get_paper_status()


@router.get("/api/paper/orders")
def paper_orders(
    limit: int = 100,
    client_order_id: Optional[str] = None,
    trader: PaperTrader = Depends(get_paper_trader),
):
    logs = trader.get_paper_logs(
        limit=max(1, min(int(limit), 1000)),
        client_order_id=client_order_id,
    )
    return {
        "orders": logs,
        "count": len(logs),
        **_PAPER_ENVELOPE,
    }


@router.get("/api/paper/sources")
def paper_sources():
    return {
        "available": list(AVAILABLE_PAPER_SOURCES),
        **_PAPER_ENVELOPE,
    }


@router.post("/api/paper/start")
def paper_start(
    trader: PaperTrader = Depends(get_paper_trader),
    _=Depends(verify_admin),
):
    status = trader.start_paper()
    return {**status.to_dict(), **_PAPER_ENVELOPE}


@router.post("/api/paper/stop")
def paper_stop(
    trader: PaperTrader = Depends(get_paper_trader),
    _=Depends(verify_admin),
):
    status = trader.stop_paper()
    return {**status.to_dict(), **_PAPER_ENVELOPE}


@router.post("/api/paper/reset")
def paper_reset(
    trader: PaperTrader = Depends(get_paper_trader),
    _=Depends(verify_admin),
):
    status = trader.reset_paper()
    return {**status.to_dict(), **_PAPER_ENVELOPE}


@router.post("/api/paper/source")
def paper_select_source(
    body: PaperSourceRequest,
    trader: PaperTrader = Depends(get_paper_trader),
    _=Depends(verify_admin),
):
    try:
        selected = trader.select_paper_source(body.name)
    except PaperTraderError as e:
        raise HTTPException(400, str(e))
    return {
        "selected": selected,
        "status": trader.get_paper_status(),
        **_PAPER_ENVELOPE,
    }
