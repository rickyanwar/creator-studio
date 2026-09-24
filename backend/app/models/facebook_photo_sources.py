from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class FacebookPhotoSource(Base):
    """A Facebook page the user curates for Mode 6 — its `/photos` grid is
    checked periodically for new photos, dedup'd by fbid via GalleryImage
    (source_engine="facebook_photo"). Rotated least-recently-used, same
    pattern as PinterestSource (pinterest_sources.py) for Mode 5's curated
    profile/board references.
    """

    __tablename__ = "facebook_photo_sources"

    id = Column(Integer, primary_key=True, index=True)
    fanpage_id = Column(
        Integer,
        ForeignKey("target_fanpages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page_url = Column(Text, nullable=False)  # profile URL or bare username
    label = Column(String(256), nullable=True)  # admin's own note
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")

    times_used = Column(Integer, default=0, nullable=False, server_default="0")
    last_used_at = Column(DateTime, nullable=True, index=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    fanpage = relationship("TargetFanpage", back_populates="facebook_photo_sources")
