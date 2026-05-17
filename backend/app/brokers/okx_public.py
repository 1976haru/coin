"""OkxPublicClient — 체크리스트 #22.

OKX 공개 market data API 만 사용하는 read-only client.

지원 메서드 (모두 public endpoint, 인증 불필요 — IP 기준 rate limit):
  - fetch_instruments(inst_type="SPOT")           GET /api/v5/public/instruments
  - fetch_ticker(inst_id)                         GET /api/v5/market/ticker
  - fetch_orderbook(inst_id, depth=20)            GET /api/v5/market/books
  - fetch_candles(inst_id, bar="1m", limit=100)   GET /api/v5/market/candles
  - fetch_funding_rate(inst_id)                   GET /api/v5/public/funding-rate

설계 원칙 (CLAUDE.md §2.1.2 / §2.5):
  - **public market data endpoint 만 호출한다.** account / trade endpoint URL 은
    본 모듈에 존재하지 않는다.
  - HTTP 전송은 ``transport`` 콜러블 추상화 — 테스트는 FakeTransport 로 네트워크 0.
  - production 사용 시에도 transport 가 없으면 ``RuntimeError`` (silent network 금지).
  - response parsing 함수 분리 — 단위 테스트 용이.
  - rate limit 응답(code=50011) 은 ``OkxRateLimitState`` 가 별도 보관.
  - 출금/이체 endpoint 사용 금지 (영구).

TransportFn 시그니처:
    Callable[[method, path, params, headers], TransportResponse]

    - method: "GET" 만 사용
    - path:   "/api/v5/market/ticker" 같은 상대 path
    - params: 쿼리 dict
    - headers: 요청 헤더 (현재 빈 dict — public endpoint)
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .okx_rate_limit import (
    OkxApiError, OkxRateLimitState, parse_okx_api_error,
)


OKX_PUBLIC_BASE_URL = "https://www.okx.com"


# 본 client 가 허용하는 path. private / trade / withdraw path 는 절대 없음.
_PUBLIC_PATHS_EXACT: tuple[str, ...] = (
    "/api/v5/public/instruments",
    "/api/v5/market/ticker",
    "/api/v5/market/books",
    "/api/v5/market/candles",
    "/api/v5/public/funding-rate",
)


@dataclass(frozen=True)
class OkxTransportResponse:
    """transport 표준 응답."""

    status_code: int
    body: Any
    headers: dict[str, str]


class OkxTransportFn(Protocol):
    def __call__(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> OkxTransportResponse:
        ...


class OkxPublicAPIError(RuntimeError):
    """OKX public API 가 비정상 응답을 돌려준 경우."""


# 허용 instrument type (본 단계).
ALLOWED_INST_TYPES: frozenset[str] = frozenset({"SPOT", "SWAP", "FUTURES"})

# 허용 candle bar.
ALLOWED_BARS: frozenset[str] = frozenset({
    "1m", "3m", "5m", "15m", "30m",
    "1H", "2H", "4H", "6H", "12H",
    "1D", "1W", "1M",
})


class OkxPublicClient:
    """OKX 공개 market data client.

    네트워크 호출은 caller 가 주입한 transport 에 위임. production transport 는 본
    단계에서 추가하지 않으며, 테스트는 FakeTransport 로 동작한다.

    path 화이트리스트(_assert_public_path) 가 모든 호출에 적용 — private/trade
    endpoint 가 본 client 로 우회되지 않는다.
    """

    def __init__(
        self,
        transport: OkxTransportFn | None = None,
        *,
        rate_limit: OkxRateLimitState | None = None,
    ):
        self._transport = transport
        self.rate_limit: OkxRateLimitState = rate_limit or OkxRateLimitState()

    # ── 공통 호출 ─────────────────────────────────────────────────

    def _call(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self._transport is None:
            raise RuntimeError(
                "OkxPublicClient: transport is not configured. "
                "Inject a transport (production: httpx/requests; tests: FakeTransport). "
                "본 client 는 silent 네트워크 호출을 하지 않는다."
            )
        _assert_public_path(path)
        resp = self._transport("GET", path, params or {}, {})
        if not isinstance(resp, OkxTransportResponse):
            raise OkxPublicAPIError(
                f"transport returned non-standard response: {type(resp).__name__}"
            )
        if resp.status_code >= 400:
            raise OkxPublicAPIError(
                f"okx public {path} status={resp.status_code} body={resp.body!r}"
            )
        err = self.rate_limit.update(resp.body)
        if not err.is_ok:
            # rate-limit 은 caller 가 backoff 적용 후 재시도. 다른 에러는 raise.
            if err.is_rate_limit:
                raise OkxPublicAPIError(
                    f"okx rate limit (code={err.code} msg={err.msg!r}) — "
                    "caller should backoff via rate_limit.maybe_backoff(...)"
                )
            raise OkxPublicAPIError(
                f"okx api error code={err.code} msg={err.msg!r}"
            )
        return err.data

    # ── public endpoints ───────────────────────────────────────────

    def fetch_instruments(self, inst_type: str = "SPOT") -> list[dict]:
        t = (inst_type or "").strip().upper()
        if t not in ALLOWED_INST_TYPES:
            raise ValueError(f"unsupported instrument type: {inst_type!r}")
        data = self._call("/api/v5/public/instruments", {"instType": t})
        return _parse_instruments(data)

    def fetch_ticker(self, inst_id: str) -> dict | None:
        inst = _require_inst_id(inst_id)
        data = self._call("/api/v5/market/ticker", {"instId": inst})
        parsed = _parse_ticker(data)
        return parsed[0] if parsed else None

    def fetch_orderbook(self, inst_id: str, depth: int = 20) -> dict:
        inst = _require_inst_id(inst_id)
        # OKX 는 sz (size) 라는 이름의 depth 파라미터를 사용.
        if not (1 <= int(depth) <= 400):
            raise ValueError("depth must be in 1..400")
        data = self._call(
            "/api/v5/market/books",
            {"instId": inst, "sz": int(depth)},
        )
        parsed = _parse_orderbook(data, depth=int(depth))
        if not parsed:
            return {"inst_id": inst, "bids": [], "asks": [], "timestamp": 0}
        return parsed[0]

    def fetch_candles(
        self,
        inst_id: str,
        bar: str = "1m",
        limit: int = 100,
    ) -> list[dict]:
        inst = _require_inst_id(inst_id)
        if bar not in ALLOWED_BARS:
            raise ValueError(f"unsupported bar: {bar!r}")
        if not (1 <= int(limit) <= 300):
            raise ValueError("limit must be in 1..300")
        data = self._call(
            "/api/v5/market/candles",
            {"instId": inst, "bar": bar, "limit": int(limit)},
        )
        return _parse_candles(data)

    def fetch_funding_rate(self, inst_id: str) -> dict | None:
        inst = _require_inst_id(inst_id)
        # funding rate 는 SWAP/PERP 전용. 잘못된 inst type 은 OKX 가 에러 응답.
        data = self._call("/api/v5/public/funding-rate", {"instId": inst})
        parsed = _parse_funding_rate(data)
        return parsed[0] if parsed else None


# ── 검증 헬퍼 ────────────────────────────────────────────────────


def _assert_public_path(path: str) -> None:
    """본 client 는 public path 만 호출한다 — 화이트리스트 강제."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise OkxPublicAPIError(f"invalid path: {path!r}")
    if path not in _PUBLIC_PATHS_EXACT:
        raise OkxPublicAPIError(
            f"non-public path rejected by OkxPublicClient: {path!r}. "
            "private/account/trade endpoints are not allowed in this client."
        )


