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

    # ── Content modes (Feature 2) ─────────────────
    mode1_ig_repost_enabled = Column(Boolean, default=True, nullable=False, server_default="true")
    mode2_news_content_enabled = Column(Boolean, default=False, nullable=False, server_default="false")
    mode2_publish_mode = Column(Enum(PublishMode), default=PublishMode.manual_review, nullable=False, server_default="manual_review")
    mode2_gallery_keywords = Column(ARRAY(String), server_default="{}", nullable=False)
    mode2_default_template_id = Column(Integer, nullable=True)  # FK to design_templates (Phase 2D)

    # ── Mode 2 caption criteria (separate set from Mode 1) ──
    mode2_caption_tone = Column(String(64), default="informative", nullable=False, server_default="informative")
    mode2_caption_language = Column(String(8), default="en", nullable=False, server_default="en")
    mode2_caption_max_length = Column(Integer, default=500, nullable=False, server_default="500")
    mode2_caption_hashtag_count = Column(Integer, default=5, nullable=False, server_default="5")
    mode2_caption_cta_text = Column(String(256), default="", nullable=False, server_default="")
    mode2_caption_custom_prompt = Column(Text, default="", nullable=False, server_default="")
    mode2_title_max_chars = Column(Integer, default=80, nullable=False, server_default="80")
    mode2_source_attribution = Column(Boolean, default=True, nullable=False, server_default="true")

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
    news_source_links = relationship("FanpageNewsSource", back_populates="fanpage", cascade="all, delete-orphan")
    publish_jobs = relationship("PublishJob", back_populates="fanpage")
