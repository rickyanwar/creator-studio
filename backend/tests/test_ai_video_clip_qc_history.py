"""Tests for clip QC history endpoint, list summary, sheet saving, and file retention."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models.ai_video import GflowAccount, GflowWorker, VideoClipQc, VideoJob
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.ai_video_clip_qc import (
    clip_qc_sheet_path,
    logic_check,
)
from app.tasks import ai_video_finalize, cleanup, publisher
from tests.test_ai_video_clip_qc_api import seed_finalize_job


@pytest.fixture(scope="module")
def create_tables():
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def db(create_tables):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: "admin"
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def setup_fanpage(db):
    suffix = os.urandom(8).hex()
    fanpage = TargetFanpage(
        repliz_account_id=f"clip-qc-hist-{suffix}",
        name=f"Clip QC Hist {suffix}",
        is_active=True,
        publish_mode=PublishMode.auto,
        ai_video_enabled=True,
    )
    db.add(fanpage)
    db.commit()
    db.refresh(fanpage)
    return fanpage


def test_clip_qc_sheet_path():
    assert clip_qc_sheet_path("x/job1_g1_c2_a3.mp4") == Path("x/job1_g1_c2_a3_sheet.jpg")


def test_logic_check_saves_sheet(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_video_clip_qc.contact_sheet", lambda path, n=8: b"JPEG")
    monkeypatch.setattr("app.services.ai_video_clip_qc._duration", lambda path: 10.0)
    monkeypatch.setattr("app.services.ai_video_clip_qc.run_vision_check", lambda check, label: [])

    clip_file = tmp_path / "job1_g1_c1_a1.mp4"
    clip_file.write_bytes(b"video-bytes")

    logic_check(str(clip_file), "prompt", None)

    sheet_file = tmp_path / "job1_g1_c1_a1_sheet.jpg"
    assert sheet_file.is_file()
    assert sheet_file.read_bytes() == b"JPEG"


def test_clip_qc_endpoint_and_404(client, db, setup_fanpage, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_video_clip_qc.clip_qc_dir", lambda: tmp_path)

    job = VideoJob(
        fanpage_id=setup_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="History Job",
        caption="Caption",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[{"index": 1, "prompt": "P1"}, {"index": 2, "prompt": "P2"}],
        credits_per_clip=15,
        credits_estimate=30,
        status="claimed",
        generation=2,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # a2 file and its sheet created in tmp_path
    a2_clip = tmp_path / f"job{job.id}_g2_c2_a2.mp4"
    a2_clip.write_bytes(b"clip2_a2")
    a2_sheet = tmp_path / f"job{job.id}_g2_c2_a2_sheet.jpg"
    a2_sheet.write_bytes(b"sheet2_a2")

    rows = [
        # gen 1 clip 1 attempt 1: pass
        VideoClipQc(
            video_job_id=job.id,
            generation=1,
            clip_index=1,
            attempt=1,
            prompt="P1_g1",
            clip_path=str(tmp_path / f"job{job.id}_g1_c1_a1.mp4"),
            status="done",
            verdict="pass",
        ),
        # gen 2 clip 1 attempt 1: pass
        VideoClipQc(
            video_job_id=job.id,
            generation=2,
            clip_index=1,
            attempt=1,
            prompt="P1_g2",
            clip_path=str(tmp_path / f"job{job.id}_g2_c1_a1.mp4"),
            status="done",
            verdict="pass",
        ),
        # gen 2 clip 2 attempt 1: retry
        VideoClipQc(
            video_job_id=job.id,
            generation=2,
            clip_index=2,
            attempt=1,
            prompt="P2_g2",
            clip_path=str(tmp_path / f"job{job.id}_g2_c2_a1.mp4"),
            status="failed",
            verdict="retry",
        ),
        # gen 2 clip 2 attempt 2: retry (file + sheet exist)
        VideoClipQc(
            video_job_id=job.id,
            generation=2,
            clip_index=2,
            attempt=2,
            prompt="P2_g2_retry",
            clip_path=str(a2_clip),
            status="failed",
            verdict="retry",
        ),
        # gen 2 clip 2 attempt 3: use_attempt with use_attempt=2
        VideoClipQc(
            video_job_id=job.id,
            generation=2,
            clip_index=2,
            attempt=3,
            prompt="P2_g2_final",
            clip_path=str(tmp_path / f"job{job.id}_g2_c2_a3.mp4"),
            status="done",
            verdict="use_attempt",
            use_attempt=2,
        ),
    ]
    db.add_all(rows)
    db.commit()

    res = client.get(f"/ai-video/jobs/{job.id}/clip-qc")
    assert res.status_code == 200
    data = res.json()
    assert data["job_id"] == job.id
    assert data["generation"] == 2
    assert data["max_attempts"] == 4

    clips = data["clips"]
    # Order: generation DESC, clip_index ASC -> gen2 clip1, gen2 clip2, gen1 clip1
    assert len(clips) == 3
    assert [c["generation"] for c in clips] == [2, 2, 1]
    assert [c["clip_index"] for c in clips] == [1, 2, 1]

    # gen 1 clip 1: outcome discarded, no used_in_video true
    g1_c1 = clips[2]
    assert g1_c1["generation"] == 1
    assert g1_c1["outcome"] == "discarded"
    assert g1_c1["current_generation"] is False
    assert not any(att["used_in_video"] for att in g1_c1["attempts"])

    # gen 2 clip 2: outcome kept_best, used_attempt 2, only attempt 2 used_in_video
    g2_c2 = clips[1]
    assert g2_c2["generation"] == 2
    assert g2_c2["clip_index"] == 2
    assert g2_c2["current_generation"] is True
    assert g2_c2["outcome"] == "kept_best"
    assert g2_c2["used_attempt"] == 2

    attempts = g2_c2["attempts"]
    assert len(attempts) == 3

    # attempt 1: clip_url is None (file missing)
    assert attempts[0]["attempt"] == 1
    assert attempts[0]["used_in_video"] is False
    assert attempts[0]["clip_url"] is None
    assert attempts[0]["sheet_url"] is None

    # attempt 2: used_in_video True, clip_url/sheet_url end with file names
    assert attempts[1]["attempt"] == 2
    assert attempts[1]["used_in_video"] is True
    assert attempts[1]["clip_url"].endswith(a2_clip.name)
    assert attempts[1]["sheet_url"].endswith(a2_sheet.name)

    # attempt 3: used_in_video False
    assert attempts[2]["attempt"] == 3
    assert attempts[2]["used_in_video"] is False
    assert attempts[2]["clip_url"] is None

    # 404 for unknown job id
    res_404 = client.get("/ai-video/jobs/999999/clip-qc")
    assert res_404.status_code == 404
    assert res_404.json() == {"detail": "Video job not found"}


def test_list_jobs_clip_qc_summary(client, db, setup_fanpage):
    job1 = VideoJob(
        fanpage_id=setup_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Summary Job",
        caption="Caption",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[],
        credits_per_clip=15,
        credits_estimate=30,
        status="claimed",
        generation=2,
    )
    job2 = VideoJob(
        fanpage_id=setup_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="No QC Job",
        caption="Caption",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[],
        credits_per_clip=15,
        credits_estimate=30,
        status="claimed",
        generation=1,
    )
    db.add_all([job1, job2])
    db.commit()
    db.refresh(job1)
    db.refresh(job2)

    rows = [
        # gen 1 row for job1 (should be ignored since job1.generation == 2)
        VideoClipQc(
            video_job_id=job1.id,
            generation=1,
            clip_index=1,
            attempt=2,
            prompt="P_old",
            clip_path="/dummy/path",
            status="failed",
            verdict="retry",
        ),
        # gen 2 rows for job1: clip 1 a1 pass; clip 2 a1 retry, a2 retry, a3 use_attempt
        VideoClipQc(
            video_job_id=job1.id,
            generation=2,
            clip_index=1,
            attempt=1,
            prompt="P1",
            clip_path="/dummy/path",
            status="done",
            verdict="pass",
        ),
        VideoClipQc(
            video_job_id=job1.id,
            generation=2,
            clip_index=2,
            attempt=1,
            prompt="P2_a1",
            clip_path="/dummy/path",
            status="failed",
            verdict="retry",
        ),
        VideoClipQc(
            video_job_id=job1.id,
            generation=2,
            clip_index=2,
            attempt=2,
            prompt="P2_a2",
            clip_path="/dummy/path",
            status="failed",
            verdict="retry",
        ),
        VideoClipQc(
            video_job_id=job1.id,
            generation=2,
            clip_index=2,
            attempt=3,
            prompt="P2_a3",
            clip_path="/dummy/path",
            status="done",
            verdict="use_attempt",
            use_attempt=2,
        ),
    ]
    db.add_all(rows)
    db.commit()

    res = client.get(f"/ai-video/jobs?fanpage_id={setup_fanpage.id}")
    assert res.status_code == 200
    jobs_data = {j["id"]: j for j in res.json()}

    # job1 has {"clips_checked": 2, "retries": 2, "kept_best": 1}
    assert jobs_data[job1.id]["clip_qc_summary"] == {
        "clips_checked": 2,
        "retries": 2,
        "kept_best": 1,
    }
    # job2 has null
    assert jobs_data[job2.id]["clip_qc_summary"] is None


def test_cleanup_old_clip_qc_files(db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_video_clip_qc.clip_qc_dir", lambda: tmp_path)
    monkeypatch.setattr(cleanup, "SessionLocal", lambda: db)

    now = datetime.now(timezone.utc)
    old_mtime = (now - timedelta(days=20)).timestamp()
    fresh_mtime = (now - timedelta(days=2)).timestamp()

    # (a) old done job with published publish job -> deleted
    pj_a = PublishJob(
        fanpage_id=1,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.published,
        design_title="Job A",
    )
    db.add(pj_a)
    db.flush()
    job_a = VideoJob(
        fanpage_id=1,
        template_key="tpl",
        title="Job A",
        caption="Cap",
        model="omni",
        aspect="9:16",
        clip_seconds=4,
        clips=[],
        credits_per_clip=15,
        credits_estimate=15,
        status="done",
        publish_job_id=pj_a.id,
    )
    db.add(job_a)

    # (b) old job whose publish job is pending_review -> kept
    pj_b = PublishJob(
        fanpage_id=1,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_review,
        design_title="Job B",
    )
    db.add(pj_b)
    db.flush()
    job_b = VideoJob(
        fanpage_id=1,
        template_key="tpl",
        title="Job B",
        caption="Cap",
        model="omni",
        aspect="9:16",
        clip_seconds=4,
        clips=[],
        credits_per_clip=15,
        credits_estimate=15,
        status="done",
        publish_job_id=pj_b.id,
    )
    db.add(job_b)

    # (c) old generating job -> kept
    job_c = VideoJob(
        fanpage_id=1,
        template_key="tpl",
        title="Job C",
        caption="Cap",
        model="omni",
        aspect="9:16",
        clip_seconds=4,
        clips=[],
        credits_per_clip=15,
        credits_estimate=15,
        status="generating",
    )
    db.add(job_c)

    # (d) done job with published publish job, but file has fresh mtime -> kept
    pj_d = PublishJob(
        fanpage_id=1,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.published,
        design_title="Job D",
    )
    db.add(pj_d)
    db.flush()
    job_d = VideoJob(
        fanpage_id=1,
        template_key="tpl",
        title="Job D",
        caption="Cap",
        model="omni",
        aspect="9:16",
        clip_seconds=4,
        clips=[],
        credits_per_clip=15,
        credits_estimate=15,
        status="done",
        publish_job_id=pj_d.id,
    )
    db.add(job_d)
    db.commit()

    # Create files
    file_a = tmp_path / f"job{job_a.id}_g1_c1_a1.mp4"
    file_a.write_bytes(b"a")
    os.utime(file_a, (old_mtime, old_mtime))

    file_b = tmp_path / f"job{job_b.id}_g1_c1_a1.mp4"
    file_b.write_bytes(b"b")
    os.utime(file_b, (old_mtime, old_mtime))

    file_c = tmp_path / f"job{job_c.id}_g1_c1_a1.mp4"
    file_c.write_bytes(b"c")
    os.utime(file_c, (old_mtime, old_mtime))

    file_d = tmp_path / f"job{job_d.id}_g1_c1_a1.mp4"
    file_d.write_bytes(b"d")
    os.utime(file_d, (fresh_mtime, fresh_mtime))

    # (e) old file of a job id that does not exist -> deleted
    file_e = tmp_path / "job9999999_g1_c1_a1.mp4"
    file_e.write_bytes(b"e")
    os.utime(file_e, (old_mtime, old_mtime))

    cleanup.cleanup_old_clip_qc_files()

    assert not file_a.exists(), "Old done job files should be deleted"
    assert file_b.exists(), "Pending review job files should be kept"
    assert file_c.exists(), "Generating job files should be kept"
    assert file_d.exists(), "Fresh mtime files should be kept"
    assert not file_e.exists(), "Files for non-existent jobs should be deleted"


def test_finalize_keeps_clip_qc_files(db, tmp_path, monkeypatch):
    job_id = seed_finalize_job(db, tmp_path, accepted_best=False)
    qc_file = tmp_path / f"job{job_id}_g1_c1_a1.mp4"
    qc_file.write_bytes(b"attempt")
    monkeypatch.setattr("app.services.ai_video_clip_qc.clip_qc_dir", lambda: tmp_path)
    technical = {
        "passed": True,
        "reasons": [],
        "duration_s": 4,
        "width": 540,
        "height": 960,
        "has_audio": True,
        "black_s": 0,
        "frozen_s": 0,
        "size_bytes": 300000,
    }
    with patch("app.services.ai_video_qc.technical_check", return_value=technical), patch.object(
        publisher.publish_job, "delay"
    ):
        ai_video_finalize.finalize_ai_video(job_id)
    assert qc_file.is_file(), "Finalize must no longer delete QC files"
