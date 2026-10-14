import os
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models.ai_video import GflowWorker, GflowAccount, VideoIdea, VideoJob
from app.models.settings import Settings
from app.models.target_fanpages import TargetFanpage, PublishMode
from app.services import ai_video_dispatch
from app.api.deps import get_current_user


@pytest.fixture(autouse=True)
def _no_claim_gap(monkeypatch):
    monkeypatch.setattr(ai_video_dispatch, "MIN_CLAIM_GAP_MINUTES", 0)


@pytest.fixture(scope="module")
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db_session(setup_db):
    session = SessionLocal()
    try:
        session.query(VideoJob).delete()
        session.query(VideoIdea).delete()
        session.query(GflowAccount).delete()
        session.query(GflowWorker).delete()
        session.query(TargetFanpage).filter(TargetFanpage.repliz_account_id == "repliz_fp_unusual").delete()
        session.commit()
        yield session
    finally:
        session.query(VideoJob).delete()
        session.query(VideoIdea).delete()
        session.query(GflowAccount).delete()
        session.query(GflowWorker).delete()
        session.query(TargetFanpage).filter(TargetFanpage.repliz_account_id == "repliz_fp_unusual").delete()
        session.commit()
        session.close()


@pytest.fixture
def test_fanpage(db_session):
    fp = db_session.query(TargetFanpage).filter(TargetFanpage.repliz_account_id == "repliz_fp_unusual").first()
    if not fp:
        fp = TargetFanpage(
            repliz_account_id="repliz_fp_unusual",
            name="F1 Fanpage",
            username="f1fan",
            is_active=True,
            publish_mode=PublishMode.auto,
            ai_video_enabled=True,
        )
        db_session.add(fp)
        db_session.commit()
        db_session.refresh(fp)
    return fp


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: "admin"
    client = TestClient(app)
    yield client
    app.dependency_overrides.pop(get_current_user, None)


def _ensure_settings(db, worker_enabled=True, reserve=0):
    s = db.query(Settings).first()
    if not s:
        s = Settings(id=1, ai_video_worker_enabled=worker_enabled, ai_video_credit_reserve=reserve)
        db.add(s)
    else:
        s.ai_video_worker_enabled = worker_enabled
        s.ai_video_credit_reserve = reserve
    db.commit()


def test_unusual_activity_pauses_worker_and_keeps_counters(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job 1",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}],
        credits_per_clip=15,
        credits_estimate=30,
        status="queued",
    )
    db_session.add_all([worker, acc, job])
    db_session.commit()

    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    claimed_job, _ = claimed

    now = ai_video_dispatch._utcnow()
    ai_video_dispatch.record_failure(db_session, claimed_job, "w1", claimed_job.generation, "unusual_activity", "x")

    assert claimed_job.status == "queued"
    assert claimed_job.unusual_hits == 1
    assert claimed_job.clip_attempts == 0
    assert claimed_job.restarts == 0

    assert claimed_job.not_before is not None
    assert abs((claimed_job.not_before - (now + timedelta(minutes=30))).total_seconds()) < 120

    db_session.refresh(worker)
    assert worker.paused_until is not None
    assert abs((worker.paused_until - (now + timedelta(minutes=30))).total_seconds()) < 120

    # Worker is paused, so claim_job returns None
    assert ai_video_dispatch.claim_job(db_session, "w1") is None


