import os
import json
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models.ai_video import GflowWorker, GflowAccount, VideoIdea, VideoJob
from app.models.api_tokens import ApiToken
from app.models.settings import Settings
from app.models.target_fanpages import TargetFanpage, PublishMode
from app.services import api_tokens, ai_video_dispatch
from app.services.ai_video_dispatch import ConflictError
from app.api import gflow_worker as gflow_worker_api
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
        # Clear V2 tables before test
        session.query(VideoJob).delete()
        session.query(VideoIdea).delete()
        session.query(GflowAccount).delete()
        session.query(GflowWorker).delete()
        session.commit()
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def test_fanpage(db_session):
    fp = db_session.query(TargetFanpage).filter(TargetFanpage.id == 1).first()
    if not fp:
        fp = TargetFanpage(
            id=1,
            repliz_account_id="repliz_fp_1",
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
def worker_token(db_session):
    # Create or get an api token with gflow:worker scope
    token, plaintext = api_tokens.create_token(db_session, "test_worker_token", ["gflow:worker"])
    return plaintext


@pytest.fixture
def read_only_token(db_session):
    token, plaintext = api_tokens.create_token(db_session, "test_read_token", ["read"])
    return plaintext


@pytest.fixture
def client():
    # Override CurrentUser dependency to return "admin"
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


def test_heartbeat_upsert(db_session):
    _ensure_settings(db_session, worker_enabled=True, reserve=10)
    payload = {
        "version": "0.83",
        "max_concurrent": 1,
        "mem_available_mb": 4096,
        "running_job_ids": [],
        "accounts": [
            {
                "profile": "prof_1",
                "email": "acc1@gmail.com",
                "authenticated": True,
                "credits": 100,
            },
            {
                "profile": "prof_2",
                "email": "acc2@gmail.com",
                "authenticated": False,
                "error": "No access to Flow",
                "error_class": "FlowAccessUnavailableError",
            },
            {
                "profile": "prof_3",
                "authenticated": False,
                "error": "Session expired",
                "error_class": "AuthError",
            },
        ],
    }

    res = ai_video_dispatch.record_heartbeat(db_session, "worker-1", payload)
    assert res["accept_jobs"] is True
    assert res["credit_reserve"] == 10
    assert "server_time" in res

    worker = db_session.query(GflowWorker).filter(GflowWorker.worker_id == "worker-1").first()
    assert worker is not None
    assert worker.version == "0.83"
    assert worker.mem_available_mb == 4096

    acc1 = db_session.query(GflowAccount).filter(GflowAccount.profile == "prof_1").first()
    assert acc1 is not None
    assert acc1.status == "ok"
    assert acc1.authenticated is True
    assert acc1.credits == 100

    acc2 = db_session.query(GflowAccount).filter(GflowAccount.profile == "prof_2").first()
    assert acc2.status == "no_access"

    acc3 = db_session.query(GflowAccount).filter(GflowAccount.profile == "prof_3").first()
    assert acc3.status == "auth_error"


def test_claim_account_choice_and_reserve(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True, reserve=20)
    # Account 1: 50 credits (50 - 20 = 30)
    acc1 = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=50)
    # Account 2: 80 credits (80 - 20 = 60)
    acc2 = GflowAccount(worker_id="w1", profile="p2", authenticated=True, status="ok", credits=80)
    db_session.add_all([acc1, acc2])
    db_session.commit()

    # Job requiring 45 credits
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job 1",
        caption="Cap 1",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}, {"index": 3}],
        credits_per_clip=15,
        credits_estimate=45,
        status="queued",
    )
    db_session.add(job)
    db_session.commit()

    # Claim: acc1 has 30 effective credits (< 45). acc2 has 60 effective credits (>= 45).
    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    c_job, c_acc = claimed
    assert c_job.id == job.id
    assert c_acc.id == acc2.id
    assert c_job.status == "claimed"
    assert c_job.worker_id == "w1"
    assert c_job.account_id == acc2.id
    assert c_job.lease_until is not None


def test_claim_no_account_has_enough(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True, reserve=20)
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=30) # 30-20=10
    db_session.add(acc)
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job 1",
        caption="Cap 1",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db_session.add(job)
    db_session.commit()

    # Effective credits = 10 < 15, should not claim
    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is None


