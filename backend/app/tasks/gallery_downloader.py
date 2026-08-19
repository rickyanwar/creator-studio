"""Gallery downloader tasks — keep the image gallery stocked per keyword.

Beat ticks every 30 minutes; each active keyword self-throttles to one
download run per 24 hours (spec: daily) by default. That base interval is
then shifted by up to three independent, automatic signals (see
_interval_hours_for):
  - Inside the keyword's own press/practice/race window (once
    next_event_date is known), it TIGHTENS to _EVENT_WINDOW_INTERVAL_HOURS —
    Getty publishes progressively through race weekend, so a single daily
    check would miss same-day photos until the day after.
  - Outside that window, it STRETCHES the further away the next event is
    (_far_from_event_interval_hours) — a quiet stretch between events is
    exactly where re-checking buys nothing.
  - Once prominence_tier is classified, a "star" is floored back to daily
    even far from any event (they generate news year-round); a "minor"
    stretches whatever interval would otherwise apply even further.
Every signal defaults to today's un-shifted behavior until its own
background task (refresh_keyword_event_dates, refresh_keyword_prominence)
has actually classified a keyword — never silently starves one on an
assumption. "Download Now" from the UI can still run any keyword
immediately via download_keyword.delay().
"""

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Celery's own beat schedule runs on Asia/Jakarta (celery_app.py), and that's
# this app's operational "wall clock" — but plain UTC's calendar date rolls
# over 7 hours earlier than WIB's. Any comparison against a bare DATE
# (next_event_date, "today" for event-window math) uses this, not UTC's
# .date(), so "today" here always means the same day a human on WIB would
# call today. Elapsed-time math (now - last_downloaded_at) is unaffected —
# a duration is the same regardless of which zone's date you'd label it with.
_WIB = timezone(timedelta(hours=7))

from sqlalchemy.exc import IntegrityError

from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.config import get_settings

_KEYWORD_INTERVAL_HOURS = 24

# web/fetch is a paid call (jina-reader) — don't spend it where it won't pay
# off. "Where it pays off" splits into two tiers (2026-08-17, replacing one
# flat max_images cap that every keyword shared regardless of popularity):
#
# - Actively newsworthy (mentioned in a recently scraped article — a rider
#   racing every week, say) → genuinely new editorial coverage keeps showing
#   up on Getty every event, so it's worth building a deep archive: grows all
#   the way to _ACTIVE_KEYWORD_CEILING. This is gated on being IN THE NEWS,
#   not on how often OUR system has historically picked its photos — pick
#   rate is a lagging signal a keyword that was previously stuck at capacity
#   (see download_keyword's docstring) could never climb out of on its own.
# - Quiet (not currently in the news) → capped much lower
#   (_QUIET_KEYWORD_CEILING), and only topped up while its own cooldown-fresh
#   pool is thin relative to its actual pick rate (_fresh_supply_status) —
#   no point re-fetching a subject nothing is currently drawing from.
_MENTION_LOOKBACK_DAYS = 7
_ACTIVE_KEYWORD_CEILING = 500
_QUIET_KEYWORD_CEILING = 60

# Event-aware throttle (2026-08-19), on top of the newsworthy/quiet split
# above: a keyword can also carry a detected GalleryKeyword.next_event_date
# (see app.services.event_calendar + refresh_keyword_event_dates below). If
# today falls inside that event's press/practice/race window, the keyword is
# spent on like any other newsworthy one regardless of whether an article
# happened to mention it in the last _MENTION_LOOKBACK_DAYS. Outside the
# window (and only once we actually know when the next event is — a keyword
# with no detected date yet keeps the old flat interval) the per-keyword
# download interval is stretched well past 24h, since a quiet stretch
# between events is exactly where re-fetching buys nothing.
# How many days before next_event_date the window opens — varies by real
# event-week structure, researched 2026-08-20 rather than assumed:
#   MotoGP: Thu press conf, Fri practice, Sat quali+SPRINT (a race itself),
#            Sun race → window opens H-3 (Thursday).
#   F1:     Fri driver press + FP1/FP2, Sat FP3+quali+team press, Sun race
#            → window opens H-2 (Friday) — press moved off Thursday in the
#            current format.
#   UFC/Boxing: Wed media day, Thu press conf, Fri weigh-in, Sat/Sun fight
#            → window opens H-3 (Wednesday).
#   Everything else (e.g. NBA — no multi-day press/practice lead-up, games
#            are same-day) falls back to _DEFAULT_EVENT_WINDOW_DAYS_BEFORE;
#            harmless since a team-sport keyword rarely gets a clean single
#            next_event_date out of event_calendar anyway.
_NICHE_EVENT_WINDOW_DAYS_BEFORE = {
    "motogp": 3,
    "f1": 2,
    "ufc": 3,
    "boxing": 3,
}
_DEFAULT_EVENT_WINDOW_DAYS_BEFORE = 2


