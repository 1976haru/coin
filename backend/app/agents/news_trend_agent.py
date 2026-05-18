"""News / Trend Agent — 체크리스트 #39 News/Trend Agent.

키워드 증가·뉴스 증가·공시 이벤트를 요약해 *테마 후보 발굴을 보조* 하는 Agent.

본 모듈은 #37 6-role Agent Architecture 의 ``STRATEGY_RESEARCHER`` 역할
specialization 이다 — 전략 후보를 *조사* 하지만 매수/매도 결론을 내리지 않는다.

원칙 (CLAUDE.md §2.3 / §2.4 / §3.1):
  - 매수/매도/진입/청산 결론을 내리지 *않는다*.
  - broker / adapter / OrderGateway / MockBroker / PaperBroker 를 *호출하지 않는다*.
  - place_order / cancel_order / get_balance / submit_order / withdraw / deposit
    / set_leverage / set_margin 를 *호출하지 않는다*.
  - 외부 뉴스 / 트렌드 / 공시 API 를 *직접 호출하지 않는다* — 입력으로 받은
    context 만 요약한다.
  - 출력에 ``direct_order_allowed=False`` / ``broker_call_allowed=False`` /
    ``used_for_order=False`` 영구.
  - ``ThemeCandidate.used_for_order`` 영구 False.
  - BUY / SELL / ENTER / EXIT 를 실행 action 으로 반환하지 않는다.
  - ``executable_order`` / ``order_request`` / ``broker_payload`` /
    ``place_order_payload`` 출력 키 부재.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Mapping, Sequence

from app.agents.base import (
    AgentArchitectureRole,
    AgentCard,
    AgentDecision,
    AgentFinding,
    AgentInput,
    AgentOutput,
    AgentPermission,
    AgentSafetyPolicy,
    StructuredAgentBase,
)


# ── 상수 라벨 ────────────────────────────────────────────────────


class RiskLevel:
    """테마 후보 리스크 등급. *주문 명령이 아님*."""

    NORMAL = "NORMAL"
    HIGH_ATTENTION = "HIGH_ATTENTION"
    HYPE = "HYPE"
    UNKNOWN = "UNKNOWN"


class TrendDirection:
    """키워드 / 뉴스 추세 방향 라벨."""

    SURGING = "SURGING"
    GROWING = "GROWING"
    STABLE = "STABLE"
    DECLINING = "DECLINING"
    UNKNOWN = "UNKNOWN"


# ── 설정 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NewsTrendAgentConfig:
    """NewsTrendAgent 임계값 + 정책 토글.

    모든 값은 *조사/요약* 목적이며 주문 권한이 아니다.
    """

    top_keywords_limit: int = 20
    top_themes_limit: int = 10
    min_keyword_growth_pct: float = 50.0
    min_news_growth_pct: float = 50.0
    surging_growth_pct: float = 200.0
    declining_growth_pct: float = -30.0
    high_attention_threshold: float = 80.0  # theme candidate score
    hype_risk_threshold: float = 90.0
    negative_sentiment_threshold: float = -0.5
    lookback_hours: int = 24
    # 영구 False — 본 모듈은 어떤 주문 권한도 부여하지 않음
    direct_order_allowed: bool = False
    broker_call_allowed: bool = False
    used_for_order: bool = False


# ── 데이터 구조 ──────────────────────────────────────────────────


@dataclass(frozen=True)
class KeywordTrendSummary:
    """단일 키워드 추세 요약."""

    keyword: str
    current_count: int
    previous_count: int
    growth_pct: float | None
    direction: str
    related_symbols: tuple[str, ...]
    sources: tuple[str, ...]


@dataclass(frozen=True)
class NewsVolumeSummary:
    """뉴스 볼륨 요약."""

    current_count: int
    previous_count: int
    growth_pct: float | None
    direction: str
    by_source: Mapping[str, int]
    window_hours: int


@dataclass(frozen=True)
class DisclosureEventSummary:
    """공시 / 거래소 공지 이벤트 요약."""

    exchange: str
    symbol: str | None
    notice_type: str
    severity: str
    title: str
    published_at: str | None
    risk_flag: str | None


@dataclass(frozen=True)
class ThemeCandidate:
    """테마 후보. *주문 명령이 아님*.

    ``used_for_order`` 영구 False — Strategy / Risk 가 후속에서 검토.
    """

    theme: str
    related_symbols: tuple[str, ...]
    related_keywords: tuple[str, ...]
    attention_score: float
    sentiment_avg: float | None
    risk_level: str
    sources: tuple[str, ...]
    notes: tuple[str, ...] = ()
    used_for_order: bool = False  # 영구 False


@dataclass(frozen=True)
class ThemeRiskNote:
    """테마 후보의 리스크 노트."""

    theme: str
    code: str          # hype_risk / high_attention / negative_sentiment / ...
    severity: str      # INFO / WARNING / HIGH / CRITICAL
    message: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NewsTrendAgentOutput:
    """News / Trend Agent 의 JSON structured output.

    *direct_order_allowed* / *broker_call_allowed* / *used_for_order* 영구 False.
    BUY / SELL / ENTER / EXIT 실행 action 부재.
    """

    role: str
    version: str
    generated_at: datetime
    summary: str
    has_data: bool
    keyword_trends: tuple[KeywordTrendSummary, ...]
    news_volume: NewsVolumeSummary | None
    disclosures: tuple[DisclosureEventSummary, ...]
    theme_candidates: tuple[ThemeCandidate, ...]
    risk_notes: tuple[ThemeRiskNote, ...]
    findings: tuple[AgentFinding, ...]
    direct_order_allowed: bool = False   # 영구 False
    broker_call_allowed: bool = False    # 영구 False
    used_for_order: bool = False         # 영구 False

    def to_dict(self) -> dict:
        return {
            "kind": "news_trend_agent_output",
            "role": self.role,
            "version": self.version,
            "generated_at": self.generated_at.isoformat(),
            "summary": self.summary,
            "has_data": self.has_data,
            "keyword_trends": [
                _kw_to_dict(k) for k in self.keyword_trends
            ],
            "news_volume": (
                None if self.news_volume is None
                else _news_volume_to_dict(self.news_volume)
            ),
            "disclosures": [
                _disclosure_to_dict(d) for d in self.disclosures
            ],
            "theme_candidates": [
                _theme_candidate_to_dict(t) for t in self.theme_candidates
            ],
            "risk_notes": [
                _risk_note_to_dict(r) for r in self.risk_notes
            ],
            "findings": [f.to_dict() for f in self.findings],
            "direct_order_allowed": self.direct_order_allowed,
            "broker_call_allowed": self.broker_call_allowed,
            "used_for_order": self.used_for_order,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, sort_keys=True)


def _kw_to_dict(k: KeywordTrendSummary) -> dict:
    return {
        "keyword": k.keyword,
        "current_count": k.current_count,
        "previous_count": k.previous_count,
        "growth_pct": k.growth_pct,
        "direction": k.direction,
        "related_symbols": list(k.related_symbols),
        "sources": list(k.sources),
    }


def _news_volume_to_dict(n: NewsVolumeSummary) -> dict:
    return {
        "current_count": n.current_count,
        "previous_count": n.previous_count,
        "growth_pct": n.growth_pct,
        "direction": n.direction,
        "by_source": dict(n.by_source),
        "window_hours": n.window_hours,
    }


def _disclosure_to_dict(d: DisclosureEventSummary) -> dict:
    return {
        "exchange": d.exchange,
        "symbol": d.symbol,
        "notice_type": d.notice_type,
        "severity": d.severity,
        "title": d.title,
        "published_at": d.published_at,
        "risk_flag": d.risk_flag,
    }


def _theme_candidate_to_dict(t: ThemeCandidate) -> dict:
    return {
        "theme": t.theme,
        "related_symbols": list(t.related_symbols),
        "related_keywords": list(t.related_keywords),
        "attention_score": t.attention_score,
        "sentiment_avg": t.sentiment_avg,
        "risk_level": t.risk_level,
        "sources": list(t.sources),
        "notes": list(t.notes),
        "used_for_order": t.used_for_order,
    }


def _risk_note_to_dict(r: ThemeRiskNote) -> dict:
    return {
        "theme": r.theme,
        "code": r.code,
        "severity": r.severity,
        "message": r.message,
        "evidence": dict(r.evidence),
    }


# ── 내부 헬퍼 ────────────────────────────────────────────────────


def _to_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _norm_symbols(syms: Any) -> tuple[str, ...]:
    if not syms:
        return ()
    if isinstance(syms, str):
        syms = (syms,)
    out: list[str] = []
    for s in syms:
        if s is None:
            continue
        text = str(s).strip().upper()
        if text:
            out.append(text)
    return tuple(out)


def _norm_keywords(kws: Any) -> tuple[str, ...]:
    if not kws:
        return ()
    if isinstance(kws, str):
        kws = (kws,)
    out: list[str] = []
    for k in kws:
        if k is None:
            continue
        text = str(k).strip()
        if text:
            out.append(text)
    return tuple(out)


def _growth_pct(current: int, previous: int) -> float | None:
    """성장률 % 계산. previous=0 이면 current 가 0 이상이면 None (분모 0)."""
    if previous <= 0:
        if current <= 0:
            return 0.0
        return None  # division by zero — caller 가 별도 처리
    return (current - previous) / previous * 100.0


def _classify_direction(
    growth_pct: float | None,
    *,
    surging: float,
    growing_min: float,
    declining_max: float,
) -> str:
    """방향 분류. DECLINING 검사를 먼저 수행 — growing_min 이 음수로 조정된
    경우에도 음수 growth 가 GROWING 으로 잘못 분류되지 않게 보장.
    """
    if growth_pct is None:
        return TrendDirection.UNKNOWN
    if growth_pct <= declining_max:
        return TrendDirection.DECLINING
    if growth_pct >= surging:
        return TrendDirection.SURGING
    if growth_pct >= growing_min:
        return TrendDirection.GROWING
    return TrendDirection.STABLE


# ── 요약 함수 ────────────────────────────────────────────────────


def summarize_keyword_trends(
    payload: Mapping[str, Any] | None,
    config: NewsTrendAgentConfig | None = None,
) -> tuple[KeywordTrendSummary, ...]:
    """키워드 증가 추세 요약.

    입력: ``payload["keywords"]`` — list of dict
        각 dict: ``keyword`` / ``current_count`` / ``previous_count``
                 / ``related_symbols`` (옵션) / ``sources`` (옵션)
    """
    cfg = config or NewsTrendAgentConfig()
    if not payload:
        return ()
    raw = payload.get("keywords") or ()
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        return ()
    rows: list[KeywordTrendSummary] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        kw = str(item.get("keyword", "")).strip()
        if not kw:
            continue
        cur = _to_int(item.get("current_count"), default=0)
        prev = _to_int(item.get("previous_count"), default=0)
        growth = _growth_pct(cur, prev)
        # min_keyword_growth_pct 필터 — None (신규 키워드) 도 통과
        if growth is not None and growth < cfg.min_keyword_growth_pct:
            continue
        direction = _classify_direction(
            growth,
            surging=cfg.surging_growth_pct,
            growing_min=cfg.min_keyword_growth_pct,
            declining_max=cfg.declining_growth_pct,
        )
        rows.append(KeywordTrendSummary(
            keyword=kw,
            current_count=cur,
            previous_count=prev,
            growth_pct=growth,
            direction=direction,
            related_symbols=_norm_symbols(item.get("related_symbols")),
            sources=_norm_keywords(item.get("sources")),
        ))
    # 정렬: growth_pct None (신규) 을 가장 위로, 그다음 큰 값 순
    rows.sort(
        key=lambda r: (
            0 if r.growth_pct is None else 1,
            -(r.growth_pct if r.growth_pct is not None else 0),
        ),
    )
    return tuple(rows[: cfg.top_keywords_limit])


def summarize_news_volume(
    payload: Mapping[str, Any] | None,
    config: NewsTrendAgentConfig | None = None,
) -> NewsVolumeSummary | None:
    """뉴스 볼륨 요약.

    입력: ``payload["news_volume"]`` — dict
        ``current`` / ``previous`` / ``by_source`` / ``window_hours``
    """
    cfg = config or NewsTrendAgentConfig()
    if not payload:
        return None
    raw = payload.get("news_volume")
    if not isinstance(raw, Mapping):
        return None
    cur = _to_int(raw.get("current"), default=0)
    prev = _to_int(raw.get("previous"), default=0)
    growth = _growth_pct(cur, prev)
    direction = _classify_direction(
        growth,
        surging=cfg.surging_growth_pct,
        growing_min=cfg.min_news_growth_pct,
        declining_max=cfg.declining_growth_pct,
    )
    by_source_raw = raw.get("by_source") or {}
    by_source: dict[str, int] = {}
    if isinstance(by_source_raw, Mapping):
        for k, v in by_source_raw.items():
            by_source[str(k)] = _to_int(v, default=0)
    window = _to_int(raw.get("window_hours"), default=cfg.lookback_hours)
    return NewsVolumeSummary(
        current_count=cur,
        previous_count=prev,
        growth_pct=growth,
        direction=direction,
        by_source=by_source,
        window_hours=window,
    )


def summarize_disclosures(
    payload: Mapping[str, Any] | None,
) -> tuple[DisclosureEventSummary, ...]:
    """공시 / 거래소 공지 이벤트 요약.

    입력: ``payload["disclosures"]`` (list) 또는 ``payload["notice_context"]``
        의 일부 필드.
    """
    if not payload:
        return ()
    out: list[DisclosureEventSummary] = []
    raw = payload.get("disclosures")
    if isinstance(raw, Sequence) and not isinstance(raw, str):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            out.append(DisclosureEventSummary(
                exchange=str(item.get("exchange", "")),
                symbol=(str(item["symbol"]).upper()
                         if item.get("symbol") else None),
                notice_type=str(item.get("notice_type", "")),
                severity=str(item.get("severity", "INFO")).upper(),
                title=str(item.get("title", "")),
                published_at=(
                    str(item["published_at"])
                    if item.get("published_at") else None
                ),
                risk_flag=(
                    str(item["risk_flag"]) if item.get("risk_flag") else None
                ),
            ))
    # NoticeContext fallback (#18) — risk_notes 의 raw text 를 풀어서 사용
    if not out:
        notice_ctx = payload.get("notice_context")
        if isinstance(notice_ctx, Mapping):
            summaries = notice_ctx.get("symbol_summaries") or ()
            if isinstance(summaries, Sequence):
                for s in summaries:
                    if not isinstance(s, Mapping):
                        continue
                    out.append(DisclosureEventSummary(
                        exchange=str(s.get("exchange", "")),
                        symbol=(
                            str(s["symbol"]).upper()
                            if s.get("symbol") else None
                        ),
                        notice_type=str(s.get("recommendation", "summary")),
                        severity=str(s.get("severity", "INFO")).upper(),
                        title=str(s.get("symbol", "")) + " 공지 요약",
                        published_at=None,
                        risk_flag=(
                            str(s.get("risk_flags", ("",))[0])
                            if s.get("risk_flags") else None
                        ),
                    ))
    return tuple(out)


def derive_theme_candidates(
    payload: Mapping[str, Any] | None,
    config: NewsTrendAgentConfig | None = None,
) -> tuple[ThemeCandidate, ...]:
    """테마 후보 + attention score 산출.

    입력: ``payload["theme_signals"]`` — list of dict (ThemeSignal 호환)
        각 dict: ``theme`` / ``related_symbols`` / ``related_keywords``
                 / ``score`` (0~1 또는 0~100) / ``sentiment`` / ``sources``

    score 가 0~1 범위면 100 배 환산. 같은 theme 의 다중 signal 은 attention_score
    누적 (cap=100), sentiment 평균.
    """
    cfg = config or NewsTrendAgentConfig()
    if not payload:
        return ()
    raw = payload.get("theme_signals") or ()
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        return ()

    by_theme: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        theme = str(item.get("theme", "")).strip()
        if not theme:
            continue
        score_raw = _to_float(item.get("score"))
        if score_raw is None:
            score = 0.0
        elif score_raw <= 1.0 and score_raw >= -1.0:
            # 0~1 정규화 가정 → 100 단위로 환산
            score = max(0.0, score_raw * 100.0)
        else:
            score = max(0.0, min(100.0, score_raw))
        sent = _to_float(item.get("sentiment"))
        bucket = by_theme.setdefault(theme, {
            "symbols": set(),
            "keywords": set(),
            "score": 0.0,
            "sent_sum": 0.0,
            "sent_count": 0,
            "sources": set(),
            "max_score": 0.0,
        })
        for s in _norm_symbols(item.get("related_symbols")):
            bucket["symbols"].add(s)
        for k in _norm_keywords(item.get("related_keywords")):
            bucket["keywords"].add(k)
        bucket["score"] = min(100.0, bucket["score"] + score * 0.5)
        bucket["max_score"] = max(bucket["max_score"], score)
        if sent is not None:
            bucket["sent_sum"] += sent
            bucket["sent_count"] += 1
        for src in _norm_keywords(item.get("sources")):
            bucket["sources"].add(src)
        # provider / source 호환 키 추가
        provider = item.get("provider") or item.get("source")
        if provider:
            bucket["sources"].add(str(provider))

    rows: list[ThemeCandidate] = []
    for theme, b in by_theme.items():
        attention = max(b["score"], b["max_score"])
        attention = max(0.0, min(100.0, attention))
        sent_avg = (
            b["sent_sum"] / b["sent_count"]
            if b["sent_count"] > 0 else None
        )
        # risk_level 분류
        if attention >= cfg.hype_risk_threshold:
            risk = RiskLevel.HYPE
        elif attention >= cfg.high_attention_threshold:
            risk = RiskLevel.HIGH_ATTENTION
        else:
            risk = RiskLevel.NORMAL
        notes: list[str] = []
        if risk in (RiskLevel.HYPE, RiskLevel.HIGH_ATTENTION):
            notes.append(
                "elevated attention — observe only, not an order signal"
            )
        if sent_avg is not None and sent_avg <= cfg.negative_sentiment_threshold:
            notes.append("negative sentiment observed — review")
        rows.append(ThemeCandidate(
            theme=theme,
            related_symbols=tuple(sorted(b["symbols"])),
            related_keywords=tuple(sorted(b["keywords"])),
            attention_score=attention,
            sentiment_avg=sent_avg,
            risk_level=risk,
            sources=tuple(sorted(b["sources"])),
            notes=tuple(notes),
        ))
    rows.sort(key=lambda r: r.attention_score, reverse=True)
    return tuple(rows[: cfg.top_themes_limit])


def compute_theme_risk_notes(
    candidates: Sequence[ThemeCandidate],
    config: NewsTrendAgentConfig | None = None,
) -> tuple[ThemeRiskNote, ...]:
    """ThemeCandidate 리스트에서 리스크 노트 산출."""
    cfg = config or NewsTrendAgentConfig()
    out: list[ThemeRiskNote] = []
    for c in candidates:
        if c.risk_level == RiskLevel.HYPE:
            out.append(ThemeRiskNote(
                theme=c.theme,
                code="hype_risk",
                severity="HIGH",
                message=(
                    f"theme {c.theme} attention_score={c.attention_score:.1f} "
                    f">= hype_risk_threshold {cfg.hype_risk_threshold}"
                ),
                evidence={
                    "attention_score": c.attention_score,
                    "threshold": cfg.hype_risk_threshold,
                },
            ))
        elif c.risk_level == RiskLevel.HIGH_ATTENTION:
            out.append(ThemeRiskNote(
                theme=c.theme,
                code="high_attention",
                severity="WARNING",
                message=(
                    f"theme {c.theme} attention_score={c.attention_score:.1f} "
                    f">= high_attention_threshold {cfg.high_attention_threshold}"
                ),
                evidence={
                    "attention_score": c.attention_score,
                    "threshold": cfg.high_attention_threshold,
                },
            ))
        if (c.sentiment_avg is not None
                and c.sentiment_avg <= cfg.negative_sentiment_threshold):
            out.append(ThemeRiskNote(
                theme=c.theme,
                code="negative_sentiment",
                severity="WARNING",
                message=(
                    f"theme {c.theme} sentiment_avg={c.sentiment_avg:.2f} "
                    f"<= {cfg.negative_sentiment_threshold}"
                ),
                evidence={
                    "sentiment_avg": c.sentiment_avg,
                    "threshold": cfg.negative_sentiment_threshold,
                },
            ))
    return tuple(out)


# ── Agent 본체 ────────────────────────────────────────────────────


class NewsTrendAgent(StructuredAgentBase):
    """뉴스 / 트렌드 / 공시 기반 테마 후보 발굴 보조 Agent.

    *주문 결론을 만들지 않으며* broker / adapter / OrderGateway / 외부 API 를
    호출하지 않는다.
    """

    role: ClassVar[AgentArchitectureRole] = (
        AgentArchitectureRole.STRATEGY_RESEARCHER
    )
    card: ClassVar[AgentCard] = AgentCard(
        role=AgentArchitectureRole.STRATEGY_RESEARCHER,
        title="News / Trend Agent",
        description=(
            "키워드 증가·뉴스 증가·공시/거래소 공지 이벤트를 요약해 테마 후보 "
            "발굴을 보조한다. 매수/매도 결론을 내리지 않으며 broker/adapter/"
            "OrderGateway 를 호출하지 않는다. 외부 뉴스/트렌드 API 직접 호출 0."
        ),
        inputs=(
            "keywords", "news_volume", "disclosures", "theme_signals",
            "notice_context",
        ),
        outputs=(
            "keyword_trends", "news_volume_summary", "disclosure_events",
            "theme_candidates", "theme_risk_notes",
        ),
        forbidden_actions=(
            "execute_order", "invoke_broker", "invoke_order_gateway",
            "write_order_request", "place_order", "cancel_order",
            "get_balance", "fetch_external_news_api", "fetch_external_trend_api",
        ),
        allowed_permissions=frozenset((
            AgentPermission.READ_NOTICES,
            AgentPermission.READ_THEMES,
            AgentPermission.READ_MARKET_DATA,
            AgentPermission.WRITE_FINDING,
        )),
    )
    safety: ClassVar[AgentSafetyPolicy] = AgentSafetyPolicy()

    def __init__(self, config: NewsTrendAgentConfig | None = None):
        self.config = config or NewsTrendAgentConfig()

    def analyze(self, input: AgentInput) -> NewsTrendAgentOutput:
        """``NewsTrendAgentOutput`` (rich) 반환."""
        payload = input.payload or {}
        cfg = self.config

        keywords = summarize_keyword_trends(payload, cfg)
        news_vol = summarize_news_volume(payload, cfg)
        disclosures = summarize_disclosures(payload)
        themes = derive_theme_candidates(payload, cfg)
        risk_notes = compute_theme_risk_notes(themes, cfg)

        has_data = bool(keywords or news_vol or disclosures or themes)

        findings: list[AgentFinding] = []
        if not has_data:
            findings.append(AgentFinding(
                kind="insufficient_data",
                severity="WARNING",
                message=(
                    "keywords / news_volume / disclosures / theme_signals "
                    "모두 미수신"
                ),
            ))
            summary = "insufficient_data — no news/trend context provided"
        else:
            if keywords:
                surging = sum(
                    1 for k in keywords if k.direction == TrendDirection.SURGING
                )
                findings.append(AgentFinding(
                    kind="keyword_trends_observed",
                    severity="INFO",
                    message=f"{len(keywords)} keyword trends ({surging} surging)",
                    evidence={"total": len(keywords), "surging": surging},
                ))
            if news_vol is not None:
                findings.append(AgentFinding(
                    kind="news_volume_observed",
                    severity="INFO",
                    message=(
                        f"news volume direction={news_vol.direction}, "
                        f"growth_pct={news_vol.growth_pct}"
                    ),
                    evidence={
                        "direction": news_vol.direction,
                        "growth_pct": news_vol.growth_pct,
                    },
                ))
            if disclosures:
                high = sum(
                    1 for d in disclosures
                    if d.severity in ("HIGH", "CRITICAL")
                )
                findings.append(AgentFinding(
                    kind="disclosure_events_observed",
                    severity="WARNING" if high else "INFO",
                    message=(
                        f"{len(disclosures)} disclosure/notice events "
                        f"({high} high/critical)"
                    ),
                    evidence={"total": len(disclosures), "high": high},
                ))
            for note in risk_notes:
                findings.append(AgentFinding(
                    kind=note.code,
                    severity=note.severity,
                    message=note.message,
                    evidence=note.evidence,
                ))
            summary = (
                f"News/Trend research: keywords={len(keywords)}, "
                f"themes={len(themes)}, disclosures={len(disclosures)}, "
                f"risk_notes={len(risk_notes)}"
            )

        return NewsTrendAgentOutput(
            role=self.role.value,
            version="v1",
            generated_at=datetime.now(timezone.utc),
            summary=summary,
            has_data=has_data,
            keyword_trends=keywords,
            news_volume=news_vol,
            disclosures=disclosures,
            theme_candidates=themes,
            risk_notes=risk_notes,
            findings=tuple(findings),
        )

    def evaluate(self, input: AgentInput) -> AgentOutput:
        """``StructuredAgentBase`` contract 충족 — AgentOutput 반환."""
        rich = self.analyze(input)
        decision = AgentDecision(
            role=self.role.value,
            summary=rich.summary,
            findings=rich.findings,
            recommendations=(),
        )
        return self.make_output(decision)


__all__ = (
    "RiskLevel",
    "TrendDirection",
    "NewsTrendAgentConfig",
    "KeywordTrendSummary",
    "NewsVolumeSummary",
    "DisclosureEventSummary",
    "ThemeCandidate",
    "ThemeRiskNote",
    "NewsTrendAgentOutput",
    "NewsTrendAgent",
    "summarize_keyword_trends",
    "summarize_news_volume",
    "summarize_disclosures",
    "derive_theme_candidates",
    "compute_theme_risk_notes",
)