def test_claim_pause_switch(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=False, reserve=0)
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job 1",
        caption="Cap 1",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db_session.add_all([acc, job])
    db_session.commit()

    assert ai_video_dispatch.claim_job(db_session, "w1") is None


def test_claim_pinned_first(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True, reserve=0)
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=50)
    db_session.add(acc)
    db_session.commit()

    # Unpinned job created earlier
    job_unpinned = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Unpinned Job",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
        created_at=datetime(2026, 1, 1),
    )
    # Pinned job created later, clip 1 done, clip 2 remaining (15 credits)
    job_pinned = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Pinned Job",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}],
        clips_done=[{"index": 1, "media_id": "m1", "at": "2026-01-02"}],
        credits_per_clip=15,
        credits_estimate=30,
        account_id=acc.id,
        status="queued",
        created_at=datetime(2026, 1, 2),
    )
    db_session.add_all([job_unpinned, job_pinned])
    db_session.commit()

    # Pinned job must be claimed first
    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    c_job, c_acc = claimed
    assert c_job.id == job_pinned.id


def test_claim_skips_busy_accounts(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True, reserve=0)
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    db_session.add(acc)
    db_session.commit()

    # Active job on acc
    active_job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Active",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="claimed",
        account_id=acc.id,
        worker_id="w1",
    )
    queued_job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Queued",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db_session.add_all([active_job, queued_job])
    db_session.commit()

    # Cannot claim queued_job with acc because acc is busy
    assert ai_video_dispatch.claim_job(db_session, "w1") is None


def test_claim_two_concurrent_jobs_get_different_accounts(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True, reserve=0)
    acc1 = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=200)
    acc2 = GflowAccount(worker_id="w1", profile="p2", authenticated=True, status="ok", credits=100)
    db_session.add_all([acc1, acc2])
    db_session.commit()

    j1 = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job 1",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    j2 = VideoJob(
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
    db_session.add_all([j1, j2])
    db_session.commit()

    claim1 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claim1 is not None
    job1_claimed, acc1_claimed = claim1
    assert acc1_claimed.id == acc1.id

    claim2 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claim2 is not None
    job2_claimed, acc2_claimed = claim2
    assert acc2_claimed.id == acc2.id
    assert acc1_claimed.id != acc2_claimed.id


def test_claim_single_account_second_claim_returns_none(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True, reserve=0)
    acc1 = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=200)
    db_session.add(acc1)
    db_session.commit()

    j1 = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job 1",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    j2 = VideoJob(
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
    db_session.add_all([j1, j2])
    db_session.commit()

    claim1 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claim1 is not None

    claim2 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claim2 is None


def test_claim_pinned_job_on_busy_account_is_skipped(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True, reserve=0)
    acc_busy = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=200)
    acc_free = GflowAccount(worker_id="w1", profile="p2", authenticated=True, status="ok", credits=100)
    db_session.add_all([acc_busy, acc_free])
    db_session.commit()

    # Active job on acc_busy
    active_job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Active",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="claimed",
        account_id=acc_busy.id,
        worker_id="w1",
    )
    # Pinned job on acc_busy
    pinned_job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Pinned",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
        account_id=acc_busy.id,
    )
    # Unpinned job that can run on acc_free
    unpinned_job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Unpinned",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db_session.add_all([active_job, pinned_job, unpinned_job])
    db_session.commit()

    claim = ai_video_dispatch.claim_job(db_session, "w1")
    assert claim is not None
    c_job, c_acc = claim
    # Pinned job on busy account was skipped, unpinned claimed on acc_free
    assert c_job.id == unpinned_job.id
    assert c_acc.id == acc_free.id



