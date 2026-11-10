"""SQLite contracts for group-job markers and hand-over."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import DefaultClause

from app.models.fb_group_share import FbShareBrandSettings, FbShareGroupJob, FbShareItem, FbShareTask
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import PublishDelivery, SocialDestination
from app.models.target_fanpages import TargetFanpage
from app.models.ai_video import VideoJob
from app.services.fb_share import group_jobs as core
from app.services.fb_share.brand_settings import set_brand_share
from app.services.fb_share.feeder import collect_group_only_items

NOW = datetime(2026, 11, 8, 12)


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://")
    with patch.object(SocialDestination.__table__.c.supported_media, "server_default", DefaultClause(text("'[]'"))), patch.object(
        VideoJob.__table__.c.clips_done, "server_default", DefaultClause(text("'[]'")),
    ):
        for model in (TargetFanpage, FbShareBrandSettings, FbShareGroupJob, FbShareItem,
                      FbShareTask, PublishJob, PublishDelivery, SocialDestination, VideoJob):
            model.__table__.create(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(TargetFanpage(id=1, name="Brand"))
        set_brand_share(db, 1, True, {"niches": ["f1"], "sources": {
            "ai_video": {"source": "both", "groups_only_per_day": 2}}})
        db.commit()
        yield db
    engine.dispose()


def make_job(db, **values):
    fields = dict(fanpage_id=1, content_type=ContentType.ai_video,
                  status=PublishJobStatus.pending_publish, video_path="clip.mp4",
                  ai_generated_caption="Race day #F1", created_at=NOW, updated_at=NOW, is_deleted=False)
    fields.update(values)
    job = PublishJob(**fields)
    db.add(job)
    db.flush()
    core.mark_group_job(db, fanpage_id=1, mode="ai_video", publish_job_id=job.id)
    db.flush()
    return job


def test_mark_link_selects_and_validation(db_session):
    db = db_session
    with pytest.raises(ValueError):
        core.mark_group_job(db, fanpage_id=1, mode="ai_video")
    core.mark_group_job(db, fanpage_id=1, mode="ai_video", video_job_id=17)
    assert core.is_group_only_video(db, 17)
    assert not core.is_group_only(db, 18)
    core.link_video_publish_job(db, video_job_id=17, publish_job_id=18)
    assert core.is_group_only(db, 18)
    assert list(db.scalars(core.group_publish_job_ids())) == [18]
    assert list(db.scalars(core.group_video_job_ids())) == [17]


def test_hand_over_and_feeder_idempotent(db_session):
    db = db_session
    job = make_job(db)
    page = db.get(TargetFanpage, 1)
    assert collect_group_only_items(db, page, NOW) == 1
    assert collect_group_only_items(db, page, NOW) == 0
    item = core.hand_over(db, job, NOW)
    assert db.query(FbShareItem).count() == 1
    assert (item.caption, item.hashtags, item.niche) == ("Race day", "#F1", "f1")
    assert item.media == ["clip.mp4"]
    assert item.expires_at == NOW + timedelta(hours=72)
    assert job.status == PublishJobStatus.skipped
    assert job.last_error == "group_only: handed to Group Share"
    assert db.query(FbShareGroupJob).one().handed_at == NOW


@pytest.mark.parametrize("status", [PublishJobStatus.pending_design, PublishJobStatus.rendering, PublishJobStatus.failed])
def test_unready_jobs_stay_unhanded(db_session, status):
    job = make_job(db_session, status=status)
    assert core.hand_over(db_session, job, NOW) is None
    assert db_session.query(FbShareItem).count() == 0


def test_missing_media_stays_unhanded(db_session):
    job = make_job(db_session, video_path=None)
    assert core.hand_over(db_session, job, NOW) is None


def test_photo_hash_fail_closed_then_handover(db_session, monkeypatch):
    from app.services.fb_share import feeder
    from app.services import radar_clustering
    job = make_job(db_session, content_type=ContentType.discussion, design_image_path="card.png")
    monkeypatch.setattr(feeder, "load_image_bytes", lambda ref: b"photo")
    def unavailable(data):
        raise ValueError("invalid photo")
    monkeypatch.setattr(radar_clustering, "compute_phash", unavailable)
    assert core.hand_over(db_session, job, NOW) is None
    assert db_session.query(FbShareItem).count() == 0
    monkeypatch.setattr(radar_clustering, "compute_phash", lambda data: "abcd")
    item = core.hand_over(db_session, job, NOW)
    assert (item.media_kind, item.phash, item.media) == ("photo", "abcd", ["card.png"])


@pytest.mark.parametrize("state", ["planned", "sent_to_pic", "publishing", "pending_admin", "uncertain"])
def test_retention_blocks_open_tasks(db_session, state):
    job = make_job(db_session)
    item = core.hand_over(db_session, job, NOW - timedelta(days=20))
    item.status = "done"
    task = FbShareTask(item_id=item.id, group_id=1, destination_id=1,
                       delivery_mode="phone_auto", scheduled_for=NOW, state=state,
                       updated_at=NOW - timedelta(days=8))
    db_session.add(task)
    assert not core.media_cleanup_allowed(db_session, job.id, NOW)
    task.state = "published"
    task.updated_at = NOW - timedelta(days=9)
    assert core.media_cleanup_allowed(db_session, job.id, NOW)
    task.updated_at = NOW - timedelta(days=7) + timedelta(seconds=1)
    assert not core.media_cleanup_allowed(db_session, job.id, NOW)
