"""Rate Limit Guard 상태 라우터 — 체크리스트 #26.

- GET /api/rate-limits — 거래소·그룹 별 guard 상태 (snapshot).

본 라우터는 read-only — 상태 reset 은 admin 전용 별도 endpoint 로 분리하거나 본
단계에서는 추가하지 않는다 (운영자 실수 방지).

응답에는 API key/secret/token 이 포함되지 않는다 (정적 회귀로 강제).
"""
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.brokers.rate_limit_guard import ExchangeRateLimitRegistry

from .deps import get_rate_limit_registry


router = APIRouter()


@router.get("/api/rate-limits")
def list_rate_limits(
    registry: ExchangeRateLimitRegistry = Depends(get_rate_limit_registry),
):
    snap = registry.snapshot_all()
    return {
        **snap,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        # secret/token 노출 부재 (회귀로 강제 — 본 라우터는 정책만 노출)
        "warning": (
            "Rate-limit policies are conservative defaults — verify against "
            "exchange documentation before live operations."
        ),
    }