def test_progress_clip_done_and_credits_decrement(db_session, test_fanpage):
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    db_session.add(acc)
    db_session.commit()

    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}],
        credits_per_clip=15,
        credits_estimate=30,
        status="claimed",
        account_id=acc.id,
        worker_id="w1",
        generation=1,
    )
    db_session.add(job)
    db_session.commit()

    # Progress: project event
    ai_video_dispatch.record_progress(db_session, job, "w1", 1, "project", {"flow_project_id": "proj-1"})
    assert job.flow_project_id == "proj-1"

    # Progress: clip 1 done
    ai_video_dispatch.record_progress(db_session, job, "w1", 1, "clip_done", {"clip_index": 1, "media_id": "m1"})
    assert job.status == "generating"
    assert len(job.clips_done) == 1
    assert job.clips_done[0]["media_id"] == "m1"
    db_session.refresh(acc)
    assert acc.credits == 85  # 100 - 15

    # Idempotent repeating clip 1
    ai_video_dispatch.record_progress(db_session, job, "w1", 1, "clip_done", {"clip_index": 1, "media_id": "m1"})
    assert len(job.clips_done) == 1
    db_session.refresh(acc)
    assert acc.credits == 85

    # Wrong clip index (skipping 2, sending 3) -> ConflictError
    with pytest.raises(ConflictError):
        ai_video_dispatch.record_progress(db_session, job, "w1", 1, "clip_done", {"clip_index": 3, "media_id": "m3"})


def test_fail_reasons_and_restarts(db_session, test_fanpage):
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    db_session.add(acc)
    db_session.commit()

    # 1. Policy failure -> failed immediately
    job_policy = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job Policy",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="claimed",
        account_id=acc.id,
        worker_id="w1",
        generation=1,
    )
    db_session.add(job_policy)
    db_session.commit()
    ai_video_dispatch.record_failure(db_session, job_policy, "w1", 1, "policy", "NSFW blocked")
    assert job_policy.status == "failed"
    assert "policy: NSFW blocked" in job_policy.error

    # 2. Out of credits failure -> credits set to 0, requeued
    job_ooc = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job OOC",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="claimed",
        account_id=acc.id,
        worker_id="w1",
        generation=1,
    )
    db_session.add(job_ooc)
    db_session.commit()
    ai_video_dispatch.record_failure(db_session, job_ooc, "w1", 1, "out_of_credits", "No balance")
    assert job_ooc.status == "queued"
    assert job_ooc.worker_id is None
    db_session.refresh(acc)
    assert acc.credits == 0

    # 3. Transient failure backoff and MAX_CLIP_ATTEMPTS -> restart
    job_trans = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job Trans",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="claimed",
        account_id=acc.id,
        worker_id="w1",
        generation=1,
        clip_attempts=0,
    )
    db_session.add(job_trans)
    db_session.commit()

    # Attempt 1: backoff 2 min
    ai_video_dispatch.record_failure(db_session, job_trans, "w1", 1, "transient", "Network blip")
    assert job_trans.status == "queued"
    assert job_trans.clip_attempts == 1
    assert job_trans.not_before is not None

    # Simulate claim again and attempt 2 & 3
    job_trans.status = "claimed"
    job_trans.worker_id = "w1"
    ai_video_dispatch.record_failure(db_session, job_trans, "w1", 1, "transient", "Network blip 2")
    assert job_trans.clip_attempts == 2

    job_trans.status = "claimed"
    job_trans.worker_id = "w1"
    ai_video_dispatch.record_failure(db_session, job_trans, "w1", 1, "transient", "Network blip 3")
    assert job_trans.clip_attempts == 3

    # Attempt 4 (> MAX_CLIP_ATTEMPTS) -> restart
    job_trans.status = "claimed"
    job_trans.worker_id = "w1"
    ai_video_dispatch.record_failure(db_session, job_trans, "w1", 1, "transient", "Network blip 4")
    assert job_trans.status == "queued"
    assert job_trans.generation == 2
    assert job_trans.restarts == 1
    assert job_trans.clip_attempts == 0
    assert job_trans.account_id is None

    # Exceed MAX_RESTARTS (2) -> failed
    job_trans.restarts = 2
    job_trans.clip_attempts = 3
    job_trans.status = "claimed"
    job_trans.worker_id = "w1"
    ai_video_dispatch.record_failure(db_session, job_trans, "w1", 2, "transient", "Network blip 5")
    assert job_trans.status == "failed"
    assert job_trans.restarts == 3


