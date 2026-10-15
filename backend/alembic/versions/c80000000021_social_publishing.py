"""social_publishing

Revision ID: c80000000021
Revises: c80000000020
"""

from alembic import context, op
import sqlalchemy as sa


revision = "c80000000021"
down_revision = "c80000000020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Allow repliz_account_id on target_fanpages to be nullable (for Profile-only projects)
    op.alter_column(
        "target_fanpages",
        "repliz_account_id",
        existing_type=sa.String(length=64),
        nullable=True,
    )

    # 2. phone_devices
    op.create_table(
        "phone_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("tailscale_node_id", sa.String(length=128), nullable=True, unique=True),
        sa.Column("tailscale_ip", sa.String(length=64), nullable=True),
        sa.Column("adb_serial", sa.String(length=128), nullable=True, unique=True),
        sa.Column("expected_profile_actor_id", sa.String(length=64), nullable=True),
        sa.Column("expected_profile_name", sa.String(length=256), nullable=True),
        sa.Column("screen_width", sa.Integer(), nullable=True),
        sa.Column("screen_height", sa.Integer(), nullable=True),
        sa.Column("screen_density", sa.Integer(), nullable=True),
        sa.Column("screen_rotation", sa.Integer(), nullable=True),
        sa.Column("facebook_app_version", sa.String(length=64), nullable=True),
        sa.Column("mobilerun_portal_version", sa.String(length=64), nullable=True),
        sa.Column("app_card_version", sa.String(length=64), nullable=True),
        sa.Column("calibration_verified_at", sa.DateTime(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="maintenance",
        ),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('available', 'in_use', 'maintenance')",
            name="ck_phone_devices_status",
        ),
    )
    op.create_index("ix_phone_devices_id", "phone_devices", ["id"])
    op.create_index("ix_phone_devices_tailscale_node_id", "phone_devices", ["tailscale_node_id"])
    op.create_index("ix_phone_devices_adb_serial", "phone_devices", ["adb_serial"])

    # 3. social_destinations
    op.create_table(
        "social_destinations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "content_project_id",
            sa.Integer(),
            sa.ForeignKey("target_fanpages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("facebook_actor_id", sa.String(length=64), nullable=True),
        sa.Column(
            "parent_profile_id",
            sa.Integer(),
            sa.ForeignKey("social_destinations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("repliz_account_id", sa.String(length=64), nullable=True, unique=True),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="phone"),
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default="Europe/London",
        ),
        sa.Column("daily_target", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("phone_daily_target", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("repliz_daily_target", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("min_gap_minutes", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "supported_media",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[\"image\", \"album\", \"video\"]'::json"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("identity_verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "kind IN ('facebook_profile', 'facebook_page')",
            name="ck_social_destinations_kind",
        ),
        sa.CheckConstraint(
            "mode IN ('phone', 'repliz', 'mixed')",
            name="ck_social_destinations_mode",
        ),
        sa.CheckConstraint("daily_target > 0", name="ck_social_destinations_daily_target_pos"),
        sa.CheckConstraint(
            "phone_daily_target >= 0",
            name="ck_social_destinations_phone_target_nonneg",
        ),
        sa.CheckConstraint(
            "repliz_daily_target >= 0",
            name="ck_social_destinations_repliz_target_nonneg",
        ),
        sa.CheckConstraint("min_gap_minutes > 0", name="ck_social_destinations_min_gap_pos"),
    )
    op.create_index("ix_social_destinations_id", "social_destinations", ["id"])
    op.create_index(
        "ix_social_destinations_content_project_id",
        "social_destinations",
        ["content_project_id"],
    )
    op.create_index(
        "ix_social_destinations_parent_profile_id",
        "social_destinations",
        ["parent_profile_id"],
    )
    op.create_index(
        "ix_social_destinations_repliz_account_id",
        "social_destinations",
        ["repliz_account_id"],
    )
    op.create_index(
        "ix_social_destinations_project_kind_parent",
        "social_destinations",
        ["content_project_id", "kind", "parent_profile_id"],
    )

    # 4. destination_device_assignments
    op.create_table(
        "destination_device_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "destination_id",
            sa.Integer(),
            sa.ForeignKey("social_destinations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("phone_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("verified_profile_actor_id", sa.String(length=64), nullable=True),
        sa.Column("verified_target_actor_id", sa.String(length=64), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("destination_id", "device_id", name="uq_destination_device"),
        sa.CheckConstraint("priority >= 0", name="ck_destination_device_assignments_priority"),
    )
    op.create_index(
        "ix_destination_device_assignments_id",
        "destination_device_assignments",
        ["id"],
    )
    op.create_index(
        "ix_destination_device_assignments_destination_id",
        "destination_device_assignments",
        ["destination_id"],
    )
    op.create_index(
        "ix_destination_device_assignments_device_id",
        "destination_device_assignments",
        ["device_id"],
    )
    op.create_index(
        "uq_destination_priority",
        "destination_device_assignments",
        ["destination_id", "priority"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
        sqlite_where=sa.text("is_active = 1"),
    )

    # 5. publish_deliveries
    op.create_table(
        "publish_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "publish_job_id",
            sa.Integer(),
            sa.ForeignKey("publish_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "destination_id",
            sa.Integer(),
            sa.ForeignKey("social_destinations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("phone_devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default="waiting_for_slot",
        ),
        sa.Column("intended_method", sa.String(length=16), nullable=True),
        sa.Column("actual_method", sa.String(length=16), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("caption_sha256", sa.String(length=64), nullable=True),
        sa.Column("media_sha256", sa.String(length=64), nullable=True),
        sa.Column("content_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("repliz_schedule_id", sa.String(length=128), nullable=True),
        sa.Column("facebook_post_id", sa.String(length=128), nullable=True),
        sa.Column("facebook_permalink", sa.Text(), nullable=True),
        sa.Column("uncertain_since", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("publish_job_id", "destination_id", name="uq_publish_job_destination"),
        sa.UniqueConstraint(
            "destination_id",
            "facebook_post_id",
            name="uq_delivery_destination_facebook_post",
        ),
        sa.CheckConstraint(
            "state IN ('waiting_for_slot', 'scheduled', 'publishing', 'waiting_for_device', "
            "'uncertain', 'needs_human', 'repliz_scheduled', 'published', 'failed', 'skipped')",
            name="ck_publish_deliveries_state",
        ),
        sa.CheckConstraint(
            "intended_method IS NULL OR intended_method IN ('phone', 'repliz')",
            name="ck_publish_deliveries_intended_method",
        ),
        sa.CheckConstraint(
            "actual_method IS NULL OR actual_method IN ('phone', 'repliz')",
            name="ck_publish_deliveries_actual_method",
        ),
    )
    op.create_index("ix_publish_deliveries_id", "publish_deliveries", ["id"])
    op.create_index(
        "ix_publish_deliveries_publish_job_id",
        "publish_deliveries",
        ["publish_job_id"],
    )
    op.create_index(
        "ix_publish_deliveries_destination_id",
        "publish_deliveries",
        ["destination_id"],
    )
    op.create_index("ix_publish_deliveries_device_id", "publish_deliveries", ["device_id"])
    op.create_index("ix_publish_deliveries_scheduled_for", "publish_deliveries", ["scheduled_for"])
    op.create_index(
        "ix_publish_deliveries_repliz_schedule_id",
        "publish_deliveries",
        ["repliz_schedule_id"],
    )
    op.create_index(
        "ix_publish_deliveries_facebook_post_id",
        "publish_deliveries",
        ["facebook_post_id"],
    )
    op.create_index(
        "ix_publish_deliveries_dest_state_scheduled",
        "publish_deliveries",
        ["destination_id", "state", "scheduled_for"],
    )

    # 6. delivery_events
    op.create_table(
        "delivery_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "delivery_id",
            sa.Integer(),
            sa.ForeignKey("publish_deliveries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "details",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_delivery_events_id", "delivery_events", ["id"])
    op.create_index("ix_delivery_events_delivery_id", "delivery_events", ["delivery_id"])
    op.create_index(
        "ix_delivery_events_delivery_id_created_at",
        "delivery_events",
        ["delivery_id", "created_at"],
    )

    # 7. device_leases
    op.create_table(
        "device_leases",
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("phone_devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "delivery_id",
            sa.Integer(),
            sa.ForeignKey("publish_deliveries.id", ondelete="CASCADE"),
            nullable=True,
            unique=True,
        ),
        sa.Column("acquired_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )

    # 8. recovery_incidents
    op.create_table(
        "recovery_incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("phone_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "delivery_id",
            sa.Integer(),
            sa.ForeignKey("publish_deliveries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default="checking",
        ),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("check_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hermes_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("owner_notified_at", sa.DateTime(), nullable=True),
        sa.Column("auto_approve_after", sa.DateTime(), nullable=True),
        sa.Column("owner_decision", sa.String(length=32), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "state IN ('checking', 'repairing', 'awaiting_approval', 'needs_human', 'resolved')",
            name="ck_recovery_incidents_state",
        ),
    )
    op.create_index("ix_recovery_incidents_id", "recovery_incidents", ["id"])
    op.create_index("ix_recovery_incidents_device_id", "recovery_incidents", ["device_id"])
    op.create_index("ix_recovery_incidents_delivery_id", "recovery_incidents", ["delivery_id"])
    op.create_index(
        "ix_recovery_incidents_device_id_state",
        "recovery_incidents",
        ["device_id", "state"],
    )

    # 9. operational_notifications
    op.create_table(
        "operational_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "state IN ('pending', 'sent', 'failed')",
            name="ck_operational_notifications_state",
        ),
    )
    op.create_index("ix_operational_notifications_id", "operational_notifications", ["id"])
    op.create_index(
        "ix_operational_notifications_dedupe_key",
        "operational_notifications",
        ["dedupe_key"],
    )

    # 10. Backfill Facebook Page destination for existing fanpages with non-null Repliz ID
    op.execute(
        """
        INSERT INTO social_destinations (
            content_project_id,
            kind,
            name,
            repliz_account_id,
            mode,
            timezone,
            daily_target,
            phone_daily_target,
            repliz_daily_target,
            min_gap_minutes,
            supported_media,
            is_active,
            created_at,
            updated_at
        )
        SELECT
            id AS content_project_id,
            'facebook_page' AS kind,
            name,
            repliz_account_id,
            'repliz' AS mode,
            COALESCE(timezone, 'Europe/London') AS timezone,
            GREATEST(COALESCE(publish_daily_limit, 45), 1) AS daily_target,
            0 AS phone_daily_target,
            GREATEST(COALESCE(publish_daily_limit, 45), 1) AS repliz_daily_target,
            5 AS min_gap_minutes,
            '["image", "album", "video"]'::json AS supported_media,
            is_active,
            created_at,
            updated_at
        FROM target_fanpages
        WHERE repliz_account_id IS NOT NULL
        ON CONFLICT (repliz_account_id) DO NOTHING
        """
    )


def downgrade() -> None:
    # 1. Refuse downgrade if local Profile-only projects (NULL repliz_account_id) exist
    bind = op.get_bind()
    if bind is not None and not context.is_offline_mode():
        result = bind.execute(
            sa.text("SELECT COUNT(*) FROM target_fanpages WHERE repliz_account_id IS NULL")
        )
        null_count = result.scalar()
        if null_count and null_count > 0:
            raise RuntimeError(
                f"Cannot restore target_fanpages.repliz_account_id NOT NULL: "
                f"{null_count} profile-only projects have NULL repliz_account_id"
            )

    # 2. Drop new tables in reverse FK dependency order
    op.drop_table("operational_notifications")
    op.drop_table("recovery_incidents")
    op.drop_table("device_leases")
    op.drop_table("delivery_events")
    op.drop_table("publish_deliveries")
    op.drop_table("destination_device_assignments")
    op.drop_table("social_destinations")
    op.drop_table("phone_devices")

    # 3. Restore repliz_account_id NOT NULL
    op.alter_column(
        "target_fanpages",
        "repliz_account_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )
