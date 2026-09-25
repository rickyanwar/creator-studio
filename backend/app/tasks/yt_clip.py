"""Mode 7: YouTube clips — discovery, AI highlight analysis, and pacing.

    [1] DISCOVER ─► [2] ANALYSE ─► [3] CLIP IDEAS ─► [4] RENDER ─► [5] PUBLISH
     (RSS/link)     (subs + AI)     (review/edit)     (9:16 MP4)    (Repliz video)

[1] `yt_clip_cycle` (every 30 min, any hour): each active source's RSS feed
    (or its single video) → new YtVideo rows (Shorts and, for channels,
    videos older than yt_clip_max_video_age_days skipped). Plain GETs, no
    YouTube player requests.
[2] Same tick: a fanpage whose clip-idea queue is thin (< 2× its daily
    count) gets ONE discovered video analysed on the `video` queue —
    subtitles + a 9Router highlight search (analyze_video). Analysing only
    on demand keeps YouTube requests and AI spend proportional to what
    actually gets posted.
[3] Highlights become YtClipIdea rows the admin can review/edit/delete.
[4]/[5] `generate_yt_clip_content` (every 30 min, 08:00-22:00 WIB) consumes
    ideas into PublishJobs paced like Modes 5/6 (newest source video first,
    then highest score); app.tasks.yt_clip_render renders and publishes.
"""

import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings
from app.database import SessionLocal
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))
_WINDOW_START_HOUR = 8
_WINDOW_END_HOUR = 22
_MAX_CATCHUP_PER_TICK = 3
_MIN_VIDEO_S = 180
_MAX_VIDEO_S = 3 * 3600
_MAX_ANALYSIS_ATTEMPTS = 3
_STALE_ANALYZING = timedelta(minutes=45)
_RENDER_DISPATCH_PER_TICK = 3


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _enabled_fanpages(db):
    from app.models.target_fanpages import TargetFanpage

    return (
        db.query(TargetFanpage)
        .filter(
            TargetFanpage.yt_clip_enabled == True,  # noqa: E712
            TargetFanpage.is_active == True,  # noqa: E712
            TargetFanpage.is_connected == True,  # noqa: E712
        )
        .all()
    )


# ── [1] discovery ────────────────────────────────────────────────────────────

def _insert_video(db, fanpage_id: int, source_id: int | None, video_id: str,
                  title: str | None = None, published_at: datetime | None = None) -> bool:
    """INSERT … ON CONFLICT DO NOTHING — True if the video is new to this fanpage."""
    from sqlalchemy.dialects.postgresql import insert
    from app.models.yt_videos import YtVideo

    stmt = insert(YtVideo).values(
        fanpage_id=fanpage_id, source_id=source_id, video_id=video_id,
        title=title, published_at=published_at, status="discovered",
    ).on_conflict_do_nothing(index_elements=["fanpage_id", "video_id"])
    return db.execute(stmt).rowcount > 0


def discover_source(db, fanpage, source) -> int:
    """New videos from one source → YtVideo rows. Returns how many were new."""
    from app.services.youtube_source import fetch_feed

    if source.kind == "video":
        added = int(_insert_video(db, fanpage.id, source.id, source.video_id))
    else:
        entries = fetch_feed(channel_id=source.channel_id, playlist_id=source.playlist_id)
        max_age = timedelta(days=fanpage.yt_clip_max_video_age_days or 7)
        added = 0
        for e in entries:
            if e.is_short:
                continue
            if source.kind == "channel" and e.published_at and _now() - e.published_at > max_age:
                continue
            added += int(_insert_video(db, fanpage.id, source.id, e.video_id, e.title, e.published_at))
    source.last_checked_at = _now()
    source.last_error = None
    source.videos_found = (source.videos_found or 0) + added
    db.commit()
    return added


def _discover_all(db, fanpage) -> int:
    from app.models.yt_clip_sources import YtClipSource

    added = 0
    for source in db.query(YtClipSource).filter_by(fanpage_id=fanpage.id, is_active=True).all():
        try:
            added += discover_source(db, fanpage, source)
        except Exception as exc:
            db.rollback()
            source.last_checked_at = _now()
            source.last_error = str(exc)[:500]
            db.commit()
            logger.warning("yt_clip: discovery failed for source %d (%s): %s", source.id, source.url, exc)
    return added


# ── [2] analysis ─────────────────────────────────────────────────────────────

def _pending_idea_count(db, fanpage_id: int) -> int:
    from sqlalchemy import func
    from app.models.yt_clip_ideas import YtClipIdea

    return db.query(func.count(YtClipIdea.id)).filter(
        YtClipIdea.fanpage_id == fanpage_id, YtClipIdea.status == "pending",
    ).scalar() or 0


