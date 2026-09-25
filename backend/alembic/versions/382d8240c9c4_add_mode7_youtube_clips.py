"""add Mode 7 YouTube clips: sources, videos, clip ideas, job/fanpage/settings columns

Mode 7: long YouTube videos (channel / playlist / single link) → AI-picked
highlights from the subtitle track → 9:16 clips → Facebook Reels. See
app/tasks/yt_clip.py.

Revision ID: 382d8240c9c4
Revises: e3e089f92d58
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "382d8240c9c4"
down_revision = "e3e089f92d58"
branch_labels = None
depends_on = None

_FANPAGE_COLUMNS = [
    ("yt_clip_enabled", sa.Boolean(), "false"),
    ("yt_clip_daily_count", sa.Integer(), "2"),
    ("yt_clip_min_s", sa.Integer(), "60"),
    ("yt_clip_max_s", sa.Integer(), "120"),
    ("yt_clip_per_video", sa.Integer(), "3"),
    ("yt_clip_min_score", sa.Integer(), "6"),
    ("yt_clip_max_video_age_days", sa.Integer(), "7"),
    ("yt_clip_captions", sa.Boolean(), "true"),
    ("yt_clip_watermark", sa.Boolean(), "true"),
]

_JOB_COLUMNS = [
    ("yt_video_id", sa.String(length=16)),
    ("clip_start_s", sa.Float()),
    ("clip_end_s", sa.Float()),
    ("video_path", sa.String(length=512)),
    ("video_url", sa.String(length=512)),
    ("video_thumbnail_path", sa.String(length=512)),
    ("video_thumbnail_url", sa.String(length=512)),
    ("video_duration_s", sa.Float()),
]


def upgrade():
    # ── target_fanpages: Mode 7 config ──
    for name, type_, default in _FANPAGE_COLUMNS:
        op.add_column("target_fanpages", sa.Column(name, type_, nullable=False, server_default=default))
    publishmode = postgresql.ENUM("auto", "manual_review", name="publishmode", create_type=False)
    op.add_column(
        "target_fanpages",
        sa.Column("yt_clip_publish_mode", publishmode, nullable=False, server_default="manual_review"),
    )

    # ── settings: YouTube access fallback + circuit breaker ──
    op.add_column("settings", sa.Column("youtube_cookies_encrypted", sa.Text(), nullable=True))
    op.add_column("settings", sa.Column("youtube_proxy", sa.String(length=512), nullable=True))
    op.add_column("settings", sa.Column("youtube_blocked_until", sa.DateTime(), nullable=True))
    op.add_column("settings", sa.Column("youtube_last_error", sa.Text(), nullable=True))

    # ── yt_clip_sources ──
    op.create_table(
        "yt_clip_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fanpage_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("channel_id", sa.String(length=64), nullable=True),
        sa.Column("playlist_id", sa.String(length=64), nullable=True),
        sa.Column("video_id", sa.String(length=16), nullable=True),
        sa.Column("label", sa.String(length=256), nullable=True),
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("videos_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["fanpage_id"], ["target_fanpages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_yt_clip_sources_id", "yt_clip_sources", ["id"])
    op.create_index("ix_yt_clip_sources_fanpage_id", "yt_clip_sources", ["fanpage_id"])
    op.create_index("ix_yt_clip_sources_last_checked_at", "yt_clip_sources", ["last_checked_at"])

    # ── yt_videos ──
    op.create_table(
        "yt_videos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fanpage_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("video_id", sa.String(length=16), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("channel_name", sa.String(length=256), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="discovered"),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ideas_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("subs_path", sa.String(length=512), nullable=True),
        sa.Column("words_path", sa.String(length=512), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["fanpage_id"], ["target_fanpages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["yt_clip_sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fanpage_id", "video_id", name="uq_yt_video_fanpage"),
    )
    for col in ("id", "fanpage_id", "source_id", "video_id", "published_at", "status", "created_at"):
        op.create_index(f"ix_yt_videos_{col}", "yt_videos", [col])

    # ── yt_clip_ideas ──
    op.create_table(
        "yt_clip_ideas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fanpage_id", sa.Integer(), nullable=False),
        sa.Column("yt_video_row_id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.String(length=16), nullable=False),
        sa.Column("start_s", sa.Float(), nullable=False),
        sa.Column("end_s", sa.Float(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("hook_text", sa.Text(), nullable=True),
        sa.Column("virality_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transcript_excerpt", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["fanpage_id"], ["target_fanpages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["yt_video_row_id"], ["yt_videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for col in ("id", "fanpage_id", "yt_video_row_id", "virality_score", "status", "created_at"):
        op.create_index(f"ix_yt_clip_ideas_{col}", "yt_clip_ideas", [col])

    # ── publish_jobs: the clip a job renders + its output ──
    op.add_column("publish_jobs", sa.Column("yt_clip_idea_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_publish_jobs_yt_clip_idea_id", "publish_jobs", "yt_clip_ideas",
        ["yt_clip_idea_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_publish_jobs_yt_clip_idea_id", "publish_jobs", ["yt_clip_idea_id"])
    for name, type_ in _JOB_COLUMNS:
        op.add_column("publish_jobs", sa.Column(name, type_, nullable=True))


def downgrade():
    for name, _ in reversed(_JOB_COLUMNS):
        op.drop_column("publish_jobs", name)
    op.drop_index("ix_publish_jobs_yt_clip_idea_id", table_name="publish_jobs")
    op.drop_constraint("fk_publish_jobs_yt_clip_idea_id", "publish_jobs", type_="foreignkey")
    op.drop_column("publish_jobs", "yt_clip_idea_id")

    for col in ("created_at", "status", "virality_score", "yt_video_row_id", "fanpage_id", "id"):
        op.drop_index(f"ix_yt_clip_ideas_{col}", table_name="yt_clip_ideas")
    op.drop_table("yt_clip_ideas")

    for col in ("created_at", "status", "published_at", "video_id", "source_id", "fanpage_id", "id"):
        op.drop_index(f"ix_yt_videos_{col}", table_name="yt_videos")
    op.drop_table("yt_videos")

    for col in ("last_checked_at", "fanpage_id", "id"):
        op.drop_index(f"ix_yt_clip_sources_{col}", table_name="yt_clip_sources")
    op.drop_table("yt_clip_sources")

    for col in ("youtube_last_error", "youtube_blocked_until", "youtube_proxy", "youtube_cookies_encrypted"):
        op.drop_column("settings", col)

    op.drop_column("target_fanpages", "yt_clip_publish_mode")
    for name, _, _ in reversed(_FANPAGE_COLUMNS):
        op.drop_column("target_fanpages", name)
