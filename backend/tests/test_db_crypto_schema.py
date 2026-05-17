"""체크리스트 #13 — 코인 전용 DB 스키마 회귀 테스트.

검증 범위:
  1. ORM 모델 — coin_* 9개 테이블 insert/query
  2. 기본값
       - CoinSignal.used_for_order=False (advisory, CLAUDE.md §2.3)
       - CoinOrder.mode 기본값이 "LIVE"가 아니다 (CLAUDE.md §2.2/§2.6)
  3. unique 제약 — coin_symbol(exchange,symbol), coin_candle(exch,sym,int,ts),
                   coin_order(idempotency_key)
  4. 가격/수량 Numeric — float 누적 오차 없이 유지
  5. Alembic 0003 upgrade/downgrade
  6. ORM과 마이그레이션 컬럼 집합 일치
  7. 기존 모델 보존 회귀 (audit_events/orders/agent_decisions/positions/watchlist 그대로)
  8. 정적 금지 검증 — 새 파일에 broker.place_order / OrderExecutor / route_order /
                      API_SECRET / ACCESS_TOKEN / LIVE_TRADING=True 등 부재
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import (
    Base,
    AuditEvent, Order, AgentDecisionRecord, Position, WatchlistEntry,
    CoinSymbol, CoinCandle, CoinTick, CoinOrderbookSnapshot,
    CoinSignal, CoinOrder, CoinTrade, CoinPosition, CoinRiskEvent,
    reset_engine,
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── 1. ORM 모델 insert/query ─────────────────────────────────────

def test_coin_symbol_insert_and_unique(session: Session):
    s1 = CoinSymbol(exchange="upbit", symbol="KRW-BTC",
                    base="BTC", quote="KRW",
                    tick_size=Decimal("1000"), lot_size=Decimal("0.00000001"))
    session.add(s1)
    session.commit()

    out = session.execute(select(CoinSymbol)).scalars().first()
    assert out.exchange == "upbit"
    assert out.symbol == "KRW-BTC"
    assert out.status == "ACTIVE"
    assert out.meta == {}

    dup = CoinSymbol(exchange="upbit", symbol="KRW-BTC", base="BTC", quote="KRW")
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_coin_candle_insert_and_unique(session: Session):
    ts = _now()
    # SQLite는 Numeric을 REAL(float64)로 저장하므로 큰 정수부 + 많은 소수부
    # 조합은 비트 표현 한계가 있다. 본 테스트는 컬럼 형식이 Numeric으로
    # 유지되는지 + 정밀한 소수 값 자체가 보존되는지에 초점을 둔다.
    # (PostgreSQL 운영에서는 28,12 정밀도가 그대로 보존된다.)
    c = CoinCandle(exchange="binance", symbol="BTC/USDT", interval="1m", ts=ts,
                   open=Decimal("50000.5"),
                   high=Decimal("50100"),
                   low=Decimal("49900"),
                   close=Decimal("50050.25"),
                   volume=Decimal("0.125"))
    session.add(c)
    session.commit()

    out = session.execute(select(CoinCandle)).scalars().first()
    assert Decimal(str(out.open)) == Decimal("50000.5")
    assert Decimal(str(out.close)) == Decimal("50050.25")
    assert Decimal(str(out.volume)) == Decimal("0.125")
    # 컬럼 자체는 Numeric으로 정의되어 있어야 한다 (float가 아님).
    from sqlalchemy import Numeric
    assert isinstance(CoinCandle.__table__.c.open.type, Numeric)
    assert isinstance(CoinCandle.__table__.c.volume.type, Numeric)

    dup = CoinCandle(exchange="binance", symbol="BTC/USDT", interval="1m", ts=ts,
                     open=Decimal("1"), high=Decimal("1"),
                     low=Decimal("1"), close=Decimal("1"), volume=Decimal("0"))
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_coin_tick_and_orderbook_insert(session: Session):
    t = CoinTick(exchange="upbit", symbol="KRW-BTC", ts=_now(),
                 price=Decimal("80000000"), qty=Decimal("0.001"), side="BUY")
    session.add(t)

    ob = CoinOrderbookSnapshot(
        exchange="upbit", symbol="KRW-BTC", ts=_now(), depth=5,
        bids=[["80000000", "0.5"], ["79999000", "1.2"]],
        asks=[["80001000", "0.3"], ["80002000", "0.7"]],
    )
    session.add(ob)
    session.commit()

    out_t = session.execute(select(CoinTick)).scalars().first()
    out_ob = session.execute(select(CoinOrderbookSnapshot)).scalars().first()
    assert out_t.side == "BUY"
    assert out_ob.depth == 5
    assert out_ob.bids[0] == ["80000000", "0.5"]


def test_coin_signal_default_used_for_order_false(session: Session):
    """CoinSignal은 advisory — used_for_order 기본 False. (CLAUDE.md §2.3)"""
    sig = CoinSignal(exchange="binance", symbol="ETH/USDT",
                     strategy="trend_following", action="BUY",
                     confidence=0.7, reason="ma cross")
    session.add(sig)
    session.commit()

    out = session.execute(select(CoinSignal)).scalars().first()
    assert out.used_for_order is False
    assert out.source_kind == "strategy"
    assert out.tags == []
    assert out.meta == {}


def test_coin_signal_used_for_order_can_be_set_true(session: Session):
    """OrderGateway 경유 후 used_for_order=True로 표시 가능."""
    sig = CoinSignal(exchange="binance", symbol="ETH/USDT",
                     strategy="trend_following", action="BUY",
                     used_for_order=True)
    session.add(sig)
    session.commit()
    out = session.execute(select(CoinSignal)).scalars().first()
    assert out.used_for_order is True


def test_coin_order_default_mode_is_not_live(session: Session):
    """CoinOrder.mode 기본값은 LIVE가 아니어야 한다. (CLAUDE.md §2.2/§2.6)"""
    o = CoinOrder(idempotency_key="ck-1", exchange="binance",
                  symbol="BTC/USDT", side="BUY",
                  qty=Decimal("0.001"))
    session.add(o)
    session.commit()

    out = session.execute(select(CoinOrder)).scalars().first()
    assert out.mode != "LIVE"
    assert out.mode == "PAPER"
    assert out.status == "PENDING"
    assert out.order_type == "MARKET"
    assert out.filled_qty == Decimal("0")


def test_coin_order_idempotency_unique(session: Session):
    o1 = CoinOrder(idempotency_key="ck-dup", exchange="binance",
                   symbol="BTC/USDT", side="BUY", qty=Decimal("0.001"))
    session.add(o1)
    session.commit()
    o2 = CoinOrder(idempotency_key="ck-dup", exchange="binance",
                   symbol="BTC/USDT", side="BUY", qty=Decimal("0.001"))
    session.add(o2)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_coin_trade_and_position_insert(session: Session):
    tr = CoinTrade(order_id=1, exchange="binance", symbol="BTC/USDT",
                   side="BUY", qty=Decimal("0.001"), price=Decimal("50000"),
                   fee=Decimal("0.0001"), fee_asset="BTC")
    session.add(tr)
    pos = CoinPosition(exchange="binance", symbol="BTC/USDT", side="LONG",
                      qty=Decimal("0.001"), avg_entry_price=Decimal("50000"))
    session.add(pos)
    session.commit()

    out_tr = session.execute(select(CoinTrade)).scalars().first()
    out_pos = session.execute(select(CoinPosition)).scalars().first()
    assert out_tr.mode == "PAPER"
    assert out_tr.fee == Decimal("0.0001")
    assert out_pos.status == "OPEN"
    assert out_pos.mode == "PAPER"


def test_coin_risk_event_insert(session: Session):
    ev = CoinRiskEvent(kind="STALE_DATA", severity="WARN",
                       exchange="upbit", symbol="KRW-BTC",
                       reason="websocket reconnecting",
                       payload={"last_ts": "2026-05-17T01:00:00Z"})
    session.add(ev)
    session.commit()

    out = session.execute(select(CoinRiskEvent)).scalars().first()
    assert out.kind == "STALE_DATA"
    assert out.severity == "WARN"
    assert out.source_kind == "risk_manager"


# ── 2. 기존 모델/테이블 보존 회귀 ─────────────────────────────────

def test_legacy_models_still_exported():
    """기존 모델 export 제거 회귀 방지."""
    # 단순 import 가능성 확인
    assert AuditEvent is not None
    assert Order is not None
    assert AgentDecisionRecord is not None
    assert Position is not None
    assert WatchlistEntry is not None


def test_legacy_tables_present_in_metadata():
    legacy = {"audit_events", "orders", "agent_decisions", "positions", "watchlist"}
    present = set(Base.metadata.tables.keys())
    assert legacy.issubset(present), f"기존 테이블 누락: {legacy - present}"


def test_no_new_agent_memory_table():
    """기존 AgentMemory 시스템을 대체하는 새 agent_memory 테이블을 만들지 않는다."""
    # 본 마이그레이션 범위 내에서 별도 agent_memory 테이블을 신설하지 않았음을 확인.
    # (현재 metadata에 agent_memory 가 없어야 한다.)
    assert "agent_memory" not in Base.metadata.tables


# ── 3. Alembic upgrade/downgrade ──────────────────────────────────

def _alembic_config(db_path: Path):
    from alembic.config import Config
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location",
                        str(backend_root / "app" / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_alembic_upgrade_head_creates_coin_tables(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "coin_up.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()

    from alembic import command
    command.upgrade(_alembic_config(db_path), "head")

    eng = create_engine(f"sqlite:///{db_path}")
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    expected = {
        "coin_symbol", "coin_candle", "coin_tick", "coin_orderbook_snapshot",
        "coin_signal", "coin_order", "coin_trade", "coin_position", "coin_risk_event",
    }
    assert expected.issubset(tables), f"누락 테이블: {expected - tables}"
    # 기존 테이블도 함께 존재
    assert {"audit_events", "orders", "agent_decisions", "positions",
            "watchlist"}.issubset(tables)
    eng.dispose()
    reset_engine()


def test_alembic_downgrade_to_0002_removes_coin_tables(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "coin_down.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()

    from alembic import command
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0002")

    eng = create_engine(f"sqlite:///{db_path}")
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    for t in ("coin_symbol", "coin_candle", "coin_tick",
              "coin_orderbook_snapshot", "coin_signal", "coin_order",
              "coin_trade", "coin_position", "coin_risk_event"):
        assert t not in tables, f"downgrade 후에도 {t} 가 남아 있음"
    # 기존 테이블은 보존되어야 한다
    assert "audit_events" in tables
    assert "watchlist" in tables
    eng.dispose()
    reset_engine()


def test_alembic_columns_match_orm_for_coin_tables(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "coin_match.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()

    from alembic import command
    command.upgrade(_alembic_config(db_path), "head")

    eng = create_engine(f"sqlite:///{db_path}")
    insp = inspect(eng)
    coin_tables = [
        "coin_symbol", "coin_candle", "coin_tick", "coin_orderbook_snapshot",
        "coin_signal", "coin_order", "coin_trade", "coin_position", "coin_risk_event",
    ]
    for t in coin_tables:
        cols_db = {c["name"] for c in insp.get_columns(t)}
        cols_orm = {c.name for c in Base.metadata.tables[t].columns}
        missing = cols_orm - cols_db
        assert not missing, f"{t} 마이그레이션 누락 컬럼: {missing}"
    eng.dispose()
    reset_engine()


# ── 4. 정적 금지 문자열 검증 ─────────────────────────────────────

_FORBIDDEN = (
    "place_order(",
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

# 정적 금지 검증은 본 작업이 추가한 "프로덕션" 코드 파일에 한해 적용.
# 본 테스트 파일 자체는 needle 문자열을 검사 대상으로 보유하므로 제외한다.
_NEW_PROD_FILES = (
    Path("app/db/models.py"),
    Path("app/db/migrations/versions/0003_crypto_schema.py"),
)


def test_no_forbidden_strings_in_new_files():
    backend_root = Path(__file__).resolve().parent.parent
    for rel in _NEW_PROD_FILES:
        p = backend_root / rel
        if not p.exists():
            pytest.fail(f"파일 없음: {p}")
        text = p.read_text(encoding="utf-8")
        for needle in _FORBIDDEN:
            assert needle not in text, f"{rel} 에 금지 문자열 발견: {needle!r}"


def test_no_secret_columns_in_coin_models():
    """API Key/Secret/Token/계좌번호 저장 컬럼이 없어야 한다. (CLAUDE.md §2.1)"""
    secret_like = ("api_key", "api_secret", "secret", "token",
                   "passphrase", "private_key", "access_token", "account_number")
    coin_tables = [
        "coin_symbol", "coin_candle", "coin_tick", "coin_orderbook_snapshot",
        "coin_signal", "coin_order", "coin_trade", "coin_position", "coin_risk_event",
    ]
    for t in coin_tables:
        col_names = {c.name.lower() for c in Base.metadata.tables[t].columns}
        for needle in secret_like:
            assert needle not in col_names, \
                f"{t} 에 secret 의심 컬럼 발견: {needle!r}"
