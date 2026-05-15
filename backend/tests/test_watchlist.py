"""체크리스트 #14 Watchlist/Universe — 회귀 테스트.

검증:
  1. WatchlistEntry 모델 — insert, unique 제약, default 값
  2. WatchlistService — CRUD, list_names, set_enabled, remove_by_list
  3. Alembic 0002 마이그레이션 — upgrade/downgrade
  4. /api/watchlist REST — GET 공개, 쓰기 admin 토큰 강제
"""
from __future__ import annotations
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, WatchlistEntry
from app.market.watchlist import (
    WatchlistService, WatchlistDuplicateError, WatchlistNotFoundError,
)


# ── 픽스처 ────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    # StaticPool: 모든 connection이 같은 in-memory DB를 공유 (FastAPI 다중 요청 대응)
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
def session(engine):
    with Session(engine, future=True) as s:
        yield s


@pytest.fixture
def svc(session):
    return WatchlistService(session)


# ── 1. 모델 ──────────────────────────────────────────────────────

def test_watchlist_entry_defaults(session):
    e = WatchlistEntry(symbol="BTC")
    session.add(e)
    session.commit()
    out = session.get(WatchlistEntry, e.id)
    assert out.list_name == "default"
    assert out.exchange == "upbit"
    assert out.enabled is True
    assert out.tags == []
    assert out.created_at is not None
    assert out.updated_at is not None


