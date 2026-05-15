"""initial schema

체크리스트 #13. audit_events, orders, agent_decisions, positions 4개 테이블 생성.

Revision ID: 0001
Revises:
Create Date: 2026-05-10
"""
from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # audit_events ─────────────────────────────────────────────────
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_audit_events_ts", "audit_events", ["ts"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])

    # orders ───────────────────────────────────────────────────────
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(32), nullable=False),
        sa.Column("notional_usdt", sa.Float, nullable=False),
        sa.Column("leverage", sa.Float, nullable=False, server_default="1"),
        sa.Column("order_type", sa.String(16), nullable=False, server_default="MARKET"),
        sa.Column("price", sa.Float, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("source", sa.String(32), nullable=False, server_default="system"),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("route", sa.String(32), nullable=True),
        sa.Column("filled_price", sa.Float, nullable=True),
        sa.Column("fee_usdt", sa.Float, nullable=True),
        sa.Column("slippage_pct", sa.Float, nullable=True),
        sa.Column("exchange_order_id", sa.String(64), nullable=True),
        sa.Column("is_paper", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("ts_created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ts_filled", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_orders_idempotency_key", "orders", ["idempotency_key"], unique=True)
    op.create_index("ix_orders_symbol", "orders", ["symbol"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_ts_created", "orders", ["ts_created"])

    # agent_decisions ──────────────────────────────────────────────
    op.create_table(
        "agent_decisions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agent_role", sa.String(32), nullable=False, server_default="orchestrator"),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("quality_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("risk_veto", sa.Boolean, nullable=False, server_default=sa.false()),
        # CLAUDE.md §2.3: AgentDecision은 is_order_intent=False 기본값 필수
        sa.Column("is_order_intent", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("explain_text", sa.Text, nullable=False, server_default=""),
        sa.Column("context", sa.JSON, nullable=False),
    )
    op.create_index("ix_agent_decisions_ts", "agent_decisions", ["ts"])
    op.create_index("ix_agent_decisions_agent_role", "agent_decisions", ["agent_role"])

    # positions ────────────────────────────────────────────────────
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("entry_price", sa.Float, nullable=False),
        sa.Column("qty", sa.Float, nullable=False),
        sa.Column("notional_usdt", sa.Float, nullable=False),
        sa.Column("leverage", sa.Float, nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("strategy", sa.String(32), nullable=False, server_default=""),
        sa.Column("entry_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_price", sa.Float, nullable=True),
        sa.Column("realized_pnl_pct", sa.Float, nullable=True),
        sa.Column("realized_pnl_usdt", sa.Float, nullable=True),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
    )
    op.create_index("ix_positions_symbol", "positions", ["symbol"])
    op.create_index("ix_positions_status", "positions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_positions_status", table_name="positions")
    op.drop_index("ix_positions_symbol", table_name="positions")
    op.drop_table("positions")

    op.drop_index("ix_agent_decisions_agent_role", table_name="agent_decisions")
    op.drop_index("ix_agent_decisions_ts", table_name="agent_decisions")
    op.drop_table("agent_decisions")

    op.drop_index("ix_orders_ts_created", table_name="orders")
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_index("ix_orders_symbol", table_name="orders")
    op.drop_index("ix_orders_idempotency_key", table_name="orders")
    op.drop_table("orders")

    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_index("ix_audit_events_ts", table_name="audit_events")
    op.drop_table("audit_events")
