"""Classifies each gallery keyword's prominence within its niche — automatic,
never manually tagged. Used purely to shape gallery download throttling (see
app.tasks.gallery_downloader): a genuine star (championship contender, top
name in the sport) tends to generate news even between events — off-track
drama, transfers, interviews — so it's worth checking daily regardless of
how far the next race/match/fight is. A backmarker/minor name mostly only
matters around their own events, so it's safe to check them even less than
the ordinary far-from-event throttle.

Classification blends two signals into one cheap 9Router TEXT call (not a
paid web/fetch — this never spends gallery budget):
  1. How often the keyword has actually shown up in our own scraped news
     over a long lookback (real, current activity, not just reputation).
  2. The model's own knowledge of who is a top-tier name in that niche
     (catches a big star going through a quiet news stretch, which (1) alone
     would misread as "minor").

Never raises — a classification failure just leaves prominence_tier
unchanged (None on a first attempt), and the downloader treats a
None/unrecognized tier exactly like "regular" (today's existing behavior),
so a flaky call never throttles a keyword harder than before this feature
existed.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_MENTION_LOOKBACK_DAYS = 60
_VALID_TIERS = ("star", "regular", "minor")


def _mention_count(db, keyword: str, cutoff: datetime) -> int:
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
        .count()
    )


def classify_prominence(db, keyword: str, niche: str | None) -> str | None:
    """Best-effort prominence tier for `keyword` — "star" | "regular" |
    "minor", or None if classification failed (caller should leave the
    existing value alone, not overwrite it with a guess)."""
    from app.services.ai_caption import generate_caption

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=_MENTION_LOOKBACK_DAYS)
    mentions = _mention_count(db, keyword, cutoff)

    prompt = f"""Classify "{keyword}"{f' ({niche})' if niche else ""} by prominence within their sport, for deciding how often to check for new photos of them.

Our own news scraper has mentioned them in {mentions} articles over the last {_MENTION_LOOKBACK_DAYS} days (0 is normal for a real but less-famous name; a genuine star is usually well above 0).

Tiers:
- "star": a top-tier, widely-known name — championship contenders, top ~10 ranked, a rider/driver/fighter major fans would instantly recognize, someone who generates news even outside their own races (transfers, interviews, controversies).
- "regular": a real, active competitor in the sport, but not a headline name — news about them is mostly tied to their own races/matches/fights.
- "minor": a marginal or barely-active name — rarely covered, backmarker, or borderline irrelevant to this niche's fanbase.

Reply with ONLY one word: star, regular, or minor."""
    try:
        raw, _ = generate_caption(prompt)
        tier = (raw or "").strip().lower()
        for candidate in _VALID_TIERS:
            if candidate in tier:
                return candidate
        logger.warning("Prominence: unrecognized classification for %r: %r", keyword, raw)
        return None
    except Exception as exc:
        logger.warning("Prominence: classification failed for %r: %s", keyword, exc)
        return None
