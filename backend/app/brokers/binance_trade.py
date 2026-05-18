"""BinanceTradeClient — 체크리스트 #23.

Binance private trade API 의 *disabled stub*. 본 단계에서는 어떠한 실제 주문·취소·
선물·마진·레버리지 API 도 **구현하지 않는다**.

원칙 (CLAUDE.md §2.1 / §2.3 / §2.4):
  - 모든 메서드 호출 즉시 ``ExchangeAdapterDisabledError``.
  - HMAC signing / timestamp / signature 구현 부재.
  - 실제 trade endpoint URL (place/cancel/margin/futures/leverage) 부재 — 본 모듈
    어디에도 literal 포함 금지 (정적 회귀로 강제).
  - credentials 인자 즉시 폐기 (보관 금지).
  - 출금 메서드 부재 (영구).

**규제/지역 제한**:
  Binance live/trading 은 지역·규제 제한 확인 전 금지. 본 stub 은 그 정책을 코드 레벨
  에서 강제한다. 실제 구현은 별도 LIVE 승격 절차 통과 후, 단일 주문 경로 (Strategy →
  Agent → RiskManager → OrderGuard → PermissionGate → ApprovalQueue → OrderGateway →
  Executor) 의 끝단에서 OrderGateway 가 호출하도록만 추가한다.
"""
from __future__ import annotations
from dataclasses import dataclass

from .base import ExchangeAdapterDisabledError


@dataclass(frozen=True)
class BinanceTradeClientCapability:
    """본 stub 의 capability 표시. 모든 trading 동작 False — 영구."""

    can_place_order:     bool = False
    can_cancel_order:    bool = False
    can_get_order:       bool = False
    can_set_leverage:    bool = False
    can_set_margin_type: bool = False
    can_trade_futures:   bool = False
    can_trade_margin:    bool = False

    def to_dict(self) -> dict:
        return {
            "can_place_order":     self.can_place_order,
            "can_cancel_order":    self.can_cancel_order,
            "can_get_order":       self.can_get_order,
            "can_set_leverage":    self.can_set_leverage,
            "can_set_margin_type": self.can_set_margin_type,
            "can_trade_futures":   self.can_trade_futures,
            "can_trade_margin":    self.can_trade_margin,
            "note": ("stub — Binance live/trading is gated on regulatory & regional "
                     "review (CLAUDE.md §2.4 / §2.6). real Binance trade/account "
                     "endpoints are not implemented in this skeleton."),
        }


_LIVE_DISABLED_REASON = (
    "binance_live_trading_disabled_until_regulatory_review"
)


class BinanceTradeClient:
    """Binance private trade API stub — 모든 동작 disabled.

    실제 구현은 별도 LIVE 승격 절차 통과 후 OrderGateway 가 호출하는 단일 경로에서만
    추가한다 (Strategy/Agent 직접 호출 금지).
    """

    capability = BinanceTradeClientCapability()
    DISABLED_REASON = _LIVE_DISABLED_REASON

    def __init__(self, *args, **kwargs):
        # credentials / transport 인자 즉시 폐기 (보관 금지).
        for k in ("api_key", "api_secret", "secret",
                  "recv_window", "transport"):
            kwargs.pop(k, None)

    def place_order(self, *args, **kwargs):
        raise ExchangeAdapterDisabledError(
            f"BinanceTradeClient.place_order is disabled "
            f"({_LIVE_DISABLED_REASON}). CLAUDE.md §2.4 / §2.6."
        )

    def cancel_order(self, *args, **kwargs):
        raise ExchangeAdapterDisabledError(
            f"BinanceTradeClient.cancel_order is disabled "
            f"({_LIVE_DISABLED_REASON}). CLAUDE.md §2.4 / §2.6."
        )

    def get_order(self, *args, **kwargs):
        raise ExchangeAdapterDisabledError(
            f"BinanceTradeClient.get_order is disabled ({_LIVE_DISABLED_REASON})."
        )

    def __repr__(self) -> str:
        return ("BinanceTradeClient(stub — all trade operations disabled "
                "until regulatory review)")


__all__ = (
    "BinanceTradeClient",
    "BinanceTradeClientCapability",
)