def _event_window_days_before(niche: str | None) -> int:
    return _NICHE_EVENT_WINDOW_DAYS_BEFORE.get((niche or "").strip().lower(), _DEFAULT_EVENT_WINDOW_DAYS_BEFORE)


# Tiered rather than one flat number (2026-08-20 tightening — a normal-day
# empirical check showed ~51 keywords still fetching once every 24h each):
# the further off the next event is, the less often it's worth spending a
# fetch to check whether anything new showed up. (days_until_event, interval)
# — first threshold days_until_event is <=, so 3 covers "just outside the
# window" up through 6, etc. A keyword whose date has passed and hasn't been
# refreshed yet (negative days_until) falls into the last, longest tier —
# safe default until refresh_keyword_event_dates catches up.
_FAR_FROM_EVENT_INTERVAL_TIERS = (
    (6, 48),     # 3-6 days out: check every 2 days
    (13, 96),    # 7-13 days out: every 4 days
    (None, 168),  # 14+ days out (or stale/unknown-future): weekly
)
# How often refresh_keyword_event_dates is allowed to spend the paid
# web-search fallback (event_calendar.detect_next_event_date) on the SAME
# keyword — independent of that task's own run cadence, so a keyword with no
# discoverable schedule doesn't get hammered with search calls daily.
_EVENT_LOOKUP_INTERVAL_DAYS = 5


def _far_from_event_interval_hours(days_until_event: int) -> int:
    for threshold, hours in _FAR_FROM_EVENT_INTERVAL_TIERS:
        if threshold is None or days_until_event <= threshold:
            return hours
    return _FAR_FROM_EVENT_INTERVAL_TIERS[-1][1]  # unreachable (last tier's threshold is None)


# Inside the event window itself, check much more often than the ordinary
# daily base — Getty publishes progressively through a race weekend (fresh
# practice shots as each session wraps, race photos landing roughly 2-3h
# after the race itself finishes), not all at once. A once-daily check would
# miss same-day photos entirely until the next day's run.
_EVENT_WINDOW_INTERVAL_HOURS = 4

# When GalleryKeyword.next_event_datetime_utc IS known (event_calendar.
# detect_event_time found a real schedule — not always available, sessions
# are sometimes only published a day or two ahead), narrow the check
# interval further, but ONLY in a window around the actual moment it
# matters: event time + Getty's own typical upload lag. Outside that narrow
# window on the same day, fall back to _EVENT_WINDOW_INTERVAL_HOURS —
# there's nothing to gain from checking tightly hours before the session
# has even happened.
_EVENT_TIME_UPLOAD_BUFFER_HOURS = 3   # Getty's own typical post-event upload lag
_EVENT_TIME_TIGHT_WINDOW_BEFORE_HOURS = 1  # start tightening this long before the estimated upload moment
_EVENT_TIME_TIGHT_WINDOW_AFTER_HOURS = 4   # ...and keep it tight this long after, in case the upload lands late
_EVENT_TIME_TIGHT_INTERVAL_HOURS = 1


# Prominence-aware throttle (2026-08-20), on top of the event-window/quiet
# split: GalleryKeyword.prominence_tier (see app.services.keyword_prominence
# + refresh_keyword_prominence below) shifts the base interval either way. A
# "star" is worth checking daily regardless of how far the next event is —
# they generate news year-round (transfers, interviews, controversies) that
# the event-only signal would otherwise miss. A "minor" name is the opposite:
# even less worth checking than the ordinary far-from-event default. NULL or
# an unrecognized value behaves like "regular" — exactly today's un-shifted
# interval, so a keyword that hasn't been classified yet (or a failed
# classification) is never throttled harder than before this existed.
_PROMINENCE_MINOR_MULTIPLIER = 2

