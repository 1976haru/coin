"""Data Freshness — 시세 신선도 안전장치 (체크리스트 #16).

본 모듈은 두 계층을 제공한다.

  1. **순수 함수 계층** (legacy, 변경 없음):
       - `FreshnessStatus`, `DataFeedState`
       - `check_timestamp_freshness(ts, max_age_seconds, ...)`
       - `check_feed_freshness(feed, max_age_seconds, ...)`
       - `should_block_new_buy(*statuses)`
     OrderGateway / strategies / 기존 코드가 그대로 사용.

  2. **FreshnessTracker** (#16 신규):
       - symbol/exchange/data_type/timeframe 단위로 last_seen_at 추적
       - reconnecting 상태 관리 (글로벌 / per-exchange / per-(symbol,exchange))
       - `evaluate_for_order(symbol, exchange, side, ...)` →
         BUY/ENTER/OPEN 계열은 stale/reconnecting 이면 block
         SELL/EXIT/CLOSE 계열은 위험 축소 동작이므로 기본 허용

설계 원칙 (CLAUDE.md §2.5):
  - WebSocket reconnecting 또는 stale 시 신규 진입(BUY/OPEN) 자동 차단
  - 청산(SELL/EXIT/CLOSE) 은 위험 축소이므로 stale 에서도 허용
  - tracker 는 메모리 기반 — DB 테이블 추가 없음
  - 실 WebSocket 구현은 본 단계 범위 밖. tracker 는 상태 전이만 제공.
"""
from __future__ import annotations
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


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
    """BUY 신호 전 freshness 체크. 하나라도 stale 이면 차단."""
    reasons = [s.reason for s in statuses if not s.ok]
    return bool(reasons), reasons


# ── 체크리스트 #16: FreshnessTracker ────────────────────────────

# 진입(엔트리) / 청산(엑시트) 사이드 분류 — stale guard 가 다르게 동작.
ENTRY_SIDES: frozenset[str] = frozenset({
    "BUY", "ENTER", "OPEN", "OPEN_LONG", "OPEN_SHORT",
    "OPEN_REVERSE_KIMP",  # KimpStrategy 기존 컨벤션 호환
})
EXIT_SIDES: frozenset[str] = frozenset({
    "SELL", "EXIT", "CLOSE", "CLOSE_LONG", "CLOSE_SHORT",
})


def is_entry_side(side: str) -> bool:
    """BUY/OPEN/ENTER 류 (신규 진입)."""
    return (side or "").upper() in ENTRY_SIDES


def is_exit_side(side: str) -> bool:
    """SELL/EXIT/CLOSE 류 (위험 축소)."""
    return (side or "").upper() in EXIT_SIDES


@dataclass(frozen=True)
class FreshnessKey:
    """수집 단위 식별자.

    timeframe 은 OHLCV 에만 의미가 있다 (ticker/orderbook/funding/fx 는 None).
    """
    symbol: str
    exchange: str
    data_type: str            # ticker / ohlcv / orderbook / funding / fx
    timeframe: str | None = None


@dataclass(frozen=True)
class FreshnessPolicy:
    """data_type 별 stale 허용 한도 + BUY 차단 정책.

    환경변수 매핑:
      MARKET_FRESHNESS_TICKER_MAX_AGE_SECONDS    (default 30)
      MARKET_FRESHNESS_ORDERBOOK_MAX_AGE_SECONDS (default 10)
      MARKET_FRESHNESS_OHLCV_MAX_AGE_SECONDS     (default 300)
      MARKET_FRESHNESS_FUNDING_MAX_AGE_SECONDS   (default 3600)
      MARKET_FRESHNESS_FX_MAX_AGE_SECONDS        (default 300)
      MARKET_BLOCK_BUY_WHEN_STALE                (default true)
      MARKET_BLOCK_BUY_WHEN_RECONNECTING         (default true)
    """
    ticker_max_age_sec:    float = 30.0
    orderbook_max_age_sec: float = 10.0
    ohlcv_max_age_sec:     float = 300.0
    funding_max_age_sec:   float = 3600.0
    fx_max_age_sec:        float = 300.0
    block_buy_when_stale:        bool = True
    block_buy_when_reconnecting: bool = True

    def max_age_for(self, data_type: str, timeframe: str | None = None) -> float:
        dt = (data_type or "").lower()
        if dt == "ticker":
            return self.ticker_max_age_sec
        if dt == "orderbook":
            return self.orderbook_max_age_sec
        if dt == "ohlcv":
            return self.ohlcv_max_age_sec
        if dt == "funding":
            return self.funding_max_age_sec
        if dt == "fx":
            return self.fx_max_age_sec
        # 모르는 data_type 은 보수적으로 짧게 잡는다.
        return self.ticker_max_age_sec


