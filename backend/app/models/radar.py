from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, Text, ForeignKey, UniqueConstraint, CheckConstraint, Index, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from app.database import Base


class StoryContentHint:
    NEWS = "news"
    QUOTE = "quote"
    OTHER = "other"
    UNKNOWN = "unknown"

class StoryShelfKind:
    BREAKING = "breaking"
    NEWS = "news"
    EVERGREEN = "evergreen"

class StoryStatus:
    WATCHING = "watching"
    TRIGGERED = "triggered"
    STALE = "stale"
    SKIPPED = "skipped"

class DecisionRule:
    FAST = "fast"
    CONFIRMED = "confirmed"
    BURST = "burst"
    NONE = "none"
    STALE = "stale"

class DecisionStatus:
    SHADOW_LOGGED = "shadow_logged"
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    CREATED = "created"
    SKIPPED_DEDUP = "skipped_dedup"
    SKIPPED_STALE = "skipped_stale"
    SKIPPED_NO_DISTINCT_PHOTO = "skipped_no_distinct_photo"
    SKIPPED_SIMILAR_CAPTION = "skipped_similar_caption"
    SKIPPED_DAILY_MAX = "skipped_daily_max"
    AWAITING_VISUAL_ENGINE = "awaiting_visual_engine"
    HELD_QUOTE_MISMATCH = "held_quote_mismatch"
    FAILED = "failed"


class RadarAccount(Base):
    __tablename__ = "radar_accounts"
    __table_args__ = (
        UniqueConstraint("niche", "ig_username"),
    )

    id = Column(Integer, primary_key=True, index=True)
    niche = Column(String(64), nullable=False, index=True)
    ig_username = Column(String(64), nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")
    leader_score = Column(Float, default=0, nullable=False, server_default="0")
    last_checked_at = Column(DateTime, nullable=True)
    last_post_seen_at = Column(DateTime, nullable=True)
    avg_scrape_seconds = Column(Float, nullable=True)
    last_error = Column(String(512), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=True)

    posts = relationship("RadarPost", back_populates="account", cascade="all, delete-orphan")


class RadarPost(Base):
    __tablename__ = "radar_posts"
    __table_args__ = (
        CheckConstraint("radar_account_id IS NOT NULL OR ig_source_id IS NOT NULL", name="chk_radar_posts_source"),
        Index("ix_radar_posts_account_taken", "radar_account_id", "taken_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    radar_account_id = Column(Integer, ForeignKey("radar_accounts.id", ondelete="CASCADE"), nullable=True)
    ig_source_id = Column(Integer, ForeignKey("ig_sources.id", ondelete="CASCADE"), nullable=True)
    ig_username = Column(String(64), nullable=False, index=True)
    shortcode = Column(String(64), unique=True, nullable=False)
    taken_at = Column(DateTime, nullable=False)
    caption = Column(Text, nullable=True)
    media_type = Column(String(16), nullable=False)
    thumbnail_url = Column(String(1024), nullable=True)
    image_source_urls = Column(ARRAY(String), nullable=False, server_default="{}")
    phash = Column(String(16), nullable=True)
    first_seen_at = Column(DateTime, server_default=func.now(), nullable=True)
    story_id = Column(Integer, ForeignKey("radar_stories.id", ondelete="SET NULL"), nullable=True, index=True)
    latest_like_count = Column(Integer, nullable=True)
    latest_comment_count = Column(Integer, nullable=True)
    latest_observed_at = Column(DateTime, nullable=True)

    account = relationship("RadarAccount", back_populates="posts")
    ig_source = relationship("IGSource", back_populates="radar_posts")
    story = relationship("RadarStory", back_populates="posts", foreign_keys=[story_id])
    snapshots = relationship("RadarSnapshot", back_populates="post", cascade="all, delete-orphan")


class RadarSnapshot(Base):
    __tablename__ = "radar_snapshots"
    __table_args__ = (
        Index("ix_radar_snapshots_post_observed", "radar_post_id", "observed_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    radar_post_id = Column(Integer, ForeignKey("radar_posts.id", ondelete="CASCADE"), nullable=False)
    observed_at = Column(DateTime, nullable=False)
    age_minutes = Column(Float, nullable=False)
    like_count = Column(Integer, nullable=True)
    comment_count = Column(Integer, nullable=True)

    post = relationship("RadarPost", back_populates="snapshots")


class RadarStory(Base):
    __tablename__ = "radar_stories"
    __table_args__ = (
        Index("ix_radar_stories_niche_seen", "niche", "first_seen_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    niche = Column(String(64), nullable=True, index=True)
    first_seen_at = Column(DateTime, nullable=False)
    last_member_at = Column(DateTime, nullable=False)
    member_count = Column(Integer, default=1, nullable=False, server_default="1")
    distinct_accounts = Column(Integer, default=1, nullable=False, server_default="1")
    best_post_id = Column(Integer, ForeignKey("radar_posts.id", ondelete="SET NULL", use_alter=True, name="fk_radar_stories_best_post_id"), nullable=True)
    content_hint = Column(String(16), default=StoryContentHint.UNKNOWN, nullable=False, server_default="unknown")
    shelf_kind = Column(String(16), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    status = Column(String(32), default=StoryStatus.WATCHING, nullable=False, server_default="watching")
    heat_score = Column(Float, default=0, nullable=False, server_default="0")
    final_max_likes_24h = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=True)

    posts = relationship("RadarPost", back_populates="story", foreign_keys="[RadarPost.story_id]")
    best_post = relationship("RadarPost", foreign_keys=[best_post_id], post_update=True)
    decisions = relationship("RadarStoryDecision", back_populates="story", cascade="all, delete-orphan")


class RadarStoryDecision(Base):
    __tablename__ = "radar_story_decisions"
    __table_args__ = (
        UniqueConstraint("story_id", "fanpage_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    story_id = Column(Integer, ForeignKey("radar_stories.id", ondelete="CASCADE"), nullable=False)
    fanpage_id = Column(Integer, ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False)
    rule = Column(String(16), nullable=False)
    would_trigger_at = Column(DateTime, nullable=True)
    shadow = Column(Boolean, default=True, nullable=False, server_default="true")
    status = Column(String(32), nullable=False)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="SET NULL"), nullable=True)
    publish_job_id = Column(Integer, ForeignKey("publish_jobs.id", ondelete="SET NULL"), nullable=True)
    used_photo_key = Column(String(1024), nullable=True)
    preview_image_path = Column(String(512), nullable=True)
    preview_status = Column(String(16), nullable=True)
    preview_error = Column(String(300), nullable=True)
    caption_text = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    via_viral_only_link = Column(Boolean, default=False, nullable=False, server_default="false")
    created_at = Column(DateTime, server_default=func.now(), nullable=True)

    story = relationship("RadarStory", back_populates="decisions")
    fanpage = relationship("TargetFanpage")
    post = relationship("Post")
    publish_job = relationship("PublishJob")