def _require_inst_id(inst_id: str) -> str:
    if not inst_id or not isinstance(inst_id, str):
        raise ValueError("inst_id is required")
    s = inst_id.strip().upper()
    if "-" not in s:
        raise ValueError(f"invalid OKX inst_id: {inst_id!r} (expected 'BASE-QUOTE[-SWAP]')")
    return s


# ── response parsers ─────────────────────────────────────────────


def _parse_instruments(data: Any) -> list[dict]:
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        out.append({
            "instId":   str(item.get("instId") or "").upper(),
            "instType": str(item.get("instType") or "").upper(),
            "baseCcy":  str(item.get("baseCcy") or "").upper(),
            "quoteCcy": str(item.get("quoteCcy") or "").upper(),
            "state":    str(item.get("state") or ""),
        })
    return out


def _parse_ticker(data: Any) -> list[dict]:
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        out.append({
            "inst_id":     str(item.get("instId") or "").upper(),
            "last":        float(item.get("last") or 0),
            "bid_px":      float(item.get("bidPx") or 0),
            "ask_px":      float(item.get("askPx") or 0),
            "bid_sz":      float(item.get("bidSz") or 0),
            "ask_sz":      float(item.get("askSz") or 0),
            "vol_ccy_24h": float(item.get("volCcy24h") or 0),
            "vol_24h":     float(item.get("vol24h") or 0),
            "timestamp":   int(item.get("ts") or 0),
        })
    return out


def _parse_orderbook(data: Any, *, depth: int) -> list[dict]:
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        bids_raw = item.get("bids") or []
        asks_raw = item.get("asks") or []
        bids = []
        for row in bids_raw[:depth]:
            if isinstance(row, list) and len(row) >= 2:
                try:
                    bids.append([float(row[0]), float(row[1])])
                except (TypeError, ValueError):
                    continue
        asks = []
        for row in asks_raw[:depth]:
            if isinstance(row, list) and len(row) >= 2:
                try:
                    asks.append([float(row[0]), float(row[1])])
                except (TypeError, ValueError):
                    continue
        out.append({
            "bids":      bids,
            "asks":      asks,
            "timestamp": int(item.get("ts") or 0),
        })
    return out


def _parse_candles(data: Any) -> list[dict]:
    """OKX 캔들 응답: ``[[ts, o, h, l, c, vol, volCcy], ...]``."""
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for row in data:
        if not isinstance(row, list) or len(row) < 6:
            continue
        try:
            out.append({
                "timestamp": int(row[0]),
                "open":      float(row[1]),
                "high":      float(row[2]),
                "low":       float(row[3]),
                "close":     float(row[4]),
                "volume":    float(row[5]),
                "vol_ccy":   float(row[6]) if len(row) >= 7 else 0.0,
            })
        except (TypeError, ValueError):
            continue
    return out


def _parse_funding_rate(data: Any) -> list[dict]:
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        out.append({
            "inst_id":         str(item.get("instId") or "").upper(),
            "funding_rate":    float(item.get("fundingRate") or 0),
            "next_funding_rate": float(item.get("nextFundingRate") or 0),
            "funding_time":    int(item.get("fundingTime") or 0),
            "next_funding_time": int(item.get("nextFundingTime") or 0),
        })
    return out


__all__ = (
    "OKX_PUBLIC_BASE_URL",
    "ALLOWED_INST_TYPES",
    "ALLOWED_BARS",
    "OkxTransportResponse",
    "OkxTransportFn",
    "OkxPublicClient",
    "OkxPublicAPIError",
)
