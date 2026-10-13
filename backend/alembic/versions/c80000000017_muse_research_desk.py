"""research desk (Muse) pilot: pack refs and copy on video ideas, researcher settings, call log, feedback

Revision ID: c80000000017
Revises: c80000000016
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000017"
down_revision = "c80000000016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_ideas", sa.Column("client_ref", sa.String(64), nullable=True))
    op.add_column("video_ideas", sa.Column("title", sa.Text(), nullable=True))
    op.add_column("video_ideas", sa.Column("caption", sa.Text(), nullable=True))
    op.create_index(
        "uq_video_ideas_client_ref_fanpage", "video_ideas", ["client_ref", "fanpage_id"],
        unique=True, postgresql_where=sa.text("client_ref IS NOT NULL"),
    )

    op.add_column("settings", sa.Column("ai_video_researcher", sa.String(16), nullable=False, server_default="muse"))
    op.add_column("settings", sa.Column("muse_research_mode", sa.String(16), nullable=False, server_default="pilot"))
    op.add_column("settings", sa.Column("muse_pilot_packs", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("settings", sa.Column("research_references", sa.JSON(), nullable=True))
    op.add_column("settings", sa.Column("research_last_report", sa.JSON(), nullable=True))

    op.create_table(
        "research_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("endpoint", sa.String(32), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("token_id", sa.Integer(), sa.ForeignKey("api_tokens.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_research_calls_created_at", "research_calls", ["created_at"])

    op.create_table(
        "research_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("client_ref", sa.String(64), nullable=True),
        sa.Column("idea_id", sa.Integer(), sa.ForeignKey("video_ideas.id", ondelete="CASCADE"), nullable=True),
        sa.Column("author", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("token_id", sa.Integer(), sa.ForeignKey("api_tokens.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_research_feedback_created_at", "research_feedback", ["created_at"])
    op.create_index("ix_research_feedback_client_ref", "research_feedback", ["client_ref"])
    op.create_index("ix_research_feedback_idea_id", "research_feedback", ["idea_id"])


def downgrade() -> None:
    op.drop_index("ix_research_feedback_idea_id", table_name="research_feedback")
    op.drop_index("ix_research_feedback_client_ref", table_name="research_feedback")
    op.drop_index("ix_research_feedback_created_at", table_name="research_feedback")
    op.drop_table("research_feedback")
    op.drop_index("ix_research_calls_created_at", table_name="research_calls")
    op.drop_table("research_calls")
    for column in ("research_last_report", "research_references", "muse_pilot_packs", "muse_research_mode", "ai_video_researcher"):
        op.drop_column("settings", column)
    op.drop_index("uq_video_ideas_client_ref_fanpage", table_name="video_ideas")
    for column in ("caption", "title", "client_ref"):
        op.drop_column("video_ideas", column)
