"""Market 라우터 — /api/freshness, /api/market/tickers, /api/market/collect,
/api/market/collector/status.

체크리스트 #15 (Market Data Collector), #16 (Freshness — 최소 연결).
"""
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.market.collector import (
    MarketDataCollector,
    EmptyWatchlistError,
    ALLOWED_INCLUDES,
)
from app.market.freshness import DataFeedState, check_feed_freshness
from app.market.market_persister import persist_report
from app.market.watchlist import WatchlistService

from .deps import settings, get_collector, get_db, verify_admin


router = APIRouter()


@router.get("/api/freshness")
def freshness():
    now  = datetime.now(timezone.utc)
    feed = DataFeedState(connected=True, reconnecting=False, last_message_at=now, source="mock_feed")
    return asdict(check_feed_freshness(feed, settings.freshness_threshold_sec, now))


# ── #15 Market Data Collector ────────────────────────────────────

def _entry_to_dict(e) -> dict:
    return {
        "symbol":    e.symbol,
        "exchange":  e.exchange,
        "ticker":    asdict(e.ticker) if e.ticker is not None else None,
        "freshness": asdict(e.freshness),
        "error":     e.error,
    }


def _report_to_dict(r) -> dict:
    return {
        "started_at":  r.started_at.isoformat(),
        "finished_at": r.finished_at.isoformat(),
        "ok_count":    r.ok_count,
        "stale_count": r.stale_count,
        "error_count": r.error_count,
        "entries":     [_entry_to_dict(e) for e in r.entries],
    }


def _multi_entry_to_dict(e) -> dict:
    return {
        "symbol":    e.symbol,
        "exchange":  e.exchange,
        "ticker":    asdict(e.ticker) if e.ticker is not None else None,
        "ohlcv":     [asdict(c) for c in e.ohlcv],
        "orderbook": asdict(e.orderbook) if e.orderbook is not None else None,
        "funding":   asdict(e.funding) if e.funding is not None else None,
        "freshness": asdict(e.freshness) if e.freshness is not None else None,
        "failures":  [{"type": t, "reason": r} for t, r in e.failures],
    }


def _multi_report_to_dict(r, persisted: dict | None = None) -> dict:
    return {
        "started_at":     r.started_at.isoformat(),
        "finished_at":    r.finished_at.isoformat(),
        "requested_pairs": r.requested_pairs,
        "deduped_pairs":   r.deduped_pairs,
        "truncated_to":    r.truncated_to,
        "symbol_count":    r.symbol_count,
        "success_count":   r.success_count,
        "failure_count":   r.failure_count,
        "includes":        list(r.includes),
        "list_name":       r.list_name,
        "exchange_filter": r.exchange_filter,
        "entries":         [_multi_entry_to_dict(e) for e in r.entries],
        "fx_rates":        [asdict(fx) for fx in r.fx_rates],
        "persisted":       persisted or {},
    }


@router.get("/api/market/tickers")
def list_cached_tickers(
    exchange: Optional[str] = None,
    list_name: Optional[str] = None,
    enabled_only: bool = False,
    db: Session = Depends(get_db),
    collector: MarketDataCollector = Depends(get_collector),
):
    """현재 캐시에 보관된 ticker 들을 반환한다.

    caller 가 직접 collect 하지 않으면 비어 있을 수 있다 — POST /api/market/collect
    또는 백그라운드 수집 루프에서 갱신한다.

    list_name 필터를 주면 해당 Watchlist 의 (symbol, exchange) 만 통과시킨다.
    """
    pairs = collector.cached_pairs()

    allowed: set[tuple[str, str]] | None = None
    if list_name is not None or enabled_only:
        svc = WatchlistService(db)
        wl = svc.list_entries(list_name=list_name, enabled_only=enabled_only)
        allowed = {(e["symbol"], e["exchange"]) for e in wl}

    out = []
    for sym, ex in pairs:
        if exchange is not None and ex != exchange:
            continue
        if allowed is not None and (sym, ex) not in allowed:
            continue
        ticker = collector.get_ticker(sym, ex)
        if ticker is None:
            continue
        out.append({
            "symbol":   sym,
            "exchange": ex,
            "ticker":   asdict(ticker),
        })
    return {"tickers": out, "exchanges": collector.known_exchanges()}


