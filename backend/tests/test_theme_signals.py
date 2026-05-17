"""체크리스트 #19 Trend/News/Theme Signals — 회귀 테스트.

검증:
  1. MockThemeProvider 결정론 + multi-source 커버
  2. normalize_signal — title 빈/소스 비정상 처리
  3. infer_risk_flags — regulatory / exchange_risk / delisting / hype / macro_fx
  4. used_for_order / direct_order_allowed 영구 False
  5. (source, provider, signal_id) dedup → update
  6. (source, provider, content_hash) dedup → update
  7. ThemeContextBuilder — direct_order_allowed=False, action 토큰 부재
  8. ThemeFilter — review_required vs ok 라벨링
  9. REST: collect (admin gating) / list / context / sources / filter
 10. BUY/SELL/ENTER/EXIT 직접 반환 금지 — payload 정적 검증
 11. broker/execution 모듈 미import
 12. ORM UNIQUE 제약 + used_for_order 기본 False 영속
"""
from __future__ import annotations
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, ThemeSignal
from app.market.theme_signals import (
    SOURCES, ALLOWED_RISK_FLAGS, FORBIDDEN_ACTION_TOKENS,
    RawThemeSignal, MockThemeProvider, ThemeSignalCollector,
    infer_risk_flags, normalize_signal, compute_content_hash,
    list_theme_signals, signal_to_dict,
)
from app.market.theme_context import (
    ThemeContextBuilder, ThemeFilter, summarize_for_agent,
    _assert_no_action_tokens,
)


# ── 픽스처 ────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture
def session(session_factory):
    with session_factory() as s:
        yield s


def _now() -> datetime:
    return datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)


# ── 1. MockThemeProvider 결정론 ──────────────────────────────────

def test_mock_provider_is_deterministic():
    p = MockThemeProvider()
    a = p.fetch_signals()
    b = p.fetch_signals()
    assert [s.title for s in a] == [s.title for s in b]
    assert len(a) >= 8


def test_mock_provider_covers_multi_source():
    sources = {s.source for s in MockThemeProvider().fetch_signals()}
    assert "trend" in sources
    assert "news" in sources
    assert "disclosure" in sources
    assert "theme" in sources
    assert "macro_fx" in sources


def test_mock_provider_since_filter():
    cutoff = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    out = MockThemeProvider().fetch_signals(since=cutoff)
    for s in out:
        assert s.published_at is None or s.published_at >= cutoff


# ── 2. normalize_signal ───────────────────────────────────────────

def test_normalize_rejects_empty_title():
    with pytest.raises(ValueError):
        normalize_signal(RawThemeSignal(source="trend", provider="p", title="  "))


def test_normalize_falls_back_to_other_for_unknown_source():
    n = normalize_signal(RawThemeSignal(source="WEIRD", provider="p", title="x"))
    assert n.source == "other"


def test_normalize_uppercases_symbols():
    n = normalize_signal(RawThemeSignal(
        source="trend", provider="p", title="t",
        related_symbols=("btc", "eth"),
    ))
    assert "BTC" in n.related_symbols
    assert "ETH" in n.related_symbols
    assert "btc" not in n.related_symbols


def test_normalize_clips_score_and_sentiment():
    n = normalize_signal(RawThemeSignal(
        source="trend", provider="p", title="t", score=2.0, sentiment=-3.0,
    ))
    assert n.score == 1.0
    assert n.sentiment == -1.0


def test_normalize_truncates_long_summary():
    n = normalize_signal(RawThemeSignal(
        source="news", provider="p", title="t", summary="x" * 5000,
    ))
    assert len(n.summary) <= 4096


# ── 3. infer_risk_flags ──────────────────────────────────────────

def test_risk_flag_regulatory():
    flags = infer_risk_flags(title="SEC investigation", summary="")
    assert "regulatory_attention" in flags


def test_risk_flag_exchange_risk():
    flags = infer_risk_flags(title="exchange withdrawal suspension reported", summary="")
    assert "exchange_risk_attention" in flags


def test_risk_flag_delisting():
    flags = infer_risk_flags(title="delisting notice", summary="")
    assert "delisting_related_theme" in flags


