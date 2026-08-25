from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class PinterestSource(Base):
    """A curated Pinterest reference the user pastes in for Mode 5 — a
    profile URL (every board on that profile is walked) or a board URL
    (its pins are walked directly). Rotated least-recently-used, same
    pattern as DiscussionTopic (discussion_topics.py) for Mode 4's evergreen
    seeds, so the same reference isn't hit on every tick.
    """

    __tablename__ = "pinterest_sources"

    id = Column(Integer, primary_key=True, index=True)
    fanpage_id = Column(
        Integer,
        ForeignKey("target_fanpages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_url = Column(Text, nullable=False)
    label = Column(String(256), nullable=True)  # admin's own note
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")

    times_used = Column(Integer, default=0, nullable=False, server_default="0")
    last_used_at = Column(DateTime, nullable=True, index=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    fanpage = relationship("TargetFanpage", back_populates="pinterest_sources")
