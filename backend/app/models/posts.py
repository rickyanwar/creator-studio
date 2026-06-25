import enum
import uuid as _uuid
from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, Enum, ForeignKey, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import relationship
from app.database import Base


class MediaType(str, enum.Enum):
    image = "image"
    album = "album"


class PostStatus(str, enum.Enum):
    crawled = "crawled"
    stored = "stored"
    pending_fanout = "pending_fanout"
    done = "done"
    cleaned = "cleaned"


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(UUID(as_uuid=True), default=_uuid.uuid4, unique=True, nullable=False, index=True)

    ig_source_id = Column(Integer, ForeignKey("ig_sources.id"), nullable=False)
    ig_media_id = Column(String(128), unique=True, nullable=False, index=True)
    ig_post_url = Column(String(512), nullable=True)

    media_type = Column(Enum(MediaType), nullable=False)
    image_local_paths = Column(ARRAY(String), server_default="{}", nullable=False)
    image_public_urls = Column(ARRAY(String), server_default="{}", nullable=False)
    image_source_urls = Column(ARRAY(String), server_default="{}", nullable=False)
    original_caption = Column(Text, nullable=True)

    status = Column(Enum(PostStatus), default=PostStatus.crawled, nullable=False, index=True)

    taken_at = Column(DateTime, nullable=True)    # when IG post was published
    crawled_at = Column(DateTime, server_default=func.now(), nullable=False)
    cleanup_at = Column(DateTime, nullable=True)  # set after all jobs published

    ig_source = relationship("IGSource")
    publish_jobs = relationship("PublishJob", back_populates="post", cascade="all, delete-orphan")
