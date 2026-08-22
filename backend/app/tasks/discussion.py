"""Mode 4: AI discussion / hot-take content — quota-driven per fanpage.

Unlike Mode 2 (one card per incoming article), Mode 4 is paced: the beat task
`generate_discussion_content` ticks every 30 min and, for each fanpage with
Mode 4 enabled, creates debate cards up to `discussion_daily_count` per WIB day,
spread evenly across an active window (08:00–22:00 WIB) so they don't all post
at once. Topics come from:
  - "news": the freshest scraped article from the fanpage's subscribed news
    sources that hasn't already been turned into a job for this fanpage
    (fact-grounded — the article is passed to the copywriter).
  - "evergreen": the least-recently-used active DiscussionTopic seed
    (opinion-only — see news_copywriter.generate_discussion_copy). Falls back
    to the AI's own general knowledge (below) when the seed pool is empty.
  - "both": the AI's own general-knowledge hot take (see _generate_general_topic
    — fact-checked, cross-checked on a second provider) is tried FIRST, since
    this is the source that produces "does X deserve Y" / "was X right to do
    Y" style cards without needing a fresh article. Only falls back to news,
    then an evergreen seed, if the general candidate fails fact-check twice.

Each card becomes a PublishJob(content_type=discussion, status=pending_design):
  design_title=question, design_subtitle=label, design_caption=subject name.
The renderer (design_renderer.render_discussion) sources the photo from the
subject and draws the card; the publisher reuses the news single-image path.
"""

import logging
import random
from datetime import datetime, timezone, timedelta

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))

# Active window (WIB) across which the daily quota is spread. No cards are
# generated outside it — a real page admin isn't posting debates at 4am.
_WINDOW_START_HOUR = 8
_WINDOW_END_HOUR = 22

# Only draw news topics from articles scraped within this many days — a debate
# card should ride current news, not last month's.
_NEWS_FRESH_DAYS = 3


def _wib_day_bounds_utc(now_utc: datetime) -> tuple[datetime, datetime]:
    """(start, end) of the current WIB calendar day, as naive-UTC datetimes to
    match the DB's stored timestamps."""
    day_wib = now_utc.replace(tzinfo=timezone.utc).astimezone(WIB)
    start_wib = day_wib.replace(hour=0, minute=0, second=0, microsecond=0)
    end_wib = start_wib + timedelta(days=1)
    return (
        start_wib.astimezone(timezone.utc).replace(tzinfo=None),
        end_wib.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _target_by_now(quota: int, now_wib_hour: float) -> int:
    """How many cards SHOULD exist by this point in the active window, so the
    quota trickles out across the day instead of all at once. Before the window
    opens → 0; at/after it closes → the full quota."""
    if now_wib_hour < _WINDOW_START_HOUR:
        return 0
    if now_wib_hour >= _WINDOW_END_HOUR:
        return quota
    frac = (now_wib_hour - _WINDOW_START_HOUR) / (_WINDOW_END_HOUR - _WINDOW_START_HOUR)
    # +1 so the first card can fire as soon as the window opens.
    import math
    return min(quota, math.ceil(quota * frac + 0.0001) if frac > 0 else 1)


def _pick_news_article(db, fanpage):
    """Freshest article from the fanpage's subscribed sources that has no
    existing publish job for this fanpage (dedupes across every mode via the
    uq_article_fanpage constraint). Returns a ScrapedArticle or None."""
    from sqlalchemy import func
    from app.models.scraped_articles import ScrapedArticle
    from app.models.fanpage_news_sources import FanpageNewsSource
    from app.models.publish_jobs import PublishJob

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=_NEWS_FRESH_DAYS)

    jobbed = (
        db.query(PublishJob.source_article_id)
        .filter(PublishJob.fanpage_id == fanpage.id, PublishJob.source_article_id.isnot(None))
    )

    rows = (
        db.query(ScrapedArticle)
        .join(FanpageNewsSource, FanpageNewsSource.news_source_id == ScrapedArticle.news_source_id)
        .filter(
            FanpageNewsSource.fanpage_id == fanpage.id,
            FanpageNewsSource.is_active == True,
            ScrapedArticle.scraped_at >= cutoff,
            ScrapedArticle.id.notin_(jobbed),
        )
        .order_by(ScrapedArticle.scraped_at.desc())
        .limit(12)
        .all()
    )
    return random.choice(rows) if rows else None