def test_risk_flag_suspicious_hype():
    flags = infer_risk_flags(title="rug pulled influencer hype", summary="")
    assert "suspicious_hype_theme" in flags


def test_risk_flag_macro_fx():
    flags = infer_risk_flags(title="FOMC decision", summary="")
    assert "macro_fx_attention" in flags


def test_risk_flag_default_context_only():
    flags = infer_risk_flags(title="단순 무관 안내", summary="")
    assert flags == ["context_only"]


def test_risk_flag_negative_sentiment_review_required():
    flags = infer_risk_flags(title="중립 제목", summary="", sentiment=-0.8)
    assert "review_required" in flags


def test_risk_flag_no_action_tokens_ever():
    """어떤 입력에서도 BUY/SELL/ENTER/EXIT 토큰이 risk_flags 에 등장하지 않는다."""
    samples = [
        ("BUY signal", -0.5), ("SELL recommendation", 0.5),
        ("ENTER position", 0.0), ("EXIT now", 0.0),
    ]
    for title, sent in samples:
        flags = infer_risk_flags(title=title, summary="", sentiment=sent)
        for f in flags:
            assert f.upper() not in FORBIDDEN_ACTION_TOKENS, f"{f} from {title!r}"
            assert f in ALLOWED_RISK_FLAGS


# ── 4. used_for_order / direct_order_allowed 영구 False ─────────

def test_collect_used_for_order_is_false(session):
    c = ThemeSignalCollector({"mock": MockThemeProvider()})
    c.collect_once(session, provider_name="mock", now=_now())
    session.commit()
    for r in session.execute(select(ThemeSignal)).scalars().all():
        assert r.used_for_order is False
        assert r.direct_order_allowed is False


def test_signal_to_dict_includes_false_flags():
    n = ThemeSignal(
        source="news", provider="mock", title="t",
        related_symbols=[], related_keywords=[], risk_flags=[],
        collected_at=_now(), updated_at=_now(), raw_payload={},
        content_hash="h", used_for_order=False, direct_order_allowed=False,
    )
    d = signal_to_dict(n)
    assert d["used_for_order"] is False
    assert d["direct_order_allowed"] is False
    # action 컬럼이 응답에 없어야 함
    assert "action" not in d
    assert "side" not in d


# ── 5. signal_id dedup ──────────────────────────────────────────

def test_collect_dedup_by_signal_id(session):
    c = ThemeSignalCollector({"mock": MockThemeProvider()})
    r1 = c.collect_once(session, provider_name="mock", now=_now())
    session.commit()
    r2 = c.collect_once(session, provider_name="mock", now=_now() + timedelta(minutes=1))
    session.commit()
    rows = session.execute(select(ThemeSignal)).scalars().all()
    assert len(rows) == r1.inserted
    assert r2.inserted == 0
    assert r2.updated >= 1


def test_collect_updates_existing(session):
    c = ThemeSignalCollector({"mock": MockThemeProvider()})
    c.collect_once(session, provider_name="mock", now=_now())
    session.commit()
    new_provider = MockThemeProvider(fixtures=[
        RawThemeSignal(
            source="trend", provider="mock_trend",
            signal_id="mock-trend-001",
            title="BTC ETF 관심도 갱신 (Mock fixture v2)",
            theme="ETF",
            related_symbols=("BTC",),
            score=0.9,
            published_at=_now(),
        ),
    ])
    c.add_provider("mock", new_provider)
    c.collect_once(session, provider_name="mock", now=_now())
    session.commit()
    row = session.execute(
        select(ThemeSignal).where(
            ThemeSignal.signal_id == "mock-trend-001",
        )
    ).scalar_one()
    assert "v2" in row.title
    assert row.used_for_order is False


# ── 6. content_hash dedup ──────────────────────────────────────

def test_collect_dedup_by_content_hash(session):
    p = MockThemeProvider(fixtures=[
        RawThemeSignal(source="theme", provider="mock_theme",
                       title="AI 관련 (Mock fixture)", summary="동일 본문"),
        RawThemeSignal(source="theme", provider="mock_theme",
                       title="AI 관련 (Mock fixture)", summary="동일 본문"),
    ])
    c = ThemeSignalCollector({"mock": p})
    r = c.collect_once(session, provider_name="mock", now=_now())
    session.commit()
    rows = session.execute(select(ThemeSignal)).scalars().all()
    assert len(rows) == 1
    assert r.inserted + r.updated == 2


