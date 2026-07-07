import enum
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum, ForeignKey, JSON, func, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from app.database import Base


class PublishJobStatus(str, enum.Enum):
    pending_watermark = "pending_watermark"
    pending_caption = "pending_caption"
    pending_design = "pending_design"   # news_content: waiting for template render (Phase 2D)
    pending_review = "pending_review"
    pending_publish = "pending_publish"
    published = "published"
    failed = "failed"
    skipped = "skipped"


class ContentType(str, enum.Enum):
    ig_repost = "ig_repost"
    news_content = "news_content"


class AIProvider(str, enum.Enum):
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
    design_title = Column(Text, nullable=True)          # AI headline that goes on the design
    design_image_path = Column(String(512), nullable=True)  # rendered PNG (Phase 2D)
    design_image_url = Column(String(512), nullable=True)
    design_template_id = Column(Integer, nullable=True)     # FK to design_templates (Phase 2D)

    ai_generated_caption = Column(Text, nullable=True)
    ai_provider_used = Column(Enum(AIProvider), nullable=True)
    watermarked_image_urls = Column(ARRAY(String), nullable=True)

    status = Column(Enum(PublishJobStatus), default=PublishJobStatus.pending_caption, nullable=False, index=True)

    repliz_schedule_id = Column(String(128), nullable=True, index=True)
    repliz_response_json = Column(JSON, nullable=True)

    attempt_count = Column(Integer, default=0, nullable=False)
    last_error = Column(Text, nullable=True)

    published_at = Column(DateTime, nullable=True)
    cleanup_at = Column(DateTime, nullable=True)   # published_at + 4 days
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    post = relationship("Post", back_populates="publish_jobs")
    fanpage = relationship("TargetFanpage", back_populates="publish_jobs")
    source_article = relationship("ScrapedArticle", back_populates="publish_jobs")