def test_sweep_lease_and_pin_expiry(db_session, test_fanpage):
    _ensure_settings(db_session, reserve=0)
    now = ai_video_dispatch._utcnow()
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=10)
    db_session.add(acc)
    db_session.commit()

    # 1. Expired lease active job -> requeued, keep pin
    job_expired_lease = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Expired Lease",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="claimed",
        account_id=acc.id,
        worker_id="w1",
        lease_until=now - timedelta(minutes=5),
    )

    # 2. Queued pinned with empty clips_done -> unpin
    job_empty_clips_pinned = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Empty Clips Pinned",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
        account_id=acc.id,
        clips_done=[],
    )

    # 3. Queued pinned with non-empty clips_done, last_progress_at > 12h ago, account lacks credits (10 < 15) -> restart
    job_stale_pin = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Stale Pin",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}],
        clips_done=[{"index": 1, "media_id": "m1", "at": "2026-01-01"}],
        credits_per_clip=15,
        credits_estimate=30,
        status="queued",
        account_id=acc.id,
        last_progress_at=now - timedelta(hours=13),
        generation=1,
    )

    db_session.add_all([job_expired_lease, job_empty_clips_pinned, job_stale_pin])
    db_session.commit()

    ai_video_dispatch.sweep(db_session)

    db_session.refresh(job_expired_lease)
    assert job_expired_lease.status == "queued"
    assert job_expired_lease.worker_id is None
    assert job_expired_lease.account_id == acc.id

    db_session.refresh(job_empty_clips_pinned)
    assert job_empty_clips_pinned.account_id is None

    db_session.refresh(job_stale_pin)
    assert job_stale_pin.generation == 2
    assert job_stale_pin.restarts == 1
    assert job_stale_pin.account_id is None
    assert len(job_stale_pin.clips_done) == 0


def test_cancelled_job_finished_at_and_sweep(db_session, test_fanpage):
    _ensure_settings(db_session, reserve=0)
    now = ai_video_dispatch._utcnow()

    # (a) cancel_job sets finished_at = now immediately when job.worker_id is None (queued)
    job_queued = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Queued Cancel",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
        worker_id=None,
    )
    db_session.add(job_queued)
    db_session.commit()

    ai_video_dispatch.cancel_job(db_session, job_queued)
    assert job_queued.status == "cancelled"
    assert job_queued.finished_at is not None

    # (b) cancel_job with worker_id keeps finished_at None initially
    job_claimed = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Claimed Cancel",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="claimed",
        worker_id="w-cancel",
        lease_until=now - timedelta(minutes=1),
    )
    db_session.add(job_claimed)
    db_session.commit()

    ai_video_dispatch.cancel_job(db_session, job_claimed)
    assert job_claimed.status == "cancelled"
    assert job_claimed.finished_at is None
    assert job_claimed.worker_id == "w-cancel"

    # Heartbeat before sweep returns job in cancel_job_ids
    hb_before = ai_video_dispatch.record_heartbeat(db_session, "w-cancel", {"accounts": []})
    assert job_claimed.id in hb_before["cancel_job_ids"]

    # Sweep sets finished_at = now and worker_id = None for cancelled jobs with expired lease
    ai_video_dispatch.sweep(db_session)
    db_session.refresh(job_claimed)
    assert job_claimed.finished_at is not None
    assert job_claimed.worker_id is None

    # Heartbeat stops returning the id after the sweep
    hb_after = ai_video_dispatch.record_heartbeat(db_session, "w-cancel", {"accounts": []})
    assert job_claimed.id not in hb_after["cancel_job_ids"]


def test_complete_job_and_ownership(db_session, test_fanpage, tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from app.config import get_settings
    cfg = get_settings()
    monkeypatch.setattr(cfg, "storage_base_path", str(tmp_path))
    monkeypatch.setattr(cfg, "storage_base_url", "http://test-server/media")

    # Mock finalize task delay so it doesn't try to connect to real redis
    import sys
    mock_finalize_module = MagicMock()
    monkeypatch.setitem(sys.modules, "app.tasks.ai_video_finalize", mock_finalize_module)

    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Complete Job",
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
    )
    db_session.add(job)
    db_session.commit()

    # Wrong worker ownership -> ConflictError
    with pytest.raises(ConflictError):
        ai_video_dispatch.complete_job(
            db_session,
            job,
            "w2",
            1,
            b"fake_video",
            b"fake_thumb",
            {"duration_s": 10.5},
        )

    # Success
    ai_video_dispatch.complete_job(
        db_session,
        job,
        "w1",
        1,
        b"fake_video_data",
        b"fake_thumb_data",
        {"duration_s": 10.5},
    )

    assert job.status == "uploaded"
    assert job.duration_s == 10.5
    assert job.video_url.startswith("http://test-server/media/ai_videos/")
    assert job.video_path.endswith(".mp4")
    assert os.path.exists(job.video_path)
    with open(job.video_path, "rb") as f:
        assert f.read() == b"fake_video_data"


