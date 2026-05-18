"""BinanceAdapter — Binance READ_ONLY 공개 API 어댑터 (체크리스트 #23).

ccxt unified API 의 Binance 공개 spot endpoint 만 사용. API 키 불필요, 주문 불가.

설계 원칙 (CLAUDE.md §2.1.2 / §4.3 / §28):
  - mode='READ_ONLY' 영구 고정. API 키를 받지 않음 (생성자에서 raise).
  - 주문/잔고 capability=False — 호출 시 ExchangeAdapterDisabledError.
  - ccxt lazy import — 테스트에서 client 주입 가능 (네트워크 호출 0).
  - 출금 메서드 정의 금지 (영구).
  - spot only — 선물(USDM/COINM)은 #67 Futures Scope 에서 별도 어댑터로.

**규제/지역 제한 (CLAUDE.md §2.4 / §2.6)**:
  Binance 는 해외 유동성 비교용 2차 후보 거래소다. 본 어댑터는 read-only 조사/
  스켈레톤 단계이며, live/trading 활성화는 별도 phase + 별도 규제·지역 제한 확인 +
  별도 LIVE adapter 추가 후에만 가능. 본 adapter / 보조 모듈에 trade endpoint 코드를
  추가하지 않는다.

체크리스트 #23 보강 (2026-05-18):
  - 모듈 레벨 헬퍼: ``normalize_binance_symbol``, ``to_internal_symbol``,
    ``is_supported_binance_quote``.
  - ``public_client`` (BinancePublicClient) 주입 옵션 — production transport 기반
    경로. legacy ``client`` (ccxt 호환) 도 그대로 유지.

사용:
    # 1) legacy ccxt
    a = BinanceAdapter()
    a.fetch_ticker("BTC")          # → Ticker (BTC/USDT 정규화)

    # 2) 신규 — BinancePublicClient + transport 주입
    from app.brokers.binance_public import BinancePublicClient
    pc = BinancePublicClient(transport=my_transport)
    a = BinanceAdapter(public_client=pc)
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Protocol

from app.schemas import Ticker, OrderBook

from .base import ExchangeAdapter, AdapterCapability


# ── 모듈 레벨 심볼 헬퍼 ───────────────────────────────────────────


# Binance native 형식에서 흔히 등장하는 quote 후미 — 분리 우선순위 순.
_BINANCE_NATIVE_QUOTES: tuple[str, ...] = (
    "USDT", "USDC", "BUSD", "TUSD", "FDUSD", "BTC", "ETH", "BNB",
)


def normalize_binance_symbol(symbol: str) -> str:
    """입력 심볼을 Binance native 형식 (`BTCUSDT`) 으로 정규화.

    지원:
      - "BTC"          → "BTCUSDT" (기본 quote USDT)
      - "btc"          → "BTCUSDT"
      - "BTC-USDT"     → "BTCUSDT"
      - "BTC/USDT"     → "BTCUSDT"
      - "BTCUSDT"      → "BTCUSDT"
      - "btcusdt"      → "BTCUSDT"

    Futures/Perp 표기 (e.g. "BTCUSDT-PERP", "BTCUSDT_PERP") 는 본 단계 미지원 →
    ValueError. 공백/빈 문자열/None 도 ValueError.
    """
    if symbol is None:
        raise ValueError("binance symbol is None")
    s = str(symbol).strip().upper()
    if not s:
        raise ValueError("binance symbol is empty")
    # Futures/Perp 후미 — 본 단계 미지원
    if "-PERP" in s or "_PERP" in s or s.endswith("-PERP") or s.endswith("_PERP"):
        raise ValueError(f"Binance futures/perp not supported in this step: {symbol!r}")
    has_separator = "/" in s or "-" in s
    if "/" in s:
        s = s.replace("/", "")
    if "-" in s:
        s = s.replace("-", "")
    if not s.isalnum():
        raise ValueError(f"invalid Binance symbol: {symbol!r}")
    # 구분자(`-` / `/`) 가 없는 입력 중 알려진 quote 후미가 없으면 USDT 기본 결합.
    if not has_separator:
        has_known_quote = any(
            s.endswith(q) and len(s) > len(q)
            for q in _BINANCE_NATIVE_QUOTES
        )
        if not has_known_quote:
            return f"{s}USDT"
    return s


def to_internal_symbol(binance_symbol: str) -> str:
    """Binance native (`BTCUSDT`) → 내부 형식 (`BTC-USDT`).

    알려진 quote 후미 (`_BINANCE_NATIVE_QUOTES`) 만 분리. 분리 불가 시 입력 그대로.
    """
    if not binance_symbol:
        raise ValueError("binance symbol is empty")
    s = binance_symbol.strip().upper()
    if "-" in s:
        return s  # 이미 내부 형식
    if "/" in s:
        return s.replace("/", "-")
    for quote in _BINANCE_NATIVE_QUOTES:
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[:-len(quote)]}-{quote}"
    return s


def is_supported_binance_quote(
    symbol: str,
    allowed_quotes: list[str] | None = None,
) -> bool:
    """심볼의 quote 가 허용 quote 집합에 속하는지.

    기본 허용 quote: USDT, USDC, BTC, ETH.
    """
    allowed = {q.upper() for q in (allowed_quotes or ["USDT", "USDC", "BTC", "ETH"])}
    if not symbol:
        return False
    s = symbol.strip().upper()
    # native 또는 dash/slash 형식 모두 처리
    if "-" in s or "/" in s:
        internal = s.replace("/", "-")
        parts = internal.split("-")
        if len(parts) != 2:
            return False
        return parts[1] in allowed
    # native 형식: 알려진 quote 후미를 가진 경우만 인식
    for quote in _BINANCE_NATIVE_QUOTES:
        if s.endswith(quote) and len(s) > len(quote):
            return quote in allowed
    return False


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
        public_client: Any | None = None,
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
        self._public_client = public_client

    @property
    def capability(self) -> AdapterCapability:
        return self._CAP

    @property
    def client(self) -> Any:
        if self._client is None:
            import ccxt  # type: ignore[import-not-found]   # lazy
            self._client = ccxt.binance({"enableRateLimit": True})
        return self._client

    @property
    def public_client(self) -> Any | None:
        """BinancePublicClient 인스턴스 (주입 시). 없으면 legacy ccxt 경로."""
        return self._public_client

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
        if self._public_client is not None:
            return self._fetch_ticker_via_public_client(symbol)
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
        if self._public_client is not None:
            return self._fetch_orderbook_via_public_client(symbol, depth)
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

    # ── BinancePublicClient 경로 ──────────────────────────────────

    def _fetch_ticker_via_public_client(self, symbol: str) -> Ticker:
        native = normalize_binance_symbol(symbol)
        tk = self._public_client.fetch_ticker(native)
        if not tk:
            raise RuntimeError(f"Binance: ticker for {native} unavailable")
        price = float(tk.get("last_price") or 0)
        bid = float(tk.get("bid_price") or 0)
        ask = float(tk.get("ask_price") or 0)
        ts_ms = int(tk.get("close_time") or tk.get("open_time") or 0)
        ts = (
            datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            if ts_ms > 0 else datetime.now(timezone.utc)
        )
        return Ticker(
            symbol=symbol,
            price=price,
            bid=bid, ask=ask,
            spread_pct=((ask - bid) / bid) if bid > 0 else 0.0,
            volume_24h=float(tk.get("quote_volume") or 0),
            ts=ts,
        )

    def _fetch_orderbook_via_public_client(self, symbol: str, depth: int) -> OrderBook:
        native = normalize_binance_symbol(symbol)
        # Binance 가 허용하는 depth 로 클램프 (5/10/20/50/100/500/1000/5000)
        allowed = (5, 10, 20, 50, 100, 500, 1000, 5000)
        d = int(depth)
        if d not in allowed:
            # 가장 가까운 (이상) 허용값으로 클램프
            for a in allowed:
                if a >= d:
                    d = a
                    break
            else:
                d = 5000
        ob = self._public_client.fetch_orderbook(native, limit=d)
        bids_raw = (ob.get("bids") or [])[:depth]
        asks_raw = (ob.get("asks") or [])[:depth]
        bids = tuple((float(p), float(q)) for p, q in bids_raw)
        asks = tuple((float(p), float(q)) for p, q in asks_raw)
        return OrderBook(
            symbol=symbol, bids=bids, asks=asks,
            ts=datetime.now(timezone.utc),
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
