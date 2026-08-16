from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class DiscussionTopic(Base):
    """An evergreen discussion seed for a fanpage's Mode 4 content.

    Unlike Mode 4's news-driven discussions (which come from freshly scraped
    articles), evergreen topics are timeless debate prompts the user curates
    per fanpage, e.g. "Is Ayrton Senna the greatest F1 driver of all time?".
    The Mode 4 scheduler rotates through active seeds least-recently-used
    (order by last_used_at NULLS FIRST) so the same debate isn't posted twice
    in a row, and 9Router turns the seed into a finished DISCUSSION/HOT TAKE
    card (opinion-only — see news_copywriter.generate_discussion_copy).
    """

    __tablename__ = "discussion_topics"

    id = Column(Integer, primary_key=True, index=True)
    fanpage_id = Column(
        Integer,
        ForeignKey("target_fanpages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The debate prompt seed, in the user's own words (any language). It may be
    # a full question ("Is Max unstoppable in a competitive car?") or a loose
    # theme ("overrated drivers") the AI sharpens into one question.
    seed_text = Column(Text, nullable=False)
    # Subject name to look up a photo for (gallery-first, Getty fallback). When
    # blank, the copywriter extracts the subject from the generated question.
    subject_hint = Column(String(128), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")

    times_used = Column(Integer, default=0, nullable=False, server_default="0")
    last_used_at = Column(DateTime, nullable=True, index=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    fanpage = relationship("TargetFanpage", back_populates="discussion_topics")
