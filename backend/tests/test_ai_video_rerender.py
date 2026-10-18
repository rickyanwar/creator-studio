"""Tests for AI video re-render overlay functionality (TASK-A1)."""

import json
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, DefaultClause
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"

from app.main import app
from app.api.deps import get_db, get_current_user
from app.models.ai_video import VideoIdea, VideoJob
from app.models.ig_sources import IGSource
from app.models.news_sources import NewsSource
from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import PublishDelivery
from app.models.scraped_articles import ScrapedArticle
from app.models.settings import Settings
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.video_captions import Word
from app.tasks.ai_video_finalize import find_raw_ai_video_path, rerender_ai_video_overlay


@pytest.fixture(scope="module")
def sqlite_engine():
    orig_clips_default = VideoJob.__table__.columns["clips_done"].server_default
    VideoJob.__table__.columns["clips_done"].server_default = DefaultClause(text("'[]'"))

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        TargetFanpage.__table__,
        IGSource.__table__,
        Post.__table__,
        NewsSource.__table__,
        ScrapedArticle.__table__,
        PublishJob.__table__,
        PublishDelivery.__table__,
        VideoIdea.__table__,
        VideoJob.__table__,
        Settings.__table__,
    ]
    for table in tables:
        table.create(bind=engine)

    yield engine

    for table in reversed(tables):
        table.drop(bind=engine, checkfirst=True)
    engine.dispose()
    VideoJob.__table__.columns["clips_done"].server_default = orig_clips_default


@pytest.fixture
def db(sqlite_engine):
    Session = sessionmaker(bind=sqlite_engine)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        # Clean up rows between tests
        session.query(PublishDelivery).delete()
        session.query(PublishJob).delete()
        session.query(VideoJob).delete()
        session.query(VideoIdea).delete()
        session.query(TargetFanpage).delete()
        session.query(Settings).delete()
        session.commit()
        session.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: "testadmin"
    yield TestClient(app)
    app.dependency_overrides.clear()


def _create_fanpage(db, mode=PublishMode.manual_review, logo=True, captions=True):
    fp = TargetFanpage(
        name="TestFanpage",
        ai_video_publish_mode=mode,
        ai_video_logo_enabled=logo,
        ai_video_captions_enabled=captions,
    )
    db.add(fp)
    db.commit()
    db.refresh(fp)
    return fp


# ═══════════════════════════════════════════════════════════════════════════════
# 1. API endpoint tests: POST /publish-jobs/{id}/render-now
# ═══════════════════════════════════════════════════════════════════════════════

def test_render_now_ai_video_not_404_when_raw_exists(db, client, tmp_path):
    """404 is gone for ai_video. When raw exists, enqueues on 'video' queue and returns 200."""
    fp = _create_fanpage(db)
    raw_video = tmp_path / "job_1_abc.mp4"
    raw_video.write_bytes(b"dummy mp4 raw content")

    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_review,
        video_path=str(raw_video),
        video_url="http://localhost/media/ai_videos/job_1_abc.mp4",
        last_error="QC: vision check failed",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    with patch("app.tasks.ai_video_finalize.rerender_ai_video_overlay.apply_async") as mock_apply:
        resp = client.post(f"/publish-jobs/{job.id}/render-now")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == job.id
    assert data["status"] == "pending_design"
    assert data["last_error"] is None

    mock_apply.assert_called_once_with(args=[job.id], queue="video")

    db.refresh(job)
    assert job.status == PublishJobStatus.pending_design
    assert job.last_error is None


def test_render_now_ai_video_409_when_raw_missing(db, client, tmp_path):
    """Returns HTTP 409 when the raw clip file is missing (not 404)."""
    fp = _create_fanpage(db)
    missing_path = tmp_path / "gone_job_99_xyz.mp4"

    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_review,
        video_path=str(missing_path),
        video_url="http://localhost/media/ai_videos/gone_job_99_xyz.mp4",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    with patch("app.tasks.ai_video_finalize.rerender_ai_video_overlay.apply_async") as mock_apply:
        resp = client.post(f"/publish-jobs/{job.id}/render-now")

    assert resp.status_code == 409
    assert resp.json()["detail"] == "The original AI video file is gone; it cannot be re-rendered"
    mock_apply.assert_not_called()


