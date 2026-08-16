"""add ai_copy_events table

Tracks the outcome of every AI copy-generation call (news_copywriter's
generate_news_copy / generate_discussion_copy) so a degraded 9Router model
shows up on the Logs dashboard instead of only being visible in raw worker
container logs. See the 2026-08-16 incident: My-Combo silently truncating
JSON output dropped Mode 2's success rate to 1.1% with zero dashboard
visibility until VPS logs were grepped manually.

Revision ID: d7e8f9a0b1c2
Revises: c5a6b7c8d9e0
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d7e8f9a0b1c2"
down_revision = "c5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_copy_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("context", sa.String(length=32), nullable=False),
        sa.Column("fanpage_id", sa.Integer(), nullable=True),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("models_tried", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("final_provider", sa.String(length=16), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["fanpage_id"], ["target_fanpages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["article_id"], ["scraped_articles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_copy_events_created_at", "ai_copy_events", ["created_at"])
    op.create_index("ix_ai_copy_events_fanpage_id", "ai_copy_events", ["fanpage_id"])
    op.create_index("ix_ai_copy_events_outcome", "ai_copy_events", ["outcome"])


def downgrade():
    op.drop_index("ix_ai_copy_events_outcome", table_name="ai_copy_events")
    op.drop_index("ix_ai_copy_events_fanpage_id", table_name="ai_copy_events")
    op.drop_index("ix_ai_copy_events_created_at", table_name="ai_copy_events")
    op.drop_table("ai_copy_events")
