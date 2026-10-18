from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker

from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
from app.models.scraped_articles import ArticleStatus
from app.models.target_fanpages import TargetFanpage, PublishMode
from app.tasks.news_copywriter import copywrite_article
from app.tasks.facebook_photo import _consume_one
from app.tasks.design_renderer import (
    render_pending_designs,
    _maybe_compose_speaker_design,
)
from app.api.publish_jobs import render_now, reedit_job

SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"


def test_news_copywriter_dispatch_queue():
    """1. news copywriter dispatch: q 'visual' -> apply_async(..., queue='visual'); q None -> apply_async(...) without queue."""
    def make_article():
        a = MagicMock()
        a.id = 10
        a.status = ArticleStatus.scraped
        a.news_source_id = 100
        return a

    mock_fp = MagicMock()
    mock_fp.id = 1
    mock_fp.mode2_publish_mode = PublishMode.auto
    mock_fp.mode2_editorial_gate_enabled = False

    mock_copy = MagicMock()
    mock_copy.title = "Title"
    mock_copy.subtitle = "Sub"
    mock_copy.caption = "Cap"
    mock_copy.provider = "gemini"
    mock_copy.is_breaking = False
    mock_copy.category = "racing"

    # Case A: q = "visual"
    with patch("app.tasks.news_copywriter.SessionLocal") as mock_session_cls, \
         patch("app.tasks.news_copywriter._subscribed_fanpages", return_value=[mock_fp]), \
         patch("app.services.news_copywriter.generate_news_copy", return_value=mock_copy), \
         patch("app.services.design_images.resolve_template", return_value=None), \
         patch("app.services.engine_router.render_queue_for", return_value="visual"), \
         patch("app.tasks.design_renderer.render_design.apply_async") as mock_render:
        db = MagicMock()
        mock_session_cls.return_value = db
        db.query().filter_by().first.side_effect = [make_article(), None]
        copywrite_article(10)

        assert mock_render.called
        assert mock_render.call_args.kwargs.get("queue") == "visual"

    # Case B: q = None
    with patch("app.tasks.news_copywriter.SessionLocal") as mock_session_cls, \
         patch("app.tasks.news_copywriter._subscribed_fanpages", return_value=[mock_fp]), \
         patch("app.services.news_copywriter.generate_news_copy", return_value=mock_copy), \
         patch("app.services.design_images.resolve_template", return_value=None), \
         patch("app.services.engine_router.render_queue_for", return_value=None), \
         patch("app.tasks.design_renderer.render_design.apply_async") as mock_render:
        db = MagicMock()
        mock_session_cls.return_value = db
        db.query().filter_by().first.side_effect = [make_article(), None]
        copywrite_article(10)

        assert mock_render.called
        assert "queue" not in mock_render.call_args.kwargs


def test_facebook_photo_dispatch_queue():
    """2. facebook_photo dispatch: q 'visual' -> apply_async(..., queue='visual'); q None -> apply_async(...) without queue."""
    mock_fp = MagicMock()
    mock_fp.id = 2
    mock_fp.facebook_photo_publish_mode = PublishMode.auto

    mock_idea = MagicMock()
    mock_idea.id = 20
    mock_idea.gallery_image_id = 200
    mock_idea.design_title = "FB Title"
    mock_idea.design_subtitle = "FB Sub"
    mock_idea.design_caption = "FB Cap"
    mock_idea.category = "racing"

    # Case A: q = "visual"
    db_a = MagicMock()
    db_a.query().filter().order_by().first.return_value = mock_idea
    with patch("app.services.ai_caption.generate_caption", return_value=("caption", "gemini")), \
         patch("app.services.design_images.resolve_template", return_value=None), \
         patch("app.tasks.facebook_photo._caption_inputs", return_value=("ctx", "quote")), \
         patch("app.services.engine_router.render_queue_for", return_value="visual"), \
         patch("app.tasks.design_renderer.render_facebook_photo.apply_async") as mock_render:
        _consume_one(db_a, mock_fp)

        assert mock_render.called
        assert mock_render.call_args.kwargs.get("queue") == "visual"

    # Case B: q = None
    db_b = MagicMock()
    db_b.query().filter().order_by().first.return_value = mock_idea
    with patch("app.services.ai_caption.generate_caption", return_value=("caption", "gemini")), \
         patch("app.services.design_images.resolve_template", return_value=None), \
         patch("app.tasks.facebook_photo._caption_inputs", return_value=("ctx", "quote")), \
         patch("app.services.engine_router.render_queue_for", return_value=None), \
         patch("app.tasks.design_renderer.render_facebook_photo.apply_async") as mock_render:
        _consume_one(db_b, mock_fp)

        assert mock_render.called
        assert "queue" not in mock_render.call_args.kwargs


