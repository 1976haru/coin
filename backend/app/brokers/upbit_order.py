"""UpbitOrderClient — 체크리스트 #21.

업비트 private 주문 API 구조의 *disabled stub*. 본 단계에서는 실제 네트워크
주문/취소를 **구현하지 않는다**.

원칙 (CLAUDE.md §2.1 / §2.3 / §2.4):
  - place_order / cancel_order 모두 호출 즉시 ``ExchangeAdapterDisabledError``.
  - JWT signing / query_hash / HMAC signing 코드 부재 — 본 모듈에서 작성 금지.
  - 실제 주문 endpoint URL 부재 — 본 단계에서 추가 금지.
  - 출금/이체 endpoint 부재 (영구). ``assert_no_withdrawal_methods`` 통과.
  - place_order 실제 구현은 별도 LIVE 승격 절차 (CLAUDE.md §2.6) 통과 후, 단일 주문
    경로(Strategy → Agent → RiskManager → OrderGuard → PermissionGate →
    ApprovalQueue → OrderGateway → Executor) 의 끝단에서 OrderGateway 가 호출하도록만
    추가한다. Strategy/Agent 가 본 client 를 직접 호출하지 않는다.

본 stub 의 목적은 "주문 API 구조가 들어갈 자리"를 명시적으로 표기하는 것이지,
실제 주문 송신 기능을 제공하는 것이 아니다.
"""
from __future__ import annotations
from dataclasses import dataclass

from .base import ExchangeAdapterDisabledError


@dataclass(frozen=True)
class UpbitOrderClientCapability:
    """본 stub 의 capability 표시. 모든 주문 동작 False — 영구."""

    can_place_order:  bool = False
    can_cancel_order: bool = False
    can_get_order:    bool = False

    def to_dict(self) -> dict:
        return {
            "can_place_order":  self.can_place_order,
            "can_cancel_order": self.can_cancel_order,
            "can_get_order":    self.can_get_order,
            "note":             "stub — implementation gated on OrderGateway + LIVE permission",
        }


class UpbitOrderClient:
    """업비트 주문 API stub — 본 단계에서는 모든 동작이 disabled.

    실제 구현은 별도 LIVE 승격 절차 통과 후 OrderGateway 가 호출하는 단일 경로에서만
    추가한다 (Strategy/Agent 직접 호출 금지).
    """

    capability = UpbitOrderClientCapability()

    def __init__(self, *args, **kwargs):
        # api_key/api_secret 같은 인자가 들어와도 *저장하지 않는다*. 본 stub 은 어떠한
        # credentials 도 사용하지 않으며 즉시 disabled 응답으로 동작.
        # del 로 명시적으로 폐기 (메모리 잔존 방지).
        kwargs.pop("api_key", None)
        kwargs.pop("api_secret", None)
        kwargs.pop("transport", None)
        # 남은 키워드 인자는 무시.

    def place_order(self, *args, **kwargs):
        raise ExchangeAdapterDisabledError(
            "UpbitOrderClient.place_order is disabled — implementation gated on "
            "OrderGateway + LIVE permission (CLAUDE.md §2.4). "
            "본 단계(#21)에서는 실제 주문 송신을 구현하지 않는다."
        )

    def cancel_order(self, *args, **kwargs):
        raise ExchangeAdapterDisabledError(
            "UpbitOrderClient.cancel_order is disabled — implementation gated on "
            "OrderGateway + LIVE permission (CLAUDE.md §2.4)."
        )

    def get_order(self, *args, **kwargs):
        raise ExchangeAdapterDisabledError(
            "UpbitOrderClient.get_order is disabled in this skeleton."
        )

    def __repr__(self) -> str:
        return "UpbitOrderClient(stub — all order operations disabled)"


__all__ = (
    "UpbitOrderClient",
    "UpbitOrderClientCapability",
)