# next_event_date is a bare DATE, and next_event_datetime_utc (the precise
# time — see above) isn't always known (schedules are sometimes only
# published a day or two ahead, and this app's own date math runs on WIB —
# see _WIB below — while celery's beat schedule runs on Asia/Jakarta too). A
# race whose local time falls late in the day can land on the NEXT calendar
# date, and Getty's own post-race upload lands ~2-3h after the race finishes
# on top of that — so closing the window at exactly next_event_date risks
# cutting off checks right as the freshest post-race photos start landing.
# Extending the window's close by a day absorbs that slop as a safety net,
# independent of whether the precise time was ever found.
_EVENT_WINDOW_DAYS_AFTER = 1


def _in_event_window(kw, today) -> bool:
    if kw.next_event_date is None:
        return False
    window_start = kw.next_event_date - timedelta(days=_event_window_days_before(kw.niche))
    window_end = kw.next_event_date + timedelta(days=_EVENT_WINDOW_DAYS_AFTER)
    return window_start <= today <= window_end


def _interval_hours_for(kw, today, now: datetime) -> int:
    """`now` is naive UTC (matches next_event_datetime_utc, which
    detect_event_time returns in UTC) — used only for the tight-window check
    around a known precise event time; every other comparison here is
    date-only (see today, WIB-based)."""
    in_event_window = _in_event_window(kw, today)
    if in_event_window and kw.next_event_datetime_utc is not None:
        target = kw.next_event_datetime_utc + timedelta(hours=_EVENT_TIME_UPLOAD_BUFFER_HOURS)
        tight_start = target - timedelta(hours=_EVENT_TIME_TIGHT_WINDOW_BEFORE_HOURS)
        tight_end = target + timedelta(hours=_EVENT_TIME_TIGHT_WINDOW_AFTER_HOURS)
        base = _EVENT_TIME_TIGHT_INTERVAL_HOURS if tight_start <= now <= tight_end else _EVENT_WINDOW_INTERVAL_HOURS
    elif in_event_window:
        base = _EVENT_WINDOW_INTERVAL_HOURS
    elif kw.next_event_date is not None:
        base = _far_from_event_interval_hours((kw.next_event_date - today).days)
    else:
        base = _KEYWORD_INTERVAL_HOURS

    if kw.prominence_tier == "star":
        return min(base, _KEYWORD_INTERVAL_HOURS)
    if kw.prominence_tier == "minor":
        return base * _PROMINENCE_MINOR_MULTIPLIER
    return base


# Usage-based fresh-supply target for a QUIET keyword: tracks its actual pick
# rate instead of a number every keyword shares. FRESH_POOL_TARGET_WEEKS is
# how many weeks of fresh supply, at the observed pick rate, to stay ahead
# by; FLOOR is the minimum buffer even at ~0 usage, so a brand-new or
# never-mentioned keyword still bootstraps past zero instead of starving.
_USAGE_LOOKBACK_DAYS = 30
_FRESH_POOL_TARGET_WEEKS = 3
_FRESH_POOL_FLOOR = 8

logger = logging.getLogger(__name__)
settings = get_settings()


