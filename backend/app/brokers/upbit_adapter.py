"""UpbitAdapter — Upbit READ_ONLY 공개 API 어댑터 (체크리스트 #21).

Upbit 공개 시세/호가 endpoint 만 사용. API 키 불필요, 주문 불가.

설계 원칙 (CLAUDE.md §2.1.2 / §4.3 / §28):
  - mode='READ_ONLY' 영구 고정. API 키를 받지 않음 (생성자에서 raise).
  - 주문/잔고 capability=False — 호출 시 ExchangeAdapterDisabledError.
  - pyupbit lazy import — 테스트에서 client 주입 가능 (네트워크 호출 0).
  - 출금 메서드 정의 금지 (영구).
  - LIVE 자격은 별도 어댑터 (#21 후속) 에서 구현하며 본 클래스는 영구 read-only.

사용:
    a = UpbitAdapter()             # pyupbit 자동 import
    a.fetch_ticker("BTC")          # → Ticker
    a.fetch_orderbook("KRW-BTC")   # → OrderBook
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Protocol

from app.schemas import Ticker, OrderBook

from .base import ExchangeAdapter, AdapterCapability


class UpbitClientProtocol(Protocol):
    """pyupbit 공개 함수의 최소 인터페이스 (테스트 주입용)."""

    def get_current_price(self, symbol: str) -> float | None: ...
    def get_orderbook(self, symbol: str) -> list[dict] | dict | None: ...


class UpbitAdapter(ExchangeAdapter):
    """Upbit 공개 시세/호가 read-only 어댑터.

    실제 네트워크 호출이 발생한다. 테스트는 ``client=...`` 인자로
    fake 모듈을 주입해 네트워크 없이 동작 검증.
    """

    _CAP = AdapterCapability(
        name="upbit",
        mode="READ_ONLY",
        can_fetch_ticker    = True,
        can_fetch_orderbook = True,
        can_fetch_balance   = False,
        can_place_order     = False,
        can_cancel_order    = False,
        supports_futures    = False,
        requires_secret     = False,
    )

    def __init__(
        self,
        *,
        client: Any | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
    ):
        # CLAUDE.md §2.1.2 + #28: read-only 어댑터는 API 키 받지 않음
        if api_key or api_secret:
            raise ValueError(
                "UpbitAdapter 는 READ_ONLY 모드 전용 — API 키 사용 금지. "
                "주문 기능은 별도 LIVE 어댑터에서 구현하며, "
                "그때도 출금 권한이 있는 키는 절대 사용하지 않는다."
            )
        self._client = client

    @property
    def capability(self) -> AdapterCapability:
        return self._CAP

    @property
    def client(self) -> Any:
        if self._client is None:
            import pyupbit  # type: ignore[import-not-found]   # lazy
            self._client = pyupbit
        return self._client

    # ── 심볼 정규화 ───────────────────────────────────────────────

    @staticmethod
    def to_upbit_symbol(symbol: str) -> str:
        """입력 심볼을 Upbit 형식 'KRW-XXX' 로 정규화.

        지원:
          - "BTC"          → "KRW-BTC"
          - "BTC/KRW"      → "KRW-BTC"
          - "KRW-BTC"      → "KRW-BTC"
          - "BTC-KRW"      → "KRW-BTC"
          - "USDT/BTC"     → "BTC-USDT"  (KRW 외 페어는 그대로 변환)
        """
        s = symbol.upper().strip()
        if "/" in s:
            base, quote = s.split("/", 1)
            return f"{quote}-{base}"
        if "-" in s:
            a, b = s.split("-", 1)
            if a == "KRW":
                return f"KRW-{b}"
            if b == "KRW":
                return f"KRW-{a}"
            return s
        return f"KRW-{s}"

    # ── ExchangeAdapter 구현 ──────────────────────────────────────

    def fetch_ticker(self, symbol: str) -> Ticker:
        upbit_symbol = self.to_upbit_symbol(symbol)
        price = self.client.get_current_price(upbit_symbol)
        if price is None:
            raise RuntimeError(f"Upbit: ticker for {upbit_symbol} unavailable")
        bid, ask = self._top_of_book(self._fetch_orderbook_raw(upbit_symbol))
        return Ticker(
            symbol=symbol,
            price=float(price),
            bid=float(bid),
            ask=float(ask),
            spread_pct=((ask - bid) / bid) if bid > 0 else 0.0,
            volume_24h=0.0,  # #21 후속에서 get_ohlcv 로 보강 가능
            ts=datetime.now(timezone.utc),
        )

    def fetch_orderbook(self, symbol: str, depth: int = 5) -> OrderBook:
        upbit_symbol = self.to_upbit_symbol(symbol)
        raw = self._fetch_orderbook_raw(upbit_symbol)
        units = (raw.get("orderbook_units") or [])[:depth]
        bids = tuple((float(u["bid_price"]), float(u["bid_size"])) for u in units)
        asks = tuple((float(u["ask_price"]), float(u["ask_size"])) for u in units)
        return OrderBook(
            symbol=symbol,
            bids=bids, asks=asks,
            ts=datetime.now(timezone.utc),
        )

    # ── 내부 헬퍼 ─────────────────────────────────────────────────

    def _fetch_orderbook_raw(self, upbit_symbol: str) -> dict:
        """pyupbit.get_orderbook 의 list/dict 양쪽 반환 형식을 dict 로 정규화."""
        out = self.client.get_orderbook(upbit_symbol)
        if isinstance(out, list):
            return out[0] if out else {}
        return out or {}

    @staticmethod
    def _top_of_book(ob: dict) -> tuple[float, float]:
        units = ob.get("orderbook_units") or []
        if not units:
            return 0.0, 0.0
        u = units[0]
        return float(u["bid_price"]), float(u["ask_price"])