def _next_video_to_analyse(db, fanpage_id: int):
    from app.models.yt_videos import YtVideo

    return (
        db.query(YtVideo)
        .filter(YtVideo.fanpage_id == fanpage_id, YtVideo.status == "discovered")
        .order_by(YtVideo.published_at.desc().nullslast(), YtVideo.created_at.desc())
        .first()
    )


def _recover_stale_analyses(db) -> None:
    """A worker killed mid-analysis (deploy, OOM) leaves no code to release
    the claim — send such videos back to the discovered pool."""
    from app.models.yt_videos import YtVideo

    n = (
        db.query(YtVideo)
        .filter(YtVideo.status == "analyzing", YtVideo.updated_at < _now() - _STALE_ANALYZING)
        .update({"status": "discovered"}, synchronize_session=False)
    )
    if n:
        db.commit()
        logger.warning("yt_clip: released %d video(s) stuck in 'analyzing'", n)


def _skip(db, video, reason: str) -> None:
    video.status = "skipped"
    video.skip_reason = reason
    db.commit()
    logger.info("yt_clip: video %s skipped for fanpage %d — %s", video.video_id, video.fanpage_id, reason)


def _retry_or_fail(db, video, error: str) -> None:
    video.attempt_count = (video.attempt_count or 0) + 1
    video.last_error = error[:2000]
    video.status = "failed" if video.attempt_count >= _MAX_ANALYSIS_ATTEMPTS else "discovered"
    db.commit()
    logger.warning("yt_clip: analysis of %s failed (attempt %d): %s", video.video_id, video.attempt_count, error[:300])


def yt_private_dir(name: str) -> Path:
    """work/ or subs/ under the private (non-web-served) Mode 7 root."""
    return Path(get_settings().yt_private_path) / name


def _store_subtitles(video_id: str, track: str, data: bytes) -> Path:
    """Cache the subtitle track per YouTube id — shared across fanpages."""
    subs_dir = yt_private_dir("subs")
    subs_dir.mkdir(parents=True, exist_ok=True)
    path = subs_dir / f"{video_id}.{track}.json3"
    path.write_bytes(data)
    return path


def _skip_reason(meta, video, fanpage) -> str | None:
    if meta.live_status in ("is_live", "is_upcoming", "post_live"):
        return f"not a finished upload ({meta.live_status})"
    if not meta.duration_s or meta.duration_s < _MIN_VIDEO_S:
        return f"too short ({meta.duration_s or 0}s)"
    if meta.duration_s > _MAX_VIDEO_S:
        return f"too long ({meta.duration_s // 60} min)"
    if not meta.sub_track:
        return "no subtitles (the AI picks highlights from the transcript)"
    source_kind = video.source.kind if getattr(video, "source", None) else None
    max_age = timedelta(days=fanpage.yt_clip_max_video_age_days or 7)
    if source_kind == "channel" and meta.published_at and _now() - meta.published_at > max_age:
        return "older than the fanpage's max video age"
    return None


def _highlight_prompt(fanpage, video, meta, srt: str, direction: str | None):
    from app.services.yt_highlights import ClipRules, build_prompt, language_name

    rules = ClipRules(
        count=max(1, fanpage.yt_clip_per_video or 3),
        min_s=fanpage.yt_clip_min_s or 60,
        max_s=max((fanpage.yt_clip_max_s or 120), (fanpage.yt_clip_min_s or 60) + 10),
        min_score=fanpage.yt_clip_min_score or 0,
    )
    niche = (fanpage.mode2_gallery_niches or [None])[0] or fanpage.name
    context = (
        f"Title: {meta.title}\nChannel: {meta.channel}\n"
        f"Duration: {meta.duration_s // 60}:{meta.duration_s % 60:02d}"
    )
    prompt = build_prompt(
        page=fanpage.name, niche=niche, language=language_name(fanpage.caption_language),
        context=context, transcript=srt, rules=rules, direction=direction,
    )
    return prompt, rules


def _save_ideas(db, fanpage, video, highlights) -> int:
    from app.models.yt_clip_ideas import YtClipIdea

    for h in highlights:
        db.add(YtClipIdea(
            fanpage_id=fanpage.id, yt_video_row_id=video.id, video_id=video.video_id,
            start_s=h.start, end_s=h.end, title=h.title, description=h.description,
            hook_text=h.hook_text, virality_score=h.score, transcript_excerpt=h.excerpt,
        ))
    video.status = "analyzed"
    video.ideas_created = len(highlights)
    video.analyzed_at = _now()
    video.last_error = None
    db.commit()
    return len(highlights)


def _claim_video(db, video_row_id: int) -> bool:
    from app.models.yt_videos import YtVideo

    claimed = (
        db.query(YtVideo)
        .filter(YtVideo.id == video_row_id, YtVideo.status == "discovered")
        .update({"status": "analyzing"}, synchronize_session=False)
    )
    db.commit()
    return bool(claimed)