def _pick_evergreen_topic(db, fanpage):
    """Least-recently-used active evergreen seed for this fanpage, or None."""
    from app.models.discussion_topics import DiscussionTopic

    return (
        db.query(DiscussionTopic)
        .filter(DiscussionTopic.fanpage_id == fanpage.id, DiscussionTopic.is_active == True)
        .order_by(DiscussionTopic.last_used_at.asc().nullsfirst(), DiscussionTopic.id.asc())
        .first()
    )


_RECENT_SUBJECTS_DAYS = 14
_RECENT_SUBJECTS_LIMIT = 15


def _recent_discussion_subjects(db, fanpage) -> list[str]:
    """Distinct subjects used in this fanpage's last _RECENT_SUBJECTS_DAYS
    discussion cards (up to _RECENT_SUBJECTS_LIMIT of them), so the
    general-knowledge prompt doesn't repeat the same person.

    The dedup cap is applied to the OUTPUT (distinct subjects), not the raw
    row fetch — a fanpage with a high discussion_daily_count can rack up
    well over 15 job rows within the 14-day window, and capping the SQL
    query itself at 15 rows (the bug here until 2026-08-22) meant a subject
    from a few days ago could silently fall out of the raw fetch before
    dedup ever saw it — confirmed on a real fanpage where the exact same
    line ("Alex Rins is the most underrated rider in MotoGP") got generated
    twice, 2 days apart, well inside the supposed 14-day avoid-window."""
    from app.models.publish_jobs import PublishJob, ContentType

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=_RECENT_SUBJECTS_DAYS)
    rows = (
        db.query(PublishJob.design_caption)
        .filter(
            PublishJob.fanpage_id == fanpage.id,
            PublishJob.content_type == ContentType.discussion,
            PublishJob.created_at >= cutoff,
            PublishJob.design_caption.isnot(None),
        )
        .order_by(PublishJob.created_at.desc())
        .all()
    )
    seen, out = set(), []
    for (subject,) in rows:
        if subject and subject not in seen:
            seen.add(subject)
            out.append(subject)
            if len(out) >= _RECENT_SUBJECTS_LIMIT:
                break
    return out


_RECENT_QUESTIONS_DAYS = 7
_RECENT_QUESTIONS_LIMIT = 8


def _recent_discussion_questions(db, fanpage) -> list[str]:
    """This fanpage's last _RECENT_QUESTIONS_DAYS discussion lines (ANY
    subject, most recent first) — passed to the general-knowledge prompt as
    "don't reuse this same angle" context. Distinct from
    _recent_discussion_subjects: that one stops the SAME PERSON from being
    picked again; this one stops the same TYPE OF TAKE ("is X underrated")
    from being reused on a DIFFERENT person, which subject-avoidance alone
    can't catch — see build_discussion_general_prompt's docstring."""
    from app.models.publish_jobs import PublishJob, ContentType

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=_RECENT_QUESTIONS_DAYS)
    rows = (
        db.query(PublishJob.design_title)
        .filter(
            PublishJob.fanpage_id == fanpage.id,
            PublishJob.content_type == ContentType.discussion,
            PublishJob.created_at >= cutoff,
            PublishJob.design_title.isnot(None),
        )
        .order_by(PublishJob.created_at.desc())
        .limit(_RECENT_QUESTIONS_LIMIT)
        .all()
    )
    return [q for (q,) in rows if q]


_HEADLINES_CONTEXT_DAYS = 60
_HEADLINES_CONTEXT_LIMIT = 20
_GENERAL_FACTCHECK_ATTEMPTS = 2


