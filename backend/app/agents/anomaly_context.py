"""Anomaly context 빌더 — 체크리스트 #40.

NoticeRegistry / ThemeRegistry+NewsRegistry / QualityReport / Freshness 결과를
AnomalyAgent.decide(input, ctx) 의 ctx 형식으로 합산한다.

설계:
  - 함수형 헬퍼. 각 source 가 None 이면 해당 키 미생성.
  - app.agents → app.market 의존 (legitimate per CLAUDE.md §3.1).
"""
from __future__ import annotations
from typing import Any


def anomaly_context_for(
    *,
    symbol: str | None = None,
    exchange: str | None = None,
    notices: Any | None = None,           # NoticeRegistry
    themes: Any | None = None,            # ThemeRegistry
    news: Any | None = None,              # NewsRegistry
    quality_report: Any | None = None,    # QualityReport
    freshness_stale: bool = False,
    kimp_pct: float | None = None,
    kimp_abnormal_min: float = -10.0,
    kimp_abnormal_max: float = +10.0,
) -> dict:
    """심볼/거래소 상황을 종합한 AnomalyAgent ctx dict.

    각 source 는 옵션 — 가능한 만큼만 채운다.
    """
    ctx: dict = {}

    # NoticeRegistry → notice_status
    if notices is not None and symbol is not None and exchange is not None:
        from app.market.notices import assess_symbol_notices
        ctx["notice_status"] = assess_symbol_notices(notices, symbol, exchange)

    # Themes/News → market context (regime + news_severity)
    if themes is not None and news is not None and symbol is not None and exchange is not None:
        from app.market.themes import assess_market_context
        mc = assess_market_context(symbol, exchange, themes=themes, news=news)
        # AnomalyAgent 는 news_severity 만 사용 (regime 은 SignalQualityAgent 가 사용)
        ctx["news_severity"] = mc.news_severity

    # QualityReport
    if quality_report is not None:
        ctx["quality_report"] = quality_report

    # Freshness
    if freshness_stale:
        ctx["freshness_stale"] = True

    # 김프율 이상치 hint
    if kimp_pct is not None:
        from app.market.kimp import is_anomaly
        if is_anomaly(kimp_pct,
                       abnormal_min=kimp_abnormal_min,
                       abnormal_max=kimp_abnormal_max):
            ctx["kimp_anomaly_hint"] = True

    return ctx