def test_render_now_ai_video_disallowed_status_400(db, client, tmp_path):
    """Disallowed statuses return HTTP 400."""
    fp = _create_fanpage(db)
    raw_video = tmp_path / "job_2_abc.mp4"
    raw_video.write_bytes(b"dummy mp4")

    disallowed = [
        PublishJobStatus.published,
        PublishJobStatus.skipped,
        PublishJobStatus.pending_caption,
        PublishJobStatus.pending_watermark,
        PublishJobStatus.publishing,
    ]

    for st in disallowed:
        job = PublishJob(
            fanpage_id=fp.id,
            content_type=ContentType.ai_video,
            status=st,
            video_path=str(raw_video),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        with patch("app.tasks.ai_video_finalize.rerender_ai_video_overlay.apply_async") as mock_apply:
            resp = client.post(f"/publish-jobs/{job.id}/render-now")

        assert resp.status_code == 400
        assert "Can't re-render an AI video" in resp.json()["detail"]
        mock_apply.assert_not_called()
        db.delete(job)
        db.commit()


def test_render_now_ai_video_all_allowed_statuses(db, client, tmp_path):
    """Allowed statuses (pending_review, pending_publish, failed) all trigger re-render."""
    fp = _create_fanpage(db)
    raw_video = tmp_path / "job_3_abc.mp4"
    raw_video.write_bytes(b"dummy mp4")

    allowed = [
        PublishJobStatus.pending_review,
        PublishJobStatus.pending_publish,
        PublishJobStatus.failed,
    ]

    for st in allowed:
        job = PublishJob(
            fanpage_id=fp.id,
            content_type=ContentType.ai_video,
            status=st,
            video_path=str(raw_video),
            last_error="some error",
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        with patch("app.tasks.ai_video_finalize.rerender_ai_video_overlay.apply_async") as mock_apply:
            resp = client.post(f"/publish-jobs/{job.id}/render-now")

        assert resp.status_code == 200
        mock_apply.assert_called_once_with(args=[job.id], queue="video")
        db.refresh(job)
        assert job.status == PublishJobStatus.pending_design
        assert job.last_error is None

        db.delete(job)
        db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. find_raw_ai_video_path helper tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_find_raw_ai_video_path_direct_and_branded(db, tmp_path):
    fp = _create_fanpage(db)
    raw_file = tmp_path / "job_10_abc.mp4"
    raw_file.write_bytes(b"raw")

    # Direct raw path on publish_job
    pj = PublishJob(fanpage_id=fp.id, content_type=ContentType.ai_video, video_path=str(raw_file))
    db.add(pj)
    db.commit()
    assert find_raw_ai_video_path(pj, db) == raw_file

    # Branded path on publish_job, raw file exists
    branded_file = tmp_path / "job_10_abc_branded_12345678.mp4"
    branded_file.write_bytes(b"branded")
    pj.video_path = str(branded_file)
    db.commit()
    assert find_raw_ai_video_path(pj, db) == raw_file

    # Branded path, but raw file deleted
    raw_file.unlink()
    assert find_raw_ai_video_path(pj, db) is None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Celery task: rerender_ai_video_overlay unit tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_rerender_ai_video_overlay_success_auto_publish(db, tmp_path, sqlite_engine):
    """Rebuilds branded MP4 from raw clip with fanpage overlay settings and auto-publishes if mode=auto."""
    fp = _create_fanpage(db, mode=PublishMode.auto, logo=True, captions=True)

    raw_file = tmp_path / "job_20_xyz.mp4"
    raw_file.write_bytes(b"raw video content")
    raw_thumb = tmp_path / "job_20_xyz.jpg"
    raw_thumb.write_bytes(b"thumb")

    idea = VideoIdea(fanpage_id=fp.id, template_key="motorsport_tech_explainer", topic="F1 Braking", variables={"narration_1": "Hello world"})
    db.add(idea)
    db.flush()

    vj = VideoJob(
        idea_id=idea.id,
        fanpage_id=fp.id,
        template_key="motorsport_tech_explainer",
        title="Title",
        caption="Caption",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[{"index": 1, "mode": "t2v", "prompt": "p"}],
        credits_per_clip=15,
        credits_estimate=15,
        status="done",
        video_path=str(raw_file),
        duration_s=4.0,
        qc={"passed": True, "reasons": []},
    )
    db.add(vj)
    db.flush()

    pj = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_design,
        video_path=str(raw_file),
        video_url="http://localhost/media/ai_videos/job_20_xyz.mp4",
        video_thumbnail_path=str(raw_thumb),
        video_thumbnail_url="http://localhost/media/ai_videos/job_20_xyz.jpg",
        video_duration_s=4.0,
    )
    db.add(pj)
    db.commit()
    vj.publish_job_id = pj.id
    db.commit()

    Session = sessionmaker(bind=sqlite_engine)

    with patch("app.tasks.ai_video_finalize.SessionLocal", side_effect=Session), \
         patch("app.services.speech_words.caption_words", return_value=[Word(0.5, 1.0, "HELLO")]), \
         patch("app.services.ai_video_overlay.render_branded") as mock_render, \
         patch("subprocess.run") as mock_subproc, \
         patch("app.tasks.publisher.publish_job.delay") as mock_pub_delay:

        def fake_render(src, out, *a, **kw):
            Path(out).write_bytes(b"branded bytes")
        mock_render.side_effect = fake_render

        rerender_ai_video_overlay(pj.id)

    db.expire_all()
    refreshed_pj = db.query(PublishJob).filter_by(id=pj.id).first()
    refreshed_vj = db.query(VideoJob).filter_by(id=vj.id).first()

    assert refreshed_pj.status == PublishJobStatus.pending_publish
    assert refreshed_pj.last_error is None
    assert "_branded_" in refreshed_pj.video_path
    assert refreshed_pj.video_thumbnail_path.endswith(".jpg")
    assert raw_file.exists()  # Raw file is kept!

    mock_pub_delay.assert_called_once_with(pj.id)


def test_rerender_ai_video_overlay_manual_review_stays_pending_review(db, tmp_path, sqlite_engine):
    """When fanpage publish mode is manual_review, job goes back to pending_review and does NOT auto-publish."""
    fp = _create_fanpage(db, mode=PublishMode.manual_review, logo=False, captions=False)

    raw_file = tmp_path / "job_21_xyz.mp4"
    raw_file.write_bytes(b"raw video content")

    vj = VideoJob(
        fanpage_id=fp.id,
        template_key="motorsport_tech_explainer",
        title="Title",
        caption="Caption",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[],
        credits_per_clip=15,
        credits_estimate=15,
        status="done",
        video_path=str(raw_file),
        qc={"passed": True, "reasons": []},
    )
    db.add(vj)
    db.flush()

    pj = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_design,
        video_path=str(raw_file),
    )
    db.add(pj)
    db.commit()
    vj.publish_job_id = pj.id
    db.commit()

    Session = sessionmaker(bind=sqlite_engine)

    with patch("app.tasks.ai_video_finalize.SessionLocal", side_effect=Session), \
         patch("subprocess.run"), \
         patch("app.tasks.publisher.publish_job.delay") as mock_pub_delay:
        rerender_ai_video_overlay(pj.id)

    db.expire_all()
    refreshed_pj = db.query(PublishJob).filter_by(id=pj.id).first()
    assert refreshed_pj.status == PublishJobStatus.pending_review
    assert refreshed_pj.last_error is None
    mock_pub_delay.assert_not_called()


