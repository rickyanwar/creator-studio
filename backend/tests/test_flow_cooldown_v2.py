import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.database import Base, SessionLocal, engine
from app.models.ai_video import GflowAccount, GflowWorker, VideoIdea, VideoJob
from app.models.settings import Settings
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services import ai_video_dispatch


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
        session.query(TargetFanpage).filter(TargetFanpage.repliz_account_id == "repliz_fp_flow_v2").delete()
        session.commit()
        yield session
    finally:
        session.query(VideoJob).delete()
        session.query(VideoIdea).delete()
        session.query(GflowAccount).delete()
        session.query(GflowWorker).delete()
        session.query(TargetFanpage).filter(TargetFanpage.repliz_account_id == "repliz_fp_flow_v2").delete()
        session.commit()
        session.close()


@pytest.fixture
def test_fanpage(db_session):
    fp = db_session.query(TargetFanpage).filter(TargetFanpage.repliz_account_id == "repliz_fp_flow_v2").first()
    if not fp:
        fp = TargetFanpage(
            repliz_account_id="repliz_fp_flow_v2",
            name="Flow V2 Fanpage",
            username="flowv2",
            is_active=True,
            publish_mode=PublishMode.auto,
            ai_video_enabled=True,
        )
        db_session.add(fp)
        db_session.commit()
        db_session.refresh(fp)
    return fp


def _ensure_settings(db_session, worker_enabled=True):
    s = db_session.query(Settings).first()
    if not s:
        s = Settings(id=1, ai_video_worker_enabled=worker_enabled, ai_video_credit_reserve=10)
        db_session.add(s)
    else:
        s.ai_video_worker_enabled = worker_enabled
        s.ai_video_credit_reserve = 10
    db_session.commit()


def test_escalating_strikes_and_reset_after_window(db_session):
    """
    1st strike -> cooldown between 60 and 120 min;
    2nd within 24 h -> 120-180 min;
    4th -> 180-240 min;
    a strike older than 24 h resets to 1 (60-120 min).
    """
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    # Provide enough enabled accounts (5 total) so share guard does not refuse cooldown for single account
    acc1 = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=100)
    extras = [
        GflowAccount(worker_id="w1", profile=f"extra_{i}", authenticated=True, status="ok", credits=100)
        for i in range(2, 6)
    ]
    db_session.add_all([worker, acc1, *extras])
    db_session.commit()

    now = ai_video_dispatch._utcnow()

    # 1st strike: 60-120 min
    with patch("random.uniform", return_value=90.0):
        refused = ai_video_dispatch._strike_account(db_session, acc1, now, "strike 1")
    assert refused is False
    assert acc1.unusual_strikes == 1
    assert acc1.last_unusual_at == now
    assert acc1.cooldown_until == now + timedelta(minutes=90)
    assert acc1.last_error == "strike 1"

    # 2nd strike within 24h: 120-180 min
    now_2 = now + timedelta(hours=3)
    with patch("random.uniform", return_value=150.0):
        refused = ai_video_dispatch._strike_account(db_session, acc1, now_2, "strike 2")
    assert refused is False
    assert acc1.unusual_strikes == 2
    assert acc1.last_unusual_at == now_2
    assert acc1.cooldown_until == now_2 + timedelta(minutes=150)

    # 3rd strike within 24h: 180-240 min
    now_3 = now_2 + timedelta(hours=2)
    with patch("random.uniform", return_value=200.0):
        refused = ai_video_dispatch._strike_account(db_session, acc1, now_3, "strike 3")
    assert refused is False
    assert acc1.unusual_strikes == 3
    assert acc1.last_unusual_at == now_3
    assert acc1.cooldown_until == now_3 + timedelta(minutes=200)

    # 4th strike within 24h: 180-240 min
    now_4 = now_3 + timedelta(hours=1)
    with patch("random.uniform", return_value=210.0):
        refused = ai_video_dispatch._strike_account(db_session, acc1, now_4, "strike 4")
    assert refused is False
    assert acc1.unusual_strikes == 4
    assert acc1.last_unusual_at == now_4
    assert acc1.cooldown_until == now_4 + timedelta(minutes=210)

    # Strike older than 24h resets to strike 1: 60-120 min
    now_reset = now_4 + timedelta(hours=25)
    with patch("random.uniform", return_value=75.0):
        refused = ai_video_dispatch._strike_account(db_session, acc1, now_reset, "strike reset")
    assert refused is False
    assert acc1.unusual_strikes == 1
    assert acc1.last_unusual_at == now_reset
    assert acc1.cooldown_until == now_reset + timedelta(minutes=75)


