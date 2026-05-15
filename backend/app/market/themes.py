"""Trend/News/Theme Signals — 체크리스트 #19.

시장 regime(추세/횡보/변동성), 뉴스 이벤트, 테마 분류를 한 곳에서 모아
AgentOrchestrator.decide(context=...) 에 주입할 dict 형태로 반환한다.

설계 원칙:
  - classify_regime() 은 순수 함수 — 외부 I/O 없음, 가격 시퀀스만 받음
  - ThemeRegistry / NewsRegistry 는 메모리 (DB 영속은 후속)
  - 본 모듈은 거래소 SDK·OrderGateway 를 import 하지 않음 (모듈 경계)
  - assess_market_context(symbol, exchange) → AgentOrchestrator 가 그대로 소비할 dict
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Iterable, Literal, Sequence


# ── Regime 분류 ───────────────────────────────────────────────────

MarketRegime = Literal[
    "TREND_UP", "TREND_DOWN", "RANGE", "UNKNOWN",
]
VolBand = Literal["LOW", "NORMAL", "HIGH", "UNKNOWN"]


@dataclass(frozen=True)
class RegimeSnapshot:
    regime: MarketRegime
    vol_band: VolBand
    confidence: float       # 0~1
    slope_pct: float        # SMA 기울기 (%, 단위 봉당 변화율)
    cv_pct: float           # 변동성 (std/mean × 100)
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


# 기본 임계값 — 운용 시 settings 로 덮어쓸 수 있게 함수 인자로 노출
DEFAULT_TREND_SLOPE_PCT = 0.10   # 봉당 0.10% 이상 → TREND
DEFAULT_HIGH_VOL_CV_PCT = 4.0
DEFAULT_LOW_VOL_CV_PCT  = 0.8
DEFAULT_MIN_SAMPLES     = 20


def classify_regime(
    closes: Sequence[float],
    volumes: Sequence[float] | None = None,
    *,
    trend_slope_pct: float = DEFAULT_TREND_SLOPE_PCT,
    high_vol_cv_pct: float = DEFAULT_HIGH_VOL_CV_PCT,
    low_vol_cv_pct: float = DEFAULT_LOW_VOL_CV_PCT,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> RegimeSnapshot:
    """가격 시퀀스로부터 regime 추정.

    - slope_pct: 단순 선형 기울기를 마지막 가격 대비 % 환산
    - cv_pct:    표준편차/평균 × 100
    """
    n = len(closes)
    if n < min_samples or any(c <= 0 for c in closes):
        return RegimeSnapshot(
            regime="UNKNOWN", vol_band="UNKNOWN",
            confidence=0.0, slope_pct=0.0, cv_pct=0.0,
            reason=f"표본 부족 또는 비정상 가격 (n={n})",
        )

    # 평균/표준편차
    mean = sum(closes) / n
    var  = sum((c - mean) ** 2 for c in closes) / n
    std  = math.sqrt(var)
    cv_pct = (std / mean) * 100.0 if mean > 0 else 0.0

    # 선형 기울기 (least-squares, x=0..n-1)
    xs = list(range(n))
    x_mean = sum(xs) / n
    cov = sum((xs[i] - x_mean) * (closes[i] - mean) for i in range(n))
    var_x = sum((x - x_mean) ** 2 for x in xs)
    slope = cov / var_x if var_x > 0 else 0.0
    last_price = closes[-1]
    slope_pct = (slope / last_price) * 100.0 if last_price > 0 else 0.0

    # vol band
    if cv_pct >= high_vol_cv_pct:
        vol_band: VolBand = "HIGH"
    elif cv_pct <= low_vol_cv_pct:
        vol_band = "LOW"
    else:
        vol_band = "NORMAL"

    # regime
    if slope_pct >= trend_slope_pct:
        regime: MarketRegime = "TREND_UP"
        confidence = min(1.0, abs(slope_pct) / (trend_slope_pct * 5))
        reason = f"slope {slope_pct:+.3f}% / 봉 ≥ {trend_slope_pct}%"
    elif slope_pct <= -trend_slope_pct:
        regime = "TREND_DOWN"
        confidence = min(1.0, abs(slope_pct) / (trend_slope_pct * 5))
        reason = f"slope {slope_pct:+.3f}% / 봉 ≤ -{trend_slope_pct}%"
    else:
        regime = "RANGE"
        confidence = 1.0 - min(1.0, abs(slope_pct) / trend_slope_pct)
        reason = f"slope |{slope_pct:+.3f}|% < {trend_slope_pct}%"

    if vol_band == "HIGH":
        reason += f" + 고변동성 (cv={cv_pct:.2f}%)"
    elif vol_band == "LOW":
        reason += f" + 저변동성 (cv={cv_pct:.2f}%)"

    return RegimeSnapshot(
        regime=regime, vol_band=vol_band,
        confidence=round(confidence, 3),
        slope_pct=round(slope_pct, 4),
        cv_pct=round(cv_pct, 3),
        reason=reason,
    )


# ── Theme 분류 ────────────────────────────────────────────────────

class ThemeRegistry:
    """심볼 ↔ 테마 다대다 매핑 (메모리).

    테마 예: "AI", "DeFi", "L1", "meme", "RWA", "stablecoin".
    같은 심볼이 여러 테마에 속할 수 있다.
    """

    def __init__(self):
        self._theme_to_symbols: dict[str, set[tuple[str, str]]] = {}
        self._symbol_to_themes: dict[tuple[str, str], set[str]] = {}

    def tag(self, theme: str, symbol: str, exchange: str = "*") -> None:
        theme = theme.strip()
        if not theme:
            raise ValueError("theme 이 빈 문자열")
        key = (symbol, exchange)
        self._theme_to_symbols.setdefault(theme, set()).add(key)
        self._symbol_to_themes.setdefault(key, set()).add(theme)

    def untag(self, theme: str, symbol: str, exchange: str = "*") -> bool:
        key = (symbol, exchange)
        removed = False
        if theme in self._theme_to_symbols and key in self._theme_to_symbols[theme]:
            self._theme_to_symbols[theme].discard(key)
            if not self._theme_to_symbols[theme]:
                del self._theme_to_symbols[theme]
            removed = True
        if key in self._symbol_to_themes and theme in self._symbol_to_themes[key]:
            self._symbol_to_themes[key].discard(theme)
            if not self._symbol_to_themes[key]:
                del self._symbol_to_themes[key]
        return removed

    def themes_for(self, symbol: str, exchange: str = "*") -> list[str]:
        """exchange='*' 와일드카드 + 정확 매칭 모두 합산."""
        themes: set[str] = set()
        themes.update(self._symbol_to_themes.get((symbol, exchange), set()))
        themes.update(self._symbol_to_themes.get((symbol, "*"), set()))
        return sorted(themes)

    def symbols_in(self, theme: str) -> list[tuple[str, str]]:
        return sorted(self._theme_to_symbols.get(theme, set()))

    def all_themes(self) -> list[str]:
        return sorted(self._theme_to_symbols.keys())

    def clear(self) -> None:
        self._theme_to_symbols.clear()
        self._symbol_to_themes.clear()


# ── News 이벤트 ───────────────────────────────────────────────────

NewsKind = Literal[
    "FOMC", "REGULATION", "EXCHANGE_LISTING", "HACK", "MACRO", "OTHER",
]
NewsSeverity = Literal["info", "warn", "block"]


@dataclass(frozen=True)
class NewsEvent:
    id: int
    kind: NewsKind
    headline: str
    severity: NewsSeverity = "info"
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    related_symbols: tuple[str, ...] = field(default_factory=tuple)
    source_url: str = ""

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if now < self.occurred_at:
            return False
        if self.expires_at is not None and now >= self.expires_at:
            return False
        return True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["occurred_at"] = self.occurred_at.isoformat()
        d["expires_at"] = self.expires_at.isoformat() if self.expires_at else None
        return d


class NewsRegistry:
    """시장 영향 뉴스 이벤트 메모리 레지스트리."""

    def __init__(self):
        self._next_id = 1
        self._items: dict[int, NewsEvent] = {}

    def add(
        self,
        kind: NewsKind,
        headline: str,
        severity: NewsSeverity = "info",
        occurred_at: datetime | None = None,
        expires_at: datetime | None = None,
        related_symbols: Iterable[str] | None = None,
        source_url: str = "",
    ) -> NewsEvent:
        ev = NewsEvent(
            id=self._next_id, kind=kind, headline=headline, severity=severity,
            occurred_at=(occurred_at or datetime.now(timezone.utc)),
            expires_at=expires_at,
            related_symbols=tuple(related_symbols or ()),
            source_url=source_url,
        )
        self._items[ev.id] = ev
        self._next_id += 1
        return ev

    def remove(self, event_id: int) -> bool:
        return self._items.pop(event_id, None) is not None

    def get(self, event_id: int) -> NewsEvent | None:
        return self._items.get(event_id)

    def all(self) -> list[NewsEvent]:
        return list(self._items.values())

    def active(self, now: datetime | None = None) -> list[NewsEvent]:
        return [e for e in self._items.values() if e.is_active(now)]

    def active_for(self, symbol: str, now: datetime | None = None) -> list[NewsEvent]:
        out: list[NewsEvent] = []
        for e in self._items.values():
            if not e.is_active(now):
                continue
            if not e.related_symbols:
                # 시장 전반 이벤트는 모든 심볼에 영향
                out.append(e)
            elif symbol in e.related_symbols:
                out.append(e)
        return out

    def clear(self) -> None:
        self._items.clear()
        self._next_id = 1


# ── 통합 컨텍스트 ─────────────────────────────────────────────────

@dataclass(frozen=True)
class MarketContext:
    """AgentOrchestrator.decide(context=...) 에 그대로 전달 가능한 형태."""

    symbol: str
    exchange: str
    regime: MarketRegime
    vol_band: VolBand
    themes: tuple[str, ...]
    news_severity: NewsSeverity      # 활성 뉴스 중 가장 높은 등급 (없으면 "info")
    active_news: tuple[NewsEvent, ...]
    regime_snapshot: RegimeSnapshot | None

    def to_agent_context(self) -> dict:
        """AgentOrchestrator.decide(context=...) 에 그대로 넣을 수 있는 dict."""
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "regime": self.regime,
            "vol_band": self.vol_band,
            "themes": list(self.themes),
            "news_severity": self.news_severity,
            "news_count": len(self.active_news),
        }

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "regime": self.regime,
            "vol_band": self.vol_band,
            "themes": list(self.themes),
            "news_severity": self.news_severity,
            "active_news": [e.to_dict() for e in self.active_news],
            "regime_snapshot": self.regime_snapshot.to_dict() if self.regime_snapshot else None,
        }


def _max_severity(events: Iterable[NewsEvent]) -> NewsSeverity:
    rank = {"info": 0, "warn": 1, "block": 2}
    best: NewsSeverity = "info"
    best_r = 0
    for e in events:
        r = rank.get(e.severity, 0)
        if r > best_r:
            best_r = r
            best = e.severity
    return best


def assess_market_context(
    symbol: str,
    exchange: str,
    *,
    themes: ThemeRegistry,
    news: NewsRegistry,
    closes: Sequence[float] | None = None,
    now: datetime | None = None,
) -> MarketContext:
    """심볼/거래소 단위 컨텍스트 집계.

    closes 가 주어지면 regime 분류, 없으면 UNKNOWN.
    """
    snap = classify_regime(closes) if closes else None
    regime: MarketRegime = snap.regime if snap else "UNKNOWN"
    vol_band: VolBand = snap.vol_band if snap else "UNKNOWN"

    active_news = news.active_for(symbol, now)
    severity = _max_severity(active_news)

    return MarketContext(
        symbol=symbol,
        exchange=exchange,
        regime=regime,
        vol_band=vol_band,
        themes=tuple(themes.themes_for(symbol, exchange)),
        news_severity=severity,
        active_news=tuple(active_news),
        regime_snapshot=snap,
    )
