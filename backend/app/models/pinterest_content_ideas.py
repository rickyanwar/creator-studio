from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class PinterestContentIdea(Base):
    """A staged Mode 5 content idea — one specific Pinterest photo already
    downloaded into GalleryImage, plus a title/description (vision-verified
    against Pinterest's own description when it had one, else vision-
    identified from scratch — see app.services.pinterest_source). Sits in
    this queue, user-editable/deletable, until the beat task
    (app.tasks.pinterest) consumes the oldest pending row (FIFO) into an
    actual PublishJob on the fanpage's own pacing.
    """

    __tablename__ = "pinterest_content_ideas"

    id = Column(Integer, primary_key=True, index=True)
    fanpage_id = Column(
        Integer,
        ForeignKey("target_fanpages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    gallery_image_id = Column(
        Integer,
        ForeignKey("gallery_images.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=False)
    # "ai_keyword" | "curated" — which Mode 5 source path found this pin.
    source_type = Column(String(16), nullable=False)
    # "pending" | "used" — consumed rows are kept (not deleted) as an audit
    # trail of what actually got posted, same convention as DiscussionTopic's
    # times_used/last_used_at rather than a hard delete on use.
    status = Column(String(16), default="pending", nullable=False, server_default="pending", index=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)

    fanpage = relationship("TargetFanpage", back_populates="pinterest_content_ideas")