class CollectRequest(BaseModel):
    list_name: Optional[str] = None
    exchange: Optional[str] = None
    include: list[str] = Field(default_factory=lambda: ["ticker"])
    timeframe: str = "1m"
    limit: int = Field(100, ge=1, le=1000)
    orderbook_depth: int = Field(5, ge=1, le=100)
    fx_pairs: list[str] = Field(default_factory=list)
    persist: bool = False


@router.post("/api/market/collect")
def collect_now(
    body: CollectRequest = Body(default_factory=CollectRequest),
    list_name: Optional[str] = None,          # legacy query 파라미터 호환
    db: Session = Depends(get_db),
    collector: MarketDataCollector = Depends(get_collector),
    _=Depends(verify_admin),
):
    """Watchlist 의 enabled 항목들에 대해 1회 동기 수집을 실행한다.

    body 필드:
      - list_name / exchange : Watchlist 필터
      - include              : {"ticker","ohlcv","orderbook","funding","fx"} 부분집합
      - timeframe / limit    : OHLCV 옵션 (1m/5m/15m/1h/4h/1d)
      - orderbook_depth      : 호가창 depth
      - fx_pairs             : FX 페어 목록 (예: ["USDT-KRW"])
      - persist              : true 면 coin_candle / coin_tick / coin_orderbook_snapshot 에 저장

    body 미동봉 시 legacy 동작 (ticker only) 유지.
    """
    # 검증
    unknown = set(s.lower() for s in body.include) - ALLOWED_INCLUDES
    if unknown:
        raise HTTPException(400, f"unknown include keys: {sorted(unknown)}")

    # legacy query 파라미터가 들어오면 우선 사용 (하위호환)
    target_list = list_name or body.list_name

    svc = WatchlistService(db)
    entries = svc.list_entries(
        list_name=target_list,
        exchange=body.exchange,
        enabled_only=True,
    )
    if not entries:
        raise HTTPException(404, "no enabled watchlist entries to collect")
    pairs = [(e["symbol"], e["exchange"]) for e in entries]

    # include 가 ticker 만 이고 persist=False 이면 legacy collect 경로 (이미 검증된 동작 유지)
    includes_set = {s.lower() for s in body.include}
    if includes_set == {"ticker"} and not body.persist:
        report = collector.collect(pairs)
        return _report_to_dict(report)

    # 신규 경로 — collect_all
    # max_symbols 는 매 호출마다 fresh Settings 를 읽어 monkeypatch 가 즉시 반영되게 한다.
    from app.core.config import get_settings as _get_settings
    fresh_settings = _get_settings()
    try:
        rep = collector.collect_all(
            pairs,
            includes=includes_set,
            timeframe=body.timeframe,
            ohlcv_limit=body.limit,
            orderbook_depth=body.orderbook_depth,
            fx_pairs=body.fx_pairs,
            max_symbols=fresh_settings.market_collector_max_symbols,
            list_name=target_list,
            exchange_filter=body.exchange,
        )
    except EmptyWatchlistError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))

    persisted: dict[str, int] = {}
    if body.persist:
        try:
            persisted = persist_report(db, rep)
        except Exception as e:
            persisted = {"error": f"{type(e).__name__}: {e}"}

    return _multi_report_to_dict(rep, persisted=persisted)


@router.get("/api/market/collector/status")
def collector_status(
    collector: MarketDataCollector = Depends(get_collector),
):
    """수집기 상태 — 마지막 수집 시각/대상/성공·실패, source 목록, 모드.

    public — secret 노출 없음.
    """
    return collector.last_status()
