"""Orders 라우터 — /api/order/preview."""
from datetime import datetime, timezone
from fastapi import APIRouter
from pydantic import BaseModel

from app.market.freshness import check_timestamp_freshness
from .deps import settings, gateway

router = APIRouter()


class OrderPreviewRequest(BaseModel):
    symbol: str = "BTC/USDT"
    side: str = "BUY"
    notional_usdt: float = 10.0
    leverage: float = 1.0
    price: float = 0.0
    source: str = "system"
    idempotency_key: str = ""


@router.post("/api/order/preview")
def order_preview(req: OrderPreviewRequest):
    now   = datetime.now(timezone.utc)
    fresh = check_timestamp_freshness(now, settings.freshness_threshold_sec, now, "mock_quote")
    order = req.model_dump()
    return gateway.submit(
        order              = order,
        account            = {"open_positions": 0, "daily_pnl_pct": 0.0, "emergency_stop": False},
        freshness_statuses = [fresh],
        source             = req.source,
    )