def test_watchlist_unique_constraint(session):
    session.add(WatchlistEntry(symbol="BTC", exchange="upbit", list_name="default"))
    session.commit()
    session.add(WatchlistEntry(symbol="BTC", exchange="upbit", list_name="default"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_watchlist_same_symbol_different_exchange_allowed(session):
    session.add(WatchlistEntry(symbol="BTC", exchange="upbit"))
    session.add(WatchlistEntry(symbol="BTC", exchange="okx"))
    session.commit()  # OK
    rows = session.query(WatchlistEntry).all()
    assert len(rows) == 2


# ── 2. 서비스 CRUD ───────────────────────────────────────────────

def test_service_add_and_list(svc):
    e = svc.add(symbol="BTC", exchange="upbit", tags=["major"])
    assert e["symbol"] == "BTC"
    assert e["tags"] == ["major"]
    assert e["enabled"] is True
    rows = svc.list_entries()
    assert len(rows) == 1
    assert rows[0]["id"] == e["id"]


def test_service_add_duplicate_raises(svc):
    svc.add(symbol="ETH")
    with pytest.raises(WatchlistDuplicateError):
        svc.add(symbol="ETH")


def test_service_filter_by_list_name(svc):
    svc.add(symbol="BTC", list_name="kimp_pairs")
    svc.add(symbol="ETH", list_name="kimp_pairs")
    svc.add(symbol="SOL", list_name="majors")
    kimp = svc.list_entries(list_name="kimp_pairs")
    assert {e["symbol"] for e in kimp} == {"BTC", "ETH"}
    majors = svc.list_entries(list_name="majors")
    assert {e["symbol"] for e in majors} == {"SOL"}


def test_service_enabled_only(svc):
    svc.add(symbol="BTC", enabled=True)
    svc.add(symbol="ETH", enabled=False)
    only = svc.list_entries(enabled_only=True)
    assert {e["symbol"] for e in only} == {"BTC"}


def test_service_set_enabled_toggles(svc):
    e = svc.add(symbol="BTC")
    out = svc.set_enabled(e["id"], False)
    assert out["enabled"] is False
    out2 = svc.set_enabled(e["id"], True)
    assert out2["enabled"] is True


def test_service_remove(svc):
    e = svc.add(symbol="BTC")
    svc.remove(e["id"])
    assert svc.list_entries() == []


def test_service_remove_not_found(svc):
    with pytest.raises(WatchlistNotFoundError):
        svc.remove(9999)


def test_service_get_by_id_not_found(svc):
    with pytest.raises(WatchlistNotFoundError):
        svc.get_by_id(9999)


def test_service_list_names(svc):
    svc.add(symbol="BTC", list_name="default")
    svc.add(symbol="ETH", list_name="kimp_pairs")
    svc.add(symbol="SOL", list_name="majors")
    names = svc.list_names()
    assert names == ["default", "kimp_pairs", "majors"]


def test_service_remove_by_list(svc):
    svc.add(symbol="BTC", list_name="kimp_pairs")
    svc.add(symbol="ETH", list_name="kimp_pairs")
    svc.add(symbol="SOL", list_name="majors")
    n = svc.remove_by_list("kimp_pairs")
    assert n == 2
    assert svc.count() == 1
    assert svc.list_entries(list_name="kimp_pairs") == []


def test_service_count(svc):
    svc.add(symbol="BTC", enabled=True)
    svc.add(symbol="ETH", enabled=False)
    assert svc.count() == 2
    assert svc.count(enabled_only=True) == 1


# ── 3. Alembic 0002 ──────────────────────────────────────────────

def _alembic_config(db_path: Path):
    from alembic.config import Config
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location",
                        str(backend_root / "app" / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_alembic_0002_creates_watchlist(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "wl_up.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from app.db.session import reset_engine
    reset_engine()
    from alembic import command
    command.upgrade(_alembic_config(db_path), "head")

    eng = create_engine(f"sqlite:///{db_path}")
    insp = inspect(eng)
    assert "watchlist" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("watchlist")}
    assert {"id", "list_name", "symbol", "exchange", "enabled",
            "max_notional_usdt_override", "tags", "note",
            "created_at", "updated_at"}.issubset(cols)
    eng.dispose()
    reset_engine()


def test_alembic_0002_downgrade_removes_watchlist(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "wl_down.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from app.db.session import reset_engine
    reset_engine()
    from alembic import command
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0001")  # 0002만 롤백

    eng = create_engine(f"sqlite:///{db_path}")
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    assert "watchlist" not in tables
    # 0001 테이블은 남아있어야 함
    assert "audit_events" in tables
    assert "orders" in tables
    eng.dispose()
    reset_engine()


# ── 4. REST API ──────────────────────────────────────────────────

@pytest.fixture
def app_with_db(engine):
    """app.main.app + get_db override (in-memory sqlite)."""
    from app.main import app
    from app.api.deps import get_db
    Sf = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    def _override():
        s = Sf()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    yield app
    app.dependency_overrides.pop(get_db, None)


def test_api_get_watchlist_public(app_with_db):
    client = TestClient(app_with_db)
    r = client.get("/api/watchlist")
    assert r.status_code == 200
    body = r.json()
    assert body["entries"] == []
    assert body["lists"] == []


def test_api_post_requires_admin(app_with_db):
    client = TestClient(app_with_db)
    r = client.post("/api/watchlist", json={"symbol": "BTC"})
    assert r.status_code == 401


def test_api_full_crud_flow(app_with_db):
    from app.core.config import get_settings
    token = get_settings().admin_token
    client = TestClient(app_with_db)
    H = {"X-Admin-Token": token}

    # 추가
    r = client.post("/api/watchlist",
                    json={"symbol": "BTC", "tags": ["major"]}, headers=H)
    assert r.status_code == 201
    eid = r.json()["id"]

    # 중복 추가 → 409
    r2 = client.post("/api/watchlist", json={"symbol": "BTC"}, headers=H)
    assert r2.status_code == 409

    # 조회 (공개)
    r3 = client.get("/api/watchlist")
    assert r3.status_code == 200
    body = r3.json()
    assert len(body["entries"]) == 1
    assert body["entries"][0]["symbol"] == "BTC"

    # disable
    r4 = client.patch(f"/api/watchlist/{eid}/disable", headers=H)
    assert r4.status_code == 200
    assert r4.json()["enabled"] is False

    # enable
    r5 = client.patch(f"/api/watchlist/{eid}/enable", headers=H)
    assert r5.status_code == 200
    assert r5.json()["enabled"] is True

    # delete
    r6 = client.delete(f"/api/watchlist/{eid}", headers=H)
    assert r6.status_code == 204

    # 다시 조회 → 비어있음
    r7 = client.get("/api/watchlist")
    assert r7.json()["entries"] == []


def test_api_delete_nonexistent_returns_404(app_with_db):
    from app.core.config import get_settings
    token = get_settings().admin_token
    client = TestClient(app_with_db)
    r = client.delete("/api/watchlist/9999",
                      headers={"X-Admin-Token": token})
    assert r.status_code == 404


def test_api_filter_by_list_name(app_with_db):
    from app.core.config import get_settings
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app_with_db)

    client.post("/api/watchlist", json={"symbol": "BTC", "list_name": "kimp"}, headers=H)
    client.post("/api/watchlist", json={"symbol": "ETH", "list_name": "kimp"}, headers=H)
    client.post("/api/watchlist", json={"symbol": "SOL", "list_name": "majors"}, headers=H)

    r = client.get("/api/watchlist?list_name=kimp")
    symbols = {e["symbol"] for e in r.json()["entries"]}
    assert symbols == {"BTC", "ETH"}
