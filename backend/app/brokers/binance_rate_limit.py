"""Binance rate-limit 헬퍼 — 체크리스트 #23.

Binance REST 응답은 weight 기반 rate limit 을 사용한다. 각 endpoint 마다 weight 가
다르며, 호출 후 사용된 누적 weight 가 ``X-MBX-USED-WEIGHT`` / ``X-MBX-USED-WEIGHT-1M``
응답 헤더로 반환된다.

본 모듈은 다음을 제공한다.

  - ``parse_binance_used_weight(headers)`` — 헤더 → dict.
  - ``should_throttle_binance(weight, soft_limit)`` — throttle 권고 여부.
  - ``BinanceRateLimitState`` — 누적 사용 weight 보관 + sleep 주입.

원칙:
  - 외부 네트워크/sleep 을 본 모듈에서 호출하지 않는다 (caller 가 결정).
  - 비어 있거나 형식이 깨진 헤더도 안전 처리.
  - 본 단계는 read-only public 호출만 사용 — 실제 weight 모니터링은 caller 책임.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable


# Binance public spot REST 의 1분 weight 제한 (현행 약 1200).
# 안전 마진을 위해 soft limit 는 80% 기본값.
DEFAULT_WEIGHT_SOFT_LIMIT: int = 960


# Binance weight header 후보 (대소문자/슬랫 시간 단위 변형 모두 인식).
_WEIGHT_HEADER_KEYS: tuple[str, ...] = (
    "X-MBX-USED-WEIGHT",
    "X-MBX-USED-WEIGHT-1M",
    "x-mbx-used-weight",
    "x-mbx-used-weight-1m",
)
_ORDER_COUNT_HEADER_KEYS: tuple[str, ...] = (
    "X-MBX-ORDER-COUNT-10S",
    "X-MBX-ORDER-COUNT-1M",
    "x-mbx-order-count-10s",
    "x-mbx-order-count-1m",
)


def parse_binance_used_weight(headers: dict | None) -> dict:
    """Binance 응답 headers 에서 used-weight / order-count 값을 파싱.

    반환 dict 예:
        {"used_weight_1m": 23, "order_count_10s": 0, "order_count_1m": 0}

    누락/형식 오류는 무시. 빈 dict 또는 None 도 안전 처리.
    """
    if not headers or not isinstance(headers, dict):
        return {}
    # 대소문자 무시 lookup
    lower = {str(k).lower(): v for k, v in headers.items()}
    out: dict[str, int] = {}
    for key in _WEIGHT_HEADER_KEYS:
        kl = key.lower()
        if kl in lower:
            try:
                out["used_weight_1m"] = int(lower[kl])
                break
            except (TypeError, ValueError):
                continue
    for key in _ORDER_COUNT_HEADER_KEYS:
        kl = key.lower()
        if kl in lower:
            try:
                # 10s / 1m 구분 명시
                if kl.endswith("-10s"):
                    out["order_count_10s"] = int(lower[kl])
                elif kl.endswith("-1m"):
                    out["order_count_1m"] = int(lower[kl])
            except (TypeError, ValueError):
                continue
    return out


def should_throttle_binance(
    weight_state: dict,
    *,
    soft_limit: int = DEFAULT_WEIGHT_SOFT_LIMIT,
) -> bool:
    """누적 weight 가 soft_limit 이상이면 throttle 권고."""
    if not weight_state:
        return False
    if soft_limit <= 0:
        return True
    used = weight_state.get("used_weight_1m")
    if not isinstance(used, int):
        return False
    return used >= soft_limit


@dataclass
class BinanceRateLimitState:
    """Binance 호출 후 누적 사용 weight 를 보관.

    sleep 은 ``sleep_fn`` 으로 주입 (테스트는 가짜 함수, production 은 ``time.sleep``).
    """

    used_weight_1m: int | None = None
    order_count_10s: int | None = None
    order_count_1m: int | None = None
    throttle_count: int = 0
    sleep_fn: Callable[[float], None] | None = field(default=None, repr=False)

    def update(self, headers: dict | None) -> dict:
        parsed = parse_binance_used_weight(headers)
        if "used_weight_1m" in parsed:
            self.used_weight_1m = int(parsed["used_weight_1m"])
        if "order_count_10s" in parsed:
            self.order_count_10s = int(parsed["order_count_10s"])
        if "order_count_1m" in parsed:
            self.order_count_1m = int(parsed["order_count_1m"])
        return parsed

    def maybe_throttle(
        self,
        *,
        soft_limit: int = DEFAULT_WEIGHT_SOFT_LIMIT,
        sleep_seconds: float = 0.5,
    ) -> bool:
        state = {
            "used_weight_1m": self.used_weight_1m,
            "order_count_10s": self.order_count_10s,
            "order_count_1m": self.order_count_1m,
        }
        if not should_throttle_binance(state, soft_limit=soft_limit):
            return False
        self.throttle_count += 1
        if self.sleep_fn is not None:
            self.sleep_fn(max(0.0, float(sleep_seconds)))
        return True


__all__ = (
    "DEFAULT_WEIGHT_SOFT_LIMIT",
    "parse_binance_used_weight",
    "should_throttle_binance",
    "BinanceRateLimitState",
)
