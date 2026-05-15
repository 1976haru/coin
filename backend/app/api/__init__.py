"""API 라우터 패키지 — main.py 에서 include_router 로 등록."""
from fastapi import APIRouter

from . import (
    health, info, market, strategies, orders, approvals,
    risk, logs, watchlist, notices, themes, config, metrics,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(info.router)
api_router.include_router(market.router)
api_router.include_router(strategies.router)
api_router.include_router(orders.router)
api_router.include_router(approvals.router)
api_router.include_router(risk.router)
api_router.include_router(logs.router)
api_router.include_router(watchlist.router)
api_router.include_router(notices.router)
api_router.include_router(themes.router)
api_router.include_router(config.router)
api_router.include_router(metrics.router)

__all__ = ["api_router"]
