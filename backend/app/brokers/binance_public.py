"""BinancePublicClient — 체크리스트 #23.

Binance Spot public market data 만 사용하는 read-only client.

지원 메서드 (모두 public endpoint — 인증 불필요, IP 기준 weight rate limit):
  - fetch_exchange_info(symbol=None)              GET /api/v3/exchangeInfo
  - fetch_ticker(symbol)                          GET /api/v3/ticker/24hr
  - fetch_orderbook(symbol, limit=100)            GET /api/v3/depth
  - fetch_klines(symbol, interval="1m", limit)    GET /api/v3/klines
  - fetch_server_time()                           GET /api/v3/time

설계 원칙 (CLAUDE.md §2.1.2 / §2.5):
  - **Binance Spot public market data endpoint 만 호출한다.** account / order /
    futures / margin endpoint URL 은 본 모듈에 존재하지 않는다.
  - HTTP 전송은 ``transport`` 콜러블 추상화 — 테스트는 FakeTransport 로 네트워크 0.
  - production 사용 시에도 transport 가 없으면 ``RuntimeError`` (silent network 금지).
  - production 기본 host 는 ``data-api.binance.vision`` (public market data 전용) 을
    *선호*. caller 가 transport 안에서 base URL 을 자유롭게 선택하지만 본 client 는
    path 만 만들고 host 결정에는 관여하지 않는다.
  - response parsing 분리 — 단위 테스트 용이.
  - X-MBX-USED-WEIGHT 헤더는 ``BinanceRateLimitState`` 가 별도 보관.

**규제 / 지역 제한 (CLAUDE.md §2.4 / §2.6)**:
  Binance 서비스 가능 지역과 약관은 변동 가능. 본 단계에서는 read-only public market
  data 만 다루지만, 실제 live/trading 활성화는 별도 규제·지역 제한 확인 + 별도 phase
  승격 절차 통과 후에만 가능 (본 모듈에는 trading endpoint 가 없다).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .binance_rate_limit import BinanceRateLimitState


# Binance public market data 전용 host. 인증/주문 endpoint 와 분리된 호스트.
# 실제 host 선택은 transport 가 책임 — 본 module 은 path 만 만들며 host 에 관여하지 않는다.
# (그러나 권장 host 를 문서화: production transport 가 이 값을 사용하는 것을 권장.)
BINANCE_PUBLIC_DATA_HOST: str = "data-api.binance.vision"

# 본 client 가 허용하는 path. private/account/order/margin/futures path 절대 없음.
_PUBLIC_PATHS_EXACT: tuple[str, ...] = (
    "/api/v3/exchangeInfo",
    "/api/v3/ticker/24hr",
    "/api/v3/depth",
    "/api/v3/klines",
    "/api/v3/time",
    "/api/v3/avgPrice",
    "/api/v3/ticker/price",
    "/api/v3/ticker/bookTicker",
)


@dataclass(frozen=True)
class BinanceTransportResponse:
    """transport 표준 응답."""

    status_code: int
    body: Any
    headers: dict[str, str]


class BinanceTransportFn(Protocol):
    def __call__(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> BinanceTransportResponse:
        ...


class BinancePublicAPIError(RuntimeError):
    """Binance public API 가 비정상 응답을 돌려준 경우."""


# 허용 kline interval.
ALLOWED_KLINE_INTERVALS: frozenset[str] = frozenset({
    "1s", "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
})


class BinancePublicClient:
    """Binance Spot 공개 market data client.

    네트워크 호출은 caller 가 주입한 transport 에 위임. production transport 는 본
    단계에서 추가하지 않으며, 테스트는 FakeTransport 로 동작.

    path 화이트리스트(``_assert_public_path``) 가 모든 호출에 적용 — private/order/
    margin/futures endpoint 가 본 client 로 우회되지 않는다.
    """

    def __init__(
        self,
        transport: BinanceTransportFn | None = None,
        *,
        rate_limit: BinanceRateLimitState | None = None,
    ):
        self._transport = transport
        self.rate_limit: BinanceRateLimitState = rate_limit or BinanceRateLimitState()

    # ── 공통 호출 ─────────────────────────────────────────────────

    def _call(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self._transport is None:
            raise RuntimeError(
                "BinancePublicClient: transport is not configured. "
                "Inject a transport (production: httpx/requests; tests: FakeTransport). "
                "본 client 는 silent 네트워크 호출을 하지 않는다."
            )
        _assert_public_path(path)
        resp = self._transport("GET", path, params or {}, {})
        if not isinstance(resp, BinanceTransportResponse):
            raise BinancePublicAPIError(
                f"transport returned non-standard response: {type(resp).__name__}"
            )
        # used weight 갱신 (헤더 있을 때만).
        self.rate_limit.update(resp.headers)
        if resp.status_code >= 400:
            # Binance error body: {"code": -1003, "msg": "Too many requests"}
            raise BinancePublicAPIError(
                f"binance public {path} status={resp.status_code} body={resp.body!r}"
            )
        return resp.body

    # ── public endpoints ───────────────────────────────────────────

    def fetch_server_time(self) -> int:
        """``GET /api/v3/time`` — 서버 시각 (ms)."""
        body = self._call("/api/v3/time", None)
        if isinstance(body, dict) and "serverTime" in body:
            try:
                return int(body["serverTime"])
            except (TypeError, ValueError):
                pass
        raise BinancePublicAPIError(f"unexpected serverTime body: {body!r}")

    def fetch_exchange_info(self, symbol: str | None = None) -> list[dict]:
        """``GET /api/v3/exchangeInfo`` — 마켓 카탈로그.

        symbol 이 주어지면 그 symbol 만 필터. native 형식(BTCUSDT) 으로 요청.
        """
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = _require_symbol(symbol)
        body = self._call("/api/v3/exchangeInfo", params)
        return _parse_exchange_info(body)

    def fetch_ticker(self, symbol: str) -> dict | None:
        """``GET /api/v3/ticker/24hr`` — 24h 통계 + 현재가."""
        sym = _require_symbol(symbol)
        body = self._call("/api/v3/ticker/24hr", {"symbol": sym})
        return _parse_ticker(body)

    def fetch_orderbook(self, symbol: str, limit: int = 100) -> dict:
        """``GET /api/v3/depth?symbol=...&limit=...``.

        Binance 가 허용하는 limit: 5,10,20,50,100,500,1000,5000.
        """
        sym = _require_symbol(symbol)
        if int(limit) not in (5, 10, 20, 50, 100, 500, 1000, 5000):
            raise ValueError(f"unsupported orderbook limit: {limit}")
        body = self._call("/api/v3/depth", {"symbol": sym, "limit": int(limit)})
        return _parse_orderbook(body, depth=int(limit))

    def fetch_klines(
        self,
        symbol: str,
        interval: str = "1m",
        limit: int = 100,
    ) -> list[dict]:
        """``GET /api/v3/klines?symbol=...&interval=...&limit=...``."""
        sym = _require_symbol(symbol)
        if interval not in ALLOWED_KLINE_INTERVALS:
            raise ValueError(f"unsupported kline interval: {interval!r}")
        if not (1 <= int(limit) <= 1000):
            raise ValueError("limit must be in 1..1000")
        body = self._call(
            "/api/v3/klines",
            {"symbol": sym, "interval": interval, "limit": int(limit)},
        )
        return _parse_klines(body)


# ── 검증 헬퍼 ────────────────────────────────────────────────────


def _assert_public_path(path: str) -> None:
    """본 client 는 public path 만 호출 — 화이트리스트 강제."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise BinancePublicAPIError(f"invalid path: {path!r}")
    if path not in _PUBLIC_PATHS_EXACT:
        raise BinancePublicAPIError(
            f"non-public path rejected by BinancePublicClient: {path!r}. "
            "private/account/order/margin/futures endpoints are not allowed."
        )