def _fresh_supply_status(db, keyword: str) -> tuple[int, int, int]:
    """(fresh_count, target, active_count) for one keyword.

    fresh_count: images currently past the reuse-cooldown gate (usable right
    now without violating [[gallery-reuse-cooldown]]/find_gallery_datauri's
    14-28 day window — using the floor here is intentionally conservative,
    i.e. this may slightly undercount what's "fresh").
    target: how big that fresh pool should be for a QUIET keyword, scaled to
    its actual pick rate over the last _USAGE_LOOKBACK_DAYS (a
    distinct-images-with-a-recent-last_used_at count — the closest proxy to
    "times picked" available without a dedicated usage-events log, and a
    reasonable one since the cooldown means an image is rarely picked twice
    inside one lookback window). Not used for an actively-newsworthy keyword
    — see _ACTIVE_KEYWORD_CEILING in download_all_keywords."""
    from app.models.gallery import GalleryImage
    from app.services.design_images import GALLERY_REUSE_COOLDOWN_MIN_DAYS

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cooldown_cutoff = now - timedelta(days=GALLERY_REUSE_COOLDOWN_MIN_DAYS)
    usage_cutoff = now - timedelta(days=_USAGE_LOOKBACK_DAYS)

    active_count = (
        db.query(GalleryImage)
        .filter(GalleryImage.keyword == keyword, GalleryImage.is_deleted == False)
        .count()
    )
    fresh_count = (
        db.query(GalleryImage)
        .filter(
            GalleryImage.keyword == keyword, GalleryImage.is_deleted == False,
            (GalleryImage.last_used_at.is_(None)) | (GalleryImage.last_used_at < cooldown_cutoff),
        )
        .count()
    )
    recent_picks = (
        db.query(GalleryImage)
        .filter(
            GalleryImage.keyword == keyword, GalleryImage.is_deleted == False,
            GalleryImage.last_used_at >= usage_cutoff,
        )
        .count()
    )
    weekly_rate = recent_picks / (_USAGE_LOOKBACK_DAYS / 7)
    target = int(min(_QUIET_KEYWORD_CEILING, max(_FRESH_POOL_FLOOR, round(weekly_rate * _FRESH_POOL_TARGET_WEEKS))))
    return fresh_count, target, active_count


def _recently_mentioned(db, keyword: str, cutoff: datetime) -> bool:
    """Whether `keyword` appears in any article scraped since `cutoff` —
    a proxy for "is this subject currently newsworthy", so gallery spend
    tracks what's actually being written about instead of blindly refreshing
    every keyword on a timer."""
    from sqlalchemy import or_
    from app.models.scraped_articles import ScrapedArticle

    needle = f"%{keyword.lower()}%"
    return (
        db.query(ScrapedArticle.id)
        .filter(
            ScrapedArticle.scraped_at >= cutoff,
            or_(
                ScrapedArticle.scraped_title.ilike(needle),
                ScrapedArticle.scraped_content.ilike(needle),
            ),
        )
        .first()
        is not None
    )


@celery_app.task(name="app.tasks.gallery_downloader.download_all_keywords")
def download_all_keywords():
    """Dispatch a download for every active keyword whose daily interval
    elapsed and that's below its tier's ceiling — actively-newsworthy
    keywords (mentioned in a recently scraped article) grow toward
    _ACTIVE_KEYWORD_CEILING regardless of past pick rate; quiet ones stop
    once their own cooldown-fresh pool already covers their actual usage
    (_fresh_supply_status), capped at the much smaller
    _QUIET_KEYWORD_CEILING. Both exist purely to avoid spending paid
    web/fetch calls where they won't help.

    Skips entirely while Settings.gallery_scraping_paused is set — a global
    kill switch for the scheduled sweep. Doesn't affect an explicit
    "Download Now" (download_keyword.delay called directly from the API) —
    that always runs regardless of these budget checks."""
    db = SessionLocal()
    try:
        from app.models.gallery import GalleryKeyword
        from app.models.settings import Settings

        row = db.query(Settings).filter_by(id=1).first()
        if row and row.gallery_scraping_paused:
            logger.info("Gallery: scraping sweep paused globally — skipping")
            return

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        today = datetime.now(_WIB).date()
        mention_cutoff = now - timedelta(days=_MENTION_LOOKBACK_DAYS)
        keywords = db.query(GalleryKeyword).filter(GalleryKeyword.is_active == True).all()

        for kw in keywords:
            in_event_window = _in_event_window(kw, today)
            interval_hours = _interval_hours_for(kw, today, now)
            if kw.last_downloaded_at and now - kw.last_downloaded_at < timedelta(hours=interval_hours):
                continue

            fresh_count, usage_target, active_count = _fresh_supply_status(db, kw.keyword)
            mentioned = _recently_mentioned(db, kw.keyword, mention_cutoff)
            active_tier = mentioned or in_event_window
            tier_label = "event window" if in_event_window else ("newsworthy" if mentioned else "quiet")

            if active_tier:
                if active_count >= _ACTIVE_KEYWORD_CEILING:
                    logger.debug(
                        "Gallery: keyword %r (%s) already at its ceiling (%d/%d) — skipping",
                        kw.keyword, tier_label, active_count, _ACTIVE_KEYWORD_CEILING,
                    )
                    continue
                needed = _ACTIVE_KEYWORD_CEILING - active_count
            else:
                if active_count >= _QUIET_KEYWORD_CEILING:
                    logger.debug("Gallery: keyword %r (quiet) at its ceiling (%d/%d) — skipping", kw.keyword, active_count, _QUIET_KEYWORD_CEILING)
                    continue
                if fresh_count >= usage_target:
                    logger.debug(
                        "Gallery: keyword %r (quiet) has enough fresh supply for its usage (%d fresh >= target %d) — skipping",
                        kw.keyword, fresh_count, usage_target,
                    )
                    continue
                needed = min(usage_target - fresh_count, _QUIET_KEYWORD_CEILING - active_count)

            # No point spending the "{keyword} press" top-up call inside the
            # event window itself — Getty is actively flooding the bare name
            # with press/practice/race coverage that day, so the plain
            # search alone already saturates max_num. Top-up still earns its
            # keep for a keyword that's merely "newsworthy" (mentioned in an
            # article, but not currently in its own event window), where the
            # bare name search is more likely to run dry before max_num.
            allow_topup = active_tier and not in_event_window
            download_keyword.delay(kw.id, max_num=needed, allow_topup=allow_topup)
            logger.info(
                "Gallery: dispatched keyword %d (%s, %s) — active=%d, fresh=%d/target=%d, requesting %d",
                kw.id, kw.keyword, tier_label, active_count, fresh_count, usage_target, needed,
            )
    finally:
        db.close()


