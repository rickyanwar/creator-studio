from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.database import Base, SessionLocal, engine
from app.models.ai_video import VideoJob
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.target_fanpages import TargetFanpage
from app.tasks import celery_app
from app.tasks import cleanup


@pytest.fixture(scope="module", autouse=True)
def create_tables():
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    fanpage_ids = []
    try:
        yield session, fanpage_ids
    finally:
        session.rollback()
        if fanpage_ids:
            video_job_ids = [row[0] for row in session.query(VideoJob.id).filter(VideoJob.fanpage_id.in_(fanpage_ids)).all()]
            publish_job_ids = [row[0] for row in session.query(PublishJob.id).filter(PublishJob.fanpage_id.in_(fanpage_ids)).all()]
            if video_job_ids:
                session.query(VideoJob).filter(VideoJob.id.in_(video_job_ids)).delete(synchronize_session=False)
            if publish_job_ids:
                session.query(PublishJob).filter(PublishJob.id.in_(publish_job_ids)).delete(synchronize_session=False)
            session.query(TargetFanpage).filter(TargetFanpage.id.in_(fanpage_ids)).delete(synchronize_session=False)
            session.commit()
        session.close()


def _fanpage(session, fanpage_ids):
    fanpage = TargetFanpage(repliz_account_id=f"cleanup-{uuid4().hex}", name=f"Cleanup {uuid4().hex}")
    session.add(fanpage)
    session.flush()
    fanpage_ids.append(fanpage.id)
    return fanpage


def _old(now, days=15):
    return now - timedelta(days=days)


def _publish_job(session, fanpage, *, status, published_at, video_path, thumbnail_path, updated_at=None):
    if updated_at is None:
        updated_at = published_at
    job = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.ai_video,
        status=status,
        published_at=published_at,
        updated_at=updated_at,
        video_path=str(video_path) if video_path else None,
        video_url="https://example/video.mp4" if video_path else None,
        video_thumbnail_path=str(thumbnail_path) if thumbnail_path else None,
        video_thumbnail_url="https://example/thumb.jpg" if thumbnail_path else None,
        design_title="title",
    )
    session.add(job)
    session.flush()
    if updated_at is not None:
        session.query(PublishJob).filter(PublishJob.id == job.id).update(
            {"updated_at": updated_at}, synchronize_session=False
        )
    return job


def _video_job(session, fanpage, *, status, finished_at, video_path, thumbnail_path, publish_job_id=None):
    job = VideoJob(
        fanpage_id=fanpage.id,
        publish_job_id=publish_job_id,
        template_key="motorsport_tech_explainer",
        title="title",
        caption="caption",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[],
        credits_per_clip=15,
        credits_estimate=15,
        status=status,
        finished_at=finished_at,
        video_path=str(video_path) if video_path else None,
        video_url="https://example/video.mp4" if video_path else None,
        thumbnail_path=str(thumbnail_path) if thumbnail_path else None,
        thumbnail_url="https://example/thumb.jpg" if thumbnail_path else None,
    )
    session.add(job)
    session.flush()
    return job


def _run_cleanup(monkeypatch, media_root):
    monkeypatch.setattr(cleanup, "SessionLocal", SessionLocal)
    monkeypatch.setattr(cleanup.settings, "storage_base_path", str(media_root))
    cleanup.cleanup_old_ai_videos.run()


