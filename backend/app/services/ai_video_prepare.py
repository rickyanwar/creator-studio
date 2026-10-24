"""Prepare Mode 8 video ideas early with Argon prompt polish."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging

from sqlalchemy import and_, or_

from app.models.ai_video import VideoIdea, VideoJob
from app.services.ai_video_fill import (
    fanpage_niche,
    fill_from_research,
    fill_idea,
    research_ready,
)
from app.services.prompt_polish import PolishConfig, PolishInterrupted, polish_clips
from app.services.video_templates import (
    allowed_video_templates,
    fit_clips_to_credits,
    get_template,
    pick_clips,
)
from app.tasks.publisher import _fanpage_tz

logger = logging.getLogger(__name__)

PREPARED_VERSION = 1
DRAFT_MAX_AGE_H = 6
MAX_PREPARE_ROUNDS = 3  # rounds of polish_clips before the idea fails
MAX_IDEAS_PER_RUN = 3
PREPARED_AHEAD_MIN = 2
RETRY_AFTER_FAILED_MIN = 60


def polish_enabled(fanpage) -> bool:
    """Check if prompt polish is enabled for this fanpage."""
    return bool(getattr(fanpage, "ai_video_prompt_polish_enabled", False))


def idea_fingerprint(idea) -> str:
    """Compute 32-char sha256 hex fingerprint of an idea's core content fields."""
    payload = {
        "template_key": getattr(idea, "template_key", None),
        "topic": getattr(idea, "topic", None),
        "angle": getattr(idea, "angle", None),
        "variables": getattr(idea, "variables", None),
        "title": getattr(idea, "title", None),
        "caption": getattr(idea, "caption", None),
        "clips": getattr(idea, "clips", None),
        "source": getattr(idea, "source", None),
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def planned_clip_count(idea, fanpage, template) -> int | None:
    """Compute planned clip count using the plan rule without daily budget."""
    source = getattr(idea, "source", None)
    idea_clips = getattr(idea, "clips", None)
    if source == "owner" and idea_clips in template.clip_options:
        clips = idea_clips
    else:
        clips = pick_clips(idea_clips, fanpage, template)
    per_video = getattr(fanpage, "ai_video_max_credits_per_video", None)
    if per_video is not None:
        clips = fit_clips_to_credits(template, clips, per_video)
    return clips


def valid_prepared(idea) -> dict | None:
    """Return prepared payload dict if valid and matching fingerprint, else None."""
    prep = getattr(idea, "prepared", None)
    if not isinstance(prep, dict):
        return None
    if prep.get("version") != PREPARED_VERSION:
        return None
    if prep.get("fingerprint") != idea_fingerprint(idea):
        return None
    if prep.get("template_key") != getattr(idea, "template_key", None):
        return None
    if not (prep.get("polished") is True or prep.get("render_fallback") is True):
        return None
    clips = prep.get("clips")
    if not isinstance(clips, list) or len(clips) == 0:
        return None
    return prep


def _resume_draft(idea, clips: int) -> dict | None:
    """Return idea.prepared if it is a valid, unexpired polish draft, else None."""
    prep = getattr(idea, "prepared", None)
    if not isinstance(prep, dict):
        return None
    if prep.get("draft") is not True:
        return None
    if prep.get("version") != PREPARED_VERSION:
        return None
    if prep.get("fingerprint") != idea_fingerprint(idea):
        return None
    if prep.get("template_key") != getattr(idea, "template_key", None):
        return None
    if prep.get("clips_count") != clips:
        return None
    rendered = prep.get("rendered")
    if not isinstance(rendered, list) or len(rendered) == 0:
        return None
    saved_at_raw = prep.get("saved_at")
    if not saved_at_raw:
        return None
    try:
        saved_at = datetime.fromisoformat(str(saved_at_raw))
    except (ValueError, TypeError):
        return None
    if saved_at.tzinfo is not None:
        saved_at = saved_at.astimezone(timezone.utc).replace(tzinfo=None)
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    age = (now_utc - saved_at).total_seconds()
    if age < 0 or age > DRAFT_MAX_AGE_H * 3600:
        return None
    return prep


def _is_matching_prepared(idea, fanpage) -> bool:
    prep = valid_prepared(idea)
    if prep is None:
        return False
    template = get_template(idea.template_key)
    planned = planned_clip_count(idea, fanpage, template)
    return prep.get("clips_count") == planned


def candidate_ideas(db, fanpage) -> list[VideoIdea]:
    """Return ideas for this fanpage eligible for prompt polish."""
    prep_ideas = (
        db.query(VideoIdea)
        .filter(
            VideoIdea.fanpage_id == fanpage.id,
            VideoIdea.status.in_(["approved", "proposed"]),
            VideoIdea.prepared.isnot(None),
        )
        .all()
    )
    valid_prep_count = sum(1 for i in prep_ideas if _is_matching_prepared(i, fanpage))
    ahead_target = max(PREPARED_AHEAD_MIN, fanpage.ai_video_daily_count or 1)
    if valid_prep_count >= ahead_target:
        return []

    query = db.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id)
    min_score = getattr(fanpage, "ai_video_min_score", None)
    if min_score is not None:
        status_cond = or_(
            VideoIdea.status == "approved",
            and_(
                VideoIdea.status == "proposed",
                VideoIdea.score.isnot(None),
                VideoIdea.score >= min_score,
            ),
        )
    else:
        status_cond = (VideoIdea.status == "approved")

    ideas = (
        query.filter(status_cond)
        .order_by(VideoIdea.score.desc().nullslast(), VideoIdea.created_at.asc())
        .all()
    )

    allowed = allowed_video_templates(fanpage)
    candidates = []
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    for idea in ideas:
        if allowed and idea.template_key not in allowed:
            continue
        if _is_matching_prepared(idea, fanpage):
            continue
        if idea.polish_error and idea.prepared_at:
            prep_at = idea.prepared_at.replace(tzinfo=None) if idea.prepared_at.tzinfo else idea.prepared_at
            if (now_utc - prep_at).total_seconds() < RETRY_AFTER_FAILED_MIN * 60:
                continue
        candidates.append(idea)
    return candidates


