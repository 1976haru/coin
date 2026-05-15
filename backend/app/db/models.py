"""SQLAlchemy ORM 모델 — 체크리스트 #13 Database Schema, #14 Watchlist.

테이블:
  - audit_events       : AuditLog의 영속 backing store (#13)
  - orders             : 주문 lifecycle 레코드 (idempotency_key unique) (#13)
  - agent_decisions    : Agent 판단 영속 레코드 (is_order_intent 기본 false, CLAUDE.md §2.3) (#13)
  - positions          : 포지션 (OPEN/CLOSING/CLOSED) (#13)
  - watchlist          : 거래 대상 심볼 universe (list_name, symbol, exchange) (#14)

규칙:
  - 모든 시각 컬럼은 timezone-aware UTC
  - secret/PII는 컬럼으로 직접 저장 금지. payload(JSON)는 redaction 거친 사본만.
"""
from __future__ import annotations
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime, JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditEvent(Base):
    """기본 감사 이벤트 — AuditLog 메모리/CSV의 DB 영속 사본."""

    __tablename__ = "audit_events"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    ts         = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    payload    = Column(JSON, nullable=False, default=dict)


class Order(Base):
    """주문 lifecycle 레코드. idempotency_key로 unique 보장."""

    __tablename__ = "orders"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    idempotency_key   = Column(String(64), nullable=False, unique=True, index=True)
    symbol            = Column(String(32), nullable=False, index=True)
    side              = Column(String(32), nullable=False)
    notional_usdt     = Column(Float, nullable=False)
    leverage          = Column(Float, nullable=False, default=1.0)
    order_type        = Column(String(16), nullable=False, default="MARKET")
    price             = Column(Float, nullable=True)
    confidence        = Column(Float, nullable=False, default=0.0)
    reason            = Column(Text, nullable=False, default="")
    source            = Column(String(32), nullable=False, default="system")
    status            = Column(String(32), nullable=False, default="PENDING", index=True)
    route             = Column(String(32), nullable=True)
    filled_price      = Column(Float, nullable=True)
    fee_usdt          = Column(Float, nullable=True)
    slippage_pct      = Column(Float, nullable=True)
    exchange_order_id = Column(String(64), nullable=True)
    is_paper          = Column(Boolean, nullable=False, default=True)
    ts_created        = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    ts_filled         = Column(DateTime(timezone=True), nullable=True)


class AgentDecisionRecord(Base):
    """Agent 판단 영속 레코드.

    CLAUDE.md §2.3: is_order_intent 기본 False. AI 에이전트는 직접 주문하지 않으며,
    이 컬럼이 True여도 OrderGateway·PermissionGate·ApprovalQueue를 우회하지 않는다.
    """

    __tablename__ = "agent_decisions"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    ts              = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    agent_role      = Column(String(32), nullable=False, default="orchestrator", index=True)
    action          = Column(String(32), nullable=False)
    confidence      = Column(Float, nullable=False, default=0.0)
    reason          = Column(Text, nullable=False, default="")
    quality_score   = Column(Float, nullable=False, default=0.0)
    risk_veto       = Column(Boolean, nullable=False, default=False)
    is_order_intent = Column(Boolean, nullable=False, default=False)
    explain_text    = Column(Text, nullable=False, default="")
    context         = Column(JSON, nullable=False, default=dict)


class Position(Base):
    """포지션 레코드 (OPEN/CLOSING/CLOSED)."""

    __tablename__ = "positions"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    symbol            = Column(String(32), nullable=False, index=True)
    side              = Column(String(16), nullable=False)
    entry_price       = Column(Float, nullable=False)
    qty               = Column(Float, nullable=False)
    notional_usdt     = Column(Float, nullable=False)
    leverage          = Column(Float, nullable=False, default=1.0)
    status            = Column(String(16), nullable=False, default="OPEN", index=True)
    strategy          = Column(String(32), nullable=False, default="")
    entry_ts          = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    exit_ts           = Column(DateTime(timezone=True), nullable=True)
    exit_price        = Column(Float, nullable=True)
    realized_pnl_pct  = Column(Float, nullable=True)
    realized_pnl_usdt = Column(Float, nullable=True)
    note              = Column(Text, nullable=False, default="")


class WatchlistEntry(Base):
    """거래 대상 universe — 체크리스트 #14.

    여러 list_name으로 그룹핑(예: "kimp_pairs", "majors", "high_volume").
    enabled=False는 조회는 되지만 Strategy/Collector가 건너뛴다.
    max_notional_usdt_override 로 글로벌 한도(MAX_ORDER_NOTIONAL_USDT)를
    심볼별 더 엄격하게만 덮어쓸 수 있다 (확장은 RiskManager가 거부).
    """

    __tablename__ = "watchlist"

    id                          = Column(Integer, primary_key=True, autoincrement=True)
    list_name                   = Column(String(32), nullable=False, default="default", index=True)
    symbol                      = Column(String(32), nullable=False, index=True)
    exchange                    = Column(String(16), nullable=False, default="upbit")
    enabled                     = Column(Boolean, nullable=False, default=True, index=True)
    max_notional_usdt_override  = Column(Float, nullable=True)
    tags                        = Column(JSON, nullable=False, default=list)
    note                        = Column(Text, nullable=False, default="")
    created_at                  = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at                  = Column(DateTime(timezone=True), nullable=False,
                                          default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("list_name", "symbol", "exchange",
                         name="uq_watchlist_list_symbol_exchange"),
    )
