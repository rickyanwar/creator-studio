"""News story deduplication — checks if a scraped news article reports the
same story as an earlier post for the same fan page within the dedup window.
"""

import logging
from datetime import datetime, timedelta, timezone

from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ScrapedArticle
from app.services.ai_caption import generate_caption
from app.services.editorial_gate import _parse_json

logger = logging.getLogger(__name__)

DEDUP_WINDOW_HOURS = 48
MAX_CANDIDATES = 40
_EXCLUDED_STATUSES = (PublishJobStatus.skipped, PublishJobStatus.failed)  # never posted -> a fresher article may replace it


def recent_story_jobs(
    db, fanpage_id: int, article_id: int, now: datetime | None = None
) -> list[tuple[PublishJob, ScrapedArticle]]:
    """News jobs of this fan page created in the last DEDUP_WINDOW_HOURS, newest first, at most MAX_CANDIDATES,
    excluding _EXCLUDED_STATUSES and jobs whose source_article_id == article_id. Joined with their ScrapedArticle."""
    if now is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
    elif now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)

    cutoff = now - timedelta(hours=DEDUP_WINDOW_HOURS)

    return (
        db.query(PublishJob, ScrapedArticle)
        .join(ScrapedArticle, ScrapedArticle.id == PublishJob.source_article_id)
        .filter(
            PublishJob.fanpage_id == fanpage_id,
            PublishJob.content_type == ContentType.news_content,
            PublishJob.created_at >= cutoff,
            PublishJob.status.notin_(_EXCLUDED_STATUSES),
            PublishJob.source_article_id != article_id,
        )
        .order_by(PublishJob.created_at.desc())
        .limit(MAX_CANDIDATES)
        .all()
    )


def build_prompt(niche: str, article, candidates: list) -> str:
    title = getattr(article, "scraped_title", None) or (article.get("scraped_title") if isinstance(article, dict) else "") or ""
    content = getattr(article, "scraped_content", None) or (article.get("scraped_content") if isinstance(article, dict) else "") or ""
    start = content[:600]

    candidate_lines = []
    for idx, cand in enumerate(candidates, start=1):
        if isinstance(cand, dict):
            cand_title = cand.get("scraped_title", "") or ""
            cand_content = cand.get("scraped_content", "") or ""
        elif isinstance(cand, (tuple, list)):
            cand_art = cand[1]
            if isinstance(cand_art, dict):
                cand_title = cand_art.get("scraped_title", "") or ""
                cand_content = cand_art.get("scraped_content", "") or ""
            else:
                cand_title = getattr(cand_art, "scraped_title", "") or ""
                cand_content = getattr(cand_art, "scraped_content", "") or ""
        else:
            cand_title = getattr(cand, "scraped_title", "") or ""
            cand_content = getattr(cand, "scraped_content", "") or ""

        cand_summary = cand_content[:200].replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        candidate_lines.append(f"{idx}. {cand_title} — {cand_summary}")

    numbered_candidates = "\n".join(candidate_lines)

    return f"""You check whether a new news article reports the same story as a post this {niche} Facebook page already made in the last 48 hours.
The articles come from different websites in different languages (for example Spanish, Italian, German, English). Compare the meaning, never the wording.

NEW ARTICLE
Headline: {title}
Start: {start}

EARLIER POSTS
{numbered_candidates}

"Same story" means the same news event AND the same main point: the same result, the same announcement, the same injury or absence, the same transfer or contract, or the same person's quote about the same topic.
These are NOT the same story: a different person's reaction to the same incident; a new development of an earlier story (a later session, an official decision or penalty after an incident, a medical update); a different topic about the same rider or team.
If you are not sure, answer null.

Reply with ONLY a JSON object: {{"duplicate_of": <number of the earlier post, or null>, "reason": "one short sentence"}}"""


def parse_verdict(raw: str, candidate_count: int) -> int | None:
    """1-based index from {"duplicate_of": n}; None for null, a non-integer, or n outside 1..candidate_count."""
    data = _parse_json(raw)
    if not isinstance(data, dict):
        return None
    val = data.get("duplicate_of")
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        n = val
    elif isinstance(val, str):
        try:
            n = int(val.strip())
        except (ValueError, TypeError):
            return None
    else:
        return None
    if 1 <= n <= candidate_count:
        return n
    return None


def find_duplicate_job(db, fanpage, article, now: datetime | None = None) -> PublishJob | None:
    """None when there are no candidates (NO AI call then). Otherwise ONE generate_caption call.
    Raises on AI / parse failure (caller lets the article through, like the editorial gate)."""
    fanpage_id = getattr(fanpage, "id", fanpage)
    article_id = getattr(article, "id", article)
    candidates = recent_story_jobs(db, fanpage_id, article_id, now=now)
    if not candidates:
        return None

    niche = (getattr(fanpage, "mode2_gallery_niches", None) or [None])[0] or getattr(fanpage, "name", "")
    prompt = build_prompt(niche, article, candidates)
    raw, _ = generate_caption(prompt)
    verdict = parse_verdict(raw, len(candidates))
    if verdict is None:
        return None

    matched_job = candidates[verdict - 1][0]
    data = _parse_json(raw)
    reason = data.get("reason", "") if isinstance(data, dict) else ""
    logger.info(
        "News dedup: article %s fanpage %s duplicate of job %s — reason: %s",
        article_id,
        fanpage_id,
        getattr(matched_job, "id", None),
        reason,
    )
    return matched_job