@celery_app.task(name="app.tasks.gallery_downloader.download_keyword", bind=True, max_retries=1)
def download_keyword(self, keyword_id: int, max_num: int | None = None, allow_topup: bool = True):
    """Download new images for one keyword: collect URLs → dedup → validate → store.

    `max_num` caps how many NEW images this run may add. Defaults to the
    keyword's remaining room under its hard ceiling (max_images minus what's
    already active) rather than blindly requesting max_images every run —
    that bug was why 54/57 active keywords had silently grown 2x+ past their
    own cap (2026-08-17) and then got stuck skipped forever by the capacity
    check in download_all_keywords, which never revisits a count that only
    grows. The scheduled sweep now passes a smaller, usage-based figure via
    this param (see _fresh_supply_status); an explicit "Download Now" from
    the UI leaves it unset and gets the default top-up-to-ceiling behavior.

    `allow_topup` (default True) is forwarded to image_downloader.download_images
    — the scheduled sweep passes False for quiet-tier keywords, skipping the
    second "{keyword} press" web/fetch call outright for a tier where it
    rarely pays off (2026-08-20 tightening).
    """
    db = SessionLocal()
    try:
        from app.models.gallery import GalleryKeyword, GalleryImage
        from app.services.image_downloader import download_images, keyword_slug

        kw = db.query(GalleryKeyword).filter_by(id=keyword_id).first()
        if not kw or not kw.is_active:
            return

        # Stamp immediately so a failing keyword still respects its interval
        kw.last_downloaded_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        active_count = (
            db.query(GalleryImage)
            .filter(GalleryImage.keyword == kw.keyword, GalleryImage.is_deleted == False)
            .count()
        )
        effective_max = max_num if max_num is not None else max(0, kw.max_images - active_count)
        if effective_max <= 0:
            logger.debug(
                "Gallery: keyword %d (%s) has no room to download (active=%d, cap=%d)",
                keyword_id, kw.keyword, active_count, kw.max_images,
            )
            return

        slug = keyword_slug(kw.keyword)
        dest_dir = Path(settings.storage_base_path) / "gallery" / slug

        # Dedup: all URLs ever stored for this keyword (global uniqueness is
        # still enforced by the unique constraint on insert)
        skip_urls = {
            url for (url,) in
            db.query(GalleryImage.source_image_url).filter(GalleryImage.keyword == kw.keyword).all()
        }

        try:
            results = download_images(
                keyword=kw.keyword,
                dest_dir=dest_dir,
                max_num=effective_max,
                min_size=(kw.min_width, kw.min_height),
                license_filter=kw.license_filter,
                skip_urls=skip_urls,
                max_pages=kw.max_pages,
                allow_topup=allow_topup,
            )
        except Exception as exc:
            kw.last_download_error = str(exc)[:512]
            db.commit()
            logger.error("Gallery: keyword %d (%s) download failed: %s", keyword_id, kw.keyword, exc)
            return

        saved = 0
        for item in results:
            image = GalleryImage(
                keyword=kw.keyword,
                source_image_url=item.source_url,
                local_path=item.local_path,
                public_url=f"{settings.storage_base_url.rstrip('/')}/gallery/{slug}/{item.filename}",
                width=item.width,
                height=item.height,
                source_engine=item.engine,
                license_info=kw.license_filter,
                label=item.label,
                captured_at=item.captured_at,
            )
            db.add(image)
            try:
                db.commit()
                saved += 1
            except IntegrityError:
                # URL already stored under another keyword — drop the duplicate file
                db.rollback()
                Path(item.local_path).unlink(missing_ok=True)

        kw.last_download_error = None
        db.commit()

        logger.info(
            "Gallery: keyword %d (%s) — %d candidates, %d saved",
            keyword_id, kw.keyword, len(results), saved,
        )

    except Exception as exc:
        db.rollback()
        logger.error("Gallery: keyword %d failed: %s", keyword_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=300)
    finally:
        db.close()


