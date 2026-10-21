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
    acc_dummy1 = GflowAccount(worker_id="w1", profile="p_d1", authenticated=False, enabled=True)
    acc_dummy2 = GflowAccount(worker_id="w1", profile="p_d2", authenticated=False, enabled=True)
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
    db_session.add_all([worker, acc, acc_dummy1, acc_dummy2, job])
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
    assert claimed_job.account_id is None
    assert claimed_job.not_before is not None

    db_session.refresh(worker)
    assert worker.paused_until is None  # Worker is NOT paused

    db_session.refresh(acc)
    assert acc.cooldown_until is not None
    assert now + timedelta(minutes=60) <= acc.cooldown_until <= now + timedelta(minutes=120)
    assert acc.last_error == "x"

    # Worker w1 has only acc (cooling), so claim_job returns None
    assert ai_video_dispatch.claim_job(db_session, "w1") is None

    # When another healthy account is available, worker claims the job once not_before allows
    claimed_job.not_before = None
    db_session.commit()
    acc2 = GflowAccount(worker_id="w1", profile="p2", authenticated=True, status="ok", credits=100)
    db_session.add(acc2)
    db_session.commit()
    claimed2 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed2 is not None
    assert claimed2[0].id == claimed_job.id
    assert claimed2[1].id == acc2.id


