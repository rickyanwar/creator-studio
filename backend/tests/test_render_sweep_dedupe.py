from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.design_templates import DesignTemplate
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ScrapedArticle
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.tasks.design_renderer import (
    _acquire_queued_lock,
    _release_queued_lock,
    render_design,
    render_discussion,
    render_facebook_photo,
    render_pending_designs,
)
from app.tasks.ig_recreate import render_ig_recreate


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


class FakeRedis:
    def __init__(self):
        self.data = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    def delete(self, *keys: str):
        count = 0
        for k in keys:
            if self.data.pop(k, None) is not None:
                count += 1
        return count

    def get(self, key: str):
        return self.data.get(key)


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    PublishJob.__table__.create(bind=engine)
    TargetFanpage.__table__.create(bind=engine)
    ScrapedArticle.__table__.create(bind=engine)
    DesignTemplate.__table__.create(bind=engine)
    from app.models.posts import Post
    Post.__table__.create(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_consecutive_sweeps_enqueue_each_job_once(db_session, fake_redis):
    """Two consecutive sweeps enqueue each job once (mock apply_async + a fake redis)."""
    fp = TargetFanpage(
        id=1,
        name="Motorsport Page",
        mode2_publish_mode=PublishMode.auto,
        discussion_publish_mode=PublishMode.auto,
        news_visual_engine="chatgpt",
    )
    news_job = PublishJob(
        id=101,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.news_content,
        design_title="Test News Title",
    )
    disc_job = PublishJob(
        id=202,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.discussion,
        design_title="Who is better?",
    )
    fb_job = PublishJob(
        id=303,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.facebook_recreate,
        design_title="Test FB Title",
    )
    ig_job = PublishJob(
        id=404,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.ig_recreate,
        design_title="Test IG Title",
        design_not_before=datetime(2020, 1, 1),
    )
    db_session.add_all([fp, news_job, disc_job, fb_job, ig_job])
    db_session.commit()

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db_session), \
         patch("app.tasks.design_renderer._redis", return_value=fake_redis), \
         patch("app.services.engine_router.render_queue_for", return_value="visual"), \
         patch("app.tasks.design_renderer.render_design.apply_async") as mock_news_async, \
         patch("app.tasks.design_renderer.render_discussion.delay") as mock_disc_delay, \
         patch("app.tasks.design_renderer.render_facebook_photo.apply_async") as mock_fb_async, \
         patch("app.tasks.ig_recreate.render_ig_recreate.apply_async") as mock_ig_async:

        # Sweep 1: enqueues all jobs and records Redis locks
        render_pending_designs()

        assert mock_news_async.call_count == 1
        mock_news_async.assert_called_once_with(args=[101], queue="visual", expires=1800)
        assert mock_disc_delay.call_count == 1
        mock_disc_delay.assert_called_once_with(202)
        assert mock_fb_async.call_count == 1
        mock_fb_async.assert_called_once_with(args=[303], queue="visual", expires=1800)
        assert mock_ig_async.call_count == 1
        mock_ig_async.assert_called_once_with((404,), queue="visual", expires=1800)

        assert fake_redis.get("render_design:queued:101") == "1"
        assert fake_redis.get("render_design:queued:202") == "1"
        assert fake_redis.get("render_design:queued:303") == "1"
        assert fake_redis.get("render_design:queued:404") == "1"

        # Sweep 2: jobs still pending_design, but locks exist -> skipped
        render_pending_designs()

        assert mock_news_async.call_count == 1
        assert mock_disc_delay.call_count == 1
        assert mock_fb_async.call_count == 1
        assert mock_ig_async.call_count == 1


def test_key_cleared_after_render_design_runs(db_session, fake_redis):
    """Key cleared in finally: after render_design finishes."""
    fp = TargetFanpage(
        id=1,
        name="Motorsport Page",
        mode2_publish_mode=PublishMode.auto,
    )
    job = PublishJob(
        id=303,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.news_content,
    )
    db_session.add_all([fp, job])
    db_session.commit()

    fake_redis.data["render_design:queued:303"] = "1"

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db_session), \
         patch("app.tasks.design_renderer._redis", return_value=fake_redis):
        # render_design executes and exits (article missing -> returns), finally block runs
        render_design(303)

    assert fake_redis.get("render_design:queued:303") is None


