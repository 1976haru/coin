"""ExchangeAdapter — 체크리스트 #20 Exchange Adapter Interface.

거래소별 read-only 시세/잔고 + (옵션) 주문 송신을 표준화한 인터페이스.
실제 거래소 구현(#21 Upbit, #22 OKX, #23 Binance)은 본 인터페이스를 따른다.

설계 원칙 (CLAUDE.md):
  - 어댑터는 OrderGateway 경유로만 호출. Strategy/Agent 직접 import 금지 (모듈 경계).
  - 출금 메서드 정의 금지 (영구) — withdraw_*/transfer_* 등 어떤 변형도 작성 금지.
  - read-only 와 trade 동작을 ``AdapterCapability`` 플래그로 명시 분리.
  - Sandbox/Paper 키와 LIVE 키 절대 섞지 않음 (#28) — adapter ``mode`` 로 표현.
  - MarketDataSource (collector.py) Protocol 호환 — 동일 인스턴스로 시세 수집/주문 모두.
  - capability 가 false 인 동작 호출 시 ExchangeAdapterDisabledError 또는 REJECTED 결과.

공통 메서드 카탈로그 (스펙):
  - fetch_price(symbol)            — fetch_ticker 의 가격 추출 alias (편의)
  - fetch_ticker(symbol)           — 전체 Ticker 객체
  - fetch_orderbook(symbol, depth) — OrderBook
  - get_balance() / fetch_balance() — 잔고 dict
  - place_order(order)             — 주문 송신
  - cancel_order(order_id)         — 주문 취소

LIVE 모드 정책 (CLAUDE.md §2.2):
  - mode 가 ``LIVE`` 인 어댑터로 place_order 가 들어오더라도, settings.enable_live_trading
    이 False 이면 본 base 가 사전 거부한다 (구현체와 무관하게 안전).
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from app.schemas import Ticker, OrderBook, OrderRequest, OrderResult


# ── 자격 모드 ────────────────────────────────────────────────────
# READ_ONLY: 시세/호가만 (잔고/주문 불가). API 키 불필요한 공개 엔드포인트만 사용.
# PAPER:    내부 가상 체결. mock/paper 어댑터.
# SANDBOX:  거래소가 제공하는 sandbox/testnet 환경. 실 키 사용 금지.
# LIVE:     실제 거래. ENABLE_LIVE_TRADING + 별도 게이트 통과 후에만 활성.
AdapterMode = Literal["READ_ONLY", "PAPER", "SANDBOX", "LIVE"]


@dataclass(frozen=True)
class AdapterCapability:
    """어댑터가 지원하는 동작 카탈로그.

    구현체는 본 객체로 자신이 무엇을 할 수 있는지 명시한다. 호출자가
    capability 를 보고 분기하므로 "조용한 NotImplemented" 가 발생하지 않는다.
    """

    name: str                          # "upbit" / "okx" / "binance" / "mock"
    mode: AdapterMode
    can_fetch_ticker:    bool = True
    can_fetch_orderbook: bool = True
    can_fetch_balance:   bool = False
    can_place_order:     bool = False
    can_cancel_order:    bool = False
    supports_futures:    bool = False  # 선물 (Phase 8 후순위)
    requires_secret:     bool = False  # API key/secret 필요 여부

    def to_dict(self) -> dict:
        return {
            "name": self.name, "mode": self.mode,
            "can_fetch_ticker":    self.can_fetch_ticker,
            "can_fetch_orderbook": self.can_fetch_orderbook,
            "can_fetch_balance":   self.can_fetch_balance,
            "can_place_order":     self.can_place_order,
            "can_cancel_order":    self.can_cancel_order,
            "supports_futures":    self.supports_futures,
            "requires_secret":     self.requires_secret,
        }


class ExchangeAdapterDisabledError(RuntimeError):
    """capability 외 동작 호출 시."""


class ExchangeAdapter(ABC):
    """거래소 read-only + 주문 송신 표준 인터페이스.

    구현 규칙:
      - 출금/이체 메서드 정의 금지 (영구).
      - mode 가 READ_ONLY/PAPER/SANDBOX 인 어댑터는 LIVE 키를 받지 않는다 (#28).
      - 모든 메서드 동기. async 가 필요하면 별도 wrapper 모듈.
      - MarketDataSource Protocol 만족 (``name``, ``fetch_ticker``, ``fetch_orderbook``).

    하위 클래스는 ``capability``, ``fetch_ticker``, ``fetch_orderbook`` 필수 구현.
    잔고/주문/취소는 capability flag 가 true 일 때만 ``_*_impl`` hook 을 구현한다.
    """

    @property
    @abstractmethod
    def capability(self) -> AdapterCapability:
        ...

    # ── MarketDataSource Protocol 호환 ────────────────────────────

    @property
    def name(self) -> str:
        return self.capability.name

    @abstractmethod
    def fetch_ticker(self, symbol: str) -> Ticker:
        ...

    @abstractmethod
    def fetch_orderbook(self, symbol: str, depth: int = 5) -> OrderBook:
        ...

    # ── 스펙 alias — fetch_price ─────────────────────────────────
    #
    # 스펙(체크리스트 #20)에는 ``fetch_price`` 메서드 명이 등장한다. 본 베이스는
    # fetch_ticker 와 동등하게 동작하는 alias 로 노출한다. (Ticker 객체 자체를
    # 사용하려면 fetch_ticker, 가격만 필요하면 fetch_price.)

    def fetch_price(self, symbol: str) -> float:
        """현재가만 반환 — fetch_ticker 결과의 ``price`` 필드."""
        return float(self.fetch_ticker(symbol).price)

    # ── 잔고 ──────────────────────────────────────────────────────

    def fetch_balance(self) -> dict:
        if not self.capability.can_fetch_balance:
            raise ExchangeAdapterDisabledError(
                f"{self.name}: fetch_balance disabled (mode={self.capability.mode})"
            )
        return self._fetch_balance_impl()

    def get_balance(self) -> dict:
        """스펙 alias — ``fetch_balance`` 와 동등."""
        return self.fetch_balance()

    def _fetch_balance_impl(self) -> dict:
        raise NotImplementedError(f"{self.name}._fetch_balance_impl must be implemented")

    # ── 주문 ──────────────────────────────────────────────────────

    def place_order(self, order: OrderRequest | dict) -> OrderResult:
        if not self.capability.can_place_order:
            raise ExchangeAdapterDisabledError(
                f"{self.name}: place_order disabled (mode={self.capability.mode})"
            )
        order_dict = order.to_dict() if isinstance(order, OrderRequest) else dict(order)
        # CLAUDE.md §2.2 — LIVE 모드 어댑터는 settings.enable_live_trading 이 False 면
        # base 단계에서 거부. 구현체가 우회하지 못하도록 base 에서 강제한다.
        if self.capability.mode == "LIVE":
            from app.core.config import get_settings
            if not get_settings().enable_live_trading:
                return OrderResult(
                    status="REJECTED",
                    route="live_not_wired",
                    symbol=str(order_dict.get("symbol", "")),
                    side=str(order_dict.get("side", "")),
                    reason=(f"{self.name}: LIVE order rejected — "
                            "ENABLE_LIVE_TRADING=false (CLAUDE.md §2.2)"),
                )
        # client_order_id 정규화 — order 가 idempotency_key 만 가지고 있으면 그대로,
        # client_order_id 가 명시되어 있으면 우선시. base 는 단순 패스스루.
        if "client_order_id" not in order_dict:
            cid = order_dict.get("idempotency_key") or ""
            if cid:
                order_dict["client_order_id"] = cid
        return self._place_order_impl(order_dict)

    def _place_order_impl(self, order: dict) -> OrderResult:
        raise NotImplementedError(f"{self.name}._place_order_impl must be implemented")

    def cancel_order(self, order_id: str) -> OrderResult:
        if not self.capability.can_cancel_order:
            return OrderResult(
                status="REJECTED", route="blocked",
                reason=f"{self.name}: cancel disabled",
            )
        return self._cancel_order_impl(order_id)

    def _cancel_order_impl(self, order_id: str) -> OrderResult:
        raise NotImplementedError(f"{self.name}._cancel_order_impl must be implemented")


# ── 적합성 헬퍼 ──────────────────────────────────────────────────

def conforms_to_market_data_source(adapter: ExchangeAdapter) -> bool:
    """Adapter 가 collector.MarketDataSource Protocol 을 만족하는지 검증.

    Protocol 은 runtime_checkable 이라 isinstance 로 확인 가능.
    """
    from app.market.collector import MarketDataSource
    return isinstance(adapter, MarketDataSource)


def assert_no_withdrawal_methods(adapter_cls: type) -> None:
    """어댑터 클래스에 출금/이체 메서드가 없는지 회귀 검증.

    CLAUDE.md §2.1.2: 출금 권한 키 사용 금지. 출금 관련 메서드 정의 자체를 금한다.
    """
    forbidden = ("withdraw", "withdrawal", "transfer", "send_to_address",
                 "create_withdrawal", "request_withdrawal")
    for name in dir(adapter_cls):
        low = name.lower()
        for f in forbidden:
            assert f not in low, (
                f"{adapter_cls.__name__} 가 출금 관련 메서드 보유: {name} "
                "(CLAUDE.md §2.1.2 위반)"
            )
