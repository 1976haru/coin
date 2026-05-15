"""Market 라우터 — /api/freshness, /api/market/tickers, /api/market/collect.

체크리스트 #16 (Freshness), #15 (Market Data Collector).
"""
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.market.collector import MarketDataCollector
from app.market.freshness import DataFeedState, check_feed_freshness
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


@router.get("/api/market/tickers")
def list_cached_tickers(
    exchange: Optional[str] = None,
    collector: MarketDataCollector = Depends(get_collector),
):
    """현재 캐시에 보관된 ticker 들을 반환한다.

    caller 가 직접 collect 하지 않으면 비어 있을 수 있다 — POST /api/market/collect
    또는 백그라운드 수집 루프에서 갱신한다.
    """
    pairs = collector.cached_pairs()
    out = []
    for sym, ex in pairs:
        if exchange is not None and ex != exchange:
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


@router.post("/api/market/collect")
def collect_now(
    list_name: Optional[str] = None,
    db: Session = Depends(get_db),
    collector: MarketDataCollector = Depends(get_collector),
    _=Depends(verify_admin),
):
    """Watchlist 의 enabled 항목들에 대해 1회 동기 수집을 실행한다.

    list_name 으로 특정 그룹만 수집할 수 있다. 미지정 시 전체 enabled 대상.
    """
    svc = WatchlistService(db)
    entries = svc.list_entries(list_name=list_name, enabled_only=True)
    if not entries:
        raise HTTPException(404, "no enabled watchlist entries to collect")
    pairs = [(e["symbol"], e["exchange"]) for e in entries]
    report = collector.collect(pairs)
    return _report_to_dict(report)
