"""Notices 라우터 — 체크리스트 #18.

GET 은 공개, 변경(POST/DELETE)은 admin 토큰 필요.

엔드포인트:
  - GET  /api/notices                  — legacy 메모리 레지스트리(KimpStrategy 호환) +
                                          영속 ExchangeNotice 통합 응답.
  - GET  /api/notices/symbol/{ex}/{s}  — legacy 심볼 상태 (메모리 레지스트리 기반).
  - POST /api/notices                  — legacy 메모리 레지스트리 추가 (admin).
  - DELETE /api/notices/{id}           — legacy 메모리 레지스트리 삭제 (admin).
  - POST /api/notices/collect          — NoticeCollector 1회 실행 (admin, mock source).
  - GET  /api/notices/context          — NoticeContextBuilder 결과 (Agent용 read-only).

직접 주문 트리거 아님 (CLAUDE.md §2.3) — 모든 응답에 ``direct_order_allowed=false`` 표시.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.market.notices import (
    NoticeRegistry, NoticeKind, assess_symbol_notices,
)
from app.market.notice_collector import (
    NoticeCollector, MockNoticeSource, NOTICE_TYPES, SEVERITIES,
    list_notices as db_list_notices, notice_to_dict,
)
from app.market.notice_context import NoticeContextBuilder

from .deps import get_notices, verify_admin, get_db, get_notice_collector


router = APIRouter()


class NoticeAddRequest(BaseModel):
    exchange:   str = Field(..., max_length=16)
    symbol:     str = Field(..., max_length=32)
    kind:       str
    message:    str = ""
    starts_at:  Optional[datetime] = None
    ends_at:    Optional[datetime] = None
    source_url: str = ""


@router.get("/api/notices")
def list_notices(
    active_only: bool = True,
    exchange: Optional[str] = None,
    symbol: Optional[str] = None,
    notice_type: Optional[str] = None,
    severity: Optional[str] = None,
    since_hours: Optional[int] = None,
    registry: NoticeRegistry = Depends(get_notices),
    session: Session = Depends(get_db),
):
    """legacy 메모리 레지스트리 + 영속 ExchangeNotice 통합 응답.

    응답 필드:
      - notices: legacy 메모리 항목 (KimpStrategy 호환 형식)
      - exchange_notices: 영속 ExchangeNotice (collector 결과)
      - summary: by_type / by_severity / high_risk_symbols
      - direct_order_allowed: False (영구)
    """
    items = registry.active() if active_only else registry.all()
    if exchange is not None:
        items = [n for n in items if n.exchange == exchange]
    if symbol is not None:
        items = [n for n in items if n.symbol == symbol]

    db_rows = db_list_notices(
        session,
        exchange=(exchange.lower() if exchange else None),
        symbol=symbol,
        notice_type=notice_type,
        severity=severity,
        since_hours=since_hours,
        limit=200,
    )
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    high_risk: set[str] = set()
    for r in db_rows:
        by_type[r.notice_type] = by_type.get(r.notice_type, 0) + 1
        by_severity[r.severity] = by_severity.get(r.severity, 0) + 1
        if r.severity in ("HIGH", "CRITICAL"):
            for s in (r.symbols or []):
                high_risk.add(s)

    return {
        "notices": [n.to_dict() for n in items],
        "count": len(items),
        "exchange_notices": [notice_to_dict(r) for r in db_rows],
        "summary": {
            "by_type":           by_type,
            "by_severity":       by_severity,
            "high_risk_symbols": sorted(high_risk),
            "updated_at":        datetime.now(timezone.utc).isoformat(),
        },
        "direct_order_allowed": False,
    }


@router.get("/api/notices/symbol/{exchange}/{symbol}")
def get_symbol_status(
    exchange: str,
    symbol: str,
    registry: NoticeRegistry = Depends(get_notices),
):
    """특정 (exchange, symbol)의 거래 가능성 요약 — KimpStrategy 입력 형식."""
    s = assess_symbol_notices(registry, symbol, exchange)
    return {
        "exchange": s.exchange,
        "symbol": s.symbol,
        "deposit_ok": s.deposit_ok,
        "withdrawal_ok": s.withdrawal_ok,
        "deposit_withdrawal_ok": s.deposit_withdrawal_ok,
        "tradable": s.tradable,
        "has_warning": s.has_warning,
        "reasons": s.reasons(),
    }


@router.post("/api/notices", status_code=201)
def add_notice(
    body: NoticeAddRequest,
    registry: NoticeRegistry = Depends(get_notices),
    _=Depends(verify_admin),
):
    try:
        notice = registry.add(
            exchange=body.exchange,
            symbol=body.symbol,
            kind=body.kind,  # type: ignore[arg-type]
            message=body.message,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            source_url=body.source_url,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return notice.to_dict()


@router.delete("/api/notices/{notice_id}", status_code=204)
def remove_notice(
    notice_id: int,
    registry: NoticeRegistry = Depends(get_notices),
    _=Depends(verify_admin),
):
    if not registry.remove(notice_id):
        raise HTTPException(404, f"notice id={notice_id} not found")


# ── #18 영속 collector / agent context ────────────────────────────


class NoticeCollectRequest(BaseModel):
    exchange: str = Field(default="mock", max_length=32)
    source:   str = Field(default="mock", max_length=64)
    since_hours: Optional[int] = Field(default=None, ge=0, le=24 * 365)


@router.post("/api/notices/collect")
def collect_notices(
    body: NoticeCollectRequest,
    collector: NoticeCollector = Depends(get_notice_collector),
    session: Session = Depends(get_db),
    _=Depends(verify_admin),
):
    """공지 수집 1회 실행 — admin 토큰 필요. 기본 source 는 mock.

    실제 거래소 사이트 호출 금지 — 외부 source 가 추가되더라도 본 엔드포인트는
    설정된 source 만 사용한다. 응답은 read-only.
    """
    if body.source not in collector.sources:
        raise HTTPException(400, f"unknown source: {body.source}")
    since = None
    if body.since_hours:
        from datetime import timedelta, timezone as _tz
        since = datetime.now(_tz.utc) - timedelta(hours=body.since_hours)
    result = collector.collect_once(
        session,
        exchange=body.exchange,
        source_name=body.source,
        since=since,
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {
        "fetched":  result.fetched,
        "inserted": result.inserted,
        "updated":  result.updated,
        "skipped":  result.skipped,
        "by_type":  result.by_type,
        "by_severity": result.by_severity,
        "direct_order_allowed": False,
    }


@router.get("/api/notices/context")
def get_notice_context(
    symbols: Optional[str] = None,
    lookback_hours: int = 72,
    exchange: Optional[str] = None,
    session: Session = Depends(get_db),
):
    """Agent / 후보 필터용 read-only notice context.

    - symbols=BTC,ETH,XRP (콤마 구분)
    - lookback_hours=72 (기본)
    - exchange 필터 옵션

    응답에 direct_order_allowed=false 가 항상 포함됨.
    """
    sym_list: list[str] | None = None
    if symbols:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    builder = NoticeContextBuilder(session)
    ctx = builder.build_notice_context(
        symbols=sym_list,
        lookback_hours=max(1, int(lookback_hours)),
        exchange=(exchange.lower() if exchange else None),
    )
    return ctx.to_dict()


@router.get("/api/notices/types")
def get_notice_types():
    """notice_type / severity 카탈로그 — 프론트 셀렉트박스 등에서 사용."""
    return {
        "notice_types": list(NOTICE_TYPES),
        "severities":   list(SEVERITIES),
        "direct_order_allowed": False,
    }
