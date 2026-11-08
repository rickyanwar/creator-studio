"""Publishing, listing and cleanup must respect group-only ownership."""

from datetime import timedelta
from unittest.mock import Mock

import pytest

from backend.tests.test_fb_share_group_jobs import db_session, make_job, NOW
from app.models.fb_group_share import FbShareItem, FbShareTask
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import PublishDelivery
from app.services.fb_share import group_jobs as core


def test_publisher_and_delivery_guard(db_session, monkeypatch):
    from app.tasks import publisher
    from app.services.social_delivery_planner import ensure_deliveries
    db = db_session
    job = make_job(db)
    monkeypatch.setattr(publisher, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    assert ensure_deliveries(db, job) == []
    publisher.publish_job.run(job.id)
    assert db.query(PublishDelivery).count() == 0
    assert db.query(FbShareItem).count() == 1
    assert job.status == PublishJobStatus.skipped


def test_waiting_and_list_exclusions(db_session, monkeypatch):
    from app.services.mode_quota import waiting_groups
    from app.api import publish_jobs
    db = db_session
    group = make_job(db)
    db.add(PublishJob(fanpage_id=1, content_type=ContentType.discussion,
                      status=PublishJobStatus.pending_publish, created_at=NOW, is_deleted=False))
    db.flush()
    assert waiting_groups(db, 1, 1, NOW) == {"discussion"}
    monkeypatch.setattr(publish_jobs, "_enrich_job", lambda job, db: job.id)
    default = publish_jobs.list_jobs(db, None, status=None, fanpage_id=None, limit=50, offset=0)
    included = publish_jobs.list_jobs(db, None, status=None, fanpage_id=None, limit=50, offset=0, include_group_only=True)
    assert group.id not in default and group.id in included


@pytest.mark.parametrize("cleanup_kind", ["youtube", "design", "ai_video"])
def test_cleanup_keeps_open_media_and_deletes_after_retention(db_session, monkeypatch, tmp_path, cleanup_kind):
    from app.tasks import cleanup, yt_clip_render
    db = db_session
    media_root = tmp_path / "ai_videos"
    media_root.mkdir()
    path = media_root / "media.mp4"
    path.write_bytes(b"test")
    job = make_job(db, video_path=str(path), updated_at=NOW - timedelta(days=30))
    if cleanup_kind == "design":
        from app.services.fb_share import feeder
        from app.services import radar_clustering
        monkeypatch.setattr(feeder, "load_image_bytes", lambda ref: b"photo")
        monkeypatch.setattr(radar_clustering, "compute_phash", lambda data: "abcd")
        job.content_type = ContentType.discussion
        job.design_image_path = str(path)
    elif cleanup_kind == "youtube":
        job.content_type = ContentType.youtube_clip
    item = core.hand_over(db, job, NOW - timedelta(days=20))
    item.status = "done"
    task = FbShareTask(item_id=item.id, group_id=1, destination_id=1,
                       delivery_mode="phone_auto", scheduled_for=NOW, state="pending_admin",
                       updated_at=NOW - timedelta(days=8))
    db.add(task)
    db.commit()
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(cleanup, "SessionLocal", lambda: db)
    monkeypatch.setattr(cleanup.settings, "storage_base_path", str(tmp_path))
    monkeypatch.setattr(cleanup, "_cleanup_facebook_photos", Mock(return_value=0))
    monkeypatch.setattr(cleanup, "_job_go_live_pending", Mock(return_value=set()))
    monkeypatch.setattr(yt_clip_render, "_now", lambda: NOW)
    run = {"youtube": lambda: yt_clip_render._cleanup_job_videos(db),
           "design": cleanup.cleanup_old_designs.run,
           "ai_video": cleanup.cleanup_old_ai_videos.run}[cleanup_kind]
    run()
    assert path.exists()
    task.state = "published"
    task.updated_at = NOW - timedelta(days=9)
    db.commit()
    run()
    assert not path.exists()
