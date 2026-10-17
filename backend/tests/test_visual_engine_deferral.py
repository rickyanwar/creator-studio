from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker

from app.services.visual_engine import VisualEngineDeferred
from app.tasks.design_renderer import (
    render_design,
    render_facebook_photo,
    render_pending_designs,
)
from app.tasks.ig_recreate import (
    render_ig_recreate,
    run_preview_redesign,
)
from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
from app.models.target_fanpages import TargetFanpage, PublishMode
from app.models.posts import Post
from app.models.design_templates import DesignTemplate
from app.models.scraped_articles import ScrapedArticle
from app.models.radar import RadarStoryDecision


def test_render_design_deferred_sets_job_pending_and_not_before():
    """Test 1: render_design with VisualEngineDeferred returns to pending_design with design_not_before set and no retry."""
    db = MagicMock()
    until_aware = datetime.now(timezone.utc) + timedelta(hours=6)
    exc = VisualEngineDeferred(until=until_aware, reason="night mode")

    job = PublishJob(
        id=101,
        fanpage_id=1,
        source_article_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.news_content,
        design_title="Test News",
    )
    fp = TargetFanpage(id=1, name="Test FP", mode2_gallery_niches=["MotoGP"])
    article = MagicMock(scraped_title="Test News")
    template = DesignTemplate(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1080,
        category="news",
    )

    def mock_query(model):
        m = MagicMock()
        if model == PublishJob:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
        elif model == TargetFanpage:
            m.filter_by.return_value.first.return_value = fp
        elif model == ScrapedArticle:
            m.filter_by.return_value.first.return_value = article
        elif model == DesignTemplate:
            m.filter_by.return_value.first.return_value = template
        return m

    db.query.side_effect = mock_query

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.tasks.design_renderer._maybe_compose_speaker_design", side_effect=exc), \
         patch.object(render_design, "retry") as mock_retry:
        render_design(101)

    assert job.status == PublishJobStatus.pending_design
    assert job.design_not_before is not None
    assert job.design_not_before.tzinfo is None
    assert job.design_not_before == until_aware.astimezone(timezone.utc).replace(tzinfo=None)
    assert "deferred" in (job.last_error or "").lower()
    mock_retry.assert_not_called()
    assert db.commit.called


def test_render_facebook_photo_deferred_sets_job_pending_and_not_before():
    """Test 2: render_facebook_photo with VisualEngineDeferred returns to pending_design with design_not_before set and no retry."""
    db = MagicMock()
    until_aware = datetime.now(timezone.utc) + timedelta(hours=4)
    exc = VisualEngineDeferred(until=until_aware, reason="night mode")

    job = PublishJob(
        id=102,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.facebook_recreate,
        design_title="Test FB Photo",
    )
    fp = TargetFanpage(id=1, name="Test FP", mode2_gallery_niches=["MotoGP"])
    template = DesignTemplate(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1080,
        category="news",
    )

    def mock_query(model):
        m = MagicMock()
        if model == PublishJob:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
        elif model == TargetFanpage:
            m.filter_by.return_value.first.return_value = fp
        elif model == DesignTemplate:
            m.filter_by.return_value.first.return_value = template
        return m

    db.query.side_effect = mock_query

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.tasks.design_renderer._maybe_compose_speaker_design", side_effect=exc), \
         patch.object(render_facebook_photo, "retry") as mock_retry:
        render_facebook_photo(102)

    assert job.status == PublishJobStatus.pending_design
    assert job.design_not_before is not None
    assert job.design_not_before.tzinfo is None
    assert job.design_not_before == until_aware.astimezone(timezone.utc).replace(tzinfo=None)
    assert "deferred" in (job.last_error or "").lower()
    mock_retry.assert_not_called()
    assert db.commit.called


def test_render_ig_recreate_deferred_does_not_fall_back_to_template():
    """Test 3: render_ig_recreate with VisualEngineDeferred defers without hitting template path or failing."""
    db = MagicMock()
    until_aware = datetime.now(timezone.utc) + timedelta(hours=5)
    exc = VisualEngineDeferred(until=until_aware, reason="night deferral")

    job = PublishJob(
        id=103,
        fanpage_id=1,
        post_id=1,
        design_template_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.ig_recreate,
        design_title="Test IG",
    )
    fp = TargetFanpage(id=1, name="Test FP", visual_engine="chatgpt")
    post = Post(id=1, image_local_paths=["/tmp/img.jpg"])
    template = DesignTemplate(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1080,
        category="quote",
    )

    def mock_query(model):
        m = MagicMock()
        if model == PublishJob:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
        elif model == TargetFanpage:
            m.filter_by.return_value.first.return_value = fp
        elif model == Post:
            m.filter_by.return_value.first.return_value = post
        elif model == DesignTemplate:
            m.filter_by.return_value.first.return_value = template
        elif getattr(model, "__name__", "") == "RadarStoryDecision":
            m.filter_by.return_value.first.return_value = None
        return m

    db.query.side_effect = mock_query

    with patch("app.tasks.ig_recreate.SessionLocal", return_value=db), \
         patch("app.services.redesign_pipeline.redesign_card", side_effect=exc), \
         patch.object(render_ig_recreate, "retry") as mock_retry:
        render_ig_recreate(103)

    assert job.status == PublishJobStatus.pending_design
    assert job.status != PublishJobStatus.failed
    assert job.design_not_before is not None
    assert job.design_not_before.tzinfo is None
    assert job.design_not_before == until_aware.astimezone(timezone.utc).replace(tzinfo=None)
    assert "deferred" in (job.last_error or "").lower()
    mock_retry.assert_not_called()
    assert db.commit.called


