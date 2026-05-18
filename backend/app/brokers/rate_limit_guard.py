"""API Rate Limit Guard — 체크리스트 #26.

거래소별 정책(`api_limits.RateLimitPolicy`)에 따라 호출 전 ``can_call/acquire``
판단 + 호출 후 응답 헤더/에러를 반영해 cooldown 을 관리한다.

설계 원칙:
  - **순수 정책 결정만 — 실제 HTTP 호출/sleep 을 본 모듈에서 하지 않는다.**
    sleep 은 caller 가 결정 (테스트는 fake sleep 주입).
  - 토큰 버킷 위에 cooldown + retry-decision + state observation 을 얹은 layer.
  - 본 단계는 in-memory state. DB 영속은 본 작업 범위 밖.
  - 무한 재시도 금지 — ``max_retries`` 초과 시 ``should_retry=False``.
  - read-only 와 private/order group 을 분리 (정책 dict 가 group 별로 다름).
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Mapping

from .api_limits import (
    RateLimitPolicy, get_default_policy,
    parse_upbit_remaining_req, parse_okx_error,
    parse_binance_used_weight, parse_retry_after,
    list_default_policies,
)
from .rate_limiter import TokenBucket


# ── 결과 타입 ────────────────────────────────────────────────────


@dataclass(frozen=True)
class AcquireDecision:
    """``can_call`` / ``acquire`` 결과."""

    allowed: bool
    reason: str = ""
    wait_seconds: float = 0.0
    remaining_tokens: float = 0.0
    cooldown_remaining: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetryDecision:
    """호출 실패 후 재시도 정책 판단 결과."""

    should_retry: bool
    wait_seconds: float
    reason: str
    attempt: int
    max_retries: int
    cooldown_until: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GuardState:
    """guard 의 현재 관제 상태 (in-memory)."""

    # 응답 헤더에서 추출한 잔여량 (있을 때만)
    remaining: int | None = None
    used_weight: int | None = None
    # cooldown_until 은 ``time_fn()`` 단위 (monotonic 권장)
    cooldown_until: float = 0.0
    consecutive_failures: int = 0
    last_error_code: str = ""
    last_error_at: float | None = None
    last_response_at: float | None = None
    # 통계
    total_calls: int = 0
    total_acquired: int = 0
    total_throttled: int = 0
    total_429: int = 0
    total_418: int = 0
    total_okx_50011: int = 0
    total_network_errors: int = 0
    total_retries_issued: int = 0
    total_retries_denied: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── 에러 유형 카탈로그 ──────────────────────────────────────────


# 본 guard 가 인식하는 에러 종류.
ERROR_KIND_429 = "rate_limit_429"
ERROR_KIND_418 = "ip_banned_418"
ERROR_KIND_OKX_50011 = "okx_50011"
ERROR_KIND_UPBIT_TOO_MANY = "upbit_too_many_requests"
ERROR_KIND_NETWORK = "network"
ERROR_KIND_AUTH = "auth"           # retry 금지
ERROR_KIND_INVALID = "invalid"     # retry 금지
ERROR_KIND_UNKNOWN = "unknown"


# 재시도 금지 에러 종류 — auth/invalid 류.
_NO_RETRY_KINDS: frozenset[str] = frozenset({ERROR_KIND_AUTH, ERROR_KIND_INVALID})


# ── RateLimitGuard ──────────────────────────────────────────────


class RateLimitGuard:
    """단일 (exchange, group) 의 rate-limit guard.

    공개 메서드:
      - can_call() -> AcquireDecision        (peek — 토큰 소비 안 함)
      - acquire(weight=1) -> AcquireDecision (토큰 소비 시도)
      - update_from_response(headers, status_code, body)  (응답 반영)
      - update_from_error(kind, headers=None) -> RetryDecision
      - snapshot() -> dict
      - reset() -> None
    """

    def __init__(
        self,
        policy: RateLimitPolicy,
        *,
        time_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ):
        self.policy = policy
        self._time_fn = time_fn or time.monotonic
        self._sleep_fn = sleep_fn or time.sleep
        self._bucket = TokenBucket(
            capacity=policy.capacity,
            refill_rate_per_sec=policy.refill_rate_per_sec,
            time_fn=self._time_fn,
            sleep_fn=self._sleep_fn,
        )
        self.state = GuardState()
        self._attempt: int = 0

    # ── 호출 가능 여부 ────────────────────────────────────────────

    def can_call(self, weight: float = 1.0) -> AcquireDecision:
        """peek — 토큰 소비 없이 호출 가능 여부 판단."""
        if self.policy.disabled:
            return AcquireDecision(
                allowed=False,
                reason=("group disabled by policy "
                        f"({self.policy.exchange}.{self.policy.group})"),
                remaining_tokens=self._bucket.available_tokens,
            )
        now = self._time_fn()
        if now < self.state.cooldown_until:
            return AcquireDecision(
                allowed=False,
                reason="cooldown active",
                wait_seconds=max(0.0, self.state.cooldown_until - now),
                remaining_tokens=self._bucket.available_tokens,
                cooldown_remaining=max(0.0, self.state.cooldown_until - now),
            )
        # safety_buffer 까지 고려해 보수적으로
        tokens = self._bucket.available_tokens
        need = float(weight) + float(self.policy.safety_buffer)
        if tokens + 1e-9 < need:
            return AcquireDecision(
                allowed=False,
                reason=(f"insufficient tokens: have {tokens:.3f}, "
                        f"need {need:.3f} (incl. safety_buffer)"),
                wait_seconds=max(0.0, (need - tokens) / self.policy.refill_rate_per_sec),
                remaining_tokens=tokens,
            )
        return AcquireDecision(
            allowed=True, reason="ok",
            remaining_tokens=tokens,
        )

    def acquire(self, weight: float = 1.0) -> AcquireDecision:
        """토큰 소비 시도. cooldown 중이면 소비 안 함."""
        self.state.total_calls += 1
        peek = self.can_call(weight)
        if not peek.allowed:
            self.state.total_throttled += 1
            return peek
        # 토큰 소비
        if self._bucket.acquire(float(weight)):
            self.state.total_acquired += 1
            return AcquireDecision(
                allowed=True, reason="ok",
                remaining_tokens=self._bucket.available_tokens,
            )
        # 경계 조건 (refill 직후 인접) — throttled
        self.state.total_throttled += 1
        return AcquireDecision(
            allowed=False,
            reason="token bucket empty (race)",
            wait_seconds=1.0 / self.policy.refill_rate_per_sec,
            remaining_tokens=self._bucket.available_tokens,
        )

    # ── 응답 반영 (성공) ──────────────────────────────────────────

    def update_from_response(
        self,
        *,
        headers: Mapping[str, str] | None = None,
        status_code: int | None = None,
        body: Any = None,
    ) -> None:
        """정상 응답의 헤더/payload 를 보고 state 갱신.

        rate-limit 에러는 별도로 ``update_from_error`` 호출 권장. 단, 헤더에
        Remaining-Req 또는 X-MBX-USED-WEIGHT 가 있으면 그것도 반영.
        """
        now = self._time_fn()
        self.state.last_response_at = now
        # 에러 응답이면 state.consecutive_failures 는 update_from_error 가 갱신.
        if status_code is None or status_code < 400:
            self.state.consecutive_failures = 0
        # 거래소별 헤더 인식 — exchange 명에 따라 분기.
        ex = self.policy.exchange.lower()
        if ex == "upbit":
            self._apply_upbit_headers(headers)
        elif ex == "binance":
            self._apply_binance_headers(headers)
        elif ex == "okx":
            # OKX 는 헤더보다 본문 code 가 핵심. body 가 dict 면 50011 감지.
            if body is not None:
                err = parse_okx_error(body, status_code=status_code)
                if err and err.get("is_rate_limit"):
                    self._enter_cooldown(
                        seconds=self.policy.cooldown_on_okx_50011_sec,
                        code=ERROR_KIND_OKX_50011,
                    )
                    self.state.total_okx_50011 += 1
                    self.state.last_error_code = ERROR_KIND_OKX_50011

    def _apply_upbit_headers(self, headers: Mapping[str, str] | None) -> None:
        if not headers:
            return
        rr = headers.get("Remaining-Req") or headers.get("remaining-req")
        if not rr:
            return
        parsed = parse_upbit_remaining_req(rr)
        if "sec" in parsed and isinstance(parsed["sec"], int):
            self.state.remaining = parsed["sec"]

    def _apply_binance_headers(self, headers: Mapping[str, str] | None) -> None:
        if not headers:
            return
        parsed = parse_binance_used_weight(dict(headers))
        if "used_weight_1m" in parsed:
            self.state.used_weight = parsed["used_weight_1m"]

    # ── 에러 반영 → RetryDecision ─────────────────────────────────

    def update_from_error(
        self,
        kind: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> RetryDecision:
        """에러 종류에 따라 cooldown 설정 + RetryDecision 반환.

        ``kind`` ∈ {429, 418, okx_50011, upbit_too_many, network, auth, invalid, unknown}
        """
        kind_norm = (kind or "").strip().lower()
        # 별칭 정규화.
        if kind_norm in {"429", "rate_limit", ERROR_KIND_429}:
            kind_norm = ERROR_KIND_429
        elif kind_norm in {"418", "ip_ban", ERROR_KIND_418}:
            kind_norm = ERROR_KIND_418
        elif kind_norm in {"50011", "okx_rate_limit", ERROR_KIND_OKX_50011}:
            kind_norm = ERROR_KIND_OKX_50011
        elif kind_norm in {"upbit_too_many", ERROR_KIND_UPBIT_TOO_MANY}:
            kind_norm = ERROR_KIND_UPBIT_TOO_MANY
        elif kind_norm in {"network", ERROR_KIND_NETWORK, "timeout", "connection"}:
            kind_norm = ERROR_KIND_NETWORK
        elif kind_norm in {"auth", ERROR_KIND_AUTH, "401", "403"}:
            kind_norm = ERROR_KIND_AUTH
        elif kind_norm in {"invalid", ERROR_KIND_INVALID, "400", "404"}:
            kind_norm = ERROR_KIND_INVALID
        elif kind_norm not in {
            ERROR_KIND_429, ERROR_KIND_418, ERROR_KIND_OKX_50011,
            ERROR_KIND_UPBIT_TOO_MANY, ERROR_KIND_NETWORK,
            ERROR_KIND_AUTH, ERROR_KIND_INVALID,
        }:
            kind_norm = ERROR_KIND_UNKNOWN

        now = self._time_fn()
        self.state.last_error_at = now
        self.state.last_error_code = kind_norm

        # cooldown 결정. Retry-After 헤더가 있으면 우선 적용 (429/418).
        retry_after = parse_retry_after(headers) if headers else None
        cooldown_sec = 0.0
        if kind_norm == ERROR_KIND_429:
            self.state.total_429 += 1
            cooldown_sec = (retry_after if retry_after is not None
                            else self.policy.cooldown_on_429_sec)
        elif kind_norm == ERROR_KIND_418:
            self.state.total_418 += 1
            cooldown_sec = (retry_after if retry_after is not None
                            else self.policy.cooldown_on_418_sec)
        elif kind_norm == ERROR_KIND_OKX_50011:
            self.state.total_okx_50011 += 1
            cooldown_sec = self.policy.cooldown_on_okx_50011_sec
        elif kind_norm == ERROR_KIND_UPBIT_TOO_MANY:
            cooldown_sec = self.policy.cooldown_on_429_sec
        elif kind_norm == ERROR_KIND_NETWORK:
            self.state.total_network_errors += 1
            cooldown_sec = self.policy.cooldown_on_network_sec

        if cooldown_sec > 0:
            self._enter_cooldown(seconds=cooldown_sec, code=kind_norm)

        # retry 결정.
        self.state.consecutive_failures += 1
        if kind_norm in _NO_RETRY_KINDS:
            self.state.total_retries_denied += 1
            return RetryDecision(
                should_retry=False,
                wait_seconds=0.0,
                reason=f"no-retry error kind: {kind_norm}",
                attempt=self._attempt,
                max_retries=self.policy.max_retries,
                cooldown_until=self.state.cooldown_until,
            )
        if self._attempt >= self.policy.max_retries:
            self.state.total_retries_denied += 1
            return RetryDecision(
                should_retry=False,
                wait_seconds=0.0,
                reason=(f"max retries exceeded "
                        f"({self._attempt}/{self.policy.max_retries})"),
                attempt=self._attempt,
                max_retries=self.policy.max_retries,
                cooldown_until=self.state.cooldown_until,
            )
        # exponential backoff (jitter 옵션은 본 모듈에서 끔 — 테스트 결정론 유지)
        backoff = min(
            self.policy.max_backoff_sec,
            self.policy.base_backoff_sec * (2 ** self._attempt),
        )
        # cooldown 이 더 길면 cooldown 우선.
        wait = max(backoff, cooldown_sec)
        self._attempt += 1
        self.state.total_retries_issued += 1
        return RetryDecision(
            should_retry=True,
            wait_seconds=wait,
            reason=f"retry after {wait:.2f}s ({kind_norm})",
            attempt=self._attempt,
            max_retries=self.policy.max_retries,
            cooldown_until=self.state.cooldown_until,
        )

    def reset_retry(self) -> None:
        """성공 후 호출자가 retry 카운터를 명시적으로 초기화."""
        self._attempt = 0

    # ── 내부: cooldown ────────────────────────────────────────────

    def _enter_cooldown(self, *, seconds: float, code: str) -> None:
        if seconds <= 0:
            return
        now = self._time_fn()
        new_until = now + float(seconds)
        # 더 긴 cooldown 유지 (덮어쓰기 방지)
        if new_until > self.state.cooldown_until:
            self.state.cooldown_until = new_until

    # ── 조회 / 리셋 ───────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        now = self._time_fn()
        cooldown_remaining = max(0.0, self.state.cooldown_until - now)
        return {
            "exchange": self.policy.exchange,
            "group": self.policy.group,
            "disabled": self.policy.disabled,
            "capacity": self.policy.capacity,
            "refill_rate_per_sec": self.policy.refill_rate_per_sec,
            "unit": self.policy.unit,
            "safety_buffer": self.policy.safety_buffer,
            "remaining_tokens": self._bucket.available_tokens,
            "remaining_header": self.state.remaining,
            "used_weight": self.state.used_weight,
            "cooldown_remaining_sec": cooldown_remaining,
            "consecutive_failures": self.state.consecutive_failures,
            "last_error_code": self.state.last_error_code,
            "current_retry_attempt": self._attempt,
            "max_retries": self.policy.max_retries,
            "stats": {
                "total_calls":         self.state.total_calls,
                "total_acquired":      self.state.total_acquired,
                "total_throttled":     self.state.total_throttled,
                "total_429":           self.state.total_429,
                "total_418":           self.state.total_418,
                "total_okx_50011":     self.state.total_okx_50011,
                "total_network_errors": self.state.total_network_errors,
                "total_retries_issued": self.state.total_retries_issued,
                "total_retries_denied": self.state.total_retries_denied,
            },
            "policy_notes": self.policy.notes,
        }

    def reset(self) -> None:
        """state + 토큰 버킷 초기화 (정책은 유지)."""
        self._bucket = TokenBucket(
            capacity=self.policy.capacity,
            refill_rate_per_sec=self.policy.refill_rate_per_sec,
            time_fn=self._time_fn,
            sleep_fn=self._sleep_fn,
        )
        self.state = GuardState()
        self._attempt = 0


# ── Registry ─────────────────────────────────────────────────────


class ExchangeRateLimitRegistry:
    """거래소·그룹 별 guard 싱글톤 보관.

    ``get(exchange, group)`` 호출 시 없으면 ``api_limits.get_default_policy`` 로
    자동 생성. 운영자가 명시적으로 ``register`` 로 커스텀 정책 주입 가능.
    """

    def __init__(
        self,
        *,
        time_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ):
        self._time_fn = time_fn
        self._sleep_fn = sleep_fn
        self._guards: dict[tuple[str, str], RateLimitGuard] = {}

    def register(
        self,
        policy: RateLimitPolicy,
        *,
        time_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> RateLimitGuard:
        key = (policy.exchange.lower(), policy.group.lower())
        guard = RateLimitGuard(
            policy,
            time_fn=time_fn or self._time_fn,
            sleep_fn=sleep_fn or self._sleep_fn,
        )
        self._guards[key] = guard
        return guard

    def get(self, exchange: str, group: str) -> RateLimitGuard:
        key = (exchange.lower(), group.lower())
        if key not in self._guards:
            policy = get_default_policy(exchange, group)
            self._guards[key] = RateLimitGuard(
                policy,
                time_fn=self._time_fn,
                sleep_fn=self._sleep_fn,
            )
        return self._guards[key]

    def known_pairs(self) -> list[tuple[str, str]]:
        return sorted(self._guards.keys())

    def snapshot_all(self) -> dict[str, Any]:
        items = [g.snapshot() for g in self._guards.values()]
        items.sort(key=lambda d: (d["exchange"], d["group"]))
        return {
            "guards": items,
            "count": len(items),
        }

    def reset_all(self) -> None:
        for g in self._guards.values():
            g.reset()


def build_default_registry(
    *,
    time_fn: Callable[[], float] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    preload: bool = True,
) -> ExchangeRateLimitRegistry:
    """기본 정책 (api_limits._DEFAULT_POLICIES) 전부 미리 등록한 registry."""
    reg = ExchangeRateLimitRegistry(time_fn=time_fn, sleep_fn=sleep_fn)
    if preload:
        for policy in list_default_policies().values():
            reg.register(policy, time_fn=time_fn, sleep_fn=sleep_fn)
    return reg


__all__ = (
    "AcquireDecision",
    "RetryDecision",
    "GuardState",
    "RateLimitGuard",
    "ExchangeRateLimitRegistry",
    "build_default_registry",
    "ERROR_KIND_429",
    "ERROR_KIND_418",
    "ERROR_KIND_OKX_50011",
    "ERROR_KIND_UPBIT_TOO_MANY",
    "ERROR_KIND_NETWORK",
    "ERROR_KIND_AUTH",
    "ERROR_KIND_INVALID",
    "ERROR_KIND_UNKNOWN",
)
