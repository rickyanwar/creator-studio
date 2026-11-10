"""Edit once: cleanup deletes a job's raw reuse file together with its design file, never alone."""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause

import app.models  # noqa: F401 - register every mapped class for the relationships
from app.models.facebook_photo_ideas import FacebookPhotoIdea
from app.models.fb_group_share import FbShareGroupJob, FbShareItem, FbShareTask
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import PublishDelivery, SocialDestination
from app.models.target_fanpages import TargetFanpage
from app.services import ai_image_reuse
from app.tasks import cleanup

NOW = datetime.now(timezone.utc).replace(tzinfo=None)


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    with patch.object(SocialDestination.__table__.c.supported_media, "server_default", DefaultClause(text("'[]'"))):
        for model in (TargetFanpage, PublishJob, SocialDestination, PublishDelivery, FbShareGroupJob, FbShareItem,
                      FbShareTask, FacebookPhotoIdea):
            model.__table__.create(engine, checkfirst=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture
def fanpage(db):
    page = TargetFanpage(name="Reuse cleanup page", timezone="UTC")
    db.add(page)
    db.commit()
    return page


@pytest.fixture
def cleanup_old_designs(session_factory):
    def _run():
        with patch.object(cleanup, "SessionLocal", session_factory):
            cleanup.cleanup_old_designs()
    return _run


def _job(db, tmp_path, fanpage, *, status=PublishJobStatus.published, published_at=None, raw_name="ai_source",
         raw_owner=None):
    """A job with a design file and a raw reuse file named like save_ai_image / copy_reused do."""
    job = PublishJob(fanpage_id=fanpage.id, content_type=ContentType.news_content, status=status,
                     published_at=published_at, design_image_url="https://example.com/d.png")
    db.add(job)
    db.flush()
    design = tmp_path / f"design_{job.id}.png"
    design.write_bytes(b"\x89PNGcard")
    raw = tmp_path / f"job_{(raw_owner or job).id}_{raw_name}_{uuid.uuid4().hex[:8]}.png"
    if not raw.exists():
        raw.write_bytes(b"\x89PNGraw")
    job.design_image_path = str(design)
    ai_image_reuse.remember(job, str(raw), source_key="news:7", format_key="pro_poster", prompt_text="",
                            engine="gptcli", qa_checked=True)
    db.commit()
    return job, design, raw


def _reload(db, job_id):
    db.expire_all()
    return db.get(PublishJob, job_id)


def test_raw_file_is_deleted_with_the_design_file(db, tmp_path, fanpage, cleanup_old_designs):
    job, design, raw = _job(db, tmp_path, fanpage, published_at=NOW - timedelta(days=8))

    cleanup_old_designs()

    assert not design.exists() and not raw.exists()
    assert _reload(db, job.id).design_image_path is None


def test_raw_file_is_kept_while_the_design_is_kept(db, tmp_path, fanpage, cleanup_old_designs):
    _, design, raw = _job(db, tmp_path, fanpage, published_at=NOW - timedelta(hours=2))

    cleanup_old_designs()

    assert design.exists() and raw.exists()


def test_group_job_not_handed_over_keeps_raw_file(db, tmp_path, fanpage, cleanup_old_designs):
    job, design, raw = _job(db, tmp_path, fanpage, published_at=NOW - timedelta(days=8))
    db.add(FbShareGroupJob(fanpage_id=fanpage.id, mode="news", publish_job_id=job.id))
    db.commit()

    cleanup_old_designs()

    assert design.exists() and raw.exists()


def test_cleaning_the_source_job_never_touches_the_copying_sibling(db, tmp_path, fanpage, cleanup_old_designs):
    source, source_design, source_raw = _job(db, tmp_path, fanpage, published_at=NOW - timedelta(days=8))
    sibling, sibling_design, sibling_copy = _job(db, tmp_path, fanpage, status=PublishJobStatus.pending_publish,
                                                 raw_name="reused")
    # Copies are per job: the sibling's record points at its own file, never at the source's.
    assert ai_image_reuse.owned_reuse_file(sibling) == str(sibling_copy) != str(source_raw)

    cleanup_old_designs()

    assert not source_design.exists() and not source_raw.exists()
    assert sibling_design.exists() and sibling_copy.exists()
    assert _reload(db, sibling.id).design_image_path == str(sibling_design)


def test_owned_reuse_file_skips_foreign_and_design_paths(tmp_path):
    def job(path, design=None):
        return SimpleNamespace(id=5, design_image_path=design, design_analysis_json={
            ai_image_reuse.RECORD_KEY: {"path": path}})

    own = str(tmp_path / "job_5_ai_source_ab.png")
    assert ai_image_reuse.owned_reuse_file(job(own)) == own
    assert ai_image_reuse.owned_reuse_file(job(str(tmp_path / "job_6_ai_source_ab.png"))) is None
    assert ai_image_reuse.owned_reuse_file(job(str(tmp_path / "job_50_ai_source_ab.png"))) is None
    assert ai_image_reuse.owned_reuse_file(job(own, design=own)) is None  # photo edit: the design is the image
    assert ai_image_reuse.owned_reuse_file(SimpleNamespace(id=5, design_image_path=None,
                                                           design_analysis_json=None)) is None
