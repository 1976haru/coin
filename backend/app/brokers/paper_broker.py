"""PaperBroker — frozen dataclass 결과 + 슬리피지/수수료/미체결 시뮬.

체크리스트 #25 Paper Broker.
이전 위치: app/execution/paper_broker.py
"""
from dataclasses import dataclass, asdict
from uuid import uuid4
from datetime import datetime, timezone
import random


@dataclass(frozen=True)
class PaperOrderResult:
    order_id: str
    status: str          # FILLED | PARTIAL | TIMEOUT
    symbol: str
    side: str
    notional_usdt: float
    filled_price: float
    fee_usdt: float
    slippage_pct: float
    created_at: str


class PaperBroker:
    """수수료·슬리피지·미체결 시뮬레이션을 포함한 가상 브로커."""

    def __init__(
        self,
        fee_rate: float = 0.0005,
        slippage_rate: float = 0.0005,
        fill_chance: float = 0.95,
        min_latency_ms: float = 50,
        max_latency_ms: float = 200,
    ):
        self.fee_rate      = fee_rate
        self.slippage_rate = slippage_rate
        self.fill_chance   = fill_chance
        self.latency_range = (min_latency_ms / 1000, max_latency_ms / 1000)

    def place_order(self, order: dict) -> dict:
        symbol    = order.get("symbol", "UNKNOWN")
        side      = order.get("side", "BUY")
        notional  = float(order.get("notional_usdt", 0))
        ref_price = float(order.get("price", 1.0)) or 1.0

        if random.random() > self.fill_chance:
            result = PaperOrderResult(
                order_id    = f"paper-timeout-{uuid4().hex[:8]}",
                status      = "TIMEOUT",
                symbol      = symbol,
                side        = side,
                notional_usdt = notional,
                filled_price  = 0.0,
                fee_usdt      = 0.0,
                slippage_pct  = 0.0,
                created_at    = datetime.now(timezone.utc).isoformat(),
            )
            return asdict(result)

        direction   = 1 if side in {"BUY", "OPEN_REVERSE_KIMP"} else -1
        slippage    = self.slippage_rate * random.uniform(0.8, 1.2) * direction
        fill_price  = ref_price * (1 + slippage)
        slippage_pct = abs(slippage) * 100

        fee = notional * self.fee_rate

        result = PaperOrderResult(
            order_id      = f"paper-{uuid4().hex[:10]}",
            status        = "FILLED",
            symbol        = symbol,
            side          = side,
            notional_usdt = notional,
            filled_price  = round(fill_price, 6),
            fee_usdt      = round(fee, 6),
            slippage_pct  = round(slippage_pct, 4),
            created_at    = datetime.now(timezone.utc).isoformat(),
        )
        return asdict(result)
