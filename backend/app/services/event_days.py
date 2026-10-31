"""Race-day helpers shared by the destination planner and the news freshness rules (owner 2026-10-26)."""
from __future__ import annotations
import logging
from datetime import date

logger = logging.getLogger(__name__)

EVENT_NEWS_FRESH_HOURS = 6   # news must post within 6 h on race/practice days
NEWS_FRESH_HOURS = 12        # and within 12 h on other days


def event_multiplier(db, fanpage, day: date) -> int:
    """2 on a detected race/practice day for this fan page's niches, else 1 (fails safe to 1)."""
    if fanpage is None:
        return 1
    try:
        from app.tasks.publisher import _EVENT_DAY_LIMIT_MULTIPLIER, _event_boosted_daily_limit
        return _EVENT_DAY_LIMIT_MULTIPLIER if _event_boosted_daily_limit(db, fanpage, 1, day) > 1 else 1
    except Exception as exc:
        logger.warning("event_multiplier failed for fanpage %s: %s", getattr(fanpage, "id", None), exc)
        return 1


def is_event_window(db, fanpage, day: date) -> bool:
    """True when `day` falls inside an event window for this fan page (reusing event_multiplier)."""
    try:
        return event_multiplier(db, fanpage, day) > 1
    except Exception as exc:
        logger.warning("is_event_window failed for fanpage %s: %s", getattr(fanpage, "id", None), exc)
        return False


def is_live_event_news(db, job, fanpage, day: date) -> bool:
    """True when the job is a news job AND is_event_window(...) (fails safe to False)."""
    try:
        if job is None:
            return False
        from app.services.content_groups import group_of
        if group_of(getattr(job, "content_type", None)) != "news":
            return False
        if fanpage is None and getattr(job, "fanpage_id", None) and db is not None:
            from app.models.target_fanpages import TargetFanpage
            fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
        if fanpage is None:
            return False
        return is_event_window(db, fanpage, day)
    except Exception as exc:
        logger.warning("is_live_event_news failed for job %s: %s", getattr(job, "id", None), exc)
        return False


def effective_daily_target(db, destination, fanpage, day: date) -> int:
    """destination.daily_target, doubled on race days."""
    return (destination.daily_target or 0) * event_multiplier(db, fanpage, day)


def news_fresh_hours(db, fanpage, base_hours: int | None, day: date) -> int:
    """How old a news item may be when it posts: the destination's own limit, capped at 6 h on race days and 12 h otherwise."""
    base = base_hours if base_hours and base_hours > 0 else 24
    cap = EVENT_NEWS_FRESH_HOURS if event_multiplier(db, fanpage, day) > 1 else NEWS_FRESH_HOURS
    return min(base, cap)
