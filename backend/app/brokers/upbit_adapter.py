"""UpbitAdapter — Upbit READ_ONLY 공개 API 어댑터 (체크리스트 #21).

Upbit 공개 시세/호가 endpoint 만 사용. API 키 불필요, 주문 불가.

설계 원칙 (CLAUDE.md §2.1.2 / §4.3 / §28):
  - mode='READ_ONLY' 영구 고정. API 키를 받지 않음 (생성자에서 raise).
  - 주문/잔고 capability=False — 호출 시 ExchangeAdapterDisabledError.
  - pyupbit lazy import — 테스트에서 client 주입 가능 (네트워크 호출 0).
  - 출금 메서드 정의 금지 (영구).
  - LIVE 자격은 별도 어댑터 (#21 후속) 에서 구현하며 본 클래스는 영구 read-only.

체크리스트 #21 보강 (2026-05-18):
  - 모듈 레벨 헬퍼: ``normalize_upbit_market``, ``to_internal_symbol``,
    ``is_krw_market`` — 심볼 정규화를 단독 함수로 노출.
  - ``public_client`` (UpbitPublicClient) 주입 옵션 — production transport 기반
    경로. legacy ``client`` (pyupbit 호환) 도 그대로 유지.

사용:
    # 1) legacy — pyupbit 자동 import (또는 fake client 주입)
    a = UpbitAdapter()
    a.fetch_ticker("BTC")          # → Ticker
    a.fetch_orderbook("KRW-BTC")   # → OrderBook

    # 2) 신규 — UpbitPublicClient 주입 (httpx/requests transport 도 외부 주입)
    from app.brokers.upbit_public import UpbitPublicClient
    pc = UpbitPublicClient(transport=my_transport)
    a = UpbitAdapter(public_client=pc)
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Protocol

from app.schemas import Ticker, OrderBook

from .base import ExchangeAdapter, AdapterCapability


# ── 모듈 레벨 심볼 헬퍼 (테스트 + adapter 양쪽에서 사용) ─────────


def normalize_upbit_market(symbol: str) -> str:
    """입력 심볼을 Upbit 형식 (`QUOTE-BASE`, e.g. `KRW-BTC`) 으로 정규화.

    지원:
      - "BTC"          → "KRW-BTC"   (기본 KRW)
      - "btc"          → "KRW-BTC"
      - "BTC/KRW"      → "KRW-BTC"
      - "BTC-KRW"      → "KRW-BTC"
      - "KRW-BTC"      → "KRW-BTC"
      - "BTC-USDT"     → "USDT-BTC"  (KRW 외 페어는 quote 가 앞)
      - "USDT-BTC"     → "USDT-BTC"
    공백/None/empty 는 ValueError.
    """
    if symbol is None:
        raise ValueError("upbit market symbol is None")
    s = str(symbol).strip().upper()
    if not s:
        raise ValueError("upbit market symbol is empty")
    if "/" in s:
        parts = [p for p in s.split("/", 1) if p]
        if len(parts) != 2:
            raise ValueError(f"invalid upbit symbol: {symbol!r}")
        base, quote = parts
        return f"{quote}-{base}"
    if "-" in s:
        parts = [p for p in s.split("-", 1) if p]
        if len(parts) != 2:
            raise ValueError(f"invalid upbit symbol: {symbol!r}")
        a, b = parts
        # 업비트는 quote 가 앞. 알려진 quote: KRW / USDT / BTC.
        # KRW 는 base 로 거의 등장하지 않으므로 양쪽 어느 위치에 있든 quote 로 본다.
        if a == "KRW":
            return f"KRW-{b}"
        if b == "KRW":
            return f"KRW-{a}"
        # USDT 도 보통 quote (base 가 USDT 인 경우는 사실상 없음).
        if a == "USDT":
            return f"USDT-{b}"
        if b == "USDT":
            return f"USDT-{a}"
        # 마지막으로 BTC. 업비트 BTC 마켓은 BTC-XRP 같은 형식 (BTC 가 앞).
        if a == "BTC":
            return f"BTC-{b}"
        if b == "BTC":
            return f"BTC-{a}"
        # 알려진 quote 가 없으면 형식 보존.
        return s
    return f"KRW-{s}"


def to_internal_symbol(upbit_market: str) -> str:
    """업비트 ``QUOTE-BASE`` 형식 → 프로젝트 내부 ``BASE-QUOTE`` 형식.

    예: ``KRW-BTC`` → ``BTC-KRW``, ``USDT-BTC`` → ``BTC-USDT``.
    이미 ``BASE-QUOTE`` 같은 형태로 들어왔다면 그대로 반환.
    """
    if not upbit_market:
        raise ValueError("upbit market is empty")
    s = upbit_market.strip().upper()
    if "-" not in s:
        return s
    a, b = s.split("-", 1)
    # 업비트 시장은 quote-base 라 quote 가 KRW/USDT/BTC 인 경우만 swap.
    if a in {"KRW", "USDT", "BTC"}:
        return f"{b}-{a}"
    return s


def is_krw_market(market: str) -> bool:
    """업비트 시장이 KRW 마켓인지."""
    if not market:
        return False
    s = market.strip().upper()
    return s.startswith("KRW-")


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
        public_client: Any | None = None,
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
        self._public_client = public_client

    @property
    def capability(self) -> AdapterCapability:
        return self._CAP

    @property
    def client(self) -> Any:
        if self._client is None:
            import pyupbit  # type: ignore[import-not-found]   # lazy
            self._client = pyupbit
        return self._client

    @property
    def public_client(self) -> Any | None:
        """UpbitPublicClient 인스턴스 (주입 시). 없으면 legacy pyupbit 경로 사용."""
        return self._public_client

    # ── 심볼 정규화 ───────────────────────────────────────────────

    @staticmethod
    def to_upbit_symbol(symbol: str) -> str:
        """입력 심볼을 Upbit 형식 ``QUOTE-BASE`` (기본 KRW-XXX) 으로 정규화.

        모듈 레벨 ``normalize_upbit_market`` 의 staticmethod alias — 기존 호출자
        호환을 위해 유지한다.
        """
        return normalize_upbit_market(symbol)

    # ── ExchangeAdapter 구현 ──────────────────────────────────────

    def fetch_ticker(self, symbol: str) -> Ticker:
        upbit_symbol = self.to_upbit_symbol(symbol)
        if self._public_client is not None:
            return self._fetch_ticker_via_public_client(symbol, upbit_symbol)
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
        if self._public_client is not None:
            return self._fetch_orderbook_via_public_client(symbol, upbit_symbol, depth)
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

    # ── UpbitPublicClient 경로 ────────────────────────────────────

    def _fetch_ticker_via_public_client(self, symbol: str, upbit_symbol: str) -> Ticker:
        tickers = self._public_client.fetch_ticker([upbit_symbol])
        if not tickers:
            raise RuntimeError(f"Upbit: ticker for {upbit_symbol} unavailable")
        t = tickers[0]
        price = float(t.get("trade_price") or 0)
        if price <= 0:
            raise RuntimeError(f"Upbit: ticker for {upbit_symbol} unavailable")
        # bid/ask 는 orderbook 한 번 더 조회
        obs = self._public_client.fetch_orderbook([upbit_symbol])
        bid = ask = 0.0
        if obs:
            units = obs[0].get("orderbook_units") or []
            if units:
                bid = float(units[0].get("bid_price") or 0)
                ask = float(units[0].get("ask_price") or 0)
        return Ticker(
            symbol=symbol,
            price=price,
            bid=bid, ask=ask,
            spread_pct=((ask - bid) / bid) if bid > 0 else 0.0,
            volume_24h=float(t.get("acc_trade_volume_24h") or 0),
            ts=datetime.now(timezone.utc),
        )

    def _fetch_orderbook_via_public_client(
        self, symbol: str, upbit_symbol: str, depth: int,
    ) -> OrderBook:
        obs = self._public_client.fetch_orderbook([upbit_symbol])
        if not obs:
            return OrderBook(symbol=symbol, bids=(), asks=(),
                             ts=datetime.now(timezone.utc))
        units = (obs[0].get("orderbook_units") or [])[:max(1, int(depth))]
        bids = tuple((float(u["bid_price"]), float(u["bid_size"])) for u in units)
        asks = tuple((float(u["ask_price"]), float(u["ask_size"])) for u in units)
        return OrderBook(symbol=symbol, bids=bids, asks=asks,
                         ts=datetime.now(timezone.utc))
