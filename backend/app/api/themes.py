"""Themes/News 라우터 — 체크리스트 #19.

- GET /api/market/context/{exchange}/{symbol}  (공개) — 통합 컨텍스트
- GET /api/themes                              (공개) — 테마 목록
- POST /api/themes/tag                         (admin) — 심볼 태깅
- DELETE /api/themes/tag/{theme}/{symbol}      (admin) — 태그 제거
- GET /api/news                                (공개) — 활성 뉴스 목록
- POST /api/news                               (admin) — 뉴스 추가
- DELETE /api/news/{event_id}                  (admin) — 뉴스 제거

체크리스트 #19 확장 (Trend/News/Theme Signals — DB-backed):
- GET  /api/theme-signals                      (공개) — 정규화 신호 조회
- POST /api/theme-signals/collect              (admin) — 1회 수집 (mock provider)
- GET  /api/theme-signals/context              (공개) — Agent context (read-only)
- GET  /api/theme-signals/sources              (공개) — source/risk_flag 카탈로그
- POST /api/theme-signals/filter               (공개) — Watchlist 후보 → review_required

모든 응답은 used_for_order=false, direct_order_allowed=false 를 포함 — 본 데이터는
직접 매매 신호가 아니다 (CLAUDE.md §2.3).
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.market.themes import (
    ThemeRegistry, NewsRegistry, NewsKind, NewsSeverity,
    assess_market_context,
)
from app.market.theme_signals import (
    ThemeSignalCollector, SOURCES, ALLOWED_RISK_FLAGS,
    list_theme_signals as db_list_theme_signals, signal_to_dict,
)
from app.market.theme_context import (
    ThemeContextBuilder, ThemeFilter,
)

from .deps import (
    get_themes, get_news, verify_admin, get_db,
    get_theme_signal_collector,
)


router = APIRouter()


# ── 통합 컨텍스트 ─────────────────────────────────────────────────

@router.get("/api/market/context/{exchange}/{symbol}")
def market_context(
    exchange: str,
    symbol: str,
    themes: ThemeRegistry = Depends(get_themes),
    news:   NewsRegistry  = Depends(get_news),
):
    ctx = assess_market_context(
        symbol, exchange, themes=themes, news=news, closes=None,
    )
    return ctx.to_dict()


# ── Themes ────────────────────────────────────────────────────────

class ThemeTagRequest(BaseModel):
    theme:    str = Field(..., max_length=32)
    symbol:   str = Field(..., max_length=32)
    exchange: str = Field("*", max_length=16)


@router.get("/api/themes")
def list_themes(themes: ThemeRegistry = Depends(get_themes)):
    return {
        "themes": [
            {"name": t, "symbols": [{"symbol": s, "exchange": ex}
                                     for s, ex in themes.symbols_in(t)]}
            for t in themes.all_themes()
        ],
    }


@router.post("/api/themes/tag", status_code=201)
def tag_theme(
    body: ThemeTagRequest,
    themes: ThemeRegistry = Depends(get_themes),
    _=Depends(verify_admin),
):
    try:
        themes.tag(body.theme, body.symbol, body.exchange)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "theme": body.theme,
        "symbol": body.symbol,
        "exchange": body.exchange,
        "themes_for_symbol": themes.themes_for(body.symbol, body.exchange),
    }


@router.delete("/api/themes/tag/{theme}/{symbol}", status_code=204)
def untag_theme(
    theme: str,
    symbol: str,
    exchange: str = "*",
    themes: ThemeRegistry = Depends(get_themes),
    _=Depends(verify_admin),
):
    if not themes.untag(theme, symbol, exchange):
        raise HTTPException(404, f"({theme}, {symbol}, {exchange}) 태그가 없음")


# ── News ──────────────────────────────────────────────────────────

class NewsAddRequest(BaseModel):
    kind:            str
    headline:        str = Field(..., max_length=256)
    severity:        str = "info"
    occurred_at:     Optional[datetime] = None
    expires_at:      Optional[datetime] = None
    related_symbols: list[str] = Field(default_factory=list)
    source_url:      str = ""


@router.get("/api/news")
def list_news(
    active_only: bool = True,
    symbol: Optional[str] = None,
    news: NewsRegistry = Depends(get_news),
):
    items = news.active() if active_only else news.all()
    if symbol is not None:
        items = [
            e for e in items
            if not e.related_symbols or symbol in e.related_symbols
        ]
    return {
        "events": [e.to_dict() for e in items],
        "count": len(items),
    }


@router.post("/api/news", status_code=201)
def add_news(
    body: NewsAddRequest,
    news: NewsRegistry = Depends(get_news),
    _=Depends(verify_admin),
):
    try:
        ev = news.add(
            kind=body.kind,                    # type: ignore[arg-type]
            headline=body.headline,
            severity=body.severity,            # type: ignore[arg-type]
            occurred_at=body.occurred_at,
            expires_at=body.expires_at,
            related_symbols=body.related_symbols,
            source_url=body.source_url,
        )
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e))
    return ev.to_dict()


@router.delete("/api/news/{event_id}", status_code=204)
def remove_news(
    event_id: int,
    news: NewsRegistry = Depends(get_news),
    _=Depends(verify_admin),
):
    if not news.remove(event_id):
        raise HTTPException(404, f"news id={event_id} not found")


# ── #19 Theme Signals (DB-backed) ─────────────────────────────────


class ThemeCollectRequest(BaseModel):
    provider: str = Field(default="mock", max_length=64)
    since_hours: Optional[int] = Field(default=None, ge=0, le=24 * 365)


class ThemeFilterRequest(BaseModel):
    """후보 (symbol, exchange) 리스트를 받아 review flag 부여."""

    candidates: list[dict] = Field(default_factory=list)
    lookback_hours: int = Field(default=72, ge=1, le=24 * 30)


@router.get("/api/theme-signals")
def list_theme_signals_endpoint(
    source: Optional[str] = None,
    provider: Optional[str] = None,
    theme: Optional[str] = None,
    symbol: Optional[str] = None,
    since_hours: Optional[int] = None,
    limit: int = 200,
    session: Session = Depends(get_db),
):
    rows = db_list_theme_signals(
        session,
        source=source, provider=provider, theme=theme, symbol=symbol,
        since_hours=since_hours, limit=max(1, min(int(limit), 1000)),
    )
    by_source: dict[str, int] = {}
    by_theme: dict[str, int] = {}
    by_risk: dict[str, int] = {}
    for r in rows:
        by_source[r.source] = by_source.get(r.source, 0) + 1
        if r.theme:
            by_theme[r.theme] = by_theme.get(r.theme, 0) + 1
        for rf in (r.risk_flags or []):
            if rf in ALLOWED_RISK_FLAGS:
                by_risk[rf] = by_risk.get(rf, 0) + 1
    return {
        "signals": [signal_to_dict(r) for r in rows],
        "summary": {
            "by_source":    by_source,
            "by_theme":     by_theme,
            "by_risk_flag": by_risk,
            "updated_at":   datetime.now(timezone.utc).isoformat(),
        },
        "used_for_order":       False,
        "direct_order_allowed": False,
    }


@router.post("/api/theme-signals/collect")
def collect_theme_signals(
    body: ThemeCollectRequest,
    collector: ThemeSignalCollector = Depends(get_theme_signal_collector),
    session: Session = Depends(get_db),
    _=Depends(verify_admin),
):
    if body.provider not in collector.providers:
        raise HTTPException(400, f"unknown provider: {body.provider}")
    since = None
    if body.since_hours:
        since = datetime.now(timezone.utc) - timedelta(hours=body.since_hours)
    result = collector.collect_once(
        session, provider_name=body.provider, since=since,
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {
        "fetched":      result.fetched,
        "inserted":     result.inserted,
        "updated":      result.updated,
        "skipped":      result.skipped,
        "by_source":    result.by_source,
        "by_theme":     result.by_theme,
        "by_risk_flag": result.by_risk_flag,
        "used_for_order":       False,
        "direct_order_allowed": False,
    }


@router.get("/api/theme-signals/context")
def get_theme_context(
    symbols: Optional[str] = None,
    themes_csv: Optional[str] = None,
    sources_csv: Optional[str] = None,
    lookback_hours: int = 72,
    session: Session = Depends(get_db),
):
    sym_list = None
    if symbols:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    theme_list = None
    if themes_csv:
        theme_list = [t.strip() for t in themes_csv.split(",") if t.strip()]
    source_list = None
    if sources_csv:
        source_list = [s.strip().lower() for s in sources_csv.split(",") if s.strip()]
    builder = ThemeContextBuilder(session)
    ctx = builder.build_theme_context(
        symbols=sym_list,
        themes=theme_list,
        sources=source_list,
        lookback_hours=max(1, int(lookback_hours)),
    )
    return ctx.to_dict()


@router.get("/api/theme-signals/sources")
def get_theme_signal_catalog():
    return {
        "sources":     list(SOURCES),
        "risk_flags":  list(ALLOWED_RISK_FLAGS),
        "used_for_order":       False,
        "direct_order_allowed": False,
    }


@router.post("/api/theme-signals/filter")
def filter_candidates(
    body: ThemeFilterRequest,
    session: Session = Depends(get_db),
):
    """Watchlist 후보 리스트에 theme context 를 붙여 review_required 표시.

    candidates: [{"symbol": "BTC", "exchange": "upbit"}, ...]
    응답은 candidate_filter_review_required / candidate_filter_ok 만 사용.
    BUY/SELL/ENTER/EXIT 같은 action 은 절대 포함되지 않는다 (정적 가드 + 회귀 테스트).
    """
    pairs: list[tuple[str, str]] = []
    for c in body.candidates or []:
        if not isinstance(c, dict):
            continue
        sym = (c.get("symbol") or "").strip()
        ex = (c.get("exchange") or "").strip()
        if not sym:
            continue
        pairs.append((sym, ex))
    if not pairs:
        raise HTTPException(400, "candidates must be non-empty list of {symbol, exchange}")
    out = ThemeFilter(session).annotate_candidates(
        pairs, lookback_hours=body.lookback_hours,
    )
    return {
        "candidates": [e.to_dict() for e in out],
        "used_for_order":       False,
        "direct_order_allowed": False,
    }
