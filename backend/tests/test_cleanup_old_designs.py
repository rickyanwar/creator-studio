import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.database import Base, SessionLocal, engine
import app.models  # noqa: F401 - register all model tables with Base.metadata
from app.models.ig_sources import IGSource
from app.models.posts import MediaType, Post, PostStatus
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.target_fanpages import TargetFanpage
from app.tasks.cleanup import cleanup_old_designs


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)


def test_cleanup_old_designs(tmp_path):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = SessionLocal()

    source = IGSource(ig_username=f"cleanup-{uuid.uuid4().hex}")
    fanpage = TargetFanpage(
        repliz_account_id=uuid.uuid4().hex,
        name="Cleanup test page",
        timezone="UTC",
    )
    db.add_all([source, fanpage])
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
        for _ in range(6)
    ]
    db.add_all(posts)
    db.flush()

    f_pub_8d = tmp_path / "pub_8d.png"
    f_pub_3d = tmp_path / "pub_3d.png"
    f_pub_2h = tmp_path / "pub_2h.png"
    f_fail_2d = tmp_path / "fail_2d.png"
    f_fail_30d = tmp_path / "fail_30d.png"
    f_pub_none_30d = tmp_path / "pub_none_30d.png"

    for f in (f_pub_8d, f_pub_3d, f_pub_2h, f_fail_2d, f_fail_30d, f_pub_none_30d):
        f.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    job_pub_8d = PublishJob(
        post_id=posts[0].id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.published,
        published_at=now - timedelta(days=8),
        design_image_path=str(f_pub_8d),
        design_image_url="https://example.com/pub_8d.png",
    )
    job_pub_3d = PublishJob(
        post_id=posts[1].id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.published,
        published_at=now - timedelta(days=3),
        design_image_path=str(f_pub_3d),
        design_image_url="https://example.com/pub_3d.png",
    )
    job_pub_2h = PublishJob(
        post_id=posts[2].id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.published,
        published_at=now - timedelta(hours=2),
        design_image_path=str(f_pub_2h),
        design_image_url="https://example.com/pub_2h.png",
    )
    job_fail_2d = PublishJob(
        post_id=posts[3].id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.failed,
        published_at=None,
        updated_at=now - timedelta(days=2),
        design_image_path=str(f_fail_2d),
        design_image_url="https://example.com/fail_2d.png",
    )
    job_fail_30d = PublishJob(
        post_id=posts[4].id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.failed,
        published_at=None,
        updated_at=now - timedelta(days=30),
        design_image_path=str(f_fail_30d),
        design_image_url="https://example.com/fail_30d.png",
    )
    job_pub_none_30d = PublishJob(
        post_id=posts[5].id,
        fanpage_id=fanpage.id,
        status=PublishJobStatus.published,
        published_at=None,
        updated_at=now - timedelta(days=30),
        design_image_path=str(f_pub_none_30d),
        design_image_url="https://example.com/pub_none_30d.png",
    )

    all_jobs = [job_pub_8d, job_pub_3d, job_pub_2h, job_fail_2d, job_fail_30d, job_pub_none_30d]
    db.add_all(all_jobs)
    db.flush()

    db.query(PublishJob).filter(PublishJob.id == job_fail_2d.id).update(
        {"updated_at": now - timedelta(days=2)}, synchronize_session=False
    )
    db.query(PublishJob).filter(PublishJob.id == job_fail_30d.id).update(
        {"updated_at": now - timedelta(days=30)}, synchronize_session=False
    )
    db.query(PublishJob).filter(PublishJob.id == job_pub_none_30d.id).update(
        {"updated_at": now - timedelta(days=30)}, synchronize_session=False
    )
    db.commit()

    try:
        cleanup_old_designs()
        db.expire_all()

        j1 = db.get(PublishJob, job_pub_8d.id)
        assert not f_pub_8d.exists()
        assert j1.design_image_path is None
        assert j1.design_image_url is None

        j_pub_3d = db.get(PublishJob, job_pub_3d.id)
        assert f_pub_3d.exists()
        assert j_pub_3d.design_image_path == str(f_pub_3d)
        assert j_pub_3d.design_image_url == "https://example.com/pub_3d.png"

        j2 = db.get(PublishJob, job_pub_2h.id)
        assert f_pub_2h.exists()
        assert j2.design_image_path == str(f_pub_2h)
        assert j2.design_image_url == "https://example.com/pub_2h.png"

        j3 = db.get(PublishJob, job_fail_2d.id)
        assert f_fail_2d.exists()
        assert j3.design_image_path == str(f_fail_2d)
        assert j3.design_image_url == "https://example.com/fail_2d.png"

        j4 = db.get(PublishJob, job_fail_30d.id)
        assert f_fail_30d.exists()
        assert j4.design_image_path == str(f_fail_30d)
        assert j4.design_image_url == "https://example.com/fail_30d.png"

        j5 = db.get(PublishJob, job_pub_none_30d.id)
        assert not f_pub_none_30d.exists()
        assert j5.design_image_path is None
        assert j5.design_image_url is None

    finally:
        db.rollback()
        job_ids = [j.id for j in all_jobs]
        post_ids = [p.id for p in posts]
        db.query(PublishJob).filter(PublishJob.id.in_(job_ids)).delete(synchronize_session=False)
        db.query(Post).filter(Post.id.in_(post_ids)).delete(synchronize_session=False)
        db.delete(fanpage)
        db.delete(source)
        db.commit()
        db.close()
