"""Initial schema — all 10 tables

Revision ID: 001
Revises:
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_users_username", "users", ["username"])

    # ── repliz_credentials ────────────────────────────────────────────────────
    op.create_table(
        "repliz_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("access_key_encrypted", sa.String(512), nullable=False),
        sa.Column("secret_key_encrypted", sa.String(512), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )

    # ── burner_accounts ───────────────────────────────────────────────────────
    op.create_table(
        "burner_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ig_username", sa.String(64), unique=True, nullable=False),
        sa.Column("encrypted_password", sa.String(512), nullable=False),
        sa.Column("encrypted_session", sa.String(4096), nullable=True),
        sa.Column("proxy_url", sa.String(256), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "challenged", "rate_limited", "banned", name="burnerstatus"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("requests_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_burner_accounts_ig_username", "burner_accounts", ["ig_username"])

    # ── target_fanpages ───────────────────────────────────────────────────────
    op.create_table(
        "target_fanpages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repliz_account_id", sa.String(64), unique=True, nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("username", sa.String(128), nullable=True),
        sa.Column("picture_url", sa.String(512), nullable=True),
        sa.Column("platform_type", sa.String(32), nullable=False, server_default="facebook"),
        sa.Column("is_connected", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "publish_mode",
            sa.Enum("auto", "manual_review", name="publishmode"),
            nullable=False,
            server_default="manual_review",
        ),
        # Caption criteria
        sa.Column("caption_tone", sa.String(64), nullable=False, server_default="engaging"),
        sa.Column("caption_language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("caption_max_length", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("caption_hashtag_count", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("caption_must_include", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("caption_must_avoid", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("caption_cta_text", sa.String(256), nullable=False, server_default=""),
        sa.Column("use_attribution", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("caption_attribution_template", sa.String(128), nullable=False, server_default="via @{source_username}"),
        sa.Column(
            "attribution_position",
            sa.Enum("caption_end", "caption_start", name="attributionposition"),
            nullable=False,
            server_default="caption_end",
        ),
        sa.Column("caption_custom_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_target_fanpages_repliz_account_id", "target_fanpages", ["repliz_account_id"])

    # ── ig_sources ────────────────────────────────────────────────────────────
    op.create_table(
        "ig_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ig_username", sa.String(64), unique=True, nullable=False),
        sa.Column("ig_user_id", sa.String(64), nullable=True),
        sa.Column("burner_account_id", sa.Integer(), sa.ForeignKey("burner_accounts.id"), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_post_id", sa.String(128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_ig_sources_ig_username", "ig_sources", ["ig_username"])

    # ── fanpage_sources ───────────────────────────────────────────────────────
    op.create_table(
        "fanpage_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fanpage_id", sa.Integer(), sa.ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ig_source_id", sa.Integer(), sa.ForeignKey("ig_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("fanpage_id", "ig_source_id", name="uq_fanpage_source"),
    )

    # ── posts ─────────────────────────────────────────────────────────────────
    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), unique=True, nullable=False),
        sa.Column("ig_source_id", sa.Integer(), sa.ForeignKey("ig_sources.id"), nullable=False),
        sa.Column("ig_media_id", sa.String(128), unique=True, nullable=False),
        sa.Column("ig_post_url", sa.String(512), nullable=True),
        sa.Column(
            "media_type",
            sa.Enum("image", "album", name="mediatype"),
            nullable=False,
        ),
        sa.Column("image_local_paths", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("image_public_urls", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("original_caption", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("crawled", "stored", "pending_fanout", "done", "cleaned", name="poststatus"),
            nullable=False,
            server_default="crawled",
        ),
        sa.Column("taken_at", sa.DateTime(), nullable=True),
        sa.Column("crawled_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("cleanup_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_posts_ig_media_id", "posts", ["ig_media_id"])
    op.create_index("ix_posts_uuid", "posts", ["uuid"])
    op.create_index("ix_posts_status", "posts", ["status"])

    # ── publish_jobs ──────────────────────────────────────────────────────────
    op.create_table(
        "publish_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fanpage_id", sa.Integer(), sa.ForeignKey("target_fanpages.id"), nullable=False),
        sa.Column("ai_generated_caption", sa.Text(), nullable=True),
        sa.Column(
            "ai_provider_used",
            sa.Enum("gemini", "groq", name="aiprovider"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending_caption", "pending_review", "pending_publish",
                "published", "failed", "skipped",
                name="publishjobstatus",
            ),
            nullable=False,
            server_default="pending_caption",
        ),
        sa.Column("repliz_schedule_id", sa.String(128), nullable=True),
        sa.Column("repliz_response_json", postgresql.JSON(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("cleanup_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("post_id", "fanpage_id", name="uq_post_fanpage"),
    )
    op.create_index("ix_publish_jobs_status", "publish_jobs", ["status"])
    op.create_index("ix_publish_jobs_repliz_schedule_id", "publish_jobs", ["repliz_schedule_id"])

    # ── settings (singleton id=1) ─────────────────────────────────────────────
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("crawl_interval_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("ai_provider_primary", sa.String(32), nullable=False, server_default="gemini"),
        sa.Column("ai_provider_fallback", sa.String(32), nullable=False, server_default="groq"),
        sa.Column("ai_gemini_api_key_encrypted", sa.String(512), nullable=True),
        sa.Column("ai_groq_api_key_encrypted", sa.String(512), nullable=True),
        sa.Column("storage_base_url", sa.String(256), nullable=True),
        sa.Column("storage_base_path", sa.String(256), nullable=True),
        sa.Column("ai_fallback_after_failures", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("ai_fallback_reset_after_minutes", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("repliz_access_key_encrypted", sa.String(512), nullable=True),
        sa.Column("repliz_secret_key_encrypted", sa.String(512), nullable=True),
        sa.Column("telegram_bot_token_encrypted", sa.String(512), nullable=True),
        sa.Column("telegram_chat_id", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )

    # ── Seed: default settings row ────────────────────────────────────────────
    op.execute("INSERT INTO settings (id) VALUES (1) ON CONFLICT DO NOTHING")


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_table("publish_jobs")
    op.drop_table("posts")
    op.drop_table("fanpage_sources")
    op.drop_table("ig_sources")
    op.drop_table("target_fanpages")
    op.drop_table("burner_accounts")
    op.drop_table("repliz_credentials")
    op.drop_table("users")

    # Drop enums
    for enum_name in [
        "burnerstatus", "publishmode", "attributionposition",
        "mediatype", "poststatus", "publishjobstatus", "aiprovider",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
