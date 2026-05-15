"""BinanceAdapter — Binance READ_ONLY 공개 API 어댑터 (체크리스트 #23).

ccxt unified API 의 Binance 공개 spot endpoint 만 사용. API 키 불필요, 주문 불가.

설계 원칙 (CLAUDE.md §2.1.2 / §4.3 / §28):
  - mode='READ_ONLY' 영구 고정. API 키를 받지 않음 (생성자에서 raise).
  - 주문/잔고 capability=False — 호출 시 ExchangeAdapterDisabledError.
  - ccxt lazy import — 테스트에서 client 주입 가능 (네트워크 호출 0).
  - 출금 메서드 정의 금지 (영구).
  - spot only — 선물(USDM/COINM)은 #67 Futures Scope 에서 별도 어댑터로.

사용:
    a = BinanceAdapter()
    a.fetch_ticker("BTC")          # → Ticker (BTC/USDT 정규화)
    a.fetch_orderbook("ETH/USDT")  # → OrderBook
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Protocol

from app.schemas import Ticker, OrderBook

from .base import ExchangeAdapter, AdapterCapability


class CcxtBinanceClientProtocol(Protocol):
    """ccxt.binance 인스턴스의 최소 인터페이스 (테스트 주입용)."""

    def fetch_ticker(self, symbol: str) -> dict: ...
    def fetch_order_book(self, symbol: str, limit: int = 5) -> dict: ...


class BinanceAdapter(ExchangeAdapter):
    """Binance 공개 spot 시세/호가 read-only 어댑터.

    실제 네트워크 호출 발생. 테스트는 ``client=...`` 인자로 fake 주입.
    """

    _CAP = AdapterCapability(
        name="binance",
        mode="READ_ONLY",
        can_fetch_ticker    = True,
        can_fetch_orderbook = True,
        can_fetch_balance   = False,
        can_place_order     = False,
        can_cancel_order    = False,
        supports_futures    = False,    # spot only — 선물은 별도 어댑터 (#67)
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
                "BinanceAdapter 는 READ_ONLY 모드 전용 — API 키 사용 금지. "
                "주문 기능은 별도 LIVE 어댑터에서 구현하며, 그때도 출금 권한 키는 절대 사용하지 않는다."
            )
        self._client = client

    @property
    def capability(self) -> AdapterCapability:
        return self._CAP

    @property
    def client(self) -> Any:
        if self._client is None:
            import ccxt  # type: ignore[import-not-found]   # lazy
            self._client = ccxt.binance({"enableRateLimit": True})
        return self._client

    # ── 심볼 정규화 ───────────────────────────────────────────────

    @staticmethod
    def to_binance_symbol(symbol: str) -> str:
        """입력 심볼을 ccxt unified 형식 'BASE/QUOTE' 로 정규화.

        지원:
          - "BTC"          → "BTC/USDT" (USDT default quote)
          - "BTC/USDT"     → "BTC/USDT"
          - "BTC-USDT"     → "BTC/USDT"
          - "BTCUSDT"      → "BTC/USDT" (Binance native 형식, 4글자 quote 추정)
          - "ETH/BTC"      → "ETH/BTC"  (다른 quote 그대로 유지)
        """
        s = symbol.upper().strip()
        if "/" in s:
            return s
        if "-" in s:
            base, quote = s.split("-", 1)
            return f"{base}/{quote}"
        # Binance native 형식 (예: BTCUSDT) — 흔한 quote 후미를 분리
        for quote in ("USDT", "USDC", "BUSD", "BTC", "ETH", "BNB", "TUSD", "FDUSD"):
            if s.endswith(quote) and len(s) > len(quote):
                return f"{s[:-len(quote)]}/{quote}"
        # 분리 불가 → 기본 USDT pair 추정
        return f"{s}/USDT"

    # ── ExchangeAdapter 구현 ──────────────────────────────────────

    def fetch_ticker(self, symbol: str) -> Ticker:
        binance_symbol = self.to_binance_symbol(symbol)
        raw = self.client.fetch_ticker(binance_symbol)
        if not raw:
            raise RuntimeError(f"Binance: ticker for {binance_symbol} unavailable")

        bid = float(raw.get("bid") or 0.0)
        ask = float(raw.get("ask") or 0.0)
        price = float(raw.get("last") or raw.get("close") or 0.0)
        volume_quote = float(raw.get("quoteVolume") or 0.0)

        return Ticker(
            symbol=symbol,
            price=price,
            bid=bid,
            ask=ask,
            spread_pct=((ask - bid) / bid) if bid > 0 else 0.0,
            volume_24h=volume_quote,
            ts=self._parse_ts(raw),
        )

    def fetch_orderbook(self, symbol: str, depth: int = 5) -> OrderBook:
        binance_symbol = self.to_binance_symbol(symbol)
        raw = self.client.fetch_order_book(binance_symbol, depth)
        bids_raw = (raw.get("bids") or [])[:depth]
        asks_raw = (raw.get("asks") or [])[:depth]
        bids = tuple((float(p), float(q)) for p, q, *_ in bids_raw)
        asks = tuple((float(p), float(q)) for p, q, *_ in asks_raw)
        return OrderBook(
            symbol=symbol,
            bids=bids, asks=asks,
            ts=self._parse_ts(raw),
        )

    # ── 내부 헬퍼 ─────────────────────────────────────────────────

    @staticmethod
    def _parse_ts(raw: dict) -> datetime:
        """ccxt timestamp(ms) → timezone-aware UTC datetime. 누락 시 now."""
        ts_ms = raw.get("timestamp")
        if ts_ms is None:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return datetime.now(timezone.utc)
