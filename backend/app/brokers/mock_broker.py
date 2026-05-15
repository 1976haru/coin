"""MockExchangeAdapter — 체크리스트 #24 Mock Broker.

결정론적 가짜 거래소. 가격은 symbol 해시 기반, 주문은 항상 동일 결과.
PaperBroker(랜덤 슬리피지/체결 확률)와 달리 CI/단위 테스트에서 재현 가능한 동작 보장.

ExchangeAdapter contract(#20)를 만족하므로 collector(MarketDataSource Protocol)
및 향후 OrderGateway 라우팅 모두에서 사용 가능.

LIVE 키를 절대 받지 않는다 (mode='PAPER'). ENABLE_LIVE_TRADING 무관하게 안전.
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from app.schemas import Ticker, OrderBook, OrderResult

from .base import ExchangeAdapter, AdapterCapability


class MockExchangeAdapter(ExchangeAdapter):
    """결정론적 mock 어댑터.

    구현 규칙:
      - 가격: ``hash(symbol)`` 으로 결정. 동일 symbol → 동일 가격.
      - 주문: 항상 ``FILLED`` (slippage/fee 0). 잔고는 in-memory 차감.
      - 취소: 항상 ACCEPTED.
    """

    def __init__(self, name: str = "mock", initial_balance_usdt: float = 10_000.0):
        self._cap = AdapterCapability(
            name=name,
            mode="PAPER",
            can_fetch_ticker    = True,
            can_fetch_orderbook = True,
            can_fetch_balance   = True,
            can_place_order     = True,
            can_cancel_order    = True,
            supports_futures    = False,
            requires_secret     = False,
        )
        self._balance_usdt = float(initial_balance_usdt)
        self._filled_count = 0

    @property
    def capability(self) -> AdapterCapability:
        return self._cap

    @staticmethod
    def _seed(symbol: str) -> int:
        return int(hashlib.md5(symbol.encode("utf-8")).hexdigest()[:8], 16)

    def fetch_ticker(self, symbol: str) -> Ticker:
        h = self._seed(symbol)
        price = 1000.0 + float(h % 100_000)
        bid, ask = price * 0.9995, price * 1.0005
        return Ticker(
            symbol=symbol, price=price,
            bid=bid, ask=ask,
            spread_pct=(ask - bid) / bid,
            volume_24h=float(h % 1_000_000_000),
            ts=datetime.now(timezone.utc),
        )

    def fetch_orderbook(self, symbol: str, depth: int = 5) -> OrderBook:
        t = self.fetch_ticker(symbol)
        bids = tuple((t.bid * (1 - 0.0001 * i), 1.0) for i in range(depth))
        asks = tuple((t.ask * (1 + 0.0001 * i), 1.0) for i in range(depth))
        return OrderBook(symbol=symbol, bids=bids, asks=asks, ts=t.ts)

    def _fetch_balance_impl(self) -> dict:
        return {"USDT": round(self._balance_usdt, 6)}

    def _place_order_impl(self, order: dict) -> OrderResult:
        notional = float(order.get("notional_usdt", 0) or 0)
        symbol = str(order.get("symbol", "MOCK"))
        side   = str(order.get("side", "BUY"))
        ref_price = float(order.get("price", 0) or 0) or 100.0
        # mock 잔고 차감 (BUY 만 — SELL/CLOSE 는 별도 회계)
        if side in {"BUY", "OPEN_REVERSE_KIMP"}:
            self._balance_usdt -= notional
        self._filled_count += 1
        return OrderResult(
            status="FILLED",
            route="paper",
            symbol=symbol, side=side,
            order_id=f"mock-{uuid4().hex[:10]}",
            filled_price=ref_price,
            notional_usdt=notional,
            fee_usdt=0.0,
            slippage_pct=0.0,
            reason="mock fill",
        )

    def _cancel_order_impl(self, order_id: str) -> OrderResult:
        return OrderResult(
            status="ACCEPTED", route="paper",
            order_id=order_id, reason="mock cancel",
        )

    # 디버그/테스트 헬퍼
    @property
    def filled_count(self) -> int:
        return self._filled_count
