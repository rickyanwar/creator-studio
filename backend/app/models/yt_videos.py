from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import relationship
from app.database import Base


class YtVideo(Base):
    """One YouTube video discovered for one fanpage (Mode 7).

    Lifecycle (`status`):
      discovered → analyzing → analyzed   (highlights staged as YtClipIdea rows)
                             ↘ skipped    (live/premiere, too short/long, no
                                           subtitles, too old — `skip_reason`)
                             ↘ failed     (download/AI error after retries)

    `discovered → analyzing` is an atomic claim (an UPDATE … WHERE
    status='discovered'), so two ticks never analyse the same video.
    Subtitle files are cached per YouTube video id (shared across fanpages
    — see services.yt_downloader), but the AI analysis is per fanpage
    (language, niche and clip length differ).
    """

    __tablename__ = "yt_videos"
    __table_args__ = (UniqueConstraint("fanpage_id", "video_id", name="uq_yt_video_fanpage"),)

    id = Column(Integer, primary_key=True, index=True)
    fanpage_id = Column(Integer, ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False, index=True)
    source_id = Column(Integer, ForeignKey("yt_clip_sources.id", ondelete="SET NULL"), nullable=True, index=True)
    video_id = Column(String(16), nullable=False, index=True)

    title = Column(Text, nullable=True)
    channel_name = Column(String(256), nullable=True)
    published_at = Column(DateTime, nullable=True, index=True)
    duration_s = Column(Integer, nullable=True)
    language = Column(String(16), nullable=True)

    status = Column(String(16), default="discovered", nullable=False, server_default="discovered", index=True)
    skip_reason = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    attempt_count = Column(Integer, default=0, nullable=False, server_default="0")
    ideas_created = Column(Integer, default=0, nullable=False, server_default="0")

    subs_path = Column(String(512), nullable=True)    # .srt — what the AI reads
    words_path = Column(String(512), nullable=True)   # .json3 — per-word timing for captions

    analyzed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    fanpage = relationship("TargetFanpage", back_populates="yt_videos")
    source = relationship("YtClipSource")
    ideas = relationship("YtClipIdea", back_populates="yt_video", cascade="all, delete-orphan")
