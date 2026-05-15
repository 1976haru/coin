"""체크리스트 #26 API Rate Limit Guard — 회귀 테스트.

검증 (모의 시계 사용 — 실제 sleep 없이 동작):
  1. TokenBucket 기본 acquire/refill
  2. try_acquire_or_raise → RateLimitExceeded
  3. wait_and_acquire → mock sleep 으로 정확한 시간 대기
  4. wait_and_acquire timeout → RateLimitTimeout
  5. 잘못된 인자 (capacity ≤ 0, n > capacity)
  6. 거래소별 프리셋 (upbit/okx/binance + default)
  7. rate_limited 데코레이터
  8. 어댑터 동작 영향 없음 (Upbit/OKX/Binance 통합 — limiter 미적용 시 그대로)
"""
from __future__ import annotations
import pytest

from app.brokers import (
    TokenBucket, RateLimitExceeded, RateLimitTimeout,
    RATE_LIMITS, DEFAULT_SPEC,
    get_limiter_for, rate_limited,
)


# ── 모의 시계 ────────────────────────────────────────────────────

class FakeClock:
    """수동 진행 가능한 시계 + sleep 누적기."""

    def __init__(self, start: float = 0.0):
        self.now = float(start)
        self.sleep_calls: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.now += max(seconds, 0.0)


# ── 1. 기본 acquire/refill ───────────────────────────────────────

def test_initial_full_capacity():
    clock = FakeClock()
    b = TokenBucket(5, 1.0, time_fn=clock.time, sleep_fn=clock.sleep)
    assert b.available_tokens == 5.0


def test_acquire_consumes_token():
    clock = FakeClock()
    b = TokenBucket(5, 1.0, time_fn=clock.time, sleep_fn=clock.sleep)
    assert b.acquire() is True
    assert b.available_tokens == 4.0


def test_acquire_returns_false_when_empty():
    clock = FakeClock()
    b = TokenBucket(2, 1.0, time_fn=clock.time, sleep_fn=clock.sleep)
    b.acquire(); b.acquire()
    assert b.acquire() is False


def test_refill_proportional_to_elapsed_time():
    clock = FakeClock()
    b = TokenBucket(10, 5.0, time_fn=clock.time, sleep_fn=clock.sleep)
    # 모두 소비
    for _ in range(10):
        assert b.acquire()
    assert b.acquire() is False
    # 1초 경과 → 5 토큰 보충
    clock.now += 1.0
    assert abs(b.available_tokens - 5.0) < 1e-6


def test_refill_capped_at_capacity():
    clock = FakeClock()
    b = TokenBucket(3, 10.0, time_fn=clock.time, sleep_fn=clock.sleep)
    clock.now += 100.0  # 매우 긴 시간
    assert b.available_tokens == 3.0  # capacity 초과 안 함


def test_acquire_with_weight_n():
    clock = FakeClock()
    b = TokenBucket(10, 1.0, time_fn=clock.time, sleep_fn=clock.sleep)
    assert b.acquire(n=5) is True
    assert b.available_tokens == 5.0
    assert b.acquire(n=6) is False  # 5 < 6


# ── 2. try_acquire_or_raise ──────────────────────────────────────

def test_try_acquire_or_raise_passes_when_available():
    clock = FakeClock()
    b = TokenBucket(2, 1.0, time_fn=clock.time, sleep_fn=clock.sleep)
    b.try_acquire_or_raise()  # 1 → 1
    b.try_acquire_or_raise()  # 1 → 0


def test_try_acquire_or_raise_raises_when_empty():
    clock = FakeClock()
    b = TokenBucket(1, 1.0, time_fn=clock.time, sleep_fn=clock.sleep)
    b.try_acquire_or_raise()
    with pytest.raises(RateLimitExceeded):
        b.try_acquire_or_raise()


# ── 3. wait_and_acquire ──────────────────────────────────────────

def test_wait_and_acquire_succeeds_immediately_when_full():
    clock = FakeClock()
    b = TokenBucket(5, 1.0, time_fn=clock.time, sleep_fn=clock.sleep)
    b.wait_and_acquire()
    assert clock.sleep_calls == []


def test_wait_and_acquire_sleeps_when_empty_then_succeeds():
    clock = FakeClock()
    b = TokenBucket(1, 2.0, time_fn=clock.time, sleep_fn=clock.sleep)
    b.acquire()  # 0 tokens
    b.wait_and_acquire()
    # 1 token 회복 = 1/2.0 = 0.5초 sleep
    assert sum(clock.sleep_calls) >= 0.5
    assert clock.now >= 0.5


def test_wait_and_acquire_handles_weight_n():
    clock = FakeClock()
    b = TokenBucket(5, 5.0, time_fn=clock.time, sleep_fn=clock.sleep)
    # 모두 소비
    b.acquire(n=5)
    b.wait_and_acquire(n=5)
    # 5/5 = 1.0초
    assert clock.now >= 1.0


# ── 4. timeout ───────────────────────────────────────────────────

def test_wait_and_acquire_timeout_raises():
    clock = FakeClock()
    b = TokenBucket(1, 0.5, time_fn=clock.time, sleep_fn=clock.sleep)
    b.acquire()  # 0 tokens

    # 0.5/0.5 = 1.0초 필요한데 timeout=0.3
    with pytest.raises(RateLimitTimeout):
        b.wait_and_acquire(timeout=0.3)


