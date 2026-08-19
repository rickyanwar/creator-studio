"""Detects each gallery keyword's next race/match/fight date (and, once it's
close enough, the exact start time) — purely to know when to stop
throttling its gallery downloads. See app.tasks.gallery_downloader: a
keyword whose subject has nothing scheduled soon gets checked (and
re-fetched) rarely; one entering its press/practice/race window gets
treated like any other newsworthy keyword, and checked especially tightly
around its own event time + Getty's upload lag once that time is known.

Two functions, both never-raising (a failure just returns None and leaves
the caller's existing state alone):

  detect_next_event_date — two-tier, cheapest first:
    1. Mine articles already scraped for our content pipeline (free — no
       extra web/fetch spend) for a date mention tied to the keyword.
    2. Only if that turns up nothing does the caller spend ONE paid 9Router
       web-search fetch (the same jina-reader call gallery downloads use)
       asking for the keyword's upcoming schedule — gated by the caller's
       own event_date_checked_at cooldown, not by anything in here.

  detect_event_time — a separate, more targeted paid search specifically for
    the exact start time on an already-known event_date (schedule/time-table
    pages, not general news), meant to be tried once per event cycle once a
    keyword enters its window — see event_time_checked_at.
"""

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

logger = logging.getLogger(__name__)

_ARTICLE_LOOKBACK_DAYS = 21  # wider than the "is it in the news" 7-day window — schedules get announced ahead of time
_MAX_SNIPPET_CHARS = 2500
_MAX_ARTICLES = 8


def _parse_date_json(raw: str) -> date | None:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        raw_date = data.get("date")
        if not raw_date:
            return None
        return datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
    except (json.JSONDecodeError, ValueError):
        return None


def _extract_event_date(keyword: str, niche: str | None, source_text: str, today: date) -> date | None:
    from app.services.ai_caption import generate_caption

    prompt = f"""Today's date is {today.isoformat()}. You're scanning text for the next scheduled race, match, or fight date for "{keyword}"{f' ({niche})' if niche else ""}.

TEXT:
{source_text[:_MAX_SNIPPET_CHARS]}

Find the single next upcoming date (on or after today) this subject races, competes, or fights — a "race day", "fight night", "match day", "kickoff", qualifying/practice doesn't count, only the actual event date. Ignore past results and unrelated dates. If several are mentioned, pick the soonest one on or after today.

Reply with ONLY a JSON object: {{"date": "YYYY-MM-DD"}} if you find one, otherwise {{"date": null}}."""
    try:
        raw, _ = generate_caption(prompt)
        return _parse_date_json(raw)
    except Exception as exc:
        logger.warning("Event calendar: AI extraction failed for %r: %s", keyword, exc)
        return None


def _recent_article_text(db, keyword: str, cutoff: datetime) -> str:
    from sqlalchemy import or_
    from app.models.scraped_articles import ScrapedArticle

    needle = f"%{keyword.lower()}%"
    articles = (
        db.query(ScrapedArticle.scraped_title, ScrapedArticle.scraped_content)
        .filter(
            ScrapedArticle.scraped_at >= cutoff,
            or_(
                ScrapedArticle.scraped_title.ilike(needle),
                ScrapedArticle.scraped_content.ilike(needle),
            ),
        )
        .order_by(ScrapedArticle.scraped_at.desc())
        .limit(_MAX_ARTICLES)
        .all()
    )
    return "\n\n".join(f"{title}\n{(content or '')[:400]}" for title, content in articles)


def _search_event_date_text(keyword: str, niche: str | None) -> str:
    """One paid 9Router web-search fetch — only ever called by the caller
    when article mining found nothing and its own lookup cooldown allows it."""
    from app.config import get_settings
    from app.services.image_downloader import _9router_fetch_markdown

    settings = get_settings()
    query = f"{keyword} {niche or ''} next race schedule date".strip()
    url = settings.editorial_factcheck_search_url_template.format(query=quote(query))
    try:
        return _9router_fetch_markdown(url, context="event_date_search", keyword=keyword, niche=niche)[:_MAX_SNIPPET_CHARS]
    except Exception as exc:
        logger.warning("Event calendar: search fallback failed for %r: %s", keyword, exc)
        return ""