def test_content_hash_stable():
    h1 = compute_content_hash("News", "MockProv", "Title  ", "body")
    h2 = compute_content_hash("news", "mockprov", "title", "body")
    assert h1 == h2


# ── 7. ThemeContextBuilder ──────────────────────────────────────

def test_context_empty(session):
    ctx = ThemeContextBuilder(session).build_theme_context(
        symbols=["BTC"], lookback_hours=72, now=_now(),
    )
    assert ctx.total_signals == 0
    assert ctx.direct_order_allowed is False
    assert ctx.used_for_order is False


def test_context_after_collect_has_summary(session):
    c = ThemeSignalCollector({"mock": MockThemeProvider()})
    c.collect_once(session, provider_name="mock", now=_now())
    session.commit()
    ctx = ThemeContextBuilder(session).build_theme_context(
        lookback_hours=72, now=_now() + timedelta(hours=1),
    )
    assert ctx.total_signals > 0
    assert ctx.direct_order_allowed is False
    assert "trend" in ctx.by_source
    # delisting/regulatory 등 review-triggering 플래그 발견
    assert any(
        s.recommendation == "candidate_filter_review_required"
        for s in ctx.symbol_summaries
    )
    # 사람이 읽는 문장에 "직접 매매 신호가 아닙니다" 명시
    assert "직접 매매" in ctx.human_summary or "후보 필터" in ctx.human_summary


def test_context_no_action_tokens(session):
    c = ThemeSignalCollector({"mock": MockThemeProvider()})
    c.collect_once(session, provider_name="mock", now=_now())
    session.commit()
    ctx_dict = ThemeContextBuilder(session).build_theme_context(
        lookback_hours=72, now=_now() + timedelta(hours=1),
    ).to_dict()
    # action key 자체가 응답 어디에도 없다 (recent_titles / risk_notes 의 문자열은 예외).
    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                kp = f"{path}.{k}" if path else k
                if k.lower() in {"action", "side"}:
                    assert v is None or v == "", f"action present at {kp}: {v}"
                walk(v, kp)
        elif isinstance(node, list):
            for i, x in enumerate(node):
                walk(x, f"{path}[{i}]")
    walk(ctx_dict)
    # 내장 가드도 통과
    _assert_no_action_tokens(ctx_dict)


def test_context_symbol_filter(session):
    c = ThemeSignalCollector({"mock": MockThemeProvider()})
    c.collect_once(session, provider_name="mock", now=_now())
    session.commit()
    ctx = ThemeContextBuilder(session).build_theme_context(
        symbols=["LUNA"], lookback_hours=72, now=_now() + timedelta(hours=1),
    )
    syms = {s.symbol: s for s in ctx.symbol_summaries}
    assert "LUNA" in syms
    assert "delisting_related_theme" in syms["LUNA"].risk_flags
    assert syms["LUNA"].recommendation == "candidate_filter_review_required"
    assert syms["LUNA"].direct_order_allowed is False
    assert syms["LUNA"].used_for_order is False


# ── 8. ThemeFilter ──────────────────────────────────────────────

def test_filter_annotates_candidates_with_review(session):
    c = ThemeSignalCollector({"mock": MockThemeProvider()})
    c.collect_once(session, provider_name="mock", now=_now())
    session.commit()
    out = ThemeFilter(session).annotate_candidates(
        [("LUNA", "upbit"), ("BTC", "upbit"), ("ZZZ", "upbit")],
        lookback_hours=72, now=_now() + timedelta(hours=1),
    )
    by_sym = {e.symbol: e for e in out}
    assert by_sym["LUNA"].recommendation == "candidate_filter_review_required"
    # ZZZ 는 관련 신호 없음 → ok
    assert by_sym["ZZZ"].recommendation == "candidate_filter_ok"
    for e in out:
        assert e.used_for_order is False
        assert e.direct_order_allowed is False
        d = e.to_dict()
        assert "action" not in d
        assert "side" not in d