def test_published_old_files_and_linked_video_job_are_deleted(db, tmp_path, monkeypatch):
    session, fanpage_ids = db
    root = tmp_path / "media"
    ai_dir = root / "ai_videos"
    ai_dir.mkdir(parents=True)
    video = ai_dir / "published.mp4"
    thumb = ai_dir / "published.jpg"
    video.write_bytes(b"video")
    thumb.write_bytes(b"thumb")
    fanpage = _fanpage(session, fanpage_ids)
    published = _publish_job(session, fanpage, status=PublishJobStatus.published, published_at=_old(datetime.now()), video_path=video, thumbnail_path=thumb)
    linked_video = _video_job(session, fanpage, status="done", finished_at=None, video_path=video, thumbnail_path=thumb, publish_job_id=published.id)
    session.commit()
    publish_id, video_id = published.id, linked_video.id

    _run_cleanup(monkeypatch, root)

    verify = SessionLocal()
    assert not video.exists() and not thumb.exists()
    stored_publish = verify.get(PublishJob, publish_id)
    stored_video = verify.get(VideoJob, video_id)
    assert stored_publish.video_path is None and stored_publish.video_url is None
    assert stored_publish.video_thumbnail_path is None and stored_publish.video_thumbnail_url is None
    assert stored_video.video_path is None and stored_video.video_url is None
    assert stored_video.thumbnail_path is None and stored_video.thumbnail_url is None
    verify.close()


def test_young_and_pending_publish_jobs_are_untouched(db, tmp_path, monkeypatch):
    session, fanpage_ids = db
    root = tmp_path / "media"
    ai_dir = root / "ai_videos"
    ai_dir.mkdir(parents=True)
    fanpage = _fanpage(session, fanpage_ids)
    ids = []
    for status, name, age in ((PublishJobStatus.published, "young", 3), (PublishJobStatus.pending_review, "review", 15), (PublishJobStatus.pending_publish, "pending", 15)):
        path = ai_dir / f"{name}.mp4"
        path.write_bytes(b"video")
        job = _publish_job(session, fanpage, status=status, published_at=_old(datetime.now(), age), video_path=path, thumbnail_path=None)
        ids.append((job.id, path))
    session.commit()

    _run_cleanup(monkeypatch, root)

    verify = SessionLocal()
    for job_id, path in ids:
        assert path.exists()
        assert verify.get(PublishJob, job_id).video_path == str(path)
    verify.close()


def test_retention_thresholds_published_7d_failed_14d(db, tmp_path, monkeypatch):
    session, fanpage_ids = db
    root = tmp_path / "media"
    ai_dir = root / "ai_videos"
    ai_dir.mkdir(parents=True)
    fanpage = _fanpage(session, fanpage_ids)
    now = datetime.now()

    # published ai_video publish job 8 days ago -> files deleted
    f_pub_8d = ai_dir / "pub_8d.mp4"
    f_pub_8d.write_bytes(b"pub_8d")
    job_pub_8d = _publish_job(session, fanpage, status=PublishJobStatus.published, published_at=_old(now, 8), video_path=f_pub_8d, thumbnail_path=None)
    v_pub_8d = _video_job(session, fanpage, status="done", finished_at=None, video_path=f_pub_8d, thumbnail_path=None, publish_job_id=job_pub_8d.id)

    # published 3 days ago -> kept
    f_pub_3d = ai_dir / "pub_3d.mp4"
    f_pub_3d.write_bytes(b"pub_3d")
    job_pub_3d = _publish_job(session, fanpage, status=PublishJobStatus.published, published_at=_old(now, 3), video_path=f_pub_3d, thumbnail_path=None)
    v_pub_3d = _video_job(session, fanpage, status="done", finished_at=None, video_path=f_pub_3d, thumbnail_path=None, publish_job_id=job_pub_3d.id)

    # failed updated 8 days ago -> kept
    f_fail_8d = ai_dir / "fail_8d.mp4"
    f_fail_8d.write_bytes(b"fail_8d")
    job_fail_8d = _publish_job(session, fanpage, status=PublishJobStatus.failed, published_at=None, video_path=f_fail_8d, thumbnail_path=None, updated_at=_old(now, 8))
    v_fail_8d = _video_job(session, fanpage, status="done", finished_at=None, video_path=f_fail_8d, thumbnail_path=None, publish_job_id=job_fail_8d.id)

    # failed updated 20 days ago -> deleted
    f_fail_20d = ai_dir / "fail_20d.mp4"
    f_fail_20d.write_bytes(b"fail_20d")
    job_fail_20d = _publish_job(session, fanpage, status=PublishJobStatus.failed, published_at=None, video_path=f_fail_20d, thumbnail_path=None, updated_at=_old(now, 20))
    v_fail_20d = _video_job(session, fanpage, status="done", finished_at=None, video_path=f_fail_20d, thumbnail_path=None, publish_job_id=job_fail_20d.id)

    session.commit()

    _run_cleanup(monkeypatch, root)

    verify = SessionLocal()
    # published 8 days ago -> files deleted
    assert not f_pub_8d.exists()
    assert verify.get(PublishJob, job_pub_8d.id).video_path is None
    assert verify.get(VideoJob, v_pub_8d.id).video_path is None

    # published 3 days ago -> kept
    assert f_pub_3d.exists()
    assert verify.get(PublishJob, job_pub_3d.id).video_path == str(f_pub_3d)
    assert verify.get(VideoJob, v_pub_3d.id).video_path == str(f_pub_3d)

    # failed updated 8 days ago -> kept
    assert f_fail_8d.exists()
    assert verify.get(PublishJob, job_fail_8d.id).video_path == str(f_fail_8d)
    assert verify.get(VideoJob, v_fail_8d.id).video_path == str(f_fail_8d)

    # failed updated 20 days ago -> kept (failed/skipped jobs never deleted)
    assert f_fail_20d.exists()
    assert verify.get(PublishJob, job_fail_20d.id).video_path == str(f_fail_20d)
    assert verify.get(VideoJob, v_fail_20d.id).video_path == str(f_fail_20d)

    verify.close()


