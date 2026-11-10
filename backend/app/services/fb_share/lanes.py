"""Hidden "group lane" fanpages: one per brand, producing content only for Facebook groups.

A lane is a normal TargetFanpage row, so every mode setting, source table and generation
pipeline works on it unchanged. Lanes are hidden from fanpage lists, never connect to a
Page destination, and every job created for a lane is marked as a group job (see the
after_flush listener at the bottom of this module).
"""

from copy import deepcopy
import logging
from weakref import WeakSet

from sqlalchemy import event, inspect, insert, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import get_history

from app.models.ai_video import VideoJob
from app.models.fb_group_share import FbShareGroupJob, FbShareLane
from app.models.publish_jobs import PublishJob
from app.models.radar import RadarStoryDecision
from app.models.social_publishing import SocialDestination
from app.models.target_fanpages import PublishMode, TargetFanpage
from .brand_settings import get_brand_share

logger = logging.getLogger(__name__)

LANE_NAME_SUFFIX = " (groups)"
FACEBOOK_KINDS = ("facebook_profile", "facebook_page")

# Rule: a column is LANE-OWNED when the Content-tab mode sections (IgTab, NewsTab, DiscussionTab,
# PinterestTab, FacebookTab, YoutubeTab/YtClipSection, AiVideoTab/AiVideoSection, RadarTab and the
# ContentTab mode toggles / "Check AI results") edit it, plus identity, connection, publishing and
# row bookkeeping columns. Groups own these values; they are never copied from the brand.
# Every other column is a LOOK field (templates, colours, badges, captions, niches, watermark/logo,
# timezone, design settings): copied brand -> lane on creation and on every brand save.
# Columns that a mode section shows but that are brand look (gallery niches, news badge text,
# split template, YouTube watermark, AI video logo/caption overlay) stay LOOK, so the brand
# remains the single place where its look is edited.
LANE_OWNED_FIELDS = frozenset({
    # identity, connection, publishing, bookkeeping
    "id", "name", "is_active", "is_connected", "repliz_account_id",
    "publish_mode", "publish_sleep_start_hour", "publish_sleep_end_hour",
    "publish_daily_limit", "publish_hour_weights",
    "last_synced_at", "created_at", "updated_at",
    # Instagram
    "mode1_ig_repost_enabled", "ig_recreate_enabled", "ig_recreate_smart_layout",
    "visual_engine", "qa_ig_enabled",
    # News
    "mode2_news_content_enabled", "mode2_publish_mode", "mode2_title_max_chars",
    "mode2_editorial_gate_enabled", "news_visual_engine", "qa_news_enabled", "quote_shorten_enabled",
    # Discussion
    "discussion_enabled", "discussion_publish_mode", "discussion_daily_count",
    "discussion_topic_mode", "discussion_label_mode",
    # Pinterest
    "pinterest_enabled", "pinterest_publish_mode", "pinterest_daily_count",
    "pinterest_source_mode", "pinterest_custom_prompt", "pinterest_allow_low_quality",
    # Facebook photo recreate
    "facebook_photo_enabled", "facebook_photo_publish_mode", "facebook_photo_daily_count",
    "facebook_photo_topic_filter", "qa_facebook_enabled",
    # YouTube clips
    "yt_clip_enabled", "yt_clip_publish_mode", "yt_clip_daily_count", "yt_clip_min_s",
    "yt_clip_max_s", "yt_clip_per_video", "yt_clip_min_score", "yt_clip_max_video_age_days",
    "yt_clip_captions", "yt_clip_action_crop", "yt_clip_title_max_chars",
    "yt_clip_description_max_chars",
    # AI video
    "ai_video_enabled", "ai_video_publish_mode", "ai_video_daily_count", "ai_video_min_score",
    "ai_video_template", "ai_video_templates", "ai_video_seconds_min", "ai_video_seconds_max",
    "ai_video_max_credits_per_video", "ai_video_max_credits_per_day", "ai_video_narrator",
    "ai_video_narration", "ai_video_title_max_chars", "ai_video_description_max_chars",
    "ai_video_prompt_polish_enabled", "ai_video_clip_qc_enabled", "ai_video_clip_qc_max_attempts",
    "qa_ai_video_enabled",
    # Viral Radar
    "radar_enabled", "radar_niches", "radar_shadow", "radar_min_likes", "radar_confirm_ratio",
    "radar_fast_ratio", "radar_fast_window_min", "radar_shelf_news_h", "radar_shelf_evergreen_h",
    "radar_burst_enabled", "radar_daily_max",
})

