"""Group catalog, per-account delivery configuration and sharing ledger."""

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects import postgresql

from app.database import Base


class FbGroup(Base):
    __tablename__ = "fb_groups"
    __table_args__ = (
        CheckConstraint("kind IN ('person_fan','team_fan','general','meme')", name="ck_fb_groups_kind"),
        CheckConstraint("posting_identity IN ('page_allowed','profile_only')", name="ck_fb_groups_posting_identity"),
    )

    id = Column(Integer, primary_key=True)
    niche = Column(String(64), nullable=False, index=True)
    url = Column(String(512), nullable=False, unique=True)
    fb_group_id = Column(String(64))
    name = Column(String(256), nullable=False)
    kind = Column(String(16), nullable=False)
    subject = Column(String(128))
    language = Column(String(8), nullable=False, default="en", server_default="en")
    posting_identity = Column(String(16), nullable=False, default="page_allowed", server_default="page_allowed")
    needs_admin_approval = Column(Boolean, nullable=False, default=False, server_default="false")
    allowed_days = Column(JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"))
    banned_content = Column(JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"))
    daily_cap = Column(Integer, nullable=False, default=1, server_default="1")
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    paused_reason = Column(String(255))
    notes = Column(Text)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class FbShareDelivery(Base):
    __tablename__ = "fb_share_deliveries"
    __table_args__ = (
        CheckConstraint("mode IS NULL OR mode IN ('phone_auto','pic_whatsapp','pic_telegram')", name="ck_fb_share_deliveries_mode"),
    )

    id = Column(Integer, primary_key=True)
    destination_id = Column(Integer, ForeignKey("social_destinations.id", ondelete="CASCADE"), nullable=False, unique=True)
    mode = Column(String(16))
    phone_device_id = Column(Integer, ForeignKey("phone_devices.id", ondelete="SET NULL"))
    pic_name = Column(String(128))
    pic_contact = Column(String(128))
    task_hours_start = Column(String(5), nullable=False, default="08:00", server_default="08:00")
    task_hours_end = Column(String(5), nullable=False, default="21:00", server_default="21:00")
    start_per_day = Column(Integer)
    weekly_increase = Column(Integer)
    max_per_day = Column(Integer)
    calibration = Column(JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"))
    calibrated_at = Column(DateTime)
    paused_until = Column(DateTime)
    block_count = Column(Integer, nullable=False, default=0, server_default="0")
    is_active = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class FbGroupMembership(Base):
    __tablename__ = "fb_group_memberships"
    __table_args__ = (
        UniqueConstraint("destination_id", "group_id", name="uq_fb_group_membership_destination_group"),
        CheckConstraint("status IN ('unknown','joined','pending_approval','not_member','removed','banned')", name="ck_fb_group_memberships_status"),
    )

    id = Column(Integer, primary_key=True)
    destination_id = Column(Integer, ForeignKey("social_destinations.id", ondelete="CASCADE"), nullable=False)
    group_id = Column(Integer, ForeignKey("fb_groups.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(24), nullable=False, default="unknown", server_default="unknown")
    checked_at = Column(DateTime)
    joined_at = Column(DateTime)


class FbShareItem(Base):
    __tablename__ = "fb_share_items"
    __table_args__ = (
        UniqueConstraint("fanpage_id", "source_kind", "source_ref", name="uq_fb_share_item_source"),
        CheckConstraint("source_kind IN ('publish_job','video_job','radar','fb_source','muse','manual')", name="ck_fb_share_items_source_kind"),
        CheckConstraint("media_kind IN ('photo','album','video')", name="ck_fb_share_items_media_kind"),
        CheckConstraint("status IN ('new','routed','done','skipped','expired')", name="ck_fb_share_items_status"),
    )

    id = Column(Integer, primary_key=True)
    fanpage_id = Column(Integer, ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False, index=True)
    niche = Column(String(64), nullable=False)
    source_kind = Column(String(24), nullable=False)
    source_ref = Column(String(128), nullable=False)
    media_kind = Column(String(8), nullable=False)
    media = Column(JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"), nullable=False)
    phash = Column(String(32))
    subjects = Column(JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"))
    sentiment = Column(JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"))
    content_type = Column(String(24))
    caption = Column(Text)
    hashtags = Column(Text)
    status = Column(String(16), nullable=False, default="new", server_default="new")
    expires_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class FbShareTask(Base):
    __tablename__ = "fb_share_tasks"
    __table_args__ = (
        UniqueConstraint("item_id", "group_id", name="uq_fb_share_task_item_group"),
        CheckConstraint("state IN ('planned','sent_to_pic','publishing','published','pending_admin','rejected','blocked','not_member','expired','failed','uncertain')", name="ck_fb_share_tasks_state"),
        Index("ix_fb_share_tasks_destination_scheduled", "destination_id", "scheduled_for"),
    )

    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("fb_share_items.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id = Column(Integer, ForeignKey("fb_groups.id", ondelete="CASCADE"), nullable=False)
    destination_id = Column(Integer, ForeignKey("social_destinations.id", ondelete="CASCADE"), nullable=False)
    delivery_mode = Column(String(16), nullable=False)
    scheduled_for = Column(DateTime, nullable=False)
    state = Column(String(16), nullable=False, default="planned", server_default="planned")
    post_url = Column(String(512))
    error = Column(Text)
    pic_message_ref = Column(String(64))
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    next_retry_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
