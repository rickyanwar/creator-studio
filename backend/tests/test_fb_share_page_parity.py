"""Page production stays independent of Group Share lanes and their budgets."""

from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import DefaultClause

from app.models.ai_copy_events import AICopyEvent
from app.models.ai_video import VideoIdea, VideoJob
from app.models.fb_group_share import FbShareBrandSettings, FbShareGroupJob, FbShareItem, FbShareLane
from app.models.gallery_fetch_events import GalleryFetchEvent
from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ArticleStatus, ScrapedArticle
from app.models.social_publishing import DeliveryEvent, PublishDelivery, SocialDestination
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.fb_share import group_jobs, lanes
from app.services.fb_share.brand_settings import set_brand_share

NOW = datetime(2026, 11, 10, 12)
sqlite3.register_adapter(list, json.dumps)


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@pytest.fixture(params=[False, True], ids=["page-only-share-disabled", "brand-page-with-lane"])
def db(request, monkeypatch):
    engine = create_engine("sqlite://")
    with patch.object(SocialDestination.__table__.c.supported_media, "server_default", DefaultClause(text("'[]'"))), patch.object(
        VideoJob.__table__.c.clips_done, "server_default", DefaultClause(text("'[]'")),
    ), patch.object(
        DeliveryEvent.__table__.c.details, "server_default", DefaultClause(text("'{}'")),
    ):
        for model in (TargetFanpage, FbShareBrandSettings, FbShareGroupJob, FbShareLane, FbShareItem,
                      PublishJob, PublishDelivery, SocialDestination, DeliveryEvent, Post, ScrapedArticle,
                      VideoIdea, VideoJob, AICopyEvent, GalleryFetchEvent):
            model.__table__.create(engine, checkfirst=True)
    with sessionmaker(bind=engine)() as session:
        session.add(TargetFanpage(
            id=1, name="Brand", timezone="UTC", is_active=True, is_connected=True,
            repliz_account_id="page-account", mode1_ig_repost_enabled=True,
            ig_recreate_enabled=False, mode2_news_content_enabled=True,
            mode2_editorial_gate_enabled=False, mode2_publish_mode=PublishMode.manual_review,
            ai_video_enabled=True, ai_video_daily_count=1,
        ))
        session.flush()
        set_brand_share(session, 1, request.param, {"niches": ["f1"]})
        if request.param:
            lanes.ensure_lane(session, 1)
        session.commit()
        monkeypatch.setattr(session, "close", lambda: None)
        yield session
    engine.dispose()


def _publish_job(db, fanpage_id=1, **values):
    fields = dict(fanpage_id=fanpage_id, content_type=ContentType.discussion,
                  status=PublishJobStatus.pending_publish, created_at=NOW, updated_at=NOW,
                  is_deleted=False, design_image_url="https://example.test/card.png",
                  ai_generated_caption="Page caption #F1")
    fields.update(values)
    job = PublishJob(**fields)
    db.add(job)
    db.flush()
    return job


def _video_job(db, fanpage_id=1, **values):
    fields = dict(fanpage_id=fanpage_id, template_key="motorsport_tech_dive", title="Other topic",
                  caption="Caption", model="veo", aspect="9:16", clip_seconds=8, clips=[],
                  credits_per_clip=10, credits_estimate=20, status="completed", created_at=NOW)
    fields.update(values)
    job = VideoJob(**fields)
    db.add(job)
    db.flush()
    return job


def test_fanout_page_creates_original_job_without_redis_or_marker(db, monkeypatch):
    from app.tasks.ai_generator import generate_caption_for_job
    from app.tasks.fan_out import fanout_link

    enqueue = Mock()
    redis = Mock(side_effect=AssertionError("Page fan-out must not use lane Redis"))
    monkeypatch.setattr(generate_caption_for_job, "apply_async", enqueue)
    monkeypatch.setattr(group_jobs, "redis_client", redis)
    link = SimpleNamespace(fanpage=db.get(TargetFanpage, 1), fanpage_id=1, ig_recreate_enabled=False)

    assert fanout_link(db, SimpleNamespace(id=27), link, countdown=120)
    job = db.query(PublishJob).one()
    assert (job.post_id, job.fanpage_id, job.content_type, job.status) == (
        27, 1, ContentType.ig_repost, PublishJobStatus.pending_caption,
    )
    enqueue.assert_called_once_with(args=[job.id], countdown=120)
    assert not fanout_link(db, SimpleNamespace(id=27), link, countdown=120)
    assert db.query(PublishJob).count() == 1
    assert db.query(FbShareGroupJob).count() == 0
    redis.assert_not_called()


