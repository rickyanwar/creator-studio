"""Planner for turning approved Mode 8 ideas into generation jobs."""
import logging
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models.ai_video import VideoIdea, VideoJob
from app.models.settings import Settings
from app.models.target_fanpages import TargetFanpage
from app.services.ai_video_fill import fill_from_research, fill_idea, research_ready
from app.services.ai_video_idea_writer import MAX_IDEAS_PER_RUN, write_ideas
from app.services.video_templates import allowed_video_templates, credits_estimate, fit_clips_to_credits, get_template, pick_clips
from app.tasks.celery_app import celery_app
from app.tasks.publisher import _fanpage_tz

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@celery_app.task(name="app.tasks.ai_video_plan.plan_ai_videos")
def plan_ai_videos():
    db = SessionLocal()
    try:
        for fanpage in db.query(TargetFanpage).filter(TargetFanpage.is_active == True, TargetFanpage.ai_video_enabled == True).all():  # noqa: E712
            now = datetime.now(timezone.utc)
            local_today = now.astimezone(_fanpage_tz(fanpage)).date()
            local_midnight = datetime.combine(local_today, datetime.min.time(), tzinfo=_fanpage_tz(fanpage)).astimezone(timezone.utc).replace(tzinfo=None)
            made_today = db.query(VideoJob).filter(VideoJob.fanpage_id == fanpage.id, VideoJob.created_at >= local_midnight, VideoJob.status != "cancelled").count()
            open_count = db.query(VideoJob).filter(VideoJob.fanpage_id == fanpage.id, VideoJob.status.in_(["queued", "claimed", "generating"])).count()
            if made_today >= fanpage.ai_video_daily_count or open_count >= 2:
                continue
            for idea in db.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id, VideoIdea.status == "proposed").all():
                if fanpage.ai_video_min_score is not None and idea.score is not None and idea.score >= fanpage.ai_video_min_score:
                    idea.status, idea.decided_by, idea.decided_at = "approved", "auto", _now()
            db.commit()

            allowed = allowed_video_templates(fanpage)
            approved_ideas = db.query(VideoIdea).filter(
                VideoIdea.fanpage_id == fanpage.id,
                VideoIdea.status == "approved"
            ).order_by(VideoIdea.score.desc().nullslast(), VideoIdea.created_at.asc()).all()

            if not approved_ideas:
                continue

            valid_ideas = []
            for app_idea in approved_ideas:
                if allowed and app_idea.template_key not in allowed:
                    logger.warning(
                        "Skipping idea %s for fanpage %s: template %s is no longer allowed",
                        app_idea.id, fanpage.id, app_idea.template_key
                    )
                    continue
                valid_ideas.append(app_idea)

            if not valid_ideas:
                continue

            # Rotation across templates: choose approved idea whose template was used least recently
            past_jobs = db.query(VideoJob.template_key, VideoJob.created_at).filter(
                VideoJob.fanpage_id == fanpage.id
            ).order_by(VideoJob.created_at.desc()).all()

            last_used_map = {}
            for t_key, c_at in past_jobs:
                if t_key not in last_used_map:
                    last_used_map[t_key] = c_at

            # Least recently used template first; within it the best score, then the oldest idea
            min_dt = datetime.min
            idea = min(
                valid_ideas,
                key=lambda i: (last_used_map.get(i.template_key, min_dt), -(i.score or 0), i.created_at)
            )

            template = get_template(idea.template_key)
            # The owner's own per-idea length wins; AI/Hermes ideas stay inside the fanpage range.
            clips = idea.clips if idea.source == "owner" and idea.clips in template.clip_options else pick_clips(idea.clips, fanpage, template)
            per_video = fanpage.ai_video_max_credits_per_video
            per_day = fanpage.ai_video_max_credits_per_day
            if per_video is not None and fit_clips_to_credits(template, clips, per_video) is None:
                idea.status = "failed"
                idea.error = (f"needs at least {credits_estimate(template, min(template.clip_options))} credits; "
                              f"this fanpage allows {per_video} per video")
                db.commit()
                continue
            budget = per_video
            if per_day is not None:
                spent_today = sum(
                    j.credits_estimate or 0 for j in db.query(VideoJob).filter(
                        VideoJob.fanpage_id == fanpage.id, VideoJob.created_at >= local_midnight,
                        VideoJob.status != "cancelled",
                    )
                )
                left_today = per_day - spent_today
                budget = left_today if budget is None else min(budget, left_today)
            fitted = fit_clips_to_credits(template, clips, budget)
            if fitted is None:
                logger.info("Mode 8: fanpage %s reached its daily credit cap; idea %s waits", fanpage.id, idea.id)
                continue
            clips = fitted
            try:
                # Research-desk ideas carry their own slots, title and caption; the rest are written by the LLM.
                filler = fill_from_research if research_ready(idea, template, clips) else fill_idea
                variables, title, caption, rendered, _provider = filler(db, idea, fanpage, clips)
                job = VideoJob(fanpage_id=fanpage.id, idea_id=idea.id, template_key=template.key, title=title, caption=caption, model=template.model, aspect=template.aspect, clip_seconds=template.clip_seconds, clips=rendered, credits_per_clip=template.credits_per_clip, credits_estimate=credits_estimate(template, clips), status="queued")
                idea.variables, idea.status, idea.error = variables, "queued", None
                db.add(job)
                db.commit()
            except ValueError as exc:
                db.rollback()
                idea.status, idea.error = "failed", str(exc)[:2000]
                db.commit()
            except Exception as exc:
                db.rollback()
                idea.status = "approved"
                idea.error = f"LLM unavailable, will retry: {type(exc).__name__}: {str(exc)[:200]}"
                db.commit()
                logger.warning("AI video planning paused: LLM unavailable for idea %s: %s", idea.id, exc)
                return
    finally:
        db.close()


