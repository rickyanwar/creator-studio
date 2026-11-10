"""Already-marked group jobs bypass Page news gates and manual review.

The 8bc0f82 overflow paths (stale/no-slot news, IG 24 h rescue, radar daily-cap overflow)
were removed: group lanes generate group content (see test_fb_share_lane_caps.py).
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.fb_group_share import FbShareBrandSettings, FbShareGroupJob
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.target_fanpages import TargetFanpage, PublishMode
from app.models.social_publishing import PublishDelivery
from app.services.fb_share.group_jobs import mark_group_job
from app.services import visual_fair_queue as fair
from app.tasks import design_renderer as renderer
from backend.tests.test_visual_fair_queue import FakeRedis, _compile_array, _compile_jsonb  # noqa: F401


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    from app.models.scraped_articles import ScrapedArticle
    from app.models.design_templates import DesignTemplate
    for model in (PublishJob, TargetFanpage, PublishDelivery, ScrapedArticle, DesignTemplate,
                  FbShareGroupJob, FbShareBrandSettings):
        model.__table__.create(engine, checkfirst=True)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        yield db
    engine.dispose()


def news(db, *, age=1, breaking=False):
    fp = TargetFanpage(name="Page", mode2_publish_mode=PublishMode.auto)
    db.add(fp)
    db.flush()
    job = PublishJob(fanpage_id=fp.id, content_type=ContentType.news_content,
                     status=PublishJobStatus.pending_design, is_breaking=breaking,
                     created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=age))
    db.add(job)
    db.commit()
    return job


def test_group_news_bypasses_page_gates_and_counters(db_session):
    # Fresh job: stale group news is skipped like Page news (see test_ai_image_reuse.py).
    job = news(db_session, age=1)
    mark_group_job(db_session, fanpage_id=job.fanpage_id, mode="news", publish_job_id=job.id)
    db_session.commit()
    now = datetime.now(timezone.utc)
    with patch("app.services.social_delivery_planner.news_slot_available", side_effect=AssertionError), \
         patch("app.services.social_delivery_planner.next_news_slot_utc", side_effect=AssertionError):
        assert fair._has_news_slot(db_session, job, now)
        assert fair._next_slot(db_session, job, now) == now
    job.status = PublishJobStatus.pending_publish
    db_session.commit()
    assert fair.ahead_count(db_session, job.fanpage_id, now) == 0
    job.status = PublishJobStatus.pending_design
    db_session.commit()
    redis = FakeRedis()
    with patch.object(renderer, "SessionLocal", return_value=db_session), \
         patch.object(renderer, "_acquire_queued_lock", return_value=True), \
         patch.object(renderer.render_design, "apply_async") as render, \
         patch("app.services.engine_router.render_queue_for", return_value="visual"), \
         patch.object(fair, "render_queue_for", return_value="visual"), \
         patch.object(fair, "page_claimed", return_value=True), \
         patch.object(renderer, "_redis", return_value=redis):
        renderer.render_pending_designs.run()
    render.assert_called_once()
    assert fair.served_today(redis, [job.fanpage_id], now) == {job.fanpage_id: 0}



@pytest.mark.parametrize("quota", [0, 1])
def test_stale_page_news_is_skipped_not_converted(db_session, quota):
    from app.services.fb_share.brand_settings import set_brand_share
    job = news(db_session, age=25)
    set_brand_share(db_session, job.fanpage_id, True, {"sources": {"news": {"source": "both", "groups_only_per_day": quota}}})
    db_session.commit()
    with patch.object(renderer, "SessionLocal", return_value=db_session), \
         patch.object(renderer, "_acquire_queued_lock", return_value=False), \
         patch("app.services.visual_fair_queue.visual_news_fanpage_ids", return_value=set()):
        renderer.render_pending_designs.run()
    assert db_session.query(FbShareGroupJob).count() == 0
    assert job.status == PublishJobStatus.skipped


def test_ig_edit_group_completion_publishes_in_manual_mode():
    from app.tasks import ig_recreate
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = None
    job = SimpleNamespace(id=1, fanpage_id=1, design_analysis_json={})
    fp = SimpleNamespace(id=1, publish_mode=PublishMode.manual_review)
    with patch("app.services.ig_photo_edit.edit_ig_photo", return_value={
        "ok": True, "image_path": "/media/designs/group.jpg", "engine": "chatgpt",
    }), patch("app.services.qa_toggles.qa_enabled", return_value=True), \
         patch("app.services.fb_share.group_jobs.is_group_only", return_value=True), \
         patch("app.tasks.publisher.publish_job.delay") as publish:
        ig_recreate._render_ig_edit(db, job, SimpleNamespace(image_local_paths=["original.jpg"]),
                                   fp, SimpleNamespace(chatgpt_edit_prompt="Keep it real"))
    assert job.status == PublishJobStatus.pending_publish
    publish.assert_called_once_with(job.id)
