from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class FacebookPhotoIdea(Base):
    """A staged Mode 6 content idea — the TEXT of one Facebook photo, read by
    vision at topup time and classified news/quote/discussion ("other" is
    rejected before ever becoming a row — see services/
    facebook_content_classifier.py and services/facebook_photo_source.py's
    build_idea_from_candidate). The photo itself is deleted right after it's
    read and never reaches a design: the card is recreated around a clean
    photo of the subject (see design_renderer.render_facebook_photo).

    The text is already rewritten into the fanpage's language when the row
    is created, so what's stored (and hand-edited in the queue) is the final
    on-card text. Consumed FIFO by the beat task (app.tasks.facebook_photo)
    into an actual PublishJob on the fanpage's own pacing.
    """

    __tablename__ = "facebook_photo_ideas"

    id = Column(Integer, primary_key=True, index=True)
    fanpage_id = Column(
        Integer,
        ForeignKey("target_fanpages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The evaluated-photo dedup marker (an is_deleted GalleryImage row, see
    # facebook_photo_source._record_seen) — not a design photo.
    gallery_image_id = Column(
        Integer,
        ForeignKey("gallery_images.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # "news" | "quote" | "discussion" — doubles as the
    # design_images.resolve_template category param at consume time.
    category = Column(String(16), nullable=False)
    design_title = Column(Text, nullable=False)      # news: headline / quote: the bare quote / discussion: question
    design_subtitle = Column(Text, nullable=True)     # quote: speaker name / discussion: "DISCUSSION" | "HOT TAKE"
    design_caption = Column(Text, nullable=True)      # discussion only: subject name
    # "pending" | "used" — consumed rows are kept (not deleted) as an audit
    # trail, same convention as PinterestContentIdea/DiscussionContentIdea.
    status = Column(String(16), default="pending", nullable=False, server_default="pending", index=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)

    fanpage = relationship("TargetFanpage", back_populates="facebook_photo_ideas")