def _recent_headlines(db, fanpage) -> list[str]:
    """Recent article titles from this fanpage's own subscribed news sources
    (any status, not just unclaimed) — passed to the general-knowledge prompt
    as best-effort grounding so a stale training cutoff is less likely to
    contradict something this page's own feed already reported."""
    from app.models.scraped_articles import ScrapedArticle
    from app.models.fanpage_news_sources import FanpageNewsSource

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=_HEADLINES_CONTEXT_DAYS)
    rows = (
        db.query(ScrapedArticle.scraped_title)
        .join(FanpageNewsSource, FanpageNewsSource.news_source_id == ScrapedArticle.news_source_id)
        .filter(
            FanpageNewsSource.fanpage_id == fanpage.id,
            FanpageNewsSource.is_active == True,
            ScrapedArticle.scraped_at >= cutoff,
        )
        .order_by(ScrapedArticle.scraped_at.desc())
        .limit(_HEADLINES_CONTEXT_LIMIT)
        .all()
    )
    return [t for (t,) in rows if t]


def _grounding_headlines_for_claim(db, subject: str, question: str) -> list[str]:
    """Targeted retrieval for the fact-check pass: pull article titles (any
    age, not just a recent window; ANY subscribed news source in the whole
    system, not just this fanpage's own) that mention a full name appearing
    in the drafted claim.

    Two reasons this is broader than the fanpage's own feed:
    - A generic "last N headlines" sample is dominated by whatever was
      scraped in the last few hours on a busy feed and easily misses a fact
      from a week+ ago; searching by the specific names in the claim finds it
      regardless of age.
    - The fact itself (e.g. "who's the reigning champion") is objective and
      independent of which sources a given fanpage happens to subscribe to —
      restricting to the fanpage's own sources can miss it entirely if the
      story only ran on a source that fanpage doesn't follow, even though
      another fanpage's feed (or the wider system) already has it."""
    import re
    from sqlalchemy import or_
    from app.models.scraped_articles import ScrapedArticle

    # The 2+-capitalized-words pattern also snags generic phrases ("World
    # Championship", "Grand Prix") that aren't names — drop known offenders
    # so they don't crowd a busy name (e.g. "Norris") out of the top results
    # with unrelated articles from other niches that share the same phrase.
    _GENERIC_PHRASES = {"world championship", "grand prix", "hot take", "formula 1"}
    raw_names = re.findall(r"\b[A-Z][a-zA-Z'.-]+(?:\s+[A-Z][a-zA-Z'.-]+)+\b", question)
    names = {n for n in raw_names if n.lower() not in _GENERIC_PHRASES}
    if subject:
        names.add(subject)
    if not names:
        return []

    name_cond = or_(*[ScrapedArticle.scraped_title.ilike(f"%{n}%") for n in names])

    # Tier 1: name + an outcome/result keyword — the exact class of fact this
    # check exists to catch (a title/race/result already decided), prioritized
    # regardless of age so it isn't crowded out by unrelated recent mentions.
    outcome_cond = or_(*[
        ScrapedArticle.scraped_title.ilike(f"%{kw}%")
        for kw in ("champion", "crowned", "clinch", "wins the", "title winner")
    ])
    tier1 = (
        db.query(ScrapedArticle.scraped_title)
        .filter(name_cond, outcome_cond)
        .order_by(ScrapedArticle.scraped_at.desc())
        .limit(6)
        .all()
    )

    # Tier 2: fill the rest with plain recent name mentions.
    tier2 = (
        db.query(ScrapedArticle.scraped_title)
        .filter(name_cond)
        .order_by(ScrapedArticle.scraped_at.desc())
        .limit(12)
        .all()
    )

    seen: set[str] = set()
    out: list[str] = []
    for (title,) in list(tier1) + list(tier2):
        if title and title not in seen:
            seen.add(title)
            out.append(title)
        if len(out) >= 12:
            break
    return out