def test_rerender_ai_video_overlay_qc_failures_stay_held(db, tmp_path, sqlite_engine):
    """QC failures stay held in pending_review even if fanpage is in auto mode."""
    fp = _create_fanpage(db, mode=PublishMode.auto, logo=True, captions=False)

    raw_file = tmp_path / "job_22_xyz.mp4"
    raw_file.write_bytes(b"raw video")

    vj = VideoJob(
        fanpage_id=fp.id,
        template_key="motorsport_tech_explainer",
        title="Title",
        caption="Caption",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[],
        credits_per_clip=15,
        credits_estimate=15,
        status="done",
        video_path=str(raw_file),
        qc={"passed": False, "reasons": ["incoherent scene geometry"]},
    )
    db.add(vj)
    db.flush()

    pj = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_design,
        video_path=str(raw_file),
    )
    db.add(pj)
    db.commit()
    vj.publish_job_id = pj.id
    db.commit()

    Session = sessionmaker(bind=sqlite_engine)

    with patch("app.tasks.ai_video_finalize.SessionLocal", side_effect=Session), \
         patch("app.services.speech_words.caption_words", return_value=[]), \
         patch("app.services.ai_video_overlay.render_branded") as mock_render, \
         patch("subprocess.run"), \
         patch("app.tasks.publisher.publish_job.delay") as mock_pub_delay:
        mock_render.side_effect = lambda src, out, *a, **k: Path(out).write_bytes(b"branded")
        rerender_ai_video_overlay(pj.id)

    db.expire_all()
    refreshed_pj = db.query(PublishJob).filter_by(id=pj.id).first()
    assert refreshed_pj.status == PublishJobStatus.pending_review
    assert "incoherent scene geometry" in (refreshed_pj.last_error or "")
    mock_pub_delay.assert_not_called()


def test_rerender_ai_video_overlay_missing_raw_sets_failed(db, tmp_path, sqlite_engine):
    """When raw video is missing during task execution, job is marked failed."""
    fp = _create_fanpage(db)

    pj = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_design,
        video_path=str(tmp_path / "nonexistent.mp4"),
    )
    db.add(pj)
    db.commit()

    Session = sessionmaker(bind=sqlite_engine)

    with patch("app.tasks.ai_video_finalize.SessionLocal", side_effect=Session):
        rerender_ai_video_overlay(pj.id)

    db.expire_all()
    refreshed_pj = db.query(PublishJob).filter_by(id=pj.id).first()
    assert refreshed_pj.status == PublishJobStatus.failed
    assert "The original AI video file is gone; it cannot be re-rendered" in refreshed_pj.last_error
