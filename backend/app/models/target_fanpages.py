import enum
from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, Enum, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from app.database import Base


class PublishMode(str, enum.Enum):
    auto = "auto"
    manual_review = "manual_review"


class AttributionPosition(str, enum.Enum):
    caption_end = "caption_end"
    caption_start = "caption_start"


class TargetFanpage(Base):
    __tablename__ = "target_fanpages"

    id = Column(Integer, primary_key=True, index=True)

    # ── From Repliz ───────────────────────────────
    repliz_account_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(256), nullable=False)
    username = Column(String(128), nullable=True)
    picture_url = Column(Text, nullable=True)
    platform_type = Column(String(32), default="facebook", nullable=False)
    is_connected = Column(Boolean, default=True, nullable=False)

    # ── Local toggles ─────────────────────────────
    is_active = Column(Boolean, default=False, nullable=False)
    publish_mode = Column(Enum(PublishMode), default=PublishMode.manual_review, nullable=False)

    # ── Caption criteria ──────────────────────────
    caption_tone = Column(String(64), default="engaging", nullable=False)
    caption_language = Column(String(8), default="en", nullable=False)
    caption_max_length = Column(Integer, default=500, nullable=False)
    caption_hashtag_count = Column(Integer, default=5, nullable=False)
    caption_must_include = Column(ARRAY(String), server_default="{}", nullable=False)
    caption_must_avoid = Column(ARRAY(String), server_default="{}", nullable=False)
    caption_cta_text = Column(String(256), default="", nullable=False)
    use_attribution = Column(Boolean, default=True, nullable=False)
    caption_attribution_template = Column(String(128), default="via @{source_username}", nullable=False)
    attribution_position = Column(Enum(AttributionPosition), default=AttributionPosition.caption_end, nullable=False)
    caption_custom_prompt = Column(Text, default="", nullable=False)

    # ── Image watermark (per fanpage) ─────────────
    watermark_text = Column(String(128), nullable=True)

    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # ── Relationships ─────────────────────────────
    source_links = relationship("FanpageSource", back_populates="fanpage", cascade="all, delete-orphan")
    publish_jobs = relationship("PublishJob", back_populates="fanpage")
