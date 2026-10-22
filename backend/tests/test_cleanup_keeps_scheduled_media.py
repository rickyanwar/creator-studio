import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Fallback to local postgres if DATABASE_URL is unset in host environment
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://postgres:admin@localhost:5432/test_cleanup_db"

import pytest

from app.database import Base, SessionLocal, engine
import app.models  # noqa: F401 - register all model tables with Base.metadata
from app.models.ig_sources import IGSource
from app.models.posts import MediaType, Post, PostStatus
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.social_publishing import SocialDestination, PublishDelivery
from app.models.target_fanpages import TargetFanpage
from app.tasks.cleanup import cleanup_old_media
from app.tasks.publisher import _keep_until


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)


def test_case_1_stuck_post_future_delivery_kept(tmp_path, monkeypatch):
    """Case 1: stuck post (status stored, crawled 20 h ago), its job has a delivery repliz_scheduled 5 h in future -> KEPT."""
    now = datetime.utcnow()
    db = SessionLocal()
    source = IGSource(ig_username=f"cleanup-{uuid.uuid4().hex}")
    fanpage = TargetFanpage(
        repliz_account_id=uuid.uuid4().hex,
        name="Case 1 Page",
        timezone="UTC",
    )
    db.add_all([source, fanpage])
    db.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Case 1 Dest",
        mode="repliz",
        supported_media=["image"],
    )
    db.add(dest)
    db.flush()

    post = Post(
        uuid=uuid.uuid4(),
        ig_source_id=source.id,
        ig_media_id=f"media-{uuid.uuid4().hex}",
        media_type=MediaType.image,
        image_local_paths=["0.jpg"],
        status=PostStatus.stored,
        crawled_at=now - timedelta(hours=20),
    )
    db.add(post)
    db.flush()

    job = PublishJob(
        post_id=post.id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.pending_publish,
        created_at=now - timedelta(hours=50),
    )
    db.add(job)
    db.flush()

    delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        state="repliz_scheduled",
        scheduled_for=now + timedelta(hours=5),
    )
    db.add(delivery)
    db.commit()

    post_dir = tmp_path / "posts" / str(post.uuid)
    post_dir.mkdir(parents=True)
    (post_dir / "0.jpg").touch()

    monkeypatch.setattr("app.tasks.cleanup.settings.storage_base_path", str(tmp_path))

    try:
        cleanup_old_media()
        db.expire_all()

        refreshed = db.get(Post, post.id)
        assert refreshed.status == PostStatus.stored
        assert post_dir.exists()
    finally:
        db.rollback()
        db.query(PublishDelivery).filter_by(publish_job_id=job.id).delete()
        db.query(PublishJob).filter_by(id=job.id).delete()
        db.query(Post).filter_by(id=post.id).delete()
        db.query(SocialDestination).filter_by(id=dest.id).delete()
        db.query(TargetFanpage).filter_by(id=fanpage.id).delete()
        db.query(IGSource).filter_by(id=source.id).delete()
        db.commit()
        db.close()


def test_case_2_stuck_post_past_delivery_cleaned(tmp_path, monkeypatch):
    """Case 2: stuck post, delivery scheduled 7 h in the PAST -> CLEANED."""
    now = datetime.utcnow()
    db = SessionLocal()
    source = IGSource(ig_username=f"cleanup-{uuid.uuid4().hex}")
    fanpage = TargetFanpage(
        repliz_account_id=uuid.uuid4().hex,
        name="Case 2 Page",
        timezone="UTC",
    )
    db.add_all([source, fanpage])
    db.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Case 2 Dest",
        mode="repliz",
        supported_media=["image"],
    )
    db.add(dest)
    db.flush()

    post = Post(
        uuid=uuid.uuid4(),
        ig_source_id=source.id,
        ig_media_id=f"media-{uuid.uuid4().hex}",
        media_type=MediaType.image,
        image_local_paths=["0.jpg"],
        status=PostStatus.stored,
        crawled_at=now - timedelta(hours=20),
    )
    db.add(post)
    db.flush()

    job = PublishJob(
        post_id=post.id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.pending_publish,
        created_at=now - timedelta(hours=50),
    )
    db.add(job)
    db.flush()

    delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        state="repliz_scheduled",
        scheduled_for=now - timedelta(hours=7),
    )
    db.add(delivery)
    db.commit()

    post_dir = tmp_path / "posts" / str(post.uuid)
    post_dir.mkdir(parents=True)
    (post_dir / "0.jpg").touch()

    monkeypatch.setattr("app.tasks.cleanup.settings.storage_base_path", str(tmp_path))

    try:
        cleanup_old_media()
        db.expire_all()

        refreshed = db.get(Post, post.id)
        assert refreshed.status == PostStatus.cleaned
        assert not post_dir.exists()
    finally:
        db.rollback()
        db.query(PublishDelivery).filter_by(publish_job_id=job.id).delete()
        db.query(PublishJob).filter_by(id=job.id).delete()
        db.query(Post).filter_by(id=post.id).delete()
        db.query(SocialDestination).filter_by(id=dest.id).delete()
        db.query(TargetFanpage).filter_by(id=fanpage.id).delete()
        db.query(IGSource).filter_by(id=source.id).delete()
        db.commit()
        db.close()


