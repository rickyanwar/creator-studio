"""Add Facebook group sharing catalog, configuration and ledger."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c80000000056"
down_revision = "c80000000055"
branch_labels = None
depends_on = None


def upgrade():
    json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
    op.create_table(
        "fb_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("niche", sa.String(64), nullable=False),
        sa.Column("url", sa.String(512), nullable=False, unique=True),
        sa.Column("fb_group_id", sa.String(64)),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("subject", sa.String(128)),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("posting_identity", sa.String(16), nullable=False, server_default="page_allowed"),
        sa.Column("needs_admin_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allowed_days", json_type),
        sa.Column("banned_content", json_type),
        sa.Column("daily_cap", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("paused_reason", sa.String(255)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.CheckConstraint("kind IN ('person_fan','team_fan','general','meme')", name="ck_fb_groups_kind"),
        sa.CheckConstraint("posting_identity IN ('page_allowed','profile_only')", name="ck_fb_groups_posting_identity"),
    )
    op.create_index("ix_fb_groups_niche", "fb_groups", ["niche"])
    op.create_table(
        "fb_share_brand_settings",
        sa.Column("fanpage_id", sa.Integer(), sa.ForeignKey("target_fanpages.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("settings", json_type, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_table(
        "fb_share_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("destination_id", sa.Integer(), sa.ForeignKey("social_destinations.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("mode", sa.String(16)),
        sa.Column("phone_device_id", sa.Integer(), sa.ForeignKey("phone_devices.id", ondelete="SET NULL")),
        sa.Column("pic_name", sa.String(128)),
        sa.Column("pic_contact", sa.String(128)),
        sa.Column("task_hours_start", sa.String(5), nullable=False, server_default="08:00"),
        sa.Column("task_hours_end", sa.String(5), nullable=False, server_default="21:00"),
        sa.Column("start_per_day", sa.Integer()),
        sa.Column("weekly_increase", sa.Integer()),
        sa.Column("max_per_day", sa.Integer()),
        sa.Column("calibration", json_type),
        sa.Column("calibrated_at", sa.DateTime()),
        sa.Column("paused_until", sa.DateTime()),
        sa.Column("block_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.CheckConstraint("mode IS NULL OR mode IN ('phone_auto','pic_whatsapp','pic_telegram')", name="ck_fb_share_deliveries_mode"),
    )
    op.create_table(
        "fb_group_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("destination_id", sa.Integer(), sa.ForeignKey("social_destinations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("fb_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("checked_at", sa.DateTime()),
        sa.Column("joined_at", sa.DateTime()),
        sa.UniqueConstraint("destination_id", "group_id", name="uq_fb_group_membership_destination_group"),
        sa.CheckConstraint("status IN ('unknown','joined','pending_approval','not_member','removed','banned')", name="ck_fb_group_memberships_status"),
    )
    op.create_table(
        "fb_share_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fanpage_id", sa.Integer(), sa.ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("niche", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column("source_ref", sa.String(128), nullable=False),
        sa.Column("media_kind", sa.String(8), nullable=False),
        sa.Column("media", json_type, nullable=False),
        sa.Column("phash", sa.String(32)),
        sa.Column("subjects", json_type),
        sa.Column("sentiment", json_type),
        sa.Column("content_type", sa.String(24)),
        sa.Column("caption", sa.Text()),
        sa.Column("hashtags", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column("expires_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("fanpage_id", "source_kind", "source_ref", name="uq_fb_share_item_source"),
        sa.CheckConstraint("source_kind IN ('publish_job','video_job','radar','fb_source','muse','manual')", name="ck_fb_share_items_source_kind"),
        sa.CheckConstraint("media_kind IN ('photo','album','video')", name="ck_fb_share_items_media_kind"),
        sa.CheckConstraint("status IN ('new','routed','done','skipped','expired')", name="ck_fb_share_items_status"),
    )
    op.create_index("ix_fb_share_items_fanpage_id", "fb_share_items", ["fanpage_id"])
    op.create_table(
        "fb_share_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("fb_share_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("fb_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("destination_id", sa.Integer(), sa.ForeignKey("social_destinations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("delivery_mode", sa.String(16), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="planned"),
        sa.Column("post_url", sa.String(512)),
        sa.Column("error", sa.Text()),
        sa.Column("pic_message_ref", sa.String(64)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("item_id", "group_id", name="uq_fb_share_task_item_group"),
        sa.CheckConstraint("state IN ('planned','sent_to_pic','publishing','published','pending_admin','rejected','blocked','not_member','expired','failed','uncertain')", name="ck_fb_share_tasks_state"),
    )
    op.create_index("ix_fb_share_tasks_item_id", "fb_share_tasks", ["item_id"])
    op.create_index("ix_fb_share_tasks_destination_scheduled", "fb_share_tasks", ["destination_id", "scheduled_for"])


def downgrade():
    op.drop_index("ix_fb_share_tasks_destination_scheduled", table_name="fb_share_tasks")
    op.drop_index("ix_fb_share_tasks_item_id", table_name="fb_share_tasks")
    op.drop_table("fb_share_tasks")
    op.drop_index("ix_fb_share_items_fanpage_id", table_name="fb_share_items")
    op.drop_table("fb_share_items")
    op.drop_table("fb_group_memberships")
    op.drop_table("fb_share_deliveries")
    op.drop_table("fb_share_brand_settings")
    op.drop_index("ix_fb_groups_niche", table_name="fb_groups")
    op.drop_table("fb_groups")