def test_transient_with_unusual_message_is_treated_as_unusual(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    acc_dummy1 = GflowAccount(worker_id="w1", profile="p_d1", authenticated=False, enabled=True)
    acc_dummy2 = GflowAccount(worker_id="w1", profile="p_d2", authenticated=False, enabled=True)
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
    db_session.add_all([worker, acc, acc_dummy1, acc_dummy2, job])
    db_session.commit()

    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    claimed_job, _ = claimed

    now = ai_video_dispatch._utcnow()
    msg = "Flow refused the submit: PUBLIC_ERROR_UNUSUAL_ACTIVITY (gRPC 7)"
    ai_video_dispatch.record_failure(db_session, claimed_job, "w1", claimed_job.generation, "transient", msg)

    assert claimed_job.unusual_hits == 1
    assert claimed_job.clip_attempts == 0
    assert claimed_job.status == "queued"
    assert claimed_job.account_id is None
    assert claimed_job.not_before is not None

    db_session.refresh(acc)
    assert acc.cooldown_until is not None
    assert now + timedelta(minutes=60) <= acc.cooldown_until <= now + timedelta(minutes=120)

    db_session.refresh(worker)
    assert worker.paused_until is None


def test_unusual_activity_backoff_then_fails(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    acc_dummy1 = GflowAccount(worker_id="w1", profile="p_d1", authenticated=False, enabled=True)
    acc_dummy2 = GflowAccount(worker_id="w1", profile="p_d2", authenticated=False, enabled=True)
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
    db_session.add_all([worker, acc, acc_dummy1, acc_dummy2, job])
    db_session.commit()

    for hit in range(1, 5):
        db_session.refresh(worker)
        db_session.refresh(acc)
        db_session.refresh(job)
        # Clear cooldown so single-account test can reclaim
        acc.cooldown_until = None
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
        db_session.refresh(worker)
        assert worker.paused_until is None
        if hit <= 3:
            assert claimed_job.status == "queued"
            assert claimed_job.account_id is None
            assert claimed_job.not_before is not None
            db_session.refresh(acc)
            assert acc.cooldown_until is not None
        else:
            assert claimed_job.status == "failed"
            assert claimed_job.finished_at is not None


def test_fail_job_submit_no_reply_cooldown_unpinned(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    acc_dummy1 = GflowAccount(worker_id="w1", profile="p_d1", authenticated=False, enabled=True)
    acc_dummy2 = GflowAccount(worker_id="w1", profile="p_d2", authenticated=False, enabled=True)
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job No Reply",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db_session.add_all([worker, acc, acc_dummy1, acc_dummy2, job])
    db_session.commit()

    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    claimed_job, _ = claimed

    now = ai_video_dispatch._utcnow()
    msg = "transient: migrated host: no YhhmEf/eb1hJf/MZZa6b/nprQif reply within 60s of clicking submit (profile afb)"
    ai_video_dispatch.record_failure(db_session, claimed_job, "w1", claimed_job.generation, "transient", msg)

    assert claimed_job.status == "queued"
    assert claimed_job.account_id is None
    assert claimed_job.not_before is not None
    assert claimed_job.unusual_hits == 1

    db_session.refresh(acc)
    assert acc.cooldown_until is not None
    assert now + timedelta(minutes=60) <= acc.cooldown_until <= now + timedelta(minutes=120)
    assert "of clicking submit" in (acc.last_error or "")

    db_session.refresh(worker)
    assert worker.paused_until is None


def test_fail_job_submit_no_reply_with_clips_done_restarts(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    acc_dummy1 = GflowAccount(worker_id="w1", profile="p_d1", authenticated=False, enabled=True)
    acc_dummy2 = GflowAccount(worker_id="w1", profile="p_d2", authenticated=False, enabled=True)
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job With Clips",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}],
        credits_per_clip=15,
        credits_estimate=30,
        status="queued",
    )
    db_session.add_all([worker, acc, acc_dummy1, acc_dummy2, job])
    db_session.commit()

    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    claimed_job, _ = claimed
    initial_gen = claimed_job.generation

    # Simulate 1 clip done
    claimed_job.clips_done = [{"index": 1, "video_url": "http://example.com/1.mp4"}]
    db_session.commit()

    now = ai_video_dispatch._utcnow()
    msg = "transient: no reply within 60s of clicking submit"
    ai_video_dispatch.record_failure(db_session, claimed_job, "w1", claimed_job.generation, "transient", msg)

    assert claimed_job.status == "queued"
    assert claimed_job.generation == initial_gen + 1
    assert claimed_job.clips_done == []
    assert claimed_job.account_id is None
    assert claimed_job.not_before is not None
    assert claimed_job.unusual_hits == 1

    db_session.refresh(acc)
    assert acc.cooldown_until is not None
    assert now + timedelta(minutes=60) <= acc.cooldown_until <= now + timedelta(minutes=120)

    db_session.refresh(worker)
    assert worker.paused_until is None


def test_fail_job_unusual_activity_cooldown_unpinned(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    acc_dummy1 = GflowAccount(worker_id="w1", profile="p_d1", authenticated=False, enabled=True)
    acc_dummy2 = GflowAccount(worker_id="w1", profile="p_d2", authenticated=False, enabled=True)
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job Unusual Activity",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db_session.add_all([worker, acc, acc_dummy1, acc_dummy2, job])
    db_session.commit()

    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    claimed_job, _ = claimed

    now = ai_video_dispatch._utcnow()
    ai_video_dispatch.record_failure(db_session, claimed_job, "w1", claimed_job.generation, "unusual_activity", "blocked by cloudflare")

    assert claimed_job.status == "queued"
    assert claimed_job.account_id is None
    assert claimed_job.not_before is not None
    assert claimed_job.unusual_hits == 1

    db_session.refresh(acc)
    assert acc.cooldown_until is not None
    assert now + timedelta(minutes=60) <= acc.cooldown_until <= now + timedelta(minutes=120)

    db_session.refresh(worker)
    assert worker.paused_until is None


def test_get_candidate_accounts_cooldown_filtering(db_session):
    now = ai_video_dispatch._utcnow()
    acc_cooling = GflowAccount(
        worker_id="w1",
        profile="cooling_prof",
        authenticated=True,
        status="ok",
        credits=100,
        cooldown_until=now + timedelta(hours=2),
    )
    acc_past = GflowAccount(
        worker_id="w1",
        profile="past_prof",
        authenticated=True,
        status="ok",
        credits=100,
        cooldown_until=now - timedelta(minutes=5),
    )
    acc_none = GflowAccount(
        worker_id="w1",
        profile="none_prof",
        authenticated=True,
        status="ok",
        credits=100,
        cooldown_until=None,
    )
    db_session.add_all([acc_cooling, acc_past, acc_none])
    db_session.commit()

    candidates = ai_video_dispatch.get_candidate_accounts(db_session, "w1")
    candidate_profiles = {a.profile for a in candidates}

    assert "cooling_prof" not in candidate_profiles
    assert "past_prof" in candidate_profiles
    assert "none_prof" in candidate_profiles

    # Once cooldown expires, cooling_prof becomes candidate again
    acc_cooling.cooldown_until = now - timedelta(seconds=1)
    db_session.commit()

    candidates2 = ai_video_dispatch.get_candidate_accounts(db_session, "w1")
    candidate_profiles2 = {a.profile for a in candidates2}
    assert "cooling_prof" in candidate_profiles2


def test_plain_transient_keeps_old_retry_backoff(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job Plain Transient",
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

    now = ai_video_dispatch._utcnow()
    ai_video_dispatch.record_failure(db_session, claimed_job, "w1", claimed_job.generation, "transient", "network timeout 504")

    assert claimed_job.status == "queued"
    assert claimed_job.clip_attempts == 1
    assert claimed_job.unusual_hits is None or claimed_job.unusual_hits == 0
    assert claimed_job.not_before is not None
    assert abs((claimed_job.not_before - (now + timedelta(minutes=2))).total_seconds()) < 120
    assert claimed_job.account_id is None  # no clip done yet, so _requeue unpins (unchanged behaviour)

    db_session.refresh(acc)
    assert acc.cooldown_until is None
    db_session.refresh(worker)
    assert worker.paused_until is None


def test_unusual_hits_exceeding_max_fails_job(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job Max Hits",
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

    # Simulate 3 previous unusual hits (max is 3 backoffs)
    job.unusual_hits = 3
    db_session.commit()

    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    claimed_job, _ = claimed

    ai_video_dispatch.record_failure(db_session, claimed_job, "w1", claimed_job.generation, "unusual_activity", "fourth hit")

    assert claimed_job.status == "failed"
    assert claimed_job.unusual_hits == 4
    assert claimed_job.finished_at is not None
    assert claimed_job.worker_id is None
    assert claimed_job.lease_until is None


def test_queued_pinned_to_cooling_account_not_claimed(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    now = ai_video_dispatch._utcnow()
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc_cooling = GflowAccount(
        worker_id="w1", profile="p_cool", authenticated=True, status="ok", credits=100,
        cooldown_until=now + timedelta(hours=6),
    )
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job Pinned Cooling",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
        account_id=None,
    )
    db_session.add_all([worker, acc_cooling, job])
    db_session.commit()

    job.account_id = acc_cooling.id
    db_session.commit()

    # Worker w1 has only acc_cooling which is on cooldown. claim_job should return None.
    assert ai_video_dispatch.claim_job(db_session, "w1") is None


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
    monkeypatch.setattr(ai_video_dispatch.random, "uniform", lambda a, b: a)
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
    job3 = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job Gap 3",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db_session.add_all([worker, acc1, acc2, job1, job2, job3])
    db_session.commit()

    # First claim succeeds on one account, resting it for 8 minutes
    claimed1 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed1 is not None
    assert claimed1[0].id == job1.id
    first_acc = claimed1[1]
    assert first_acc.cooldown_until is not None
    assert abs((first_acc.cooldown_until - first_acc.last_used_at).total_seconds() - 8 * 60) < 5

    # Second claim right away on ANOTHER account succeeds
    claimed2 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed2 is not None
    assert claimed2[0].id == job2.id
    second_acc = claimed2[1]
    assert second_acc.id != first_acc.id
    assert second_acc.cooldown_until is not None
    assert abs((second_acc.cooldown_until - second_acc.last_used_at).total_seconds() - 8 * 60) < 5

    # Third claim fails because all accounts are resting
    claimed3 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed3 is None

    # Free the first account's busy status so it can be claimed again once rested
    claimed1[0].status = "uploaded"
    now = ai_video_dispatch._utcnow()
    first_acc.cooldown_until = now - timedelta(seconds=1)
    db_session.commit()

    claimed4 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed4 is not None
    assert claimed4[0].id == job3.id
    assert claimed4[1].id == first_acc.id


def test_claim_gap_single_account_blocks_until_rested(db_session, test_fanpage, monkeypatch):
    monkeypatch.setattr(ai_video_dispatch, "MIN_CLAIM_GAP_MINUTES", 10)
    monkeypatch.setattr(ai_video_dispatch.random, "uniform", lambda a, b: 8.0)
    _ensure_settings(db_session, worker_enabled=True)

    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    job1 = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Single Acc 1",
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
        title="Single Acc 2",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db_session.add_all([worker, acc, job1, job2])
    db_session.commit()

    claimed1 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed1 is not None
    assert claimed1[0].id == job1.id
    assert claimed1[1].id == acc.id

    # Second claim on the same single account returns None until 10 minutes pass
    claimed2 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed2 is None

    # Free the first job's busy status and expire cooldown
    claimed1[0].status = "uploaded"
    now = ai_video_dispatch._utcnow()
    acc.cooldown_until = now - timedelta(seconds=1)
    db_session.commit()

    claimed3 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed3 is not None
    assert claimed3[0].id == job2.id


def test_worker_random_pause_after_complete_and_failure(db_session, test_fanpage, tmp_path, monkeypatch):
    import sys
    from app.config import get_settings
    cfg = get_settings()
    monkeypatch.setattr(cfg, "storage_base_path", str(tmp_path))
    monkeypatch.setattr(cfg, "storage_base_url", "http://test-server/media")
    monkeypatch.setattr(ai_video_dispatch, "MIN_CLAIM_GAP_MINUTES", 10)
    monkeypatch.setattr(ai_video_dispatch.random, "uniform", lambda a, b: 60.0)
    mock_finalize_module = MagicMock()
    monkeypatch.setitem(sys.modules, "app.tasks.ai_video_finalize", mock_finalize_module)
    _ensure_settings(db_session, worker_enabled=True)

    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    job1 = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Worker Pause Complete",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="claimed",
        worker_id="w1",
        account_id=acc.id,
        generation=1,
    )
    job2 = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Worker Pause Fail",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="claimed",
        worker_id="w1",
        account_id=acc.id,
        generation=1,
    )
    db_session.add_all([worker, acc, job1, job2])
    db_session.commit()

    # 1. Complete job pauses worker ~60s
    ai_video_dispatch.complete_job(
        db_session, job1, "w1", 1, b"video", b"thumb", {"duration_s": 10.0}
    )
    db_session.refresh(worker)
    assert worker.paused_until is not None
    now = ai_video_dispatch._utcnow()
    assert abs((worker.paused_until - now).total_seconds() - 60.0) < 5

    # Worker is paused, so claim_job returns None
    assert ai_video_dispatch.claim_job(db_session, "w1") is None

    # Clear worker pause
    worker.paused_until = now - timedelta(seconds=1)
    db_session.commit()

    # 2. Failure pauses worker ~60s
    ai_video_dispatch.record_failure(db_session, job2, "w1", 1, "transient", "network flake")
    db_session.refresh(worker)
    assert worker.paused_until is not None
    now2 = ai_video_dispatch._utcnow()
    assert abs((worker.paused_until - now2).total_seconds() - 60.0) < 5


def test_min_claim_gap_zero_skips_random_rests(db_session, test_fanpage, tmp_path, monkeypatch):
    import sys
    from app.config import get_settings
    cfg = get_settings()
    monkeypatch.setattr(cfg, "storage_base_path", str(tmp_path))
    monkeypatch.setattr(cfg, "storage_base_url", "http://test-server/media")
    monkeypatch.setattr(ai_video_dispatch, "MIN_CLAIM_GAP_MINUTES", 0)
    mock_finalize_module = MagicMock()
    monkeypatch.setitem(sys.modules, "app.tasks.ai_video_finalize", mock_finalize_module)
    _ensure_settings(db_session, worker_enabled=True)

    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    job1 = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Gap Zero 1",
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
        title="Gap Zero 2",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db_session.add_all([worker, acc, job1, job2])
    db_session.commit()

    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    assert claimed[1].cooldown_until is None

    # Complete job -> no worker pause applied
    ai_video_dispatch.complete_job(
        db_session, claimed[0], "w1", 1, b"video", b"thumb", {"duration_s": 10.0}
    )
    db_session.refresh(worker)
    assert worker.paused_until is None

    # Claim job2 and record failure -> no worker pause applied
    claimed2 = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed2 is not None
    ai_video_dispatch.record_failure(db_session, claimed2[0], "w1", 1, "transient", "flake")
    db_session.refresh(worker)
    assert worker.paused_until is None


def test_sweep_does_not_restart_pinned_job_in_cooldown_gap(db_session, test_fanpage):
    _ensure_settings(db_session, reserve=0)
    now = ai_video_dispatch._utcnow()
    acc = GflowAccount(
        worker_id="w1",
        profile="p1",
        authenticated=True,
        status="ok",
        credits=100,
        cooldown_until=now + timedelta(minutes=10),
        last_used_at=now - timedelta(minutes=5),
    )
    db_session.add(acc)
    db_session.commit()

    job_stale_pinned = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Stale Pinned In Gap",
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
    db_session.add(job_stale_pinned)
    db_session.commit()

    # Sweep should NOT restart the job just because its account has cooldown_until or was used 5m ago
    ai_video_dispatch.sweep(db_session)

    db_session.refresh(job_stale_pinned)
    assert job_stale_pinned.status == "queued"
    assert job_stale_pinned.account_id == acc.id
    assert job_stale_pinned.generation == 1
    assert job_stale_pinned.restarts == 0
    assert len(job_stale_pinned.clips_done) == 1

    # But if the account lacks credits, sweep restarts it
    acc.credits = 0
    db_session.commit()

    ai_video_dispatch.sweep(db_session)
    db_session.refresh(job_stale_pinned)
    assert job_stale_pinned.generation == 2
    assert job_stale_pinned.restarts == 1
    assert job_stale_pinned.account_id is None
    assert len(job_stale_pinned.clips_done) == 0


def test_get_candidate_accounts_respect_claim_gap_param(db_session):
    now = ai_video_dispatch._utcnow()
    acc = GflowAccount(
        worker_id="w1",
        profile="gap_prof",
        authenticated=True,
        status="ok",
        credits=100,
        cooldown_until=now + timedelta(minutes=10),
    )
    db_session.add(acc)
    db_session.commit()

    # With respect_claim_gap=True (default), cooling down account is excluded
    candidates_default = ai_video_dispatch.get_candidate_accounts(db_session, "w1")
    assert acc.id not in [a.id for a in candidates_default]

    # With respect_claim_gap=False, cooling down account is included
    candidates_no_gap = ai_video_dispatch.get_candidate_accounts(db_session, "w1", respect_claim_gap=False)
    assert acc.id in [a.id for a in candidates_no_gap]


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