def test_http_auth_and_endpoints(client, db_session, test_fanpage, worker_token, read_only_token, tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    import sys
    mock_finalize_module = MagicMock()
    monkeypatch.setitem(sys.modules, "app.tasks.ai_video_finalize", mock_finalize_module)

    from app.config import get_settings
    cfg = get_settings()
    monkeypatch.setattr(cfg, "storage_base_path", str(tmp_path))

    # 1. Missing token -> 403 (FastAPI HTTPBearer default when Authorization header missing)
    res = client.post("/worker/gflow/heartbeat", json={"worker_id": "w1"})
    assert res.status_code == 403

    # Invalid token -> 401
    res = client.post(
        "/worker/gflow/heartbeat",
        headers={"Authorization": "Bearer hst_invalid"},
        json={"worker_id": "w1"},
    )
    assert res.status_code == 401

    # 2. 403 Forbidden with wrong scope (read instead of gflow:worker)
    res = client.post(
        "/worker/gflow/heartbeat",
        headers={"Authorization": f"Bearer {read_only_token}"},
        json={"worker_id": "w1"},
    )
    assert res.status_code == 403

    # 3. Heartbeat with valid gflow:worker token
    res = client.post(
        "/worker/gflow/heartbeat",
        headers={"Authorization": f"Bearer {worker_token}"},
        json={
            "worker_id": "w1",
            "version": "0.83",
            "accounts": [{"profile": "p1", "authenticated": True, "credits": 50}],
        },
    )
    assert res.status_code == 200
    assert res.json()["accept_jobs"] is True

    # 4. Claim: 204 when no job
    res = client.post(
        "/worker/gflow/claim",
        headers={"Authorization": f"Bearer {worker_token}"},
        json={"worker_id": "w1"},
    )
    assert res.status_code == 204

    # Create a job to claim
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="HTTP Job",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1, "mode": "t2v", "prompt": "prompt"}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db_session.add(job)
    db_session.commit()

    # Claim: 200
    res = client.post(
        "/worker/gflow/claim",
        headers={"Authorization": f"Bearer {worker_token}"},
        json={"worker_id": "w1"},
    )
    assert res.status_code == 200
    claim_data = res.json()["job"]
    assert claim_data["id"] == job.id
    assert claim_data["project_name"] == f"ai-video-{job.id}-g1"

    # Progress
    res = client.post(
        f"/worker/gflow/jobs/{job.id}/progress",
        headers={"Authorization": f"Bearer {worker_token}"},
        json={"worker_id": "w1", "generation": 1, "event": "project", "flow_project_id": "fp_123"},
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True

    # Complete multipart upload & 413 checks
    meta_json = json.dumps({"duration_s": 10.0, "clips": [{"index": 1, "media_id": "m1"}]})

    big_video = b"x" * (gflow_worker_api.MAX_VIDEO_BYTES + 1)
    res = client.post(
        f"/worker/gflow/jobs/{job.id}/complete",
        headers={"Authorization": f"Bearer {worker_token}"},
        data={"worker_id": "w1", "generation": "1", "meta": meta_json},
        files={
            "video": ("video.mp4", big_video, "video/mp4"),
            "thumbnail": ("thumb.jpg", b"thumb", "image/jpeg"),
        },
    )
    assert res.status_code == 413

    big_thumb = b"x" * (gflow_worker_api.MAX_THUMB_BYTES + 1)
    res = client.post(
        f"/worker/gflow/jobs/{job.id}/complete",
        headers={"Authorization": f"Bearer {worker_token}"},
        data={"worker_id": "w1", "generation": "1", "meta": meta_json},
        files={
            "video": ("video.mp4", b"small_video", "video/mp4"),
            "thumbnail": ("thumb.jpg", big_thumb, "image/jpeg"),
        },
    )
    assert res.status_code == 413

    res = client.post(
        f"/worker/gflow/jobs/{job.id}/complete",
        headers={"Authorization": f"Bearer {worker_token}"},
        data={"worker_id": "w1", "generation": "1", "meta": meta_json},
        files={
            "video": ("video.mp4", b"small_video", "video/mp4"),
            "thumbnail": ("thumb.jpg", b"small_thumb", "image/jpeg"),
        },
    )
    assert res.status_code == 200
    assert res.json()["status"] == "uploaded"

    # Admin endpoints
    res = client.get("/ai-video/jobs")
    assert res.status_code == 200
    jobs = res.json()
    assert len(jobs) >= 1
    assert jobs[0]["id"] == job.id

    res = client.get("/ai-video/accounts")
    assert res.status_code == 200
    accounts = res.json()
    assert len(accounts) >= 1
    acc_id = accounts[0]["id"]

    res = client.patch(f"/ai-video/accounts/{acc_id}", json={"label": "Primary Mac Profile"})
    assert res.status_code == 200
    assert res.json()["label"] == "Primary Mac Profile"

    res = client.get("/ai-video/workers")
    assert res.status_code == 200
    workers = res.json()
    assert len(workers) >= 1
    assert workers[0]["worker_id"] == "w1"
    assert workers[0]["online"] is True


def test_upload_terms_marks_account_and_restarts(db_session, test_fanpage):
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    db_session.add(acc)
    db_session.commit()

    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job Upload Terms",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}],
        credits_per_clip=15,
        credits_estimate=30,
        status="claimed",
        account_id=acc.id,
        worker_id="w1",
        generation=1,
        clips_done=[{"index": 1, "media_id": "m1"}],
        clip_attempts=0,
        restarts=0,
    )
    db_session.add(job)
    db_session.commit()

    ai_video_dispatch.record_failure(
        db_session,
        job,
        "w1",
        1,
        "upload_terms",
        "migrated host: ... most likely Flow's one-time upload-terms confirmation (host=migrated)",
    )
    db_session.refresh(acc)
    db_session.refresh(job)

    assert acc.status == "needs_terms"
    assert "upload-terms" in (acc.last_error or "")
    assert job.status == "queued"
    assert job.account_id is None
    assert job.restarts == 1
    assert job.clips_done == []


