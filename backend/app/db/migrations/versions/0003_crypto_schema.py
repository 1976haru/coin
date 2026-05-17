"""crypto schema (coin_* tables)

체크리스트 #13 — 코인 전용 데이터 스키마.

신규 테이블 (9개):
  coin_symbol, coin_candle, coin_tick, coin_orderbook_snapshot,
  coin_signal, coin_order, coin_trade, coin_position, coin_risk_event

원칙:
  - 기존 0001/0002 마이그레이션과 모델은 건드리지 않는다.
  - AgentMemory는 기존 시스템 재사용. 본 마이그레이션은 새 agent_memory를 만들지 않는다.
  - 가격/수량은 Numeric(28,12). float 누적 오차 회피.
  - CoinSignal.used_for_order 기본 False (advisory)
  - CoinOrder.mode 기본 "PAPER" (LIVE 아님)
  - API Key/Secret/Token 저장 컬럼 없음 (CLAUDE.md §2.1)

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-17
"""
from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


_PRICE = sa.Numeric(28, 12)
_QTY   = sa.Numeric(28, 12)


def upgrade() -> None:
    # coin_symbol ──────────────────────────────────────────────────
    op.create_table(
        "coin_symbol",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("base", sa.String(16), nullable=False, server_default=""),
        sa.Column("quote", sa.String(16), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("tick_size", _PRICE, nullable=True),
        sa.Column("lot_size", _QTY, nullable=True),
        sa.Column("min_notional", _PRICE, nullable=True),
        sa.Column("meta", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("exchange", "symbol",
                            name="uq_coin_symbol_exchange_symbol"),
    )
    op.create_index("ix_coin_symbol_exchange", "coin_symbol", ["exchange"])
    op.create_index("ix_coin_symbol_symbol", "coin_symbol", ["symbol"])
    op.create_index("ix_coin_symbol_status", "coin_symbol", ["status"])

    # coin_candle ──────────────────────────────────────────────────
    op.create_table(
        "coin_candle",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("interval", sa.String(16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", _PRICE, nullable=False),
        sa.Column("high", _PRICE, nullable=False),
        sa.Column("low", _PRICE, nullable=False),
        sa.Column("close", _PRICE, nullable=False),
        sa.Column("volume", _QTY, nullable=False),
        sa.Column("quote_volume", _QTY, nullable=True),
        sa.Column("trades_count", sa.Integer, nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="research"),
        sa.Column("meta", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("exchange", "symbol", "interval", "ts",
                            name="uq_coin_candle_exch_sym_int_ts"),
    )
    op.create_index("ix_coin_candle_sym_int_ts",
                    "coin_candle", ["symbol", "interval", "ts"])

    # coin_tick ────────────────────────────────────────────────────
    op.create_table(
        "coin_tick",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", _PRICE, nullable=False),
        sa.Column("qty", _QTY, nullable=False),
        sa.Column("side", sa.String(8), nullable=False, server_default=""),
        sa.Column("trade_uid", sa.String(64), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="research"),
        sa.Column("meta", sa.JSON, nullable=False),
    )
    op.create_index("ix_coin_tick_exch_sym_ts",
                    "coin_tick", ["exchange", "symbol", "ts"])

    # coin_orderbook_snapshot ──────────────────────────────────────
    op.create_table(
        "coin_orderbook_snapshot",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("depth", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bids", sa.JSON, nullable=False),
        sa.Column("asks", sa.JSON, nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="research"),
        sa.Column("meta", sa.JSON, nullable=False),
    )
    op.create_index("ix_coin_ob_exch_sym_ts",
                    "coin_orderbook_snapshot", ["exchange", "symbol", "ts"])

    # coin_signal ──────────────────────────────────────────────────
    op.create_table(
        "coin_signal",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("strategy", sa.String(64), nullable=False, server_default=""),
        sa.Column("action", sa.String(16), nullable=False, server_default="HOLD"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        # advisory 기본값 — CLAUDE.md §2.3 (AI/Strategy는 직접 주문하지 않는다)
        sa.Column("used_for_order", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("source_kind", sa.String(32), nullable=False, server_default="strategy"),
        sa.Column("source_id", sa.String(64), nullable=True),
        sa.Column("tags", sa.JSON, nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
    )
    op.create_index("ix_coin_signal_ts", "coin_signal", ["ts"])
    op.create_index("ix_coin_signal_exchange", "coin_signal", ["exchange"])
    op.create_index("ix_coin_signal_symbol", "coin_signal", ["symbol"])
    op.create_index("ix_coin_signal_used_for_order",
                    "coin_signal", ["used_for_order"])

    # coin_order ───────────────────────────────────────────────────
    op.create_table(
        "coin_order",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("ts_created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ts_submitted", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ts_filled", sa.DateTime(timezone=True), nullable=True),
        # 기본 PAPER — LIVE 아님 (CLAUDE.md §2.2/§2.6)
        sa.Column("mode", sa.String(16), nullable=False, server_default="PAPER"),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("order_type", sa.String(16), nullable=False, server_default="MARKET"),
        sa.Column("qty", _QTY, nullable=False),
        sa.Column("price", _PRICE, nullable=True),
        sa.Column("filled_qty", _QTY, nullable=False, server_default="0"),
        sa.Column("avg_fill_price", _PRICE, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("signal_id", sa.Integer, nullable=True),
        sa.Column("source_kind", sa.String(32), nullable=False, server_default="strategy"),
        sa.Column("source_id", sa.String(64), nullable=True),
        sa.Column("tags", sa.JSON, nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
    )
    op.create_index("ix_coin_order_idempotency_key",
                    "coin_order", ["idempotency_key"], unique=True)
    op.create_index("ix_coin_order_ts_created", "coin_order", ["ts_created"])
    op.create_index("ix_coin_order_mode", "coin_order", ["mode"])
    op.create_index("ix_coin_order_exchange", "coin_order", ["exchange"])
    op.create_index("ix_coin_order_symbol", "coin_order", ["symbol"])
    op.create_index("ix_coin_order_status", "coin_order", ["status"])
    op.create_index("ix_coin_order_signal_id", "coin_order", ["signal_id"])

    # coin_trade ───────────────────────────────────────────────────
    op.create_table(
        "coin_trade",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("order_id", sa.Integer, nullable=True),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("qty", _QTY, nullable=False),
        sa.Column("price", _PRICE, nullable=False),
        sa.Column("fee", _PRICE, nullable=False, server_default="0"),
        sa.Column("fee_asset", sa.String(16), nullable=False, server_default=""),
        sa.Column("mode", sa.String(16), nullable=False, server_default="PAPER"),
        sa.Column("trade_uid", sa.String(64), nullable=True),
        sa.Column("meta", sa.JSON, nullable=False),
    )
    op.create_index("ix_coin_trade_ts", "coin_trade", ["ts"])
    op.create_index("ix_coin_trade_order_id", "coin_trade", ["order_id"])
    op.create_index("ix_coin_trade_exchange", "coin_trade", ["exchange"])
    op.create_index("ix_coin_trade_symbol", "coin_trade", ["symbol"])
    op.create_index("ix_coin_trade_mode", "coin_trade", ["mode"])

    # coin_position ────────────────────────────────────────────────
    op.create_table(
        "coin_position",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False, server_default="LONG"),
        sa.Column("qty", _QTY, nullable=False, server_default="0"),
        sa.Column("avg_entry_price", _PRICE, nullable=True),
        sa.Column("realized_pnl", _PRICE, nullable=False, server_default="0"),
        sa.Column("unrealized_pnl", _PRICE, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("mode", sa.String(16), nullable=False, server_default="PAPER"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("strategy", sa.String(64), nullable=False, server_default=""),
        sa.Column("meta", sa.JSON, nullable=False),
    )
    op.create_index("ix_coin_position_exchange", "coin_position", ["exchange"])
    op.create_index("ix_coin_position_symbol", "coin_position", ["symbol"])
    op.create_index("ix_coin_position_status", "coin_position", ["status"])
    op.create_index("ix_coin_position_mode", "coin_position", ["mode"])

    # coin_risk_event ──────────────────────────────────────────────
    op.create_table(
        "coin_risk_event",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="INFO"),
        sa.Column("exchange", sa.String(32), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("source_kind", sa.String(32), nullable=False, server_default="risk_manager"),
        sa.Column("source_id", sa.String(64), nullable=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_coin_risk_event_ts", "coin_risk_event", ["ts"])
    op.create_index("ix_coin_risk_event_kind", "coin_risk_event", ["kind"])
    op.create_index("ix_coin_risk_event_severity", "coin_risk_event", ["severity"])


def downgrade() -> None:
    op.drop_index("ix_coin_risk_event_severity", table_name="coin_risk_event")
    op.drop_index("ix_coin_risk_event_kind", table_name="coin_risk_event")
    op.drop_index("ix_coin_risk_event_ts", table_name="coin_risk_event")
    op.drop_table("coin_risk_event")

    op.drop_index("ix_coin_position_mode", table_name="coin_position")
    op.drop_index("ix_coin_position_status", table_name="coin_position")
    op.drop_index("ix_coin_position_symbol", table_name="coin_position")
    op.drop_index("ix_coin_position_exchange", table_name="coin_position")
    op.drop_table("coin_position")

    op.drop_index("ix_coin_trade_mode", table_name="coin_trade")
    op.drop_index("ix_coin_trade_symbol", table_name="coin_trade")
    op.drop_index("ix_coin_trade_exchange", table_name="coin_trade")
    op.drop_index("ix_coin_trade_order_id", table_name="coin_trade")
    op.drop_index("ix_coin_trade_ts", table_name="coin_trade")
    op.drop_table("coin_trade")

    op.drop_index("ix_coin_order_signal_id", table_name="coin_order")
    op.drop_index("ix_coin_order_status", table_name="coin_order")
    op.drop_index("ix_coin_order_symbol", table_name="coin_order")
    op.drop_index("ix_coin_order_exchange", table_name="coin_order")
    op.drop_index("ix_coin_order_mode", table_name="coin_order")
    op.drop_index("ix_coin_order_ts_created", table_name="coin_order")
    op.drop_index("ix_coin_order_idempotency_key", table_name="coin_order")
    op.drop_table("coin_order")

    op.drop_index("ix_coin_signal_used_for_order", table_name="coin_signal")
    op.drop_index("ix_coin_signal_symbol", table_name="coin_signal")
    op.drop_index("ix_coin_signal_exchange", table_name="coin_signal")
    op.drop_index("ix_coin_signal_ts", table_name="coin_signal")
    op.drop_table("coin_signal")

    op.drop_index("ix_coin_ob_exch_sym_ts", table_name="coin_orderbook_snapshot")
    op.drop_table("coin_orderbook_snapshot")

    op.drop_index("ix_coin_tick_exch_sym_ts", table_name="coin_tick")
    op.drop_table("coin_tick")

    op.drop_index("ix_coin_candle_sym_int_ts", table_name="coin_candle")
    op.drop_table("coin_candle")

    op.drop_index("ix_coin_symbol_status", table_name="coin_symbol")
    op.drop_index("ix_coin_symbol_symbol", table_name="coin_symbol")
    op.drop_index("ix_coin_symbol_exchange", table_name="coin_symbol")
    op.drop_table("coin_symbol")
