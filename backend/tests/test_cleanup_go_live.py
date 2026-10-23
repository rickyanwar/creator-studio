import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://postgres:admin@localhost:5432/test_cleanup_db"

import pytest

from app.database import Base, SessionLocal, engine
import app.models  # noqa: F401
from app.models.ig_sources import IGSource
from app.models.posts import MediaType, Post, PostStatus
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import SocialDestination, PublishDelivery
from app.models.target_fanpages import TargetFanpage
from app.models.ai_video import VideoJob
from app.tasks import cleanup
from app.tasks.cleanup import (
    _job_go_live_pending,
    cleanup_old_designs,
    cleanup_old_ai_videos,
)


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)


def test_cleanup_logs_kept_not_live(caplog):
    import logging
    with caplog.at_level(logging.INFO):
        cleanup_old_designs()
        cleanup_old_ai_videos()
    assert any("Design cleanup: kept" in r.message and "not live yet" in r.message for r in caplog.records)
    assert any("AI video cleanup: kept" in r.message and "not live yet" in r.message for r in caplog.records)


def test_job_go_live_pending_filters():
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    fanpage = TargetFanpage(
        repliz_account_id=uuid.uuid4().hex,
        name="Pending Helper Test Page",
        timezone="UTC",
    )
    db.add(fanpage)
    db.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Pending Test Dest",
        mode="repliz",
        supported_media=["image"],
    )
    db.add(dest)
    db.flush()

    jobs = [
        PublishJob(fanpage_id=fanpage.id, status=PublishJobStatus.published)
        for _ in range(4)
    ]
    db.add_all(jobs)
    db.flush()

    # Job 0: repliz_scheduled in future -> pending
    d0 = PublishDelivery(
        publish_job_id=jobs[0].id,
        destination_id=dest.id,
        state="repliz_scheduled",
        scheduled_for=now.replace(tzinfo=None) + timedelta(days=1),
    )
    # Job 1: waiting_for_slot with None scheduled_for -> pending
    d1 = PublishDelivery(
        publish_job_id=jobs[1].id,
        destination_id=dest.id,
        state="waiting_for_slot",
        scheduled_for=None,
    )
    # Job 2: scheduled 10 hours ago -> expired (cutoff is 6h)
    d2 = PublishDelivery(
        publish_job_id=jobs[2].id,
        destination_id=dest.id,
        state="repliz_scheduled",
        scheduled_for=now.replace(tzinfo=None) - timedelta(hours=10),
    )
    # Job 3: published state -> not in _LIVE_DELIVERY_STATES
    d3 = PublishDelivery(
        publish_job_id=jobs[3].id,
        destination_id=dest.id,
        state="published",
        scheduled_for=now.replace(tzinfo=None) + timedelta(days=1),
    )
    db.add_all([d0, d1, d2, d3])
    db.commit()

    try:
        pending_ids = _job_go_live_pending(db, [j.id for j in jobs], now)
        assert jobs[0].id in pending_ids
        assert jobs[1].id in pending_ids
        assert jobs[2].id not in pending_ids
        assert jobs[3].id not in pending_ids
    finally:
        db.query(PublishDelivery).filter(PublishDelivery.publish_job_id.in_([j.id for j in jobs])).delete(synchronize_session=False)
        db.query(PublishJob).filter(PublishJob.id.in_([j.id for j in jobs])).delete(synchronize_session=False)
        db.query(SocialDestination).filter_by(id=dest.id).delete(synchronize_session=False)
        db.query(TargetFanpage).filter_by(id=fanpage.id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_cleanup_old_designs_go_live(tmp_path):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = SessionLocal()

    source = IGSource(ig_username=f"cleanup-{uuid.uuid4().hex}")
    fanpage = TargetFanpage(
        repliz_account_id=uuid.uuid4().hex,
        name="Design Go-Live Page",
        timezone="UTC",
    )
    db.add_all([source, fanpage])
    db.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Design Dest",
        mode="repliz",
        supported_media=["image"],
    )
    db.add(dest)
    db.flush()

    posts = [
        Post(
            uuid=uuid.uuid4(),
            ig_source_id=source.id,
            ig_media_id=f"design-{uuid.uuid4().hex}",
            media_type=MediaType.image,
            image_local_paths=["test.jpg"],
            status=PostStatus.done,
            crawled_at=now,
        )
        for _ in range(5)
    ]
    db.add_all(posts)
    db.flush()

    f_future = tmp_path / "future_sched.png"
    f_repliz = tmp_path / "repliz_sched.png"
    f_live_9d = tmp_path / "went_live_9d.png"
    f_failed_old = tmp_path / "failed_old.png"
    f_live_3d = tmp_path / "went_live_3d.png"

    for f in (f_future, f_repliz, f_live_9d, f_failed_old, f_live_3d):
        f.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    # 1. published 10 days ago but scheduled_for 2 days in the future -> kept
    job_future = PublishJob(
        post_id=posts[0].id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.published,
        published_at=now - timedelta(days=10),
        scheduled_for=now + timedelta(days=2),
        design_image_path=str(f_future),
        design_image_url="https://example.com/future.png",
    )

    # 2. published 10 days ago with a repliz_scheduled delivery scheduled tomorrow -> kept
    job_repliz = PublishJob(
        post_id=posts[1].id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.published,
        published_at=now - timedelta(days=10),
        design_image_path=str(f_repliz),
        design_image_url="https://example.com/repliz.png",
    )

    # 3. published 10 days ago, went live 9 days ago -> deleted (old behaviour)
    job_live_9d = PublishJob(
        post_id=posts[2].id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.published,
        published_at=now - timedelta(days=10),
        scheduled_for=now - timedelta(days=9),
        design_image_path=str(f_live_9d),
        design_image_url="https://example.com/live_9d.png",
    )

    # 4. failed job older than retention -> deleted as before
    job_failed = PublishJob(
        post_id=posts[3].id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.failed,
        published_at=None,
        updated_at=now - timedelta(days=30),
        design_image_path=str(f_failed_old),
        design_image_url="https://example.com/failed.png",
    )

    # 5. published 10 days ago, went live 3 days ago -> kept (retention from go-live < 7 days)
    job_live_3d = PublishJob(
        post_id=posts[4].id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.published,
        published_at=now - timedelta(days=10),
        design_image_path=str(f_live_3d),
        design_image_url="https://example.com/live_3d.png",
    )

    db.add_all([job_future, job_repliz, job_live_9d, job_failed, job_live_3d])
    db.flush()

    db.query(PublishJob).filter(PublishJob.id == job_failed.id).update(
        {"updated_at": now - timedelta(days=30)}, synchronize_session=False
    )

    # Delivery for job_repliz scheduled tomorrow
    deliv_repliz = PublishDelivery(
        publish_job_id=job_repliz.id,
        destination_id=dest.id,
        state="repliz_scheduled",
        scheduled_for=now + timedelta(days=1),
    )
    # Delivery for job_live_9d went live 9 days ago
    deliv_live_9d = PublishDelivery(
        publish_job_id=job_live_9d.id,
        destination_id=dest.id,
        state="published",
        scheduled_for=now - timedelta(days=9),
        published_at=now - timedelta(days=9),
    )
    # Delivery for job_live_3d went live 3 days ago
    deliv_live_3d = PublishDelivery(
        publish_job_id=job_live_3d.id,
        destination_id=dest.id,
        state="published",
        scheduled_for=now - timedelta(days=3),
        published_at=now - timedelta(days=3),
    )
    db.add_all([deliv_repliz, deliv_live_9d, deliv_live_3d])
    db.commit()

    all_jobs = [job_future, job_repliz, job_live_9d, job_failed, job_live_3d]
    try:
        cleanup_old_designs()
        db.expire_all()

        # job_future: kept
        j_fut = db.get(PublishJob, job_future.id)
        assert f_future.exists()
        assert j_fut.design_image_path == str(f_future)

        # job_repliz: kept
        j_rep = db.get(PublishJob, job_repliz.id)
        assert f_repliz.exists()
        assert j_rep.design_image_path == str(f_repliz)

        # job_live_9d: deleted
        j_9d = db.get(PublishJob, job_live_9d.id)
        assert not f_live_9d.exists()
        assert j_9d.design_image_path is None
        assert j_9d.design_image_url is None

        # job_failed: kept (failed/skipped jobs never deleted automatically)
        j_fail = db.get(PublishJob, job_failed.id)
        assert f_failed_old.exists()
        assert j_fail.design_image_path == str(f_failed_old)

        # job_live_3d: kept
        j_3d = db.get(PublishJob, job_live_3d.id)
        assert f_live_3d.exists()
        assert j_3d.design_image_path == str(f_live_3d)

    finally:
        db.query(PublishDelivery).filter(PublishDelivery.publish_job_id.in_([j.id for j in all_jobs])).delete(synchronize_session=False)
        db.query(PublishJob).filter(PublishJob.id.in_([j.id for j in all_jobs])).delete(synchronize_session=False)
        db.query(Post).filter(Post.id.in_([p.id for p in posts])).delete(synchronize_session=False)
        db.query(SocialDestination).filter_by(id=dest.id).delete(synchronize_session=False)
        db.query(TargetFanpage).filter_by(id=fanpage.id).delete(synchronize_session=False)
        db.query(IGSource).filter_by(id=source.id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_cleanup_old_ai_videos_go_live(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = SessionLocal()

    media_root = tmp_path / "media"
    ai_dir = media_root / "ai_videos"
    ai_dir.mkdir(parents=True)
    monkeypatch.setattr(cleanup.settings, "storage_base_path", str(media_root))

    fanpage = TargetFanpage(
        repliz_account_id=uuid.uuid4().hex,
        name="AI Video Go-Live Page",
        timezone="UTC",
    )
    db.add(fanpage)
    db.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="AI Video Dest",
        mode="repliz",
        supported_media=["video"],
    )
    db.add(dest)
    db.flush()

    v_future = ai_dir / "vid_future.mp4"
    t_future = ai_dir / "vid_future.jpg"
    v_repliz = ai_dir / "vid_repliz.mp4"
    t_repliz = ai_dir / "vid_repliz.jpg"
    v_live_9d = ai_dir / "vid_live_9d.mp4"
    t_live_9d = ai_dir / "vid_live_9d.jpg"
    v_failed = ai_dir / "vid_failed.mp4"
    t_failed = ai_dir / "vid_failed.jpg"

    for f in (v_future, t_future, v_repliz, t_repliz, v_live_9d, t_live_9d, v_failed, t_failed):
        f.write_bytes(b"dummy_video_bytes")

    # 1. published 10 days ago but scheduled_for 2 days in the future -> kept
    pub_future = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.published,
        published_at=now - timedelta(days=10),
        scheduled_for=now + timedelta(days=2),
        video_path=str(v_future),
        video_url="https://example.com/v_future.mp4",
        video_thumbnail_path=str(t_future),
        video_thumbnail_url="https://example.com/t_future.jpg",
    )

    # 2. published 10 days ago with a repliz_scheduled delivery scheduled tomorrow -> kept
    pub_repliz = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.published,
        published_at=now - timedelta(days=10),
        video_path=str(v_repliz),
        video_url="https://example.com/v_repliz.mp4",
        video_thumbnail_path=str(t_repliz),
        video_thumbnail_url="https://example.com/t_repliz.jpg",
    )

    # 3. published 10 days ago, went live 9 days ago -> deleted
    pub_live_9d = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.published,
        published_at=now - timedelta(days=10),
        scheduled_for=now - timedelta(days=9),
        video_path=str(v_live_9d),
        video_url="https://example.com/v_9d.mp4",
        video_thumbnail_path=str(t_live_9d),
        video_thumbnail_url="https://example.com/t_9d.jpg",
    )

    # 4. failed job older than retention (14 days) -> deleted
    pub_failed = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.failed,
        published_at=None,
        updated_at=now - timedelta(days=20),
        video_path=str(v_failed),
        video_url="https://example.com/v_failed.mp4",
        video_thumbnail_path=str(t_failed),
        video_thumbnail_url="https://example.com/t_failed.jpg",
    )

    db.add_all([pub_future, pub_repliz, pub_live_9d, pub_failed])
    db.flush()

    db.query(PublishJob).filter(PublishJob.id == pub_failed.id).update(
        {"updated_at": now - timedelta(days=20)}, synchronize_session=False
    )

    deliv_repliz = PublishDelivery(
        publish_job_id=pub_repliz.id,
        destination_id=dest.id,
        state="repliz_scheduled",
        scheduled_for=now + timedelta(days=1),
    )
    deliv_live_9d = PublishDelivery(
        publish_job_id=pub_live_9d.id,
        destination_id=dest.id,
        state="published",
        scheduled_for=now - timedelta(days=9),
        published_at=now - timedelta(days=9),
    )
    db.add_all([deliv_repliz, deliv_live_9d])
    db.commit()

    all_jobs = [pub_future, pub_repliz, pub_live_9d, pub_failed]
    try:
        cleanup_old_ai_videos()
        db.expire_all()

        # pub_future: kept
        j_fut = db.get(PublishJob, pub_future.id)
        assert v_future.exists()
        assert t_future.exists()
        assert j_fut.video_path == str(v_future)

        # pub_repliz: kept
        j_rep = db.get(PublishJob, pub_repliz.id)
        assert v_repliz.exists()
        assert t_repliz.exists()
        assert j_rep.video_path == str(v_repliz)

        # pub_live_9d: deleted
        j_9d = db.get(PublishJob, pub_live_9d.id)
        assert not v_live_9d.exists()
        assert not t_live_9d.exists()
        assert j_9d.video_path is None
        assert j_9d.video_thumbnail_path is None

        # pub_failed: kept (failed/skipped jobs never deleted automatically)
        j_fail = db.get(PublishJob, pub_failed.id)
        assert v_failed.exists()
        assert t_failed.exists()
        assert j_fail.video_path == str(v_failed)
        assert j_fail.video_thumbnail_path == str(t_failed)

    finally:
        db.query(PublishDelivery).filter(PublishDelivery.publish_job_id.in_([j.id for j in all_jobs])).delete(synchronize_session=False)
        db.query(PublishJob).filter(PublishJob.id.in_([j.id for j in all_jobs])).delete(synchronize_session=False)
        db.query(SocialDestination).filter_by(id=dest.id).delete(synchronize_session=False)
        db.query(TargetFanpage).filter_by(id=fanpage.id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_skipped_job_older_than_30d_keeps_design_and_video(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = SessionLocal()

    media_root = tmp_path / "media"
    ai_dir = media_root / "ai_videos"
    ai_dir.mkdir(parents=True)
    monkeypatch.setattr(cleanup.settings, "storage_base_path", str(media_root))

    source = IGSource(ig_username=f"cleanup-{uuid.uuid4().hex}")
    fanpage = TargetFanpage(
        repliz_account_id=uuid.uuid4().hex,
        name="Skipped Job Test Page",
        timezone="UTC",
    )
    db.add_all([source, fanpage])
    db.flush()

    post = Post(
        uuid=uuid.uuid4(),
        ig_source_id=source.id,
        ig_media_id=f"skipped-{uuid.uuid4().hex}",
        media_type=MediaType.image,
        image_local_paths=["test.jpg"],
        status=PostStatus.done,
        crawled_at=now,
    )
    db.add(post)
    db.flush()

    f_design = tmp_path / "skipped_35d.png"
    f_design.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    job_design = PublishJob(
        post_id=post.id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.skipped,
        published_at=None,
        updated_at=now - timedelta(days=35),
        design_image_path=str(f_design),
        design_image_url="https://example.com/skipped_35d.png",
    )

    v_video = ai_dir / "skipped_35d.mp4"
    t_video = ai_dir / "skipped_35d.jpg"
    v_video.write_bytes(b"skipped_video_content")
    t_video.write_bytes(b"skipped_thumb_content")
    job_video = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.skipped,
        published_at=None,
        updated_at=now - timedelta(days=35),
        video_path=str(v_video),
        video_url="https://example.com/skipped_35d.mp4",
        video_thumbnail_path=str(t_video),
        video_thumbnail_url="https://example.com/skipped_35d.jpg",
    )
    db.add_all([job_design, job_video])
    db.flush()

    db.query(PublishJob).filter(PublishJob.id.in_([job_design.id, job_video.id])).update(
        {"updated_at": now - timedelta(days=35)}, synchronize_session=False
    )
    db.commit()

    try:
        cleanup_old_designs()
        cleanup_old_ai_videos()
        db.expire_all()

        assert f_design.exists(), "Skipped job design file must be kept"
        assert db.get(PublishJob, job_design.id).design_image_path == str(f_design)

        assert v_video.exists(), "Skipped job video file must be kept"
        assert t_video.exists(), "Skipped job video thumbnail must be kept"
        assert db.get(PublishJob, job_video.id).video_path == str(v_video)
        assert db.get(PublishJob, job_video.id).video_thumbnail_path == str(t_video)
    finally:
        db.query(PublishJob).filter(PublishJob.id.in_([job_design.id, job_video.id])).delete(synchronize_session=False)
        db.query(Post).filter_by(id=post.id).delete(synchronize_session=False)
        db.query(TargetFanpage).filter_by(id=fanpage.id).delete(synchronize_session=False)
        db.query(IGSource).filter_by(id=source.id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_stuck_post_older_than_12h_keeps_images(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = SessionLocal()

    source = IGSource(ig_username=f"cleanup-{uuid.uuid4().hex}")
    fanpage = TargetFanpage(
        repliz_account_id=uuid.uuid4().hex,
        name="Stuck Post Test Page",
        timezone="UTC",
    )
    db.add_all([source, fanpage])
    db.flush()

    post = Post(
        uuid=uuid.uuid4(),
        ig_source_id=source.id,
        ig_media_id=f"stuck-15h-{uuid.uuid4().hex}",
        media_type=MediaType.image,
        image_local_paths=["img1.jpg"],
        status=PostStatus.stored,
        crawled_at=now - timedelta(hours=15),
    )
    db.add(post)
    db.flush()

    post_dir = tmp_path / "posts" / str(post.uuid)
    post_dir.mkdir(parents=True)
    (post_dir / "img1.jpg").write_bytes(b"post_image_data")

    monkeypatch.setattr("app.tasks.cleanup.settings.storage_base_path", str(tmp_path))

    try:
        cleanup.cleanup_old_media()
        db.expire_all()

        refreshed = db.get(Post, post.id)
        assert refreshed.status == PostStatus.stored
        assert (post_dir / "img1.jpg").exists(), "Images of stuck post older than 12h must be kept"
    finally:
        db.query(Post).filter_by(id=post.id).delete(synchronize_session=False)
        db.query(TargetFanpage).filter_by(id=fanpage.id).delete(synchronize_session=False)
        db.query(IGSource).filter_by(id=source.id).delete(synchronize_session=False)
        db.commit()
        db.close()