@dataclass
class FreshnessRecord:
    key: FreshnessKey
    last_seen_at: datetime | None = None
    update_count: int = 0


@dataclass(frozen=True)
class ReconnectScope:
    """reconnecting 적용 범위. 각 필드가 None 이면 wildcard 매치."""
    symbol:    str | None = None
    exchange:  str | None = None
    data_type: str | None = None


def is_stale(
    last_seen_at: datetime | None,
    max_age_seconds: float,
    now: datetime | None = None,
) -> bool:
    """순수 함수: last_seen_at 이 max_age_seconds 이내인지 판정.

    - last_seen_at=None → True (missing 은 stale 로 간주)
    - max_age_seconds<=0 → True (정책 미설정/잘못 → 안전하게 stale)
    - future timestamp (now 보다 미래) → True (clock skew, 안전하게 stale)
    """
    if last_seen_at is None:
        return True
    if max_age_seconds <= 0:
        return True
    now_n  = _normalize(now) or utc_now()
    seen_n = _normalize(last_seen_at)
    if seen_n is None:
        return True
    if seen_n > now_n:
        return True
    age = (now_n - seen_n).total_seconds()
    return age > max_age_seconds


def compute_lag_seconds(
    last_seen_at: datetime | None,
    now: datetime | None = None,
) -> float | None:
    """현재까지 경과 초. last_seen_at=None → None. future → 0.0."""
    if last_seen_at is None:
        return None
    now_n  = _normalize(now) or utc_now()
    seen_n = _normalize(last_seen_at)
    if seen_n is None:
        return None
    return max(0.0, (now_n - seen_n).total_seconds())