LOOK_FIELDS = frozenset({
    "username", "picture_url", "platform_type", "timezone", "target_country",
    # gallery, templates and brand colours
    "mode2_gallery_keywords", "mode2_gallery_niches", "mode2_default_template_id",
    "default_quote_template_id", "default_news_template_id", "default_discussion_template_id",
    "accent_color", "template_style", "brand_override", "accent_secondary_color", "badge_color",
    "badge_text_color", "quote_mark_color", "quote_streak",
    "ig_recreate_quote_template_id", "ig_recreate_news_template_id", "ig_recreate_split_template_id",
    "design_expand", "fb_visual_engine",
    # news captions and badges
    "mode2_caption_tone", "mode2_caption_language", "mode2_caption_max_length",
    "mode2_caption_hashtag_count", "mode2_caption_cta_text", "mode2_caption_custom_prompt",
    "mode2_badge_text", "badge_breaking_enabled", "badge_discussion_enabled", "news_badge_percent",
    "mode2_source_attribution", "pinterest_hashtag_count",
    # video look
    "yt_clip_watermark", "ai_video_clips", "ai_video_clips_min",
    "ai_video_logo_enabled", "ai_video_logo_size_pct", "ai_video_logo_position",
    "ai_video_logo_margin_pct", "ai_video_captions_enabled", "ai_video_caption_size",
    "ai_video_caption_bottom_pct", "ai_video_caption_highlight", "ai_video_cover_enabled",
    # caption style
    "caption_tone", "caption_language", "caption_max_length", "caption_hashtag_count",
    "caption_must_include", "caption_must_avoid", "caption_cta_text", "use_attribution",
    "caption_attribution_template", "attribution_position", "caption_custom_prompt",
    # watermark / logo
    "watermark_text", "watermark_image_url",
})

# Mode switches that start OFF on a new lane; the owner turns them on in the Group Share tab.
MODE_TOGGLES = (
    "mode1_ig_repost_enabled", "ig_recreate_enabled", "mode2_news_content_enabled",
    "discussion_enabled", "pinterest_enabled", "facebook_photo_enabled", "yt_clip_enabled",
    "ai_video_enabled", "radar_enabled",
)

# Content type -> Group Share mode key for jobs a lane produces.
CONTENT_TYPE_MODES = {
    "ig_repost": "ig", "ig_recreate": "ig", "news_content": "news",
    "discussion": "discussion", "pinterest_content": "pinterest",
    "facebook_recreate": "fb_recreate", "youtube_clip": "yt_clips",
}


def get_lane_id(db, brand_id: int) -> int | None:
    return db.scalar(select(FbShareLane.lane_fanpage_id).where(FbShareLane.brand_fanpage_id == brand_id))


def lane_brand_id(db, fanpage_id: int) -> int | None:
    return db.scalar(select(FbShareLane.brand_fanpage_id).where(FbShareLane.lane_fanpage_id == fanpage_id))


def is_lane(db, fanpage_id: int) -> bool:
    return lane_brand_id(db, fanpage_id) is not None


def lane_fanpage_ids():
    """Select of every lane fanpage id, for ``TargetFanpage.id.notin_(lane_fanpage_ids())``."""
    return select(FbShareLane.lane_fanpage_id)


def sync_lane_from_brand(db, brand: TargetFanpage, lane: TargetFanpage) -> None:
    """Copy the brand's LOOK fields onto its lane. The caller commits."""
    for field in sorted(LOOK_FIELDS):
        setattr(lane, field, deepcopy(getattr(brand, field)))
    lane.name = f"{brand.name}{LANE_NAME_SUFFIX}"


def ensure_lane(db, brand_id: int) -> TargetFanpage:
    """Return the brand's lane, creating it on first call. The caller commits."""
    if is_lane(db, brand_id):
        raise ValueError("A group lane cannot have its own lane")
    brand = db.get(TargetFanpage, brand_id)
    if brand is None:
        raise LookupError(f"Brand {brand_id} not found")
    lane_id = get_lane_id(db, brand_id)
    if lane_id is not None:
        lane = db.get(TargetFanpage, lane_id)
        if lane is not None:
            return lane
        # The lane fanpage row was deleted by hand: drop the stale mapping and recreate.
        db.query(FbShareLane).filter_by(brand_fanpage_id=brand_id).delete(synchronize_session=False)
    enabled, _ = get_brand_share(db, brand_id)
    lane = TargetFanpage(
        name=f"{brand.name}{LANE_NAME_SUFFIX}", repliz_account_id=None, is_connected=False,
        publish_mode=PublishMode.auto, is_active=enabled,
        **{toggle: False for toggle in MODE_TOGGLES},
    )
    sync_lane_from_brand(db, brand, lane)
    db.add(lane)
    db.flush()
    db.add(FbShareLane(brand_fanpage_id=brand_id, lane_fanpage_id=lane.id))
    db.flush()
    return lane


