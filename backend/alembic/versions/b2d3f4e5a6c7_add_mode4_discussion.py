"""add Mode 4 discussion fields to fanpages + discussion_topics table

Mode 4: AI-generated discussion / hot-take cards (badge + big question +
athlete photo, no yes/no buttons). Quota-driven per fanpage — the scheduler
creates up to discussion_daily_count cards per WIB day. Topics come from
scraped news, curated evergreen seeds (new discussion_topics table), or both.

Revision ID: b2d3f4e5a6c7
Revises: a1c2e3d4f5b6
Create Date: 2026-08-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "b2d3f4e5a6c7"
down_revision = "a1c2e3d4f5b6"
branch_labels = None
depends_on = None


def upgrade():
    # ── target_fanpages: Mode 4 config ──
    op.add_column(
        "target_fanpages",
        sa.Column("discussion_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    # Reuse the existing publishmode enum type (do NOT recreate it).
    publishmode = postgresql.ENUM("auto", "manual_review", name="publishmode", create_type=False)
    op.add_column(
        "target_fanpages",
        sa.Column(
            "discussion_publish_mode",
            publishmode,
            nullable=False,
            server_default="manual_review",
        ),
    )
    op.add_column(
        "target_fanpages",
        sa.Column("discussion_daily_count", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "target_fanpages",
        sa.Column("discussion_topic_mode", sa.String(length=16), nullable=False, server_default="both"),
    )
    op.add_column(
        "target_fanpages",
        sa.Column("default_discussion_template_id", sa.Integer(), nullable=True),
    )

    # ── discussion_topics: evergreen debate seeds ──
    op.create_table(
        "discussion_topics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fanpage_id", sa.Integer(), nullable=False),
        sa.Column("seed_text", sa.Text(), nullable=False),
        sa.Column("subject_hint", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("times_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["fanpage_id"], ["target_fanpages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_discussion_topics_fanpage_id", "discussion_topics", ["fanpage_id"])
    op.create_index("ix_discussion_topics_last_used_at", "discussion_topics", ["last_used_at"])


def downgrade():
    op.drop_index("ix_discussion_topics_last_used_at", table_name="discussion_topics")
    op.drop_index("ix_discussion_topics_fanpage_id", table_name="discussion_topics")
    op.drop_table("discussion_topics")
    op.drop_column("target_fanpages", "default_discussion_template_id")
    op.drop_column("target_fanpages", "discussion_topic_mode")
    op.drop_column("target_fanpages", "discussion_daily_count")
    op.drop_column("target_fanpages", "discussion_publish_mode")
    op.drop_column("target_fanpages", "discussion_enabled")
