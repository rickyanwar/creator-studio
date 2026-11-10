"""Group Share lanes: hidden per-brand fanpages that produce content only for groups."""

from datetime import datetime
import json
import sqlite3
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import DefaultClause

from app.models.ai_video import VideoJob
from app.models.fb_group_share import FbShareBrandSettings, FbShareGroupJob, FbShareItem, FbShareLane, FbShareTask
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.radar import RadarStoryDecision
from app.models.social_publishing import PublishDelivery, SocialDestination
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.fb_share import lanes
from app.services.fb_share.brand_settings import set_brand_share
from app.services.social_project_eligibility import project_can_generate

NOW = datetime(2026, 11, 10, 12)
# Lanes copy Postgres ARRAY look fields; let SQLite store them (same as other SQLite suites).
sqlite3.register_adapter(list, json.dumps)


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with patch.object(SocialDestination.__table__.c.supported_media, "server_default", DefaultClause(text("'[]'"))), patch.object(
        VideoJob.__table__.c.clips_done, "server_default", DefaultClause(text("'[]'")),
    ):
        for model in (TargetFanpage, FbShareBrandSettings, FbShareGroupJob, FbShareLane, FbShareItem, FbShareTask,
                      PublishJob, PublishDelivery, SocialDestination, VideoJob, RadarStoryDecision):
            model.__table__.create(engine, checkfirst=True)
    with sessionmaker(bind=engine)() as session:
        session.add_all([
            TargetFanpage(id=1, name="Brand", accent_color="#FF0000", caption_tone="witty",
                          timezone="America/New_York"),
            TargetFanpage(id=2, name="Sister"),
            TargetFanpage(id=3, name="Stranger"),
        ])
        session.flush()
        set_brand_share(session, 1, True, {"niches": ["f1"]})
        session.commit()
        yield session
    engine.dispose()


def test_every_fanpage_column_is_lane_owned_or_look():
    columns = {column.name for column in TargetFanpage.__table__.columns}
    assert not lanes.LANE_OWNED_FIELDS & lanes.LOOK_FIELDS
    assert lanes.LANE_OWNED_FIELDS | lanes.LOOK_FIELDS == columns, (
        "New TargetFanpage column: add it to LANE_OWNED_FIELDS or LOOK_FIELDS in services/fb_share/lanes.py. "
        f"Missing: {sorted(columns - lanes.LANE_OWNED_FIELDS - lanes.LOOK_FIELDS)}; "
        f"unknown: {sorted((lanes.LANE_OWNED_FIELDS | lanes.LOOK_FIELDS) - columns)}"
    )
    assert set(lanes.MODE_TOGGLES) <= lanes.LANE_OWNED_FIELDS


def test_ensure_lane_creates_hidden_offline_lane_with_brand_look(db):
    lane = lanes.ensure_lane(db, 1)
    db.commit()
    assert lane.name == "Brand (groups)"
    assert (lane.repliz_account_id, lane.is_connected, lane.publish_mode) == (None, False, PublishMode.auto)
    assert lane.is_active is True
    assert all(getattr(lane, toggle) is False for toggle in lanes.MODE_TOGGLES)
    assert (lane.accent_color, lane.caption_tone, lane.timezone) == ("#FF0000", "witty", "America/New_York")
    assert lanes.get_lane_id(db, 1) == lane.id
    assert lanes.lane_brand_id(db, lane.id) == 1
    assert lanes.is_lane(db, lane.id) and not lanes.is_lane(db, 1)
    assert lanes.lane_brand_id(db, 1) is None


def test_ensure_lane_is_idempotent_and_rejects_lanes(db):
    lane = lanes.ensure_lane(db, 1)
    assert lanes.ensure_lane(db, 1).id == lane.id
    assert db.query(FbShareLane).count() == 1
    with pytest.raises(ValueError):
        lanes.ensure_lane(db, lane.id)
    with pytest.raises(LookupError):
        lanes.ensure_lane(db, 999)


def test_lane_inactive_when_brand_share_disabled(db):
    assert lanes.ensure_lane(db, 2).is_active is False
    lanes.set_lane_active(db, 2, True)
    assert db.get(TargetFanpage, lanes.get_lane_id(db, 2)).is_active is True
    lanes.set_lane_active(db, 3, True)  # no lane: no-op


def test_lane_fanpage_ids_hides_lanes(db):
    lane = lanes.ensure_lane(db, 1)
    db.commit()
    ids = [row.id for row in db.query(TargetFanpage).filter(TargetFanpage.id.notin_(lanes.lane_fanpage_ids()))]
    assert ids == [1, 2, 3]
    assert lane.id not in ids


def test_sync_copies_look_only(db):
    lane = lanes.ensure_lane(db, 1)
    lane.discussion_daily_count = 7
    brand = db.get(TargetFanpage, 1)
    brand.accent_color, brand.name, brand.discussion_daily_count = "#00FF00", "Renamed", 1
    lanes.sync_lane_from_brand(db, brand, lane)
    assert (lane.accent_color, lane.name, lane.discussion_daily_count) == ("#00FF00", "Renamed (groups)", 7)