def set_lane_active(db, brand_id: int, enabled: bool) -> None:
    """Keep the lane's is_active equal to the brand's Group Share switch. The caller commits."""
    lane_id = get_lane_id(db, brand_id)
    lane = db.get(TargetFanpage, lane_id) if lane_id is not None else None
    if lane is not None:
        lane.is_active = enabled


def sibling_fanpage_ids(db, fanpage_id: int) -> set[int]:
    """Fanpages that may share one AI-edited image with ``fanpage_id`` (edit once).

    The brand and its lane, plus every fanpage owning a Facebook destination on the same
    Facebook profile as one of the brand's Facebook destinations, and those fanpages' lanes.
    """
    brand_id = lane_brand_id(db, fanpage_id) or fanpage_id
    brand_ids = {brand_id}
    profile_keys = set()
    for dest_id, parent_id in db.query(SocialDestination.id, SocialDestination.parent_profile_id).filter(
        SocialDestination.content_project_id == brand_id, SocialDestination.kind.in_(FACEBOOK_KINDS),
    ).all():
        profile_keys.update(key for key in (dest_id, parent_id) if key is not None)
    if profile_keys:
        brand_ids.update(project_id for (project_id,) in db.query(SocialDestination.content_project_id).filter(
            (SocialDestination.id.in_(profile_keys)) | (SocialDestination.parent_profile_id.in_(profile_keys)),
            SocialDestination.content_project_id.isnot(None),
        ).all())
    lane_ids = {lane_id for (lane_id,) in db.query(FbShareLane.lane_fanpage_id).filter(
        FbShareLane.brand_fanpage_id.in_(brand_ids),
    ).all()}
    return {fanpage_id} | brand_ids | lane_ids


_ENGINES_WITH_LANE_TABLE = WeakSet()


def _lane_table_exists(connection) -> bool:
    # No fb_share_lanes table means no lanes exist (e.g. before the migration ran), so there is
    # nothing to mark. Only a positive answer is cached, so a later migration is picked up.
    engine = connection.engine
    if engine in _ENGINES_WITH_LANE_TABLE:
        return True
    if not inspect(connection).has_table(FbShareLane.__tablename__):
        return False
    _ENGINES_WITH_LANE_TABLE.add(engine)
    return True


def _content_type_key(job) -> str:
    return getattr(job.content_type, "value", job.content_type) or "ig_repost"


@event.listens_for(Session, "after_flush")
def _mark_lane_jobs(session, flush_context) -> None:
    """Insert a group-job marker for every new PublishJob / VideoJob of a lane fanpage.

    The marker carries the BRAND id, so every existing group-job guard applies (no Page
    deliveries, hand-over to Group Share, cleanup protection). A lane's ai_video PublishJob
    gets no new marker: ai_video_finalize links it to the VideoJob's marker.
    """
    publish_jobs = [obj for obj in session.new if isinstance(obj, PublishJob)
                    and _content_type_key(obj) != "ai_video"]
    video_jobs = [obj for obj in session.new if isinstance(obj, VideoJob)]
    radar_links = [obj.publish_job_id for obj in list(session.new) + list(session.dirty)
                   if isinstance(obj, RadarStoryDecision) and obj.publish_job_id is not None
                   and get_history(obj, "publish_job_id").added]
    if not (publish_jobs or video_jobs or radar_links):
        return
    connection = session.connection()
    if not _lane_table_exists(connection):
        return
    if publish_jobs or video_jobs:
        fanpage_ids = {job.fanpage_id for job in publish_jobs + video_jobs if job.fanpage_id is not None}
        brands = dict(connection.execute(
            select(FbShareLane.lane_fanpage_id, FbShareLane.brand_fanpage_id).where(
                FbShareLane.lane_fanpage_id.in_(fanpage_ids),
            )
        ).all()) if fanpage_ids else {}
        rows = [
            {"fanpage_id": brands[job.fanpage_id], "publish_job_id": job.id, "video_job_id": None,
             "mode": CONTENT_TYPE_MODES.get(_content_type_key(job), "ig")}
            for job in publish_jobs if job.fanpage_id in brands
        ] + [
            {"fanpage_id": brands[job.fanpage_id], "publish_job_id": None, "video_job_id": job.id, "mode": "ai_video"}
            for job in video_jobs if job.fanpage_id in brands
        ]
        if rows:
            connection.execute(insert(FbShareGroupJob), rows)
            logger.info("Group Share: marked %d lane job(s) as group jobs", len(rows))
    if radar_links:
        # Radar recreates link the decision after the job insert; a lane IG job becomes viral_radar.
        connection.execute(
            update(FbShareGroupJob)
            .where(FbShareGroupJob.publish_job_id.in_(radar_links), FbShareGroupJob.mode == "ig")
            .values(mode="viral_radar")
        )
