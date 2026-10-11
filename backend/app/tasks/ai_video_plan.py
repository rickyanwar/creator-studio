"""Planner for turning approved Mode 8 ideas into generation jobs."""
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models.ai_video import VideoIdea, VideoJob
from app.models.target_fanpages import TargetFanpage
from app.services.ai_video_fill import fill_idea
from app.services.video_templates import credits_estimate, get_template
from app.tasks.celery_app import celery_app
from app.tasks.publisher import _fanpage_tz


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
            idea = db.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id, VideoIdea.status == "approved").order_by(VideoIdea.score.desc().nullslast(), VideoIdea.created_at.asc()).first()
            if not idea:
                continue
            template = get_template(idea.template_key)
            clips = idea.clips if idea.clips in template.clip_options else fanpage.ai_video_clips if fanpage.ai_video_clips in template.clip_options else template.default_clips
            try:
                variables, title, caption, rendered, _provider = fill_idea(db, idea, fanpage, clips)
                job = VideoJob(fanpage_id=fanpage.id, idea_id=idea.id, template_key=template.key, title=title, caption=caption, model=template.model, aspect=template.aspect, clip_seconds=template.clip_seconds, clips=rendered, credits_per_clip=template.credits_per_clip, credits_estimate=credits_estimate(template, clips), status="queued")
                idea.variables, idea.status, idea.error = variables, "queued", None
                db.add(job)
                db.commit()
            except Exception as exc:
                idea.status, idea.error = "failed", str(exc)[:2000]
                db.commit()
    finally:
        db.close()