def test_sibling_fanpages_share_brand_lane_and_facebook_profile(db):
    db.add_all([
        SocialDestination(id=10, content_project_id=1, name="Profile", kind="facebook_profile"),
        SocialDestination(id=11, content_project_id=2, name="Sister Page", kind="facebook_page", parent_profile_id=10),
        SocialDestination(id=12, content_project_id=3, name="Other", kind="facebook_profile"),
    ])
    lane = lanes.ensure_lane(db, 1)
    sister_lane = lanes.ensure_lane(db, 2)
    db.commit()
    expected = {1, lane.id, 2, sister_lane.id}
    assert lanes.sibling_fanpage_ids(db, 1) == expected
    assert lanes.sibling_fanpage_ids(db, lane.id) == expected
    assert lanes.sibling_fanpage_ids(db, 3) == {3}


def test_project_can_generate_for_lane_follows_brand_switch(db):
    lane = lanes.ensure_lane(db, 1)
    db.commit()
    eligible = lambda: {row.id for row in db.query(TargetFanpage).filter(project_can_generate())}  # noqa: E731
    assert lane.id in eligible()
    set_brand_share(db, 1, False, {})
    db.commit()
    assert lane.id not in eligible()
    assert not {1, 2, 3} & eligible()


def _job(db, fanpage_id, content_type, **values):
    job = PublishJob(fanpage_id=fanpage_id, content_type=content_type, status=PublishJobStatus.pending_publish,
                     created_at=NOW, updated_at=NOW, is_deleted=False, **values)
    db.add(job)
    db.flush()
    return job


@pytest.mark.parametrize("content_type,mode", [
    (ContentType.ig_repost, "ig"), (ContentType.ig_recreate, "ig"), (ContentType.news_content, "news"),
    (ContentType.discussion, "discussion"), (ContentType.pinterest_content, "pinterest"),
    (ContentType.facebook_recreate, "fb_recreate"), (ContentType.youtube_clip, "yt_clips"),
])
def test_lane_publish_job_gets_brand_marker(db, content_type, mode):
    lane = lanes.ensure_lane(db, 1)
    job = _job(db, lane.id, content_type)
    marker = db.query(FbShareGroupJob).filter_by(publish_job_id=job.id).one()
    assert (marker.fanpage_id, marker.mode, marker.video_job_id) == (1, mode, None)


def test_brand_jobs_are_not_marked_and_marker_is_not_duplicated(db):
    from app.services.fb_share.group_jobs import mark_group_job
    lane = lanes.ensure_lane(db, 1)
    _job(db, 1, ContentType.news_content)
    assert db.query(FbShareGroupJob).count() == 0
    job = _job(db, lane.id, ContentType.news_content)
    mark_group_job(db, fanpage_id=1, mode="news", publish_job_id=job.id)
    db.commit()
    assert db.query(FbShareGroupJob).count() == 1


def test_lane_video_job_marker_links_to_ai_video_publish_job(db):
    from app.services.fb_share.group_jobs import is_group_only, link_video_publish_job
    lane = lanes.ensure_lane(db, 1)
    video = VideoJob(fanpage_id=lane.id, template_key="motorsport_tech_dive", title="Title", caption="Caption",
                     model="veo", aspect="9:16", clip_seconds=8, clips=[], credits_per_clip=10, credits_estimate=20)
    db.add(video)
    db.flush()
    marker = db.query(FbShareGroupJob).filter_by(video_job_id=video.id).one()
    assert (marker.fanpage_id, marker.mode) == (1, "ai_video")
    publish = _job(db, lane.id, ContentType.ai_video, video_path="clip.mp4")
    assert db.query(FbShareGroupJob).count() == 1  # finalize links the video marker instead
    link_video_publish_job(db, video_job_id=video.id, publish_job_id=publish.id)
    db.commit()
    assert is_group_only(db, publish.id)


def test_radar_decision_turns_lane_ig_marker_into_viral_radar(db):
    lane = lanes.ensure_lane(db, 1)
    job = _job(db, lane.id, ContentType.ig_recreate)
    decision = RadarStoryDecision(story_id=1, fanpage_id=lane.id, rule="fast", status="pending")
    db.add(decision)
    db.flush()
    decision.publish_job_id = job.id
    db.commit()
    assert db.scalar(select(FbShareGroupJob.mode).where(FbShareGroupJob.publish_job_id == job.id)) == "viral_radar"


def test_lane_job_is_handed_to_brand_without_page_delivery(db, monkeypatch):
    from app.services.social_delivery_planner import ensure_deliveries
    from app.tasks import publisher
    lane = lanes.ensure_lane(db, 1)
    job = _job(db, lane.id, ContentType.youtube_clip, video_path="clip.mp4",
               ai_generated_caption="Overtake of the year #F1 #Racing")
    db.commit()
    monkeypatch.setattr(publisher, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    assert ensure_deliveries(db, job) == []
    publisher.publish_job.run(job.id)
    assert db.query(PublishDelivery).count() == 0
    item = db.query(FbShareItem).one()
    assert (item.fanpage_id, item.source_ref, item.niche, item.media_kind) == (1, str(job.id), "f1", "video")
    assert (item.caption, item.hashtags) == ("Overtake of the year", "#F1 #Racing")
    assert job.status == PublishJobStatus.skipped
    assert db.query(FbShareGroupJob).one().handed_at is not None
