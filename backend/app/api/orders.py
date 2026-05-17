"""Orders 라우터 — /api/order/preview.

체크리스트 #16: order_preview 가 placeholder freshness 대신
`FreshnessTracker.evaluate_for_order(symbol, exchange, side)` 를 사용한다.

- BUY/OPEN/ENTER 계열 : tracker 의 stale 또는 reconnecting 이면 OrderGateway
  단계에서 차단된다 (`should_block_new_buy` → RiskManager 거부).
- SELL/EXIT/CLOSE 계열: freshness 로 막지 않는다 (위험 축소 동작).
"""
from fastapi import APIRouter
from pydantic import BaseModel

from .deps import gateway, freshness_tracker

router = APIRouter()


class OrderPreviewRequest(BaseModel):
    symbol: str = "BTC/USDT"
    exchange: str = "upbit"
    side: str = "BUY"
    notional_usdt: float = 10.0
    leverage: float = 1.0
    price: float = 0.0
    source: str = "system"
    idempotency_key: str = ""


@router.post("/api/order/preview")
def order_preview(req: OrderPreviewRequest):
    # #16: tracker 에서 freshness 평가. 청산은 자동으로 통과.
    _block, statuses, _reasons = freshness_tracker.evaluate_for_order(
        symbol=req.symbol, exchange=req.exchange, side=req.side,
    )
    order = req.model_dump()
    return gateway.submit(
        order              = order,
        account            = {"open_positions": 0, "daily_pnl_pct": 0.0, "emergency_stop": False},
        freshness_statuses = statuses,
        source             = req.source,
    )
