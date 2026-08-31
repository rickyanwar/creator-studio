"""add Mode 4 discussion_content_ideas staging queue + discussion_label_mode

Mode 4: mirrors Mode 5's PinterestContentIdea staging pattern (see
3136ab873a6d_add_mode5_pinterest.py) — discussion.py's 3-tier topic
selection (news/evergreen/general-knowledge) + copywriting now happens at
"topup" time into a reviewable/editable DiscussionContentIdea row, consumed
FIFO on the fanpage's own pacing into a PublishJob — see app/tasks/discussion.py.

discussion_label_mode lets a fanpage restrict which label style(s)
("DISCUSSION" question-style vs "HOT TAKE" declarative-style) the AI is
allowed to pick, same string-enum convention as discussion_topic_mode /
pinterest_source_mode.

Revision ID: 8cf01a8c836c
Revises: 2c8a214c0b39
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "8cf01a8c836c"
down_revision = "2c8a214c0b39"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "target_fanpages",
        sa.Column("discussion_label_mode", sa.String(length=16), nullable=False, server_default="both"),
    )

    op.create_table(
        "discussion_content_ideas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fanpage_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("subject_name", sa.String(length=256), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_article_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["fanpage_id"], ["target_fanpages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_article_id"], ["scraped_articles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_discussion_content_ideas_fanpage_id", "discussion_content_ideas", ["fanpage_id"])
    op.create_index("ix_discussion_content_ideas_status", "discussion_content_ideas", ["status"])
    op.create_index("ix_discussion_content_ideas_created_at", "discussion_content_ideas", ["created_at"])


def downgrade():
    op.drop_index("ix_discussion_content_ideas_created_at", table_name="discussion_content_ideas")
    op.drop_index("ix_discussion_content_ideas_status", table_name="discussion_content_ideas")
    op.drop_index("ix_discussion_content_ideas_fanpage_id", table_name="discussion_content_ideas")
    op.drop_table("discussion_content_ideas")
    op.drop_column("target_fanpages", "discussion_label_mode")
