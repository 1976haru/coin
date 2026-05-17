"""UpbitAccountClient — 체크리스트 #21.

업비트 private/account 영역의 *gated stub*. 본 단계에서는 실제 네트워크 호출,
JWT signing, query_hash, HMAC signing 을 **구현하지 않는다**.

원칙 (CLAUDE.md §2.1.2 / §2.3 / §28):
  - credentials (API key/secret) 가 *없으면* 어떤 메서드도 호출 불가 — PermissionError.
  - credentials 가 있어도 ``transport`` 를 명시적으로 주입해야 동작 — silent
    네트워크 호출 금지.
  - **출금 / 이체 메서드 정의 금지 (영구).** ``assert_no_withdrawal_methods`` 통과.
  - 응답에 secret 노출 금지.
  - 본 모듈은 read-only ``GET /v1/accounts`` 형식만 인식하며 실제 사용은 후속 단계에서.

테스트는 다음 두 방식 중 하나로 동작.
  1. credentials 없음 → 모든 메서드가 PermissionError.
  2. credentials 있고 ``transport=FakeAccountTransport`` 주입 → 결정론적 mock 응답.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable


class UpbitAccountPermissionError(PermissionError):
    """credentials 또는 transport 가 없는데 private 동작을 호출한 경우."""


# 본 client 가 *인식* 하는 path. 화이트리스트 — 출금/이체 path 는 본 단계에서 정의 금지.
_ACCOUNT_PUBLIC_SAFE_PATHS: tuple[str, ...] = (
    "/v1/accounts",  # 잔고 조회 (private GET)
)


@dataclass(frozen=True)
class AccountTransportResponse:
    status_code: int
    body: Any
    headers: dict[str, str]


AccountTransportFn = Callable[[str, str, dict, dict], AccountTransportResponse]


class UpbitAccountClient:
    """업비트 private/account *gated* client.

    Parameters
    ----------
    api_key, api_secret:
        업비트 access key / secret key. **둘 중 하나라도 없으면 모든 메서드가
        PermissionError**. 본 모듈은 secret 을 응답/repr/log 에 절대 노출하지 않는다.
    transport:
        명시적 transport 주입 (테스트는 FakeAccountTransport 만 허용). production
        transport 코드는 본 단계에서 추가하지 않는다 (후속 PR + LIVE 승격 절차).

    NOTE: API key 가 *있어도* 출금 권한이 있는 키는 절대 사용하지 않는다 — 운영
    정책에서 출금권한 부여 자체를 금한다 (docs/api_key_policy.md).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        transport: AccountTransportFn | None = None,
    ):
        # secret 은 attribute 로 저장하되 repr 에 노출되지 않도록 dataclass 제외 + 직접 보관.
        self._has_creds = bool(api_key) and bool(api_secret)
        self._transport = transport
        # secret 자체는 보관하지 않는다 — 본 단계에서 사용 안 함.
        # (후속 PR에서 JWT signing 을 구현할 때 별도 secure 저장소 도입.)
        del api_key, api_secret

    @property
    def credentials_loaded(self) -> bool:
        """credentials 존재 여부만 노출 — 값은 노출하지 않는다."""
        return self._has_creds

    def fetch_balances(self) -> list[dict]:
        """``GET /v1/accounts`` — 잔고 조회 stub.

        credentials 가 없거나 transport 가 주입되지 않았으면 PermissionError.
        반환 형식: ``[{"currency": "BTC", "balance": "...", "locked": "..."}, ...]``.
        """
        if not self._has_creds:
            raise UpbitAccountPermissionError(
                "UpbitAccountClient: credentials not loaded "
                "(api_key/api_secret 미주입). read-only adapter 만 사용 가능."
            )
        if self._transport is None:
            raise UpbitAccountPermissionError(
                "UpbitAccountClient: transport not configured. "
                "production transport 는 후속 안전 단계에서 LIVE 승격 절차 후에만 추가. "
                "tests 는 FakeAccountTransport 를 주입한다."
            )
        path = "/v1/accounts"
        if path not in _ACCOUNT_PUBLIC_SAFE_PATHS:  # 정적 가드
            raise UpbitAccountPermissionError(
                f"non-whitelisted account path: {path!r}"
            )
        resp = self._transport("GET", path, {}, {})
        if not isinstance(resp, AccountTransportResponse):
            raise UpbitAccountPermissionError(
                f"transport returned non-standard response: {type(resp).__name__}"
            )
        if resp.status_code >= 400:
            raise UpbitAccountPermissionError(
                f"upbit /v1/accounts status={resp.status_code}"
            )
        return _parse_balances(resp.body)

    # ── repr / debug ──────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"UpbitAccountClient(credentials_loaded={self._has_creds}, "
            f"transport={'injected' if self._transport else 'none'})"
        )


def _parse_balances(body: Any) -> list[dict]:
    """``/v1/accounts`` 응답 → balance dict 리스트 (secret 미포함)."""
    if not isinstance(body, list):
        return []
    out: list[dict] = []
    for item in body:
        if not isinstance(item, dict):
            continue
        out.append({
            "currency":              str(item.get("currency") or "").upper(),
            "balance":               str(item.get("balance") or "0"),
            "locked":                str(item.get("locked") or "0"),
            "avg_buy_price":         str(item.get("avg_buy_price") or "0"),
            "avg_buy_price_modified": bool(item.get("avg_buy_price_modified") or False),
            "unit_currency":         str(item.get("unit_currency") or "KRW").upper(),
        })
    return out


__all__ = (
    "UpbitAccountClient",
    "UpbitAccountPermissionError",
    "AccountTransportResponse",
    "AccountTransportFn",
)