def test_job_backoff_and_max_hits_failure(db_session, test_fanpage, monkeypatch):
    """
    Job backoff: after 1st unusual hit not_before is 25-38 min ahead;
    after 3rd hit the next unusual hit fails the job and idea.
    """
    # Three hits on three accounts inside 30 min would trip the circuit breaker and pause the worker;
    # this test is about the per-job limit, so keep the breaker out of the way.
    monkeypatch.setattr(ai_video_dispatch, "BREAKER_ACCOUNTS", 99)
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    accounts = [
        GflowAccount(worker_id="w1", profile=f"p{i}", authenticated=True, status="ok", credits=100)
        for i in range(1, 6)
    ]
    idea = VideoIdea(fanpage_id=test_fanpage.id, template_key="motorsport_tech_explainer", topic="Topic", status="approved")
    db_session.add_all([worker, *accounts, idea])
    db_session.commit()

    job = VideoJob(
        fanpage_id=test_fanpage.id,
        idea_id=idea.id,
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
    db_session.add(job)
    db_session.commit()

    # 1st unusual hit
    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    claimed_job, _ = claimed

    now = ai_video_dispatch._utcnow()
    ai_video_dispatch.record_failure(db_session, claimed_job, "w1", claimed_job.generation, "unusual_activity", "hit 1")

    assert claimed_job.status == "queued"
    assert claimed_job.unusual_hits == 1
    assert claimed_job.account_id is None
    assert claimed_job.not_before is not None
    # 30 min * (0.85 .. 1.25) -> 25.5 to 37.5 min
    diff_s = (claimed_job.not_before - now).total_seconds()
    assert 25 * 60 <= diff_s <= 38 * 60

    # 2nd unusual hit
    claimed_job.not_before = None
    db_session.commit()
    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    claimed_job, _ = claimed
    ai_video_dispatch.record_failure(db_session, claimed_job, "w1", claimed_job.generation, "unusual_activity", "hit 2")
    assert claimed_job.unusual_hits == 2
    assert claimed_job.status == "queued"

    # 3rd unusual hit
    claimed_job.not_before = None
    db_session.commit()
    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    claimed_job, _ = claimed
    ai_video_dispatch.record_failure(db_session, claimed_job, "w1", claimed_job.generation, "unusual_activity", "hit 3")
    assert claimed_job.unusual_hits == 3
    assert claimed_job.status == "queued"

    # 4th unusual hit (next hit after 3rd) -> fails the job and idea.
    # The 3rd strike hit the 40% share guard (2 of 5 accounts already cooling), which pauses the worker
    # instead of cooling a 3rd account; lift that pause so the per-job limit can be checked.
    db_session.refresh(worker)
    assert worker.paused_until is not None
    worker.paused_until = None
    claimed_job.not_before = None
    db_session.commit()
    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    claimed_job, _ = claimed
    ai_video_dispatch.record_failure(db_session, claimed_job, "w1", claimed_job.generation, "unusual_activity", "hit 4")
    assert claimed_job.unusual_hits == 4
    assert claimed_job.status == "failed"
    assert claimed_job.finished_at is not None

    db_session.refresh(idea)
    assert idea.status == "failed"


def test_job_backoff_with_clips_done_restarts(db_session, test_fanpage):
    """
    Job with clips_done restarts and sets not_before backoff.
    """
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    accounts = [
        GflowAccount(worker_id="w1", profile=f"p{i}", authenticated=True, status="ok", credits=100)
        for i in range(1, 6)
    ]
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job with clips",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}],
        credits_per_clip=15,
        credits_estimate=30,
        status="queued",
    )
    db_session.add_all([worker, *accounts, job])
    db_session.commit()

    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    claimed_job, _ = claimed
    claimed_job.clips_done = [{"index": 1, "video_url": "http://example.com/1.mp4"}]
    db_session.commit()

    now = ai_video_dispatch._utcnow()
    initial_gen = claimed_job.generation
    ai_video_dispatch.record_failure(
        db_session, claimed_job, "w1", claimed_job.generation, "unusual_activity", "no reply within 60s of clicking submit"
    )

    assert claimed_job.status == "queued"
    assert claimed_job.generation == initial_gen + 1
    assert claimed_job.clips_done == []
    assert claimed_job.account_id is None
    assert claimed_job.unusual_hits == 1
    assert claimed_job.not_before is not None
    diff_s = (claimed_job.not_before - now).total_seconds()
    assert 25 * 60 <= diff_s <= 38 * 60


