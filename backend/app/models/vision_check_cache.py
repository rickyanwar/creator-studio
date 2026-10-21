from sqlalchemy import Column, Integer, String, JSON, DateTime, Index, func
from app.database import Base


class VisionCheckCache(Base):
    __tablename__ = "vision_check_cache"
    __table_args__ = (
        Index("ix_vision_check_cache_kind_image_key", "kind", "image_sha256", "key", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(32), nullable=False)
    image_sha256 = Column(String(64), nullable=False)
    key = Column(String(512), nullable=False)
    result = Column(JSON, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
