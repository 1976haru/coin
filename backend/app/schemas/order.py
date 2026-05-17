"""주문 요청·결과 스키마 — 체크리스트 #8 Shared Schemas.

본 모듈은 두 계층의 주문 모델을 함께 제공한다:
  1. (legacy) `OrderRequest` / `OrderResult` — frozen dataclass.
     OrderGateway.submit() 진입 형식. 기존 dict 호출과 호환.
  2. (new) `OrderRequestModel` — Pydantic v2 BaseModel.
     FastAPI 요청 모델 + 강한 validation (quantity>0, limit 필수, etc.).
     스펙 (체크리스트 #8) 의 "OrderRequest" 가 가리키는 객체이며,
     `app.schemas.models` 를 통해 `OrderRequest` 이름으로도 import 가능.

기존 dataclass 와 신규 Pydantic 클래스는 의도적으로 이름을 분리한다
(`OrderRequest` ↔ `OrderRequestModel`) — 1300+ 회귀 테스트 호환을 위해
legacy 이름은 보존한다.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import uuid4

from pydantic import Field, model_validator

from .common import ConfiguredBaseModel, utc_now
from .enums import (
    OrderSide as OrderSideEnum,
    OrderStatus as OrderStatusEnum,
    OrderType as OrderTypeEnum,
    TradingMode,
)


OrderType = Literal["MARKET", "LIMIT"]

OrderStatus = Literal[
    "ACCEPTED", "REJECTED", "BLOCKED", "PENDING_APPROVAL", "SHADOW_LOGGED",
    "FILLED", "PARTIAL", "TIMEOUT",
]

OrderRoute = Literal[
    "paper", "shadow", "approval_queue", "live", "live_not_wired",
    "blocked", "risk", "idempotency", "",
]


@dataclass(frozen=True)
class OrderRequest:
    """OrderGateway.submit()이 받는 표준 주문 요청.

    OrderGateway는 dict도 받지만(레거시), 신규 코드는 이 타입을 사용해
    .to_dict()로 변환해 전달한다.
    """

    symbol: str
    side: str
    notional_usdt: float
    order_type: OrderType = "MARKET"
    price: float = 0.0
    leverage: float = 1.0
    confidence: float = 0.0
    reason: str = ""
    idempotency_key: str = field(default_factory=lambda: str(uuid4()))
    is_order_intent: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OrderResult:
    """OrderGateway.submit() / Broker.place_order()의 정규화된 결과.

    기존 코드는 dict를 반환하므로, 호출자가 OrderResult.from_dict로 감쌀 수
    있도록 하되 이 타입 자체는 새 코드 경로에서 직접 사용한다.
    """

    status: str
    route: str = ""
    symbol: str = ""
    side: str = ""
    order_id: str = ""
    filled_price: float = 0.0
    notional_usdt: float = 0.0
    fee_usdt: float = 0.0
    slippage_pct: float = 0.0
    reason: str = ""
    reasons: tuple[str, ...] = ()
    audit: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "OrderResult":
        reasons = d.get("reasons", ())
        if isinstance(reasons, list):
            reasons = tuple(reasons)
        return cls(
            status=d.get("status", ""),
            route=d.get("route", ""),
            symbol=d.get("symbol", ""),
            side=d.get("side", ""),
            order_id=d.get("order_id", ""),
            filled_price=float(d.get("filled_price", 0.0) or 0.0),
            notional_usdt=float(d.get("notional_usdt", 0.0) or 0.0),
            fee_usdt=float(d.get("fee_usdt", 0.0) or 0.0),
            slippage_pct=float(d.get("slippage_pct", 0.0) or 0.0),
            reason=d.get("reason", ""),
            reasons=reasons,
            audit=d.get("audit", {}) or {},
        )


# ─────────────────────────────────────────────────────────────────
# Pydantic v2 모델 — 체크리스트 #8 (스펙 OrderRequest)
# ─────────────────────────────────────────────────────────────────

class OrderRequestModel(ConfiguredBaseModel):
    """주문 요청 (FastAPI/API 컨트랙트용 Pydantic v2).

    안전 원칙:
      - `requires_approval` 기본 True (사람 승인 우선).
      - `approved` 기본 False — live 모드 + approved=False 면 실행 불가 상태로 노출.
      - `is_order_intent=True` (주문은 명시적 의도).
      - 본 객체에는 broker API key / 계좌번호 / 토큰 등 secret 필드 금지.

    Validation:
      - `quantity > 0`
      - `order_type == LIMIT` 이면 `limit_price` 필수 (>0)
    """

    symbol:       str            = Field(..., min_length=1)
    side:         OrderSideEnum  = Field(..., description="buy / sell")
    order_type:   OrderTypeEnum  = Field(default=OrderTypeEnum.MARKET)
    quantity:     Decimal        = Field(..., gt=0, description="주문 수량 (>0)")
    limit_price:  Optional[Decimal] = Field(
        default=None, gt=0, description="limit 주문 시 필수 (>0)",
    )
    trading_mode: TradingMode    = Field(
        default=TradingMode.PAPER,
        description="기본 paper — live 는 본 단계 비활성 (CLAUDE.md §2.2).",
    )
    status: OrderStatusEnum = Field(
        default=OrderStatusEnum.PENDING,
        description="라이프사이클 상태. 기본 pending.",
    )
    requires_approval: bool = Field(
        default=True,
        description="사람 승인 필요 여부. 기본 True (안전 우선).",
    )
    approved: bool = Field(
        default=False,
        description="승인 완료 여부. live 모드 + approved=False 는 실행 차단 표시.",
    )
    is_order_intent: bool = Field(
        default=True,
        description="명시적 주문 의도. 항상 True.",
    )
    reason: str = Field(default="", description="요청 사유 / 발신 전략")
    idempotency_key: str = Field(
        default_factory=lambda: str(uuid4()),
        description="중복 송신 방지 키",
    )
    ts: datetime = Field(default_factory=utc_now, description="요청 시각 (UTC)")

    # ── 추가 안전장치 ──────────────────────────────────────────
    @model_validator(mode="after")
    def _validate_limit_price_required(self) -> "OrderRequestModel":
        if self.order_type == OrderTypeEnum.LIMIT and self.limit_price is None:
            raise ValueError(
                "limit_price is required when order_type=limit"
            )
        return self

    @property
    def is_executable(self) -> bool:
        """실행 가능 상태인지 (보고용 — 본 단계는 paper 만 실제 실행)."""
        if self.trading_mode == TradingMode.LIVE and not self.approved:
            return False
        if self.requires_approval and not self.approved:
            return False
        return True
