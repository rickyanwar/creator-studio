from sqlalchemy import Column, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import relationship
from app.database import Base


class FanpageNewsSource(Base):
    """Fanpage ↔ news source subscription (Mode 2), mirrors FanpageSource."""

    __tablename__ = "fanpage_news_sources"
    __table_args__ = (UniqueConstraint("fanpage_id", "news_source_id", name="uq_fanpage_news_source"),)

    id = Column(Integer, primary_key=True, index=True)
    fanpage_id = Column(Integer, ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False)
    news_source_id = Column(Integer, ForeignKey("news_sources.id", ondelete="CASCADE"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    fanpage = relationship("TargetFanpage", back_populates="news_source_links")
    news_source = relationship("NewsSource", back_populates="fanpage_links")
