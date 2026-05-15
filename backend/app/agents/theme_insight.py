"""ThemeInsightAgent — 체크리스트 #43 Theme/Market Insight Agent.

심볼별 테마/뉴스/공지/김프 컨텍스트를 한 곳에 모아 사람이 읽기 좋은 브리핑을
생성한다. 거래 결정을 내리지 않고 정보만 제공 (행동 권고 없음 — explain only).

집계 source:
  - ThemeRegistry (#19) — 심볼이 속한 테마들
  - NewsRegistry  (#19) — 심볼 관련 활성 뉴스
  - NoticeRegistry (#18) — 입출금/상폐/유의 상태
  - kimp_pct (#34) — 입력으로 받음 (선택)

결정론 — LLM 사용 안 함. ENABLE_AI_AGENTS=true 시 LLM 강화 옵션은 후속 PR.
CLAUDE.md §2.3: is_order_intent 기본 False.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

from .base import AgentCapability


@dataclass(frozen=True)
class SymbolBriefing:
    """심볼 한 건의 통합 브리핑."""

    symbol: str
    exchange: str
    themes: tuple[str, ...]
    news_severity: str                    # "info"/"warn"/"block"
    news_count: int
    news_headlines: tuple[str, ...]       # 활성 뉴스 헤드라인 최대 5개
    tradable: bool
    deposit_withdrawal_ok: bool
    has_warning: bool
    notice_reasons: tuple[str, ...]       # 활성 공지 사유들
    kimp_pct: float | None
    kimp_anomaly: bool                    # ±10% 초과 여부
    overall_outlook: Literal["positive", "neutral", "caution", "block"]

    def to_dict(self) -> dict:
        return asdict(self)


class ThemeInsightAgent:
    """심볼 컨텍스트 통합 브리핑 Agent.

    호출 방식:
      - briefing(symbol, exchange, *, themes, news, notices, kimp_pct=None)
        → SymbolBriefing
      - render_text(briefing, format)  → markdown/plain
      - decide(input, ctx)             → AgentDecision (explain_text 에 markdown)
    """

    capability = AgentCapability(
        name="theme_insight",
        role="explain",
        description=(
            "심볼별 테마/뉴스/공지/김프 통합 브리핑 — 운영자/UI 컨텍스트. "
            "거래 결정 없음, 정보만 제공."
        ),
        has_veto_power=False,
        is_deterministic=True,
        requires_llm=False,
        inputs=("symbol", "exchange", "themes_registry",
                "news_registry", "notices_registry", "kimp_pct"),
    )

    # ── 핵심 — 브리핑 생성 ───────────────────────────────────────

    def briefing(
        self,
        *,
        symbol: str,
        exchange: str,
        themes: Any | None = None,
        news: Any | None = None,
        notices: Any | None = None,
        kimp_pct: float | None = None,
    ) -> SymbolBriefing:
        themes_list = self._collect_themes(themes, symbol, exchange)
        news_severity, news_count, news_headlines = self._collect_news(news, symbol)
        tradable, dwd, has_warning, notice_reasons = self._collect_notices(
            notices, symbol, exchange,
        )
        kimp_anomaly = self._is_kimp_anomalous(kimp_pct)
        outlook = self._assess_outlook(
            news_severity=news_severity,
            tradable=tradable,
            dwd=dwd,
            has_warning=has_warning,
            kimp_anomaly=kimp_anomaly,
        )
        return SymbolBriefing(
            symbol=symbol,
            exchange=exchange,
            themes=tuple(themes_list),
            news_severity=news_severity,
            news_count=news_count,
            news_headlines=tuple(news_headlines),
            tradable=tradable,
            deposit_withdrawal_ok=dwd,
            has_warning=has_warning,
            notice_reasons=tuple(notice_reasons),
            kimp_pct=kimp_pct,
            kimp_anomaly=kimp_anomaly,
            overall_outlook=outlook,
        )

    def render_text(
        self,
        briefing: SymbolBriefing,
        *,
        format: str = "markdown",
    ) -> str:
        outlook_emoji = {
            "positive": "🟢", "neutral": "⚪", "caution": "🟡", "block": "🔴",
        }.get(briefing.overall_outlook, "⚪")

        if format == "markdown":
            lines = [
                f"## {outlook_emoji} {briefing.exchange.upper()} : {briefing.symbol}",
                f"- **종합 전망**: `{briefing.overall_outlook}`",
            ]
            if briefing.themes:
                lines.append(f"- **테마**: {', '.join(briefing.themes)}")
            else:
                lines.append("- **테마**: (없음)")
            lines.append(
                f"- **거래 가능**: {'✓' if briefing.tradable else '✗'} "
                f"/ 입출금: {'✓' if briefing.deposit_withdrawal_ok else '✗'}"
                f"{' / ⚠ 유의' if briefing.has_warning else ''}"
            )
            if briefing.notice_reasons:
                lines.append("- **공지 사유**:")
                lines.extend(f"  - {r}" for r in briefing.notice_reasons[:5])
            lines.append(
                f"- **뉴스**: {briefing.news_count}건 (severity={briefing.news_severity})"
            )
            if briefing.news_headlines:
                for h in briefing.news_headlines[:5]:
                    lines.append(f"  - {h}")
            if briefing.kimp_pct is not None:
                tag = " ⚠ 이상치" if briefing.kimp_anomaly else ""
                lines.append(f"- **김프**: {briefing.kimp_pct:+.2f}%{tag}")
            return "\n".join(lines)

        # plain
        lines = [
            f"=== {briefing.exchange.upper()} : {briefing.symbol} ===",
            f"  종합 전망: {briefing.overall_outlook}",
            f"  테마: {', '.join(briefing.themes) if briefing.themes else '(없음)'}",
            f"  거래 가능: {briefing.tradable} / 입출금: {briefing.deposit_withdrawal_ok}",
        ]
        if briefing.notice_reasons:
            lines.append("  공지 사유:")
            for r in briefing.notice_reasons[:5]:
                lines.append(f"    - {r}")
        lines.append(
            f"  뉴스: {briefing.news_count}건 (severity={briefing.news_severity})"
        )
        for h in briefing.news_headlines[:5]:
            lines.append(f"    - {h}")
        if briefing.kimp_pct is not None:
            tag = " (이상치)" if briefing.kimp_anomaly else ""
            lines.append(f"  김프: {briefing.kimp_pct:+.2f}%{tag}")
        return "\n".join(lines)

    # ── AgentBase contract ────────────────────────────────────────

    def decide(self, input_signal: dict, context: dict | None = None) -> Any:
        from .orchestrator import AgentDecision
        ctx = context or {}
        symbol = input_signal.get("symbol") or ctx.get("symbol", "")
        exchange = ctx.get("exchange", "upbit")
        b = self.briefing(
            symbol=symbol, exchange=exchange,
            themes=ctx.get("themes_registry"),
            news=ctx.get("news_registry"),
            notices=ctx.get("notices_registry"),
            kimp_pct=ctx.get("kimp_pct"),
        )
        # 거래 결정은 내리지 않고 HOLD + briefing 을 explain_text 로
        return AgentDecision(
            "HOLD", 0.0,
            f"ThemeInsightAgent: 브리핑 ({b.overall_outlook})",
            explain_text=self.render_text(b, format="markdown"),
        )

    # ── 내부 — 각 source 수집 ───────────────────────────────────

    @staticmethod
    def _collect_themes(themes_registry: Any, symbol: str, exchange: str) -> list[str]:
        if themes_registry is None:
            return []
        if hasattr(themes_registry, "themes_for"):
            return list(themes_registry.themes_for(symbol, exchange))
        return []

    @staticmethod
    def _collect_news(
        news_registry: Any, symbol: str,
    ) -> tuple[str, int, list[str]]:
        if news_registry is None:
            return "info", 0, []
        if not hasattr(news_registry, "active_for"):
            return "info", 0, []
        events = list(news_registry.active_for(symbol))
        rank = {"info": 0, "warn": 1, "block": 2}
        max_sev = "info"
        max_r = 0
        headlines: list[str] = []
        for e in events:
            sev = getattr(e, "severity", "info")
            r = rank.get(sev, 0)
            if r > max_r:
                max_r = r
                max_sev = sev
            headline = getattr(e, "headline", "")
            if headline:
                headlines.append(headline)
        return max_sev, len(events), headlines

    @staticmethod
    def _collect_notices(
        notices_registry: Any, symbol: str, exchange: str,
    ) -> tuple[bool, bool, bool, list[str]]:
        if notices_registry is None:
            return True, True, False, []
        try:
            from app.market.notices import assess_symbol_notices
            status = assess_symbol_notices(notices_registry, symbol, exchange)
            return (status.tradable, status.deposit_withdrawal_ok,
                    status.has_warning, list(status.reasons()))
        except Exception:
            return True, True, False, []

    @staticmethod
    def _is_kimp_anomalous(kimp_pct: float | None) -> bool:
        if kimp_pct is None:
            return False
        from app.market.kimp import is_anomaly
        return is_anomaly(kimp_pct)

    @staticmethod
    def _assess_outlook(
        *,
        news_severity: str,
        tradable: bool,
        dwd: bool,
        has_warning: bool,
        kimp_anomaly: bool,
    ) -> Literal["positive", "neutral", "caution", "block"]:
        # block 조건
        if not tradable or not dwd or news_severity == "block" or kimp_anomaly:
            return "block"
        # caution
        if has_warning or news_severity == "warn":
            return "caution"
        # 기본 neutral (현재는 positive 기준 없음 — 향후 themes/regime 통합 시 추가)
        return "neutral"