def test_run_preview_redesign_deferred_sets_resting_message():
    """Test 4: run_preview_redesign with VisualEngineDeferred sets failed preview_status with resting message."""
    db = MagicMock()
    until_aware = datetime.now(timezone.utc) + timedelta(hours=3)
    exc = VisualEngineDeferred(until=until_aware, reason="night")

    decision = RadarStoryDecision(id=201, fanpage_id=1, post_id=1, preview_status="pending")
    fp = TargetFanpage(id=1, name="Test FP")
    post = Post(id=1, image_local_paths=["/tmp/img.jpg"])

    def mock_query(model):
        m = MagicMock()
        if model == RadarStoryDecision:
            m.filter_by.return_value.first.return_value = decision
        elif model == TargetFanpage:
            m.filter_by.return_value.first.return_value = fp
        elif model == Post:
            m.filter_by.return_value.first.return_value = post
        return m

    db.query.side_effect = mock_query

    with patch("app.tasks.ig_recreate.SessionLocal", return_value=db), \
         patch("app.services.redesign_pipeline.redesign_card", side_effect=exc):
        run_preview_redesign(201)

    assert decision.preview_status == "failed"
    expected_msg = f"AI image engines resting until {until_aware.isoformat()} — try again later"[:300]
    assert decision.preview_error == expected_msg
    assert db.commit.called


def test_render_pending_designs_deferral_filters_and_dispatches_visual():
    """Test 5: render_pending_designs respects design_not_before filter and dispatches deferred ig_recreate to visual queue."""
    SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"

    engine = create_engine("sqlite:///:memory:")
    PublishJob.__table__.create(bind=engine)
    TargetFanpage.__table__.create(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    fp = TargetFanpage(id=1, name="Test FP", mode2_publish_mode=PublishMode.auto)
    session.add(fp)

    # News jobs
    job_news_future = PublishJob(
        id=1,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.news_content,
        design_not_before=now + timedelta(hours=2),
    )
    job_news_past = PublishJob(
        id=2,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.news_content,
        design_not_before=now - timedelta(hours=1),
    )
    job_news_none = PublishJob(
        id=3,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.news_content,
        design_not_before=None,
    )

    # IG recreate jobs
    job_ig_future = PublishJob(
        id=4,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.ig_recreate,
        design_not_before=now + timedelta(hours=2),
    )
    job_ig_past = PublishJob(
        id=5,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.ig_recreate,
        design_not_before=now - timedelta(hours=1),
    )
    job_ig_none = PublishJob(
        id=6,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.ig_recreate,
        design_not_before=None,
    )

    session.add_all([
        job_news_future, job_news_past, job_news_none,
        job_ig_future, job_ig_past, job_ig_none,
    ])
    session.commit()

    with patch("app.tasks.design_renderer.SessionLocal", return_value=session), \
         patch("app.tasks.design_renderer.render_design.delay") as mock_news_delay, \
         patch("app.tasks.ig_recreate.render_ig_recreate.apply_async") as mock_ig_async:
        render_pending_designs()

    news_dispatched = [c.args[0] for c in mock_news_delay.call_args_list]
    ig_dispatched = [c.args[0][0] for c in mock_ig_async.call_args_list]
    ig_queues = [c.kwargs.get("queue") for c in mock_ig_async.call_args_list]

    assert 1 not in news_dispatched, "Future news job should not be dispatched"
    assert 2 in news_dispatched, "Past news job should be dispatched"
    assert 3 in news_dispatched, "Non-deferred news job should be dispatched"

    assert 4 not in ig_dispatched, "Future IG recreate job should not be dispatched"
    assert 5 in ig_dispatched, "Past IG recreate job should be dispatched"
    assert 6 not in ig_dispatched, "Non-deferred IG recreate job should not be dispatched by this sweep"
    assert ig_queues == ["visual"], "IG recreate jobs must be dispatched to queue='visual'"


def test_successful_claim_clears_design_not_before():
    """Test 6: A successful claim clears design_not_before on PublishJob."""
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. render_design
    db = MagicMock()
    job_news = PublishJob(
        id=301,
        fanpage_id=1,
        source_article_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.news_content,
        design_not_before=now_naive - timedelta(hours=1),
    )

    def mock_query_news(model):
        m = MagicMock()
        if model == PublishJob:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job_news
        elif model == TargetFanpage:
            m.filter_by.return_value.first.return_value = None
        return m

    db.query.side_effect = mock_query_news
    with patch("app.tasks.design_renderer.SessionLocal", return_value=db):
        render_design(301)
    assert job_news.design_not_before is None, "render_design claim must clear design_not_before"

    # 2. render_facebook_photo
    db = MagicMock()
    job_fb = PublishJob(
        id=302,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.facebook_recreate,
        design_not_before=now_naive - timedelta(hours=1),
    )

    def mock_query_fb(model):
        m = MagicMock()
        if model == PublishJob:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job_fb
        elif model == TargetFanpage:
            m.filter_by.return_value.first.return_value = None
        return m

    db.query.side_effect = mock_query_fb
    with patch("app.tasks.design_renderer.SessionLocal", return_value=db):
        render_facebook_photo(302)
    assert job_fb.design_not_before is None, "render_facebook_photo claim must clear design_not_before"

    # 3. render_ig_recreate
    db = MagicMock()
    job_ig = PublishJob(
        id=303,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.ig_recreate,
        design_not_before=now_naive - timedelta(hours=1),
    )

    def mock_query_ig(model):
        m = MagicMock()
        if model == PublishJob:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job_ig
        elif model == TargetFanpage:
            m.filter_by.return_value.first.return_value = None
        return m

    db.query.side_effect = mock_query_ig
    with patch("app.tasks.ig_recreate.SessionLocal", return_value=db):
        render_ig_recreate(303)
    assert job_ig.design_not_before is None, "render_ig_recreate claim must clear design_not_before"
