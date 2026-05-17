"""UpbitPublicClient — 체크리스트 #21.

업비트 공개 quotation API 만 사용하는 read-only client.

지원 메서드 (모두 public endpoint):
  - fetch_markets()                                   GET /v1/market/all
  - fetch_ticker(markets)                             GET /v1/ticker
  - fetch_orderbook(markets)                          GET /v1/orderbook
  - fetch_candles_minutes(market, unit, count)        GET /v1/candles/minutes/{unit}
  - fetch_trades_ticks(market, count)                 GET /v1/trades/ticks

설계 원칙 (CLAUDE.md §2.1.2 / §2.5):
  - **본 client 는 public quotation endpoint 만 호출한다.** private/account/order
    endpoint URL 은 본 모듈에 존재하지 않는다.
  - HTTP 전송은 ``transport`` 콜러블을 통해 추상화 — 테스트는 ``FakeTransport`` 로
    네트워크 호출 0.
  - production 사용 시에도 transport 가 없으면 ``RuntimeError`` (silent network
    호출 금지). 명시적으로 transport 를 주입해야 한다.
  - Remaining-Req 헤더는 ``RateLimitState`` 로 위임. sleep 은 caller 결정.
  - response parsing 은 별도 함수로 분리 — 단위 테스트 용이.
  - 출금/이체 endpoint 사용 금지 (영구).

TransportFn 시그니처:
    Callable[[method, path, params, headers], TransportResponse]

    - method: "GET" 만 사용 (본 client 는 GET 외 호출하지 않는다)
    - path:   "/v1/ticker" 같은 상대 path
    - params: 쿼리 dict
    - headers: 요청 헤더 (현재 비어 있음 — public endpoint)
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from .upbit_rate_limit import RateLimitState


# 공개 quotation host. 실제 호출은 transport 책임 — 본 client 는 path 만 만든다.
UPBIT_PUBLIC_BASE_URL = "https://api.upbit.com"

# 본 모듈이 인식하는 path 화이트리스트. private/account/order path 는 절대 없음.
_PUBLIC_PATHS: tuple[str, ...] = (
    "/v1/market/all",
    "/v1/ticker",
    "/v1/orderbook",
    "/v1/candles/minutes/",   # prefix — 뒤에 unit
    "/v1/trades/ticks",
)


@dataclass(frozen=True)
class TransportResponse:
    """transport 가 반환해야 하는 최소 표준 응답."""

    status_code: int
    body: Any
    headers: dict[str, str]


class TransportFn(Protocol):
    def __call__(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> TransportResponse:
        ...


class UpbitPublicAPIError(RuntimeError):
    """Upbit public API 가 비정상 응답을 돌려준 경우."""


class UpbitPublicClient:
    """업비트 공개 quotation client.

    네트워크 호출은 caller 가 주입한 transport 에 위임한다. 본 단계에서는 production
    transport 코드를 추가하지 않으며, 테스트는 ``FakeTransport`` 로 동작한다.

    실제 production transport (httpx/requests) 는 별도 PR 에서 추가하며 그 때도
    public path 화이트리스트만 통과해야 한다 (가드: ``_assert_public_path``).
    """

    def __init__(
        self,
        transport: TransportFn | None = None,
        *,
        rate_limit: RateLimitState | None = None,
    ):
        self._transport = transport
        self.rate_limit: RateLimitState = rate_limit or RateLimitState()

    # ── 공통 호출 ─────────────────────────────────────────────────

    def _call(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if self._transport is None:
            raise RuntimeError(
                "UpbitPublicClient: transport is not configured. "
                "Inject a transport (production: httpx/requests; tests: FakeTransport). "
                "본 client 는 silent 네트워크 호출을 하지 않는다."
            )
        _assert_public_path(path)
        resp = self._transport("GET", path, params or {}, {})
        if not isinstance(resp, TransportResponse):
            raise UpbitPublicAPIError(
                f"transport returned non-standard response: {type(resp).__name__}"
            )
        # Remaining-Req 갱신 (있을 때만)
        rr = resp.headers.get("Remaining-Req") or resp.headers.get("remaining-req")
        if rr:
            self.rate_limit.update(rr)
        if resp.status_code >= 400:
            raise UpbitPublicAPIError(
                f"upbit public {path} status={resp.status_code} body={resp.body!r}"
            )
        return resp.body

    # ── public quotation endpoints ────────────────────────────────

    def fetch_markets(self) -> list[dict]:
        """``GET /v1/market/all`` — KRW/BTC/USDT 마켓 카탈로그."""
        body = self._call("/v1/market/all", {"isDetails": "false"})
        return _parse_markets(body)

    def fetch_ticker(self, markets: Sequence[str]) -> list[dict]:
        """``GET /v1/ticker?markets=...`` — 다중 마켓 현재가."""
        markets_arg = _normalize_markets_arg(markets)
        body = self._call("/v1/ticker", {"markets": markets_arg})
        return _parse_ticker(body)

    def fetch_orderbook(self, markets: Sequence[str]) -> list[dict]:
        """``GET /v1/orderbook?markets=...`` — 다중 마켓 호가."""
        markets_arg = _normalize_markets_arg(markets)
        body = self._call("/v1/orderbook", {"markets": markets_arg})
        return _parse_orderbook(body)

    def fetch_candles_minutes(
        self,
        market: str,
        unit: int = 1,
        count: int = 200,
    ) -> list[dict]:
        """``GET /v1/candles/minutes/{unit}?market=...&count=...``."""
        if unit not in (1, 3, 5, 10, 15, 30, 60, 240):
            raise ValueError(f"unsupported candle unit: {unit}")
        if not (1 <= int(count) <= 200):
            raise ValueError("count must be in 1..200")
        path = f"/v1/candles/minutes/{int(unit)}"
        body = self._call(path, {"market": market, "count": int(count)})
        return _parse_candles_minutes(body)

    def fetch_trades_ticks(
        self,
        market: str,
        count: int = 100,
    ) -> list[dict]:
        """``GET /v1/trades/ticks?market=...&count=...``."""
        if not (1 <= int(count) <= 500):
            raise ValueError("count must be in 1..500")
        body = self._call("/v1/trades/ticks", {"market": market, "count": int(count)})
        return _parse_trades_ticks(body)


# ── 검증 헬퍼 ────────────────────────────────────────────────────


def _assert_public_path(path: str) -> None:
    """본 client 는 public path 만 호출한다 — production transport 가 우회하지 못하도록 강제.

    private/account/order endpoint 가 실수로 들어오면 즉시 차단.
    """
    if not isinstance(path, str) or not path.startswith("/"):
        raise UpbitPublicAPIError(f"invalid path: {path!r}")
    for prefix in _PUBLIC_PATHS:
        if prefix.endswith("/"):
            if path.startswith(prefix):
                return
        else:
            if path == prefix:
                return
    raise UpbitPublicAPIError(
        f"non-public path rejected by UpbitPublicClient: {path!r}. "
        "private/account/order endpoints are not allowed in this client."
    )


def _normalize_markets_arg(markets: Sequence[str]) -> str:
    out: list[str] = []
    for m in markets or []:
        m2 = (m or "").strip().upper()
        if not m2:
            continue
        # 외부 호출 단계에서 KRW-BTC 형식만 받는다 — 변환은 UpbitAdapter 가 책임.
        if "-" not in m2:
            raise ValueError(f"invalid upbit market: {m!r} (expected 'QUOTE-BASE')")
        out.append(m2)
    if not out:
        raise ValueError("at least one market is required")
    return ",".join(out)


# ── response parsers ─────────────────────────────────────────────


def _parse_markets(body: Any) -> list[dict]:
    if not isinstance(body, list):
        raise UpbitPublicAPIError(f"unexpected markets body: {type(body).__name__}")
    out: list[dict] = []
    for item in body:
        if not isinstance(item, dict):
            continue
        market = str(item.get("market") or "").strip().upper()
        if not market or "-" not in market:
            continue
        out.append({
            "market":         market,
            "korean_name":    str(item.get("korean_name") or ""),
            "english_name":   str(item.get("english_name") or ""),
            "market_warning": str(item.get("market_warning") or "NONE"),
        })
    return out


def _parse_ticker(body: Any) -> list[dict]:
    if not isinstance(body, list):
        raise UpbitPublicAPIError(f"unexpected ticker body: {type(body).__name__}")
    out: list[dict] = []
    for item in body:
        if not isinstance(item, dict):
            continue
        out.append({
            "market":             str(item.get("market") or "").upper(),
            "trade_price":        float(item.get("trade_price") or 0),
            "trade_volume":       float(item.get("trade_volume") or 0),
            "acc_trade_volume_24h": float(item.get("acc_trade_volume_24h") or 0),
            "high_price":         float(item.get("high_price") or 0),
            "low_price":          float(item.get("low_price") or 0),
            "timestamp":          int(item.get("timestamp") or 0),
        })
    return out


def _parse_orderbook(body: Any) -> list[dict]:
    if not isinstance(body, list):
        raise UpbitPublicAPIError(f"unexpected orderbook body: {type(body).__name__}")
    out: list[dict] = []
    for item in body:
        if not isinstance(item, dict):
            continue
        units_raw = item.get("orderbook_units") or []
        units: list[dict] = []
        for u in units_raw:
            if not isinstance(u, dict):
                continue
            units.append({
                "ask_price": float(u.get("ask_price") or 0),
                "bid_price": float(u.get("bid_price") or 0),
                "ask_size":  float(u.get("ask_size")  or 0),
                "bid_size":  float(u.get("bid_size")  or 0),
            })
        out.append({
            "market":           str(item.get("market") or "").upper(),
            "orderbook_units":  units,
            "total_ask_size":   float(item.get("total_ask_size") or 0),
            "total_bid_size":   float(item.get("total_bid_size") or 0),
            "timestamp":        int(item.get("timestamp") or 0),
        })
    return out


def _parse_candles_minutes(body: Any) -> list[dict]:
    if not isinstance(body, list):
        raise UpbitPublicAPIError(f"unexpected candles body: {type(body).__name__}")
    out: list[dict] = []
    for item in body:
        if not isinstance(item, dict):
            continue
        out.append({
            "market":               str(item.get("market") or "").upper(),
            "candle_date_time_utc": str(item.get("candle_date_time_utc") or ""),
            "opening_price":        float(item.get("opening_price") or 0),
            "high_price":           float(item.get("high_price") or 0),
            "low_price":            float(item.get("low_price") or 0),
            "trade_price":          float(item.get("trade_price") or 0),
            "candle_acc_trade_volume": float(item.get("candle_acc_trade_volume") or 0),
            "timestamp":            int(item.get("timestamp") or 0),
            "unit":                 int(item.get("unit") or 0),
        })
    return out


def _parse_trades_ticks(body: Any) -> list[dict]:
    if not isinstance(body, list):
        raise UpbitPublicAPIError(f"unexpected trades_ticks body: {type(body).__name__}")
    out: list[dict] = []
    for item in body:
        if not isinstance(item, dict):
            continue
        out.append({
            "market":            str(item.get("market") or "").upper(),
            "trade_price":       float(item.get("trade_price") or 0),
            "trade_volume":      float(item.get("trade_volume") or 0),
            "ask_bid":           str(item.get("ask_bid") or ""),
            "trade_date_utc":    str(item.get("trade_date_utc") or ""),
            "trade_time_utc":    str(item.get("trade_time_utc") or ""),
            "timestamp":         int(item.get("timestamp") or 0),
        })
    return out


__all__ = (
    "UPBIT_PUBLIC_BASE_URL",
    "TransportResponse",
    "TransportFn",
    "UpbitPublicClient",
    "UpbitPublicAPIError",
)
