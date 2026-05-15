"""체크리스트 #13 Database Schema — 회귀 테스트.

검증 범위:
  1. ORM 모델 — insert/query, 기본값, unique 제약
  2. CLAUDE.md §2.3: agent_decisions.is_order_intent 기본 False
  3. session.py — DATABASE_URL 우선순위, reset_engine, session_scope
  4. Alembic — 0001_initial_schema 마이그레이션 upgrade/downgrade
  5. ORM 모델과 마이그레이션 테이블 집합 일치
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import (
    Base, AuditEvent, Order, AgentDecisionRecord, Position,
    reset_engine, get_database_url,
)


# ── 픽스처 ────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine, future=True) as s:
        yield s


# ── 1. ORM 모델 ──────────────────────────────────────────────────

def test_audit_event_insert_and_query(session: Session):
    ev = AuditEvent(event_type="ORDER_SUBMITTED",
                    payload={"symbol": "BTC", "side": "BUY"})
    session.add(ev)
    session.commit()
    out = session.execute(select(AuditEvent)).scalars().first()
    assert out.event_type == "ORDER_SUBMITTED"
    assert out.payload["symbol"] == "BTC"
    assert out.ts is not None


def test_order_unique_idempotency_key(session: Session):
    o1 = Order(idempotency_key="k-1", symbol="BTC/USDT", side="BUY",
               notional_usdt=50.0, leverage=1.0)
    session.add(o1)
    session.commit()
    o2 = Order(idempotency_key="k-1", symbol="BTC/USDT", side="BUY",
               notional_usdt=50.0, leverage=1.0)
    session.add(o2)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_order_default_status_pending(session: Session):
    o = Order(idempotency_key="k-2", symbol="BTC", side="BUY",
              notional_usdt=10.0)
    session.add(o)
    session.commit()
    out = session.get(Order, o.id)
    assert out.status == "PENDING"
    assert out.is_paper is True
    assert out.order_type == "MARKET"


def test_agent_decision_default_is_order_intent_false(session: Session):
    """CLAUDE.md §2.3: DB 레코드도 기본 False"""
    d = AgentDecisionRecord(action="HOLD", confidence=0.0, reason="test",
                            context={})
    session.add(d)
    session.commit()
    out = session.execute(select(AgentDecisionRecord)).scalars().first()
    assert out.is_order_intent is False
    assert out.risk_veto is False
    assert out.agent_role == "orchestrator"


def test_agent_decision_explicit_true_persists(session: Session):
    d = AgentDecisionRecord(action="BUY", confidence=0.9, reason="strong",
                            is_order_intent=True, context={"regime": "TREND"})
    session.add(d)
    session.commit()
    out = session.execute(select(AgentDecisionRecord)).scalars().first()
    assert out.is_order_intent is True
    assert out.context["regime"] == "TREND"


def test_position_status_default_open(session: Session):
    p = Position(symbol="BTC/USDT", side="LONG", entry_price=100000.0,
                 qty=0.001, notional_usdt=100.0,
                 entry_ts=datetime.now(timezone.utc))
    session.add(p)
    session.commit()
    out = session.execute(select(Position)).scalars().first()
    assert out.status == "OPEN"
    assert out.leverage == 1.0
    assert out.exit_ts is None


# ── 2. session.py ────────────────────────────────────────────────

def test_get_database_url_default(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_engine()
    assert "sqlite" in get_database_url()


def test_get_database_url_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    assert get_database_url() == "sqlite:///:memory:"


def test_session_scope_commits_and_rolls_back(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    reset_engine()
    from app.db import create_all_tables, session_scope, get_session_factory
    create_all_tables()

    # 정상 커밋
    with session_scope() as s:
        s.add(AuditEvent(event_type="OK", payload={"v": 1}))
    Sf = get_session_factory()
    with Sf() as s:
        rows = s.execute(select(AuditEvent)).scalars().all()
        assert len(rows) == 1

    # 예외 시 롤백
    with pytest.raises(RuntimeError):
        with session_scope() as s:
            s.add(AuditEvent(event_type="FAIL", payload={"v": 2}))
            raise RuntimeError("boom")
    with Sf() as s:
        rows = s.execute(select(AuditEvent)).scalars().all()
        assert len(rows) == 1, "rollback이 동작하지 않음"

    reset_engine()


# ── 3. Alembic 마이그레이션 ───────────────────────────────────────

def _alembic_config(db_path: Path):
    from alembic.config import Config
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location",
                        str(backend_root / "app" / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_alembic_upgrade_creates_all_tables(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "alembic_up.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()

    from alembic import command
    command.upgrade(_alembic_config(db_path), "head")

    eng = create_engine(f"sqlite:///{db_path}")
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    assert {"audit_events", "orders", "agent_decisions",
            "positions", "alembic_version"}.issubset(tables)
    eng.dispose()
    reset_engine()


def test_alembic_downgrade_removes_tables(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "alembic_down.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()

    from alembic import command
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    eng = create_engine(f"sqlite:///{db_path}")
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    assert "audit_events" not in tables
    assert "orders" not in tables
    assert "agent_decisions" not in tables
    assert "positions" not in tables
    eng.dispose()
    reset_engine()


def test_alembic_schema_matches_orm_models(tmp_path: Path, monkeypatch):
    """마이그레이션이 만든 테이블/컬럼 집합이 ORM 모델과 일치"""
    db_path = tmp_path / "match.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()

    from alembic import command
    command.upgrade(_alembic_config(db_path), "head")

    eng = create_engine(f"sqlite:///{db_path}")
    insp = inspect(eng)
    for table_name, table in Base.metadata.tables.items():
        cols_db = {c["name"] for c in insp.get_columns(table_name)}
        cols_orm = {c.name for c in table.columns}
        missing = cols_orm - cols_db
        assert not missing, f"{table_name} 마이그레이션 누락 컬럼: {missing}"
    eng.dispose()
    reset_engine()
