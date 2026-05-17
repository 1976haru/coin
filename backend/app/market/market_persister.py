"""MarketPersister — collect_all 결과를 #13 코인 스키마로 영속화.

체크리스트 #15 보조 모듈. collector.py 가 `app.db` 를 직접 import 하지 않게
분리한다 (회귀 테스트 `test_collector_does_not_import_brokers_or_execution`
와 같은 정책 — collector 는 IO/DB 로부터 격리).

쓰기 대상 (13번 #crypto schema):
  - CoinCandle              : OHLCV. (exchange,symbol,interval,ts) UNIQUE — 중복은 skip.
  - CoinTick                : ticker.price 를 1 행씩 append.
  - CoinOrderbookSnapshot   : orderbook 1 행씩 append.

funding / FX 는 본 단계에서 별도 테이블이 없어 영속화하지 않는다.
필요 시 별도 PR 에서 표/캐시를 추가한다 (16번 이후 작업 범위).
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import CoinCandle, CoinTick, CoinOrderbookSnapshot


def persist_report(session: Session, report) -> dict[str, int]:
    """`MultiCollectorReport` 를 DB 에 기록한다.

    부분 실패 격리:
      - 한 entry 의 한 데이터 타입 쓰기가 실패하면 그 항목만 skip 한다.
      - 다른 entry / 다른 데이터 타입 쓰기는 계속 진행한다.

    반환: 데이터 타입별 실제 insert 된 행 수.
    """
    candles_in = 0
    candles_skipped = 0
    ticks_in = 0
    obs_in = 0

    for e in report.entries:
        # OHLCV — 중복 (exchange,symbol,interval,ts) 는 skip.
        for c in e.ohlcv:
            row = CoinCandle(
                exchange=e.exchange,
                symbol=c.symbol,
                interval=c.timeframe,
                ts=c.ts,
                open=c.open, high=c.high, low=c.low, close=c.close,
                volume=c.volume,
                source="mock",
                meta={},
            )
            try:
                session.add(row)
                session.commit()
                candles_in += 1
            except IntegrityError:
                session.rollback()
                candles_skipped += 1
            except Exception:
                session.rollback()

        # Ticker — 단순 append (시계열 tick 로 활용).
        if e.ticker is not None:
            try:
                t = CoinTick(
                    exchange=e.exchange,
                    symbol=e.ticker.symbol,
                    ts=e.ticker.ts,
                    price=e.ticker.price,
                    qty=0,
                    side="",
                    source="ticker",
                    meta={
                        "bid": float(e.ticker.bid),
                        "ask": float(e.ticker.ask),
                        "volume_24h": float(e.ticker.volume_24h),
                    },
                )
                session.add(t)
                session.commit()
                ticks_in += 1
            except Exception:
                session.rollback()

        # Orderbook snapshot.
        if e.orderbook is not None:
            try:
                ob = CoinOrderbookSnapshot(
                    exchange=e.exchange,
                    symbol=e.orderbook.symbol,
                    ts=e.orderbook.ts,
                    depth=max(len(e.orderbook.bids), len(e.orderbook.asks)),
                    bids=[[float(p), float(q)] for p, q in e.orderbook.bids],
                    asks=[[float(p), float(q)] for p, q in e.orderbook.asks],
                    source="mock",
                    meta={},
                )
                session.add(ob)
                session.commit()
                obs_in += 1
            except Exception:
                session.rollback()

    return {
        "candles_inserted":  candles_in,
        "candles_skipped":   candles_skipped,
        "ticks_inserted":    ticks_in,
        "orderbooks_inserted": obs_in,
    }
