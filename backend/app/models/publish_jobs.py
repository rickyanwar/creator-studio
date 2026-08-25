import enum
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum, ForeignKey, JSON, func, UniqueConstraint, Boolean
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from app.database import Base


class PublishJobStatus(str, enum.Enum):
    pending_watermark = "pending_watermark"
    pending_caption = "pending_caption"
    pending_design = "pending_design"   # news_content: waiting for template render (Phase 2D)
    rendering = "rendering"       # claimed lease: render_design/render_ig_recreate is running now
    pending_review = "pending_review"
    pending_publish = "pending_publish"
    publishing = "publishing"     # claimed lease: publish_job is running now
    published = "published"
    failed = "failed"
    skipped = "skipped"


class ContentType(str, enum.Enum):
    ig_repost = "ig_repost"
    news_content = "news_content"
    ig_recreate = "ig_recreate"  # IG post classified + rebuilt on a quote/news template
    discussion = "discussion"    # Mode 4: AI-generated debate/hot-take card (news- or evergreen-seeded)
    pinterest_content = "pinterest_content"  # Mode 5: photo-seeded card from a consumed PinterestContentIdea


class AIProvider(str, enum.Enum):
    router = "router"
    gemini = "gemini"
    groq = "groq"


class PublishJob(Base):
    __tablename__ = "publish_jobs"
    __table_args__ = (
        UniqueConstraint("post_id", "fanpage_id", name="uq_post_fanpage"),
        UniqueConstraint("source_article_id", "fanpage_id", name="uq_article_fanpage"),
    )

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=True)  # null for news_content
    fanpage_id = Column(Integer, ForeignKey("target_fanpages.id"), nullable=False)

    # ── Content source (Feature 2) ────────────────
    content_type = Column(Enum(ContentType), default=ContentType.ig_repost, nullable=False, server_default="ig_repost")
    source_article_id = Column(Integer, ForeignKey("scraped_articles.id", ondelete="CASCADE"), nullable=True, index=True)
    # Mode 5 only: the specific GalleryImage a consumed PinterestContentIdea
    # was bound to — render_pinterest loads it directly, no search needed.
    source_gallery_image_id = Column(Integer, ForeignKey("gallery_images.id", ondelete="SET NULL"), nullable=True, index=True)
    design_title = Column(Text, nullable=True)          # AI headline that goes on the design
    design_subtitle = Column(Text, nullable=True)       # AI sub-headline (may carry **red** markers)
    design_caption = Column(Text, nullable=True)        # AI caption line under the name badge (e.g. "on X's Y")
    design_image_path = Column(String(512), nullable=True)  # rendered PNG (Phase 2D)
    design_image_url = Column(String(512), nullable=True)
    design_template_id = Column(Integer, nullable=True)     # FK to design_templates (Phase 2D)
    # AI's own call (from the same news-copy generation, see news_copywriter.py
    # build_news_copy_prompt TASK 1) on whether this is significant enough
    # breaking news to skip the normal per-fanpage publish pacing/daily-cap
    # queue — see publisher._next_schedule_at's `breaking` param. Only ever
    # set for content_type=news_content; false/unset means "schedule normally".
    is_breaking = Column(Boolean, default=False, nullable=False, server_default="false")

    ai_generated_caption = Column(Text, nullable=True)
    ai_provider_used = Column(Enum(AIProvider), nullable=True)
    watermarked_image_urls = Column(ARRAY(String), nullable=True)

    status = Column(Enum(PublishJobStatus), default=PublishJobStatus.pending_caption, nullable=False, index=True)

    repliz_schedule_id = Column(String(128), nullable=True, index=True)
    repliz_response_json = Column(JSON, nullable=True)
    # The actual Facebook go-live time sent to Repliz as scheduleAt — distinct
    # from published_at (when we made the API call). Used to space out
    # consecutive posts on the SAME fanpage (see publisher._next_schedule_at):
    # unlike published_at, this reflects the real future slot even when it
    # was pushed out by an earlier job's spacing requirement.
    scheduled_for = Column(DateTime, nullable=True, index=True)

    attempt_count = Column(Integer, default=0, nullable=False)
    last_error = Column(Text, nullable=True)

    # Which image the design pipeline actually used last render (e.g.
    # "gallery:123" or "scraped:https://..."), so a History "Re-edit with new
    # image" retry can tell select_image_for_job to skip it — see
    # design_renderer.select_image_for_job.
    last_image_marker = Column(Text, nullable=True)

    published_at = Column(DateTime, nullable=True)
    cleanup_at = Column(DateTime, nullable=True)   # published_at + 4 days
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # Soft delete from History: the row is kept (not hard-deleted) so the
    # unique constraints above keep preventing the same post/article from
    # being reposted to this fanpage again.
    is_deleted = Column(Boolean, nullable=False, server_default="false")
    deleted_at = Column(DateTime, nullable=True)

    post = relationship("Post", back_populates="publish_jobs")
    fanpage = relationship("TargetFanpage", back_populates="publish_jobs")
    source_article = relationship("ScrapedArticle", back_populates="publish_jobs")