def _parse_datetime_json(raw: str) -> datetime | None:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        raw_dt = data.get("datetime_utc")
        if not raw_dt:
            return None
        return datetime.strptime(str(raw_dt)[:16], "%Y-%m-%dT%H:%M")
    except (json.JSONDecodeError, ValueError):
        return None


def detect_event_time(keyword: str, niche: str | None, event_date: date) -> datetime | None:
    """One targeted paid web-search + AI extraction for the EXACT UTC start
    time of `keyword`'s event on `event_date` — a schedule/time-table
    lookup, distinct from detect_next_event_date's broader "when's the next
    race" search (which mines free-form news mentions that rarely state an
    exact time). Meant to be called at most once per event cycle, when a
    keyword has just entered its event window and no time is known yet — see
    GalleryKeyword.event_time_checked_at / refresh_keyword_event_dates.

    Returns a naive UTC datetime, or None if no reliable time was found
    (schedules for later sessions in a race weekend are sometimes only
    published a day or two ahead) — callers should treat None as "still
    unknown, keep the safe periodic-polling fallback," not as an error."""
    from app.config import get_settings
    from app.services.ai_caption import generate_caption
    from app.services.image_downloader import _9router_fetch_markdown

    settings = get_settings()
    query = f"{keyword} {niche or ''} time schedule {event_date.isoformat()} start time".strip()
    url = settings.editorial_factcheck_search_url_template.format(query=quote(query))
    try:
        text = _9router_fetch_markdown(url, context="event_time_search", keyword=keyword, niche=niche)[:_MAX_SNIPPET_CHARS]
    except Exception as exc:
        logger.warning("Event calendar: time-schedule search failed for %r: %s", keyword, exc)
        return None
    if not text:
        return None

    prompt = f"""You're scanning web-search text for the exact start time of "{keyword}"{f' ({niche})' if niche else ""}'s event on {event_date.isoformat()}.

TEXT:
{text}

Find the specific session that subject is IN on that date (a race, sprint, fight, or match — not a practice/qualifying session unless that's genuinely all that's scheduled that day) and its start time. Sources usually state a LOCAL time and a venue/city — convert that to UTC yourself using the venue's known timezone. If multiple candidate times appear, pick the one that most specifically matches this subject's own event.

Reply with ONLY a JSON object: {{"datetime_utc": "YYYY-MM-DDTHH:MM"}} (24h, UTC) if you can confidently determine it, otherwise {{"datetime_utc": null}}."""
    try:
        raw, _ = generate_caption(prompt)
        return _parse_datetime_json(raw)
    except Exception as exc:
        logger.warning("Event calendar: time extraction failed for %r: %s", keyword, exc)
        return None


def detect_next_event_date(db, keyword: str, niche: str | None, allow_search_fallback: bool) -> date | None:
    """Best-effort next event date for `keyword`. Tries free article mining
    first; only spends a paid web-search fetch when that's empty AND
    `allow_search_fallback` is True (the caller's own cooldown decision)."""
    # "Today" for the AI prompt uses WIB (this app's operational wall clock —
    # see gallery_downloader.py's _WIB), not UTC — matters near WIB midnight,
    # where UTC's calendar date still lags 7 hours behind, which could make
    # the model treat a same-WIB-day event as "in the past" and skip it.
    today = datetime.now(timezone(timedelta(hours=7))).date()
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=_ARTICLE_LOOKBACK_DAYS)

    article_text = _recent_article_text(db, keyword, cutoff)
    if article_text:
        found = _extract_event_date(keyword, niche, article_text, today)
        if found:
            return found

    if allow_search_fallback:
        search_text = _search_event_date_text(keyword, niche)
        if search_text:
            return _extract_event_date(keyword, niche, search_text, today)

    return None
