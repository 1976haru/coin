"""SQLAlchemy ORM 모델 — 체크리스트 #13 Database Schema, #14 Watchlist.

테이블:
  - audit_events       : AuditLog의 영속 backing store (#13)
  - orders             : 주문 lifecycle 레코드 (idempotency_key unique) (#13)
  - agent_decisions    : Agent 판단 영속 레코드 (is_order_intent 기본 false, CLAUDE.md §2.3) (#13)
  - positions          : 포지션 (OPEN/CLOSING/CLOSED) (#13)
  - watchlist          : 거래 대상 심볼 universe (list_name, symbol, exchange) (#14)

코인 전용 (#13 crypto schema, docs/crypto_database_schema.md):
  - coin_symbol              : 거래소-심볼 마스터
  - coin_candle              : OHLCV 봉 (exchange,symbol,interval,ts unique)
  - coin_tick                : 체결 틱
  - coin_orderbook_snapshot  : 호가창 스냅샷
  - coin_signal              : 전략 advisory 신호 (used_for_order 기본 False)
  - coin_order               : paper/mock/research 주문 추적 (mode 기본 PAPER, LIVE 아님)
  - coin_trade               : 체결 fill 레코드
  - coin_position            : 코인 포지션
  - coin_risk_event          : 코인 리스크/가드 이벤트

규칙:
  - 모든 시각 컬럼은 timezone-aware UTC
  - secret/PII는 컬럼으로 직접 저장 금지. payload(JSON)는 redaction 거친 사본만.
  - coin_* 가격/수량은 Numeric(28,12) — float 반올림 손실 회피
  - CoinSignal은 advisory. 주문 의도는 OrderGateway 경유 후 used_for_order 표시.
  - API Key/Secret/Token/계좌번호 저장용 컬럼은 존재하지 않는다 (CLAUDE.md §2.1).
"""
from __future__ import annotations
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime, JSON, Numeric,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import DeclarativeBase


# 코인 가격/수량 공통 정밀도
_COIN_PRICE = Numeric(28, 12)
_COIN_QTY   = Numeric(28, 12)


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


# ── 체크리스트 #13 코인 전용 스키마 ─────────────────────────────────
#
# 설계 원칙 (docs/crypto_database_schema.md):
#   - coin_ prefix로 기존 주식/공통 테이블과 분리
#   - 가격/수량은 Numeric(28,12). float 누적 오차 회피.
#   - CoinSignal은 advisory: used_for_order=False 기본. 직접 주문 트리거 아님.
#   - CoinOrder.mode 기본 "PAPER". LIVE는 별도 승격 절차에서만 허용. (CLAUDE.md §2.2/§2.6)
#   - source_kind/source_id/tags/meta 공통 컬럼으로 AgentMemory·전략·외부 입력을 느슨하게 연결.
#   - API Key/Secret/Token 저장 컬럼 금지 (CLAUDE.md §2.1).


class CoinSymbol(Base):
    """거래소-심볼 마스터. (exchange, symbol) unique."""

    __tablename__ = "coin_symbol"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    exchange    = Column(String(32), nullable=False, index=True)
    symbol      = Column(String(32), nullable=False, index=True)
    base        = Column(String(16), nullable=False, default="")
    quote       = Column(String(16), nullable=False, default="")
    status      = Column(String(16), nullable=False, default="ACTIVE", index=True)
    tick_size   = Column(_COIN_PRICE, nullable=True)
    lot_size    = Column(_COIN_QTY, nullable=True)
    min_notional = Column(_COIN_PRICE, nullable=True)
    meta        = Column(JSON, nullable=False, default=dict)
    created_at  = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at  = Column(DateTime(timezone=True), nullable=False,
                         default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("exchange", "symbol", name="uq_coin_symbol_exchange_symbol"),
    )


class CoinCandle(Base):
    """OHLCV 봉. (exchange, symbol, interval, ts) unique."""

    __tablename__ = "coin_candle"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    exchange  = Column(String(32), nullable=False)
    symbol    = Column(String(32), nullable=False)
    interval  = Column(String(16), nullable=False)  # 1m / 5m / 1h / 1d ...
    ts        = Column(DateTime(timezone=True), nullable=False)
    open      = Column(_COIN_PRICE, nullable=False)
    high      = Column(_COIN_PRICE, nullable=False)
    low       = Column(_COIN_PRICE, nullable=False)
    close     = Column(_COIN_PRICE, nullable=False)
    volume    = Column(_COIN_QTY, nullable=False)
    quote_volume = Column(_COIN_QTY, nullable=True)
    trades_count = Column(Integer, nullable=True)
    source    = Column(String(32), nullable=False, default="research")  # research/paper/mock
    meta      = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("exchange", "symbol", "interval", "ts",
                         name="uq_coin_candle_exch_sym_int_ts"),
        Index("ix_coin_candle_sym_int_ts", "symbol", "interval", "ts"),
    )


class CoinTick(Base):
    """체결 틱 (trade tick). 시계열로 누적 저장."""

    __tablename__ = "coin_tick"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    exchange  = Column(String(32), nullable=False)
    symbol    = Column(String(32), nullable=False)
    ts        = Column(DateTime(timezone=True), nullable=False)
    price     = Column(_COIN_PRICE, nullable=False)
    qty       = Column(_COIN_QTY, nullable=False)
    side      = Column(String(8), nullable=False, default="")  # BUY/SELL/""
    trade_uid = Column(String(64), nullable=True)
    source    = Column(String(32), nullable=False, default="research")
    meta      = Column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_coin_tick_exch_sym_ts", "exchange", "symbol", "ts"),
    )