def test_share_guard_pauses_worker_and_refuses_cooldown(db_session):
    """
    Share guard: with 5 enabled accounts and 2 already cooling,
    a 3rd strike sets NO cooldown (60% > 40%) and pauses the worker.
    """
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    now = ai_video_dispatch._utcnow()
    # 5 enabled accounts: 2 already cooling, 3 healthy
    a1 = GflowAccount(worker_id="w1", profile="p1", enabled=True, cooldown_until=now + timedelta(hours=2))
    a2 = GflowAccount(worker_id="w1", profile="p2", enabled=True, cooldown_until=now + timedelta(hours=2))
    a3 = GflowAccount(worker_id="w1", profile="p3", enabled=True, cooldown_until=None)
    a4 = GflowAccount(worker_id="w1", profile="p4", enabled=True, cooldown_until=None)
    a5 = GflowAccount(worker_id="w1", profile="p5", enabled=True, cooldown_until=None)
    db_session.add_all([worker, a1, a2, a3, a4, a5])
    db_session.commit()

    with patch("random.uniform", return_value=45.0):
        refused = ai_video_dispatch._strike_account(db_session, a3, now, "blocked by cloudflare")

    assert refused is True
    # Cooldown should NOT be set on a3
    assert a3.cooldown_until is None
    assert a3.unusual_strikes == 1
    assert a3.last_unusual_at == now
    assert a3.last_error == "blocked by cloudflare"

    # Worker should be paused (between 30 and 60 minutes)
    db_session.flush()
    db_session.refresh(worker)
    assert worker.paused_until is not None
    assert worker.paused_until == now + timedelta(minutes=45)


def test_breaker_distinct_accounts_pauses_worker(db_session):
    """
    Breaker: 3 distinct accounts with last_unusual_at in the last 30 min ->
    worker paused_until 30-60 min ahead.
    """
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    now = ai_video_dispatch._utcnow()
    a1 = GflowAccount(worker_id="w1", profile="p1", enabled=True, last_unusual_at=now - timedelta(minutes=15))
    a2 = GflowAccount(worker_id="w1", profile="p2", enabled=True, last_unusual_at=now - timedelta(minutes=5))
    a3 = GflowAccount(worker_id="w1", profile="p3", enabled=True, last_unusual_at=now)
    a4 = GflowAccount(worker_id="w1", profile="p4", enabled=True, last_unusual_at=None)
    db_session.add_all([worker, a1, a2, a3, a4])
    db_session.commit()

    with patch("random.uniform", return_value=50.0):
        tripped = ai_video_dispatch._maybe_trip_breaker(db_session, "w1", now)

    assert tripped is True
    db_session.flush()
    db_session.refresh(worker)
    assert worker.paused_until is not None
    assert worker.paused_until == now + timedelta(minutes=50)


def test_composer_is_agent_only_rests_account_without_strike(db_session, test_fanpage):
    """
    An agent-only composer is an account property, not anti-bot: flag it, rest it 2-3 h, no strike (2026-10-22).
    """
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    accounts = [
        GflowAccount(worker_id="w1", profile=f"p{i}", authenticated=True, status="ok", credits=100)
        for i in range(1, 6)
    ]
    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Agent Only Job",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db_session.add_all([worker, *accounts, job])
    db_session.commit()

    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    claimed_job, acc = claimed

    now = ai_video_dispatch._utcnow()
    msg = "Flow CLI error: composer is agent-only for this profile"
    ai_video_dispatch.record_failure(db_session, claimed_job, "w1", claimed_job.generation, "other", msg)

    assert claimed_job.unusual_hits in (0, None)
    assert claimed_job.status == "queued"
    assert claimed_job.not_before is None
    assert claimed_job.account_id is None

    db_session.refresh(acc)
    assert acc.agent_only is True
    assert acc.unusual_strikes in (0, None)
    hours = (acc.cooldown_until - now).total_seconds() / 3600
    assert 2 <= hours <= 3.01
    assert "composer is agent-only" in acc.last_error
