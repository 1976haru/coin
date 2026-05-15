"""주문 요청·결과 스키마 — 체크리스트 #8 Shared Schemas.

OrderRequest는 OrderGateway.submit() 진입 형식. 기존 dict 호출도 호환되지만
신규 호출자는 이 타입을 권장한다 (필드 누락·오타 방지).
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal
from uuid import uuid4


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