@celery_app.task(name="app.tasks.yt_clip.analyze_video", soft_time_limit=1200, time_limit=1320)
def analyze_video(video_row_id: int):
    """Subtitles → 9Router highlight search → YtClipIdea rows, for one video."""
    from app.models.yt_videos import YtVideo
    from app.services import yt_downloader as yd
    from app.services.yt_highlights import find_highlights
    from app.services.yt_transcript import parse_json3, to_srt

    db = SessionLocal()
    workdir = yt_private_dir("work") / f"analyze_{video_row_id}"
    try:
        if not _claim_video(db, video_row_id):
            return
        video = db.query(YtVideo).filter_by(id=video_row_id).first()
        fanpage = video.fanpage
        try:
            meta, data = yd.fetch_video(db, video.video_id, workdir)
        except yd.YouTubeBlockedError as exc:
            video.status, video.last_error = "discovered", str(exc)[:2000]
            db.commit()
            return
        except yd.YouTubeUnavailableError as exc:
            _skip(db, video, f"unavailable: {str(exc)[:300]}")
            return
        video.title, video.channel_name = meta.title or video.title, meta.channel
        video.duration_s, video.language = meta.duration_s, meta.language
        video.published_at = meta.published_at or video.published_at
        db.commit()
        reason = _skip_reason(meta, video, fanpage)
        if reason:
            _skip(db, video, reason)
            return
        words_path = _store_subtitles(video.video_id, meta.sub_track, data)
        lines, words = parse_json3(data)
        video.words_path = str(words_path)
        db.commit()
        direction = video.source.direction if video.source else None
        prompt, rules = _highlight_prompt(fanpage, video, meta, to_srt(lines), direction)
        highlights = find_highlights(prompt, words, float(meta.duration_s), rules, fanpage_id=fanpage.id)
        n = _save_ideas(db, fanpage, video, highlights)
        logger.info("yt_clip: %s analysed for fanpage %d → %d idea(s)", video.video_id, fanpage.id, n)
    except Exception as exc:
        db.rollback()
        video = db.query(YtVideo).filter_by(id=video_row_id).first()
        if video and video.status == "analyzing":
            _retry_or_fail(db, video, str(exc))
        logger.error("yt_clip: analysis of video row %d crashed: %s", video_row_id, exc, exc_info=True)
    finally:
        yd.cleanup_workdir(workdir)
        db.close()


def _dispatch_analysis(db, fanpage) -> bool:
    from app.services.yt_downloader import blocked_until

    if blocked_until(db):
        return False
    if _pending_idea_count(db, fanpage.id) >= 2 * max(1, fanpage.yt_clip_daily_count or 1):
        return False
    video = _next_video_to_analyse(db, fanpage.id)
    if not video:
        return False
    analyze_video.delay(video.id)
    return True


def _dispatch_renders(db) -> int:
    from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
    from app.tasks.yt_clip_render import render_youtube_clip

    ids = [
        jid for (jid,) in db.query(PublishJob.id).filter(
            PublishJob.content_type == ContentType.youtube_clip,
            PublishJob.status == PublishJobStatus.pending_design,
            PublishJob.is_deleted == False,  # noqa: E712
        ).order_by(PublishJob.created_at.asc()).limit(_RENDER_DISPATCH_PER_TICK).all()
    ]
    for jid in ids:
        render_youtube_clip.delay(jid)
    return len(ids)


@celery_app.task(name="app.tasks.yt_clip.yt_clip_cycle")
def yt_clip_cycle():
    """Beat tick (any hour): discover new videos, analyse one per thin
    queue, and (re)dispatch clip renders that are waiting."""
    db = SessionLocal()
    try:
        _recover_stale_analyses(db)
        discovered = analysing = 0
        for fp in _enabled_fanpages(db):
            discovered += _discover_all(db, fp)
            analysing += int(_dispatch_analysis(db, fp))
        renders = _dispatch_renders(db)
        if discovered or analysing or renders:
            logger.info("yt_clip cycle: +%d video(s), %d analysis, %d render(s) dispatched",
                        discovered, analysing, renders)
    finally:
        db.close()


# ── [4] consume (paced) ──────────────────────────────────────────────────────

def _target_by_now(quota: int, now_wib_hour: float) -> int:
    from app.tasks.facebook_photo import _target_by_now as paced

    return paced(quota, now_wib_hour)


def _next_idea(db, fanpage_id: int):
    from app.models.yt_clip_ideas import YtClipIdea
    from app.models.yt_videos import YtVideo

    return (
        db.query(YtClipIdea)
        .join(YtVideo, YtVideo.id == YtClipIdea.yt_video_row_id)
        .filter(YtClipIdea.fanpage_id == fanpage_id, YtClipIdea.status == "pending")
        .order_by(YtVideo.published_at.desc().nullslast(), YtClipIdea.virality_score.desc(), YtClipIdea.created_at.asc())
        .first()
    )