def prepare_idea(db, idea, fanpage, config: PolishConfig) -> str:
    """Fill and polish one idea. Returns 'prepared' | 'failed' | 'skipped'."""
    template = get_template(idea.template_key)
    clips = planned_clip_count(idea, fanpage, template)
    if clips is None:
        return "skipped"

    draft = _resume_draft(idea, clips)
    if draft is not None:
        variables = draft["variables"]
        title = draft["title"]
        caption = draft["caption"]
        provider = draft["provider"]
        rendered = draft["rendered"]
        resume = {
            "answer": draft["answer"],
            "problems": draft["problems"],
            "attempts_used": draft["attempts_used"],
        }
    else:
        resume = None
        filler = fill_from_research if research_ready(idea, template, clips) else fill_idea
        try:
            variables, title, caption, rendered, provider = filler(db, idea, fanpage, clips)
        except ValueError as exc:
            idea.status = "failed"
            idea.error = str(exc)[:2000]
            db.commit()
            return "failed"
        except Exception as exc:
            logger.warning("Filler failed for idea %s: %s", getattr(idea, "id", None), exc)
            db.rollback()
            return "skipped"

    logger.info("prepare: idea %s fanpage %s %s (resume=%s)", getattr(idea, "id", None), getattr(fanpage, "id", None), idea.template_key, bool(resume))

    # The polish call can take many minutes. End the read transaction first: an
    # idle-in-transaction connection blocks every migration ALTER (incident 2026-10-24).
    niche = fanpage_niche(fanpage)
    db.commit()

    try:
        if resume is not None:
            result = polish_clips(
                rendered,
                template=template,
                niche=niche,
                config=config,
                resume=resume,
            )
        else:
            result = polish_clips(
                rendered,
                template=template,
                niche=niche,
                config=config,
            )
    except PolishInterrupted as exc:
        now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        idea.prepared = {
            "draft": True,
            "version": PREPARED_VERSION,
            "fingerprint": idea_fingerprint(idea),
            "template_key": idea.template_key,
            "clips_count": clips,
            "variables": variables,
            "title": title,
            "caption": caption,
            "provider": provider,
            "rendered": rendered,
            "answer": exc.answer,
            "problems": exc.problems,
            "attempts_used": exc.attempts_used,
            "account": exc.account,
            "seconds": exc.seconds,
            "saved_at": now_iso,
        }
        db.commit()
        logger.info(
            "prepare: idea %s polish interrupted after %d attempt(s), draft saved",
            idea.id,
            exc.attempts_used,
        )
        raise

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    idea.polish_attempts = (idea.polish_attempts or 0) + 1
    idea.prepared_at = now_utc

    if result.ok:
        orig_by_idx = {c["index"]: c for c in rendered}
        polished_clips = []
        for pol_clip in result.clips:
            idx = pol_clip["index"]
            orig_prompt = orig_by_idx.get(idx, {}).get("prompt", "")
            polished_clips.append({**pol_clip, "prompt_original": orig_prompt})

        prep = {
            "version": PREPARED_VERSION,
            "fingerprint": idea_fingerprint(idea),
            "template_key": idea.template_key,
            "clips_count": clips,
            "variables": variables,
            "title": title,
            "caption": caption,
            "provider": provider,
            "polished": True,
            "clips": polished_clips,
            "voice_sentence": result.voice_sentence,
            "ambience_sentence": result.ambience_sentence,
            "changes": result.changes,
            "account": result.account,
            "seconds": result.seconds,
            "attempts": result.attempts,
        }
        idea.prepared = prep
        idea.polish_error = None
        db.commit()
        logger.info(
            "prepare: idea %s -> prepared attempts=%d problems=%s",
            idea.id,
            result.attempts,
            (" | ".join(result.problems))[:600],
        )
        return "prepared"
    elif template.raw.get("polish_fallback") == "render":
        fallback_clips = [
            {
                **clip,
                "prompt_original": clip.get("prompt", ""),
                "voice_sentence": None,
                "ambience_sentence": None,
                "changes": [],
                "polish_problems": result.problems[:5],
            }
            for clip in rendered
        ]
        prep = {
            "version": PREPARED_VERSION,
            "fingerprint": idea_fingerprint(idea),
            "template_key": idea.template_key,
            "clips_count": clips,
            "variables": variables,
            "title": title,
            "caption": caption,
            "provider": provider,
            "account": result.account,
            "seconds": result.seconds,
            "attempts": result.attempts,
            "polished": False,
            "render_fallback": True,
            "clips": fallback_clips,
        }
        idea.prepared = prep
        idea.polish_error = ("; ".join(result.problems))[:2000]
        db.commit()
        logger.info(
            "prepare: idea %s -> render fallback attempts=%d problems=%s",
            idea.id,
            result.attempts,
            (" | ".join(result.problems))[:600],
        )
        return "prepared"
    else:
        idea.prepared = None
        idea.polish_error = ("; ".join(result.problems))[:2000]
        if idea.polish_attempts >= MAX_PREPARE_ROUNDS:
            idea.status = "failed"
            idea.error = f"Argon prompt polish failed after {idea.polish_attempts} rounds: {idea.polish_error}"[:2000]
            db.commit()
            outcome = "failed"
        else:
            db.commit()
            outcome = "skipped"
        logger.info(
            "prepare: idea %s -> %s attempts=%d problems=%s",
            idea.id,
            outcome,
            result.attempts,
            (" | ".join(result.problems))[:600],
        )
        return outcome