def test_case_3_done_post_future_scheduled_for_kept(tmp_path, monkeypatch):
    """Case 3: post done, job published with cleanup_at in past, but scheduled_for 2 h in future -> KEPT."""
    now = datetime.utcnow()
    db = SessionLocal()
    source = IGSource(ig_username=f"cleanup-{uuid.uuid4().hex}")
    fanpage = TargetFanpage(
        repliz_account_id=uuid.uuid4().hex,
        name="Case 3 Page",
        timezone="UTC",
    )
    db.add_all([source, fanpage])
    db.flush()

    post = Post(
        uuid=uuid.uuid4(),
        ig_source_id=source.id,
        ig_media_id=f"media-{uuid.uuid4().hex}",
        media_type=MediaType.image,
        image_local_paths=["0.jpg"],
        status=PostStatus.done,
        crawled_at=now - timedelta(hours=5),
    )
    db.add(post)
    db.flush()

    job = PublishJob(
        post_id=post.id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.published,
        cleanup_at=now - timedelta(hours=1),
        scheduled_for=now + timedelta(hours=2),
    )
    db.add(job)
    db.commit()

    post_dir = tmp_path / "posts" / str(post.uuid)
    post_dir.mkdir(parents=True)
    (post_dir / "0.jpg").touch()

    monkeypatch.setattr("app.tasks.cleanup.settings.storage_base_path", str(tmp_path))

    try:
        cleanup_old_media()
        db.expire_all()

        refreshed = db.get(Post, post.id)
        assert refreshed.status == PostStatus.done
        assert post_dir.exists()
    finally:
        db.rollback()
        db.query(PublishJob).filter_by(id=job.id).delete()
        db.query(Post).filter_by(id=post.id).delete()
        db.query(TargetFanpage).filter_by(id=fanpage.id).delete()
        db.query(IGSource).filter_by(id=source.id).delete()
        db.commit()
        db.close()