class CoinOrderbookSnapshot(Base):
    """호가창 스냅샷. bids/asks는 [[price, qty], ...] JSON."""

    __tablename__ = "coin_orderbook_snapshot"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    exchange  = Column(String(32), nullable=False)
    symbol    = Column(String(32), nullable=False)
    ts        = Column(DateTime(timezone=True), nullable=False)
    depth     = Column(Integer, nullable=False, default=0)
    bids      = Column(JSON, nullable=False, default=list)
    asks      = Column(JSON, nullable=False, default=list)
    source    = Column(String(32), nullable=False, default="research")
    meta      = Column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_coin_ob_exch_sym_ts", "exchange", "symbol", "ts"),
    )


class CoinSignal(Base):
    """전략 advisory 신호.

    used_for_order 기본 False. CLAUDE.md §2.3: AI/Strategy는 직접 주문하지 않으며,
    OrderGateway 경유 후 주문이 생성되면 그때 used_for_order=True로 갱신한다.
    """

    __tablename__ = "coin_signal"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    ts          = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    exchange    = Column(String(32), nullable=False, index=True)
    symbol      = Column(String(32), nullable=False, index=True)
    strategy    = Column(String(64), nullable=False, default="")
    action      = Column(String(16), nullable=False, default="HOLD")  # BUY/SELL/HOLD/WATCH
    confidence  = Column(Float, nullable=False, default=0.0)
    reason      = Column(Text, nullable=False, default="")
    used_for_order = Column(Boolean, nullable=False, default=False, index=True)
    source_kind = Column(String(32), nullable=False, default="strategy")
    source_id   = Column(String(64), nullable=True)
    tags        = Column(JSON, nullable=False, default=list)
    meta        = Column(JSON, nullable=False, default=dict)


class CoinOrder(Base):
    """코인 주문 추적 — paper/mock/shadow/research용.

    mode 기본 "PAPER". 실거래 활성화는 CLAUDE.md §2.6 승격 절차로만 허용.
    idempotency_key로 중복 방지.
    """

    __tablename__ = "coin_order"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    idempotency_key   = Column(String(64), nullable=False, unique=True, index=True)
    ts_created        = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    ts_submitted      = Column(DateTime(timezone=True), nullable=True)
    ts_filled         = Column(DateTime(timezone=True), nullable=True)
    mode              = Column(String(16), nullable=False, default="PAPER", index=True)
    exchange          = Column(String(32), nullable=False, index=True)
    symbol            = Column(String(32), nullable=False, index=True)
    side              = Column(String(8), nullable=False)
    order_type        = Column(String(16), nullable=False, default="MARKET")
    qty               = Column(_COIN_QTY, nullable=False)
    price             = Column(_COIN_PRICE, nullable=True)
    filled_qty        = Column(_COIN_QTY, nullable=False, default=0)
    avg_fill_price    = Column(_COIN_PRICE, nullable=True)
    status            = Column(String(16), nullable=False, default="PENDING", index=True)
    signal_id         = Column(Integer, nullable=True, index=True)
    source_kind       = Column(String(32), nullable=False, default="strategy")
    source_id         = Column(String(64), nullable=True)
    tags              = Column(JSON, nullable=False, default=list)
    meta              = Column(JSON, nullable=False, default=dict)


class CoinTrade(Base):
    """체결 fill 레코드. 한 주문이 N개 fill로 쪼개질 수 있어 별도 테이블."""

    __tablename__ = "coin_trade"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    ts          = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    order_id    = Column(Integer, nullable=True, index=True)
    exchange    = Column(String(32), nullable=False, index=True)
    symbol      = Column(String(32), nullable=False, index=True)
    side        = Column(String(8), nullable=False)
    qty         = Column(_COIN_QTY, nullable=False)
    price       = Column(_COIN_PRICE, nullable=False)
    fee         = Column(_COIN_PRICE, nullable=False, default=0)
    fee_asset   = Column(String(16), nullable=False, default="")
    mode        = Column(String(16), nullable=False, default="PAPER", index=True)
    trade_uid   = Column(String(64), nullable=True)
    meta        = Column(JSON, nullable=False, default=dict)


class CoinPosition(Base):
    """코인 포지션 (mode별 / 거래소별 / 심볼별)."""

    __tablename__ = "coin_position"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    exchange          = Column(String(32), nullable=False, index=True)
    symbol            = Column(String(32), nullable=False, index=True)
    side              = Column(String(8), nullable=False, default="LONG")
    qty               = Column(_COIN_QTY, nullable=False, default=0)
    avg_entry_price   = Column(_COIN_PRICE, nullable=True)
    realized_pnl      = Column(_COIN_PRICE, nullable=False, default=0)
    unrealized_pnl    = Column(_COIN_PRICE, nullable=False, default=0)
    status            = Column(String(16), nullable=False, default="OPEN", index=True)
    mode              = Column(String(16), nullable=False, default="PAPER", index=True)
    opened_at         = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    closed_at         = Column(DateTime(timezone=True), nullable=True)
    strategy          = Column(String(64), nullable=False, default="")
    meta              = Column(JSON, nullable=False, default=dict)


class CoinRiskEvent(Base):
    """코인 리스크/가드 이벤트 (kill switch, freshness fail, 호가 이상 등)."""

    __tablename__ = "coin_risk_event"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    ts          = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    kind        = Column(String(64), nullable=False, index=True)
    severity    = Column(String(16), nullable=False, default="INFO", index=True)
    exchange    = Column(String(32), nullable=True)
    symbol      = Column(String(32), nullable=True)
    reason      = Column(Text, nullable=False, default="")
    source_kind = Column(String(32), nullable=False, default="risk_manager")
    source_id   = Column(String(64), nullable=True)
    payload     = Column(JSON, nullable=False, default=dict)
