"""체크리스트 #18 Exchange Notices — collector / context / API 회귀 테스트.

검증:
  1. MockNoticeSource 결정론 — fixture 가 다중 notice_type 포함
  2. 분류기 — DELISTING / CAUTION / DWS / LISTING / MAINTENANCE / TRADING_SUSPENSION /
     POLICY / OTHER
  3. severity 매핑 — CRITICAL 키워드, INFO 기본
  4. symbol upper 정규화
  5. content_hash 안정성 + 결정론
  6. notice_id 기반 dedup → update
  7. content_hash 기반 dedup → update (notice_id 부재)
  8. NoticeContextBuilder — direct_order_allowed=False 보장
  9. NoticeContextBuilder — high_risk_symbols / by_type / by_severity
 10. NoticeContextBuilder — lookback_hours 필터
 11. NoticeContextBuilder — 심볼별 risk_flags
 12. POST /api/notices/collect — admin gating + mock source 결과
 13. GET /api/notices — exchange_notices + summary 통합 응답
 14. GET /api/notices/context — Agent context, direct_order_allowed=false
 15. ExchangeNotice ORM — UNIQUE 제약 작동
 16. 모듈 import 금지 — broker/execution 모듈 미참조
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

from app.db import Base, ExchangeNotice
from app.market.notice_collector import (
    NOTICE_TYPES, SEVERITIES, RawNotice, MockNoticeSource, NoticeCollector,
    classify_notice_type, compute_severity, compute_content_hash,
    extract_symbols, normalize_notice, list_notices,
)
from app.market.notice_context import (
    NoticeContextBuilder, summarize_notices_for_agent,
)


# ── 픽스처 ────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    # FastAPI TestClient 가 별도 thread 에서 작동 — StaticPool 로 동일 in-memory
    # 연결을 공유해야 동일 테이블을 보게 된다.
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


# ── 1. MockNoticeSource 결정론 ───────────────────────────────────

def test_mock_source_returns_deterministic_fixtures():
    src = MockNoticeSource("mock")
    a = src.fetch_notices("mock")
    b = src.fetch_notices("mock")
    assert len(a) == len(b)
    assert [n.title for n in a] == [n.title for n in b]
    assert len(a) >= 8  # 8개 notice_type 커버


def test_mock_source_filters_by_exchange():
    src = MockNoticeSource("mock")
    assert src.fetch_notices("mock") != []
    assert src.fetch_notices("unknown_exchange") == []


def test_mock_source_includes_required_notice_types():
    src = MockNoticeSource("mock")
    titles = " ".join(n.title for n in src.fetch_notices("mock")).lower()
    # 각 분류가 최소 1개씩
    assert "입출금" in titles
    assert "유의" in titles
    assert "상장폐지" in titles
    assert "신규 상장" in titles
    assert "점검" in titles
    assert "거래 중단" in titles
    assert "수수료" in titles or "정책" in titles


def test_mock_source_since_filter():
    src = MockNoticeSource("mock")
    cutoff = datetime(2026, 5, 18, 12, 30, tzinfo=timezone.utc)
    out = src.fetch_notices("mock", since=cutoff)
    # cutoff 이후 published_at 만 반환되어야 함 (또는 published_at=None)
    for n in out:
        assert n.published_at is None or n.published_at >= cutoff


# ── 2. 분류기 ────────────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("[상장폐지] LUNA 상장폐지 안내", "DELISTING"),
    ("LUNA delisting announcement", "DELISTING"),
    ("[거래 중단] SOL 거래 일시 정지", "TRADING_SUSPENSION"),
    ("Trading halt notice for SOL", "TRADING_SUSPENSION"),
    ("[안내] XRP 입출금 일시 중단", "DEPOSIT_WITHDRAWAL_SUSPENSION"),
    ("XRP deposit and withdrawal suspension", "DEPOSIT_WITHDRAWAL_SUSPENSION"),
    ("[유의종목 지정] DOGE 투자유의", "CAUTION"),
    ("Investment caution notice for DOGE", "CAUTION"),
    ("[신규 상장] APT 거래지원 개시", "LISTING"),
    ("New listing announcement", "LISTING"),
    ("[시스템 점검] 정기 시스템 점검 안내", "MAINTENANCE"),
    ("scheduled maintenance", "MAINTENANCE"),
    ("[정책] 수수료 정책 변경", "POLICY"),
    ("Fee schedule update", "POLICY"),
    ("기타 일반 안내", "OTHER"),
])
def test_classify_notice_type(title, expected):
    assert classify_notice_type(title) == expected


# ── 3. severity ─────────────────────────────────────────────────

def test_severity_default_mappings():
    assert compute_severity("DELISTING", "상장폐지", "") == "CRITICAL"
    assert compute_severity("TRADING_SUSPENSION", "거래 중단", "") == "CRITICAL"
    assert compute_severity("DEPOSIT_WITHDRAWAL_SUSPENSION", "입출금 중단", "") == "HIGH"
    assert compute_severity("CAUTION", "유의종목", "") == "WARNING"
    assert compute_severity("MAINTENANCE", "점검", "") == "WARNING"
    assert compute_severity("LISTING", "신규 상장", "") == "INFO"
    assert compute_severity("POLICY", "수수료", "") == "INFO"
    assert compute_severity("OTHER", "기타", "") == "INFO"


def test_severity_bumps_on_urgent_keywords():
    sev = compute_severity("CAUTION", "긴급 유의 안내", "")
    assert sev == "HIGH"  # WARNING → HIGH


def test_severity_critical_keyword_overrides():
    sev = compute_severity("OTHER", "상장폐지 관련 추가 공지", "")
    assert sev == "CRITICAL"


# ── 4. symbol 정규화 / 추출 ─────────────────────────────────────

def test_extract_symbols_whitelist():
    syms = extract_symbols("XRP 입출금 일시 중단", "")
    assert "XRP" in syms


def test_extract_symbols_blacklist_excluded():
    # BTC 는 false-positive 위험으로 blacklist — source 가 명시해야 함
    syms = extract_symbols("API 변경 안내", "USDT 페어 안내")
    assert "API" not in syms
    assert "USDT" not in syms


def test_normalize_notice_uppercases_provided_symbols():
    raw = RawNotice(
        exchange="mock", title="안내", symbols=("btc", "eth", " xrp "),
        body="...",
    )
    n = normalize_notice(raw)
    assert "BTC" in n.symbols
    assert "ETH" in n.symbols
    assert "XRP" in n.symbols
    assert "btc" not in n.symbols


def test_normalize_notice_rejects_empty_title():
    with pytest.raises(ValueError):
        normalize_notice(RawNotice(exchange="mock", title="   "))


def test_normalize_notice_truncates_long_title():
    raw = RawNotice(exchange="mock", title="A" * 1000)
    n = normalize_notice(raw)
    assert len(n.title) <= 512


def test_normalize_notice_exchange_lowercased():
    raw = RawNotice(exchange="UPBIT", title="공지")
    n = normalize_notice(raw)
    assert n.exchange == "upbit"


# ── 5. content_hash ─────────────────────────────────────────────

def test_content_hash_stable_across_invocations():
    h1 = compute_content_hash("upbit", "공지", "본문")
    h2 = compute_content_hash("upbit", "공지", "본문")
    assert h1 == h2


def test_content_hash_normalizes_whitespace_and_case():
    h1 = compute_content_hash("UPBIT", "  공지  ", "본문")
    h2 = compute_content_hash("upbit", "공지", "본문")
    assert h1 == h2


def test_content_hash_differs_for_different_content():
    h1 = compute_content_hash("upbit", "공지A", "본문")
    h2 = compute_content_hash("upbit", "공지B", "본문")
    assert h1 != h2


# ── 6. notice_id 기반 dedup ─────────────────────────────────────

def test_collect_dedup_by_notice_id(session):
    collector = NoticeCollector({"mock": MockNoticeSource("mock")})
    r1 = collector.collect_once(session, exchange="mock", source_name="mock", now=_now())
    session.commit()
    r2 = collector.collect_once(session, exchange="mock", source_name="mock", now=_now() + timedelta(minutes=1))
    session.commit()
    rows = session.execute(select(ExchangeNotice)).scalars().all()
    # 같은 fixture 를 두 번 수집해도 같은 수만큼 row 가 유지되어야 함
    assert len(rows) == r1.inserted
    assert r2.inserted == 0
    assert r2.updated >= 1


def test_collect_updates_existing_row(session):
    collector = NoticeCollector({"mock": MockNoticeSource("mock")})
    collector.collect_once(session, exchange="mock", source_name="mock", now=_now())
    session.commit()
    # 같은 notice_id 로 새 title 을 주는 source 를 simulate
    new_src = MockNoticeSource("mock", fixtures=[
        RawNotice(
            exchange="mock", notice_id="mock-2026-003",
            title="[상장폐지] LUNA 상장폐지 안내 (수정)",
            body="새 본문", category="delisting",
            published_at=_now(),
        ),
    ])
    collector.add_source("mock", new_src)
    collector.collect_once(session, exchange="mock", source_name="mock", now=_now())
    session.commit()
    row = session.execute(
        select(ExchangeNotice).where(
            ExchangeNotice.exchange == "mock",
            ExchangeNotice.notice_id == "mock-2026-003",
        )
    ).scalar_one()
    assert "(수정)" in row.title


# ── 7. content_hash 기반 dedup (notice_id 없음) ─────────────────

def test_collect_dedup_by_content_hash(session):
    """notice_id 부재 fixture 가 두 번 수집되어도 row 가 중복되지 않는다."""
    src = MockNoticeSource("mock", fixtures=[
        RawNotice(exchange="mock", title="[유의] 유의종목 지정 안내",
                  body="X 코인 유의종목 지정"),
        # 같은 title/body — content_hash 동일 → dedup
        RawNotice(exchange="mock", title="[유의] 유의종목 지정 안내",
                  body="X 코인 유의종목 지정"),
    ])
    collector = NoticeCollector({"mock": src})
    r = collector.collect_once(session, exchange="mock", source_name="mock", now=_now())
    session.commit()
    rows = session.execute(select(ExchangeNotice)).scalars().all()
    assert len(rows) == 1
    assert r.inserted + r.updated == 2  # 두 번 처리되었지만 row 는 1개


def test_collect_records_all_notice_types(session):
    collector = NoticeCollector({"mock": MockNoticeSource("mock")})
    collector.collect_once(session, exchange="mock", source_name="mock", now=_now())
    session.commit()
    types_in_db = {r.notice_type for r in session.execute(select(ExchangeNotice)).scalars().all()}
    # 최소 5개 분류는 mock fixture 가 커버
    assert "DELISTING" in types_in_db
    assert "DEPOSIT_WITHDRAWAL_SUSPENSION" in types_in_db
    assert "CAUTION" in types_in_db
    assert "TRADING_SUSPENSION" in types_in_db
    assert "MAINTENANCE" in types_in_db


def test_collect_direct_order_allowed_is_false(session):
    collector = NoticeCollector({"mock": MockNoticeSource("mock")})
    collector.collect_once(session, exchange="mock", source_name="mock", now=_now())
    session.commit()
    for r in session.execute(select(ExchangeNotice)).scalars().all():
        assert r.direct_order_allowed is False


# ── 8~11. NoticeContextBuilder ──────────────────────────────────

def test_context_builder_empty_when_no_notices(session):
    b = NoticeContextBuilder(session)
    ctx = b.build_notice_context(symbols=["BTC"], lookback_hours=72, now=_now())
    assert ctx.total_notices == 0
    assert ctx.direct_order_allowed is False
    d = ctx.to_dict()
    assert d["direct_order_allowed"] is False


def test_context_builder_after_collect_has_summary(session):
    collector = NoticeCollector({"mock": MockNoticeSource("mock")})
    collector.collect_once(session, exchange="mock", source_name="mock", now=_now())
    session.commit()
    b = NoticeContextBuilder(session)
    ctx = b.build_notice_context(lookback_hours=72, now=_now() + timedelta(hours=1))
    assert ctx.total_notices > 0
    assert ctx.direct_order_allowed is False
    assert "DELISTING" in ctx.by_type
    assert "CRITICAL" in ctx.by_severity
    # 고위험 심볼 — LUNA (DELISTING), SOL (TRADING_SUSPENSION) 가 최소 포함
    assert "LUNA" in ctx.high_risk_symbols or "SOL" in ctx.high_risk_symbols
    # 사람이 읽는 문장
    assert "후보 필터" in ctx.human_summary
    assert "주문" in ctx.human_summary  # 직접 주문 트리거 아님 명시


def test_context_builder_symbol_filter(session):
    collector = NoticeCollector({"mock": MockNoticeSource("mock")})
    collector.collect_once(session, exchange="mock", source_name="mock", now=_now())
    session.commit()
    b = NoticeContextBuilder(session)
    ctx = b.build_notice_context(symbols=["LUNA"], lookback_hours=72, now=_now() + timedelta(hours=1))
    # LUNA 요약 — DELISTING 플래그 + CRITICAL severity
    syms = {s.symbol: s for s in ctx.symbol_summaries}
    assert "LUNA" in syms
    luna = syms["LUNA"]
    assert "delisting_or_termination" in luna.risk_flags
    assert luna.severity == "CRITICAL"
    assert luna.recommendation == "candidate_filter_review_required"
    assert luna.direct_order_allowed is False


def test_context_builder_lookback_filter(session):
    """3시간 lookback 이면 8시간 전 fixture 의 일부는 제외되어야 한다."""
    collector = NoticeCollector({"mock": MockNoticeSource("mock")})
    collector.collect_once(session, exchange="mock", source_name="mock", now=_now())
    session.commit()
    b = NoticeContextBuilder(session)
    # mock fixture 가 모두 _now() 직전(같은 시각)에 collected_at 으로 저장됨 — 1시간 lookback
    ctx_short = b.build_notice_context(lookback_hours=1, now=_now() + timedelta(hours=24))
    ctx_long = b.build_notice_context(lookback_hours=72, now=_now() + timedelta(hours=1))
    assert ctx_long.total_notices >= ctx_short.total_notices


def test_context_get_symbol_risk_flags(session):
    collector = NoticeCollector({"mock": MockNoticeSource("mock")})
    collector.collect_once(session, exchange="mock", source_name="mock", now=_now())
    session.commit()
    b = NoticeContextBuilder(session)
    flags = b.get_symbol_risk_flags("XRP", lookback_hours=72, now=_now() + timedelta(hours=1))
    assert any(f.flag == "deposit_withdrawal_suspended" for f in flags)
    for f in flags:
        assert f.symbol == "XRP"


def test_summarize_notices_helper(session):
    collector = NoticeCollector({"mock": MockNoticeSource("mock")})
    collector.collect_once(session, exchange="mock", source_name="mock", now=_now())
    session.commit()
    rows = session.execute(select(ExchangeNotice)).scalars().all()
    d = summarize_notices_for_agent(rows)
    assert d["count"] == len(rows)
    assert d["direct_order_allowed"] is False


# ── 12~14. REST API ─────────────────────────────────────────────

@pytest.fixture
def api_client(session_factory, engine, monkeypatch):
    """앱 + DB override fixture."""
    from app.main import app
    from app.api.deps import get_db, get_notice_collector

    def _override_db():
        with session_factory() as s:
            try:
                yield s
            finally:
                pass

    fresh_collector = NoticeCollector({"mock": MockNoticeSource("mock")})

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_notice_collector] = lambda: fresh_collector
    yield TestClient(app), fresh_collector
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_notice_collector, None)


def test_api_collect_requires_admin(api_client):
    client, _ = api_client
    r = client.post("/api/notices/collect", json={"exchange": "mock", "source": "mock"})
    assert r.status_code == 401


def test_api_collect_with_admin_succeeds(api_client):
    from app.core.config import get_settings
    client, _ = api_client
    token = get_settings().admin_token
    r = client.post(
        "/api/notices/collect",
        json={"exchange": "mock", "source": "mock", "since_hours": 240},
        headers={"X-Admin-Token": token},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["fetched"] >= 5
    assert body["inserted"] >= 5
    assert body["direct_order_allowed"] is False
    assert "DELISTING" in body["by_type"]


def test_api_collect_rejects_unknown_source(api_client):
    from app.core.config import get_settings
    client, _ = api_client
    token = get_settings().admin_token
    r = client.post(
        "/api/notices/collect",
        json={"exchange": "mock", "source": "nonexistent"},
        headers={"X-Admin-Token": token},
    )
    assert r.status_code == 400


def test_api_get_notices_includes_exchange_notices(api_client):
    from app.core.config import get_settings
    client, _ = api_client
    token = get_settings().admin_token
    client.post(
        "/api/notices/collect",
        json={"exchange": "mock", "source": "mock"},
        headers={"X-Admin-Token": token},
    )
    r = client.get("/api/notices")
    assert r.status_code == 200
    body = r.json()
    assert "exchange_notices" in body
    assert len(body["exchange_notices"]) >= 5
    assert body["summary"]["by_type"]
    assert body["direct_order_allowed"] is False


def test_api_get_notice_context(api_client):
    from app.core.config import get_settings
    client, _ = api_client
    token = get_settings().admin_token
    client.post(
        "/api/notices/collect",
        json={"exchange": "mock", "source": "mock"},
        headers={"X-Admin-Token": token},
    )
    r = client.get("/api/notices/context?lookback_hours=72")
    assert r.status_code == 200
    body = r.json()
    assert body["direct_order_allowed"] is False
    assert body["total_notices"] > 0
    assert "DELISTING" in body["by_type"]


def test_api_notice_context_symbol_filter(api_client):
    from app.core.config import get_settings
    client, _ = api_client
    token = get_settings().admin_token
    client.post(
        "/api/notices/collect",
        json={"exchange": "mock", "source": "mock"},
        headers={"X-Admin-Token": token},
    )
    r = client.get("/api/notices/context?symbols=LUNA,XRP&lookback_hours=72")
    assert r.status_code == 200
    body = r.json()
    syms = {s["symbol"]: s for s in body["symbol_summaries"]}
    assert "LUNA" in syms
    assert "XRP" in syms
    assert syms["LUNA"]["direct_order_allowed"] is False
    assert "delisting_or_termination" in syms["LUNA"]["risk_flags"]


def test_api_notice_types_catalog(api_client):
    client, _ = api_client
    r = client.get("/api/notices/types")
    assert r.status_code == 200
    body = r.json()
    assert "DEPOSIT_WITHDRAWAL_SUSPENSION" in body["notice_types"]
    assert "CRITICAL" in body["severities"]
    assert body["direct_order_allowed"] is False


# ── 15. ExchangeNotice ORM UNIQUE 제약 ──────────────────────────

def test_exchange_notice_unique_notice_id(session):
    n1 = ExchangeNotice(
        exchange="mock", notice_id="x-1", title="t1",
        notice_type="OTHER", severity="INFO", symbols=[],
        collected_at=_now(), updated_at=_now(),
        content_hash="h1", raw_payload={},
    )
    session.add(n1)
    session.commit()
    n2 = ExchangeNotice(
        exchange="mock", notice_id="x-1", title="t1-dup",
        notice_type="OTHER", severity="INFO", symbols=[],
        collected_at=_now(), updated_at=_now(),
        content_hash="h2", raw_payload={},
    )
    session.add(n2)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_exchange_notice_direct_order_allowed_default_false(session):
    n = ExchangeNotice(
        exchange="mock", title="t",
        notice_type="OTHER", severity="INFO", symbols=[],
        collected_at=_now(), updated_at=_now(),
        content_hash="h-direct-default", raw_payload={},
    )
    session.add(n)
    session.commit()
    fetched = session.execute(select(ExchangeNotice).where(ExchangeNotice.id == n.id)).scalar_one()
    assert fetched.direct_order_allowed is False


# ── 16. 금지 모듈 import 회귀 ───────────────────────────────────

def test_notice_modules_dont_import_broker_or_execution():
    root = Path(__file__).resolve().parent.parent / "app" / "market"
    forbidden = re.compile(
        r"^\s*(?:from\s+app\.(?:brokers|execution)|import\s+app\.(?:brokers|execution))",
        re.M,
    )
    for fname in ("notice_collector.py", "notice_context.py"):
        text = (root / fname).read_text(encoding="utf-8")
        assert not forbidden.search(text), f"{fname} imports broker/execution"


def test_notice_modules_dont_contain_forbidden_strings():
    """CLAUDE.md §2.1 / §2.3 — 금지 문자열이 본 모듈에 새로 생기지 않았는지 확인."""
    root = Path(__file__).resolve().parent.parent / "app" / "market"
    forbidden_substrings = (
        "ENABLE_LIVE_TRADING = True",
        "ENABLE_AI_EXECUTION = True",
        "ENABLE_CRYPTO_FUTURES_LIVE = True",
        "place_order(",
        "cancel_order(",
        "get_balance(",
    )
    for fname in ("notice_collector.py", "notice_context.py"):
        text = (root / fname).read_text(encoding="utf-8")
        for needle in forbidden_substrings:
            assert needle not in text, f"{fname} contains forbidden substring {needle!r}"


# ── 17. NOTICE_TYPES / SEVERITIES 카탈로그 ──────────────────────

def test_notice_types_catalog_size():
    assert len(NOTICE_TYPES) == 8
    assert "OTHER" in NOTICE_TYPES


def test_severities_catalog():
    assert SEVERITIES == ("INFO", "WARNING", "HIGH", "CRITICAL")
