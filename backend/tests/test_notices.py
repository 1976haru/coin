"""체크리스트 #18 Exchange Notices — 회귀 테스트.

검증:
  1. Notice 생성/시간창 active/만료
  2. NoticeRegistry CRUD
  3. assess_symbol_notices — 각 NoticeKind 별 플래그 변화
  4. KimpStrategy 호환 — deposit_withdrawal_ok 직결
  5. block_reasons 다중 (symbol, exchange) 차단 사유
  6. REST: /api/notices CRUD + admin gating + symbol status
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.market.notices import (
    Notice, NoticeRegistry, assess_symbol_notices, block_reasons,
)


# ── 1. Notice 시간창 ─────────────────────────────────────────────

def test_notice_active_within_window():
    now = datetime.now(timezone.utc)
    n = Notice(
        id=1, exchange="upbit", symbol="BTC", kind="DELISTING",
        message="상장폐지", starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=1),
    )
    assert n.is_active(now) is True


def test_notice_inactive_before_start():
    now = datetime.now(timezone.utc)
    n = Notice(
        id=1, exchange="upbit", symbol="BTC", kind="WARNING",
        message="유의", starts_at=now + timedelta(hours=1),
    )
    assert n.is_active(now) is False


def test_notice_inactive_after_end():
    now = datetime.now(timezone.utc)
    n = Notice(
        id=1, exchange="upbit", symbol="BTC", kind="MAINTENANCE",
        message="점검", starts_at=now - timedelta(hours=2),
        ends_at=now - timedelta(hours=1),
    )
    assert n.is_active(now) is False


def test_notice_open_ended_stays_active():
    now = datetime.now(timezone.utc)
    n = Notice(
        id=1, exchange="upbit", symbol="BTC", kind="DELISTING",
        message="상폐", starts_at=now - timedelta(days=1),
    )
    assert n.is_active(now) is True
    # 1년 후도 여전히 active
    assert n.is_active(now + timedelta(days=365)) is True


def test_notice_severity_mapping():
    assert Notice(1, "u", "B", "DELISTING", "", datetime.now(timezone.utc)).severity == "block"
    assert Notice(1, "u", "B", "WARNING", "", datetime.now(timezone.utc)).severity == "warn"
    assert Notice(1, "u", "B", "DEPOSIT_SUSPENDED", "", datetime.now(timezone.utc)).severity == "block"


def test_notice_to_dict_serializes_datetimes():
    now = datetime.now(timezone.utc)
    n = Notice(1, "upbit", "BTC", "WARNING", "msg", now)
    d = n.to_dict()
    assert d["starts_at"] == now.isoformat()
    assert d["ends_at"] is None
    assert d["severity"] == "warn"


# ── 2. NoticeRegistry CRUD ───────────────────────────────────────

def test_registry_add_and_get():
    r = NoticeRegistry()
    n = r.add(exchange="upbit", symbol="BTC", kind="WARNING", message="유의")
    assert n.id == 1
    assert r.get(1) == n


def test_registry_id_autoincrement():
    r = NoticeRegistry()
    a = r.add("upbit", "BTC", "WARNING", "m")
    b = r.add("okx", "ETH", "WARNING", "m")
    assert a.id == 1
    assert b.id == 2


def test_registry_remove():
    r = NoticeRegistry()
    n = r.add("upbit", "BTC", "WARNING", "m")
    assert r.remove(n.id) is True
    assert r.get(n.id) is None
    assert r.remove(n.id) is False  # 두 번째 제거는 실패


def test_registry_active_filters_by_time():
    r = NoticeRegistry()
    now = datetime.now(timezone.utc)
    r.add("upbit", "BTC", "WARNING", "now",
          starts_at=now - timedelta(minutes=1))
    r.add("upbit", "ETH", "MAINTENANCE", "future",
          starts_at=now + timedelta(hours=1))
    actives = r.active(now)
    assert len(actives) == 1
    assert actives[0].symbol == "BTC"


def test_registry_active_for_specific_pair():
    r = NoticeRegistry()
    r.add("upbit", "BTC", "WARNING", "btc warn")
    r.add("upbit", "ETH", "WARNING", "eth warn")
    r.add("okx",   "BTC", "WARNING", "btc okx")
    out = r.active_for("BTC", "upbit")
    assert len(out) == 1
    assert out[0].message == "btc warn"


def test_registry_invalid_kind_raises():
    r = NoticeRegistry()
    with pytest.raises(ValueError):
        r.add("upbit", "BTC", "BOGUS_KIND", "test")  # type: ignore[arg-type]


# ── 3. assess_symbol_notices — 개별 kind ────────────────────────

def test_assess_clean_symbol_returns_all_ok():
    r = NoticeRegistry()
    s = assess_symbol_notices(r, "BTC", "upbit")
    assert s.deposit_ok is True
    assert s.withdrawal_ok is True
    assert s.tradable is True
    assert s.has_warning is False


def test_assess_deposit_suspended():
    r = NoticeRegistry()
    r.add("upbit", "BTC", "DEPOSIT_SUSPENDED", "입금 중단")
    s = assess_symbol_notices(r, "BTC", "upbit")
    assert s.deposit_ok is False
    assert s.withdrawal_ok is True
    assert s.tradable is True
    assert s.deposit_withdrawal_ok is False


def test_assess_withdrawal_suspended():
    r = NoticeRegistry()
    r.add("upbit", "BTC", "WITHDRAWAL_SUSPENDED", "출금 중단")
    s = assess_symbol_notices(r, "BTC", "upbit")
    assert s.withdrawal_ok is False
    assert s.deposit_withdrawal_ok is False


def test_assess_both_suspended():
    r = NoticeRegistry()
    r.add("upbit", "BTC", "BOTH_SUSPENDED", "입출금 중단")
    s = assess_symbol_notices(r, "BTC", "upbit")
    assert s.deposit_ok is False
    assert s.withdrawal_ok is False


def test_assess_delisting_blocks_everything():
    r = NoticeRegistry()
    r.add("upbit", "BTC", "DELISTING", "상폐")
    s = assess_symbol_notices(r, "BTC", "upbit")
    assert s.tradable is False
    assert s.deposit_ok is False
    assert s.withdrawal_ok is False


def test_assess_maintenance_blocks_tradable_only():
    r = NoticeRegistry()
    r.add("upbit", "BTC", "MAINTENANCE", "점검")
    s = assess_symbol_notices(r, "BTC", "upbit")
    assert s.tradable is False
    # 점검은 입출금 자체와는 분리 — 기본은 ok 유지
    assert s.deposit_ok is True


def test_assess_warning_does_not_block_trade():
    r = NoticeRegistry()
    r.add("upbit", "BTC", "WARNING", "유의종목")
    s = assess_symbol_notices(r, "BTC", "upbit")
    assert s.tradable is True
    assert s.has_warning is True
    assert s.deposit_withdrawal_ok is True


def test_assess_combines_multiple_active_notices():
    r = NoticeRegistry()
    r.add("upbit", "BTC", "WARNING", "유의")
    r.add("upbit", "BTC", "DEPOSIT_SUSPENDED", "입금 중단")
    s = assess_symbol_notices(r, "BTC", "upbit")
    assert s.has_warning is True
    assert s.deposit_ok is False
    assert s.tradable is True
    assert len(s.active_notices) == 2


def test_assess_ignores_other_exchange():
    r = NoticeRegistry()
    r.add("okx", "BTC", "DELISTING", "okx 상폐")
    s = assess_symbol_notices(r, "BTC", "upbit")
    assert s.tradable is True


# ── 4. block_reasons 다중 검사 ───────────────────────────────────

def test_block_reasons_collects_failures():
    r = NoticeRegistry()
    r.add("upbit", "BTC", "DEPOSIT_SUSPENDED", "")
    r.add("upbit", "ETH", "DELISTING", "")
    r.add("upbit", "SOL", "WARNING", "")  # 유의는 차단 사유 아님

    targets = [("BTC", "upbit"), ("ETH", "upbit"), ("SOL", "upbit"), ("XRP", "upbit")]
    reasons = block_reasons(r, targets)
    assert len(reasons) == 2  # BTC + ETH
    assert any("BTC" in x for x in reasons)
    assert any("ETH" in x for x in reasons)


def test_block_reasons_empty_when_no_blocks():
    r = NoticeRegistry()
    r.add("upbit", "BTC", "WARNING", "유의")
    reasons = block_reasons(r, [("BTC", "upbit")])
    assert reasons == []


# ── 5. REST API ──────────────────────────────────────────────────

@pytest.fixture
def app_with_clean_notices():
    """깨끗한 NoticeRegistry override + 토큰 헤더 헬퍼."""
    from app.main import app
    from app.api.deps import get_notices

    test_registry = NoticeRegistry()
    app.dependency_overrides[get_notices] = lambda: test_registry
    yield app, test_registry
    app.dependency_overrides.pop(get_notices, None)


def test_api_get_notices_empty(app_with_clean_notices):
    app, _ = app_with_clean_notices
    client = TestClient(app)
    r = client.get("/api/notices")
    assert r.status_code == 200
    assert r.json() == {"notices": [], "count": 0}


def test_api_post_requires_admin(app_with_clean_notices):
    app, _ = app_with_clean_notices
    client = TestClient(app)
    r = client.post("/api/notices",
                    json={"exchange": "upbit", "symbol": "BTC", "kind": "WARNING"})
    assert r.status_code == 401


def test_api_full_crud_flow(app_with_clean_notices):
    from app.core.config import get_settings
    app, registry = app_with_clean_notices
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app)

    # 추가
    r = client.post("/api/notices", headers=H, json={
        "exchange": "upbit", "symbol": "BTC",
        "kind": "DELISTING", "message": "상폐",
    })
    assert r.status_code == 201
    nid = r.json()["id"]

    # 잘못된 kind → 400
    r2 = client.post("/api/notices", headers=H, json={
        "exchange": "upbit", "symbol": "X", "kind": "BOGUS",
    })
    assert r2.status_code == 400

    # 조회
    r3 = client.get("/api/notices")
    assert r3.json()["count"] == 1

    # 심볼 상태 조회
    r4 = client.get("/api/notices/symbol/upbit/BTC")
    body = r4.json()
    assert body["tradable"] is False
    assert body["deposit_withdrawal_ok"] is False
    assert "상폐" in body["reasons"][0]

    # 다른 심볼은 깨끗
    r5 = client.get("/api/notices/symbol/upbit/ETH")
    assert r5.json()["tradable"] is True

    # 삭제
    r6 = client.delete(f"/api/notices/{nid}", headers=H)
    assert r6.status_code == 204
    assert client.get("/api/notices").json()["count"] == 0


def test_api_delete_nonexistent_returns_404(app_with_clean_notices):
    from app.core.config import get_settings
    app, _ = app_with_clean_notices
    token = get_settings().admin_token
    client = TestClient(app)
    r = client.delete("/api/notices/9999", headers={"X-Admin-Token": token})
    assert r.status_code == 404


def test_api_filter_by_exchange_and_symbol(app_with_clean_notices):
    from app.core.config import get_settings
    app, _ = app_with_clean_notices
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app)
    client.post("/api/notices", headers=H,
                json={"exchange": "upbit", "symbol": "BTC", "kind": "WARNING"})
    client.post("/api/notices", headers=H,
                json={"exchange": "okx", "symbol": "BTC", "kind": "WARNING"})

    r = client.get("/api/notices?exchange=okx")
    assert r.json()["count"] == 1
    r2 = client.get("/api/notices?symbol=BTC&exchange=upbit")
    assert r2.json()["count"] == 1
