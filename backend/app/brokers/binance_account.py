"""BinanceAccountClient — 체크리스트 #23.

Binance private/account 영역의 *disabled stub*. 본 단계에서는 실제 네트워크 호출,
HMAC signing, timestamp/signature 구현을 **하지 않는다**.

원칙 (CLAUDE.md §2.1.2 / §2.3 / §28):
  - 모든 메서드 호출 즉시 ``BinanceAccountPermissionError`` (regulatory gate 통과
    전 일체 차단).
  - 본 단계는 read-only research/skeleton 단계 — credentials/transport 주입과
    무관하게 disabled. 추후 phase 에서 별도 LIVE class 로 추가.
  - secret/api_key 를 attribute 로 *저장하지 않는다* (보관/repr/log 노출 부재).
  - 출금/이체 메서드 정의 금지 (영구). assert_no_withdrawal_methods 통과.

**규제/지역 제한**:
  Binance live/trading 은 지역·규제 제한 확인 전 금지. 본 stub 은 그 정책을 코드 레벨에
  강제한다.
"""
from __future__ import annotations


class BinanceAccountPermissionError(PermissionError):
    """Binance private account 동작은 본 단계에서 일체 disabled.

    LIVE 활성화는 별도 phase + 별도 규제·지역 제한 확인 + 별도 클래스에서만.
    """


class BinanceAccountClient:
    """Binance private/account API stub — 본 단계에서는 모든 동작 disabled.

    credentials 가 들어와도 *보관하지 않는다*. 본 stub 은 어떠한 키/토큰도 사용하지
    않으며 호출 즉시 PermissionError.
    """

    def __init__(self, *args, **kwargs):
        # credentials 인자 즉시 폐기 — 보관 금지.
        for k in ("api_key", "api_secret", "secret", "transport"):
            kwargs.pop(k, None)
        # 남은 인자도 보관하지 않음.

    @property
    def credentials_loaded(self) -> bool:
        """본 단계 stub 은 credentials 를 보관하지 않으므로 항상 False."""
        return False

    def fetch_balances(self):
        raise BinanceAccountPermissionError(
            "BinanceAccountClient.fetch_balances is disabled — Binance live/trading "
            "is gated on regulatory & regional review (CLAUDE.md §2.4 / §2.6). "
            "본 단계(#23)에서는 read-only public market data 만 다룬다."
        )

    def fetch_account_info(self):
        raise BinanceAccountPermissionError(
            "BinanceAccountClient.fetch_account_info is disabled (#23 skeleton)."
        )

    def fetch_open_orders(self, *args, **kwargs):
        raise BinanceAccountPermissionError(
            "BinanceAccountClient.fetch_open_orders is disabled (#23 skeleton)."
        )

    def __repr__(self) -> str:
        return ("BinanceAccountClient(stub — all private/account operations disabled "
                "until regulatory review)")


__all__ = (
    "BinanceAccountClient",
    "BinanceAccountPermissionError",
)