def _generate_general_topic(db, fanpage):
    """General-knowledge hot takes have no article/seed to fact-check
    against, so before accepting one they go through three layers of defense:

    1. Prompt-time grounding — the generation call itself is given this
       fanpage's recent headlines and told not to bet on undecided outcomes
       (see build_discussion_general_prompt).
    2. A fact-check call grounded in this page's own archived coverage of the
       names in the claim (_grounding_headlines_for_claim) — concrete,
       page-specific evidence beats a second guess from bare model memory.
    3. An independent cross-check of that same fact-check on a DIFFERENT
       model provider (Groq, not 9Router/Gemini) — layer 2 alone still shares
       the generation call's blind spot when its own knowledge is stale and
       no grounding evidence turns up; a differently-trained model is less
       likely to be wrong about the exact same fact in the exact same way.
       Both checks must agree "valid" for a candidate to pass.

    Returns None if nothing passes within the attempt budget — the caller
    skips this cycle rather than risk posting a wrong claim."""
    from app.services.news_copywriter import generate_discussion_copy, factcheck_discussion_claim

    avoid_subjects = _recent_discussion_subjects(db, fanpage)
    headlines = _recent_headlines(db, fanpage)
    recent_questions = _recent_discussion_questions(db, fanpage)
    niche = (fanpage.mode2_gallery_niches or [None])[0] or fanpage.name

    for _attempt in range(_GENERAL_FACTCHECK_ATTEMPTS):
        candidate = generate_discussion_copy(
            fanpage, avoid_subjects=avoid_subjects, recent_headlines=headlines,
            recent_questions=recent_questions,
        )
        grounding = _grounding_headlines_for_claim(db, candidate.subject_name, candidate.question)

        valid, reason = factcheck_discussion_claim(
            candidate.question, candidate.subject_name, niche, grounding_headlines=grounding
        )
        if not valid:
            logger.warning(
                "Discussion: fanpage %d general-knowledge claim rejected by fact-check: %r (%s)",
                fanpage.id, candidate.question, reason,
            )
            avoid_subjects = [candidate.subject_name] + avoid_subjects
            continue

        valid2, reason2 = factcheck_discussion_claim(
            candidate.question, candidate.subject_name, niche,
            grounding_headlines=grounding, force_provider="groq",
        )
        if not valid2:
            logger.warning(
                "Discussion: fanpage %d general-knowledge claim rejected by cross-check (groq): %r (%s)",
                fanpage.id, candidate.question, reason2,
            )
            avoid_subjects = [candidate.subject_name] + avoid_subjects
            continue

        return candidate

    logger.info(
        "Discussion: fanpage %d general-knowledge topic failed fact-check %d time(s), skipping this cycle",
        fanpage.id, _GENERAL_FACTCHECK_ATTEMPTS,
    )
    return None


