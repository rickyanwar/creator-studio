"""Planner for turning approved Mode 8 ideas into generation jobs."""
import logging
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models.ai_video import VideoIdea, VideoJob
from app.models.settings import Settings
from app.models.target_fanpages import TargetFanpage
from app.services.ai_video_cover_build import apply_prepared_cover, prepare_cover
from app.services.ai_video_fill import fill_from_research, fill_idea, research_ready
from app.services.ai_video_idea_writer import MAX_IDEAS_PER_RUN, write_ideas
from app.services.ai_video_prepare import polish_enabled, valid_prepared
from app.services.social_project_eligibility import project_can_generate
from app.services.topic_similarity import similar_subjects
from app.services.video_templates import allowed_video_templates, credits_estimate, fit_clips_to_credits, get_template, pick_clips
from app.tasks.celery_app import celery_app
from app.tasks.publisher import _fanpage_tz

logger = logging.getLogger(__name__)

# Failed videos (Flow error, QC reject) are replaced the same day, at most this many per fan page per day,
# so a Flow outage cannot loop through ideas and credits (owner 2026-10-22).
MAX_FAILED_REPLACEMENTS_PER_DAY = 2
AI_VIDEO_STOCK_DAYS = 20
# While Muse researches, the in-app writer still fills a fan page whose idea bank is empty and that got no Muse idea for this many hours.
MUSE_DRY_HOURS = 6


def video_stock(db, fanpage_id: int) -> int:
    """Number of unposted ai_video PublishJob rows waiting in stock for a fan page."""
    from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus

    try:
        return (
            db.query(PublishJob)
            .filter(
                PublishJob.fanpage_id == fanpage_id,
                PublishJob.content_type == ContentType.ai_video,
                PublishJob.is_deleted.is_(False),
                PublishJob.status.in_([
                    PublishJobStatus.pending_publish,
                    PublishJobStatus.pending_review,
                ]),
            )
            .count()
        )
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return 0


def _daily_counts(jobs) -> tuple[int, int]:
    """(jobs that count toward ai_video_daily_count, credits spent) for today's non-cancelled jobs."""
    failed = [j for j in jobs if j.status == "failed"]
    others = [j for j in jobs if j.status != "failed"]
    count = len(others) + max(0, len(failed) - MAX_FAILED_REPLACEMENTS_PER_DAY)
    credits_spent = sum(j.credits_estimate or 0 for j in others) + sum(
        (j.credits_per_clip or 0) * len(j.clips_done or []) for j in failed
    )
    return count, credits_spent


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _create_and_commit_job(db, fanpage, idea, template, clips, variables, title, caption, rendered, cover):
    job = VideoJob(
        fanpage_id=fanpage.id,
        idea_id=idea.id,
        template_key=template.key,
        title=title,
        caption=caption,
        model=template.model,
        aspect=template.aspect,
        clip_seconds=template.clip_seconds,
        clips=rendered,
        credits_per_clip=template.credits_per_clip,
        credits_estimate=credits_estimate(template, clips),
        status="queued",
    )
    idea.variables, idea.status, idea.error = variables, "queued", None
    db.add(job)
    if isinstance(cover, dict):
        apply_prepared_cover(job, cover)
    else:
        prepare_cover(db, fanpage, job)
    db.commit()


