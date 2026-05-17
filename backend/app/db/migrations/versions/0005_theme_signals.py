"""theme signals

체크리스트 #19 — Trend/News/Theme 정규화 레코드.

신규 테이블: theme_signals
  - source: trend / news / disclosure / theme / macro_fx / other
  - 중복 제거: (source, provider, signal_id) + (source, provider, content_hash)
  - used_for_order: 영구 False (advisory 도 아닌 context 전용)
  - direct_order_allowed: 영구 False (CLAUDE.md §2.3)

원칙:
  - 기존 0001~0004 마이그레이션 / 테이블은 건드리지 않는다.
  - secret/PII 컬럼 없음 (CLAUDE.md §2.1).
  - BUY/SELL/ENTER/EXIT 같은 action 컬럼 없음 — 본 레코드는 매매 신호가 아니다.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-18
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "theme_signals",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("signal_id", sa.String(128), nullable=True),
        sa.Column("theme", sa.String(64), nullable=False, server_default=""),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("url", sa.Text, nullable=False, server_default=""),
        sa.Column("related_symbols", sa.JSON, nullable=False),
        sa.Column("related_keywords", sa.JSON, nullable=False),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("sentiment", sa.Float, nullable=True),
        sa.Column("risk_flags", sa.JSON, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "used_for_order",
            sa.Boolean, nullable=False, server_default=sa.false(),
        ),
        sa.Column(
            "direct_order_allowed",
            sa.Boolean, nullable=False, server_default=sa.false(),
        ),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("raw_payload", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source", "provider", "signal_id",
            name="uq_theme_signals_source_provider_signal_id",
        ),
        sa.UniqueConstraint(
            "source", "provider", "content_hash",
            name="uq_theme_signals_source_provider_content_hash",
        ),
    )
    op.create_index("ix_theme_signals_source", "theme_signals", ["source"])
    op.create_index("ix_theme_signals_provider", "theme_signals", ["provider"])
    op.create_index("ix_theme_signals_signal_id", "theme_signals", ["signal_id"])
    op.create_index("ix_theme_signals_theme", "theme_signals", ["theme"])
    op.create_index("ix_theme_signals_published_at", "theme_signals", ["published_at"])
    op.create_index("ix_theme_signals_collected_at", "theme_signals", ["collected_at"])
    op.create_index("ix_theme_signals_content_hash", "theme_signals", ["content_hash"])
    op.create_index("ix_theme_signals_used_for_order", "theme_signals", ["used_for_order"])
    op.create_index("ix_theme_signals_theme_source", "theme_signals", ["theme", "source"])


def downgrade() -> None:
    op.drop_index("ix_theme_signals_theme_source", table_name="theme_signals")
    op.drop_index("ix_theme_signals_used_for_order", table_name="theme_signals")
    op.drop_index("ix_theme_signals_content_hash", table_name="theme_signals")
    op.drop_index("ix_theme_signals_collected_at", table_name="theme_signals")
    op.drop_index("ix_theme_signals_published_at", table_name="theme_signals")
    op.drop_index("ix_theme_signals_theme", table_name="theme_signals")
    op.drop_index("ix_theme_signals_signal_id", table_name="theme_signals")
    op.drop_index("ix_theme_signals_provider", table_name="theme_signals")
    op.drop_index("ix_theme_signals_source", table_name="theme_signals")
    op.drop_table("theme_signals")
