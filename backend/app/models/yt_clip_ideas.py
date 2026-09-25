from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class YtClipIdea(Base):
    """A staged Mode 7 clip — one highlight the AI picked from a YtVideo's
    transcript, waiting to be consumed into a PublishJob on the fanpage's own
    pacing (app.tasks.yt_clip). Nothing is downloaded or rendered at this
    stage: rendering is expensive, so only clips that will actually publish
    get rendered. Edit the title, or delete ideas you don't want, in the
    fanpage's Mode 7 queue — `preview_url` jumps straight to the moment.
    """

    __tablename__ = "yt_clip_ideas"

    id = Column(Integer, primary_key=True, index=True)
    fanpage_id = Column(Integer, ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False, index=True)
    yt_video_row_id = Column(Integer, ForeignKey("yt_videos.id", ondelete="CASCADE"), nullable=False, index=True)
    video_id = Column(String(16), nullable=False)     # YouTube id (denormalised for the preview link)

    start_s = Column(Float, nullable=False)
    end_s = Column(Float, nullable=False)
    title = Column(Text, nullable=False)              # fanpage language
    description = Column(Text, nullable=True)
    hook_text = Column(Text, nullable=True)
    virality_score = Column(Integer, nullable=False, default=0, server_default="0", index=True)
    transcript_excerpt = Column(Text, nullable=True)

    # "pending" | "used" — consumed rows are kept as an audit trail, same
    # convention as the other idea queues.
    status = Column(String(16), default="pending", nullable=False, server_default="pending", index=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)

    fanpage = relationship("TargetFanpage", back_populates="yt_clip_ideas")
    yt_video = relationship("YtVideo", back_populates="ideas")

    @property
    def preview_url(self) -> str:
        return f"https://youtu.be/{self.video_id}?t={int(self.start_s)}"
