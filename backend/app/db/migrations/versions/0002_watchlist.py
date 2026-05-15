"""watchlist table

체크리스트 #14 Watchlist/Universe.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-10
"""
from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watchlist",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("list_name", sa.String(32), nullable=False, server_default="default"),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("exchange", sa.String(16), nullable=False, server_default="upbit"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("max_notional_usdt_override", sa.Float, nullable=True),
        sa.Column("tags", sa.JSON, nullable=False),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("list_name", "symbol", "exchange",
                            name="uq_watchlist_list_symbol_exchange"),
    )
    op.create_index("ix_watchlist_list_name", "watchlist", ["list_name"])
    op.create_index("ix_watchlist_symbol", "watchlist", ["symbol"])
    op.create_index("ix_watchlist_enabled", "watchlist", ["enabled"])


def downgrade() -> None:
    op.drop_index("ix_watchlist_enabled", table_name="watchlist")
    op.drop_index("ix_watchlist_symbol", table_name="watchlist")
    op.drop_index("ix_watchlist_list_name", table_name="watchlist")
    op.drop_table("watchlist")
