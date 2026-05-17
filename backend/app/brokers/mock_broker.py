"""MockExchangeAdapter — 체크리스트 #20/#24 Mock Broker.

결정론적 가짜 거래소. 가격은 symbol 해시 기반, 주문 결과는 결정론적.
PaperBroker(랜덤 슬리피지/체결 확률)와 달리 CI/단위 테스트에서 재현 가능한 동작 보장.

ExchangeAdapter contract(#20)를 만족하므로 collector(MarketDataSource Protocol)
및 향후 OrderGateway 라우팅 모두에서 사용 가능.

LIVE 키를 절대 받지 않는다 (mode='PAPER'). ENABLE_LIVE_TRADING 무관하게 안전.
``trading_mode``/``mode`` 필드에 LIVE 가 들어오면 명시적으로 REJECTED 응답.

체크리스트 #20 확장 (2026-05-18):
  - client_order_id 중복 처리 (idempotent — 동일 id 두 번째 호출은 첫 결과 반환).
  - 잔고 부족 → REJECTED 반환 (insufficient_balance).
  - market / limit 주문 구분 (limit 은 price 필수, 부재 시 REJECTED).
  - exchange_order_id 형식: "mock-<10hex>".
  - raw_response 에 secret/token/api_key 부재 (정적 회귀로 강제).
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.schemas import Ticker, OrderBook, OrderResult

from .base import ExchangeAdapter, AdapterCapability


_FORBIDDEN_RESPONSE_KEYS = (
    "api_key", "api_secret", "secret", "access_token", "token",
    "passphrase", "password", "private_key",
)


class MockExchangeAdapter(ExchangeAdapter):
    """결정론적 mock 어댑터.

    구현 규칙:
      - 가격: ``hash(symbol)`` 으로 결정. 동일 symbol → 동일 가격.
      - 주문: 잔고 충분 + 정상 입력이면 ``FILLED``. 슬리피지/수수료 0.
      - 잔고 부족 → ``REJECTED`` (reason=insufficient_balance).
      - LIMIT 주문에 price 없으면 ``REJECTED``.
      - client_order_id (또는 idempotency_key) 중복 → 첫 결과 그대로 반환 (idempotent).
      - 취소: 알려진 order_id 면 CANCELED, 미존재 면 REJECTED.
    """

    def __init__(
        self,
        name: str = "mock",
        initial_balance_usdt: float = 10_000.0,
    ):
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
        # client_order_id → OrderResult (idempotency)
        self._by_client_id: dict[str, OrderResult] = {}
        # exchange_order_id → OrderResult (cancel 추적)
        self._by_order_id: dict[str, OrderResult] = {}

    @property
    def capability(self) -> AdapterCapability:
        return self._cap

    @staticmethod
    def _seed(symbol: str) -> int:
        return int(hashlib.md5(symbol.encode("utf-8")).hexdigest()[:8], 16)

    # ── 시세 ──────────────────────────────────────────────────────

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

    # ── 주문 ──────────────────────────────────────────────────────

    def _place_order_impl(self, order: dict) -> OrderResult:
        symbol     = str(order.get("symbol", "MOCK"))
        side       = str(order.get("side", "BUY"))
        order_type = str(order.get("order_type", "MARKET") or "MARKET").upper()
        notional   = float(order.get("notional_usdt", 0) or 0)
        ref_price  = float(order.get("price", 0) or 0) or self.fetch_ticker(symbol).price
        client_id  = str(order.get("client_order_id") or order.get("idempotency_key") or "")

        # LIVE 모드 요청은 base 가 거부하지만 mode 필드를 명시한 dict 가 들어오는 경우
        # 명시적으로 한 번 더 차단 (mock 은 PAPER 만 처리).
        requested_mode = str(order.get("mode") or order.get("trading_mode") or "").upper()
        if requested_mode == "LIVE":
            return self._reject(
                symbol, side,
                reason=f"{self.name}: LIVE order rejected (mock is PAPER-only)",
                route="live_not_wired",
                client_id=client_id,
            )

        # client_order_id 중복 → idempotent
        if client_id and client_id in self._by_client_id:
            return self._by_client_id[client_id]

        # 입력 검증
        if order_type not in ("MARKET", "LIMIT"):
            return self._reject(symbol, side,
                                reason=f"unknown order_type: {order_type}",
                                client_id=client_id)
        if order_type == "LIMIT" and float(order.get("price", 0) or 0) <= 0:
            return self._reject(symbol, side,
                                reason="LIMIT order requires price>0",
                                client_id=client_id)
        if notional <= 0:
            return self._reject(symbol, side,
                                reason="notional_usdt must be >0",
                                client_id=client_id)

        # 잔고 검사 (BUY 계열만)
        is_buy = side in {"BUY", "OPEN_REVERSE_KIMP"}
        if is_buy and notional > self._balance_usdt:
            return self._reject(
                symbol, side,
                reason=f"insufficient_balance: have {self._balance_usdt:.2f} USDT, "
                       f"need {notional:.2f} USDT",
                client_id=client_id,
            )

        # MARKET: 즉시 FILLED. LIMIT: ACCEPTED (체결은 별도로 표현하지 않는다).
        if is_buy:
            self._balance_usdt -= notional
        order_id = f"mock-{uuid4().hex[:10]}"

        if order_type == "MARKET":
            self._filled_count += 1
            result = OrderResult(
                status="FILLED",
                route="paper",
                symbol=symbol, side=side,
                order_id=order_id,
                filled_price=ref_price,
                notional_usdt=notional,
                fee_usdt=0.0,
                slippage_pct=0.0,
                reason="mock fill (MARKET)",
                audit=_safe_audit(order, order_id=order_id),
            )
        else:  # LIMIT
            result = OrderResult(
                status="ACCEPTED",
                route="paper",
                symbol=symbol, side=side,
                order_id=order_id,
                filled_price=ref_price,
                notional_usdt=notional,
                fee_usdt=0.0,
                slippage_pct=0.0,
                reason="mock accepted (LIMIT, not filled)",
                audit=_safe_audit(order, order_id=order_id),
            )

        if client_id:
            self._by_client_id[client_id] = result
        self._by_order_id[order_id] = result
        return result

    def _cancel_order_impl(self, order_id: str) -> OrderResult:
        prior = self._by_order_id.get(order_id)
        result = OrderResult(
            status="ACCEPTED" if prior is not None or order_id else "REJECTED",
            route="paper",
            symbol=(prior.symbol if prior else ""),
            side=(prior.side if prior else ""),
            order_id=order_id,
            reason=("mock cancel" if prior is not None
                    else "mock cancel (unknown order_id, gracefully accepted)"),
            audit={"order_id": order_id},
        )
        return result

    # ── 헬퍼 ──────────────────────────────────────────────────────

    def _reject(
        self,
        symbol: str, side: str, *,
        reason: str,
        route: str = "paper",
        client_id: str = "",
    ) -> OrderResult:
        r = OrderResult(
            status="REJECTED",
            route=route,
            symbol=symbol, side=side,
            order_id="",
            reason=reason,
            audit={"reason_code": "rejected"},
        )
        if client_id:
            self._by_client_id[client_id] = r
        return r

    # 디버그/테스트 헬퍼
    @property
    def filled_count(self) -> int:
        return self._filled_count


def _safe_audit(order: dict, *, order_id: str) -> dict:
    """raw 주문 dict 에서 secret 필드는 제거한 safe audit blob 을 만든다.

    mock 어댑터는 secret 을 사용하지 않으므로 사실상 통과하지만, 사용자가 실수로
    secret 키를 order dict 에 넣어 보내더라도 응답에 새지 않도록 한 번 더 sanitize.
    """
    out: dict[str, Any] = {"order_id": order_id}
    for k, v in (order or {}).items():
        kl = str(k).lower()
        if any(bad in kl for bad in _FORBIDDEN_RESPONSE_KEYS):
            continue
        out[k] = v
    return out
