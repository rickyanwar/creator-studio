import enum

from sqlalchemy import Column, Integer, String, Boolean, DateTime, func, Enum as SAEnum
from sqlalchemy.orm import relationship
from app.database import Base


class RenderMode(str, enum.Enum):
    static = "static"  # plain HTTP fetch + BeautifulSoup
    js = "js"          # Playwright headless render for JS-heavy sites


class NewsSource(Base):
    __tablename__ = "news_sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    category_url = Column(String(512), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")
    scrape_interval_minutes = Column(Integer, nullable=False, server_default="60")
    render_mode = Column(
        SAEnum(RenderMode, name="rendermode"),
        nullable=False,
        server_default="static",
    )

    # ── Per-site CSS selector config ──
    article_list_selector = Column(String(256), nullable=False)
    article_link_attribute = Column(String(64), nullable=False, server_default="href")
    title_selector = Column(String(256), nullable=False)
    content_selector = Column(String(256), nullable=False)
    image_selector = Column(String(256), nullable=True)
    date_selector = Column(String(256), nullable=True)

    last_scraped_at = Column(DateTime, nullable=True)
    last_scrape_error = Column(String(512), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    articles = relationship("ScrapedArticle", back_populates="news_source", cascade="all, delete-orphan")
    fanpage_links = relationship("FanpageNewsSource", back_populates="news_source", cascade="all, delete-orphan")
