import enum

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, func, Enum as SAEnum
from sqlalchemy.orm import relationship
from app.database import Base


class ArticleStatus(str, enum.Enum):
    scraped = "scraped"
    copywritten = "copywritten"
    designed = "designed"
    published = "published"
    skipped = "skipped"


class ScrapedArticle(Base):
    __tablename__ = "scraped_articles"

    id = Column(Integer, primary_key=True, index=True)
    news_source_id = Column(Integer, ForeignKey("news_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    article_url = Column(String(1024), unique=True, nullable=False, index=True)  # dedup key
    scraped_title = Column(Text, nullable=False)
    scraped_content = Column(Text, nullable=False)
    scraped_image_url = Column(String(1024), nullable=True)
    is_processed = Column(Boolean, default=False, nullable=False, server_default="false")
    status = Column(
        SAEnum(ArticleStatus, name="articlestatus"),
        nullable=False,
        server_default="scraped",
        index=True,
    )
    scraped_at = Column(DateTime, server_default=func.now(), nullable=False)

    news_source = relationship("NewsSource", back_populates="articles")
    publish_jobs = relationship("PublishJob", back_populates="source_article")