def test_wait_and_acquire_no_timeout_default():
    """timeout 미지정 시 무제한 대기."""
    clock = FakeClock()
    b = TokenBucket(1, 100.0, time_fn=clock.time, sleep_fn=clock.sleep)
    b.acquire()
    b.wait_and_acquire()  # 매우 빨리 회복 → 통과


# ── 5. 잘못된 인자 ───────────────────────────────────────────────

def test_capacity_must_be_positive():
    with pytest.raises(ValueError):
        TokenBucket(0, 1.0)
    with pytest.raises(ValueError):
        TokenBucket(-1, 1.0)


def test_refill_rate_must_be_positive():
    with pytest.raises(ValueError):
        TokenBucket(5, 0)
    with pytest.raises(ValueError):
        TokenBucket(5, -1.0)


def test_wait_and_acquire_n_greater_than_capacity_raises():
    """capacity 보다 큰 n 은 영원히 채워지지 않으므로 즉시 거부."""
    clock = FakeClock()
    b = TokenBucket(5, 1.0, time_fn=clock.time, sleep_fn=clock.sleep)
    with pytest.raises(ValueError):
        b.wait_and_acquire(n=10)


# ── 6. 거래소별 프리셋 ───────────────────────────────────────────

def test_presets_exist_for_main_exchanges():
    for key in ("upbit_public", "okx_public", "binance_public"):
        assert key in RATE_LIMITS
        spec = RATE_LIMITS[key]
        assert spec.capacity > 0
        assert spec.refill_rate_per_sec > 0


def test_get_limiter_for_known_exchange():
    clock = FakeClock()
    b = get_limiter_for("upbit", "public", time_fn=clock.time, sleep_fn=clock.sleep)
    assert b.capacity == 10
    assert b.refill_rate == 10.0


def test_get_limiter_for_unknown_falls_back_to_default():
    b = get_limiter_for("unknown_exchange")
    assert b.capacity == DEFAULT_SPEC.capacity
    assert b.refill_rate == DEFAULT_SPEC.refill_rate_per_sec


def test_get_limiter_case_insensitive():
    a = get_limiter_for("UPBIT", "PUBLIC")
    b = get_limiter_for("upbit", "public")
    assert a.capacity == b.capacity
    assert a.refill_rate == b.refill_rate


def test_get_limiter_default_tier_is_public():
    a = get_limiter_for("upbit")
    b = get_limiter_for("upbit", "public")
    assert a.capacity == b.capacity


# ── 7. rate_limited 데코레이터 ───────────────────────────────────

def test_rate_limited_decorator_delays_calls_when_empty():
    clock = FakeClock()
    b = TokenBucket(1, 1.0, time_fn=clock.time, sleep_fn=clock.sleep)
    calls: list[int] = []

    @rate_limited(b)
    def f(x):
        calls.append(x)
        return x * 2

    assert f(1) == 2
    assert f(2) == 4   # 2번째 호출 — 토큰 부족 → sleep
    assert calls == [1, 2]
    assert sum(clock.sleep_calls) >= 1.0


def test_rate_limited_decorator_passes_args_kwargs():
    clock = FakeClock()
    b = TokenBucket(5, 1.0, time_fn=clock.time, sleep_fn=clock.sleep)

    @rate_limited(b)
    def g(a, b, *, c=0):
        return a + b + c

    assert g(1, 2, c=3) == 6


def test_rate_limited_with_weight_n():
    clock = FakeClock()
    b = TokenBucket(10, 5.0, time_fn=clock.time, sleep_fn=clock.sleep)

    @rate_limited(b, n=3)
    def heavy():
        return "ok"

    heavy()  # 7 tokens
    heavy()  # 4 tokens
    heavy()  # 1 token
    heavy()  # 0 then wait
    # 4번째 호출에서 (3-1)/5 = 0.4초 sleep
    assert clock.now >= 0.4


# ── 8. 어댑터 통합 (선택) ────────────────────────────────────────

def test_limiter_can_wrap_adapter_calls():
    """rate_limited 데코레이터로 어댑터 메서드를 감쌀 수 있는지."""
    from app.brokers import UpbitAdapter

    class FakeUpbit:
        def __init__(self):
            self.calls = 0
        def get_current_price(self, s):
            self.calls += 1
            return 50_000_000.0
        def get_orderbook(self, s):
            return [{"orderbook_units": [
                {"ask_price": 50_010_000, "bid_price": 49_990_000,
                 "ask_size": 1.0, "bid_size": 1.0},
            ]}]

    clock = FakeClock()
    fake = FakeUpbit()
    b = TokenBucket(1, 5.0, time_fn=clock.time, sleep_fn=clock.sleep)
    a = UpbitAdapter(client=fake)

    @rate_limited(b)
    def fetch_with_limit(symbol):
        return a.fetch_ticker(symbol)

    fetch_with_limit("BTC")
    fetch_with_limit("BTC")  # 두 번째는 sleep
    assert fake.calls == 2
    assert clock.now > 0.0