@celery_app.task(name="app.tasks.ai_video_plan.plan_ai_videos")
def plan_ai_videos():
    db = SessionLocal()
    try:
        for fanpage in db.query(TargetFanpage).filter(TargetFanpage.is_active == True, TargetFanpage.ai_video_enabled == True, project_can_generate()).all():  # noqa: E712
            now = datetime.now(timezone.utc)
            local_today = now.astimezone(_fanpage_tz(fanpage)).date()
            local_midnight = datetime.combine(local_today, datetime.min.time(), tzinfo=_fanpage_tz(fanpage)).astimezone(timezone.utc).replace(tzinfo=None)
            today_jobs = db.query(VideoJob).filter(VideoJob.fanpage_id == fanpage.id, VideoJob.created_at >= local_midnight, VideoJob.status != "cancelled").all()
            made_today, spent_today = _daily_counts(today_jobs)
            open_count = db.query(VideoJob).filter(VideoJob.fanpage_id == fanpage.id, VideoJob.status.in_(["queued", "claimed", "generating"])).count()
            if open_count >= 2:
                continue
            stock = video_stock(db, fanpage.id)
            if made_today >= fanpage.ai_video_daily_count and stock >= fanpage.ai_video_daily_count * AI_VIDEO_STOCK_DAYS:
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

            other_jobs = (
                db.query(VideoJob.fanpage_id, VideoJob.title, VideoIdea.topic)
                .outerjoin(VideoIdea, VideoJob.idea_id == VideoIdea.id)
                .filter(
                    VideoJob.fanpage_id != fanpage.id,
                    VideoJob.status != "cancelled",
                    VideoJob.created_at >= _now() - timedelta(days=30),
                )
                .all()
            )
            other_page_topics = [
                (job_fp_id, (idea_topic or job_title or "").strip())
                for job_fp_id, job_title, idea_topic in other_jobs
                if (idea_topic or job_title)
            ]

            valid_ideas = []
            for app_idea in approved_ideas:
                duplicate_fp = None
                if app_idea.topic:
                    for other_fp_id, other_topic in other_page_topics:
                        if other_topic and similar_subjects(other_topic, app_idea.topic):
                            duplicate_fp = other_fp_id
                            break
                if duplicate_fp is not None:
                    app_idea.status = "rejected"
                    app_idea.error = f"duplicate subject: already used on channel {duplicate_fp}"
                    db.commit()
                    continue
                if allowed and app_idea.template_key not in allowed:
                    logger.warning(
                        "Skipping idea %s for fanpage %s: template %s is no longer allowed",
                        app_idea.id, fanpage.id, app_idea.template_key
                    )
                    continue
                valid_ideas.append(app_idea)

            if not valid_ideas:
                continue

            if polish_enabled(fanpage):
                valid_ideas = [i for i in valid_ideas if valid_prepared(i) is not None]
                if not valid_ideas:
                    logger.info("Mode 8: fanpage %s waits for Argon prompt polish", fanpage.id)
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
                left_today = per_day - spent_today
                budget = left_today if budget is None else min(budget, left_today)
            fitted = fit_clips_to_credits(template, clips, budget)
            if fitted is None:
                logger.info("Mode 8: fanpage %s reached its daily credit cap; idea %s waits", fanpage.id, idea.id)
                continue
            clips = fitted
            if polish_enabled(fanpage):
                prepared = valid_prepared(idea)
                if not prepared:
                    continue
                if prepared.get("clips_count") != clips:
                    logger.info("prepared clip count %s does not fit today (%s); waiting", prepared.get("clips_count"), clips)
                    continue
                variables = prepared["variables"]
                title = prepared["title"]
                caption = prepared["caption"]
                rendered = prepared["clips"]
                cover = prepared.get("cover")
                try:
                    _create_and_commit_job(db, fanpage, idea, template, clips, variables, title, caption, rendered, cover)
                except ValueError as exc:
                    db.rollback()
                    idea.status, idea.error = "failed", str(exc)[:2000]
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    logger.warning("AI video planning failed for idea %s: %s", idea.id, exc)
                    continue
            else:
                try:
                    # Research-desk ideas carry their own slots, title and caption; the rest are written by the LLM.
                    filler = fill_from_research if research_ready(idea, template, clips) else fill_idea
                    variables, title, caption, rendered, _provider = filler(db, idea, fanpage, clips)
                    cover = None
                    _create_and_commit_job(db, fanpage, idea, template, clips, variables, title, caption, rendered, cover)
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
    """Keep each Mode 8 fanpage's idea bank (videos per day x bank days) filled, emptiest first; off while Muse researches, except for fan pages whose bank is empty and got no Muse idea for MUSE_DRY_HOURS."""
    db = SessionLocal()
    try:
        row = db.query(Settings).first()
        if row is None:
            return
        muse_mode = row is not None and (row.ai_video_researcher or "muse") == "muse"
        bank_days = max(1, row.ai_video_idea_bank_days or 1)
        plans = []
        for fanpage in db.query(TargetFanpage).filter(TargetFanpage.is_active == True, TargetFanpage.ai_video_enabled == True, project_can_generate()).all():  # noqa: E712
            daily = max(1, fanpage.ai_video_daily_count or 1)
            waiting = db.query(VideoIdea).filter(
                VideoIdea.fanpage_id == fanpage.id,
                VideoIdea.status.in_(["pilot", "proposed", "approved"]),
            ).count()
            if muse_mode:
                if waiting != 0:
                    continue
                has_recent_muse = db.query(VideoIdea).filter(
                    VideoIdea.fanpage_id == fanpage.id,
                    VideoIdea.source == "muse",
                    VideoIdea.created_at >= _now() - timedelta(hours=MUSE_DRY_HOURS),
                ).first() is not None
                if has_recent_muse:
                    continue
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
            logger.info("Mode 8 idea writer: fanpage %s wrote %s idea(s)%s", fanpage.id, created, " (Muse dry)" if muse_mode else "")
    finally:
        db.close()

