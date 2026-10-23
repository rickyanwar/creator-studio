import uuid
from datetime import datetime, timedelta

import pytest

from app.database import Base, SessionLocal, engine
import app.models  # noqa: F401 - register all model tables with Base.metadata
from app.models.ig_sources import IGSource
from app.models.posts import MediaType, Post, PostStatus
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.target_fanpages import TargetFanpage
from app.tasks.cleanup import cleanup_old_media


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)


def test_cleanup_old_media_uses_naive_utc_datetimes(tmp_path, monkeypatch):
    now = datetime.utcnow()
    db = SessionLocal()
    source = IGSource(ig_username=f"cleanup-{uuid.uuid4().hex}")
    fanpage = TargetFanpage(
        repliz_account_id=uuid.uuid4().hex,
        name="Cleanup test page",
        timezone="UTC",
    )
    db.add_all([source, fanpage])
    db.flush()

    due_post = Post(
        uuid=uuid.uuid4(),
        ig_source_id=source.id,
        ig_media_id=f"due-{uuid.uuid4().hex}",
        media_type=MediaType.image,
        image_local_paths=["due.jpg"],
        status=PostStatus.done,
        crawled_at=now,
    )
    future_post = Post(
        uuid=uuid.uuid4(),
        ig_source_id=source.id,
        ig_media_id=f"future-{uuid.uuid4().hex}",
        media_type=MediaType.image,
        image_local_paths=["future.jpg"],
        status=PostStatus.done,
        crawled_at=now,
    )
    stuck_post = Post(
        uuid=uuid.uuid4(),
        ig_source_id=source.id,
        ig_media_id=f"stuck-{uuid.uuid4().hex}",
        media_type=MediaType.image,
        image_local_paths=["stuck.jpg"],
        status=PostStatus.stored,
        crawled_at=now - timedelta(hours=13),
    )
    db.add_all([due_post, future_post, stuck_post])
    db.flush()
    db.add_all([
        PublishJob(
            post_id=due_post.id,
            fanpage_id=fanpage.id,
            status=PublishJobStatus.published,
            cleanup_at=now - timedelta(minutes=1),
        ),
        PublishJob(
            post_id=future_post.id,
            fanpage_id=fanpage.id,
            status=PublishJobStatus.published,
            cleanup_at=now + timedelta(hours=1),
        ),
    ])
    db.commit()

    for post in (due_post, future_post, stuck_post):
        (tmp_path / "posts" / str(post.uuid)).mkdir(parents=True)
        (tmp_path / "posts" / str(post.uuid) / "media.jpg").touch()

    monkeypatch.setattr("app.tasks.cleanup.settings.storage_base_path", str(tmp_path))

    try:
        cleanup_old_media()
        db.expire_all()

        assert db.get(Post, due_post.id).status == PostStatus.cleaned
        assert db.get(Post, stuck_post.id).status == PostStatus.stored
        assert db.get(Post, future_post.id).status == PostStatus.done
        assert not (tmp_path / "posts" / str(due_post.uuid)).exists()
        assert (tmp_path / "posts" / str(stuck_post.uuid)).exists()
        assert (tmp_path / "posts" / str(future_post.uuid)).exists()
    finally:
        db.rollback()
        db.query(PublishJob).filter(PublishJob.post_id.in_([due_post.id, future_post.id])).delete(
            synchronize_session=False
        )
        db.query(Post).filter(Post.id.in_([due_post.id, future_post.id, stuck_post.id])).delete(
            synchronize_session=False
        )
        db.delete(fanpage)
        db.delete(source)
        db.commit()
        db.close()
