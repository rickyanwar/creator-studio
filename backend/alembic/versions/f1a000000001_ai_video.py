"""AI video schema (hand-written).

Revision ID: f1a000000001
Revises: e1a000000001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f1a000000001"
down_revision = "e1a000000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE contenttype ADD VALUE IF NOT EXISTS 'ai_video'")
    op.create_table(
        "gflow_workers",
        sa.Column("worker_id", sa.String(64), primary_key=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("version", sa.String(32), nullable=True),
        sa.Column("max_concurrent", sa.Integer(), nullable=True),
        sa.Column("mem_available_mb", sa.Integer(), nullable=True),
        sa.Column("running_job_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    op.create_table(
        "gflow_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("worker_id", sa.String(64), nullable=False),
        sa.Column("profile", sa.String(100), nullable=False),
        sa.Column("email", sa.String(200), nullable=True),
        sa.Column("label", sa.String(100), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("authenticated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("credits", sa.Integer(), nullable=True),
        sa.Column("credits_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("worker_id", "profile", name="uq_gflow_accounts_worker_profile"),
    )
    op.create_table(
        "video_ideas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fanpage_id", sa.Integer(), sa.ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_key", sa.String(64), nullable=False),
        sa.Column("topic", sa.String(200), nullable=False),
        sa.Column("angle", sa.Text(), nullable=True),
        sa.Column("research_notes", sa.Text(), nullable=True),
        sa.Column("variables", sa.JSON(), nullable=True),
        sa.Column("clips", sa.Integer(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="owner"),
        sa.Column("token_id", sa.Integer(), sa.ForeignKey("api_tokens.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by", sa.String(64), nullable=True),
    )
    for column in ("fanpage_id", "status"):
        op.create_index(f"ix_video_ideas_{column}", "video_ideas", [column])
    op.create_table(
        "video_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idea_id", sa.Integer(), sa.ForeignKey("video_ideas.id", ondelete="SET NULL"), nullable=True),
        sa.Column("fanpage_id", sa.Integer(), sa.ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_key", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("model", sa.String(32), nullable=False),
        sa.Column("aspect", sa.String(8), nullable=False),
        sa.Column("clip_seconds", sa.Integer(), nullable=False),
        sa.Column("clips", sa.JSON(), nullable=False),
        sa.Column("credits_per_clip", sa.Integer(), nullable=False),
        sa.Column("credits_estimate", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("gflow_accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("flow_project_id", sa.String(128), nullable=True),
        sa.Column("clips_done", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("clip_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("restarts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("not_before", sa.DateTime(), nullable=True),
        sa.Column("last_progress_at", sa.DateTime(), nullable=True),
        sa.Column("video_path", sa.String(512), nullable=True),
        sa.Column("video_url", sa.String(512), nullable=True),
        sa.Column("thumbnail_path", sa.String(512), nullable=True),
        sa.Column("thumbnail_url", sa.String(512), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("qc", sa.JSON(), nullable=True),
        sa.Column("publish_job_id", sa.Integer(), sa.ForeignKey("publish_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    for column in ("idea_id", "fanpage_id", "status", "account_id", "created_at"):
        op.create_index(f"ix_video_jobs_{column}", "video_jobs", [column])
    publishmode = postgresql.ENUM("auto", "manual_review", name="publishmode", create_type=False)
    op.add_column("target_fanpages", sa.Column("ai_video_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("target_fanpages", sa.Column("ai_video_publish_mode", publishmode, nullable=False, server_default="manual_review"))
    op.add_column("target_fanpages", sa.Column("ai_video_daily_count", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("target_fanpages", sa.Column("ai_video_min_score", sa.Integer(), nullable=True, server_default="70"))
    op.add_column("target_fanpages", sa.Column("ai_video_template", sa.String(64), nullable=True))
    op.add_column("target_fanpages", sa.Column("ai_video_clips", sa.Integer(), nullable=False, server_default="2"))
    op.add_column("settings", sa.Column("ai_video_worker_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("settings", sa.Column("ai_video_vision_qc_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("settings", sa.Column("ai_video_credit_reserve", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    for column in ("ai_video_credit_reserve", "ai_video_vision_qc_enabled", "ai_video_worker_enabled"):
        op.drop_column("settings", column)
    for column in ("ai_video_clips", "ai_video_template", "ai_video_min_score", "ai_video_daily_count", "ai_video_publish_mode", "ai_video_enabled"):
        op.drop_column("target_fanpages", column)
    op.drop_table("video_jobs")
    op.drop_table("video_ideas")
    op.drop_table("gflow_accounts")
    op.drop_table("gflow_workers")
    # PostgreSQL enum values cannot be dropped safely; leave contenttype.ai_video in place.