def _require_symbol(symbol: str) -> str:
    if not symbol or not isinstance(symbol, str):
        raise ValueError("symbol is required")
    s = symbol.strip().upper()
    if not s:
        raise ValueError("symbol is empty")
    # Binance native 형식: 영문 대문자 + 숫자만. 슬래시 / 대시는 허용 안 함.
    if "/" in s or "-" in s:
        raise ValueError(
            f"invalid Binance native symbol: {symbol!r} (expected e.g. 'BTCUSDT')"
        )
    if not s.isalnum():
        raise ValueError(f"invalid Binance symbol: {symbol!r}")
    return s


# ── response parsers ─────────────────────────────────────────────


def _parse_exchange_info(body: Any) -> list[dict]:
    if not isinstance(body, dict):
        return []
    symbols = body.get("symbols") or []
    if not isinstance(symbols, list):
        return []
    out: list[dict] = []
    for item in symbols:
        if not isinstance(item, dict):
            continue
        out.append({
            "symbol":       str(item.get("symbol") or "").upper(),
            "status":       str(item.get("status") or ""),
            "base_asset":   str(item.get("baseAsset") or "").upper(),
            "quote_asset":  str(item.get("quoteAsset") or "").upper(),
            "is_spot_trading_allowed": bool(item.get("isSpotTradingAllowed") or False),
        })
    return out