# ── 9. REST API ─────────────────────────────────────────────────

@pytest.fixture
def api_client(session_factory):
    from app.main import app
    from app.api.deps import get_db, get_theme_signal_collector

    def _override_db():
        with session_factory() as s:
            try:
                yield s
            finally:
                pass

    fresh = ThemeSignalCollector({"mock": MockThemeProvider()})

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_theme_signal_collector] = lambda: fresh
    yield TestClient(app), fresh
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_theme_signal_collector, None)


def test_api_collect_requires_admin(api_client):
    client, _ = api_client
    r = client.post("/api/theme-signals/collect", json={"provider": "mock"})
    assert r.status_code == 401


def test_api_collect_with_admin_succeeds(api_client):
    from app.core.config import get_settings
    client, _ = api_client
    token = get_settings().admin_token
    r = client.post(
        "/api/theme-signals/collect",
        json={"provider": "mock", "since_hours": 240},
        headers={"X-Admin-Token": token},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["fetched"] >= 7
    assert body["inserted"] >= 7
    assert body["used_for_order"] is False
    assert body["direct_order_allowed"] is False
    assert "trend" in body["by_source"]


def test_api_collect_rejects_unknown_provider(api_client):
    from app.core.config import get_settings
    client, _ = api_client
    token = get_settings().admin_token
    r = client.post(
        "/api/theme-signals/collect",
        json={"provider": "nope"},
        headers={"X-Admin-Token": token},
    )
    assert r.status_code == 400


def test_api_list_theme_signals(api_client):
    from app.core.config import get_settings
    client, _ = api_client
    token = get_settings().admin_token
    client.post("/api/theme-signals/collect", json={"provider": "mock"},
                headers={"X-Admin-Token": token})
    r = client.get("/api/theme-signals?source=news")
    assert r.status_code == 200
    body = r.json()
    assert all(s["source"] == "news" for s in body["signals"])
    assert body["used_for_order"] is False
    assert body["direct_order_allowed"] is False


def test_api_context_endpoint(api_client):
    from app.core.config import get_settings
    client, _ = api_client
    token = get_settings().admin_token
    client.post("/api/theme-signals/collect", json={"provider": "mock"},
                headers={"X-Admin-Token": token})
    r = client.get("/api/theme-signals/context?lookback_hours=72")
    assert r.status_code == 200
    body = r.json()
    assert body["total_signals"] > 0
    assert body["used_for_order"] is False
    assert body["direct_order_allowed"] is False
    # 응답에 action key 부재
    for s in body["symbol_summaries"]:
        assert "action" not in s
        assert "side" not in s


def test_api_sources_catalog(api_client):
    client, _ = api_client
    r = client.get("/api/theme-signals/sources")
    assert r.status_code == 200
    body = r.json()
    assert "news" in body["sources"]
    assert "review_required" in body["risk_flags"]
    assert body["used_for_order"] is False
    assert body["direct_order_allowed"] is False


def test_api_filter_endpoint(api_client):
    from app.core.config import get_settings
    client, _ = api_client
    token = get_settings().admin_token
    client.post("/api/theme-signals/collect", json={"provider": "mock"},
                headers={"X-Admin-Token": token})
    r = client.post("/api/theme-signals/filter", json={
        "candidates": [
            {"symbol": "LUNA", "exchange": "upbit"},
            {"symbol": "BTC",  "exchange": "upbit"},
            {"symbol": "ZZZ",  "exchange": "upbit"},
        ],
        "lookback_hours": 72,
    })
    assert r.status_code == 200
    body = r.json()
    recs = {c["symbol"]: c["recommendation"] for c in body["candidates"]}
    assert recs["LUNA"] == "candidate_filter_review_required"
    assert recs["ZZZ"] == "candidate_filter_ok"
    for c in body["candidates"]:
        assert "action" not in c
        assert "side" not in c


def test_api_filter_rejects_empty_candidates(api_client):
    client, _ = api_client
    r = client.post("/api/theme-signals/filter", json={"candidates": []})
    assert r.status_code == 400


# ── 10. action 토큰 정적 가드 ────────────────────────────────────

def test_payload_no_buy_sell_action(session):
    c = ThemeSignalCollector({"mock": MockThemeProvider()})
    c.collect_once(session, provider_name="mock", now=_now())
    session.commit()
    rows = session.execute(select(ThemeSignal)).scalars().all()
    for r in rows:
        # risk_flags 화이트리스트 강제
        for f in (r.risk_flags or []):
            assert f in ALLOWED_RISK_FLAGS, f"unknown flag {f!r}"
            assert f.upper() not in FORBIDDEN_ACTION_TOKENS


def test_summarize_for_agent_no_action(session):
    c = ThemeSignalCollector({"mock": MockThemeProvider()})
    c.collect_once(session, provider_name="mock", now=_now())
    session.commit()
    rows = session.execute(select(ThemeSignal)).scalars().all()
    d = summarize_for_agent(rows)
    assert d["used_for_order"] is False
    assert d["direct_order_allowed"] is False
    assert "action" not in d


# ── 11. 모듈 import / 금지 문자열 정적 검증 ─────────────────────

def test_theme_signal_modules_dont_import_broker_or_execution():
    root = Path(__file__).resolve().parent.parent / "app" / "market"
    forbidden = re.compile(
        r"^\s*(?:from\s+app\.(?:brokers|execution)|import\s+app\.(?:brokers|execution))",
        re.M,
    )
    for fname in ("theme_signals.py", "theme_context.py"):
        text = (root / fname).read_text(encoding="utf-8")
        assert not forbidden.search(text), f"{fname} imports broker/execution"


def test_theme_signal_modules_no_forbidden_substrings():
    root = Path(__file__).resolve().parent.parent / "app" / "market"
    forbidden = (
        "ENABLE_LIVE_TRADING = True",
        "ENABLE_AI_EXECUTION = True",
        "ENABLE_CRYPTO_FUTURES_LIVE = True",
        "place_order(",
        "cancel_order(",
        "get_balance(",
    )
    for fname in ("theme_signals.py", "theme_context.py"):
        text = (root / fname).read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{fname} contains {needle!r}"


def test_theme_modules_no_action_return_tokens_in_constants():
    """ALLOWED_RISK_FLAGS / 그 외 production 상수에 action 토큰이 없는지."""
    for f in ALLOWED_RISK_FLAGS:
        assert f.upper() not in FORBIDDEN_ACTION_TOKENS


# ── 12. ORM 제약 ────────────────────────────────────────────────

def test_unique_signal_id(session):
    a = ThemeSignal(
        source="trend", provider="p", signal_id="dup-1", title="t",
        related_symbols=[], related_keywords=[], risk_flags=[],
        collected_at=_now(), updated_at=_now(), raw_payload={},
        content_hash="ha",
    )
    session.add(a)
    session.commit()
    b = ThemeSignal(
        source="trend", provider="p", signal_id="dup-1", title="t2",
        related_symbols=[], related_keywords=[], risk_flags=[],
        collected_at=_now(), updated_at=_now(), raw_payload={},
        content_hash="hb",
    )
    session.add(b)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_used_for_order_default_false_on_persist(session):
    n = ThemeSignal(
        source="theme", provider="p", title="t",
        related_symbols=[], related_keywords=[], risk_flags=[],
        collected_at=_now(), updated_at=_now(), raw_payload={},
        content_hash="hu",
    )
    session.add(n)
    session.commit()
    fetched = session.execute(select(ThemeSignal).where(ThemeSignal.id == n.id)).scalar_one()
    assert fetched.used_for_order is False
    assert fetched.direct_order_allowed is False


# ── 13. 카탈로그 ────────────────────────────────────────────────

def test_sources_catalog():
    assert "trend" in SOURCES
    assert "news" in SOURCES
    assert "disclosure" in SOURCES
    assert "theme" in SOURCES
    assert "macro_fx" in SOURCES


def test_risk_flags_catalog():
    assert "review_required" in ALLOWED_RISK_FLAGS
    assert "context_only" in ALLOWED_RISK_FLAGS
    # action 토큰이 카탈로그에 들어있지 않다
    for f in ALLOWED_RISK_FLAGS:
        assert f.upper() not in FORBIDDEN_ACTION_TOKENS
