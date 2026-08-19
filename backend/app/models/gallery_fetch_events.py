from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, func
from app.database import Base


class GalleryFetchEvent(Base):
    """One row per paid 9Router web/fetch (jina-reader) call, logged from the
    single choke point every caller routes through
    (image_downloader._9router_fetch_markdown) — exists purely to make
    actual daily Jina spend queryable instead of estimated, after
    2026-08-20's throttling work (see gallery_downloader.py). Covers every
    caller, not just the gallery keyword downloader: editorial_gate's
    fact-check search, event_calendar's date/time detection, and
    design_images.fetch_subject_datauri's live search all route through the
    same paid call and get tagged with their own `context`.

    Logging never blocks or fails the actual fetch — a DB error here is
    swallowed (see _log_fetch_event), so this table can lag or miss rows
    without ever affecting the pipeline it's observing.
    """

    __tablename__ = "gallery_fetch_events"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)

    # "gallery" | "editorial_factcheck" | "event_date_search" |
    # "event_time_search" | "subject_datauri"
    context = Column(String(32), nullable=False, index=True)
    keyword = Column(String(128), nullable=True, index=True)  # the search phrase, where applicable
    niche = Column(String(64), nullable=True)
    url = Column(String(1024), nullable=True)
    success = Column(Boolean, nullable=False, default=True, index=True)
    error_message = Column(Text, nullable=True)
    latency_ms = Column(Integer, nullable=True)
