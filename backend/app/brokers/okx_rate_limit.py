"""OKX rate-limit / error 헬퍼 — 체크리스트 #22.

OKX REST 응답은 일반적으로 다음 형태:
    {"code": "0", "msg": "", "data": [...]}

비정상 응답 시 ``code`` 가 "0" 외 값. 대표 rate limit 코드:
    - "50011" : Requests too frequent (rate limit 초과)

본 모듈은 OKX 의 응답 payload 만으로 rate limit / 에러를 판정하며 sleep 자체는
caller 가 제어한다 (테스트가 느려지지 않도록 sleep 함수 주입).

설계 원칙:
  - 외부 네트워크/sleep 을 본 모듈에서 호출하지 않는다.
  - 비정상/None payload 도 안전 처리.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable


# 알려진 rate-limit 에러 코드. 추가 코드는 후속에서 보강.
OKX_RATE_LIMIT_CODES: frozenset[str] = frozenset({"50011"})


@dataclass(frozen=True)
class OkxApiError:
    """OKX 응답 ``{"code": ..., "msg": ..., "data": ...}`` 구조의 정규화 결과."""

    code: str = "0"
    msg:  str = ""
    is_rate_limit: bool = False
    data: Any = None

    @property
    def is_ok(self) -> bool:
        return self.code == "0"


def parse_okx_api_error(payload: Any) -> OkxApiError:
    """OKX 응답 payload → ``OkxApiError`` 정규화.

    - dict 가 아닌 경우 안전하게 code="" + is_rate_limit=False 로 처리.
    - code/msg 가 누락되어도 안전.
    """
    if not isinstance(payload, dict):
        return OkxApiError(code="", msg="non-dict payload", is_rate_limit=False)
    code = str(payload.get("code") or "")
    msg = str(payload.get("msg") or "")
    return OkxApiError(
        code=code,
        msg=msg,
        is_rate_limit=(code in OKX_RATE_LIMIT_CODES),
        data=payload.get("data"),
    )


def is_okx_rate_limit_error(payload: Any) -> bool:
    """payload 가 OKX rate-limit 에러인지 (50011 등)."""
    return parse_okx_api_error(payload).is_rate_limit


def should_throttle_okx(
    last_error: OkxApiError | dict | None,
    *,
    min_backoff_seconds: float = 1.0,
) -> bool:
    """마지막 에러가 rate-limit 이면 throttle 권고.

    - None / 정상 응답 → False.
    - rate-limit → True (caller 가 backoff sleep 적용).
    - min_backoff_seconds 자체는 caller 가 sleep 시 사용 — 본 함수는 결정만.
    """
    if last_error is None:
        return False
    if isinstance(last_error, OkxApiError):
        return last_error.is_rate_limit
    if isinstance(last_error, dict):
        return is_okx_rate_limit_error(last_error)
    return False


@dataclass
class OkxRateLimitState:
    """OKX 호출 결과 누적 — 가장 최근 에러를 보관해 진단/관제에 사용.

    sleep 은 ``sleep_fn`` 으로 주입 (테스트는 가짜 함수, production 은 ``time.sleep``).
    """

    last_error: OkxApiError | None = None
    rate_limit_hits: int = 0
    sleep_fn: Callable[[float], None] | None = field(default=None, repr=False)

    def update(self, payload: Any) -> OkxApiError:
        err = parse_okx_api_error(payload)
        if not err.is_ok:
            self.last_error = err
            if err.is_rate_limit:
                self.rate_limit_hits += 1
        else:
            self.last_error = None
        return err

    def maybe_backoff(
        self,
        *,
        seconds: float = 1.0,
    ) -> bool:
        """마지막 에러가 rate-limit 이면 ``sleep_fn(seconds)`` 호출. 호출 여부 반환."""
        if not should_throttle_okx(self.last_error):
            return False
        if self.sleep_fn is not None:
            self.sleep_fn(max(0.0, float(seconds)))
        return True


__all__ = (
    "OKX_RATE_LIMIT_CODES",
    "OkxApiError",
    "parse_okx_api_error",
    "is_okx_rate_limit_error",
    "should_throttle_okx",
    "OkxRateLimitState",
)