class FreshnessTracker:
    """symbol/exchange/data_type/timeframe 별 마지막 수신 시각 + reconnecting 상태.

    스레드-안전. 메모리 기반. 본 단계에서는 DB 영속화 없음.
    """

    def __init__(self, policy: FreshnessPolicy | None = None):
        self.policy = policy or FreshnessPolicy()
        self._records: dict[FreshnessKey, FreshnessRecord] = {}
        # ReconnectScope → reason
        self._reconnects: dict[ReconnectScope, str] = {}
        self._lock = threading.RLock()

    # ── Write ──────────────────────────────────────────────────────

    def mark_seen(
        self,
        symbol: str,
        exchange: str,
        data_type: str,
        timeframe: str | None = None,
        seen_at: datetime | None = None,
    ) -> FreshnessRecord:
        """수집 성공 시 호출. seen_at=None → now."""
        seen = _normalize(seen_at) or utc_now()
        key = FreshnessKey(symbol=symbol, exchange=exchange,
                           data_type=(data_type or "").lower(),
                           timeframe=timeframe)
        with self._lock:
            rec = self._records.get(key)
            if rec is None:
                rec = FreshnessRecord(key=key, last_seen_at=seen, update_count=1)
            else:
                # 더 최신 timestamp 만 기록 (out-of-order 방지)
                if rec.last_seen_at is None or seen >= rec.last_seen_at:
                    rec.last_seen_at = seen
                rec.update_count += 1
            self._records[key] = rec
            return rec

    def mark_reconnecting(
        self,
        *,
        symbol: str | None = None,
        exchange: str | None = None,
        data_type: str | None = None,
        reason: str = "",
    ) -> ReconnectScope:
        """주어진 범위에 reconnecting 표시. 빈 범위(모두 None) 는 글로벌."""
        scope = ReconnectScope(symbol=symbol, exchange=exchange,
                               data_type=(data_type.lower() if data_type else None))
        with self._lock:
            self._reconnects[scope] = reason or "reconnecting"
        return scope

    def clear_reconnecting(
        self,
        *,
        symbol: str | None = None,
        exchange: str | None = None,
        data_type: str | None = None,
    ) -> bool:
        scope = ReconnectScope(symbol=symbol, exchange=exchange,
                               data_type=(data_type.lower() if data_type else None))
        with self._lock:
            return self._reconnects.pop(scope, None) is not None

    def reset(self) -> None:
        """테스트 전용 — 전체 초기화."""
        with self._lock:
            self._records.clear()
            self._reconnects.clear()

    # ── Read ───────────────────────────────────────────────────────

    def get_record(
        self,
        symbol: str,
        exchange: str,
        data_type: str,
        timeframe: str | None = None,
    ) -> FreshnessRecord | None:
        key = FreshnessKey(symbol=symbol, exchange=exchange,
                           data_type=data_type.lower(),
                           timeframe=timeframe)
        with self._lock:
            return self._records.get(key)

    def is_reconnecting(
        self,
        symbol: str | None = None,
        exchange: str | None = None,
        data_type: str | None = None,
    ) -> tuple[bool, str]:
        """주어진 (symbol, exchange, data_type) 이 reconnecting 범위에 포함되는지.

        scope 의 각 필드가 None 이면 wildcard 매치.
        """
        dt = data_type.lower() if data_type else None
        with self._lock:
            for scope, reason in self._reconnects.items():
                if scope.symbol not in (None, symbol):
                    continue
                if scope.exchange not in (None, exchange):
                    continue
                if scope.data_type not in (None, dt):
                    continue
                return True, reason
        return False, ""

    def reconnecting_scopes(self) -> list[tuple[ReconnectScope, str]]:
        with self._lock:
            return list(self._reconnects.items())

    # ── 평가 ───────────────────────────────────────────────────────

    def evaluate(
        self,
        symbol: str,
        exchange: str,
        data_type: str,
        timeframe: str | None = None,
        now: datetime | None = None,
    ) -> FreshnessStatus:
        """단일 (symbol, exchange, data_type) freshness 평가.

        reconnecting 이 있으면 그 사유를 먼저 반환한다. 그렇지 않으면 stale 체크.
        """
        label = f"{exchange}:{symbol}:{data_type}" + (f":{timeframe}" if timeframe else "")
        reconnecting, reason = self.is_reconnecting(symbol, exchange, data_type)
        if reconnecting:
            return FreshnessStatus(False, None, f"{label}: 재연결 중 ({reason or 'reconnecting'})")
        return self._evaluate_stale_only(symbol, exchange, data_type, timeframe, now=now)

    def _evaluate_stale_only(
        self,
        symbol: str,
        exchange: str,
        data_type: str,
        timeframe: str | None = None,
        now: datetime | None = None,
    ) -> FreshnessStatus:
        """reconnecting 을 무시하고 stale 여부만 평가. 정책 토글 분리용."""
        label = f"{exchange}:{symbol}:{data_type}" + (f":{timeframe}" if timeframe else "")
        rec = self.get_record(symbol, exchange, data_type, timeframe)
        max_age = self.policy.max_age_for(data_type, timeframe)
        last = rec.last_seen_at if rec else None
        if last is None:
            return FreshnessStatus(False, None, f"{label}: 수신 기록 없음")
        return check_timestamp_freshness(last, max_age, now=now, label=label)

    def evaluate_required(
        self,
        required_keys: Iterable[tuple[str, str, str] | tuple[str, str, str, str | None]],
        now: datetime | None = None,
    ) -> list[FreshnessStatus]:
        """여러 key 의 freshness 를 한 번에 평가. tuple = (symbol, exchange, data_type[, timeframe])."""
        out: list[FreshnessStatus] = []
        for k in required_keys:
            if len(k) == 3:
                sym, ex, dt = k
                tf = None
            else:
                sym, ex, dt, tf = k  # type: ignore[misc]
            out.append(self.evaluate(sym, ex, dt, tf, now=now))
        return out

    def can_open_new_position(
        self,
        symbol: str,
        exchange: str,
        required_data_types: Iterable[str] = ("ticker",),
        now: datetime | None = None,
    ) -> tuple[bool, list[str]]:
        """신규 진입(open) 가능 여부 + 차단 사유.

        정책:
          - block_buy_when_reconnecting 가 True 이고 reconnecting 이면 차단.
          - block_buy_when_stale 가 True 이고 required_data_types 중 하나라도
            stale 이면 차단.
        """
        reasons: list[str] = []
        if self.policy.block_buy_when_reconnecting:
            rc, why = self.is_reconnecting(symbol, exchange)
            if rc:
                reasons.append(f"reconnecting:{exchange}:{symbol} ({why or 'reconnecting'})")
        if self.policy.block_buy_when_stale:
            for dt in required_data_types:
                # _evaluate_stale_only 사용 — reconnecting 정책 토글과 분리.
                st = self._evaluate_stale_only(symbol, exchange, dt, now=now)
                if not st.ok:
                    reasons.append(st.reason)
        return (not reasons), reasons

    def can_generate_signal(
        self,
        symbol: str,
        exchange: str,
        side: str,
        required_data_types: Iterable[str] = ("ticker",),
        now: datetime | None = None,
    ) -> tuple[bool, list[str]]:
        """신호 생성 가능 여부.

        - entry 사이드(BUY/OPEN/ENTER…) : 진입 정책 적용 → can_open_new_position 위임
        - exit 사이드(SELL/EXIT/CLOSE…) : 기본 허용 (위험 축소)
        """
        if is_exit_side(side):
            return True, []
        if is_entry_side(side):
            return self.can_open_new_position(symbol, exchange,
                                              required_data_types=required_data_types,
                                              now=now)
        # 알 수 없는 side 는 보수적으로 entry 와 같이 본다
        return self.can_open_new_position(symbol, exchange,
                                          required_data_types=required_data_types,
                                          now=now)

    def evaluate_for_order(
        self,
        symbol: str,
        exchange: str,
        side: str,
        required_data_types: Iterable[str] = ("ticker",),
        now: datetime | None = None,
    ) -> tuple[bool, list[FreshnessStatus], list[str]]:
        """주문 단위 평가.

        반환:
          block (bool) — True 면 신규 진입을 막아야 함 (SELL/EXIT 는 항상 False)
          statuses     — 평가된 FreshnessStatus 목록 (gateway 의 freshness_statuses 인자 호환)
          reasons      — 차단 사유 문자열 목록 (block=False 면 빈 리스트)
        """
        statuses: list[FreshnessStatus] = []
        # ticker 는 항상 평가 (gateway 호환 — 한 row 라도 채워서 보낸다)
        statuses.append(self.evaluate(symbol, exchange, "ticker", now=now))

        if is_exit_side(side):
            return False, statuses, []   # 청산은 freshness 로 막지 않는다.

        ok, reasons = self.can_generate_signal(symbol, exchange, side,
                                               required_data_types=required_data_types,
                                               now=now)
        return (not ok), statuses, reasons

    # ── Summary ────────────────────────────────────────────────────

    def get_summary(self, now: datetime | None = None) -> dict:
        """공개 status 응답에 그대로 쓸 수 있는 dict.

        키:
          - now (ISO)
          - records: [{symbol, exchange, data_type, timeframe, last_seen_at, age_seconds, stale}]
          - counts:  {fresh, stale, missing, total, reconnecting_scopes}
          - reconnecting: [{symbol, exchange, data_type, reason}]
          - policy: 현재 정책 스냅샷
          - blocks_new_entries: bool — 어느 reconnecting scope 라도 있으면 True
        """
        now_n = _normalize(now) or utc_now()
        records_out: list[dict] = []
        fresh = stale = missing = 0
        with self._lock:
            for key, rec in self._records.items():
                age = compute_lag_seconds(rec.last_seen_at, now_n)
                max_age = self.policy.max_age_for(key.data_type, key.timeframe)
                stale_flag = is_stale(rec.last_seen_at, max_age, now=now_n)
                if rec.last_seen_at is None:
                    missing += 1
                elif stale_flag:
                    stale += 1
                else:
                    fresh += 1
                records_out.append({
                    "symbol":       key.symbol,
                    "exchange":     key.exchange,
                    "data_type":    key.data_type,
                    "timeframe":    key.timeframe,
                    "last_seen_at": rec.last_seen_at.isoformat() if rec.last_seen_at else None,
                    "age_seconds":  age,
                    "max_age_seconds": max_age,
                    "stale":        stale_flag,
                })
            reconnects_out = [
                {
                    "symbol":    sc.symbol,
                    "exchange":  sc.exchange,
                    "data_type": sc.data_type,
                    "reason":    reason,
                }
                for sc, reason in self._reconnects.items()
            ]
        return {
            "now": now_n.isoformat(),
            "records": records_out,
            "counts": {
                "fresh": fresh,
                "stale": stale,
                "missing": missing,
                "total": len(records_out),
                "reconnecting_scopes": len(reconnects_out),
            },
            "reconnecting": reconnects_out,
            "policy": {
                "ticker_max_age_sec":    self.policy.ticker_max_age_sec,
                "orderbook_max_age_sec": self.policy.orderbook_max_age_sec,
                "ohlcv_max_age_sec":     self.policy.ohlcv_max_age_sec,
                "funding_max_age_sec":   self.policy.funding_max_age_sec,
                "fx_max_age_sec":        self.policy.fx_max_age_sec,
                "block_buy_when_stale":        self.policy.block_buy_when_stale,
                "block_buy_when_reconnecting": self.policy.block_buy_when_reconnecting,
            },
            "blocks_new_entries": bool(reconnects_out) or stale > 0,
        }


