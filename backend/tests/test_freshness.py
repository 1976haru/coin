"""체크리스트 #16 Data Freshness — 회귀 테스트.

검증 범위:
  1. 순수 함수 (legacy 유지) — check_timestamp_freshness, check_feed_freshness,
     should_block_new_buy
  2. is_stale / compute_lag_seconds — 정밀 케이스 (None / past / future / 0 max_age)
  3. FreshnessPolicy — data_type 별 max_age 매핑
  4. FreshnessTracker — mark_seen, mark/clear_reconnecting, evaluate
  5. side 정책 — BUY/ENTER/OPEN 차단, SELL/EXIT/CLOSE 통과
  6. collector 연동 — 수집 성공 시 mark_seen, 실패 시 unchanged
  7. order_preview — stale/reconnecting 시 BLOCKED/REJECTED
  8. REST — GET /api/freshness summary, POST/DELETE reconnecting admin token 강제
  9. 정적 금지 문자열 부재
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.market.freshness import (
    check_timestamp_freshness, check_feed_freshness, should_block_new_buy,
    DataFeedState,
    FreshnessTracker, FreshnessPolicy, FreshnessStatus,
    is_stale, compute_lag_seconds,
    is_entry_side, is_exit_side, ENTRY_SIDES, EXIT_SIDES,
)


# ── 1. legacy 순수 함수 (회귀 보장) ──────────────────────────────

def test_freshness_stale_blocks_buy():
    now = datetime.now(timezone.utc)
    stale = check_timestamp_freshness(now - timedelta(seconds=10), 5, now, "quote")
    block, reasons = should_block_new_buy(stale)
    assert block is True
    assert "지연" in reasons[0]


def test_reconnecting_feed_is_not_fresh():
    now = datetime.now(timezone.utc)
    status = check_feed_freshness(DataFeedState(True, True, now, "upbit"), 5, now)
    assert status.ok is False
    assert "재연결" in status.reason


def test_fresh_quote_allows_buy():
    now = datetime.now(timezone.utc)
    fresh = check_timestamp_freshness(now, 5, now, "quote")
    block, reasons = should_block_new_buy(fresh)
    assert block is False
    assert reasons == []


# ── 2. is_stale / compute_lag_seconds ──────────────────────────

def _now():
    return datetime.now(timezone.utc)


def test_is_stale_recent_returns_false():
    now = _now()
    assert is_stale(now - timedelta(seconds=1), max_age_seconds=10, now=now) is False


def test_is_stale_exceeded_returns_true():
    now = _now()
    assert is_stale(now - timedelta(seconds=20), max_age_seconds=10, now=now) is True


def test_is_stale_none_returns_true():
    """last_seen_at None → missing, stale 처리."""
    assert is_stale(None, max_age_seconds=10) is True


def test_is_stale_future_timestamp_treated_stale():
    """clock skew 방지: 미래 timestamp 는 stale 로 처리."""
    now = _now()
    assert is_stale(now + timedelta(seconds=60), max_age_seconds=10, now=now) is True


def test_is_stale_zero_or_negative_max_age_returns_true():
    now = _now()
    assert is_stale(now, max_age_seconds=0, now=now) is True
    assert is_stale(now, max_age_seconds=-1, now=now) is True


def test_compute_lag_seconds_none_returns_none():
    assert compute_lag_seconds(None) is None


def test_compute_lag_seconds_future_returns_zero():
    now = _now()
    assert compute_lag_seconds(now + timedelta(seconds=10), now=now) == 0.0


def test_compute_lag_seconds_naive_input_treated_as_utc():
    now = _now()
    naive = (now - timedelta(seconds=5)).replace(tzinfo=None)
    lag = compute_lag_seconds(naive, now=now)
    assert lag is not None
    assert 4 < lag < 6


# ── 3. FreshnessPolicy ──────────────────────────────────────────

def test_policy_max_age_for_each_type():
    p = FreshnessPolicy()
    assert p.max_age_for("ticker")    == 30.0
    assert p.max_age_for("orderbook") == 10.0
    assert p.max_age_for("ohlcv")     == 300.0
    assert p.max_age_for("funding")   == 3600.0
    assert p.max_age_for("fx")        == 300.0
    # 알 수 없는 data_type 은 보수적으로 짧은 한도 (ticker) 적용
    assert p.max_age_for("unknown")   == 30.0


# ── 4. FreshnessTracker — 기본 동작 ─────────────────────────────

def test_tracker_mark_seen_records_last_seen_at():
    t = FreshnessTracker()
    t.mark_seen("BTC", "upbit", "ticker")
    rec = t.get_record("BTC", "upbit", "ticker")
    assert rec is not None
    assert rec.last_seen_at is not None


def test_tracker_mark_seen_keeps_most_recent_timestamp():
    t = FreshnessTracker()
    base = _now()
    t.mark_seen("BTC", "upbit", "ticker", seen_at=base - timedelta(seconds=10))
    t.mark_seen("BTC", "upbit", "ticker", seen_at=base)
    rec = t.get_record("BTC", "upbit", "ticker")
    # 더 최신 ts 가 보존
    assert (rec.last_seen_at - base).total_seconds() == pytest.approx(0, abs=1e-3)


def test_tracker_summary_counts():
    t = FreshnessTracker(policy=FreshnessPolicy(ticker_max_age_sec=1.0))
    now = _now()
    t.mark_seen("BTC", "upbit", "ticker", seen_at=now)
    t.mark_seen("ETH", "upbit", "ticker", seen_at=now - timedelta(seconds=30))
    s = t.get_summary(now=now)
    assert s["counts"]["fresh"] == 1
    assert s["counts"]["stale"] == 1
    assert s["counts"]["missing"] == 0
    assert s["counts"]["total"]   == 2


def test_tracker_evaluate_unknown_record_returns_missing():
    t = FreshnessTracker()
    st = t.evaluate("BTC", "upbit", "ticker")
    assert st.ok is False
    assert "수신 기록 없음" in st.reason


def test_tracker_evaluate_fresh_record_ok():
    t = FreshnessTracker()
    t.mark_seen("BTC", "upbit", "ticker")
    st = t.evaluate("BTC", "upbit", "ticker")
    assert st.ok is True


def test_tracker_evaluate_old_record_stale():
    t = FreshnessTracker(policy=FreshnessPolicy(ticker_max_age_sec=1.0))
    now = _now()
    t.mark_seen("BTC", "upbit", "ticker", seen_at=now - timedelta(seconds=30))
    st = t.evaluate("BTC", "upbit", "ticker", now=now)
    assert st.ok is False
    assert "지연" in st.reason


# ── 5. Reconnecting guard ──────────────────────────────────────

def test_tracker_global_reconnecting_blocks_evaluate():
    t = FreshnessTracker()
    t.mark_seen("BTC", "upbit", "ticker")
    t.mark_reconnecting(reason="ws drop")
    st = t.evaluate("BTC", "upbit", "ticker")
    assert st.ok is False
    assert "재연결" in st.reason


def test_tracker_per_exchange_reconnecting():
    t = FreshnessTracker()
    t.mark_seen("BTC", "upbit",   "ticker")
    t.mark_seen("BTC", "binance", "ticker")
    t.mark_reconnecting(exchange="upbit", reason="ws upbit reset")
    st_upbit   = t.evaluate("BTC", "upbit",   "ticker")
    st_binance = t.evaluate("BTC", "binance", "ticker")
    assert st_upbit.ok   is False
    assert st_binance.ok is True


def test_tracker_clear_reconnecting_restores_fresh():
    t = FreshnessTracker()
    t.mark_seen("BTC", "upbit", "ticker")
    t.mark_reconnecting(exchange="upbit")
    assert t.evaluate("BTC", "upbit", "ticker").ok is False
    assert t.clear_reconnecting(exchange="upbit") is True
    assert t.evaluate("BTC", "upbit", "ticker").ok is True


def test_tracker_clear_reconnecting_but_stale_still_blocked():
    """reconnecting 해제해도 last_seen_at 이 오래됐으면 여전히 stale."""
    t = FreshnessTracker(policy=FreshnessPolicy(ticker_max_age_sec=1.0))
    now = _now()
    t.mark_seen("BTC", "upbit", "ticker", seen_at=now - timedelta(seconds=60))
    t.mark_reconnecting(exchange="upbit")
    t.clear_reconnecting(exchange="upbit")
    st = t.evaluate("BTC", "upbit", "ticker", now=now)
    assert st.ok is False
    assert "지연" in st.reason


def test_can_open_new_position_blocked_by_reconnect():
    t = FreshnessTracker()
    t.mark_seen("BTC", "upbit", "ticker")
    t.mark_reconnecting(exchange="upbit", reason="ws drop")
    ok, reasons = t.can_open_new_position("BTC", "upbit")
    assert ok is False
    assert any("reconnecting" in r for r in reasons)


def test_can_open_new_position_blocked_by_stale():
    t = FreshnessTracker(policy=FreshnessPolicy(ticker_max_age_sec=1.0))
    now = _now()
    t.mark_seen("BTC", "upbit", "ticker", seen_at=now - timedelta(seconds=10))
    ok, reasons = t.can_open_new_position("BTC", "upbit", now=now)
    assert ok is False
    assert any("지연" in r for r in reasons)


def test_can_open_new_position_ok_when_fresh():
    t = FreshnessTracker()
    t.mark_seen("BTC", "upbit", "ticker")
    ok, reasons = t.can_open_new_position("BTC", "upbit")
    assert ok is True
    assert reasons == []


# ── 6. side 정책 — entry vs exit ───────────────────────────────

def test_side_classifier_entry_vs_exit():
    for s in ("BUY", "ENTER", "OPEN", "OPEN_LONG", "OPEN_SHORT", "OPEN_REVERSE_KIMP"):
        assert is_entry_side(s) is True
        assert is_exit_side(s)  is False
    for s in ("SELL", "EXIT", "CLOSE", "CLOSE_LONG", "CLOSE_SHORT"):
        assert is_exit_side(s)  is True
        assert is_entry_side(s) is False


def test_entry_exit_sides_constants_match_spec():
    assert "BUY" in ENTRY_SIDES
    assert "SELL" in EXIT_SIDES
    assert ENTRY_SIDES.isdisjoint(EXIT_SIDES)


def test_can_generate_signal_exit_always_allowed():
    """SELL/EXIT/CLOSE 는 stale/reconnecting 이어도 허용 (위험 축소)."""
    t = FreshnessTracker()
    t.mark_reconnecting()
    for side in ("SELL", "EXIT", "CLOSE", "CLOSE_LONG"):
        ok, reasons = t.can_generate_signal("BTC", "upbit", side)
        assert ok is True
        assert reasons == []


def test_can_generate_signal_entry_blocked_when_reconnecting():
    t = FreshnessTracker()
    t.mark_reconnecting(exchange="upbit", reason="ws")
    ok, reasons = t.can_generate_signal("BTC", "upbit", "BUY")
    assert ok is False
    assert any("reconnecting" in r for r in reasons)


def test_evaluate_for_order_exit_returns_not_block():
    t = FreshnessTracker()
    t.mark_reconnecting()
    block, statuses, reasons = t.evaluate_for_order("BTC", "upbit", "SELL")
    assert block is False
    assert reasons == []
    # statuses 는 ticker 1 행 채워서 반환 — gateway 호환
    assert len(statuses) == 1


def test_evaluate_for_order_entry_block_includes_reason():
    t = FreshnessTracker(policy=FreshnessPolicy(ticker_max_age_sec=0.001))
    now = _now()
    t.mark_seen("BTC", "upbit", "ticker", seen_at=now - timedelta(seconds=10))
    block, statuses, reasons = t.evaluate_for_order("BTC", "upbit", "BUY", now=now)
    assert block is True
    assert any("지연" in r for r in reasons)
    assert all(isinstance(s, FreshnessStatus) for s in statuses)


# ── 7. block_buy_when_* 정책 토글 ──────────────────────────────

def test_block_buy_when_stale_false_lets_entry_through():
    p = FreshnessPolicy(ticker_max_age_sec=1.0, block_buy_when_stale=False)
    t = FreshnessTracker(policy=p)
    now = _now()
    t.mark_seen("BTC", "upbit", "ticker", seen_at=now - timedelta(seconds=60))
    ok, _ = t.can_open_new_position("BTC", "upbit", now=now)
    assert ok is True


def test_block_buy_when_reconnecting_false_lets_entry_through():
    p = FreshnessPolicy(block_buy_when_reconnecting=False)
    t = FreshnessTracker(policy=p)
    t.mark_seen("BTC", "upbit", "ticker")
    t.mark_reconnecting()
    ok, _ = t.can_open_new_position("BTC", "upbit")
    assert ok is True


# ── 8. Collector 연동 ──────────────────────────────────────────

def test_collector_marks_seen_on_ticker_success():
    from app.market.collector import MarketDataCollector, MockMarketDataSource
    tracker = FreshnessTracker()
    c = MarketDataCollector(
        sources={"mock": MockMarketDataSource("mock")},
        freshness_tracker=tracker,
    )
    c.collect([("BTC", "mock")])
    assert tracker.get_record("BTC", "mock", "ticker") is not None


def test_collector_does_not_mark_seen_when_ticker_fails():
    from app.market.collector import MarketDataCollector
    from app.schemas import Ticker as _T  # noqa: F401

    class BrokenSource:
        name = "broken"

        def fetch_ticker(self, symbol):
            raise RuntimeError("ticker down")

        def fetch_orderbook(self, symbol, depth=5):
            raise NotImplementedError

    tracker = FreshnessTracker()
    c = MarketDataCollector(
        sources={"broken": BrokenSource()},
        freshness_tracker=tracker,
    )
    c.collect([("BTC", "broken")])
    # 실패는 mark_seen 호출 안 됨
    assert tracker.get_record("BTC", "broken", "ticker") is None


def test_collect_all_marks_seen_for_all_included_types():
    from app.market.collector import MarketDataCollector, MockMarketDataSource
    tracker = FreshnessTracker()
    c = MarketDataCollector(
        sources={"mock": MockMarketDataSource("mock")},
        freshness_tracker=tracker,
    )
    c.collect_all(
        [("BTC", "mock")],
        includes={"ticker", "ohlcv", "orderbook"},
        ohlcv_limit=5,
    )
    assert tracker.get_record("BTC", "mock", "ticker") is not None
    assert tracker.get_record("BTC", "mock", "ohlcv", "1m") is not None
    assert tracker.get_record("BTC", "mock", "orderbook") is not None


def test_collect_all_fx_recorded_under_fx_pseudoexchange():
    from app.market.collector import MarketDataCollector, MockMarketDataSource
    tracker = FreshnessTracker()
    fx = MockMarketDataSource("fx_mock", supports_fx=True)
    c = MarketDataCollector(
        sources={"mock": MockMarketDataSource("mock")},
        fx_source=fx,
        freshness_tracker=tracker,
    )
    c.collect_all(
        [("BTC", "mock")],
        includes={"ticker", "fx"},
        fx_pairs=["USDT-KRW"],
    )
    assert tracker.get_record("USDT-KRW", "fx", "fx") is not None


# ── 9. REST API ────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def app_with_db():
    """app + in-memory sqlite + fresh tracker override.

    deps.freshness_tracker 가 다른 테스트로 인해 오염될 수 있어,
    본 fixture 에서는 tracker 를 reset() 한다.
    """
    eng = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    from app.db.models import Base
    Base.metadata.create_all(eng)
    Sf = sessionmaker(bind=eng, expire_on_commit=False, future=True)

    from app.main import app
    from app.api.deps import get_db, freshness_tracker as _ft

    _ft.reset()

    def _override_db():
        s = Sf()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_db
    yield app
    app.dependency_overrides.pop(get_db, None)
    _ft.reset()
    eng.dispose()


def test_api_freshness_returns_summary(app_with_db):
    client = TestClient(app_with_db)
    r = client.get("/api/freshness")
    assert r.status_code == 200
    body = r.json()
    # legacy 호환 키 유지
    assert "ok" in body and "reason" in body
    # 신규 summary
    assert "summary" in body
    s = body["summary"]
    assert set(s.keys()) >= {
        "now", "records", "counts", "reconnecting", "policy", "blocks_new_entries",
    }
    assert set(s["counts"].keys()) >= {
        "fresh", "stale", "missing", "total", "reconnecting_scopes",
    }


def test_api_post_reconnecting_requires_admin(app_with_db):
    client = TestClient(app_with_db)
    r = client.post("/api/freshness/reconnecting", json={})
    assert r.status_code == 401


def test_api_delete_reconnecting_requires_admin(app_with_db):
    client = TestClient(app_with_db)
    r = client.delete("/api/freshness/reconnecting")
    assert r.status_code == 401


def test_api_post_then_delete_reconnecting_flow(app_with_db):
    from app.core.config import get_settings
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app_with_db)

    r = client.post(
        "/api/freshness/reconnecting",
        headers=H,
        json={"exchange": "upbit", "reason": "ws drop"},
    )
    assert r.status_code == 201
    assert r.json()["marked"] is True
    assert r.json()["scope"]["exchange"] == "upbit"

    # GET 후 summary 에 반영
    s = client.get("/api/freshness").json()["summary"]
    assert s["counts"]["reconnecting_scopes"] >= 1
    assert s["blocks_new_entries"] is True

    # DELETE
    r2 = client.delete(
        "/api/freshness/reconnecting?exchange=upbit",
        headers=H,
    )
    assert r2.status_code == 200
    assert r2.json()["cleared"] is True

    # 다시 GET — reconnecting 0
    s2 = client.get("/api/freshness").json()["summary"]
    assert s2["counts"]["reconnecting_scopes"] == 0


def test_order_preview_blocked_when_reconnecting(app_with_db):
    """체크리스트 #16: BUY preview 는 reconnecting 시 freshness reason 으로 차단."""
    from app.core.config import get_settings
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app_with_db)

    # reconnecting 글로벌 표시
    r0 = client.post(
        "/api/freshness/reconnecting",
        headers=H,
        json={"reason": "global ws drop"},
    )
    assert r0.status_code == 201

    r = client.post("/api/order/preview", json={
        "symbol": "BTC/USDT", "exchange": "upbit", "side": "BUY",
        "notional_usdt": 10, "leverage": 1, "price": 100000,
    })
    assert r.status_code == 200
    body = r.json()
    # 차단 — RiskManager 단계에서 REJECTED 가 일반적, 혹은 BLOCKED
    assert body["status"] in {"REJECTED", "BLOCKED"}
    # 차단 reason 어딘가에 freshness 흔적
    reasons_blob = str(body.get("reasons") or body.get("reason") or "")
    assert ("재연결" in reasons_blob or "reconnecting" in reasons_blob
            or "지연" in reasons_blob or "수신 기록 없음" in reasons_blob)


