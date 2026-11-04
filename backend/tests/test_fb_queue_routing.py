import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.facebook_photo_ideas import FacebookPhotoIdea
from app.models.facebook_photo_sources import FacebookPhotoSource
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services import engine_router
from app.services.fb_source_format import format_for_job, queue_override
from app.tasks import design_renderer, facebook_photo


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    for model in (TargetFanpage, FacebookPhotoSource, FacebookPhotoIdea, PublishJob):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def routing(monkeypatch):
    monkeypatch.setattr(engine_router, "get_gptcli_config", lambda db: object())
    monkeypatch.setattr(engine_router, "gptcli_applies", lambda cfg, fp: True)


@pytest.mark.parametrize("toggle", ["off", "chatgpt"])
@pytest.mark.parametrize("fmt,expected", [("chatgpt", "visual"), ("template", None), ("repost", None), ("follow", None)])
def test_facebook_queue_uses_only_source_override(routing, toggle, fmt, expected):
    page = SimpleNamespace(id=1, fb_visual_engine=toggle)
    assert engine_router.render_queue_for(None, page, "facebook", visual_engine_override=queue_override(fmt, None)) == expected


@pytest.mark.parametrize("fmt", ["chatgpt", "template", "repost", None])
def test_consume_dispatches_source_format(db, routing, monkeypatch, fmt):
    page = TargetFanpage(id=1, name="Page", fb_visual_engine="off", facebook_photo_publish_mode=PublishMode.auto)
    source = FacebookPhotoSource(id=2, fanpage_id=1, page_url="https://www.facebook.com/page", format=fmt)
    idea = FacebookPhotoIdea(id=3, fanpage_id=1, source_id=2, gallery_image_id=10, category="news", design_title="Headline", status="pending")
    db.add_all([page, source, idea])
    db.commit()
    monkeypatch.setattr("app.services.ai_caption.generate_caption", lambda *a: ("Caption", "gemini"))
    monkeypatch.setattr("app.services.ai_caption.build_caption_prompt", lambda *a, **kw: "Prompt")
    monkeypatch.setattr("app.services.design_images.resolve_template", lambda *a, **kw: None)
    dispatch = MagicMock()
    monkeypatch.setattr(design_renderer.render_facebook_photo, "apply_async", dispatch)
    assert facebook_photo._consume_one(db, page)
    assert dispatch.call_count == 1
    assert dispatch.call_args.kwargs.get("queue") == ("visual" if fmt == "chatgpt" else None)


def test_format_for_job_scopes_page_and_uses_newest_idea(db):
    now = datetime.now()
    db.add_all([
        FacebookPhotoSource(id=1, fanpage_id=1, page_url="one", format="chatgpt"),
        FacebookPhotoSource(id=2, fanpage_id=1, page_url="two", format="template"),
        FacebookPhotoIdea(id=1, fanpage_id=1, gallery_image_id=10, source_id=1, created_at=now, category="news", design_title="Old"),
        FacebookPhotoIdea(id=2, fanpage_id=1, gallery_image_id=10, source_id=2, created_at=now, category="news", design_title="New"),
        FacebookPhotoIdea(id=3, fanpage_id=2, gallery_image_id=10, source_id=1, created_at=now + timedelta(seconds=1), category="news", design_title="Other"),
    ])
    db.commit()
    job = SimpleNamespace(fanpage_id=1, source_gallery_image_id=10)
    assert format_for_job(db, job) == "template"
    db.get(FacebookPhotoIdea, 2).source_id = None
    db.commit()
    assert format_for_job(db, job) == "follow"
    job.source_gallery_image_id = None
    assert format_for_job(db, job) == "follow"
    job.source_gallery_image_id = 999
    assert format_for_job(db, job) == "follow"


@pytest.mark.parametrize("fmt", ["chatgpt", "template", "repost", None])
def test_sweep_routes_source_format(db, routing, monkeypatch, fmt):
    db.add_all([
        TargetFanpage(id=1, name="Page", fb_visual_engine="chatgpt"),
        FacebookPhotoSource(id=2, fanpage_id=1, page_url="page", format=fmt),
        FacebookPhotoIdea(id=3, fanpage_id=1, source_id=2, gallery_image_id=10, category="news", design_title="Headline"),
        PublishJob(id=4, fanpage_id=1, source_gallery_image_id=10, content_type=ContentType.facebook_recreate, status=PublishJobStatus.pending_design),
    ])
    db.commit()
    monkeypatch.setattr(design_renderer, "SessionLocal", lambda: db)
    monkeypatch.setattr(design_renderer, "_acquire_queued_lock", lambda job_id: True)
    monkeypatch.setattr("app.services.visual_fair_queue.visual_news_fanpage_ids", lambda db: [])
    dispatch, delay = MagicMock(), MagicMock()
    monkeypatch.setattr(design_renderer.render_facebook_photo, "apply_async", dispatch)
    monkeypatch.setattr(design_renderer.render_facebook_photo, "delay", delay)
    design_renderer.render_pending_designs()
    if fmt == "chatgpt":
        dispatch.assert_called_once_with(args=[4], queue="visual", expires=1800)
        delay.assert_not_called()
    else:
        delay.assert_called_once_with(4)
        dispatch.assert_not_called()


def test_migration_preserves_explicit_source_formats(db, monkeypatch):
    path = Path(__file__).parents[1] / "alembic/versions/c80000000054_fb_visual_engine_to_sources.py"
    spec = importlib.util.spec_from_file_location("fb_format_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    db.add_all([
        TargetFanpage(id=1, name="AI", fb_visual_engine="chatgpt"),
        TargetFanpage(id=2, name="Template", fb_visual_engine="off"),
        FacebookPhotoSource(id=1, fanpage_id=1, page_url="one"),
        FacebookPhotoSource(id=2, fanpage_id=1, page_url="two", format="repost"),
        FacebookPhotoSource(id=3, fanpage_id=2, page_url="three"),
    ])
    db.commit()
    from sqlalchemy import text
    monkeypatch.setattr(migration.op, "execute", lambda sql: db.execute(text(sql)))
    migration.upgrade()
    db.expire_all()
    assert [db.get(FacebookPhotoSource, i).format for i in (1, 2, 3)] == ["chatgpt", "repost", None]
    assert migration.down_revision == "c80000000053"
    migration.downgrade()
    assert db.get(FacebookPhotoSource, 1).format == "chatgpt"
