from sqlalchemy import Column, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import relationship
from app.database import Base


class FanpageSource(Base):
    __tablename__ = "fanpage_sources"
    __table_args__ = (UniqueConstraint("fanpage_id", "ig_source_id", name="uq_fanpage_source"),)

    id = Column(Integer, primary_key=True, index=True)
    fanpage_id = Column(Integer, ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False)
    ig_source_id = Column(Integer, ForeignKey("ig_sources.id", ondelete="CASCADE"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    fanpage = relationship("TargetFanpage", back_populates="source_links")
    ig_source = relationship("IGSource", back_populates="fanpage_links")