def _parse_ticker(body: Any) -> dict | None:
    if not isinstance(body, dict):
        return None
    return {
        "symbol":      str(body.get("symbol") or "").upper(),
        "last_price":  float(body.get("lastPrice") or 0),
        "bid_price":   float(body.get("bidPrice") or 0),
        "ask_price":   float(body.get("askPrice") or 0),
        "bid_qty":     float(body.get("bidQty") or 0),
        "ask_qty":     float(body.get("askQty") or 0),
        "high_price":  float(body.get("highPrice") or 0),
        "low_price":   float(body.get("lowPrice") or 0),
        "volume":      float(body.get("volume") or 0),
        "quote_volume": float(body.get("quoteVolume") or 0),
        "open_time":   int(body.get("openTime") or 0),
        "close_time":  int(body.get("closeTime") or 0),
    }


def _parse_orderbook(body: Any, *, depth: int) -> dict:
    if not isinstance(body, dict):
        return {"bids": [], "asks": [], "last_update_id": 0}
    bids_raw = body.get("bids") or []
    asks_raw = body.get("asks") or []
    bids: list[list[float]] = []
    for row in bids_raw[:depth]:
        if isinstance(row, list) and len(row) >= 2:
            try:
                bids.append([float(row[0]), float(row[1])])
            except (TypeError, ValueError):
                continue
    asks: list[list[float]] = []
    for row in asks_raw[:depth]:
        if isinstance(row, list) and len(row) >= 2:
            try:
                asks.append([float(row[0]), float(row[1])])
            except (TypeError, ValueError):
                continue
    return {
        "bids":           bids,
        "asks":           asks,
        "last_update_id": int(body.get("lastUpdateId") or 0),
    }


def _parse_klines(body: Any) -> list[dict]:
    """Binance kline: ``[openTime, open, high, low, close, volume, closeTime, ...]``."""
    if not isinstance(body, list):
        return []
    out: list[dict] = []
    for row in body:
        if not isinstance(row, list) or len(row) < 7:
            continue
        try:
            out.append({
                "open_time":   int(row[0]),
                "open":        float(row[1]),
                "high":        float(row[2]),
                "low":         float(row[3]),
                "close":       float(row[4]),
                "volume":      float(row[5]),
                "close_time":  int(row[6]),
                "quote_volume": float(row[7]) if len(row) >= 8 else 0.0,
                "trades":      int(row[8]) if len(row) >= 9 else 0,
            })
        except (TypeError, ValueError):
            continue
    return out


__all__ = (
    "BINANCE_PUBLIC_DATA_HOST",
    "ALLOWED_KLINE_INTERVALS",
    "BinanceTransportResponse",
    "BinanceTransportFn",
    "BinancePublicClient",
    "BinancePublicAPIError",
)