def _clip_caption_prompt(fanpage, idea, video) -> str:
    """The Facebook post text, on the fanpage's Mode 2 caption settings."""
    attribution = ""
    if fanpage.mode2_source_attribution and video.channel_name:
        attribution = f'\n- End with a source line: "Source: {video.channel_name} (YouTube)"'
    return f"""You are the social media editor of the Facebook page "{fanpage.name}". Write the caption for a short video clip (a Reel) cut from a YouTube video.

CLIP TITLE: {idea.title}
WHY IT'S INTERESTING: {idea.description or "-"}
HOOK: {idea.hook_text or "-"}
FROM VIDEO: {video.title or "-"} ({video.channel_name or "YouTube"})
WHAT IS SAID IN THE CLIP (transcript excerpt, may contain speech-recognition errors):
{(idea.transcript_excerpt or "")[:1200]}

Caption rules:
- Language: {fanpage.mode2_caption_language}
- Tone: {fanpage.mode2_caption_tone}
- Maximum length: {fanpage.mode2_caption_max_length} characters
- Short paragraphs; open with a hook, give the context, stay faithful to what is actually said.
- End with EXACTLY {fanpage.mode2_caption_hashtag_count} specific, relevant hashtags on their own line.
- Call-to-action: {fanpage.mode2_caption_cta_text or "invite viewers to comment"}{attribution}
- Additional notes: {fanpage.mode2_caption_custom_prompt or "none"}

OUTPUT: only the final caption, no explanation."""


def _consume_one(db, fanpage) -> bool:
    from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType, AIProvider
    from app.models.target_fanpages import PublishMode
    from app.services.ai_caption import generate_caption

    idea = _next_idea(db, fanpage.id)
    if not idea:
        return False
    video = idea.yt_video
    try:
        caption, provider = generate_caption(_clip_caption_prompt(fanpage, idea, video))
    except Exception as exc:
        logger.warning("yt_clip: caption for idea %d failed: %s", idea.id, exc)
        return False
    job = PublishJob(
        fanpage_id=fanpage.id, post_id=None, content_type=ContentType.youtube_clip,
        design_title=idea.title, design_subtitle=idea.hook_text,
        yt_clip_idea_id=idea.id, yt_video_id=idea.video_id,
        clip_start_s=idea.start_s, clip_end_s=idea.end_s,
        ai_generated_caption=caption.strip(), ai_provider_used=AIProvider(provider),
        status=PublishJobStatus.pending_design,
    )
    db.add(job)
    idea.status, idea.used_at = "used", _now()
    db.commit()
    logger.info("yt_clip: fanpage %d job %d from idea %d (%s +%.0fs) %r",
                fanpage.id, job.id, idea.id, idea.video_id, idea.end_s - idea.start_s, idea.title)
    if fanpage.yt_clip_publish_mode == PublishMode.auto:
        from app.tasks.yt_clip_render import render_youtube_clip
        render_youtube_clip.apply_async(args=[job.id], countdown=random.randint(5, 60))
    return True


@celery_app.task(name="app.tasks.yt_clip.generate_yt_clip_content")
def generate_yt_clip_content():
    """Beat tick: consume clip ideas toward each fanpage's paced daily count."""
    from sqlalchemy import func
    from app.models.publish_jobs import PublishJob, ContentType
    from app.tasks.facebook_photo import _wib_day_bounds_utc

    db = SessionLocal()
    try:
        now_utc = _now()
        now_wib = now_utc.replace(tzinfo=timezone.utc).astimezone(WIB)
        hour = now_wib.hour + now_wib.minute / 60.0
        if not _WINDOW_START_HOUR <= hour < _WINDOW_END_HOUR:
            return
        day_start, day_end = _wib_day_bounds_utc(now_utc)
        created = 0
        for fp in _enabled_fanpages(db):
            quota = fp.yt_clip_daily_count or 0
            done = db.query(func.count(PublishJob.id)).filter(
                PublishJob.fanpage_id == fp.id, PublishJob.content_type == ContentType.youtube_clip,
                PublishJob.is_deleted == False,  # noqa: E712
                PublishJob.created_at >= day_start, PublishJob.created_at < day_end,
            ).scalar() or 0
            target, budget = _target_by_now(quota, hour), _MAX_CATCHUP_PER_TICK
            while done < quota and done < target and budget > 0 and _consume_one(db, fp):
                done, budget, created = done + 1, budget - 1, created + 1
        if created:
            logger.info("yt_clip: %d clip job(s) created", created)
    finally:
        db.close()