@celery_app.task(name="app.tasks.gallery_downloader.refresh_keyword_event_dates")
def refresh_keyword_event_dates():
    """Keep GalleryKeyword.next_event_date current so download_all_keywords
    knows when a keyword is inside its press/practice/race window (spend
    freely, see _event_window_days_before) versus far from it (throttle
    harder, see _far_from_event_interval_hours).

    Runs daily; each keyword is only re-checked once its own
    event_date_checked_at is older than _EVENT_LOOKUP_INTERVAL_DAYS (or a
    previously-detected date has already passed), so this doesn't spend the
    paid web-search fallback in event_calendar.detect_next_event_date any
    more often than that per keyword, however frequently the task itself runs.
    """
    db = SessionLocal()
    try:
        from app.models.gallery import GalleryKeyword
        from app.services.event_calendar import detect_next_event_date

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        today = datetime.now(_WIB).date()
        lookup_cutoff = now - timedelta(days=_EVENT_LOOKUP_INTERVAL_DAYS)

        keywords = db.query(GalleryKeyword).filter(GalleryKeyword.is_active == True).all()
        for kw in keywords:
            if kw.next_event_date and kw.next_event_date >= today:
                continue  # already know about an upcoming event — nothing to refresh yet
            if kw.event_date_checked_at and kw.event_date_checked_at >= lookup_cutoff:
                continue  # checked too recently to justify another paid search fallback

            try:
                found = detect_next_event_date(db, kw.keyword, kw.niche, allow_search_fallback=True)
            except Exception as exc:
                logger.warning("Gallery: event-date detection failed for %r: %s", kw.keyword, exc)
                found = None

            kw.event_date_checked_at = now
            if found:
                if kw.next_event_date != found:
                    # A new event cycle — any precise time we'd pinned down
                    # belonged to the OLD date, so it must not carry over.
                    kw.next_event_datetime_utc = None
                    kw.event_time_checked_at = None
                kw.next_event_date = found
                logger.info("Gallery: keyword %r next event detected as %s", kw.keyword, found)
            db.commit()
    finally:
        db.close()


@celery_app.task(name="app.tasks.gallery_downloader.refresh_keyword_event_times")
def refresh_keyword_event_times():
    """Try to pin down GalleryKeyword.next_event_datetime_utc — the exact
    start time of a keyword's already-known next_event_date — once that
    keyword has entered its own event window (see _in_event_window).

    Runs every few hours. Retries at most once a day within the SAME event
    cycle while still unknown (a schedule for a later session is sometimes
    only published a day or two ahead of it), and stops entirely once either
    a time is found or the window closes; a NEW event cycle gets a fresh
    attempt because refresh_keyword_event_dates clears
    next_event_datetime_utc/event_time_checked_at whenever next_event_date
    moves to a new date.
    """
    db = SessionLocal()
    try:
        from app.models.gallery import GalleryKeyword
        from app.services.event_calendar import detect_event_time

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        today = datetime.now(_WIB).date()
        retry_cutoff = now - timedelta(hours=20)

        keywords = db.query(GalleryKeyword).filter(GalleryKeyword.is_active == True).all()
        for kw in keywords:
            if not _in_event_window(kw, today):
                continue
            if kw.next_event_datetime_utc is not None:
                continue  # already have a precise time for this cycle
            if kw.event_time_checked_at and kw.event_time_checked_at >= retry_cutoff:
                continue  # tried recently this cycle — retry at most daily

            try:
                found = detect_event_time(kw.keyword, kw.niche, kw.next_event_date)
            except Exception as exc:
                logger.warning("Gallery: event-time detection failed for %r: %s", kw.keyword, exc)
                found = None

            kw.event_time_checked_at = now
            if found:
                kw.next_event_datetime_utc = found
                logger.info("Gallery: keyword %r event time detected as %s UTC", kw.keyword, found)
            db.commit()
    finally:
        db.close()


