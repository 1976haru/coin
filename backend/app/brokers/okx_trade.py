"""OKX 주문 클라이언트 — 체크리스트 #22.

두 클래스를 분리해 제공한다.

  1. ``OkxTradeClient`` — 실제 OKX private trade API 의 *disabled stub*.
     모든 메서드 호출 즉시 ``ExchangeAdapterDisabledError``.
     OK-ACCESS-* signing / JWT / HMAC 구현 부재.
     실제 trade endpoint URL (place/cancel/batch/leverage/position-mode) 은 본
     모듈 어디에도 literal 로 포함되지 않는다 — 정적 회귀로 강제한다.

  2. ``OkxPaperOrderClient`` — 외부 네트워크 없이 결정론적 spot/swap PAPER 주문을
     처리하는 paper engine.
     - mode 는 항상 ``PAPER`` 또는 ``MOCK``.
     - ``trading_mode``/``mode`` 가 ``LIVE`` 이면 거부.
     - swap order 는 instrument_type=SWAP 을 명시해야 함.
     - leverage / margin_mode 는 *입력으로만 받고* 실제 적용하지 않는다.
     - reduce_only 필드 허용 (실제 거래소 호출 없음).
     - client_order_id 기반 idempotency.
     - 응답에 ``secret`` / ``passphrase`` 류 키 포함 금지 (audit sanitize).

원칙 (CLAUDE.md §2.1 / §2.3 / §2.4):
  - Strategy/Agent 가 직접 호출하지 않는다 (모듈 경계 + 정적 회귀).
  - 실제 LIVE 주문 송신 코드는 본 모듈에서 절대 작성하지 않는다 — 후속 LIVE
    승격 절차에서 별도 클래스로 추가.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.schemas import OrderResult

from .base import ExchangeAdapterDisabledError


_FORBIDDEN_RESPONSE_KEYS = (
    "api_key", "api_secret", "secret", "access_token", "token",
    "passphrase", "password", "private_key",
    "ok_access_key", "ok_access_sign", "ok_access_passphrase",
    "ok_access_timestamp",
)


# ── OkxTradeClient — disabled stub ─────────────────────────────


@dataclass(frozen=True)
class OkxTradeClientCapability:
    """본 stub 의 capability 표시. 모든 주문 동작 False — 영구."""

    can_place_order:    bool = False
    can_cancel_order:   bool = False
    can_amend_order:    bool = False
    can_get_order:      bool = False
    can_set_leverage:   bool = False
    can_set_margin_mode: bool = False

    def to_dict(self) -> dict:
        return {
            "can_place_order":     self.can_place_order,
            "can_cancel_order":    self.can_cancel_order,
            "can_amend_order":     self.can_amend_order,
            "can_get_order":       self.can_get_order,
            "can_set_leverage":    self.can_set_leverage,
            "can_set_margin_mode": self.can_set_margin_mode,
            "note": ("stub — implementation gated on OrderGateway + LIVE permission "
                     "+ separate phase. real OKX trade/account endpoints are not "
                     "implemented in this skeleton."),
        }


class OkxTradeClient:
    """OKX private trade API stub — 본 단계에서는 모든 동작 disabled.

    실제 구현은 별도 LIVE 승격 절차 통과 후 OrderGateway 가 호출하는 단일 경로에서만
    추가한다 (Strategy/Agent 직접 호출 금지).
    """

    capability = OkxTradeClientCapability()

    def __init__(self, *args, **kwargs):
        # credentials 가 들어와도 *저장하지 않는다*. 본 stub 은 어떠한 키도 사용 안 함.
        for k in ("api_key", "api_secret", "api_password",
                  "api_passphrase", "transport"):
            kwargs.pop(k, None)

    def place_order(self, *args, **kwargs):
        raise ExchangeAdapterDisabledError(
            "OkxTradeClient.place_order is disabled — implementation gated on "
            "OrderGateway + LIVE permission (CLAUDE.md §2.4). "
            "본 단계(#22)에서는 실제 OKX 주문 송신을 구현하지 않는다."
        )

    def cancel_order(self, *args, **kwargs):
        raise ExchangeAdapterDisabledError(
            "OkxTradeClient.cancel_order is disabled — implementation gated on "
            "OrderGateway + LIVE permission (CLAUDE.md §2.4)."
        )

    def amend_order(self, *args, **kwargs):
        raise ExchangeAdapterDisabledError(
            "OkxTradeClient.amend_order is disabled."
        )

    def get_order(self, *args, **kwargs):
        raise ExchangeAdapterDisabledError(
            "OkxTradeClient.get_order is disabled."
        )

    def __repr__(self) -> str:
        return "OkxTradeClient(stub — all trade operations disabled)"


# ── OkxPaperOrderClient — 결정론적 PAPER 주문 엔진 ─────────────


@dataclass
class _PaperBookEntry:
    order_id: str
    inst_id: str
    inst_type: str            # SPOT / SWAP
    side: str                 # BUY / SELL
    order_type: str           # MARKET / LIMIT
    px: float                 # 체결가 또는 limit 가격
    sz: float                 # 수량
    notional_usdt: float
    status: str               # FILLED / ACCEPTED / CANCELED / REJECTED
    reduce_only: bool = False
    leverage: float = 1.0
    margin_mode: str = ""


class OkxPaperOrderClient:
    """OKX spot/swap 주문의 결정론적 PAPER 엔진 — 외부 네트워크 호출 0.

    spot MARKET BUY → 잔고(USDT) 차감 + FILLED.
    spot LIMIT SELL → ACCEPTED (체결은 별도 표현 없음).
    swap MARKET BUY → FILLED (실제 leverage/margin 미적용, 입력은 받음).
    LIVE mode dict 가 들어오면 무조건 REJECTED.
    """

    def __init__(self, initial_balance_usdt: float = 10_000.0):
        self._balance_usdt = float(initial_balance_usdt)
        self._by_client_id: dict[str, OrderResult] = {}
        self._by_order_id: dict[str, _PaperBookEntry] = {}
        self._filled_count = 0

    @property
    def filled_count(self) -> int:
        return self._filled_count

    def get_balance_usdt(self) -> float:
        return round(self._balance_usdt, 6)

    def place_order(self, order: dict) -> OrderResult:
        inst_id   = str(order.get("inst_id") or order.get("symbol") or "")
        inst_type = str(order.get("inst_type") or order.get("instrument_type") or "SPOT").upper()
        side      = str(order.get("side") or "BUY").upper()
        order_type = str(order.get("order_type") or order.get("ord_type") or "MARKET").upper()
        notional  = float(order.get("notional_usdt") or 0)
        ref_price = float(order.get("price") or order.get("px") or 0)
        sz        = float(order.get("size") or order.get("sz") or 0)
        client_id = str(order.get("client_order_id") or order.get("idempotency_key") or "")
        reduce_only = bool(order.get("reduce_only") or False)
        leverage  = float(order.get("leverage") or 1.0)
        margin_mode = str(order.get("margin_mode") or "")

        # 1) LIVE mode 거부.
        requested_mode = str(
            order.get("mode") or order.get("trading_mode") or ""
        ).upper()
        if requested_mode == "LIVE":
            return self._reject(
                inst_id, side, inst_type,
                reason="OkxPaperOrderClient: LIVE order rejected (paper engine only)",
                route="live_not_wired",
                client_id=client_id,
            )

        # 2) idempotent (client_order_id 또는 idempotency_key 중복).
        if client_id and client_id in self._by_client_id:
            return self._by_client_id[client_id]

        # 3) instrument 유효성.
        if not inst_id or "-" not in inst_id:
            return self._reject(
                inst_id, side, inst_type,
                reason="invalid inst_id (expected e.g. BTC-USDT or BTC-USDT-SWAP)",
                client_id=client_id,
            )
        if inst_type not in ("SPOT", "SWAP"):
            return self._reject(
                inst_id, side, inst_type,
                reason=f"unsupported inst_type: {inst_type}",
                client_id=client_id,
            )
        # SWAP 은 instrument 명에 SWAP 이 포함되어야 함 (의도 명시).
        if inst_type == "SWAP" and not inst_id.endswith("-SWAP"):
            return self._reject(
                inst_id, side, inst_type,
                reason="SWAP order requires inst_id ending with '-SWAP'",
                client_id=client_id,
            )
        if inst_type == "SPOT" and inst_id.endswith("-SWAP"):
            return self._reject(
                inst_id, side, inst_type,
                reason="SPOT order cannot use '-SWAP' inst_id",
                client_id=client_id,
            )
        if order_type not in ("MARKET", "LIMIT"):
            return self._reject(
                inst_id, side, inst_type,
                reason=f"unknown order_type: {order_type}",
                client_id=client_id,
            )
        if order_type == "LIMIT" and ref_price <= 0:
            return self._reject(
                inst_id, side, inst_type,
                reason="LIMIT order requires price>0",
                client_id=client_id,
            )
        if notional <= 0 and sz <= 0:
            return self._reject(
                inst_id, side, inst_type,
                reason="notional_usdt or size must be >0",
                client_id=client_id,
            )

        # 4) 결정론적 mock 체결가 — 사용자가 price 를 줬으면 그대로, 아니면 inst_id 해시.
        if ref_price <= 0:
            ref_price = 1000.0 + (abs(hash(inst_id)) % 100_000) / 1.0

        # 5) 잔고 체크 — SPOT BUY 만.
        if inst_type == "SPOT" and side == "BUY":
            if notional > self._balance_usdt:
                return self._reject(
                    inst_id, side, inst_type,
                    reason=(f"insufficient_balance: have {self._balance_usdt:.2f} USDT, "
                            f"need {notional:.2f} USDT"),
                    client_id=client_id,
                )
            self._balance_usdt -= notional

        # 6) FILLED (MARKET) / ACCEPTED (LIMIT).
        order_id = f"okx-paper-{uuid4().hex[:10]}"
        if order_type == "MARKET":
            status = "FILLED"
            reason = ("okx paper fill (MARKET, spot)" if inst_type == "SPOT"
                      else "okx paper fill (MARKET, swap — leverage/margin not applied)")
            self._filled_count += 1
        else:
            status = "ACCEPTED"
            reason = ("okx paper accepted (LIMIT, spot)" if inst_type == "SPOT"
                      else "okx paper accepted (LIMIT, swap — leverage/margin not applied)")

        entry = _PaperBookEntry(
            order_id=order_id, inst_id=inst_id, inst_type=inst_type,
            side=side, order_type=order_type, px=ref_price,
            sz=sz, notional_usdt=notional, status=status,
            reduce_only=reduce_only, leverage=leverage, margin_mode=margin_mode,
        )
        self._by_order_id[order_id] = entry

        result = OrderResult(
            status=status,
            route="paper",
            symbol=inst_id, side=side,
            order_id=order_id,
            filled_price=ref_price,
            notional_usdt=notional,
            fee_usdt=0.0,
            slippage_pct=0.0,
            reason=reason,
            audit=_safe_audit(order, order_id=order_id, inst_type=inst_type),
        )
        if client_id:
            self._by_client_id[client_id] = result
        return result

    def cancel_order(self, order_id: str) -> OrderResult:
        entry = self._by_order_id.get(order_id)
        if entry is None:
            return OrderResult(
                status="ACCEPTED", route="paper",
                order_id=order_id,
                reason="okx paper cancel (unknown order_id, gracefully accepted)",
                audit={"order_id": order_id},
            )
        entry.status = "CANCELED"
        return OrderResult(
            status="ACCEPTED", route="paper",
            symbol=entry.inst_id, side=entry.side,
            order_id=order_id,
            reason=f"okx paper cancel ({entry.inst_type})",
            audit={"order_id": order_id, "inst_type": entry.inst_type},
        )

    def _reject(
        self,
        inst_id: str, side: str, inst_type: str, *,
        reason: str,
        route: str = "paper",
        client_id: str = "",
    ) -> OrderResult:
        r = OrderResult(
            status="REJECTED",
            route=route,
            symbol=inst_id, side=side,
            order_id="",
            reason=reason,
            audit={"reason_code": "rejected", "inst_type": inst_type},
        )
        if client_id:
            self._by_client_id[client_id] = r
        return r


def _safe_audit(order: dict, *, order_id: str, inst_type: str) -> dict:
    out: dict[str, Any] = {"order_id": order_id, "inst_type": inst_type}
    for k, v in (order or {}).items():
        kl = str(k).lower()
        if any(bad in kl for bad in _FORBIDDEN_RESPONSE_KEYS):
            continue
        out[k] = v
    return out


__all__ = (
    "OkxTradeClient",
    "OkxTradeClientCapability",
    "OkxPaperOrderClient",
)