def test_order_preview_sell_not_blocked_by_reconnecting(app_with_db):
    """SELL/EXIT/CLOSE 는 reconnecting 이어도 freshness 로 막지 않는다."""
    from app.core.config import get_settings
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app_with_db)

    client.post(
        "/api/freshness/reconnecting",
        headers=H,
        json={"reason": "global"},
    )
    r = client.post("/api/order/preview", json={
        "symbol": "BTC/USDT", "exchange": "upbit", "side": "SELL",
        "notional_usdt": 10, "leverage": 1, "price": 100000,
    })
    assert r.status_code == 200
    body = r.json()
    # SELL 은 freshness 가 비어 통과 — 다른 단계에서 ACCEPTED/REJECTED 결정.
    # 핵심: '재연결' / '지연' 같은 freshness 사유로는 차단되지 않아야 한다.
    blob = str(body.get("reasons") or body.get("reason") or "")
    assert "재연결" not in blob
    assert "지연" not in blob


# ── 10. 정적 금지 문자열 ───────────────────────────────────────

_FORBIDDEN = (
    "place_order(",
    "cancel_order(",
    "get_balance(",
    "broker.place_order",
    "OrderExecutor",
    "route_order",
    "KIS_APP_KEY",
    "KIS_APP_SECRET",
    "API_SECRET",
    "ACCESS_TOKEN",
    "ENABLE_LIVE_TRADING = True",
    "ENABLE_AI_EXECUTION = True",
    "ENABLE_CRYPTO_FUTURES_LIVE = True",
)


def test_no_forbidden_strings_in_freshness_production():
    files = (
        "backend/app/market/freshness.py",
        "backend/app/api/orders.py",
    )
    for rel in files:
        p = REPO_ROOT / rel
        assert p.exists(), f"missing: {p}"
        text = p.read_text(encoding="utf-8")
        for needle in _FORBIDDEN:
            assert needle not in text, \
                f"{rel} contains forbidden string: {needle!r}"
