"""체크리스트 #19 Trend/News/Theme Signals — 회귀 테스트.

검증:
  1. classify_regime — TREND_UP / TREND_DOWN / RANGE / UNKNOWN, vol_band
  2. ThemeRegistry — tag/untag/themes_for/symbols_in/와일드카드
  3. NewsRegistry — CRUD + active 시간창 + symbol 연관/시장 전반
  4. assess_market_context — Agent context dict 형식 + severity 합산
  5. AgentOrchestrator 통합 — to_agent_context() 출력이 decide(context=...)과 호환
  6. REST: 7개 엔드포인트 + admin gating
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.market.themes import (
    classify_regime, RegimeSnapshot,
    ThemeRegistry,
    NewsRegistry, NewsEvent,
    assess_market_context, MarketContext,
)


# ── 1. classify_regime ───────────────────────────────────────────

def test_regime_uptrend_detected():
    closes = [100 + i * 0.5 for i in range(30)]   # 강한 상승
    snap = classify_regime(closes)
    assert snap.regime == "TREND_UP"
    assert snap.slope_pct > 0
    assert snap.confidence > 0.0


def test_regime_downtrend_detected():
    closes = [200 - i * 0.5 for i in range(30)]
    snap = classify_regime(closes)
    assert snap.regime == "TREND_DOWN"
    assert snap.slope_pct < 0


def test_regime_range_when_flat():
    closes = [100.0 + (0.05 if i % 2 else -0.05) for i in range(30)]
    snap = classify_regime(closes)
    assert snap.regime == "RANGE"
    assert abs(snap.slope_pct) < 0.10


def test_regime_unknown_when_too_few_samples():
    snap = classify_regime([100.0] * 10)
    assert snap.regime == "UNKNOWN"
    assert snap.vol_band == "UNKNOWN"


def test_regime_unknown_when_zero_or_negative_price():
    snap = classify_regime([100, 0, 100, -1] + [100] * 30)
    assert snap.regime == "UNKNOWN"


def test_regime_high_volatility_band():
    closes = [100, 130, 80, 120, 90] * 6  # 큰 변동
    snap = classify_regime(closes)
    assert snap.vol_band == "HIGH"
    assert snap.cv_pct >= 4.0


def test_regime_low_volatility_band():
    closes = [100.0 + 0.01 * (i % 3) for i in range(30)]
    snap = classify_regime(closes)
    assert snap.vol_band == "LOW"
    assert snap.cv_pct < 0.8


def test_regime_thresholds_overridable():
    """trend_slope_pct 를 낮추면 약한 추세도 TREND 로 잡힘."""
    closes = [100.0 + i * 0.05 for i in range(30)]  # 매우 약한 상승
    base = classify_regime(closes)
    # 기본 임계값 0.10% 에서는 RANGE 일 가능성 높음
    sensitive = classify_regime(closes, trend_slope_pct=0.01)
    assert sensitive.regime == "TREND_UP"
    # base 가 TREND_UP 이 아닌 경우 sensitive 가 더 민감
    if base.regime != "TREND_UP":
        assert sensitive.regime == "TREND_UP"


# ── 2. ThemeRegistry ─────────────────────────────────────────────

def test_theme_tag_and_lookup():
    r = ThemeRegistry()
    r.tag("AI", "FET", "upbit")
    r.tag("AI", "AGIX", "upbit")
    r.tag("DeFi", "UNI", "upbit")
    assert set(r.all_themes()) == {"AI", "DeFi"}
    assert ("FET", "upbit") in r.symbols_in("AI")
    assert r.themes_for("FET", "upbit") == ["AI"]


def test_theme_symbol_can_belong_to_multiple_themes():
    r = ThemeRegistry()
    r.tag("AI", "FET", "upbit")
    r.tag("L1", "FET", "upbit")
    assert r.themes_for("FET", "upbit") == ["AI", "L1"]


def test_theme_untag():
    r = ThemeRegistry()
    r.tag("AI", "FET", "upbit")
    r.tag("AI", "AGIX", "upbit")
    assert r.untag("AI", "FET", "upbit") is True
    assert r.themes_for("FET", "upbit") == []
    # AI 테마는 다른 심볼 보유로 살아남음
    assert "AI" in r.all_themes()


def test_theme_untag_returns_false_when_missing():
    r = ThemeRegistry()
    assert r.untag("AI", "FET", "upbit") is False


def test_theme_wildcard_exchange_matches_specific():
    r = ThemeRegistry()
    r.tag("meme", "DOGE", "*")          # 전 거래소
    r.tag("meme", "PEPE", "upbit")
    assert "meme" in r.themes_for("DOGE", "upbit")  # * 매칭
    assert "meme" in r.themes_for("DOGE", "okx")    # * 매칭
    assert "meme" in r.themes_for("PEPE", "upbit")
    # PEPE on okx 는 별도로 태깅 안 했음
    assert "meme" not in r.themes_for("PEPE", "okx")


def test_theme_empty_name_raises():
    r = ThemeRegistry()
    with pytest.raises(ValueError):
        r.tag("", "BTC")


# ── 3. NewsRegistry ──────────────────────────────────────────────

def test_news_active_in_window():
    r = NewsRegistry()
    now = datetime.now(timezone.utc)
    e = r.add("FOMC", "금리 발표", severity="warn",
              occurred_at=now - timedelta(hours=1),
              expires_at=now + timedelta(hours=2))
    assert e.is_active(now) is True
    assert e.is_active(now + timedelta(hours=3)) is False


def test_news_active_for_market_wide_event_returns_to_all_symbols():
    r = NewsRegistry()
    r.add("MACRO", "BTC dominance 60%↑", related_symbols=())  # 시장 전반
    out = r.active_for("BTC")
    assert len(out) == 1
    out2 = r.active_for("XRP")
    assert len(out2) == 1


def test_news_active_for_symbol_specific():
    r = NewsRegistry()
    r.add("EXCHANGE_LISTING", "BTC 신규 상장", related_symbols=("BTC",))
    assert len(r.active_for("BTC")) == 1
    assert len(r.active_for("ETH")) == 0


def test_news_remove():
    r = NewsRegistry()
    e = r.add("OTHER", "test")
    assert r.remove(e.id) is True
    assert r.get(e.id) is None
    assert r.remove(e.id) is False


def test_news_to_dict_serializes_datetimes():
    r = NewsRegistry()
    now = datetime.now(timezone.utc)
    e = r.add("FOMC", "금리 발표", severity="warn", occurred_at=now)
    d = e.to_dict()
    assert d["occurred_at"] == now.isoformat()
    assert d["expires_at"] is None


# ── 4. assess_market_context ─────────────────────────────────────

def test_assess_context_with_no_data_returns_unknown():
    th = ThemeRegistry(); nw = NewsRegistry()
    ctx = assess_market_context("BTC", "upbit", themes=th, news=nw)
    assert ctx.regime == "UNKNOWN"
    assert ctx.vol_band == "UNKNOWN"
    assert ctx.themes == ()
    assert ctx.news_severity == "info"


def test_assess_context_aggregates_themes_and_news():
    th = ThemeRegistry()
    th.tag("AI", "FET", "upbit")
    th.tag("L1", "FET", "*")

    nw = NewsRegistry()
    nw.add("MACRO", "Fed 발언", severity="warn")
    nw.add("HACK", "FET 거래소 해킹 의혹", severity="block",
           related_symbols=("FET",))

    closes = [100 + i * 0.5 for i in range(30)]
    ctx = assess_market_context("FET", "upbit",
                                 themes=th, news=nw, closes=closes)
    assert ctx.regime == "TREND_UP"
    assert set(ctx.themes) == {"AI", "L1"}
    # severity: block > warn > info
    assert ctx.news_severity == "block"
    assert len(ctx.active_news) == 2


def test_assess_context_max_severity_is_warn_when_no_block():
    th = ThemeRegistry(); nw = NewsRegistry()
    nw.add("FOMC", "금리", severity="warn")
    nw.add("MACRO", "정상", severity="info")
    ctx = assess_market_context("BTC", "upbit", themes=th, news=nw)
    assert ctx.news_severity == "warn"


# ── 5. AgentOrchestrator 통합 ────────────────────────────────────

def test_to_agent_context_compatible_with_orchestrator():
    """AgentOrchestrator.decide(context=...) 가 사용하는 키들이 들어있는지."""
    th = ThemeRegistry(); nw = NewsRegistry()
    closes = [100 + i * 0.5 for i in range(30)]
    ctx = assess_market_context("BTC", "upbit",
                                 themes=th, news=nw, closes=closes)
    agent_ctx = ctx.to_agent_context()
    # AgentOrchestrator._calc_quality 가 ctx.get("regime") 사용
    assert agent_ctx["regime"] in {"TREND_UP", "TREND_DOWN", "RANGE", "UNKNOWN"}
    assert "vol_band" in agent_ctx
    assert isinstance(agent_ctx["themes"], list)


def test_agent_orchestrator_uses_regime_from_context():
    """to_agent_context() 결과를 그대로 넘겨도 동작."""
    from app.agents.orchestrator import AgentOrchestrator
    th = ThemeRegistry(); nw = NewsRegistry()
    closes = [100 + i * 0.5 for i in range(30)]  # TREND_UP
    ctx = assess_market_context("BTC", "upbit",
                                 themes=th, news=nw, closes=closes)
    agent_ctx = ctx.to_agent_context()
    agent_ctx["volume_surge"] = 1.5  # 추가 컨텍스트

    a = AgentOrchestrator()
    decision = a.decide(
        {"action": "BUY", "confidence": 0.9, "reason": "test"},
        context=agent_ctx,
    )
    # TREND_UP + volume_surge → quality_score 가 80 이상 → 진입 가능
    assert decision.action == "BUY"
    assert decision.quality_score >= 80


# ── 6. REST API ──────────────────────────────────────────────────

@pytest.fixture
def app_with_clean_themes():
    from app.main import app
    from app.api.deps import get_themes, get_news

    th = ThemeRegistry()
    nw = NewsRegistry()
    app.dependency_overrides[get_themes] = lambda: th
    app.dependency_overrides[get_news]   = lambda: nw
    yield app, th, nw
    app.dependency_overrides.pop(get_themes, None)
    app.dependency_overrides.pop(get_news, None)


def test_api_get_market_context(app_with_clean_themes):
    app, th, nw = app_with_clean_themes
    th.tag("AI", "FET", "upbit")
    nw.add("FOMC", "금리", severity="warn")
    client = TestClient(app)
    r = client.get("/api/market/context/upbit/FET")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "FET"
    assert "AI" in body["themes"]
    assert body["news_severity"] == "warn"


def test_api_themes_post_requires_admin(app_with_clean_themes):
    app, _, _ = app_with_clean_themes
    client = TestClient(app)
    r = client.post("/api/themes/tag",
                    json={"theme": "AI", "symbol": "FET"})
    assert r.status_code == 401


def test_api_themes_full_flow(app_with_clean_themes):
    from app.core.config import get_settings
    app, _, _ = app_with_clean_themes
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app)

    # 태깅
    r = client.post("/api/themes/tag", headers=H,
                    json={"theme": "AI", "symbol": "FET", "exchange": "upbit"})
    assert r.status_code == 201
    assert "AI" in r.json()["themes_for_symbol"]

    # 빈 theme → 400
    r2 = client.post("/api/themes/tag", headers=H,
                     json={"theme": "  ", "symbol": "X"})
    assert r2.status_code == 400

    # 목록
    r3 = client.get("/api/themes")
    assert any(t["name"] == "AI" for t in r3.json()["themes"])

    # 태그 제거
    r4 = client.delete("/api/themes/tag/AI/FET?exchange=upbit", headers=H)
    assert r4.status_code == 204

    # 다시 제거 → 404
    r5 = client.delete("/api/themes/tag/AI/FET?exchange=upbit", headers=H)
    assert r5.status_code == 404


def test_api_news_full_flow(app_with_clean_themes):
    from app.core.config import get_settings
    app, _, _ = app_with_clean_themes
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app)

    # 추가
    r = client.post("/api/news", headers=H, json={
        "kind": "FOMC", "headline": "금리 동결",
        "severity": "warn", "related_symbols": ["BTC", "ETH"],
    })
    assert r.status_code == 201
    nid = r.json()["id"]

    # 목록
    r2 = client.get("/api/news")
    assert r2.json()["count"] == 1

    # 심볼 필터 — BTC는 매칭, XRP는 미매칭
    assert client.get("/api/news?symbol=BTC").json()["count"] == 1
    assert client.get("/api/news?symbol=XRP").json()["count"] == 0

    # 삭제
    r3 = client.delete(f"/api/news/{nid}", headers=H)
    assert r3.status_code == 204

    # 다시 삭제 → 404
    r4 = client.delete(f"/api/news/{nid}", headers=H)
    assert r4.status_code == 404


def test_api_news_post_requires_admin(app_with_clean_themes):
    app, _, _ = app_with_clean_themes
    client = TestClient(app)
    r = client.post("/api/news",
                    json={"kind": "FOMC", "headline": "test"})
    assert r.status_code == 401
