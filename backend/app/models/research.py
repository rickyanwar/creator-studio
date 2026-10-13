from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, func

from app.database import Base


class ResearchCall(Base):
    """One research desk (Muse) API call, kept so the owner can see when Muse checked in and what happened."""

    __tablename__ = "research_calls"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    endpoint = Column(String(32), nullable=False)
    action = Column(String(32), nullable=False)
    detail = Column(JSON, nullable=True)
    token_id = Column(Integer, ForeignKey("api_tokens.id", ondelete="SET NULL"), nullable=True)


class ResearchFeedback(Base):
    """A message on a research pack or one of its ideas: the studio's review notes for Muse, or Muse's notes back."""

    __tablename__ = "research_feedback"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    client_ref = Column(String(64), nullable=True, index=True)
    idea_id = Column(Integer, ForeignKey("video_ideas.id", ondelete="CASCADE"), nullable=True, index=True)
    author = Column(String(16), nullable=False)  # "studio" | "muse"
    kind = Column(String(16), nullable=False)  # "comment" | "rejected" | "note"
    message = Column(Text, nullable=False)
    username = Column(String(64), nullable=True)
    token_id = Column(Integer, ForeignKey("api_tokens.id", ondelete="SET NULL"), nullable=True)
