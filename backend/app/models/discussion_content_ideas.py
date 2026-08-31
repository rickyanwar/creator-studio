from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class DiscussionContentIdea(Base):
    """A staged Mode 4 content idea — a fully AI-drafted discussion/hot-take
    card (label + question + subject + caption), already run through the
    same 3-tier topic selection and copywriting `app.tasks.discussion`
    always used (news / evergreen / general-knowledge), or typed directly
    by the user (source_type="manual"). Sits in this queue, user-editable/
    deletable, until the beat task consumes the oldest pending row (FIFO)
    into an actual PublishJob on the fanpage's own pacing — mirrors Mode
    5's PinterestContentIdea (see that model for the sibling pattern).
    """

    __tablename__ = "discussion_content_ideas"

    id = Column(Integer, primary_key=True, index=True)
    fanpage_id = Column(
        Integer,
        ForeignKey("target_fanpages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label = Column(String(16), nullable=False)          # "DISCUSSION" | "HOT TAKE"
    question = Column(Text, nullable=False)              # -> PublishJob.design_title
    subject_name = Column(String(256), nullable=False)   # -> PublishJob.design_caption (photo lookup only)
    caption = Column(Text, nullable=False)                # -> PublishJob.ai_generated_caption
    # "news" | "evergreen" | "general" | "manual" — which tier produced this idea.
    source_type = Column(String(16), nullable=False)
    # Only set for source_type="news" — mirrors PublishJob.source_article_id,
    # carried through at consume time.
    source_article_id = Column(
        Integer, ForeignKey("scraped_articles.id", ondelete="SET NULL"), nullable=True,
    )
    # "pending" | "used" — same convention as PinterestContentIdea.
    status = Column(String(16), default="pending", nullable=False, server_default="pending", index=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)

    fanpage = relationship("TargetFanpage", back_populates="discussion_content_ideas")
