"""Themes/News 라우터 — 체크리스트 #19.

- GET /api/market/context/{exchange}/{symbol}  (공개) — 통합 컨텍스트
- GET /api/themes                              (공개) — 테마 목록
- POST /api/themes/tag                         (admin) — 심볼 태깅
- DELETE /api/themes/tag/{theme}/{symbol}      (admin) — 태그 제거
- GET /api/news                                (공개) — 활성 뉴스 목록
- POST /api/news                               (admin) — 뉴스 추가
- DELETE /api/news/{event_id}                  (admin) — 뉴스 제거
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.market.themes import (
    ThemeRegistry, NewsRegistry, NewsKind, NewsSeverity,
    assess_market_context,
)

from .deps import get_themes, get_news, verify_admin


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
