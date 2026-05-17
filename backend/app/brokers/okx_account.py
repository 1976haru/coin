"""OkxAccountClient — 체크리스트 #22.

OKX private/account 영역의 *gated stub*. 본 단계에서는 실제 네트워크 호출, OK-ACCESS
signing(API key/secret/passphrase) 을 **구현하지 않는다**.

원칙 (CLAUDE.md §2.1.2 / §2.3 / §28):
  - credentials (api_key/api_secret/api_password) 가 *부족하면* 모든 메서드 호출 불가
    → ``OkxAccountPermissionError``.
  - credentials 가 있어도 ``transport`` 가 명시 주입되지 않으면 동작 불가 — silent
    네트워크 호출 금지.
  - secret/passphrase 를 attribute 로 저장하지 않는다 (repr/응답 노출 부재).
  - **출금 / 이체 메서드 정의 금지 (영구).** assert_no_withdrawal_methods 통과.
  - account 영역의 *읽기* path 만 화이트리스트 — /api/v5/account/balance.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable


class OkxAccountPermissionError(PermissionError):
    """credentials / transport 가 없는데 private 동작을 호출한 경우."""


_ACCOUNT_PATHS_EXACT: tuple[str, ...] = (
    "/api/v5/account/balance",
    "/api/v5/account/positions",
)


@dataclass(frozen=True)
class OkxAccountTransportResponse:
    status_code: int
    body: Any
    headers: dict[str, str]


AccountTransportFn = Callable[[str, str, dict, dict], OkxAccountTransportResponse]


class OkxAccountClient:
    """OKX private/account *gated* client.

    Parameters
    ----------
    api_key, api_secret, api_password:
        세 값이 모두 있어야 credentials_loaded=True. 하나라도 빠지면 모든 메서드가
        OkxAccountPermissionError. secret/passphrase 는 객체에 보관하지 않는다.
    transport:
        명시적 transport 주입 (테스트는 FakeAccountTransport 만 허용). production
        transport 코드는 본 단계에서 추가하지 않는다 (후속 PR + LIVE 승격 절차).

    NOTE: API key 가 *있어도* 출금 권한이 있는 키는 절대 사용하지 않는다 — 정책상
    출금권한 부여 자체를 금한다 (docs/api_key_policy.md).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_password: str | None = None,
        transport: AccountTransportFn | None = None,
    ):
        self._has_creds = bool(api_key) and bool(api_secret) and bool(api_password)
        self._transport = transport
        del api_key, api_secret, api_password  # 보관 금지

    @property
    def credentials_loaded(self) -> bool:
        return self._has_creds

    def _ensure_ready(self, path: str) -> AccountTransportFn:
        if not self._has_creds:
            raise OkxAccountPermissionError(
                "OkxAccountClient: credentials not loaded "
                "(api_key/api_secret/api_password 모두 필요). read-only adapter 만 사용 가능."
            )
        if self._transport is None:
            raise OkxAccountPermissionError(
                "OkxAccountClient: transport not configured. "
                "production transport 는 후속 안전 단계에서 LIVE 승격 절차 후에만 추가. "
                "tests 는 FakeAccountTransport 를 주입한다."
            )
        if path not in _ACCOUNT_PATHS_EXACT:
            raise OkxAccountPermissionError(
                f"non-whitelisted account path: {path!r}"
            )
        return self._transport

    def fetch_balances(self) -> list[dict]:
        """``GET /api/v5/account/balance`` — 잔고 조회 stub.

        반환: ``[{"ccy": "BTC", "bal": "...", "frozenBal": "...", "availBal": "..."}, ...]``
        """
        path = "/api/v5/account/balance"
        t = self._ensure_ready(path)
        resp = t("GET", path, {}, {})
        if not isinstance(resp, OkxAccountTransportResponse):
            raise OkxAccountPermissionError(
                f"transport returned non-standard response: {type(resp).__name__}"
            )
        if resp.status_code >= 400:
            raise OkxAccountPermissionError(
                f"okx /api/v5/account/balance status={resp.status_code}"
            )
        return _parse_balances(resp.body)

    def fetch_positions(self, inst_type: str = "SWAP") -> list[dict]:
        """``GET /api/v5/account/positions`` — 포지션 조회 stub."""
        path = "/api/v5/account/positions"
        t = self._ensure_ready(path)
        resp = t("GET", path, {"instType": (inst_type or "SWAP").upper()}, {})
        if not isinstance(resp, OkxAccountTransportResponse):
            raise OkxAccountPermissionError(
                f"transport returned non-standard response: {type(resp).__name__}"
            )
        if resp.status_code >= 400:
            raise OkxAccountPermissionError(
                f"okx /api/v5/account/positions status={resp.status_code}"
            )
        return _parse_positions(resp.body)

    def __repr__(self) -> str:
        return (
            f"OkxAccountClient(credentials_loaded={self._has_creds}, "
            f"transport={'injected' if self._transport else 'none'})"
        )


def _parse_balances(body: Any) -> list[dict]:
    """OKX 응답: ``{"code": "0", "data": [{"details": [{"ccy": "BTC", "bal": "..."}], ...}]}``."""
    if not isinstance(body, dict):
        return []
    data = body.get("data") or []
    if not isinstance(data, list) or not data:
        return []
    first = data[0]
    if not isinstance(first, dict):
        return []
    details = first.get("details") or []
    out: list[dict] = []
    for item in details:
        if not isinstance(item, dict):
            continue
        out.append({
            "ccy":       str(item.get("ccy") or "").upper(),
            "bal":       str(item.get("bal") or "0"),
            "frozen_bal": str(item.get("frozenBal") or "0"),
            "avail_bal": str(item.get("availBal") or "0"),
            "eq":        str(item.get("eq") or "0"),
        })
    return out


def _parse_positions(body: Any) -> list[dict]:
    if not isinstance(body, dict):
        return []
    data = body.get("data") or []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        out.append({
            "inst_id":   str(item.get("instId") or "").upper(),
            "inst_type": str(item.get("instType") or "").upper(),
            "pos":       str(item.get("pos") or "0"),
            "avg_px":    str(item.get("avgPx") or "0"),
            "upl":       str(item.get("upl") or "0"),
            "lever":     str(item.get("lever") or "0"),
            "mgn_mode":  str(item.get("mgnMode") or ""),
            "pos_side":  str(item.get("posSide") or ""),
        })
    return out


__all__ = (
    "OkxAccountClient",
    "OkxAccountPermissionError",
    "OkxAccountTransportResponse",
    "AccountTransportFn",
)
