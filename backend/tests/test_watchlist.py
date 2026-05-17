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
    WatchlistValidationError, WatchlistLimitError,
    ALLOWED_EXCHANGES, DEFAULT_LIST_LIMITS,
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


# ── 5. 정규화 / 검증 (#14 보강) ──────────────────────────────────

def test_service_normalizes_symbol_upper_and_strip(svc):
    e = svc.add(symbol="  btc  ")
    assert e["symbol"] == "BTC"


def test_service_normalizes_exchange_lower(svc):
    e = svc.add(symbol="BTC", exchange="UPBIT")
    assert e["exchange"] == "upbit"


def test_service_normalizes_list_name_lower(svc):
    e = svc.add(symbol="BTC", list_name="Majors")
    assert e["list_name"] == "majors"


def test_service_rejects_empty_symbol(svc):
    with pytest.raises(WatchlistValidationError):
        svc.add(symbol="   ")


def test_service_rejects_whitespace_inside_symbol(svc):
    with pytest.raises(WatchlistValidationError):
        svc.add(symbol="BT C")


def test_service_rejects_too_long_symbol(svc):
    with pytest.raises(WatchlistValidationError):
        svc.add(symbol="A" * 33)


def test_service_rejects_unknown_exchange(svc):
    with pytest.raises(WatchlistValidationError):
        svc.add(symbol="BTC", exchange="ftx")  # 화이트리스트 외


def test_service_allows_known_exchanges(svc):
    # 화이트리스트 전수 확인
    for i, x in enumerate(ALLOWED_EXCHANGES):
        svc.add(symbol=f"SYM{i}", exchange=x)


def test_normalized_duplicate_detection(svc):
    """대소문자가 다르더라도 정규화 후 중복이면 차단되어야 한다."""
    svc.add(symbol="btc", exchange="UPBIT", list_name="DEFAULT")
    with pytest.raises(WatchlistDuplicateError):
        svc.add(symbol="BTC", exchange="upbit", list_name="default")


# ── 6. universe 크기 제한 (#14 보강) ─────────────────────────────

def test_service_list_limit_blocks_add(session):
    """list_name 별 enabled cap 초과 시 LimitError."""
    svc = WatchlistService(session, list_limits={"tiny": 2}, max_enabled_total=999)
    svc.add(symbol="A", list_name="tiny")
    svc.add(symbol="B", list_name="tiny")
    with pytest.raises(WatchlistLimitError):
        svc.add(symbol="C", list_name="tiny")


def test_service_disabled_entries_excluded_from_limit(session):
    """disabled 항목은 cap 계산에서 제외된다."""
    svc = WatchlistService(session, list_limits={"tiny": 2}, max_enabled_total=999)
    svc.add(symbol="A", list_name="tiny", enabled=True)
    svc.add(symbol="B", list_name="tiny", enabled=False)
    # tiny: enabled=1 ≤ 2 → 추가 허용
    svc.add(symbol="C", list_name="tiny", enabled=True)
    # enabled 2개 도달 → 더 이상 enable 추가 불가
    with pytest.raises(WatchlistLimitError):
        svc.add(symbol="D", list_name="tiny", enabled=True)
    # disabled 는 여전히 허용
    svc.add(symbol="E", list_name="tiny", enabled=False)


def test_service_total_enabled_cap_blocks_across_lists(session):
    svc = WatchlistService(
        session,
        list_limits={"a": 10, "b": 10},
        max_enabled_total=3,
    )
    svc.add(symbol="A1", list_name="a")
    svc.add(symbol="A2", list_name="a")
    svc.add(symbol="B1", list_name="b")
    with pytest.raises(WatchlistLimitError):
        svc.add(symbol="B2", list_name="b")


def test_service_set_enabled_respects_limit(session):
    svc = WatchlistService(session, list_limits={"x": 2}, max_enabled_total=99)
    a = svc.add(symbol="A", list_name="x", enabled=True)
    b = svc.add(symbol="B", list_name="x", enabled=True)
    c = svc.add(symbol="C", list_name="x", enabled=False)  # disabled — cap 면제
    # c 를 enable 하려고 하면 cap 초과
    with pytest.raises(WatchlistLimitError):
        svc.set_enabled(c["id"], True)
    # a 를 disable → 한 자리 확보 → c enable 가능
    svc.set_enabled(a["id"], False)
    out = svc.set_enabled(c["id"], True)
    assert out["enabled"] is True
    # 사용 안 한 변수 가드
    assert b["enabled"] is True


def test_service_default_limits_match_spec():
    assert DEFAULT_LIST_LIMITS["default"]    == 50
    assert DEFAULT_LIST_LIMITS["majors"]     == 20
    assert DEFAULT_LIST_LIMITS["kimp_pairs"] == 100


# ── 7. summary() 와 API 응답 ─────────────────────────────────────

