"""Theme/News/Trend Context Builder + Candidate Filter — 체크리스트 #19.

NewsTrendAgent / 후보 필터가 사용할 *read-only* theme context 를 생성한다.

중요 (CLAUDE.md §2.3):
  - 본 context 는 **후보 필터와 리스크 설명** 용도. 직접 매매 신호가 아니다.
  - 반환 dict 어디에도 BUY/SELL/ENTER/EXIT/LONG/SHORT 같은 action 토큰이 없다.
  - 허용 표현: candidate_filter / review_required / context_only / risk_note.
  - direct_order_allowed / used_for_order 가 응답에 항상 False 로 포함된다.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import ThemeSignal
from app.market.theme_signals import (
    ALLOWED_RISK_FLAGS, FORBIDDEN_ACTION_TOKENS,
    list_theme_signals, signal_to_dict,
)


# review_required 를 유발하는 risk_flag 화이트리스트.
_REVIEW_TRIGGERING_FLAGS: frozenset[str] = frozenset({
    "regulatory_attention",
    "exchange_risk_attention",
    "delisting_related_theme",
    "suspicious_hype_theme",
    "review_required",
})


# ── dataclass ────────────────────────────────────────────────────


@dataclass(frozen=True)
class SymbolThemeSummary:
    """심볼별 theme/news/trend 요약 (action 필드 없음)."""

    symbol: str
    themes: tuple[str, ...]
    risk_flags: tuple[str, ...]
    signal_count: int
    high_attention_count: int
    sentiment_avg: float | None
    recommendation: str  # candidate_filter_review_required / candidate_filter_ok
    used_for_order: bool = False         # 영구 False
    direct_order_allowed: bool = False   # 영구 False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["themes"] = list(self.themes)
        d["risk_flags"] = list(self.risk_flags)
        return d


@dataclass(frozen=True)
class CandidateFilterEntry:
    """ThemeFilter 결과 — Watchlist 후보 한 건의 컨텍스트."""

    symbol: str
    exchange: str
    themes: tuple[str, ...]
    risk_flags: tuple[str, ...]
    recommendation: str  # candidate_filter_review_required / candidate_filter_ok
    risk_notes: tuple[str, ...]
    used_for_order: bool = False
    direct_order_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["themes"] = list(self.themes)
        d["risk_flags"] = list(self.risk_flags)
        d["risk_notes"] = list(self.risk_notes)
        return d


@dataclass(frozen=True)
class ThemeContext:
    """NewsTrendAgent context — read-only."""

    generated_at: str
    lookback_hours: int
    total_signals: int
    by_source: dict[str, int]
    by_theme: dict[str, int]
    by_risk_flag: dict[str, int]
    high_attention_themes: tuple[str, ...]
    review_required_symbols: tuple[str, ...]
    symbol_summaries: tuple[SymbolThemeSummary, ...]
    recent_titles: tuple[str, ...]
    human_summary: str
    candidate_filter_flags: tuple[str, ...]
    risk_notes: tuple[str, ...]
    used_for_order: bool = False
    direct_order_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":            self.generated_at,
            "lookback_hours":          self.lookback_hours,
            "total_signals":           self.total_signals,
            "by_source":               dict(self.by_source),
            "by_theme":                dict(self.by_theme),
            "by_risk_flag":            dict(self.by_risk_flag),
            "high_attention_themes":   list(self.high_attention_themes),
            "review_required_symbols": list(self.review_required_symbols),
            "symbol_summaries":        [s.to_dict() for s in self.symbol_summaries],
            "recent_titles":           list(self.recent_titles),
            "human_summary":           self.human_summary,
            "candidate_filter_flags":  list(self.candidate_filter_flags),
            "risk_notes":              list(self.risk_notes),
            "used_for_order":          False,
            "direct_order_allowed":    False,
        }


# ── ThemeFilter — 후보 필터 ─────────────────────────────────────


class ThemeFilter:
    """Watchlist 후보에 theme context 를 붙이고 review_required 를 표시한다.

    return payload 는 **추천(action) 이 아니라 context 와 review flag** 만 포함한다.
    """

    def __init__(self, session: Session):
        self.session = session

    def annotate_candidates(
        self,
        candidates: list[tuple[str, str]],  # (symbol, exchange) 페어
        *,
        lookback_hours: int = 72,
        now: datetime | None = None,
    ) -> list[CandidateFilterEntry]:
        now = now or datetime.now(timezone.utc)
        out: list[CandidateFilterEntry] = []
        signals_cache = list_theme_signals(
            self.session, since_hours=lookback_hours, limit=500, now=now,
        )
        for symbol, exchange in candidates:
            sym = symbol.strip().upper()
            related = [s for s in signals_cache if sym in (s.related_symbols or [])]
            themes = sorted({s.theme for s in related if s.theme})
            risk_flags = sorted({
                rf for s in related for rf in (s.risk_flags or [])
                if rf in ALLOWED_RISK_FLAGS
            })
            if any(rf in _REVIEW_TRIGGERING_FLAGS for rf in risk_flags):
                recommendation = "candidate_filter_review_required"
            else:
                recommendation = "candidate_filter_ok"
            risk_notes = tuple(
                f"[{s.source}] {s.theme or '-'}: {s.title}"
                for s in related[:5]
            )
            entry = CandidateFilterEntry(
                symbol=sym,
                exchange=exchange,
                themes=tuple(themes),
                risk_flags=tuple(risk_flags),
                recommendation=recommendation,
                risk_notes=risk_notes,
            )
            _assert_no_action_tokens(entry.to_dict())
            out.append(entry)
        return out


# ── ThemeContextBuilder — NewsTrendAgent context ────────────────


class ThemeContextBuilder:
    """ThemeSignal → NewsTrendAgent context 변환기. read-only."""

    def __init__(self, session: Session):
        self.session = session

    def build_theme_context(
        self,
        *,
        symbols: list[str] | None = None,
        themes: list[str] | None = None,
        sources: list[str] | None = None,
        lookback_hours: int = 72,
        now: datetime | None = None,
    ) -> ThemeContext:
        now = now or datetime.now(timezone.utc)
        rows = list_theme_signals(
            self.session, since_hours=lookback_hours, limit=500, now=now,
        )

        if sources:
            wanted_sources = {s.strip().lower() for s in sources if s and s.strip()}
            rows = [r for r in rows if r.source in wanted_sources]
        if themes:
            wanted_themes = {t.strip() for t in themes if t and t.strip()}
            rows = [r for r in rows if r.theme in wanted_themes]

        normalized_syms: list[str] = []
        if symbols:
            normalized_syms = sorted({
                s.strip().upper() for s in symbols if s and s.strip()
            })
            filtered: list[ThemeSignal] = []
            for r in rows:
                rs = set(r.related_symbols or [])
                if not rs:
                    filtered.append(r)
                elif rs & set(normalized_syms):
                    filtered.append(r)
            rows = filtered

        by_source: dict[str, int] = {}
        by_theme: dict[str, int] = {}
        by_risk: dict[str, int] = {}
        candidate_flags: set[str] = set()
        risk_notes: list[str] = []
        recent_titles: list[str] = []
        sym_to_signals: dict[str, list[ThemeSignal]] = {}
        review_required_syms: set[str] = set()
        high_attention_themes: set[str] = set()

        for r in rows[:50]:
            recent_titles.append(r.title)

        for r in rows:
            by_source[r.source] = by_source.get(r.source, 0) + 1
            if r.theme:
                by_theme[r.theme] = by_theme.get(r.theme, 0) + 1
            for rf in (r.risk_flags or []):
                if rf in ALLOWED_RISK_FLAGS:
                    by_risk[rf] = by_risk.get(rf, 0) + 1
                    candidate_flags.add(rf)
                    if rf == "high_news_attention" and r.theme:
                        high_attention_themes.add(r.theme)
            related = list(r.related_symbols or [])
            for s in related:
                sym_to_signals.setdefault(s, []).append(r)
                if any(rf in _REVIEW_TRIGGERING_FLAGS for rf in (r.risk_flags or [])):
                    review_required_syms.add(s)
            if len(risk_notes) < 20:
                risk_notes.append(
                    f"[{r.source}] {r.theme or '-'} ({r.provider}): {r.title}"
                )

        target_syms: list[str]
        if normalized_syms:
            target_syms = normalized_syms
        else:
            target_syms = sorted(sym_to_signals.keys())

        summaries: list[SymbolThemeSummary] = []
        for s in target_syms:
            sigs = sym_to_signals.get(s, [])
            themes_for = tuple(sorted({sg.theme for sg in sigs if sg.theme}))
            rflags = tuple(sorted({
                rf for sg in sigs for rf in (sg.risk_flags or [])
                if rf in ALLOWED_RISK_FLAGS
            }))
            high_count = sum(
                1 for sg in sigs
                if "high_news_attention" in (sg.risk_flags or [])
            )
            sents = [sg.sentiment for sg in sigs if sg.sentiment is not None]
            avg = round(sum(sents) / len(sents), 3) if sents else None
            review = any(rf in _REVIEW_TRIGGERING_FLAGS for rf in rflags)
            summaries.append(SymbolThemeSummary(
                symbol=s,
                themes=themes_for,
                risk_flags=rflags,
                signal_count=len(sigs),
                high_attention_count=high_count,
                sentiment_avg=avg,
                recommendation=("candidate_filter_review_required"
                                if review else "candidate_filter_ok"),
            ))

        human = self._render_summary(
            total=len(rows), by_source=by_source, by_theme=by_theme,
            review_required_syms=sorted(review_required_syms),
            high_attention_themes=sorted(high_attention_themes),
            lookback_hours=lookback_hours,
        )

        ctx = ThemeContext(
            generated_at=now.isoformat(),
            lookback_hours=lookback_hours,
            total_signals=len(rows),
            by_source=by_source,
            by_theme=by_theme,
            by_risk_flag=by_risk,
            high_attention_themes=tuple(sorted(high_attention_themes)),
            review_required_symbols=tuple(sorted(review_required_syms)),
            symbol_summaries=tuple(summaries),
            recent_titles=tuple(recent_titles),
            human_summary=human,
            candidate_filter_flags=tuple(sorted(candidate_flags)),
            risk_notes=tuple(risk_notes),
        )
        _assert_no_action_tokens(ctx.to_dict())
        return ctx

    @staticmethod
    def _render_summary(
        *,
        total: int,
        by_source: dict[str, int],
        by_theme: dict[str, int],
        review_required_syms: list[str],
        high_attention_themes: list[str],
        lookback_hours: int,
    ) -> str:
        if total == 0:
            return f"최근 {lookback_hours}시간 내 수집된 theme signal 이 없습니다."
        parts = [f"최근 {lookback_hours}시간 내 theme signal {total}건 수집."]
        risky_sources = [(k, v) for k, v in by_source.items()
                         if k in ("news", "disclosure", "macro_fx") and v > 0]
        if risky_sources:
            parts.append("주요 source: " + ", ".join(
                f"{k}={v}" for k, v in risky_sources) + ".")
        if high_attention_themes:
            parts.append(f"고관심 테마: {', '.join(high_attention_themes[:5])}.")
        if review_required_syms:
            parts.append(f"검토 필요 심볼: {', '.join(review_required_syms[:10])}.")
        parts.append(
            "본 정보는 후보 필터/리스크 설명용이며, 직접 매매 신호가 아닙니다."
        )
        return " ".join(parts)


# ── 안전 가드 ────────────────────────────────────────────────────


def _assert_no_action_tokens(payload: Any) -> None:
    """방어적 검증 — return payload 에 BUY/SELL/ENTER/EXIT 등이 없는지.

    payload 는 dict 또는 dict 의 nested 형태로 가정. 위반 시 RuntimeError.
    """
    def walk(node: Any, path: str = ""):
        if isinstance(node, dict):
            for k, v in node.items():
                kp = f"{path}.{k}" if path else str(k)
                # key 이름이 'action' 또는 'side' 인 경우 forbidden 토큰 검사
                if isinstance(v, str) and k.lower() in {"action", "side", "recommendation_action"}:
                    if v.upper() in FORBIDDEN_ACTION_TOKENS:
                        raise RuntimeError(
                            f"forbidden action token in theme context at {kp}: {v}"
                        )
                walk(v, kp)
        elif isinstance(node, (list, tuple)):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")
        # str 자체는 검사하지 않는다 (제목/요약에 의도적으로 들어갈 수 있음).
    walk(payload)


def summarize_for_agent(signals: list[ThemeSignal]) -> dict[str, Any]:
    """간단 요약 — NewsTrendAgent 가 ctx 로 받을 수 있는 가벼운 형태."""
    by_source: dict[str, int] = {}
    by_theme: dict[str, int] = {}
    by_risk: dict[str, int] = {}
    titles: list[str] = []
    for s in signals:
        by_source[s.source] = by_source.get(s.source, 0) + 1
        if s.theme:
            by_theme[s.theme] = by_theme.get(s.theme, 0) + 1
        for rf in (s.risk_flags or []):
            if rf in ALLOWED_RISK_FLAGS:
                by_risk[rf] = by_risk.get(rf, 0) + 1
        if len(titles) < 20:
            titles.append(s.title)
    out = {
        "count": len(signals),
        "by_source": by_source,
        "by_theme": by_theme,
        "by_risk_flag": by_risk,
        "recent_titles": titles,
        "used_for_order": False,
        "direct_order_allowed": False,
    }
    _assert_no_action_tokens(out)
    return out


__all__ = (
    "SymbolThemeSummary",
    "CandidateFilterEntry",
    "ThemeContext",
    "ThemeFilter",
    "ThemeContextBuilder",
    "summarize_for_agent",
)