def _fanpage_need_key(db, fanpage) -> tuple[int, int]:
    from app.tasks.ai_video_plan import _daily_counts

    now = datetime.now(timezone.utc)
    local_today = now.astimezone(_fanpage_tz(fanpage)).date()
    local_midnight = (
        datetime.combine(local_today, datetime.min.time(), tzinfo=_fanpage_tz(fanpage))
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    today_jobs = (
        db.query(VideoJob)
        .filter(
            VideoJob.fanpage_id == fanpage.id,
            VideoJob.created_at >= local_midnight,
            VideoJob.status != "cancelled",
        )
        .all()
    )
    made_today, _ = _daily_counts(today_jobs)
    can_make_today = made_today < (getattr(fanpage, "ai_video_daily_count", 0) or 0)

    prep_ideas = (
        db.query(VideoIdea)
        .filter(
            VideoIdea.fanpage_id == fanpage.id,
            VideoIdea.status.in_(["approved", "proposed"]),
            VideoIdea.prepared.isnot(None),
        )
        .all()
    )
    has_valid_prep = any(valid_prepared(i) is not None for i in prep_ideas)

    if can_make_today and not has_valid_prep:
        rank = 0
    elif can_make_today:
        rank = 1
    else:
        rank = 2

    return (rank, fanpage.id)


def pick_run_ideas(db, fanpages) -> list[tuple]:
    """Pick up to MAX_IDEAS_PER_RUN ideas across fanpages round-robin."""
    sorted_fps = sorted(fanpages, key=lambda fp: _fanpage_need_key(db, fp))
    fp_candidates = {fp.id: candidate_ideas(db, fp) for fp in sorted_fps}
    picked = []
    while len(picked) < MAX_IDEAS_PER_RUN:
        added_in_round = False
        for fp in sorted_fps:
            cands = fp_candidates[fp.id]
            if cands:
                idea = cands.pop(0)
                picked.append((fp, idea))
                added_in_round = True
                if len(picked) >= MAX_IDEAS_PER_RUN:
                    break
        if not added_in_round:
            break
    return picked
