from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class FacebookPhotoIdea(Base):
    """A staged Mode 6 content idea — one Facebook photo already classified
    (news/discussion) and downloaded into GalleryImage. Unlike Mode 5's
    PinterestContentIdea (classified at CONSUME time, since a pin's type
    isn't decidable until vision has produced text to classify), classification
    happens at TOPUP time here — a single vision call over the photo itself
    decides news/discussion/other, and "other" is rejected before ever
    becoming a row (see services/facebook_content_classifier.py and
    services/facebook_photo_source.py's build_idea_from_candidate).

    Sits in this queue, consumed FIFO by the beat task (app.tasks.
    facebook_photo) into an actual PublishJob on the fanpage's own pacing.
    """

    __tablename__ = "facebook_photo_ideas"

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
    # "news" | "discussion" — doubles as the design_images.resolve_template
    # category param at consume time.
    category = Column(String(16), nullable=False)
    design_title = Column(Text, nullable=False)      # news: headline / discussion: question
    design_subtitle = Column(Text, nullable=True)     # discussion only: "DISCUSSION" | "HOT TAKE"
    design_caption = Column(Text, nullable=True)      # discussion only: subject name
    # "pending" | "used" — consumed rows are kept (not deleted) as an audit
    # trail, same convention as PinterestContentIdea/DiscussionContentIdea.
    status = Column(String(16), default="pending", nullable=False, server_default="pending", index=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)

    fanpage = relationship("TargetFanpage", back_populates="facebook_photo_ideas")