def test_transient_with_unusual_message_is_treated_as_unusual(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job 2",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db_session.add_all([worker, acc, job])
    db_session.commit()

    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    claimed_job, _ = claimed

    msg = "Flow refused the submit: PUBLIC_ERROR_UNUSUAL_ACTIVITY (gRPC 7)"
    ai_video_dispatch.record_failure(db_session, claimed_job, "w1", claimed_job.generation, "transient", msg)

    assert claimed_job.unusual_hits == 1
    assert claimed_job.clip_attempts == 0
    assert claimed_job.status == "queued"


def test_unusual_activity_backoff_then_fails(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job 3",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db_session.add_all([worker, acc, job])
    db_session.commit()

    expected_delays = [30, 60, 120, 120]

    for hit in range(1, 6):
        db_session.refresh(worker)
        db_session.refresh(job)
        worker.paused_until = None
        job.not_before = None
        db_session.commit()

        claimed = ai_video_dispatch.claim_job(db_session, "w1")
        assert claimed is not None
        claimed_job, _ = claimed

        now = ai_video_dispatch._utcnow()
        ai_video_dispatch.record_failure(
            db_session, claimed_job, "w1", claimed_job.generation, "unusual_activity", f"hit {hit}"
        )

        assert claimed_job.unusual_hits == hit
        if hit <= 4:
            assert claimed_job.status == "queued"
            assert claimed_job.not_before is not None
            expected_sec = expected_delays[hit - 1] * 60
            actual_sec = (claimed_job.not_before - now).total_seconds()
            assert abs(actual_sec - expected_sec) < 120
        else:
            assert claimed_job.status == "failed"
            assert claimed_job.finished_at is not None


def test_sticky_account_prefers_most_recently_used(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    now = ai_video_dispatch._utcnow()
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc_a = GflowAccount(
        worker_id="w1", profile="pA", authenticated=True, status="ok", credits=1000,
        last_used_at=now - timedelta(hours=1),
    )
    acc_b = GflowAccount(
        worker_id="w1", profile="pB", authenticated=True, status="ok", credits=1035,
        last_used_at=None,
    )
    acc_c = GflowAccount(
        worker_id="w1", profile="pC", authenticated=True, status="ok", credits=1030,
        last_used_at=now - timedelta(hours=3),
    )
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job Sticky",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=30,
        credits_estimate=30,
        status="queued",
    )
    db_session.add_all([worker, acc_a, acc_b, acc_c, job])
    db_session.commit()

    # 1. Most recently used candidate: acc_a (last_used_at = now-1h vs now-3h vs None)
    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    _, best_acc = claimed
    assert best_acc.id == acc_a.id

    # Reset job and set acc_a credits to 10 (< 30 estimate)
    job.status = "queued"
    job.worker_id = None
    job.account_id = None
    acc_a.credits = 10
    db_session.commit()

    # 2. Next most recently used candidate: acc_c (used before, has credits)
    claimed2 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed2 is not None
    _, best_acc2 = claimed2
    assert best_acc2.id == acc_c.id

    # Reset job and set acc_c credits to 10 (< 30 estimate)
    job.status = "queued"
    job.worker_id = None
    job.account_id = None
    acc_c.credits = 10
    db_session.commit()

    # 3. Fallback to never-used candidate: acc_b
    claimed3 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed3 is not None
    _, best_acc3 = claimed3
    assert best_acc3.id == acc_b.id


def test_claim_gap_blocks_second_claim(db_session, test_fanpage, monkeypatch):
    monkeypatch.setattr(ai_video_dispatch, "MIN_CLAIM_GAP_MINUTES", 10)
    _ensure_settings(db_session, worker_enabled=True)

    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc1 = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    acc2 = GflowAccount(worker_id="w1", profile="p2", authenticated=True, status="ok", credits=100)
    job1 = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job Gap 1",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    job2 = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job Gap 2",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db_session.add_all([worker, acc1, acc2, job1, job2])
    db_session.commit()

    # First claim succeeds
    claimed1 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed1 is not None
    assert claimed1[0].id == job1.id

    # Second claim immediately fails due to claim gap
    claimed2 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed2 is None

    # Set all accounts' last_used_at to 11 minutes ago
    now = ai_video_dispatch._utcnow()
    past = now - timedelta(minutes=11)
    acc1.last_used_at = past
    acc2.last_used_at = past
    db_session.commit()

    # Second claim now succeeds
    claimed3 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed3 is not None
    assert claimed3[0].id == job2.id


def test_complete_clears_error(db_session, test_fanpage, tmp_path, monkeypatch):
    from app.config import get_settings
    import sys

    cfg = get_settings()
    monkeypatch.setattr(cfg, "storage_base_path", str(tmp_path))
    monkeypatch.setattr(cfg, "storage_base_url", "http://test-server/media")

    mock_finalize_module = MagicMock()
    monkeypatch.setitem(sys.modules, "app.tasks.ai_video_finalize", mock_finalize_module)

    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job Complete",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="generating",
        worker_id="w1",
        generation=1,
        error="previous_error: something broke earlier",
    )
    db_session.add(job)
    db_session.commit()

    ai_video_dispatch.complete_job(
        db_session,
        job,
        "w1",
        1,
        b"video_bytes",
        b"thumb_bytes",
        {"duration_s": 10.0},
    )

    assert job.status == "uploaded"
    assert job.error is None


def test_retry_resets_unusual_hits(db_session, test_fanpage):
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job Retry",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="failed",
        unusual_hits=3,
        error="unusual_activity: hit limit",
    )
    db_session.add(job)
    db_session.commit()

    ai_video_dispatch.retry_job(db_session, job)

    assert job.status == "queued"
    assert job.unusual_hits == 0
    assert job.error is None


def test_list_workers_returns_paused_until(db_session, client):
    now = ai_video_dispatch._utcnow()
    paused_dt = now + timedelta(hours=1)
    worker = GflowWorker(
        worker_id="w1",
        last_seen_at=now,
        version="1.0",
        running_job_ids=[],
        paused_until=paused_dt,
    )
    db_session.add(worker)
    db_session.commit()

    res = client.get("/ai-video/workers")
    assert res.status_code == 200
    workers = res.json()
    worker_entry = next((w for w in workers if w["worker_id"] == "w1"), None)
    assert worker_entry is not None
    assert "paused_until" in worker_entry
    assert worker_entry["paused_until"] is not None
