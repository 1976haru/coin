"""
Data Freshness — GPT의 베스트 구현 (frozen dataclass + 함수형)
WebSocket 재연결 중 / 시세 지연 시 신규 매수 차단
"""
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class FreshnessStatus:
    ok: bool
    age_seconds: float | None
    reason: str


@dataclass(frozen=True)
class DataFeedState:
    connected: bool
    reconnecting: bool
    last_message_at: datetime | None
    source: str = "unknown"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def check_timestamp_freshness(
    ts: datetime | None,
    max_age_seconds: float,
    now: datetime | None = None,
    label: str = "data",
) -> FreshnessStatus:
    ts  = _normalize(ts)
    now = _normalize(now) or utc_now()
    if ts is None:
        return FreshnessStatus(False, None, f"{label}: timestamp 없음")
    age = max(0.0, (now - ts).total_seconds())
    if max_age_seconds > 0 and age > max_age_seconds:
        return FreshnessStatus(False, age, f"{label}: 지연 {age:.2f}s > {max_age_seconds:.2f}s")
    return FreshnessStatus(True, age, f"{label}: 신선 {age:.2f}s")


def check_feed_freshness(
    feed: DataFeedState,
    max_age_seconds: float,
    now: datetime | None = None,
) -> FreshnessStatus:
    if not feed.connected:
        return FreshnessStatus(False, None, f"{feed.source}: 연결 끊김")
    if feed.reconnecting:
        return FreshnessStatus(False, None, f"{feed.source}: 재연결 중 — 신규 매수 금지")
    return check_timestamp_freshness(feed.last_message_at, max_age_seconds, now, feed.source)


def should_block_new_buy(*statuses: FreshnessStatus) -> tuple[bool, list[str]]:
    """BUY 신호 전 freshness 체크. 하나라도 stale이면 차단."""
    reasons = [s.reason for s in statuses if not s.ok]
    return bool(reasons), reasons
