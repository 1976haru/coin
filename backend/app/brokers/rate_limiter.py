"""API Rate Limit Guard — 체크리스트 #26.

토큰 버킷 기반 호출 빈도 제한. 거래소별 기본 한도를 프리셋으로 제공하며,
어댑터/Collector 가 명시적으로 wait_and_acquire 또는 try_acquire_or_raise 를
호출해 호출 시점에 throttle/backoff 한다.

ccxt 의 ``enableRateLimit=True`` 와 중복 적용 가능 — ccxt 가 client-side throttle 을
자체 제공하나, 본 모듈은 어댑터 외부에서 균일한 정책을 강제하기 위한 보강 layer.

설계 원칙:
  - 순수 알고리즘 (외부 I/O 없음) — 테스트는 ``time_fn``/``sleep_fn`` 주입.
  - 동기 API. async 가 필요하면 별도 wrapper.
  - 토큰 부족 시 두 가지 정책 중 선택: wait (blocking) 또는 raise.
"""
from __future__ import annotations
import functools
import time
from dataclasses import dataclass
from typing import Callable, TypeVar


T = TypeVar("T")


# ── 예외 ──────────────────────────────────────────────────────────

class RateLimitExceeded(RuntimeError):
    """try_acquire_or_raise 에서 토큰 부족 시."""


class RateLimitTimeout(TimeoutError):
    """wait_and_acquire 가 timeout 안에 토큰을 얻지 못한 경우."""


# ── 토큰 버킷 ─────────────────────────────────────────────────────

class TokenBucket:
    """표준 token bucket — capacity 토큰, refill_rate_per_sec 으로 보충.

    요청 1건 = 토큰 1개 소비 (가중치는 ``acquire(n)`` 로 변경 가능).
    공유 자원이 아니므로 단일 스레드 가정. 멀티스레드는 호출 측에서 lock.
    """

    def __init__(
        self,
        capacity: float,
        refill_rate_per_sec: float,
        *,
        time_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ):
        if capacity <= 0:
            raise ValueError(f"capacity must be > 0 (got {capacity})")
        if refill_rate_per_sec <= 0:
            raise ValueError(f"refill_rate_per_sec must be > 0 (got {refill_rate_per_sec})")
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate_per_sec)
        self._time_fn = time_fn or time.monotonic
        self._sleep_fn = sleep_fn or time.sleep
        self._tokens: float = float(capacity)
        self._last: float = self._time_fn()

    # ── 내부: 토큰 보충 ───────────────────────────────────────────

    def _refill(self) -> None:
        now = self._time_fn()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
            self._last = now

    # ── public API ────────────────────────────────────────────────

    def acquire(self, n: float = 1.0) -> bool:
        """비차단 시도. 토큰이 충분하면 소비하고 True, 아니면 False."""
        self._refill()
        if self._tokens + 1e-9 >= n:
            self._tokens -= n
            return True
        return False

    def try_acquire_or_raise(self, n: float = 1.0) -> None:
        """토큰 부족 시 RateLimitExceeded."""
        if not self.acquire(n):
            raise RateLimitExceeded(
                f"rate limit exceeded: need {n}, have {self._tokens:.3f}"
            )

    def wait_and_acquire(self, n: float = 1.0, timeout: float | None = None) -> None:
        """차단 대기. 토큰이 모일 때까지 sleep_fn 으로 대기 후 소비.

        timeout 지정 시 deadline 까지 토큰을 얻지 못하면 RateLimitTimeout.
        """
        if n > self.capacity:
            raise ValueError(f"requested {n} > capacity {self.capacity}")
        deadline = (self._time_fn() + timeout) if timeout is not None else None

        while True:
            self._refill()
            if self._tokens + 1e-9 >= n:
                self._tokens -= n
                return
            needed = n - self._tokens
            wait_time = needed / self.refill_rate
            if deadline is not None:
                remaining = deadline - self._time_fn()
                if remaining <= 0:
                    raise RateLimitTimeout(
                        f"timeout {timeout}s while waiting for {n} tokens"
                    )
                wait_time = min(wait_time, remaining)
            self._sleep_fn(max(wait_time, 0.0))

    @property
    def available_tokens(self) -> float:
        """현재 사용 가능한 토큰 수 (refill 적용 후)."""
        self._refill()
        return self._tokens

    def __repr__(self) -> str:
        return (f"TokenBucket(capacity={self.capacity}, "
                f"refill={self.refill_rate}/s, tokens={self._tokens:.3f})")


# ── 거래소별 프리셋 ───────────────────────────────────────────────

@dataclass(frozen=True)
class RateLimitSpec:
    capacity: float
    refill_rate_per_sec: float


# 거래소 공식 문서 기준 보수적 설정. 실제 한도보다 낮춰 안전 마진.
# 키 형식: f"{exchange}_{tier}"  tier ∈ {"public", "private"}
RATE_LIMITS: dict[str, RateLimitSpec] = {
    # Upbit: 공개 10 req/sec, 사설 8 req/sec (0.5 sec window)
    "upbit_public":   RateLimitSpec(capacity=10, refill_rate_per_sec=10.0),
    "upbit_private":  RateLimitSpec(capacity=8,  refill_rate_per_sec=8.0),
    # OKX: 공개 weight 기반 — 단순화해 20 req/sec
    "okx_public":     RateLimitSpec(capacity=20, refill_rate_per_sec=20.0),
    "okx_private":    RateLimitSpec(capacity=20, refill_rate_per_sec=20.0),
    # Binance: 1200 weight/min ≈ 20 req/sec (가벼운 호출 기준)
    "binance_public": RateLimitSpec(capacity=20, refill_rate_per_sec=20.0),
    "binance_private":RateLimitSpec(capacity=10, refill_rate_per_sec=10.0),
}

# 보수적 default — 알 수 없는 거래소
DEFAULT_SPEC = RateLimitSpec(capacity=5, refill_rate_per_sec=5.0)


def get_limiter_for(
    exchange: str,
    tier: str = "public",
    *,
    time_fn: Callable[[], float] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> TokenBucket:
    """거래소·tier 별 기본 limiter 인스턴스 생성.

    매 호출 시 새 인스턴스 — 호출자가 싱글톤으로 보관.
    """
    key = f"{exchange.lower()}_{tier.lower()}"
    spec = RATE_LIMITS.get(key, DEFAULT_SPEC)
    return TokenBucket(
        capacity=spec.capacity,
        refill_rate_per_sec=spec.refill_rate_per_sec,
        time_fn=time_fn,
        sleep_fn=sleep_fn,
    )


# ── 데코레이터 ────────────────────────────────────────────────────

def rate_limited(limiter: TokenBucket, *, n: float = 1.0,
                  timeout: float | None = None):
    """동기 함수 호출 전에 ``limiter.wait_and_acquire`` 호출하는 데코레이터.

    사용:
        bucket = get_limiter_for("upbit")
        @rate_limited(bucket)
        def fetch_x(): ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            limiter.wait_and_acquire(n=n, timeout=timeout)
            return func(*args, **kwargs)
        return wrapper
    return decorator
