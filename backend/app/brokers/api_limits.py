"""거래소별 API rate-limit 정책 카탈로그 — 체크리스트 #26.

원칙:
  - 본 모듈은 *기본값* 만 제공. 운영자가 실제 한도를 공식 문서에서 재확인 후
    조정해야 한다 (한도 정책은 변동 가능).
  - read-only(public/quotation) 와 order/private 그룹을 분리.
  - 모든 정책은 안전 마진(safety_buffer) 을 포함 — 공식 한도보다 보수적으로 설정.
  - 본 모듈은 순수 데이터 + parser. 실제 sleep / HTTP 호출 없음.

거래소 한도 출처 (2026-05 기준 — 운영 전 재확인 필수):
  - Upbit: Remaining-Req 헤더로 분/초 잔여 알림. 공식 한도는 group 별 sec=10 수준.
  - OKX:   endpoint 별 weight + IP 기반. 50011 = "Requests too frequent".
  - Binance: 1200 weight/minute (1m window), 50 orders/10s, X-MBX-USED-WEIGHT 헤더.
            429 = rate limit, 418 = IP ban (retry after).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Mapping


# ── 정책 dataclass ───────────────────────────────────────────────


@dataclass(frozen=True)
class RateLimitPolicy:
    """거래소·그룹 별 rate-limit 정책.

    capacity / refill_rate_per_sec 는 토큰 버킷 파라미터.
    cooldown_on_* 는 특정 에러 발생 시 추가 대기 시간.

    `unit` 은 ``"req"`` (요청 1건 = 토큰 1개) 또는 ``"weight"`` (요청별 weight
    차감) 둘 중 하나. binance public 은 weight 기반.
    """

    exchange: str
    group: str
    capacity: float
    refill_rate_per_sec: float
    safety_buffer: int = 1
    unit: str = "req"                        # "req" | "weight"
    # 에러 발생 시 cooldown (초)
    cooldown_on_429_sec: float = 5.0
    cooldown_on_418_sec: float = 60.0        # Binance IP ban hint
    cooldown_on_okx_50011_sec: float = 3.0
    cooldown_on_network_sec: float = 0.5
    # 재시도 정책 — exponential backoff
    max_retries: int = 2
    base_backoff_sec: float = 0.5
    max_backoff_sec: float = 8.0
    # 본 그룹이 비활성(주문 미지원 등)인지
    disabled: bool = False
    notes: str = ""

    def __post_init__(self):
        if self.capacity <= 0:
            raise ValueError("capacity must be > 0")
        if self.refill_rate_per_sec <= 0:
            raise ValueError("refill_rate_per_sec must be > 0")
        if self.unit not in ("req", "weight"):
            raise ValueError(f"unit must be 'req' or 'weight', got {self.unit!r}")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")


# ── 거래소별 프리셋 ────────────────────────────────────────────────


# 본 단계는 read-only 중심 + private/order 는 보수적으로 disabled 또는 매우 낮은 한도.
# 운영 전 공식 문서 재확인 필요.
_DEFAULT_POLICIES: dict[tuple[str, str], RateLimitPolicy] = {
    # ── Upbit ────────────────────────────────────────────────────
    ("upbit", "quotation"): RateLimitPolicy(
        exchange="upbit", group="quotation",
        capacity=10, refill_rate_per_sec=10.0,   # ~10 req/sec
        safety_buffer=1,
        notes=("Upbit public quotation. Remaining-Req 헤더로 동적 갱신. "
               "운영 전 공식 문서 재확인 필요."),
    ),
    ("upbit", "exchange"): RateLimitPolicy(
        exchange="upbit", group="exchange",
        capacity=8, refill_rate_per_sec=8.0,
        safety_buffer=1, disabled=True,
        notes="Private/order group — 본 단계에서는 호출 금지 (disabled=True).",
    ),

    # ── OKX ──────────────────────────────────────────────────────
    ("okx", "public"): RateLimitPolicy(
        exchange="okx", group="public",
        capacity=20, refill_rate_per_sec=20.0,
        safety_buffer=2,
        cooldown_on_okx_50011_sec=3.0,
        notes=("OKX public market data. 코드 50011=rate-limit. "
               "운영 전 공식 문서 재확인 필요."),
    ),
    ("okx", "private"): RateLimitPolicy(
        exchange="okx", group="private",
        capacity=10, refill_rate_per_sec=10.0,
        safety_buffer=1, disabled=True,
        notes="OKX private/account — disabled in this phase.",
    ),
    ("okx", "trade"): RateLimitPolicy(
        exchange="okx", group="trade",
        capacity=10, refill_rate_per_sec=10.0,
        safety_buffer=1, disabled=True,
        notes="OKX trade endpoints — disabled in this phase.",
    ),

    # ── Binance ──────────────────────────────────────────────────
    ("binance", "spot_public"): RateLimitPolicy(
        exchange="binance", group="spot_public",
        capacity=1200.0, refill_rate_per_sec=20.0,   # 1200 weight/min
        safety_buffer=240,                            # 80% soft limit
        unit="weight",
        cooldown_on_429_sec=10.0,
        cooldown_on_418_sec=120.0,                   # IP ban — 보수적으로 2분
        notes=("Binance Spot public. X-MBX-USED-WEIGHT(-1M) 헤더로 동적 갱신. "
               "429=rate-limit, 418=IP ban (Retry-After 우선). "
               "운영 전 공식 문서 재확인 필요."),
    ),
    ("binance", "spot_private"): RateLimitPolicy(
        exchange="binance", group="spot_private",
        capacity=10, refill_rate_per_sec=10.0,
        safety_buffer=2, disabled=True,
        notes="Binance Spot private — disabled until regulatory review (#23).",
    ),
    ("binance", "futures"): RateLimitPolicy(
        exchange="binance", group="futures",
        capacity=10, refill_rate_per_sec=10.0,
        safety_buffer=2, disabled=True,
        notes="Binance Futures — disabled (#67 Futures Scope).",
    ),

    # ── Mock / Paper ─────────────────────────────────────────────
    ("mock", "default"): RateLimitPolicy(
        exchange="mock", group="default",
        capacity=10_000, refill_rate_per_sec=10_000.0,
        safety_buffer=0,
        notes="Mock — high limit; tests may override with stricter policy.",
    ),
    ("paper", "default"): RateLimitPolicy(
        exchange="paper", group="default",
        capacity=10_000, refill_rate_per_sec=10_000.0,
        safety_buffer=0,
        notes="Paper — high limit; same as mock.",
    ),
}


def list_default_policies() -> dict[tuple[str, str], RateLimitPolicy]:
    """기본 정책 사본 (수정 안전)."""
    return dict(_DEFAULT_POLICIES)


def get_default_policy(exchange: str, group: str) -> RateLimitPolicy:
    """기본 정책 조회. 없으면 보수적 default 반환."""
    key = (exchange.lower(), group.lower())
    if key in _DEFAULT_POLICIES:
        return _DEFAULT_POLICIES[key]
    return RateLimitPolicy(
        exchange=exchange.lower(),
        group=group.lower(),
        capacity=5, refill_rate_per_sec=5.0,
        safety_buffer=1,
        notes="default conservative policy (unknown exchange/group)",
    )


# ── 헤더 / 에러 parser 통합 ─────────────────────────────────────────


def parse_upbit_remaining_req(header: str | None) -> dict:
    """Upbit ``Remaining-Req`` 헤더 → dict.

    예: ``group=market; min=599; sec=9`` → ``{"group": "market", "min": 599, "sec": 9}``.
    """
    # upbit_rate_limit.parse_remaining_req 를 재사용 — backward-compat wrapper.
    from .upbit_rate_limit import parse_remaining_req
    return parse_remaining_req(header)


def parse_okx_error(
    payload: object,
    status_code: int | None = None,
) -> dict | None:
    """OKX 응답 payload → ``{"code": ..., "msg": ..., "is_rate_limit": bool}``.

    정상(code="0") 이면 None 반환. status_code 가 429 면 강제 rate-limit 인식.
    """
    from .okx_rate_limit import parse_okx_api_error
    err = parse_okx_api_error(payload)
    is_rate_limit = err.is_rate_limit or (status_code == 429)
    if err.is_ok and status_code != 429:
        return None
    return {
        "code": err.code,
        "msg": err.msg,
        "is_rate_limit": is_rate_limit,
    }


def parse_binance_used_weight(headers: Mapping[str, str] | None) -> dict:
    """Binance ``X-MBX-USED-WEIGHT(-1M)`` + order-count 헤더 파싱."""
    from .binance_rate_limit import parse_binance_used_weight as _parse
    return _parse(dict(headers) if headers else None)


def parse_retry_after(headers: Mapping[str, str] | None) -> float | None:
    """``Retry-After`` 헤더 파싱 — 초 단위 float 또는 None.

    HTTP 사양: 정수 초 또는 HTTP-date. 본 함수는 정수/실수만 인식 (HTTP-date 는
    실제 거래소가 거의 쓰지 않으므로 무시 — 운영 시 재확인 필요).
    """
    if not headers:
        return None
    # case-insensitive lookup
    lower = {str(k).lower(): v for k, v in headers.items()}
    raw = lower.get("retry-after")
    if raw is None:
        return None
    try:
        v = float(str(raw).strip())
        return v if v >= 0 else None
    except (TypeError, ValueError):
        return None


__all__ = (
    "RateLimitPolicy",
    "list_default_policies",
    "get_default_policy",
    "parse_upbit_remaining_req",
    "parse_okx_error",
    "parse_binance_used_weight",
    "parse_retry_after",
)