def test_case_4_stuck_post_pending_job_keep_hours(tmp_path, monkeypatch):
    """Case 4: stuck post with pending_publish job: 2 h ago -> KEPT; 50 h ago -> CLEANED."""
    now = datetime.utcnow()
    db = SessionLocal()
    source = IGSource(ig_username=f"cleanup-{uuid.uuid4().hex}")
    fanpage = TargetFanpage(
        repliz_account_id=uuid.uuid4().hex,
        name="Case 4 Page",
        timezone="UTC",
    )
    db.add_all([source, fanpage])
    db.flush()

    post_kept = Post(
        uuid=uuid.uuid4(),
        ig_source_id=source.id,
        ig_media_id=f"media-{uuid.uuid4().hex}",
        media_type=MediaType.image,
        image_local_paths=["0.jpg"],
        status=PostStatus.stored,
        crawled_at=now - timedelta(hours=20),
    )
    post_cleaned = Post(
        uuid=uuid.uuid4(),
        ig_source_id=source.id,
        ig_media_id=f"media-{uuid.uuid4().hex}",
        media_type=MediaType.image,
        image_local_paths=["0.jpg"],
        status=PostStatus.stored,
        crawled_at=now - timedelta(hours=20),
    )
    db.add_all([post_kept, post_cleaned])
    db.flush()

    job_recent = PublishJob(
        post_id=post_kept.id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.pending_publish,
        created_at=now - timedelta(hours=2),
    )
    job_old = PublishJob(
        post_id=post_cleaned.id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.pending_publish,
        created_at=now - timedelta(hours=50),
    )
    db.add_all([job_recent, job_old])
    db.commit()

    dir_kept = tmp_path / "posts" / str(post_kept.uuid)
    dir_kept.mkdir(parents=True)
    (dir_kept / "0.jpg").touch()

    dir_cleaned = tmp_path / "posts" / str(post_cleaned.uuid)
    dir_cleaned.mkdir(parents=True)
    (dir_cleaned / "0.jpg").touch()

    monkeypatch.setattr("app.tasks.cleanup.settings.storage_base_path", str(tmp_path))

    try:
        cleanup_old_media()
        db.expire_all()

        assert db.get(Post, post_kept.id).status == PostStatus.stored
        assert dir_kept.exists()

        assert db.get(Post, post_cleaned.id).status == PostStatus.cleaned
        assert not dir_cleaned.exists()
    finally:
        db.rollback()
        db.query(PublishJob).filter(PublishJob.id.in_([job_recent.id, job_old.id])).delete(synchronize_session=False)
        db.query(Post).filter(Post.id.in_([post_kept.id, post_cleaned.id])).delete(synchronize_session=False)
        db.query(TargetFanpage).filter_by(id=fanpage.id).delete()
        db.query(IGSource).filter_by(id=source.id).delete()
        db.commit()
        db.close()


def test_case_5_all_jobs_failed_cleaned(tmp_path, monkeypatch):
    """Case 5: all jobs failed, no deliveries, no scheduled_for -> CLEANED."""
    now = datetime.utcnow()
    db = SessionLocal()
    source = IGSource(ig_username=f"cleanup-{uuid.uuid4().hex}")
    fanpage = TargetFanpage(
        repliz_account_id=uuid.uuid4().hex,
        name="Case 5 Page",
        timezone="UTC",
    )
    db.add_all([source, fanpage])
    db.flush()

    post = Post(
        uuid=uuid.uuid4(),
        ig_source_id=source.id,
        ig_media_id=f"media-{uuid.uuid4().hex}",
        media_type=MediaType.image,
        image_local_paths=["0.jpg"],
        status=PostStatus.stored,
        crawled_at=now - timedelta(hours=2),
    )
    db.add(post)
    db.flush()

    job = PublishJob(
        post_id=post.id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.failed,
        scheduled_for=None,
    )
    db.add(job)
    db.commit()

    post_dir = tmp_path / "posts" / str(post.uuid)
    post_dir.mkdir(parents=True)
    (post_dir / "0.jpg").touch()

    monkeypatch.setattr("app.tasks.cleanup.settings.storage_base_path", str(tmp_path))

    try:
        cleanup_old_media()
        db.expire_all()

        refreshed = db.get(Post, post.id)
        assert refreshed.status == PostStatus.cleaned
        assert not post_dir.exists()
    finally:
        db.rollback()
        db.query(PublishJob).filter_by(id=job.id).delete()
        db.query(Post).filter_by(id=post.id).delete()
        db.query(TargetFanpage).filter_by(id=fanpage.id).delete()
        db.query(IGSource).filter_by(id=source.id).delete()
        db.commit()
        db.close()


def test_keep_until_scheduled_ahead():
    now = datetime(2026, 10, 15, 10, 0, 0)
    scheduled_for = now + timedelta(hours=30)
    res = _keep_until(scheduled_for, now)
    assert res == scheduled_for + timedelta(hours=6)
    assert res.tzinfo is None


def test_keep_until_scheduled_for_none():
    now = datetime(2026, 10, 15, 10, 0, 0)
    res = _keep_until(None, now)
    assert res == now + timedelta(hours=6)
    assert res.tzinfo is None


def test_keep_until_aware_scheduled_for():
    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    scheduled_for = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
    res = _keep_until(scheduled_for, now)
    assert res == datetime(2026, 10, 16, 18, 0, 0)
    assert res.tzinfo is None


def test_keep_until_scheduled_in_past():
    now = datetime(2026, 10, 15, 10, 0, 0)
    scheduled_for = now - timedelta(hours=5)
    res = _keep_until(scheduled_for, now)
    assert res == now + timedelta(hours=6)
    assert res.tzinfo is None