def test_render_pending_designs_routing():
    """3. render_pending_designs: news and facebook jobs with q 'visual' -> apply_async with queue; with None -> .delay."""
    engine = create_engine("sqlite:///:memory:")
    PublishJob.__table__.create(bind=engine)
    TargetFanpage.__table__.create(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    fp = TargetFanpage(id=1, name="FP", mode2_publish_mode=PublishMode.auto)
    session.add(fp)

    job_news = PublishJob(
        id=101,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.news_content,
    )
    job_fb = PublishJob(
        id=102,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.facebook_recreate,
    )
    session.add_all([job_news, job_fb])
    session.commit()

    # Case A: q = "visual"
    with patch("app.tasks.design_renderer.SessionLocal", return_value=session), \
         patch("app.services.engine_router.render_queue_for", return_value="visual"), \
         patch("app.tasks.design_renderer.render_design.apply_async") as mock_news_async, \
         patch("app.tasks.design_renderer.render_design.delay") as mock_news_delay, \
         patch("app.tasks.design_renderer.render_facebook_photo.apply_async") as mock_fb_async, \
         patch("app.tasks.design_renderer.render_facebook_photo.delay") as mock_fb_delay:
        render_pending_designs()

        mock_news_async.assert_called_once_with(args=[101], queue="visual", expires=1800)
        mock_news_delay.assert_not_called()
        mock_fb_async.assert_called_once_with(args=[102], queue="visual", expires=1800)
        mock_fb_delay.assert_not_called()

    # Case B: q = None
    with patch("app.tasks.design_renderer.SessionLocal", return_value=session), \
         patch("app.services.engine_router.render_queue_for", return_value=None), \
         patch("app.tasks.design_renderer.render_design.apply_async") as mock_news_async, \
         patch("app.tasks.design_renderer.render_design.delay") as mock_news_delay, \
         patch("app.tasks.design_renderer.render_facebook_photo.apply_async") as mock_fb_async, \
         patch("app.tasks.design_renderer.render_facebook_photo.delay") as mock_fb_delay:
        render_pending_designs()

        mock_news_delay.assert_called_once_with(101)
        mock_news_async.assert_not_called()
        mock_fb_delay.assert_called_once_with(102)
        mock_fb_async.assert_not_called()


def test_publish_jobs_endpoints_routing():
    """4. publish_jobs endpoints: q None -> .delay; q 'visual' -> apply_async with queue."""
    def make_pending_job():
        j = MagicMock()
        j.id = 501
        j.fanpage_id = 1
        j.content_type = ContentType.news_content
        j.status = PublishJobStatus.pending_design
        return j

    def make_published_job():
        j = MagicMock()
        j.id = 502
        j.fanpage_id = 1
        j.content_type = ContentType.news_content
        j.status = PublishJobStatus.published
        j.repliz_schedule_id = None
        return j

    fanpage = MagicMock()
    fanpage.id = 1

    # render_now with q = "visual"
    db = MagicMock()
    db.query().filter_by().first.side_effect = [make_pending_job(), fanpage]
    with patch("app.services.engine_router.render_queue_for", return_value="visual"), \
         patch("app.tasks.design_renderer.render_design.apply_async") as mock_async, \
         patch("app.tasks.design_renderer.render_design.delay") as mock_delay, \
         patch("app.api.publish_jobs._enrich_job", return_value={"id": 501}):
        render_now(501, db, MagicMock())
        mock_async.assert_called_once_with(args=[501], queue="visual")
        mock_delay.assert_not_called()

    # render_now with q = None
    db = MagicMock()
    db.query().filter_by().first.side_effect = [make_pending_job(), fanpage]
    with patch("app.services.engine_router.render_queue_for", return_value=None), \
         patch("app.tasks.design_renderer.render_design.apply_async") as mock_async, \
         patch("app.tasks.design_renderer.render_design.delay") as mock_delay, \
         patch("app.api.publish_jobs._enrich_job", return_value={"id": 501}):
        render_now(501, db, MagicMock())
        mock_delay.assert_called_once_with(501)
        mock_async.assert_not_called()

    # reedit_job with q = "visual"
    db = MagicMock()
    db.query().filter_by().first.side_effect = [make_published_job(), fanpage]
    with patch("app.services.engine_router.render_queue_for", return_value="visual"), \
         patch("app.tasks.design_renderer.render_design.apply_async") as mock_async, \
         patch("app.tasks.design_renderer.render_design.delay") as mock_delay, \
         patch("app.api.publish_jobs._enrich_job", return_value={"id": 502}):
        reedit_job(502, db, MagicMock())
        mock_async.assert_called_once_with(args=[502], queue="visual")
        mock_delay.assert_not_called()

    # reedit_job with q = None
    db = MagicMock()
    db.query().filter_by().first.side_effect = [make_published_job(), fanpage]
    with patch("app.services.engine_router.render_queue_for", return_value=None), \
         patch("app.tasks.design_renderer.render_design.apply_async") as mock_async, \
         patch("app.tasks.design_renderer.render_design.delay") as mock_delay, \
         patch("app.api.publish_jobs._enrich_job", return_value={"id": 502}):
        reedit_job(502, db, MagicMock())
        mock_delay.assert_called_once_with(502)
        mock_async.assert_not_called()


def test_maybe_compose_speaker_design_content_kind():
    """5. _maybe_compose_speaker_design passes content_kind=mode to compose_speaker_design."""
    fanpage = MagicMock()
    fanpage.name = "MotoGP FP"
    fanpage.mode2_gallery_niches = ["MotoGP"]
    fanpage.fb_visual_engine = "chatgpt"
    fanpage.news_visual_engine = "chatgpt"

    job = MagicMock()
    job.design_title = "Marc Marquez Returns"
    job.design_subtitle = "Marc Marquez"

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=True), \
         patch("app.services.speaker_design.compose_speaker_design") as mock_compose:
        mock_compose.return_value = b"fake-png-bytes"
        db = MagicMock()

        # mode = "facebook"
        res = _maybe_compose_speaker_design(db, fanpage, job, None, mode="facebook")
        assert res == b"fake-png-bytes"
        assert mock_compose.call_args.kwargs.get("content_kind") == "facebook"

        # mode = "news"
        res_news = _maybe_compose_speaker_design(db, fanpage, job, None, mode="news")
        assert res_news == b"fake-png-bytes"
        assert mock_compose.call_args.kwargs.get("content_kind") == "news"