def _create_one(db, fanpage) -> bool:
    """Generate a single discussion card for the fanpage. Returns True if a job
    was created. Picks the topic source per discussion_topic_mode. On 'both'
    the AI's own general knowledge of the fanpage's niche (fact-checked, see
    _generate_general_topic) is tried FIRST — hot takes don't need to ride an
    article, and this is the source that actually produces the "does X
    deserve Y" / "was X right to do Y" style cards the user wants as the
    default, not a last resort. news/evergreen are kept as a fallback for
    'both' only if the general candidate fails fact-check twice, so the daily
    quota still gets filled. 'news' alone stays news-only by design; 'evergreen'
    alone still falls back to general when its seed pool is empty (unchanged)."""
    from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
    from app.models.target_fanpages import PublishMode
    from app.services.news_copywriter import generate_discussion_copy
    from app.services.design_images import resolve_template

    mode = (fanpage.discussion_topic_mode or "both").lower()

    article = None
    topic = None
    copy = None

    try:
        if mode == "both":
            copy = _generate_general_topic(db, fanpage)

        if copy is None:
            if mode in ("news", "both"):
                article = _pick_news_article(db, fanpage)
            if article is None and mode in ("evergreen", "both"):
                topic = _pick_evergreen_topic(db, fanpage)

            if article is not None:
                copy = generate_discussion_copy(fanpage, article=article)
            elif topic is not None:
                copy = generate_discussion_copy(
                    fanpage, seed_text=topic.seed_text, subject_hint=topic.subject_hint
                )
            elif mode == "evergreen":
                copy = _generate_general_topic(db, fanpage)
    except Exception as exc:
        logger.error("Discussion: copy generation failed for fanpage %d: %s", fanpage.id, exc)
        return False

    if copy is None:
        logger.info("Discussion: fanpage %d has no available topic (mode=%s)", fanpage.id, mode)
        return False

    # Prefer a discussion-tagged template; fall back to the fanpage's News
    # template if none is set/seeded (render_discussion applies the same
    # fallback, this just pins a sensible template_id on the job up front).
    template = resolve_template(db, "discussion", fanpage=fanpage) or resolve_template(db, "news", fanpage=fanpage)

    job = PublishJob(
        fanpage_id=fanpage.id,
        post_id=None,
        content_type=ContentType.discussion,
        source_article_id=article.id if article is not None else None,
        design_title=copy.question,
        design_subtitle=copy.label,          # badge text ("DISCUSSION"/"HOT TAKE")
        design_caption=copy.subject_name,    # subject for photo sourcing (not rendered)
        ai_generated_caption=copy.caption,
        ai_provider_used=copy.provider,
        design_template_id=template.id if template else None,
        status=PublishJobStatus.pending_design,
    )
    db.add(job)

    if topic is not None:
        topic.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
        topic.times_used = (topic.times_used or 0) + 1

    db.commit()

    if article is not None:
        source_desc = f"article:{article.id}"
    elif topic is not None:
        source_desc = f"topic:{topic.id}"
    else:
        source_desc = "general"
    logger.info(
        "Discussion: fanpage %d created job %d (%s) label=%s subject=%r source=%s",
        fanpage.id, job.id, mode, copy.label, copy.subject_name, source_desc,
    )

    # Auto mode → render now (staggered slightly); review mode waits for designer.
    if fanpage.discussion_publish_mode == PublishMode.auto:
        from app.tasks.design_renderer import render_discussion
        render_discussion.apply_async(args=[job.id], countdown=random.randint(5, 90))

    return True


@celery_app.task(name="app.tasks.discussion.generate_discussion_content")
def generate_discussion_content():
    """Beat tick: top up each Mode-4 fanpage toward its daily quota, paced across
    the active window. Creates at most one card per fanpage per tick so the
    output trickles rather than bursts."""
    db = SessionLocal()
    try:
        from sqlalchemy import func
        from app.models.target_fanpages import TargetFanpage
        from app.models.publish_jobs import PublishJob, ContentType

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        now_wib = now_utc.replace(tzinfo=timezone.utc).astimezone(WIB)
        now_hour = now_wib.hour + now_wib.minute / 60.0
        if now_hour < _WINDOW_START_HOUR or now_hour >= _WINDOW_END_HOUR:
            return  # outside the active window — nothing to do

        day_start_utc, day_end_utc = _wib_day_bounds_utc(now_utc)

        fanpages = (
            db.query(TargetFanpage)
            .filter(
                TargetFanpage.discussion_enabled == True,
                TargetFanpage.is_active == True,
                TargetFanpage.is_connected == True,
                TargetFanpage.discussion_daily_count > 0,
            )
            .all()
        )

        created = 0
        for fp in fanpages:
            quota = fp.discussion_daily_count or 0
            count_today = (
                db.query(func.count(PublishJob.id))
                .filter(
                    PublishJob.fanpage_id == fp.id,
                    PublishJob.content_type == ContentType.discussion,
                    PublishJob.is_deleted == False,
                    PublishJob.created_at >= day_start_utc,
                    PublishJob.created_at < day_end_utc,
                )
                .scalar()
            ) or 0

            if count_today >= quota:
                continue
            if count_today >= _target_by_now(quota, now_hour):
                continue  # ahead of pace — wait for a later slot

            if _create_one(db, fp):
                created += 1

        if created:
            logger.info("Discussion sweep: created %d card(s) across %d fanpage(s)", created, len(fanpages))
    finally:
        db.close()
