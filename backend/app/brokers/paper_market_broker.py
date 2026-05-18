"""PaperMarketBroker — 체크리스트 #25.

read-only 시세 source 의 현재가를 사용해 paper(가상) 주문을 체결하는 broker.

설계 원칙 (CLAUDE.md §2.2 / §2.3 / §2.4):
  - 실제 거래소 *주문* API 호출 절대 금지 — `fetch_ticker` 만 사용.
  - read-only source 가 stale 이면 BUY 차단, EXIT 는 허용 (#16 freshness 정책).
  - Watchlist universe 밖 symbol 은 신규 진입 차단 (review_required).
  - LIVE mode 요청 거부 — PaperBroker 는 PAPER 영구.
  - 모든 응답에 `mode="PAPER"`, `is_real_trade=False`,
    `execution_source="paper_broker"`, `warning`, `fill_quality_warning` 포함.

`mock_simulation.MockBroker` 차이:
  - MockBroker : 결정론 mock market (set_market_price 명시) + 다중 자산 잔고/포지션.
                 CI 단위 테스트 중심.
  - PaperMarketBroker : 외부 read-only adapter(`MockExchangeAdapter`, UpbitAdapter,
                       OkxAdapter, BinanceAdapter ...) 의 시세를 그대로 사용 +
                       freshness/universe 가드. 실시간 환경 검증 중심.

OrderGateway 호환:
  - `place_order(dict) → dict` 시그니처가 기존 ``PaperBroker``/`MockBroker` 와 호환.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol, runtime_checkable
from uuid import uuid4

from .mock_simulation import (
    MockAccountState, MockPositionBook, MockExecutionEngine, MockBrokerConfig,
    _split_symbol, _safe_audit, _MOCK_WARNING,
)


_PAPER_WARNING: str = "Paper execution only. Not real profit or real trade."
_FILL_QUALITY_WARNING: str = (
    "Paper fills may differ from live execution (no real market impact, "
    "no real slippage, no real partial fills)."
)


@runtime_checkable
class PaperMarketSource(Protocol):
    """PaperMarketBroker 가 현재가 조회용으로 받는 read-only source.

    `MockExchangeAdapter`, `UpbitAdapter`, `OkxAdapter`, `BinanceAdapter` 모두
    `fetch_ticker(symbol)` 메서드를 가지므로 그대로 주입 가능.
    """

    name: str

    def fetch_ticker(self, symbol: str) -> Any:
        ...


@dataclass(frozen=True)
class PaperMarketBrokerConfig:
    """PaperMarketBroker 동작 파라미터."""

    base_currency: str = "USDT"
    fee_bps: float = 5.0
    slippage_bps: float = 0.0
    allow_short: bool = False
    allow_margin: bool = False
    max_order_notional: float = 0.0
    # source 가 None 또는 fetch_ticker 실패 시 paper 주문 REJECTED.
    require_source: bool = True
    # universe 화이트리스트 — None 이면 비활성.
    universe: tuple[str, ...] | None = None
    # ticker.ts 가 이 값보다 오래되면 BUY 차단 (#16 freshness).
    max_ticker_age_sec: float = 30.0
    initial_balances: dict[str, float] | None = None

    def __post_init__(self):
        if self.fee_bps < 0 or self.slippage_bps < 0:
            raise ValueError("fee_bps / slippage_bps must be >= 0")
        if self.max_order_notional < 0:
            raise ValueError("max_order_notional must be >= 0")
        if self.max_ticker_age_sec < 0:
            raise ValueError("max_ticker_age_sec must be >= 0")


# ── PaperMarketBroker ────────────────────────────────────────────


class PaperMarketBroker:
    """read-only market data + 가상 주문 체결.

    공개 메서드:
      - place_order(request: dict) -> dict
      - cancel_order(order_id_or_client_id: str) -> dict
      - get_balance(ccy: str | None = None) -> dict
      - get_position(symbol: str) -> dict
      - get_account_summary() -> dict
      - reset() -> None
    """

    def __init__(
        self,
        *,
        source: PaperMarketSource | None = None,
        config: PaperMarketBrokerConfig | None = None,
    ):
        self.config = config or PaperMarketBrokerConfig()
        self.source = source
        initial = dict(self.config.initial_balances or {})
        self.account = MockAccountState(initial)
        self.positions = MockPositionBook()
        # MockExecutionEngine 은 MockBrokerConfig 만 받으므로 변환해 사용.
        self._engine = MockExecutionEngine(
            MockBrokerConfig(
                base_currency=self.config.base_currency,
                fee_bps=self.config.fee_bps,
                slippage_bps=self.config.slippage_bps,
                allow_short=self.config.allow_short,
                allow_margin=self.config.allow_margin,
                max_order_notional=self.config.max_order_notional,
                mode="PAPER",
                initial_balances=initial,
            )
        )
        self._by_client_id: dict[str, dict] = {}
        # exchange order id → request snapshot (cancel 용)
        self._open_orders: dict[str, dict] = {}
        self._closed_orders: dict[str, dict] = {}
        self._filled_count = 0
        self._rejected_count = 0
        self._last_market_ts: datetime | None = None
        self._last_market_source: str | None = None

    # ── 조회 ────────────────────────────────────────────────────────

    def get_balance(self, ccy: str | None = None) -> dict:
        snap = self.account.snapshot()
        envelope = {
            "mode": "PAPER",
            "is_real_trade": False,
            "execution_source": "paper_broker",
            "warning": _PAPER_WARNING,
            "fill_quality_warning": _FILL_QUALITY_WARNING,
        }
        if ccy is None:
            return {"balances": snap, **envelope}
        c = ccy.upper()
        slot = snap.get(c, {"free": 0.0, "locked": 0.0})
        return {
            "ccy": c,
            "free": slot["free"],
            "locked": slot["locked"],
            "total": slot["free"] + slot["locked"],
            **envelope,
        }

    def get_position(self, symbol: str) -> dict:
        s = symbol.upper()
        p = self.positions.get(s)
        mark = self._maybe_mark_price(s)
        return {
            "symbol": s,
            "qty": p.qty,
            "avg_entry_price": p.avg_entry_price,
            "realized_pnl": p.realized_pnl,
            "unrealized_pnl": self.positions.unrealized_pnl(s, mark or 0.0),
            "mark_price": mark or 0.0,
            "mode": "PAPER",
            "is_real_trade": False,
            "execution_source": "paper_broker",
        }

    def get_account_summary(self) -> dict:
        snap = self.account.snapshot()
        positions: dict[str, Any] = {}
        for s, p in self.positions.all().items():
            mark = self._maybe_mark_price(s) or 0.0
            positions[s] = {
                "qty": p.qty,
                "avg_entry_price": p.avg_entry_price,
                "realized_pnl": p.realized_pnl,
                "unrealized_pnl": self.positions.unrealized_pnl(s, mark),
                "mark_price": mark,
            }
        return {
            "balances": snap,
            "positions": positions,
            "open_orders": list(self._open_orders.values()),
            "filled_count": self._filled_count,
            "rejected_count": self._rejected_count,
            "last_market_ts": (self._last_market_ts.isoformat()
                               if self._last_market_ts else None),
            "last_market_source": self._last_market_source,
            "source_name": (getattr(self.source, "name", "")
                            if self.source is not None else ""),
            "config": {
                "base_currency": self.config.base_currency,
                "fee_bps": self.config.fee_bps,
                "slippage_bps": self.config.slippage_bps,
                "allow_short": self.config.allow_short,
                "allow_margin": self.config.allow_margin,
                "max_order_notional": self.config.max_order_notional,
                "max_ticker_age_sec": self.config.max_ticker_age_sec,
                "universe": list(self.config.universe or ()),
            },
            "mode": "PAPER",
            "is_real_trade": False,
            "execution_source": "paper_broker",
            "warning": _PAPER_WARNING,
            "fill_quality_warning": _FILL_QUALITY_WARNING,
        }

    def reset(self) -> None:
        initial = dict(self.config.initial_balances or {})
        self.account = MockAccountState(initial)
        self.positions = MockPositionBook()
        self._by_client_id.clear()
        self._open_orders.clear()
        self._closed_orders.clear()
        self._filled_count = 0
        self._rejected_count = 0
        self._last_market_ts = None
        self._last_market_source = None

    # ── 주문 ────────────────────────────────────────────────────────

    def place_order(self, request: dict) -> dict:
        order = dict(request or {})
        symbol = str(order.get("symbol") or "").upper()
        side = str(order.get("side") or "BUY").upper()
        order_type = str(order.get("order_type") or "MARKET").upper()
        notional = float(order.get("notional_usdt") or 0)
        qty_req = float(order.get("size") or order.get("qty") or 0)
        ref_price = float(order.get("price") or 0)
        client_id = str(
            order.get("client_order_id")
            or order.get("idempotency_key")
            or ""
        )

        # LIVE mode 거부.
        requested_mode = str(
            order.get("mode") or order.get("trading_mode") or ""
        ).upper()
        if requested_mode == "LIVE":
            return self._reject(
                order, client_id,
                reason="PaperBroker: LIVE mode rejected (paper-only)",
                route="live_not_wired",
            )

        # idempotent.
        if client_id and client_id in self._by_client_id:
            return dict(self._by_client_id[client_id])

        if not symbol or side not in ("BUY", "SELL"):
            return self._reject(
                order, client_id,
                reason=f"invalid input: symbol={symbol!r} side={side!r}",
            )
        if order_type not in ("MARKET", "LIMIT"):
            return self._reject(
                order, client_id,
                reason=f"unknown order_type: {order_type}",
            )
        if order_type == "LIMIT" and ref_price <= 0:
            return self._reject(
                order, client_id,
                reason="LIMIT order requires price > 0",
            )

        # universe whitelist (BUY/엔트리 만 — EXIT 는 보유 청산 허용).
        if (
            self.config.universe is not None
            and side == "BUY"
            and symbol not in self.config.universe
        ):
            return self._reject(
                order, client_id,
                reason=(f"symbol {symbol!r} not in paper universe — "
                        "candidate_filter_review_required"),
            )

        # max_order_notional 한도.
        if (self.config.max_order_notional > 0
                and notional > 0
                and notional > self.config.max_order_notional):
            return self._reject(
                order, client_id,
                reason=(f"order notional {notional} exceeds "
                        f"max_order_notional={self.config.max_order_notional}"),
            )

        # 시장가 조회 — read-only source 만 사용.
        market_price, ticker_age_sec = self._fetch_market_price(symbol)
        if market_price is None:
            if self.config.require_source:
                return self._reject(
                    order, client_id,
                    reason="market data unavailable (paper source missing or failed)",
                )
            # source 가 없어도 ref_price 가 있으면 그대로 사용 — 보수 옵션.
            if ref_price <= 0:
                return self._reject(
                    order, client_id,
                    reason="no market price and no ref_price",
                )
            market_price = ref_price
            ticker_age_sec = 0.0

        # freshness 차단 (BUY 만, EXIT 는 허용 — #16 정책 그대로).
        if (side == "BUY"
                and ticker_age_sec is not None
                and self.config.max_ticker_age_sec > 0
                and ticker_age_sec > self.config.max_ticker_age_sec):
            return self._reject(
                order, client_id,
                reason=(f"stale market data age={ticker_age_sec:.1f}s > "
                        f"max={self.config.max_ticker_age_sec}s — BUY blocked"),
            )

        # 체결가 결정.
        if order_type == "MARKET":
            base_price = market_price
        else:
            base_price = ref_price

        # qty 결정.
        if notional > 0 and qty_req <= 0:
            qty = notional / base_price
        elif qty_req > 0:
            qty = qty_req
            if notional <= 0:
                notional = qty * base_price
        else:
            return self._reject(
                order, client_id,
                reason="notional_usdt or qty must be > 0",
            )

        # fill / fee / slippage 계산.
        fill = self._engine.calc_fill(side=side, ref_price=base_price)
        if order_type == "MARKET":
            fill_price = fill.fill_price
            slippage_pct = fill.slippage_pct
        else:
            # LIMIT — crossable 인지 확인.
            crossable = (
                (side == "BUY" and market_price <= ref_price)
                or (side == "SELL" and market_price >= ref_price)
            )
            if not crossable:
                # 미체결 → open. 잠금.
                return self._open_limit(
                    order=order, client_id=client_id,
                    symbol=symbol, side=side, qty=qty,
                    limit_price=ref_price, fee_rate=fill.fee,
                )
            fill_price = ref_price
            slippage_pct = 0.0

        # 잔고/포지션 정산.
        return self._fill(
            order=order, client_id=client_id,
            symbol=symbol, side=side, qty=qty,
            fill_price=fill_price, fee_rate=fill.fee,
            slippage_pct=slippage_pct,
        )

    def cancel_order(self, order_id_or_client_id: str) -> dict:
        oid = (order_id_or_client_id or "").strip()
        target_oid: str | None = None
        if oid in self._open_orders:
            target_oid = oid
        else:
            for k, snap in self._open_orders.items():
                if snap.get("client_order_id") == oid:
                    target_oid = k
                    break
        envelope = {
            "mode": "PAPER",
            "is_real_trade": False,
            "execution_source": "paper_broker",
            "warning": _PAPER_WARNING,
            "fill_quality_warning": _FILL_QUALITY_WARNING,
        }
        if target_oid is None:
            return {
                "status": "REJECTED",
                "route": "paper",
                "order_id": oid,
                "reason": "unknown order_id (not open)",
                **envelope,
            }
        snap = self._open_orders.pop(target_oid)
        # locked 해제.
        base_ccy, quote_ccy = _split_symbol(
            snap["symbol"], default_quote=self.config.base_currency,
        )
        if snap["side"] == "BUY":
            self.account.unlock(quote_ccy, snap["locked_notional"])
        else:
            self.account.unlock(base_ccy, snap["qty"])
        snap["status"] = "CANCELED"
        self._closed_orders[target_oid] = snap
        return {
            "status": "ACCEPTED",
            "route": "paper",
            "symbol": snap["symbol"],
            "side": snap["side"],
            "order_id": target_oid,
            "reason": "paper cancel",
            **envelope,
        }

    # ── 내부 — 시세 조회 ────────────────────────────────────────────

    def _fetch_market_price(self, symbol: str) -> tuple[float | None, float | None]:
        """source.fetch_ticker → (price, age_sec). 실패 시 (None, None)."""
        if self.source is None:
            return None, None
        try:
            tk = self.source.fetch_ticker(symbol)
        except Exception:
            return None, None
        if tk is None:
            return None, None
        price = float(getattr(tk, "price", 0) or 0)
        ts = getattr(tk, "ts", None)
        now = datetime.now(timezone.utc)
        if ts is not None:
            try:
                if isinstance(ts, datetime):
                    age = (now - ts).total_seconds()
                else:
                    age = 0.0
            except Exception:
                age = 0.0
        else:
            age = 0.0
        if price > 0:
            self._last_market_ts = ts if isinstance(ts, datetime) else now
            self._last_market_source = getattr(self.source, "name", "") or ""
            return price, max(0.0, age)
        return None, None

    def _maybe_mark_price(self, symbol: str) -> float | None:
        """포지션 표시용 — 시세 source 가 있을 때만, 실패 시 None."""
        if self.source is None:
            return None
        try:
            tk = self.source.fetch_ticker(symbol)
        except Exception:
            return None
        if tk is None:
            return None
        return float(getattr(tk, "price", 0) or 0)

    # ── 내부 — fill / open / reject ────────────────────────────────

    def _fill(
        self,
        *,
        order: dict, client_id: str,
        symbol: str, side: str, qty: float,
        fill_price: float, fee_rate: float, slippage_pct: float,
    ) -> dict:
        base_ccy, quote_ccy = _split_symbol(
            symbol, default_quote=self.config.base_currency,
        )
        notional = qty * fill_price
        fee = notional * fee_rate
        if side == "BUY":
            if not self.config.allow_margin:
                if self.account.free(quote_ccy) + 1e-12 < (notional + fee):
                    return self._reject(
                        order, client_id,
                        reason=(f"insufficient_balance: have "
                                f"{self.account.free(quote_ccy):.6f} {quote_ccy}, "
                                f"need {notional + fee:.6f} {quote_ccy}"),
                    )
            self.account.settle_buy(
                base_ccy=base_ccy, base_amt=qty,
                quote_ccy=quote_ccy, quote_used=notional + fee,
            )
            self.positions.on_buy(symbol, qty, fill_price)
        else:
            if not self.config.allow_short:
                if self.account.free(base_ccy) + 1e-12 < qty:
                    return self._reject(
                        order, client_id,
                        reason=(f"insufficient_base_balance: have "
                                f"{self.account.free(base_ccy):.6f} {base_ccy}, "
                                f"need {qty:.6f} {base_ccy}"),
                    )
            self.account.settle_sell(
                base_ccy=base_ccy, base_amt=qty,
                quote_ccy=quote_ccy, quote_received=notional - fee,
                allow_negative_base=self.config.allow_short,
            )
            self.positions.on_sell(symbol, qty, fill_price)

        self._filled_count += 1
        order_id = f"paper-{uuid4().hex[:10]}"
        result = {
            "status": "FILLED",
            "route": "paper",
            "symbol": symbol, "side": side,
            "order_id": order_id,
            "filled_price": fill_price,
            "qty": qty,
            "notional_usdt": notional,
            "fee_usdt": fee,
            "slippage_pct": slippage_pct,
            "reason": "paper fill (market data)",
            "mode": "PAPER",
            "is_real_trade": False,
            "execution_source": "paper_broker",
            "warning": _PAPER_WARNING,
            "fill_quality_warning": _FILL_QUALITY_WARNING,
            "audit": _safe_audit(order, order_id=order_id),
        }
        if client_id:
            self._by_client_id[client_id] = result
        return result

    def _open_limit(
        self,
        *,
        order: dict, client_id: str,
        symbol: str, side: str, qty: float,
        limit_price: float, fee_rate: float,
    ) -> dict:
        base_ccy, quote_ccy = _split_symbol(
            symbol, default_quote=self.config.base_currency,
        )
        notional = qty * limit_price
        fee_reserve = notional * fee_rate
        if side == "BUY":
            need = notional + fee_reserve
            if not self.config.allow_margin and self.account.free(quote_ccy) + 1e-12 < need:
                return self._reject(
                    order, client_id,
                    reason=(f"insufficient_balance to lock {need:.6f} {quote_ccy} "
                            f"(free={self.account.free(quote_ccy):.6f})"),
                )
            self.account.lock(quote_ccy, need)
            locked_notional = need
        else:
            if not self.config.allow_short:
                if self.account.free(base_ccy) + 1e-12 < qty:
                    return self._reject(
                        order, client_id,
                        reason=(f"insufficient_base_balance to lock {qty:.6f} "
                                f"{base_ccy} (free={self.account.free(base_ccy):.6f})"),
                    )
            self.account.lock(base_ccy, qty)
            locked_notional = notional

        order_id = f"paper-{uuid4().hex[:10]}"
        self._open_orders[order_id] = {
            "order_id": order_id,
            "client_order_id": client_id,
            "symbol": symbol, "side": side,
            "price": limit_price, "qty": qty,
            "locked_notional": locked_notional,
            "status": "OPEN",
        }
        result = {
            "status": "ACCEPTED",
            "route": "paper",
            "symbol": symbol, "side": side,
            "order_id": order_id,
            "filled_price": 0.0,
            "qty": qty,
            "notional_usdt": notional,
            "fee_usdt": 0.0,
            "slippage_pct": 0.0,
            "reason": "paper open (LIMIT, not crossable)",
            "mode": "PAPER",
            "is_real_trade": False,
            "execution_source": "paper_broker",
            "warning": _PAPER_WARNING,
            "fill_quality_warning": _FILL_QUALITY_WARNING,
            "audit": _safe_audit(order, order_id=order_id),
        }
        if client_id:
            self._by_client_id[client_id] = result
        return result

    def _reject(
        self,
        order: dict, client_id: str,
        *, reason: str, route: str = "paper",
    ) -> dict:
        self._rejected_count += 1
        r = {
            "status": "REJECTED",
            "route": route,
            "symbol": str(order.get("symbol") or ""),
            "side": str(order.get("side") or ""),
            "order_id": "",
            "filled_price": 0.0,
            "qty": 0.0,
            "notional_usdt": float(order.get("notional_usdt") or 0),
            "fee_usdt": 0.0,
            "slippage_pct": 0.0,
            "reason": reason,
            "mode": "PAPER",
            "is_real_trade": False,
            "execution_source": "paper_broker",
            "warning": _PAPER_WARNING,
            "fill_quality_warning": _FILL_QUALITY_WARNING,
            "audit": {"reason_code": "rejected"},
        }
        if client_id:
            self._by_client_id[client_id] = r
        return r


def make_paper_universe(symbols: Iterable[str]) -> tuple[str, ...]:
    """tuple로 정규화 (대문자, 중복 제거, 정렬)."""
    out = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    return tuple(out)


__all__ = (
    "PaperMarketSource",
    "PaperMarketBrokerConfig",
    "PaperMarketBroker",
    "make_paper_universe",
)