def test_key_cleared_after_render_discussion_runs(db_session, fake_redis):
    """Key cleared in finally: after render_discussion finishes."""
    fp = TargetFanpage(
        id=1,
        name="Motorsport Page",
        discussion_publish_mode=PublishMode.auto,
    )
    job = PublishJob(
        id=404,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.discussion,
    )
    db_session.add_all([fp, job])
    db_session.commit()

    fake_redis.data["render_design:queued:404"] = "1"

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db_session), \
         patch("app.tasks.design_renderer._redis", return_value=fake_redis):
        render_discussion(404)

    assert fake_redis.get("render_design:queued:404") is None


def test_key_cleared_after_render_facebook_photo_runs(db_session, fake_redis):
    """Key cleared in finally: after render_facebook_photo finishes."""
    fp = TargetFanpage(
        id=1,
        name="Motorsport Page",
        mode2_publish_mode=PublishMode.auto,
    )
    job = PublishJob(
        id=707,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.facebook_recreate,
    )
    db_session.add_all([fp, job])
    db_session.commit()

    fake_redis.data["render_design:queued:707"] = "1"

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db_session), \
         patch("app.tasks.design_renderer._redis", return_value=fake_redis):
        render_facebook_photo(707)

    assert fake_redis.get("render_design:queued:707") is None


def test_key_cleared_after_render_ig_recreate_runs(db_session, fake_redis):
    """Key cleared in finally: after render_ig_recreate finishes."""
    fp = TargetFanpage(
        id=1,
        name="Motorsport Page",
        mode2_publish_mode=PublishMode.auto,
    )
    job = PublishJob(
        id=808,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.ig_recreate,
    )
    db_session.add_all([fp, job])
    db_session.commit()

    fake_redis.data["render_design:queued:808"] = "1"

    with patch("app.tasks.ig_recreate.SessionLocal", return_value=db_session), \
         patch("app.tasks.design_renderer._redis", return_value=fake_redis):
        render_ig_recreate(808)

    assert fake_redis.get("render_design:queued:808") is None


def test_key_cleared_on_render_design_failure(db_session, fake_redis):
    """Key cleared in finally: even if render_design fails or retries."""
    fp = TargetFanpage(
        id=1,
        name="Motorsport Page",
        mode2_publish_mode=PublishMode.auto,
    )
    job = PublishJob(
        id=505,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.news_content,
    )
    db_session.add_all([fp, job])
    db_session.commit()

    fake_redis.data["render_design:queued:505"] = "1"

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db_session), \
         patch("app.tasks.design_renderer._redis", return_value=fake_redis), \
         patch("app.tasks.design_renderer.render_design.retry", side_effect=RuntimeError("retrying")):
        with pytest.raises(RuntimeError, match="retrying"):
            # Trigger an error before template check by mocking db.query side effect or similar
            with patch.object(db_session, "commit", side_effect=Exception("DB boom")):
                render_design(505)

    assert fake_redis.get("render_design:queued:505") is None


def test_redis_down_still_enqueues(db_session, caplog):
    """Redis down -> still enqueues (logs warning, never blocks rendering)."""
    fp = TargetFanpage(
        id=1,
        name="Motorsport Page",
        mode2_publish_mode=PublishMode.auto,
        discussion_publish_mode=PublishMode.auto,
    )
    job = PublishJob(
        id=606,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.news_content,
    )
    db_session.add_all([fp, job])
    db_session.commit()

    import redis
    unreachable = MagicMock(side_effect=redis.exceptions.ConnectionError("Connection refused"))

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db_session), \
         patch("app.tasks.design_renderer._redis", side_effect=unreachable), \
         patch("app.tasks.design_renderer.render_design.delay") as mock_news_delay:

        # Sweep runs without raising
        render_pending_designs()

        assert mock_news_delay.call_count == 1
        mock_news_delay.assert_called_once_with(606)

    # Verify warning logged
    assert any("Redis unreachable while acquiring queue lock for job 606" in r.message for r in caplog.records)