def test_heartbeat_keeps_needs_terms(db_session):
    acc_terms = GflowAccount(
        worker_id="w1",
        profile="p_terms",
        authenticated=True,
        status="needs_terms",
        last_error="Flow upload terms not accepted",
        credits=50,
    )
    acc_auth = GflowAccount(
        worker_id="w1",
        profile="p_auth",
        authenticated=False,
        status="auth_error",
        last_error="Session expired",
        credits=50,
    )
    db_session.add_all([acc_terms, acc_auth])
    db_session.commit()

    payload = {
        "accounts": [
            {
                "profile": "p_terms",
                "authenticated": True,
                "credits": 50,
                "error": "some transient warning",
            },
            {
                "profile": "p_auth",
                "authenticated": True,
                "credits": 50,
            },
        ],
    }
    ai_video_dispatch.record_heartbeat(db_session, "w1", payload)
    db_session.refresh(acc_terms)
    db_session.refresh(acc_auth)

    assert acc_terms.status == "needs_terms"
    assert acc_terms.last_error == "Flow upload terms not accepted"
    assert acc_auth.status == "ok"


def test_claim_skips_needs_terms_account(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True, reserve=0)
    acc = GflowAccount(
        worker_id="w1",
        profile="p1",
        authenticated=True,
        status="needs_terms",
        credits=100,
    )
    db_session.add(acc)
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job 1",
        caption="Cap 1",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db_session.add(job)
    db_session.commit()

    candidates = ai_video_dispatch.get_candidate_accounts(db_session, "w1")
    assert acc not in candidates
    assert candidates == []

    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is None
