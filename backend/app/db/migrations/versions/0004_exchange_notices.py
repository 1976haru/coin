"""exchange notices

체크리스트 #18 — 거래소 공지 정규화 레코드.

신규 테이블: exchange_notice
  - notice_type: DEPOSIT_WITHDRAWAL_SUSPENSION / CAUTION / DELISTING / LISTING /
                 MAINTENANCE / TRADING_SUSPENSION / POLICY / OTHER
  - severity: INFO / WARNING / HIGH / CRITICAL
  - 중복 제거: (exchange, notice_id) + (exchange, content_hash) UNIQUE
  - direct_order_allowed: 영구 False — 주문 행위 허가 아님 (CLAUDE.md §2.3)

원칙:
  - 기존 0001/0002/0003 마이그레이션과 테이블은 건드리지 않는다.
  - secret/PII 컬럼 없음 (CLAUDE.md §2.1).

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-18
"""
from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exchange_notice",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("notice_id", sa.String(128), nullable=True),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("url", sa.Text, nullable=False, server_default=""),
        sa.Column("category", sa.String(64), nullable=False, server_default=""),
        sa.Column("notice_type", sa.String(48), nullable=False, server_default="OTHER"),
        sa.Column("severity", sa.String(16), nullable=False, server_default="INFO"),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("symbols", sa.JSON, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("source_name", sa.String(64), nullable=False, server_default="mock"),
        sa.Column(
            "direct_order_allowed",
            sa.Boolean, nullable=False, server_default=sa.false(),
        ),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("raw_payload", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("exchange", "notice_id",
                            name="uq_exchange_notice_exchange_notice_id"),
        sa.UniqueConstraint("exchange", "content_hash",
                            name="uq_exchange_notice_exchange_content_hash"),
    )
    op.create_index("ix_exchange_notice_exchange",
                    "exchange_notice", ["exchange"])
    op.create_index("ix_exchange_notice_notice_id",
                    "exchange_notice", ["notice_id"])
    op.create_index("ix_exchange_notice_notice_type",
                    "exchange_notice", ["notice_type"])
    op.create_index("ix_exchange_notice_severity",
                    "exchange_notice", ["severity"])
    op.create_index("ix_exchange_notice_collected_at",
                    "exchange_notice", ["collected_at"])
    op.create_index("ix_exchange_notice_content_hash",
                    "exchange_notice", ["content_hash"])
    op.create_index("ix_exchange_notice_type_severity",
                    "exchange_notice", ["notice_type", "severity"])
    op.create_index("ix_exchange_notice_published_at",
                    "exchange_notice", ["published_at"])


def downgrade() -> None:
    op.drop_index("ix_exchange_notice_published_at", table_name="exchange_notice")
    op.drop_index("ix_exchange_notice_type_severity", table_name="exchange_notice")
    op.drop_index("ix_exchange_notice_content_hash", table_name="exchange_notice")
    op.drop_index("ix_exchange_notice_collected_at", table_name="exchange_notice")
    op.drop_index("ix_exchange_notice_severity", table_name="exchange_notice")
    op.drop_index("ix_exchange_notice_notice_type", table_name="exchange_notice")
    op.drop_index("ix_exchange_notice_notice_id", table_name="exchange_notice")
    op.drop_index("ix_exchange_notice_exchange", table_name="exchange_notice")
    op.drop_table("exchange_notice")
