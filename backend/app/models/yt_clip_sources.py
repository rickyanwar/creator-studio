from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class YtClipSource(Base):
    """A YouTube source a fanpage clips from (Mode 7). `kind` is detected from
    the pasted URL (services.youtube_source.classify_url):

      - "channel"  → watched automatically via its RSS feed; `channel_id`
                     (UC…) is resolved once from an @handle / /c/ URL and
                     cached here.
      - "playlist" → its RSS feed, every entry processed once (no age limit —
                     a playlist is a deliberately curated bank).
      - "video"    → a single video, processed once.

    `direction` is an optional note steered into the highlight prompt
    (e.g. "focus on the conflict moments", "skip the sponsor read").
    """

    __tablename__ = "yt_clip_sources"

    id = Column(Integer, primary_key=True, index=True)
    fanpage_id = Column(Integer, ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False, index=True)
    url = Column(Text, nullable=False)
    kind = Column(String(16), nullable=False)          # channel | playlist | video
    channel_id = Column(String(64), nullable=True)     # UC… (channel sources)
    playlist_id = Column(String(64), nullable=True)    # PL… (playlist sources)
    video_id = Column(String(16), nullable=True)       # single-video sources
    label = Column(String(256), nullable=True)         # admin's own note
    direction = Column(Text, nullable=True)            # optional AI steering for this source
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")

    last_checked_at = Column(DateTime, nullable=True, index=True)
    last_error = Column(Text, nullable=True)
    videos_found = Column(Integer, default=0, nullable=False, server_default="0")

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    fanpage = relationship("TargetFanpage", back_populates="yt_clip_sources")
