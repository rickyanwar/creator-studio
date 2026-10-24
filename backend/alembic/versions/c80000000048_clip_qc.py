"""clip_qc

Revision ID: c80000000048
Revises: c80000000047
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000048"
down_revision = "c80000000047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "target_fanpages",
        sa.Column("ai_video_clip_qc_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_table(
        "video_clip_qc",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_job_id", sa.Integer(), sa.ForeignKey("video_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("clip_index", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("clip_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("issues", sa.JSON(), nullable=True),
        sa.Column("major_count", sa.Integer(), nullable=True),
        sa.Column("minor_count", sa.Integer(), nullable=True),
        sa.Column("verdict", sa.String(20), nullable=True),
        sa.Column("next_prompt", sa.Text(), nullable=True),
        sa.Column("use_attempt", sa.Integer(), nullable=True),
        sa.Column("waiting_reason", sa.Text(), nullable=True),
        sa.Column("processing_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("video_job_id", "generation", "clip_index", "attempt", name="uq_video_clip_qc_attempt"),
    )
    op.create_index("ix_video_clip_qc_video_job_id", "video_clip_qc", ["video_job_id"])


def downgrade() -> None:
    op.drop_index("ix_video_clip_qc_video_job_id", table_name="video_clip_qc")
    op.drop_table("video_clip_qc")
    op.drop_column("target_fanpages", "ai_video_clip_qc_enabled")
