"""PaperTrader — 체크리스트 #25.

`PaperMarketBroker` 를 상위에서 관리하는 paper-trading 실행 레이어.

역할:
  - paper market source 선택 (mock / upbit_readonly / okx_readonly /
    binance_readonly / kis_readonly_stub).
  - paper mode 시작/중지/리셋.
  - paper order log 보관 (CRUD-lite).
  - PaperStatus 노출 — UI/API 가 paper 상태 표시용.
  - **OrderGateway 를 통해서만 주문이 들어와야 한다는 정책을 지원** — 본 클래스는
    직접 broker.place_order 를 호출하는 helper(`submit_paper_order_via_gateway`)
    를 제공하지만, 실제 주문은 OrderGateway.submit() 를 거친다.

원칙 (CLAUDE.md §2.4):
  - Strategy/Agent 가 직접 호출 금지 (정적 회귀).
  - 실제 거래소 주문 endpoint 호출 코드 부재.
  - LIVE mode 요청은 거부.
  - 모든 응답에 `mode="PAPER"`, `is_real_trade=False`,
    `execution_source="paper_trader"`, `warning`, `fill_quality_warning` 포함.
  - KIS adapter 미구현 — `kis_readonly_stub` 은 disabled stub.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable

from .paper_market_broker import (
    PaperMarketBroker, PaperMarketBrokerConfig, PaperMarketSource,
)


# ── 카탈로그 ─────────────────────────────────────────────────────


AVAILABLE_PAPER_SOURCES: tuple[str, ...] = (
    "mock",
    "upbit_readonly",
    "okx_readonly",
    "binance_readonly",
    "kis_readonly_stub",   # KIS adapter 미구현 — stub
)


_PAPER_WARNING: str = "Paper execution only. Not real profit or real trade."
_FILL_QUALITY_WARNING: str = (
    "Paper fills may differ from live execution (no real market impact, "
    "no real slippage, no real partial fills)."
)


class PaperTraderError(RuntimeError):
    """PaperTrader 호출 시점 정책 위반."""


# ── 로그 / 상태 ──────────────────────────────────────────────────


@dataclass
class PaperOrderLogEntry:
    """단일 paper 주문/체결 이벤트."""

    ts: str
    client_order_id: str
    order_id: str
    symbol: str
    side: str
    order_type: str
    status: str          # FILLED / ACCEPTED / REJECTED / CANCELED
    notional_usdt: float
    filled_price: float
    qty: float
    fee_usdt: float
    slippage_pct: float
    reason: str
    source_name: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PaperStatus:
    """PaperTrader 의 현재 상태 (UI/API 표시용)."""

    running: bool = False
    source_name: str = "mock"
    started_at: str | None = None
    stopped_at: str | None = None
    last_order_at: str | None = None
    last_market_at: str | None = None
    orders_submitted: int = 0
    orders_filled: int = 0
    orders_rejected: int = 0
    orders_canceled: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["warnings"] = list(self.warnings)
        return d


# ── source factory ───────────────────────────────────────────────


# Optional source factory — 외부에서 주입 가능 (테스트는 fake 주입).
SourceFactoryFn = Callable[[str], PaperMarketSource | None]


def _default_source_factory(name: str) -> PaperMarketSource | None:
    """기본 source 팩토리.

    - "mock" → ``MockExchangeAdapter`` (외부 호출 0).
    - 다른 거래소는 본 단계에서 *명시적으로 주입* 받지 않으면 사용 불가 —
      네트워크 호출이 발생할 수 있는 source 를 임의로 생성하지 않는다.
    - "kis_readonly_stub" → 항상 None (KIS adapter 미구현).
    """
    n = (name or "").lower()
    if n == "mock":
        from .mock_broker import MockExchangeAdapter
        return MockExchangeAdapter("paper_mock")
    return None  # 안전 — 호출자가 inject 해야 한다.


# ── PaperTrader ──────────────────────────────────────────────────


class PaperTrader:
    """paper 모드 컨트롤러.

    공개 메서드:
      - select_paper_source(name) -> str
      - start_paper() -> PaperStatus
      - stop_paper() -> PaperStatus
      - reset_paper() -> PaperStatus
      - submit_paper_order_via_gateway(request, gateway) -> dict
      - get_paper_status() -> dict
      - get_paper_logs(limit=100, client_order_id=None) -> list[dict]
    """

    def __init__(
        self,
        *,
        broker: PaperMarketBroker | None = None,
        source_factory: SourceFactoryFn | None = None,
        default_source_name: str = "mock",
        broker_config: PaperMarketBrokerConfig | None = None,
        log_capacity: int = 1000,
    ):
        self._source_factory = source_factory or _default_source_factory
        self._broker_config = broker_config or PaperMarketBrokerConfig()
        self._log_capacity = max(10, int(log_capacity))
        self._broker: PaperMarketBroker | None = broker
        self._status = PaperStatus(
            running=False,
            source_name=default_source_name,
        )
        self._logs: list[PaperOrderLogEntry] = []
        # broker 가 주입되었으면 그 source 의 이름을 status 에 반영.
        if self._broker is not None and self._broker.source is not None:
            self._status.source_name = (
                getattr(self._broker.source, "name", default_source_name)
                or default_source_name
            )
        # default source 가 mock 이면 미리 생성해 두어도 안전.
        if self._broker is None:
            self._broker = self._build_broker(self._status.source_name)

    # ── source / mode 컨트롤 ────────────────────────────────────────

    def select_paper_source(self, name: str) -> str:
        n = (name or "").lower().strip()
        if n not in AVAILABLE_PAPER_SOURCES:
            raise PaperTraderError(
                f"unknown paper source: {name!r}. "
                f"available: {list(AVAILABLE_PAPER_SOURCES)}"
            )
        if n == "kis_readonly_stub":
            # 명시적 stub — 운영자가 인지하도록 warning 추가.
            self._broker = self._build_broker_without_source()
            self._status.source_name = n
            self._status.warnings = tuple(
                set(self._status.warnings)
                | {"kis_readonly_stub: KIS adapter is not implemented in this phase"}
            )
            return n
        # 새 source 빌드.
        self._broker = self._build_broker(n)
        self._status.source_name = n
        # 다른 source 로 변경했으면 KIS 경고 제거.
        self._status.warnings = tuple(
            w for w in self._status.warnings if "kis_readonly_stub" not in w
        )
        return n

    def start_paper(self) -> PaperStatus:
        if self._broker is None:
            self._broker = self._build_broker(self._status.source_name)
        self._status.running = True
        self._status.started_at = self._now()
        return self._status

    def stop_paper(self) -> PaperStatus:
        self._status.running = False
        self._status.stopped_at = self._now()
        return self._status

    def reset_paper(self) -> PaperStatus:
        if self._broker is not None:
            self._broker.reset()
        self._logs.clear()
        self._status = PaperStatus(
            running=False,
            source_name=self._status.source_name,
        )
        return self._status

    # ── 주문 (반드시 OrderGateway 경유) ────────────────────────────

    def submit_paper_order_via_gateway(
        self,
        request: dict,
        gateway: Any,
    ) -> dict:
        """OrderGateway 경유로 paper 주문을 제출한다.

        호출자가 직접 ``PaperMarketBroker.place_order`` 를 부르는 대신 이 함수를
        사용 — gateway 가 risk / guard / permission / approval / freshness 를 모두
        통과시킨 후 paper broker 에 전달하도록 한다.

        본 모듈은 broker 를 *직접 호출하지 않는다*. 단순 위임 + 로그만 책임.
        """
        if not self._status.running:
            raise PaperTraderError(
                "paper trader is not running. call start_paper() first."
            )
        if not hasattr(gateway, "submit"):
            raise PaperTraderError(
                "gateway must expose .submit(order) (OrderGateway 호환)"
            )
        # LIVE mode 직접 시도 차단 — 정책 강화.
        mode = str(request.get("mode") or request.get("trading_mode") or "")
        if mode.upper() == "LIVE":
            raise PaperTraderError(
                "PaperTrader rejects LIVE mode requests (paper-only)"
            )
        result = gateway.submit(request)
        # 결과 정규화 — gateway 가 dataclass 일 수도 dict 일 수도 있다.
        result_dict = _coerce_to_dict(result)
        # paper 표시 envelope 강제 (gateway 가 다른 broker 를 썼다면 paper 아니므로 표시 안 함).
        envelope = self._mark_paper_envelope(result_dict)
        self._record_log(request, envelope)
        self._status.orders_submitted += 1
        if envelope.get("status") == "FILLED":
            self._status.orders_filled += 1
        elif envelope.get("status") == "REJECTED":
            self._status.orders_rejected += 1
        self._status.last_order_at = self._now()
        if self._broker is not None and self._broker._last_market_ts is not None:
            self._status.last_market_at = self._broker._last_market_ts.isoformat()
        return envelope

    # ── 조회 ────────────────────────────────────────────────────────

    def get_paper_status(self) -> dict[str, Any]:
        d = self._status.to_dict()
        d.update({
            "mode": "PAPER",
            "is_real_trade": False,
            "execution_source": "paper_trader",
            "warning": _PAPER_WARNING,
            "fill_quality_warning": _FILL_QUALITY_WARNING,
            "available_sources": list(AVAILABLE_PAPER_SOURCES),
        })
        return d

    def get_paper_logs(
        self,
        limit: int = 100,
        client_order_id: str | None = None,
    ) -> list[dict[str, Any]]:
        out = self._logs
        if client_order_id:
            out = [e for e in out if e.client_order_id == client_order_id]
        return [e.to_dict() for e in out[-max(1, int(limit)):]]

    # 외부에서 broker 직접 접근이 필요하면 — 단, OrderGateway 우회 위험.
    @property
    def broker(self) -> PaperMarketBroker | None:
        return self._broker

    # ── 내부 ────────────────────────────────────────────────────────

    def _build_broker(self, source_name: str) -> PaperMarketBroker:
        src = self._source_factory(source_name)
        if src is None:
            return self._build_broker_without_source()
        return PaperMarketBroker(source=src, config=self._broker_config)

    def _build_broker_without_source(self) -> PaperMarketBroker:
        # source=None 인 broker — 시장가 조회가 안 되므로 require_source=True
        # (기본) 인 한 모든 신규 주문이 REJECTED.
        return PaperMarketBroker(source=None, config=self._broker_config)

    def _record_log(self, request: dict, result: dict) -> None:
        entry = PaperOrderLogEntry(
            ts=self._now(),
            client_order_id=str(
                request.get("client_order_id")
                or request.get("idempotency_key")
                or result.get("client_order_id")
                or ""
            ),
            order_id=str(result.get("order_id") or ""),
            symbol=str(result.get("symbol") or request.get("symbol") or ""),
            side=str(result.get("side") or request.get("side") or ""),
            order_type=str(
                request.get("order_type") or "MARKET"
            ).upper(),
            status=str(result.get("status") or "UNKNOWN"),
            notional_usdt=float(result.get("notional_usdt") or 0),
            filled_price=float(result.get("filled_price") or 0),
            qty=float(result.get("qty") or 0),
            fee_usdt=float(result.get("fee_usdt") or 0),
            slippage_pct=float(result.get("slippage_pct") or 0),
            reason=str(result.get("reason") or ""),
            source_name=self._status.source_name,
        )
        self._logs.append(entry)
        if len(self._logs) > self._log_capacity:
            self._logs = self._logs[-self._log_capacity:]

    def _mark_paper_envelope(self, result: dict) -> dict:
        out = dict(result)
        # gateway 결과가 이미 paper 표시를 갖고 있으면 보존, 없으면 강제 표시.
        out.setdefault("mode", "PAPER")
        out.setdefault("is_real_trade", False)
        out.setdefault("execution_source", "paper_broker")
        out.setdefault("warning", _PAPER_WARNING)
        out.setdefault("fill_quality_warning", _FILL_QUALITY_WARNING)
        return out

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


def _coerce_to_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        try:
            return dict(obj.to_dict())  # type: ignore[union-attr]
        except Exception:
            pass
    try:
        return asdict(obj)
    except Exception:
        return {"raw": repr(obj)}


__all__ = (
    "AVAILABLE_PAPER_SOURCES",
    "PaperTraderError",
    "PaperOrderLogEntry",
    "PaperStatus",
    "PaperTrader",
)