def test_failed_video_job_without_publish_job_is_kept(db, tmp_path, monkeypatch):
    session, fanpage_ids = db
    root = tmp_path / "media"
    ai_dir = root / "ai_videos"
    ai_dir.mkdir(parents=True)
    video = ai_dir / "failed.mp4"
    thumb = ai_dir / "failed.jpg"
    video.write_bytes(b"video")
    thumb.write_bytes(b"thumb")
    fanpage = _fanpage(session, fanpage_ids)
    job = _video_job(session, fanpage, status="failed", finished_at=_old(datetime.now()), video_path=video, thumbnail_path=thumb)
    session.commit()
    job_id = job.id

    _run_cleanup(monkeypatch, root)

    verify = SessionLocal()
    stored = verify.get(VideoJob, job_id)
    assert video.exists() and thumb.exists()
    assert stored.video_path == str(video) and stored.thumbnail_path == str(thumb)
    verify.close()


def test_outside_path_is_not_deleted_and_logs_warning(db, tmp_path, monkeypatch, caplog):
    session, fanpage_ids = db
    root = tmp_path / "media"
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"do not delete")
    fanpage = _fanpage(session, fanpage_ids)
    job = _publish_job(session, fanpage, status=PublishJobStatus.published, published_at=_old(datetime.now()), video_path=outside, thumbnail_path=None)
    session.commit()
    job_id = job.id

    with caplog.at_level("WARNING"):
        _run_cleanup(monkeypatch, root)

    verify = SessionLocal()
    assert outside.exists()
    assert verify.get(PublishJob, job_id).video_path == str(outside)
    assert "outside" in caplog.text
    verify.close()


def test_missing_files_are_cleared_without_crashing(db, tmp_path, monkeypatch):
    session, fanpage_ids = db
    root = tmp_path / "media"
    (root / "ai_videos").mkdir(parents=True)
    video = root / "ai_videos" / "missing.mp4"
    fanpage = _fanpage(session, fanpage_ids)
    job = _publish_job(session, fanpage, status=PublishJobStatus.published, published_at=_old(datetime.now(), 15), video_path=video, thumbnail_path=None)
    session.commit()
    job_id = job.id

    _run_cleanup(monkeypatch, root)

    verify = SessionLocal()
    assert verify.get(PublishJob, job_id).video_path is None
    verify.close()


def test_beat_schedule_contains_ai_video_cleanup():
    assert celery_app.celery_app.conf.beat_schedule["cleanup-old-ai-videos"]["task"] == "app.tasks.cleanup.cleanup_old_ai_videos"
