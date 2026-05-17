"""체크리스트 #8 Shared Schemas — 체결(FillEvent) 스키마.

브로커가 보고하는 단일 체결(부분/전체) 이벤트의 정규 형식.
본 단계는 paper / mock 체결만 사용. live 송신은 비활성.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import Field

from .common import ConfiguredBaseModel, utc_now
from .enums import OrderSide, TradingMode


class FillEvent(ConfiguredBaseModel):
    """단일 체결 이벤트.

    Note
    ----
    `price` 는 0 보다 큰 Decimal 이어야 한다 (validation error 강제).
    민감정보(브로커 API key, 계좌번호, 토큰) 는 본 스키마에 두지 않는다.
    """

    fill_id:      str        = Field(..., description="브로커가 발급한 체결 식별자")
    order_id:     str        = Field(..., description="원 주문 식별자")
    symbol:       str        = Field(..., min_length=1, description="거래 심볼")
    side:         OrderSide  = Field(..., description="체결 방향 — buy/sell")
    quantity:     Decimal    = Field(..., gt=0, description="체결 수량 (>0)")
    price:        Decimal    = Field(..., gt=0, description="체결 가격 (>0)")
    fee:          Decimal    = Field(default=Decimal("0"), ge=0, description="수수료")
    fee_currency: Optional[str] = Field(default=None, description="수수료 통화 (예: USDT, KRW)")
    trading_mode: TradingMode  = Field(
        default=TradingMode.PAPER,
        description="체결 환경. 본 단계는 paper/mock 만 활성.",
    )
    ts: datetime = Field(default_factory=utc_now, description="체결 시각 (UTC)")
    is_simulated: bool = Field(
        default=True,
        description="paper/mock 체결이면 True. live 실제 체결은 본 단계에서 발생하지 않는다.",
    )
