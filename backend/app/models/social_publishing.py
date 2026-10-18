from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    Text,
    DateTime,
    ForeignKey,
    JSON,
    CheckConstraint,
    UniqueConstraint,
    Index,
    func,
    text,
)
from sqlalchemy.orm import relationship
from app.database import Base


class SocialDestination(Base):
    __tablename__ = "social_destinations"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('facebook_profile', 'facebook_page', 'instagram', 'youtube', 'threads', 'x')",
            name="ck_social_destinations_kind",
        ),
        CheckConstraint(
            "mode IN ('phone', 'repliz', 'mixed')",
            name="ck_social_destinations_mode",
        ),
        CheckConstraint("daily_target > 0", name="ck_social_destinations_daily_target_pos"),
        CheckConstraint(
            "phone_daily_target >= 0",
            name="ck_social_destinations_phone_target_nonneg",
        ),
        CheckConstraint(
            "repliz_daily_target >= 0",
            name="ck_social_destinations_repliz_target_nonneg",
        ),
        CheckConstraint("min_gap_minutes > 0", name="ck_social_destinations_min_gap_pos"),
        Index(
            "ix_social_destinations_project_kind_parent",
            "content_project_id",
            "kind",
            "parent_profile_id",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    content_project_id = Column(
        Integer,
        ForeignKey("target_fanpages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = Column(String(32), nullable=False)
    name = Column(String(256), nullable=False)
    facebook_actor_id = Column(String(64), nullable=True)
    parent_profile_id = Column(
        Integer,
        ForeignKey("social_destinations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    repliz_account_id = Column(String(64), nullable=True, unique=True, index=True)
    mode = Column(String(16), nullable=False, default="phone", server_default="phone")
    timezone = Column(
        String(64),
        nullable=False,
        default="Europe/London",
        server_default="Europe/London",
    )
    daily_target = Column(Integer, nullable=False, default=1, server_default="1")
    phone_daily_target = Column(Integer, nullable=False, default=0, server_default="0")
    repliz_daily_target = Column(Integer, nullable=False, default=0, server_default="0")
    min_gap_minutes = Column(Integer, nullable=False, default=5, server_default="5")
    supported_media = Column(
        JSON,
        nullable=False,
        default=lambda: ["image", "album", "video"],
        server_default=text("'[\"image\", \"album\", \"video\"]'::json"),
    )
    is_active = Column(Boolean, default=False, nullable=False, server_default="false")
    identity_verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    content_project = relationship("TargetFanpage", back_populates="social_destinations")
    parent_profile = relationship(
        "SocialDestination",
        remote_side=[id],
        back_populates="child_pages",
    )
    child_pages = relationship("SocialDestination", back_populates="parent_profile")
    device_assignments = relationship(
        "DestinationDeviceAssignment",
        back_populates="destination",
        cascade="all, delete-orphan",
    )
    deliveries = relationship(
        "PublishDelivery",
        back_populates="destination",
        cascade="all, delete-orphan",
    )


class PhoneDevice(Base):
    __tablename__ = "phone_devices"
    __table_args__ = (
        CheckConstraint(
            "status IN ('available', 'in_use', 'maintenance')",
            name="ck_phone_devices_status",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    tailscale_node_id = Column(String(128), nullable=True, unique=True, index=True)
    tailscale_ip = Column(String(64), nullable=True)
    adb_serial = Column(String(128), nullable=True, unique=True, index=True)
    expected_profile_actor_id = Column(String(64), nullable=True)
    expected_profile_name = Column(String(256), nullable=True)
    screen_width = Column(Integer, nullable=True)
    screen_height = Column(Integer, nullable=True)
    screen_density = Column(Integer, nullable=True)
    screen_rotation = Column(Integer, nullable=True)
    facebook_app_version = Column(String(64), nullable=True)
    mobilerun_portal_version = Column(String(64), nullable=True)
    app_card_version = Column(String(64), nullable=True)
    calibration_verified_at = Column(DateTime, nullable=True)
    status = Column(
        String(32),
        nullable=False,
        default="maintenance",
        server_default="maintenance",
    )
    last_seen_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    assignments = relationship(
        "DestinationDeviceAssignment",
        back_populates="device",
        cascade="all, delete-orphan",
    )
    deliveries = relationship("PublishDelivery", back_populates="device")
    lease = relationship(
        "DeviceLease",
        back_populates="device",
        uselist=False,
        cascade="all, delete-orphan",
    )
    recovery_incidents = relationship(
        "RecoveryIncident",
        back_populates="device",
        cascade="all, delete-orphan",
    )


class DestinationDeviceAssignment(Base):
    __tablename__ = "destination_device_assignments"
    __table_args__ = (
        UniqueConstraint("destination_id", "device_id", name="uq_destination_device"),
        Index(
            "uq_destination_priority",
            "destination_id",
            "priority",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
        CheckConstraint("priority >= 0", name="ck_destination_device_assignments_priority"),
    )

    id = Column(Integer, primary_key=True, index=True)
    destination_id = Column(
        Integer,
        ForeignKey("social_destinations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id = Column(
        Integer,
        ForeignKey("phone_devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    priority = Column(Integer, nullable=False, default=0, server_default="0")
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")
    verified_profile_actor_id = Column(String(64), nullable=True)
    verified_target_actor_id = Column(String(64), nullable=True)
    verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    destination = relationship("SocialDestination", back_populates="device_assignments")
    device = relationship("PhoneDevice", back_populates="assignments")


class PublishDelivery(Base):
    __tablename__ = "publish_deliveries"
    __table_args__ = (
        UniqueConstraint("publish_job_id", "destination_id", name="uq_publish_job_destination"),
        UniqueConstraint("destination_id", "facebook_post_id", name="uq_delivery_destination_facebook_post"),
        CheckConstraint(
            "state IN ('waiting_for_slot', 'scheduled', 'publishing', 'waiting_for_device', "
            "'uncertain', 'needs_human', 'repliz_scheduled', 'published', 'failed', 'skipped')",
            name="ck_publish_deliveries_state",
        ),
        CheckConstraint(
            "intended_method IS NULL OR intended_method IN ('phone', 'repliz')",
            name="ck_publish_deliveries_intended_method",
        ),
        CheckConstraint(
            "actual_method IS NULL OR actual_method IN ('phone', 'repliz')",
            name="ck_publish_deliveries_actual_method",
        ),
        CheckConstraint(
            "media_kind IS NULL OR media_kind IN ('image', 'album', 'video')",
            name="ck_publish_deliveries_media_kind",
        ),
        Index(
            "ix_publish_deliveries_dest_state_scheduled",
            "destination_id",
            "state",
            "scheduled_for",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    publish_job_id = Column(
        Integer,
        ForeignKey("publish_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    destination_id = Column(
        Integer,
        ForeignKey("social_destinations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id = Column(
        Integer,
        ForeignKey("phone_devices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    state = Column(
        String(32),
        nullable=False,
        default="waiting_for_slot",
        server_default="waiting_for_slot",
    )
    intended_method = Column(String(16), nullable=True)
    actual_method = Column(String(16), nullable=True)
    scheduled_for = Column(DateTime, nullable=True, index=True)
    claimed_at = Column(DateTime, nullable=True)
    claim_token = Column(String(64), nullable=True)
    attempt_count = Column(Integer, default=0, nullable=False, server_default="0")
    caption_sha256 = Column(String(64), nullable=True)
    media_sha256 = Column(String(64), nullable=True)
    caption_snapshot = Column(Text, nullable=True)
    media_snapshot = Column(JSON, nullable=True)
    media_kind = Column(String(16), nullable=True)
    title_snapshot = Column(Text, nullable=True)
    alt_snapshot = Column(Text, nullable=True)
    content_type_snapshot = Column(String(32), nullable=True)
    content_version = Column(Integer, default=1, nullable=False, server_default="1")
    repliz_schedule_id = Column(String(128), nullable=True, index=True)
    facebook_post_id = Column(String(128), nullable=True, index=True)
    facebook_permalink = Column(Text, nullable=True)
    uncertain_since = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    publish_job = relationship("PublishJob", back_populates="deliveries")
    destination = relationship("SocialDestination", back_populates="deliveries")
    device = relationship("PhoneDevice", back_populates="deliveries")
    events = relationship(
        "DeliveryEvent",
        back_populates="delivery",
        cascade="all, delete-orphan",
    )
    recovery_incidents = relationship("RecoveryIncident", back_populates="delivery")


class DeliveryEvent(Base):
    __tablename__ = "delivery_events"
    __table_args__ = (
        Index("ix_delivery_events_delivery_id_created_at", "delivery_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    delivery_id = Column(
        Integer,
        ForeignKey("publish_deliveries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = Column(String(64), nullable=False)
    details = Column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'::json"),
    )
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    delivery = relationship("PublishDelivery", back_populates="events")


class DeviceLease(Base):
    __tablename__ = "device_leases"

    device_id = Column(
        Integer,
        ForeignKey("phone_devices.id", ondelete="CASCADE"),
        primary_key=True,
    )
    delivery_id = Column(
        Integer,
        ForeignKey("publish_deliveries.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    acquired_at = Column(DateTime, nullable=False, server_default=func.now())
    expires_at = Column(DateTime, nullable=False)

    device = relationship("PhoneDevice", back_populates="lease")
    delivery = relationship("PublishDelivery")


class RecoveryIncident(Base):
    __tablename__ = "recovery_incidents"
    __table_args__ = (
        CheckConstraint(
            "state IN ('checking', 'repairing', 'awaiting_approval', 'needs_human', 'resolved')",
            name="ck_recovery_incidents_state",
        ),
        Index("ix_recovery_incidents_device_id_state", "device_id", "state"),
    )

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(
        Integer,
        ForeignKey("phone_devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    delivery_id = Column(
        Integer,
        ForeignKey("publish_deliveries.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    state = Column(
        String(32),
        nullable=False,
        default="checking",
        server_default="checking",
    )
    reason_code = Column(String(64), nullable=False)
    check_count = Column(Integer, default=0, nullable=False, server_default="0")
    hermes_attempt_count = Column(Integer, default=0, nullable=False, server_default="0")
    owner_notified_at = Column(DateTime, nullable=True)
    auto_approve_after = Column(DateTime, nullable=True)
    owner_decision = Column(String(32), nullable=True)
    last_attempt_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    device = relationship("PhoneDevice", back_populates="recovery_incidents")
    delivery = relationship("PublishDelivery", back_populates="recovery_incidents")


class OperationalNotification(Base):
    __tablename__ = "operational_notifications"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'sent', 'failed', 'open', 'resolved')",
            name="ck_operational_notifications_state",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    dedupe_key = Column(String(128), unique=True, nullable=False, index=True)
    kind = Column(String(64), nullable=False)
    message = Column(Text, nullable=False)
    state = Column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    attempt_count = Column(Integer, default=0, nullable=False, server_default="0")
    last_error = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