def policy_from_settings() -> FreshnessPolicy:
    """현재 Settings 로부터 FreshnessPolicy 인스턴스 생성."""
    from app.core.config import get_settings
    s = get_settings()
    return FreshnessPolicy(
        ticker_max_age_sec    = float(getattr(s, "freshness_ticker_max_age_sec",    30.0)),
        orderbook_max_age_sec = float(getattr(s, "freshness_orderbook_max_age_sec", 10.0)),
        ohlcv_max_age_sec     = float(getattr(s, "freshness_ohlcv_max_age_sec",     300.0)),
        funding_max_age_sec   = float(getattr(s, "freshness_funding_max_age_sec",   3600.0)),
        fx_max_age_sec        = float(getattr(s, "freshness_fx_max_age_sec",        300.0)),
        block_buy_when_stale        = bool(getattr(s, "freshness_block_buy_when_stale",        True)),
        block_buy_when_reconnecting = bool(getattr(s, "freshness_block_buy_when_reconnecting", True)),
    )


# 본 모듈 import 시 부작용 없음. tracker 는 deps.py 에서 싱글톤으로 생성한다.
__all__ = [
    # legacy
    "FreshnessStatus", "DataFeedState",
    "check_timestamp_freshness", "check_feed_freshness", "should_block_new_buy",
    # #16 신규
    "FreshnessKey", "FreshnessPolicy", "FreshnessRecord", "ReconnectScope",
    "FreshnessTracker", "policy_from_settings",
    "is_stale", "compute_lag_seconds",
    "ENTRY_SIDES", "EXIT_SIDES", "is_entry_side", "is_exit_side",
    "utc_now",
]