def test_service_summary_shape(session):
    svc = WatchlistService(session, max_enabled_total=42)
    svc.add(symbol="BTC", exchange="upbit",   list_name="majors", enabled=True)
    svc.add(symbol="ETH", exchange="upbit",   list_name="majors", enabled=False)
    svc.add(symbol="SOL", exchange="binance", list_name="default", enabled=True)
    s = svc.summary()
    assert s["total"]    == 3
    assert s["enabled"]  == 2
    assert s["disabled"] == 1
    assert s["by_exchange"]  == {"upbit": 1, "binance": 1}
    assert s["by_list_name"] == {"majors": 1, "default": 1}
    assert s["limits"]["max_enabled_total"] == 42
    assert s["limits"]["default"] == 50
    assert s["limits"]["majors"]  == 20
    assert s["limits"]["kimp_pairs"] == 100


def test_api_get_includes_summary(app_with_db):
    client = TestClient(app_with_db)
    r = client.get("/api/watchlist")
    body = r.json()
    assert "summary" in body
    assert set(body["summary"].keys()) >= {
        "total", "enabled", "disabled",
        "by_exchange", "by_list_name", "limits",
    }
    assert "max_enabled_total" in body["summary"]["limits"]


# ── 8. API 오류 매핑 (#14 보강) ──────────────────────────────────

def test_api_post_validation_returns_400(app_with_db):
    from app.core.config import get_settings
    token = get_settings().admin_token
    client = TestClient(app_with_db)
    # exchange 화이트리스트 외
    r = client.post(
        "/api/watchlist",
        json={"symbol": "BTC", "exchange": "ftx"},
        headers={"X-Admin-Token": token},
    )
    assert r.status_code == 400


def test_api_post_limit_returns_409(app_with_db, monkeypatch):
    """전체 enabled cap 초과 시 409 — 환경변수 1로 좁혀 검증."""
    monkeypatch.setenv("WATCHLIST_MAX_ENABLED_TOTAL", "1")
    from app.core.config import reset_settings_cache, get_settings
    reset_settings_cache()
    token = get_settings().admin_token
    H = {"X-Admin-Token": token}
    client = TestClient(app_with_db)
    r1 = client.post("/api/watchlist", json={"symbol": "BTC"}, headers=H)
    assert r1.status_code == 201
    r2 = client.post("/api/watchlist", json={"symbol": "ETH"}, headers=H)
    assert r2.status_code == 409
    reset_settings_cache()


def test_api_patch_disable_does_not_admin_bypass(app_with_db):
    client = TestClient(app_with_db)
    r = client.patch("/api/watchlist/1/disable")
    assert r.status_code == 401


# ── 9. Seed import (#14 보강) ────────────────────────────────────

def test_seed_import_is_idempotent(session, tmp_path):
    import json as _json
    from app.market.watchlist_seed import import_file

    p = tmp_path / "seed.json"
    p.write_text(_json.dumps({
        "list_name": "default",
        "entries": [
            {"symbol": "btc", "exchange": "UPBIT"},
            {"symbol": "ETH", "exchange": "upbit"},
        ],
    }), encoding="utf-8")

    r1 = import_file(session, p)
    assert r1.added == 2
    assert r1.skipped_duplicate == 0

    # 2회차: 동일 항목 → 모두 skip
    r2 = import_file(session, p)
    assert r2.added == 0
    assert r2.skipped_duplicate == 2

    svc = WatchlistService(session)
    rows = svc.list_entries()
    assert {r["symbol"] for r in rows} == {"BTC", "ETH"}
    assert all(r["exchange"] == "upbit" for r in rows)


def test_seed_import_skips_invalid(session, tmp_path):
    import json as _json
    from app.market.watchlist_seed import import_file

    p = tmp_path / "seed.json"
    p.write_text(_json.dumps({
        "list_name": "default",
        "entries": [
            {"symbol": "BTC", "exchange": "upbit"},
            {"symbol": "",    "exchange": "upbit"},      # invalid
            {"symbol": "SOL", "exchange": "ftx"},        # invalid (whitelist)
        ],
    }), encoding="utf-8")

    r = import_file(session, p)
    assert r.added == 1
    assert r.skipped_invalid == 2


def test_seed_files_load_successfully():
    """레포에 포함된 config/watchlists/*.json 가 형식상 올바르다."""
    from app.market.watchlist_seed import load_seed_file
    repo = Path(__file__).resolve().parents[2]
    for name in ("default.json", "majors.json", "kimp_pairs.json"):
        list_name, entries = load_seed_file(repo / "config" / "watchlists" / name)
        assert list_name
        assert len(entries) >= 1
        for e in entries:
            assert "symbol" in e
            assert "exchange" in e


# ── 10. CoinSymbol vs WatchlistEntry 역할 분리 회귀 ──────────────

def test_coin_symbol_and_watchlist_entry_are_distinct():
    """13번 #CoinSymbol 과 14번 WatchlistEntry 가 같은 테이블/모델로
    합쳐지지 않았음을 회귀 방지."""
    from app.db.models import CoinSymbol
    assert CoinSymbol.__tablename__ == "coin_symbol"
    assert WatchlistEntry.__tablename__ == "watchlist"
    assert CoinSymbol is not WatchlistEntry
    # 컬럼 차이 — Watchlist 에는 list_name, CoinSymbol 에는 base/quote.
    wl_cols = {c.name for c in WatchlistEntry.__table__.columns}
    cs_cols = {c.name for c in CoinSymbol.__table__.columns}
    assert "list_name" in wl_cols and "list_name" not in cs_cols
    assert "base" in cs_cols and "base" not in wl_cols
