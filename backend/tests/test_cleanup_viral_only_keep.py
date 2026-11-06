import uuid
from datetime import datetime, timedelta

import pytest

from app.database import Base, SessionLocal, engine
import app.models  # noqa: F401 - register all model tables
from app.models.fanpage_sources import FanpageSource
from app.models.ig_sources import IGSource
from app.models.posts import MediaType, Post, PostStatus
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.target_fanpages import TargetFanpage
from app.tasks.cleanup import cleanup_old_media


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)


@pytest.mark.parametrize("age, has_job, radar_enabled, kept", [
    (2, False, True, True),
    (60, False, True, False),
    (2, True, True, False),
    (2, False, False, False),
])
def test_cleanup_waits_for_viral_only_radar(tmp_path, monkeypatch, age, has_job, radar_enabled, kept):
    now = datetime.utcnow()
    db = SessionLocal()
    source = IGSource(ig_username=f"cleanup-{uuid.uuid4().hex}")
    published_page = TargetFanpage(repliz_account_id=uuid.uuid4().hex, name="Published Page")
    radar_page = TargetFanpage(repliz_account_id=uuid.uuid4().hex, name="Radar Page",
                              is_active=True, radar_enabled=radar_enabled)
    db.add_all([source, published_page, radar_page])
    db.flush()
    link = FanpageSource(ig_source_id=source.id, fanpage_id=radar_page.id,
                         trigger="viral_only", is_active=True)
    # Production topology: the same source is every_post for the page that already published it.
    every_post_link = FanpageSource(ig_source_id=source.id, fanpage_id=published_page.id,
                                    trigger="every_post", is_active=True)
    post = Post(uuid=uuid.uuid4(), ig_source_id=source.id, ig_media_id=uuid.uuid4().hex,
                media_type=MediaType.image, image_local_paths=["0.jpg"],
                image_public_urls=["https://storage/0.jpg"], status=PostStatus.done,
                crawled_at=now - timedelta(hours=age))
    db.add_all([link, every_post_link, post])
    db.flush()
    jobs = [PublishJob(post_id=post.id, fanpage_id=published_page.id,
                       status=PublishJobStatus.published, cleanup_at=now - timedelta(hours=1))]
    if has_job:
        jobs.append(PublishJob(post_id=post.id, fanpage_id=radar_page.id,
                               status=PublishJobStatus.published, cleanup_at=now - timedelta(hours=1)))
    db.add_all(jobs)
    db.commit()
    post_dir = tmp_path / "posts" / str(post.uuid)
    post_dir.mkdir(parents=True)
    (post_dir / "0.jpg").touch()
    monkeypatch.setattr("app.tasks.cleanup.settings.storage_base_path", str(tmp_path))

    try:
        cleanup_old_media()
        db.expire_all()
        refreshed = db.get(Post, post.id)
        assert refreshed.status == (PostStatus.done if kept else PostStatus.cleaned)
        assert bool(refreshed.image_local_paths) == kept
        assert bool(refreshed.image_public_urls) == kept
        assert post_dir.exists() == kept
    finally:
        db.rollback()
        db.query(PublishJob).filter_by(post_id=post.id).delete()
        db.query(Post).filter_by(id=post.id).delete()
        db.query(FanpageSource).filter(FanpageSource.id.in_([link.id, every_post_link.id])).delete(synchronize_session=False)
        db.query(TargetFanpage).filter(TargetFanpage.id.in_([published_page.id, radar_page.id])).delete(synchronize_session=False)
        db.query(IGSource).filter_by(id=source.id).delete()
        db.commit()
        db.close()
