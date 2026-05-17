"""Trend/News/Theme Signals — 체크리스트 #19.

구글트렌드/뉴스/공시/테마 데이터를 read-only 로 수집해 정규화하고
``theme_signals`` 테이블에 영속화한다. 본 데이터는 **후보 필터/리스크 설명**
용도이며 직접 매매 신호가 아니다.

설계 원칙 (CLAUDE.md §2.3, §2.5):
  - 외부 Google Trends / 뉴스 / 공시 API 직접 호출 금지.
  - ``ThemeProvider`` Protocol 추상화 — 실제 외부 adapter 는 후속 단계.
  - ``MockThemeProvider`` 로 외부 네트워크 없이 테스트 가능.
  - ``used_for_order`` 영구 False (advisory 도 아닌 context).
  - ``direct_order_allowed`` 영구 False.
  - action 컬럼 / BUY·SELL·ENTER·EXIT·LONG·SHORT 반환 없음.

중복 제거 키:
  1. (source, provider, signal_id) — provider 가 식별자 제공 시.
  2. (source, provider, content_hash) — 부재 시 본문/제목 sha256.
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ThemeSignal


# ── 분류 카탈로그 ──────────────────────────────────────────────────

SOURCES: tuple[str, ...] = (
    "trend",        # Google Trends 류 검색량/관심도
    "news",         # 뉴스 기사
    "disclosure",   # 공시/규제/거래소 공식 알림
    "theme",        # 정적 테마 태깅 (ETF, AI, Layer2, RWA, ...)
    "macro_fx",     # 거시/환율 관련
    "other",
)


# Theme/News/Trend 에서 도출 가능한 *위험 플래그* 만 사용 가능.
# action(BUY/SELL/ENTER/EXIT) 은 절대 포함하지 않는다.
ALLOWED_RISK_FLAGS: tuple[str, ...] = (
    "high_news_attention",
    "regulatory_attention",
    "exchange_risk_attention",
    "delisting_related_theme",
    "suspicious_hype_theme",
    "macro_fx_attention",
    "review_required",
    "context_only",
)


# 위험 플래그 키워드 — 부분 매칭 (소문자).
_RISK_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("regulatory_attention",
     ("sec", "규제", "regulation", "법안", "compliance",
      "investigation", "조사", "subpoena")),
    ("exchange_risk_attention",
     ("거래소 위험", "exchange risk", "거래소 해킹", "exchange hack",
      "거래소 인출 중단", "withdrawal suspension")),
    ("delisting_related_theme",
     ("delisting", "상장폐지", "거래지원 종료")),
    ("suspicious_hype_theme",
     ("rug", "scam", "ponzi", "hype")),
    ("macro_fx_attention",
     ("fomc", "환율", "fx", "달러", "dollar", "krw", "krw/usd",
      "macro", "interest rate", "금리")),
    ("high_news_attention",
     ("breaking", "긴급", "exclusive", "특보")),
)


# 금지 단어 — 본 모듈의 정규화 결과 어디에도 들어가서는 안 되는 action 토큰.
# normalize_signal 에서 risk_flags 와 title/summary 의 risk_flag 추론을 분리해
# action 토큰이 risk_flag 로 새지 않도록 한다. (방어적 — risk_flag 화이트리스트
# 기반이라 자연적으로 차단되지만 명시적으로 한 번 더 강제한다.)
FORBIDDEN_ACTION_TOKENS: tuple[str, ...] = (
    "BUY", "SELL", "ENTER", "EXIT", "LONG", "SHORT",
)


# 심볼 추출 화이트리스트 — notice_collector 와 동일 정책.
_SYMBOL_WHITELIST: frozenset[str] = frozenset({
    "BTC", "ETH", "XRP", "ADA", "SOL", "DOT", "DOGE", "TRX", "AVAX",
    "MATIC", "ATOM", "LINK", "LTC", "BCH", "ETC", "XLM", "NEAR",
    "FIL", "APT", "ARB", "OP", "SUI", "INJ", "TIA", "SEI", "STX",
})


# ── dataclass ────────────────────────────────────────────────────


@dataclass(frozen=True)
class RawThemeSignal:
    """provider → collector 전달용 정규화 전 원본.

    title 만 필수, 나머지는 모두 optional.
    """

    source: str
    provider: str
    title: str
    signal_id: str | None = None
    theme: str = ""
    summary: str = ""
    url: str = ""
    related_symbols: tuple[str, ...] = ()
    related_keywords: tuple[str, ...] = ()
    score: float | None = None
    sentiment: float | None = None
    published_at: datetime | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedThemeSignal:
    """upsert 직전 객체. action 필드 없음 (BUY/SELL 등 표현하지 않는다)."""

    source: str
    provider: str
    signal_id: str | None
    theme: str
    title: str
    summary: str
    url: str
    related_symbols: tuple[str, ...]
    related_keywords: tuple[str, ...]
    score: float | None
    sentiment: float | None
    risk_flags: tuple[str, ...]
    published_at: datetime | None
    content_hash: str
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class ThemeCollectResult:
    fetched: int
    inserted: int
    updated: int
    skipped: int
    by_source: dict[str, int]
    by_theme: dict[str, int]
    by_risk_flag: dict[str, int]
    signals: tuple[ThemeSignal, ...] = ()


# ── Protocol / Mock provider ─────────────────────────────────────


@runtime_checkable
class ThemeProvider(Protocol):
    """외부 trend/news/disclosure source 추상화 — read-only.

    실제 Google Trends / 뉴스 / 공시 adapter 는 후속 단계에서 같은 Protocol 로
    추가한다. 본 collector 는 provider 를 import 하지 않으며 어떤 거래소 SDK,
    private endpoint, 주문 endpoint 도 호출하지 않는다.
    """

    name: str

    def fetch_signals(
        self,
        since: datetime | None = None,
    ) -> list[RawThemeSignal]:
        ...


class MockThemeProvider:
    """결정론적 mock provider — 외부 네트워크 호출 없음.

    fixture 는 source(trend/news/disclosure/theme/macro_fx) 다양성을 보장하며
    일부는 signal_id 없이 content_hash dedup 검증용으로 설계되어 있다.
    실제 투자 추천이 아닌 mock/test fixture 임을 본문에 명시한다.
    """

    name = "mock"

    def __init__(self, fixtures: list[RawThemeSignal] | None = None):
        self._fixtures = fixtures if fixtures is not None else self._default_fixtures()

    @staticmethod
    def _default_fixtures() -> list[RawThemeSignal]:
        base = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
        return [
            # 1. Google Trends 유사 (signal_id 보유)
            RawThemeSignal(
                source="trend",
                provider="mock_trend",
                signal_id="mock-trend-001",
                title="BTC ETF 관심도 급상승 (Mock fixture)",
                theme="ETF",
                summary="Mock fixture — BTC ETF 검색량 지난 24시간 +180%.",
                url="https://example.test/trend/001",
                related_symbols=("BTC",),
                related_keywords=("ETF", "BTC", "spot ETF"),
                score=0.82,
                sentiment=0.4,
                published_at=base,
            ),
            # 2. 뉴스 (signal_id 보유) — 규제 키워드 → regulatory_attention
            RawThemeSignal(
                source="news",
                provider="mock_news",
                signal_id="mock-news-001",
                title="SEC, 거래소 ABC 조사 착수 (Mock fixture)",
                theme="Regulation",
                summary="Mock fixture — SEC 규제 조사 보도. 실제 사건 아님.",
                url="https://example.test/news/001",
                related_symbols=("ETH",),
                related_keywords=("SEC", "regulation"),
                sentiment=-0.6,
                published_at=base + timedelta(hours=1),
            ),
            # 3. 공시 (signal_id 보유) — delisting 키워드
            RawThemeSignal(
                source="disclosure",
                provider="mock_disclosure",
                signal_id="mock-disc-001",
                title="상장폐지 관련 공시 — LUNA (Mock fixture)",
                theme="Delisting",
                summary="Mock fixture — LUNA delisting 관련 공시.",
                url="https://example.test/disclosure/001",
                related_symbols=("LUNA",),
                related_keywords=("delisting", "LUNA"),
                sentiment=-0.8,
                published_at=base + timedelta(hours=2),
            ),
            # 4. 정적 테마 (signal_id 없음 — content_hash dedup 검증용)
            RawThemeSignal(
                source="theme",
                provider="mock_theme",
                title="AI 테마 — 관련 토큰 다수 활성화 (Mock fixture)",
                theme="AI",
                summary="Mock fixture — AI 관련 테마.",
                related_symbols=("FET", "AGIX"),
                related_keywords=("AI",),
                score=0.6,
                published_at=base + timedelta(hours=3),
            ),
            # 5. 정적 테마 (Layer2)
            RawThemeSignal(
                source="theme",
                provider="mock_theme",
                title="Layer2 테마 — ARB/OP 활성도 (Mock fixture)",
                theme="Layer2",
                summary="Mock fixture — Layer2 활동량.",
                related_symbols=("ARB", "OP"),
                related_keywords=("Layer2", "rollup"),
                score=0.55,
                published_at=base + timedelta(hours=4),
            ),
            # 6. 정적 테마 (RWA)
            RawThemeSignal(
                source="theme",
                provider="mock_theme",
                signal_id="mock-theme-rwa",
                title="RWA 테마 (Mock fixture)",
                theme="RWA",
                summary="Mock fixture — 실물자산 토큰화 테마.",
                related_keywords=("RWA",),
                score=0.45,
                published_at=base + timedelta(hours=5),
            ),
            # 7. 거시/환율 (signal_id 보유)
            RawThemeSignal(
                source="macro_fx",
                provider="mock_macro",
                signal_id="mock-macro-fx-001",
                title="FOMC 금리 결정 임박 — 환율 변동성 (Mock fixture)",
                theme="Macro",
                summary="Mock fixture — FOMC 관련 거시 알림.",
                related_keywords=("FOMC", "환율", "interest rate"),
                sentiment=-0.2,
                published_at=base + timedelta(hours=6),
            ),
            # 8. 거래소 위험 (Exchange Risk)
            RawThemeSignal(
                source="news",
                provider="mock_news",
                signal_id="mock-news-002",
                title="거래소 위험 — 가상의 인출 중단 보도 (Mock fixture)",
                theme="Exchange Risk",
                summary="Mock fixture — exchange withdrawal suspension 키워드 보도.",
                related_symbols=("XRP",),
                related_keywords=("exchange risk", "withdrawal suspension"),
                sentiment=-0.5,
                published_at=base + timedelta(hours=7),
            ),
            # 9. 의심 hype (suspicious_hype_theme)
            RawThemeSignal(
                source="news",
                provider="mock_news",
                title="익명 인플루언서 hype 코인 보도 (Mock fixture)",
                theme="Hype",
                summary="Mock fixture — rug 의심 hype.",
                related_keywords=("hype", "rug"),
                sentiment=0.1,
                published_at=base + timedelta(hours=8),
            ),
        ]

    def fetch_signals(self, since: datetime | None = None) -> list[RawThemeSignal]:
        out = list(self._fixtures)
        if since is not None:
            out = [s for s in out
                   if s.published_at is None or s.published_at >= since]
        return out


# ── 정규화 헬퍼 ───────────────────────────────────────────────────


def compute_content_hash(source: str, provider: str, title: str, summary: str = "") -> str:
    norm = (source.lower() + "\x1f" + provider.lower() + "\x1f"
            + (title or "").strip().lower() + "\x1f"
            + (summary or "").strip().lower())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def infer_risk_flags(
    *,
    title: str,
    summary: str = "",
    theme: str = "",
    keywords: Iterable[str] = (),
    sentiment: float | None = None,
    score: float | None = None,
) -> list[str]:
    """제목/본문/테마/키워드/감정에서 위험 플래그 추론.

    반환은 ALLOWED_RISK_FLAGS 화이트리스트 부분집합. action 토큰은 절대 포함되지 않는다.
    """
    blob = " ".join([
        (title or ""), (summary or ""), (theme or ""), " ".join(keywords or []),
    ]).lower()
    flags: list[str] = []
    seen: set[str] = set()
    for flag, kws in _RISK_KEYWORDS:
        for kw in kws:
            if kw.lower() in blob:
                if flag not in seen:
                    seen.add(flag)
                    flags.append(flag)
                break
    # sentiment 매우 부정 → review_required
    if sentiment is not None and sentiment <= -0.5 and "review_required" not in seen:
        flags.append("review_required")
        seen.add("review_required")
    # score 매우 높음 + hype 의심 키워드 → review_required
    if score is not None and score >= 0.8 and "high_news_attention" not in seen:
        flags.append("high_news_attention")
        seen.add("high_news_attention")
    # 화이트리스트 강제
    out = [f for f in flags if f in ALLOWED_RISK_FLAGS]
    if not out:
        out.append("context_only")
    return out


def normalize_signal(
    raw: RawThemeSignal,
    *,
    collected_at: datetime | None = None,
) -> NormalizedThemeSignal:
    """RawThemeSignal → NormalizedThemeSignal."""
    title = (raw.title or "").strip()
    if not title:
        raise ValueError("theme signal title is empty")
    if len(title) > 512:
        title = title[:509] + "..."

    source = (raw.source or "").strip().lower()
    if not source:
        raise ValueError("theme signal source is empty")
    if source not in SOURCES:
        # 잘못된 source 는 "other" 로 fallback (분류 안 되어도 저장은 가능).
        source = "other"
    provider = (raw.provider or "").strip().lower()
    if not provider:
        raise ValueError("theme signal provider is empty")

    theme = (raw.theme or "").strip()
    summary = (raw.summary or "").strip()
    if len(summary) > 4096:
        summary = summary[:4093] + "..."

    syms = _normalize_symbols(raw.related_symbols)
    kws = _normalize_keywords(raw.related_keywords)
    risk_flags = infer_risk_flags(
        title=title, summary=summary, theme=theme, keywords=kws,
        sentiment=raw.sentiment, score=raw.score,
    )
    # 방어적 — action 토큰이 risk_flags 에 어떠한 경로로도 들어가지 못하도록 한 번 더.
    risk_flags = [f for f in risk_flags if f.upper() not in FORBIDDEN_ACTION_TOKENS]

    score = raw.score
    if score is not None:
        score = float(max(0.0, min(1.0, score)))
    sentiment = raw.sentiment
    if sentiment is not None:
        sentiment = float(max(-1.0, min(1.0, sentiment)))

    return NormalizedThemeSignal(
        source=source,
        provider=provider,
        signal_id=(raw.signal_id or None),
        theme=theme,
        title=title,
        summary=summary,
        url=(raw.url or ""),
        related_symbols=syms,
        related_keywords=kws,
        score=score,
        sentiment=sentiment,
        risk_flags=tuple(risk_flags),
        published_at=raw.published_at,
        content_hash=compute_content_hash(source, provider, title, summary),
        raw_payload=dict(raw.raw_payload or {}),
    )


def _normalize_symbols(syms: Iterable[str]) -> tuple[str, ...]:
    out = sorted({s.strip().upper() for s in (syms or []) if s and s.strip()})
    # 화이트리스트 외 토큰도 그대로 둔다 — provider 가 명시한 심볼은 신뢰.
    return tuple(out)


def _normalize_keywords(keywords: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for k in keywords or ():
        k2 = (k or "").strip()
        if not k2:
            continue
        key = k2.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(k2)
    return tuple(out)


# ── Collector ────────────────────────────────────────────────────


class ThemeSignalCollector:
    """여러 provider 에서 신호를 받아 ``theme_signals`` 로 영속화.

    중복 제거 규칙:
      1. (source, provider, signal_id) — signal_id 있을 때.
      2. (source, provider, content_hash) — 부재 시.

    used_for_order / direct_order_allowed 는 항상 False (영구).
    """

    def __init__(self, providers: dict[str, ThemeProvider] | None = None):
        if providers is None:
            providers = {"mock": MockThemeProvider()}
        self.providers: dict[str, ThemeProvider] = dict(providers)

    def add_provider(self, name: str, provider: ThemeProvider) -> None:
        self.providers[name] = provider

    def collect_once(
        self,
        session: Session,
        *,
        provider_name: str | None = None,
        since: datetime | None = None,
        now: datetime | None = None,
    ) -> ThemeCollectResult:
        now = now or datetime.now(timezone.utc)
        if provider_name is not None:
            if provider_name not in self.providers:
                raise KeyError(f"unknown theme provider: {provider_name}")
            to_run = [(provider_name, self.providers[provider_name])]
        else:
            to_run = list(self.providers.items())

        fetched = 0
        inserted = 0
        updated = 0
        skipped = 0
        by_source: dict[str, int] = {s: 0 for s in SOURCES}
        by_theme: dict[str, int] = {}
        by_risk: dict[str, int] = {f: 0 for f in ALLOWED_RISK_FLAGS}
        kept: list[ThemeSignal] = []

        for pname, provider in to_run:
            try:
                raws = provider.fetch_signals(since=since)
            except Exception:
                continue
            for raw in raws:
                fetched += 1
                try:
                    norm = normalize_signal(raw, collected_at=now)
                except ValueError:
                    skipped += 1
                    continue
                row, is_new = self._upsert(session, norm, now=now)
                if is_new:
                    inserted += 1
                else:
                    updated += 1
                by_source[row.source] = by_source.get(row.source, 0) + 1
                if row.theme:
                    by_theme[row.theme] = by_theme.get(row.theme, 0) + 1
                for rf in (row.risk_flags or []):
                    if rf in by_risk:
                        by_risk[rf] += 1
                kept.append(row)

        session.flush()
        return ThemeCollectResult(
            fetched=fetched, inserted=inserted, updated=updated,
            skipped=skipped, by_source=by_source, by_theme=by_theme,
            by_risk_flag=by_risk, signals=tuple(kept),
        )

    @staticmethod
    def _upsert(
        session: Session,
        norm: NormalizedThemeSignal,
        *,
        now: datetime,
    ) -> tuple[ThemeSignal, bool]:
        existing: ThemeSignal | None = None
        if norm.signal_id:
            existing = session.execute(
                select(ThemeSignal).where(
                    ThemeSignal.source == norm.source,
                    ThemeSignal.provider == norm.provider,
                    ThemeSignal.signal_id == norm.signal_id,
                )
            ).scalar_one_or_none()
        if existing is None:
            existing = session.execute(
                select(ThemeSignal).where(
                    ThemeSignal.source == norm.source,
                    ThemeSignal.provider == norm.provider,
                    ThemeSignal.content_hash == norm.content_hash,
                )
            ).scalar_one_or_none()

        if existing is not None:
            existing.theme = norm.theme or existing.theme
            existing.title = norm.title
            existing.summary = norm.summary or existing.summary
            existing.url = norm.url or existing.url
            existing.related_symbols = list(norm.related_symbols)
            existing.related_keywords = list(norm.related_keywords)
            existing.score = norm.score
            existing.sentiment = norm.sentiment
            existing.risk_flags = list(norm.risk_flags)
            if norm.published_at is not None:
                existing.published_at = norm.published_at
            existing.content_hash = norm.content_hash
            existing.raw_payload = norm.raw_payload
            existing.used_for_order = False  # 영구 False (CLAUDE.md §2.3)
            existing.direct_order_allowed = False
            existing.updated_at = now
            return existing, False

        row = ThemeSignal(
            source=norm.source,
            provider=norm.provider,
            signal_id=norm.signal_id,
            theme=norm.theme,
            title=norm.title,
            summary=norm.summary,
            url=norm.url,
            related_symbols=list(norm.related_symbols),
            related_keywords=list(norm.related_keywords),
            score=norm.score,
            sentiment=norm.sentiment,
            risk_flags=list(norm.risk_flags),
            published_at=norm.published_at,
            collected_at=now,
            content_hash=norm.content_hash,
            used_for_order=False,
            direct_order_allowed=False,
            raw_payload=norm.raw_payload,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row, True


# ── 조회 헬퍼 (read-only) ─────────────────────────────────────────


def list_theme_signals(
    session: Session,
    *,
    source: str | None = None,
    provider: str | None = None,
    theme: str | None = None,
    symbol: str | None = None,
    since_hours: int | None = None,
    since: datetime | None = None,
    limit: int = 200,
    now: datetime | None = None,
) -> list[ThemeSignal]:
    now = now or datetime.now(timezone.utc)
    stmt = select(ThemeSignal)
    if source is not None:
        stmt = stmt.where(ThemeSignal.source == source.lower())
    if provider is not None:
        stmt = stmt.where(ThemeSignal.provider == provider.lower())
    if theme is not None:
        stmt = stmt.where(ThemeSignal.theme == theme)
    if since is None and since_hours is not None and since_hours > 0:
        since = now - timedelta(hours=int(since_hours))
    if since is not None:
        stmt = stmt.where(ThemeSignal.collected_at >= since)
    stmt = stmt.order_by(ThemeSignal.collected_at.desc()).limit(int(limit))
    rows = list(session.execute(stmt).scalars().all())
    if symbol is not None:
        sym = symbol.strip().upper()
        rows = [r for r in rows if sym in (r.related_symbols or [])]
    return rows


def signal_to_dict(s: ThemeSignal) -> dict[str, Any]:
    return {
        "id":               s.id,
        "source":           s.source,
        "provider":         s.provider,
        "signal_id":        s.signal_id,
        "theme":            s.theme,
        "title":            s.title,
        "summary":          s.summary,
        "url":              s.url,
        "related_symbols":  list(s.related_symbols or []),
        "related_keywords": list(s.related_keywords or []),
        "score":            s.score,
        "sentiment":        s.sentiment,
        "risk_flags":       list(s.risk_flags or []),
        "published_at":     s.published_at.isoformat() if s.published_at else None,
        "collected_at":     s.collected_at.isoformat() if s.collected_at else None,
        # CLAUDE.md §2.3 — 응답에 명시 (영구 False)
        "used_for_order":        False,
        "direct_order_allowed":  False,
    }


__all__ = (
    "SOURCES",
    "ALLOWED_RISK_FLAGS",
    "FORBIDDEN_ACTION_TOKENS",
    "RawThemeSignal",
    "NormalizedThemeSignal",
    "ThemeCollectResult",
    "ThemeProvider",
    "MockThemeProvider",
    "ThemeSignalCollector",
    "compute_content_hash",
    "infer_risk_flags",
    "normalize_signal",
    "list_theme_signals",
    "signal_to_dict",
)
