"""Tests for PublishJob design draft endpoints and payload (TASK C3)."""

from datetime import datetime, timezone
import io
import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles

sqlite3.register_adapter(list, json.dumps)

@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"

@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"

from app.api.deps import get_db, get_current_user
from app.api.publish_jobs import router as publish_jobs_router
from app.config import get_settings
from app.models.design_templates import DesignTemplate
from app.models.gallery import GalleryKeyword, GalleryImage
from app.models.news_sources import NewsSource
from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ScrapedArticle
from app.models.social_publishing import PublishDelivery
from app.models.target_fanpages import TargetFanpage

api_app = FastAPI()
api_app.include_router(publish_jobs_router)


@pytest.fixture(scope="module")
def sqlite_engine():
    orig_niches_sd = TargetFanpage.__table__.columns["mode2_gallery_niches"].server_default
    orig_niches_null = TargetFanpage.__table__.columns["mode2_gallery_niches"].nullable
    TargetFanpage.__table__.columns["mode2_gallery_niches"].server_default = None
    TargetFanpage.__table__.columns["mode2_gallery_niches"].nullable = True

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        TargetFanpage.__table__,
        DesignTemplate.__table__,
        GalleryKeyword.__table__,
        GalleryImage.__table__,
        Post.__table__,
        NewsSource.__table__,
        ScrapedArticle.__table__,
        PublishJob.__table__,
        PublishDelivery.__table__,
    ]
    for table in tables:
        table.create(bind=engine)

    yield engine

    for table in reversed(tables):
        table.drop(bind=engine, checkfirst=True)
    engine.dispose()
    TargetFanpage.__table__.columns["mode2_gallery_niches"].server_default = orig_niches_sd
    TargetFanpage.__table__.columns["mode2_gallery_niches"].nullable = orig_niches_null


@pytest.fixture
def db(sqlite_engine):
    Session = sessionmaker(bind=sqlite_engine)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        session.query(PublishDelivery).delete()
        session.query(PublishJob).delete()
        session.query(ScrapedArticle).delete()
        session.query(GalleryImage).delete()
        session.query(GalleryKeyword).delete()
        session.query(DesignTemplate).delete()
        session.query(TargetFanpage).delete()
        session.commit()
        session.close()


@pytest.fixture
def client(db):
    api_app.dependency_overrides[get_db] = lambda: db
    api_app.dependency_overrides[get_current_user] = lambda: "testadmin"
    yield TestClient(api_app)
    api_app.dependency_overrides.clear()


def _create_fanpage(db):
    fp = TargetFanpage(name="Test Fanpage", is_active=True, mode2_gallery_niches=None)
    db.add(fp)
    db.commit()
    db.refresh(fp)
    return fp


def test_save_design_draft_success(db, client):
    fp = _create_fanpage(db)
    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.pending_design,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    draft_data = {"version": "5.3.0", "objects": [{"type": "text", "text": "Draft Headline"}]}
    resp = client.put(f"/publish-jobs/{job.id}/design-draft", json={"draft": draft_data})
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "ok"
    assert "saved_at" in res and res["saved_at"] is not None

    db.refresh(job)
    assert job.design_draft_json == draft_data
    assert job.design_draft_saved_at is not None


def test_save_design_draft_conflict_when_not_pending_design(db, client):
    fp = _create_fanpage(db)
    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.pending_review,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    resp = client.put(
        f"/publish-jobs/{job.id}/design-draft",
        json={"draft": {"objects": []}},
    )
    assert resp.status_code == 409
    assert "Cannot save draft" in resp.json()["detail"]


def test_save_design_draft_too_large(db, client):
    fp = _create_fanpage(db)
    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.pending_design,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Payload larger than 2 MB
    big_draft = {"large": "x" * (2 * 1024 * 1024 + 10)}
    resp = client.put(f"/publish-jobs/{job.id}/design-draft", json={"draft": big_draft})
    assert resp.status_code == 413


def test_delete_design_draft_success(db, client):
    fp = _create_fanpage(db)
    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.pending_design,
        design_draft_json={"foo": "bar"},
        design_draft_saved_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    resp = client.delete(f"/publish-jobs/{job.id}/design-draft")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    db.refresh(job)
    assert job.design_draft_json is None
    assert job.design_draft_saved_at is None


def test_get_design_payload_includes_draft_info(db, client):
    fp = _create_fanpage(db)
    tpl = DesignTemplate(
        name="Test Template",
        category="news",
        canvas_width=1080,
        canvas_height=1080,
        template_json={"objects": []},
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)

    saved_time = datetime(2026, 10, 18, 14, 20, 0, tzinfo=timezone.utc)
    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.pending_design,
        design_template_id=tpl.id,
        design_draft_json={"version": "1.0", "custom": True},
        design_draft_saved_at=saved_time,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    resp = client.get(f"/publish-jobs/{job.id}/design-payload")
    assert resp.status_code == 200
    data = resp.json()
    assert data["design_draft_json"] == {"version": "1.0", "custom": True}
    assert "saved_at" in data
    assert "14:20:00" in data["saved_at"]


def test_upload_design_image_clears_draft(db, client, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))
    fp = _create_fanpage(db)
    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.pending_design,
        design_draft_json={"saved": "draft"},
        design_draft_saved_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    file_content = b"\x89PNG\r\n\x1a\nfake-png-content"
    with patch("app.tasks.publisher.publish_job.delay") as mock_delay:
        resp = client.post(
            f"/publish-jobs/{job.id}/design-image",
            files={"file": ("design.png", io.BytesIO(file_content), "image/png")},
        )
    assert resp.status_code == 200
    mock_delay.assert_called_once_with(job.id)

    db.refresh(job)
    assert job.status == PublishJobStatus.pending_publish
    assert job.design_draft_json is None
    assert job.design_draft_saved_at is None


def test_draft_endpoints_not_found(client):
    resp = client.put("/publish-jobs/999999/design-draft", json={"draft": {}})
    assert resp.status_code == 404
    resp = client.delete("/publish-jobs/999999/design-draft")
    assert resp.status_code == 404
