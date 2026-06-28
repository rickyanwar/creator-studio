import enum

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, func, Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from app.database import Base


class ScraperBackend(str, enum.Enum):
    auto = "auto"            # try instagrapi; fall back to FlashAPI if no burner
    instagrapi = "instagrapi"  # always use burner account
    flashapi = "flashapi"    # always use FlashAPI (no burner needed)


class IGSource(Base):
    __tablename__ = "ig_sources"

    id = Column(Integer, primary_key=True, index=True)
    ig_username = Column(String(64), unique=True, nullable=False, index=True)
    ig_user_id = Column(String(64), nullable=True)  # populated after first successful crawl
    burner_account_id = Column(Integer, ForeignKey("burner_accounts.id"), nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    last_crawl_error = Column(String(512), nullable=True)
    last_seen_post_id = Column(String(128), nullable=True)  # ig_media_id of most recent seen post
    is_active = Column(Boolean, default=True, nullable=False)
    album_image_indices = Column(ARRAY(Integer), nullable=False, server_default="{1}")
    scraper_backend = Column(
        SAEnum(ScraperBackend, name="scraperbackend"),
        nullable=False,
        server_default="auto",
    )
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    burner = relationship("BurnerAccount", foreign_keys=[burner_account_id])
    fanpage_links = relationship("FanpageSource", back_populates="ig_source", cascade="all, delete-orphan")
