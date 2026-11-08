"""Track content produced exclusively for Group Share."""

from alembic import op
import sqlalchemy as sa

revision = "c80000000057"
down_revision = "c80000000056"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "fb_share_group_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fanpage_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("publish_job_id", sa.Integer(), nullable=True),
        sa.Column("video_job_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("handed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("publish_job_id IS NOT NULL OR video_job_id IS NOT NULL", name="ck_fb_share_group_jobs_job"),
    )
    op.create_index("ix_fb_share_group_jobs_fanpage_id", "fb_share_group_jobs", ["fanpage_id"])
    op.create_index("ix_fb_share_group_jobs_publish_job_id", "fb_share_group_jobs", ["publish_job_id"], unique=True)
    op.create_index("ix_fb_share_group_jobs_video_job_id", "fb_share_group_jobs", ["video_job_id"], unique=True)


def downgrade():
    op.drop_table("fb_share_group_jobs")