def test_news_page_job_has_no_marker_or_redis_calls(db, monkeypatch):
    from app.tasks import news_copywriter

    article = ScrapedArticle(news_source_id=1, article_url="https://example.test/news",
                             scraped_title="Race news", scraped_content="Race report", status=ArticleStatus.scraped)
    db.add(article)
    db.commit()
    page = db.get(TargetFanpage, 1)
    copy = SimpleNamespace(title="Page title", subtitle="Page subtitle", caption="Page news caption",
                           provider="router", category="news", is_breaking=False)
    redis_client = Mock()
    monkeypatch.setattr(news_copywriter, "SessionLocal", lambda: db)
    monkeypatch.setattr(news_copywriter, "_acquire_article_lock", lambda article_id: True)
    monkeypatch.setattr(news_copywriter, "_release_article_lock", lambda article_id: None)
    monkeypatch.setattr(news_copywriter, "_subscribed_fanpages", lambda session, source_id: [page])
    monkeypatch.setattr(group_jobs, "redis_client", lambda: redis_client)
    monkeypatch.setattr("app.services.news_copywriter.generate_news_copy", Mock(return_value=copy))
    monkeypatch.setattr("app.services.news_dedup.find_duplicate_job", lambda *args: None)
    monkeypatch.setattr("app.services.design_images.resolve_template", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.event_days.is_event_window", lambda *args: False)

    news_copywriter.copywrite_article.run(article.id)

    job = db.query(PublishJob).one()
    assert (job.fanpage_id, job.source_article_id, job.content_type, job.status) == (
        1, article.id, ContentType.news_content, PublishJobStatus.pending_design,
    )
    assert (job.design_title, job.design_subtitle, job.ai_generated_caption) == (
        copy.title, copy.subtitle, copy.caption,
    )
    assert article.status == ArticleStatus.copywritten
    assert db.query(FbShareGroupJob).count() == 0
    assert redis_client.mock_calls == []


@pytest.mark.parametrize("page_made_today,stock,expected_new", [(1, 20, 0), (0, 20, 1), (1, 19, 1)])
def test_ai_video_page_daily_count_and_stock_ignore_lane_jobs(
    db, monkeypatch, page_made_today, stock, expected_new,
):
    from app.tasks import ai_video_plan

    monkeypatch.setattr(ai_video_plan, "SessionLocal", lambda: db)
    clock = Mock(wraps=datetime)
    clock.now.return_value = NOW.replace(tzinfo=timezone.utc)
    monkeypatch.setattr(ai_video_plan, "datetime", clock)
    monkeypatch.setattr(ai_video_plan, "polish_enabled", lambda page: False)
    monkeypatch.setattr(ai_video_plan, "allowed_video_templates", lambda page: [])
    monkeypatch.setattr(ai_video_plan, "research_ready", lambda *args: False)
    fill = Mock(return_value=({}, "Page video", "Page caption", [], "mock"))
    monkeypatch.setattr(ai_video_plan, "fill_idea", fill)
    monkeypatch.setattr(ai_video_plan, "resolve_narration", lambda *args: None)
    monkeypatch.setattr(ai_video_plan, "prepare_cover", lambda *args, **kwargs: None)
    monkeypatch.setattr(ai_video_plan, "similar_subjects", lambda *args: False)
    for _ in range(page_made_today):
        _video_job(db)
    for _ in range(stock):
        _publish_job(db, content_type=ContentType.ai_video, video_path="page.mp4")
    lane_id = lanes.get_lane_id(db, 1)
    if lane_id is not None:
        # These would exhaust the Page's open-job limit and daily budget if mixed in.
        for _ in range(2):
            _video_job(db, fanpage_id=lane_id, status="queued", credits_estimate=9999)
            _publish_job(db, fanpage_id=lane_id, content_type=ContentType.ai_video, video_path="lane.mp4")
    idea = VideoIdea(fanpage_id=1, template_key="motorsport_tech_dive", topic="New Page topic",
                     status="approved", source="owner", clips=2, score=90, created_at=NOW)
    db.add(idea)
    db.commit()

    assert ai_video_plan.video_stock(db, 1) == stock
    ai_video_plan.plan_ai_videos.run()

    assert db.query(VideoJob).filter_by(fanpage_id=1).count() == page_made_today + expected_new
    assert idea.status == ("queued" if expected_new else "approved")
    assert fill.call_count == expected_new
    assert db.query(FbShareGroupJob).filter(
        FbShareGroupJob.video_job_id.in_(db.query(VideoJob.id).filter_by(fanpage_id=1)),
    ).count() == 0


def test_dashboard_page_stats_do_not_change_when_lane_jobs_are_added(db, monkeypatch):
    from app.api import dashboard

    monkeypatch.setattr(dashboard, "_utcnow", lambda: NOW.replace(tzinfo=timezone.utc))
    monkeypatch.setattr(dashboard.shutil, "disk_usage", lambda path: SimpleNamespace(used=0, total=0))
    monkeypatch.setattr(dashboard.os, "walk", lambda path: [])
    published = _publish_job(db, status=PublishJobStatus.published, published_at=NOW)
    _publish_job(db, status=PublishJobStatus.failed)
    _publish_job(db, status=PublishJobStatus.pending_review)
    db.commit()
    before = dashboard.get_dashboard_stats(db, None)
    assert (before["published_today"], before["failed_today"], before["pending_review"]) == (1, 1, 1)
    assert (before["active_fanpages"], before["total_fanpages"]) == (1, 1)
    brand = before["brands_today"][0]
    assert (brand["published_today"], brand["failed_today"], brand["waiting_review"], brand["needs_review"]) == (1, 1, 1, 1)
    assert [post["id"] for post in before["latest_posts"]] == [published.id]

    lane_id = lanes.get_lane_id(db, 1)
    if lane_id is not None:
        _publish_job(db, lane_id, status=PublishJobStatus.published, published_at=NOW)
        _publish_job(db, lane_id, status=PublishJobStatus.failed)
        _publish_job(db, lane_id, status=PublishJobStatus.pending_review)
        db.commit()
    assert dashboard.get_dashboard_stats(db, None) == before


def test_publisher_page_job_creates_delivery_instead_of_group_handover(db, monkeypatch):
    from app.tasks import publisher

    destination = SocialDestination(content_project_id=1, name="Page", kind="facebook_page",
                                    mode="repliz", timezone="UTC", is_active=True,
                                    supported_media=["image"], daily_target=2, repliz_daily_target=2)
    db.add(destination)
    job = _publish_job(db)
    db.commit()
    dispatch = Mock()
    hand_over = Mock(side_effect=AssertionError("Page jobs must not be handed to Group Share"))
    monkeypatch.setattr(publisher, "SessionLocal", lambda: db)
    monkeypatch.setattr(group_jobs, "hand_over", hand_over)
    monkeypatch.setattr("app.services.social_delivery_dispatch.dispatch_due_deliveries", dispatch)

    publisher.publish_job.run(job.id)

    delivery = db.query(PublishDelivery).one()
    assert (delivery.publish_job_id, delivery.destination_id) == (job.id, destination.id)
    assert (delivery.caption_snapshot, delivery.media_snapshot) == (
        "Page caption #F1", ["https://example.test/card.png"],
    )
    assert delivery.state == "scheduled"
    assert delivery.intended_method == "repliz"
    assert db.query(DeliveryEvent).filter_by(delivery_id=delivery.id, event_type="planned").count() == 1
    assert db.query(FbShareGroupJob).count() == 0
    assert db.query(FbShareItem).count() == 0
    assert job.status != PublishJobStatus.skipped
    dispatch.assert_called_once_with(db, job_id=job.id)
    hand_over.assert_not_called()


def test_after_flush_fifty_page_jobs_cost_at_most_one_select(db):
    jobs = [PublishJob(fanpage_id=1, content_type=ContentType.discussion,
                       status=PublishJobStatus.pending_design) for _ in range(50)]
    selects = []

    def count_selects(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(db.bind, "before_cursor_execute", count_selects)
    try:
        db.add_all(jobs)
        db.flush()
    finally:
        event.remove(db.bind, "before_cursor_execute", count_selects)

    assert len(selects) <= 1, selects
    assert all(job.id is not None for job in jobs)
    assert db.query(PublishJob).count() == 50
    assert db.query(FbShareGroupJob).count() == 0