@celery_app.task(name="app.tasks.ai_video_plan.top_up_ai_video_ideas")
def top_up_ai_video_ideas():
    """Keep each Mode 8 fanpage's idea bank (videos per day x bank days) filled, emptiest first; off while Muse researches."""
    db = SessionLocal()
    try:
        row = db.query(Settings).first()
        if row is None or (row.ai_video_researcher or "muse") == "muse":
            return  # Muse researches Mode 8 ideas; the in-app writer stays off.
        bank_days = max(1, row.ai_video_idea_bank_days or 1)
        plans = []
        for fanpage in db.query(TargetFanpage).filter(TargetFanpage.is_active == True, TargetFanpage.ai_video_enabled == True).all():  # noqa: E712
            daily = max(1, fanpage.ai_video_daily_count or 1)
            waiting = db.query(VideoIdea).filter(
                VideoIdea.fanpage_id == fanpage.id,
                VideoIdea.status.in_(["pilot", "proposed", "approved"]),
            ).count()
            target = max(2, daily * bank_days)
            if waiting >= target:
                continue
            written_24h = db.query(VideoIdea).filter(
                VideoIdea.fanpage_id == fanpage.id,
                VideoIdea.source == "ai",
                VideoIdea.created_at >= _now() - timedelta(hours=24),
            ).count()
            if written_24h >= 2 * daily:
                continue
            need = min(target - waiting, 2 * daily - written_24h, MAX_IDEAS_PER_RUN)
            plans.append((waiting / target, fanpage.id, fanpage, need))
        # Emptiest bank first, so no fanpage waits behind another one.
        for _coverage, _fid, fanpage, need in sorted(plans, key=lambda p: (p[0], p[1])):
            try:
                created = write_ideas(db, fanpage, need)
            except Exception as exc:
                db.rollback()
                logger.warning("Mode 8 idea writer failed for fanpage %s: %s", fanpage.id, exc)
                continue
            logger.info("Mode 8 idea writer: fanpage %s wrote %s idea(s)", fanpage.id, created)
    finally:
        db.close()

