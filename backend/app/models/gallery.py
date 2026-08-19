from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, func
from sqlalchemy.dialects.postgresql import ARRAY
from app.database import Base


class GalleryKeyword(Base):
    """Download settings for one gallery keyword (e.g. "marc marquez")."""

    __tablename__ = "gallery_keywords"

    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String(128), unique=True, nullable=False)
    niche = Column(String(64), nullable=True, index=True)  # e.g. "F1", "MotoGP", "UFC" — free text
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")
    max_images = Column(Integer, nullable=False, server_default="500")
    max_pages = Column(Integer, nullable=False, server_default="20")  # search-result pages to fetch per run
    min_width = Column(Integer, nullable=False, server_default="200")
    min_height = Column(Integer, nullable=False, server_default="200")
    source_engine = Column(String(16), nullable=False, server_default="bing")  # bing | google | 9router
    license_filter = Column(String(64), nullable=False, server_default="commercial,modify")
    last_downloaded_at = Column(DateTime, nullable=True)
    last_download_error = Column(String(512), nullable=True)
    # Best-known date of this keyword's next race/match/fight — lets
    # download_all_keywords spend freely during its press/practice/race
    # window and throttle harder the rest of the time. Detected by
    # app.services.event_calendar (article mining, then a paid web-search
    # fallback), never entered manually. NULL means "no schedule detected
    # yet" — treated like an ordinary (non-event) keyword until it is.
    next_event_date = Column(Date, nullable=True, index=True)
    # Last time refresh_keyword_event_dates tried to (re)detect next_event_date
    # for this keyword — throttles the paid search fallback independently of
    # this task's own run cadence.
    event_date_checked_at = Column(DateTime, nullable=True)
    # Precise UTC start time of the event itself, when known — see
    # app.services.event_calendar.detect_event_time (one targeted schedule
    # search, distinct from the broader "when's the next race" search behind
    # next_event_date). Refines the bare next_event_date window: lets
    # download_all_keywords tighten checking specifically around "event time
    # + Getty's ~2-3h upload lag" instead of blindly polling all day. NULL
    # until a schedule search actually finds one (schedules are often
    # unpublished until close to the event) — the date-only window/interval
    # tiers are the safe fallback in that case, never starved on it.
    next_event_datetime_utc = Column(DateTime, nullable=True)
    # Last time this event cycle's time was (attempted to be) detected —
    # throttles that search independently of event_date_checked_at, and
    # resets implicitly once next_event_date moves to a new event (a stale
    # check from the PREVIOUS event cycle shouldn't block detecting the new
    # one — see refresh_keyword_event_dates).
    event_time_checked_at = Column(DateTime, nullable=True)
    # Automatic prominence classification — "star" | "regular" | "minor", or
    # NULL if never classified. See app.services.keyword_prominence: blends
    # real scraped-mention frequency with the model's own knowledge of who's
    # a top-tier name in the niche. Lets download_all_keywords check a star
    # daily even far from their next event (they generate news year-round)
    # while throttling a minor name harder than the ordinary far-from-event
    # default. NULL/unrecognized behaves exactly like "regular" — a
    # classification failure never throttles harder than before this existed.
    prominence_tier = Column(String(16), nullable=True, index=True)
    # Last time refresh_keyword_prominence tried to classify this keyword —
    # throttles the classification call's own cadence (prominence rarely
    # changes week to week, unlike next_event_date).
    prominence_checked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class GalleryImage(Base):
    """One downloaded gallery image. source_image_url is the dedup key."""

    __tablename__ = "gallery_images"

    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String(128), nullable=False, index=True)
    # A photo can feature more than one person (e.g. two riders on a podium) —
    # extra_keywords lets the same image match under other names too, beyond
    # the single primary `keyword` it was downloaded under.
    extra_keywords = Column(ARRAY(String(128)), nullable=False, server_default="{}")
    source_image_url = Column(String(1024), unique=True, nullable=False)
    local_path = Column(String(512), nullable=False)
    public_url = Column(String(512), nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    source_engine = Column(String(16), nullable=False)  # bing | google | 9router
    license_info = Column(String(64), nullable=True)
    # Vision label at download time: "face" | "action" | "other" — lets the
    # designer pick the right kind of photo per news context.
    label = Column(String(16), nullable=True, index=True)
    # Shot date parsed from the Getty editorial caption (e.g. "...on August
    # 09, 2026 in Northampton, England.") — see
    # image_downloader._parse_caption_date. Free to obtain (already in the
    # markdown page paid for), but not guaranteed: NULL means no date could
    # be parsed from the caption (non-Getty source, unrecognized caption
    # shape), not that the photo has no real shot date.
    captured_at = Column(Date, nullable=True, index=True)
    is_used = Column(Boolean, default=False, nullable=False, server_default="false", index=True)
    # When this image was last picked for a render — drives the reuse cooldown
    # (don't reuse within N days) separately from is_used (which only tracks
    # "has this ever been used", not "how recently").
    last_used_at = Column(DateTime, nullable=True, index=True)
    downloaded_at = Column(DateTime, server_default=func.now(), nullable=False)
    # Soft delete: the row (and its source_image_url) stays so the same URL is
    # never re-downloaded. local_path/public_url are left as historical
    # references even though the file on disk is removed.
    is_deleted = Column(Boolean, default=False, nullable=False, server_default="false", index=True)
    deleted_at = Column(DateTime, nullable=True)