_PROMINENCE_RECHECK_INTERVAL_DAYS = 30


@celery_app.task(name="app.tasks.gallery_downloader.refresh_keyword_prominence")
def refresh_keyword_prominence():
    """Keep GalleryKeyword.prominence_tier current so download_all_keywords
    can check a "star" daily even far from their next event, and throttle a
    "minor" name harder than the ordinary far-from-event default (see
    _interval_hours_for).

    Runs weekly; each keyword is only re-classified once
    prominence_checked_at is older than _PROMINENCE_RECHECK_INTERVAL_DAYS (or
    never set), since prominence rarely shifts week to week — unlike
    next_event_date this doesn't spend any paid web/fetch at all, just one
    cheap 9Router text call per keyword due for a recheck.
    """
    db = SessionLocal()
    try:
        from app.models.gallery import GalleryKeyword
        from app.services.keyword_prominence import classify_prominence

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        recheck_cutoff = now - timedelta(days=_PROMINENCE_RECHECK_INTERVAL_DAYS)

        keywords = db.query(GalleryKeyword).filter(GalleryKeyword.is_active == True).all()
        for kw in keywords:
            if kw.prominence_checked_at and kw.prominence_checked_at >= recheck_cutoff:
                continue

            tier = classify_prominence(db, kw.keyword, kw.niche)
            kw.prominence_checked_at = now
            if tier:
                kw.prominence_tier = tier
                logger.info("Gallery: keyword %r classified as prominence=%s", kw.keyword, tier)
            db.commit()
    finally:
        db.close()


@celery_app.task(name="app.tasks.gallery_downloader.scan_gallery_closeup_filter", bind=True, max_retries=1)
def scan_gallery_closeup_filter(self, criteria: str, keyword: str | None = None) -> dict:
    """Manual, on-demand AI scan (never automatic) — checks every non-deleted
    gallery image against an admin-typed criteria and reports which ones
    DON'T match. `keyword=None` scans the whole gallery; a specific keyword
    string scopes it to just that one. Read-only: nothing is ever deleted
    here. The API dispatches this and polls task state for progress
    ({"done", "total"} while running); the final return value is the
    candidate list the admin reviews and confirms via
    POST /gallery/images/bulk-delete — this task never deletes anything
    itself, by design, since a global scan can span the whole gallery."""
    db = SessionLocal()
    try:
        from app.models.gallery import GalleryImage
        from app.services.design_images import classify_closeup_match

        q = db.query(GalleryImage).filter(GalleryImage.is_deleted == False)
        if keyword:
            q = q.filter(GalleryImage.keyword == keyword)
        images = q.all()
        total = len(images)

        candidates = []
        for i, image in enumerate(images):
            self.update_state(state="PROGRESS", meta={"done": i, "total": total})
            try:
                image_bytes = Path(image.local_path).read_bytes()
            except OSError as exc:
                logger.warning("Gallery: AI filter scan — image %d unreadable (%s), skipping", image.id, exc)
                continue

            result = classify_closeup_match(image_bytes, criteria)
            if not result["match"]:
                candidates.append({
                    "id": image.id,
                    "public_url": image.public_url,
                    "keyword": image.keyword,
                    "confidence": result["confidence"],
                })

        logger.info(
            "Gallery: AI filter scan (%s) — %d/%d flagged for review",
            keyword or "ALL KEYWORDS", len(candidates), total,
        )
        return {"done": total, "total": total, "candidates": candidates}

    except Exception as exc:
        logger.error("Gallery: AI filter scan failed (keyword=%s): %s", keyword, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
