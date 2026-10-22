from datetime import timedelta
import pytest

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
        session.query(TargetFanpage).filter(TargetFanpage.repliz_account_id == "repliz_fp_usage_limit").delete()
        session.commit()
        yield session
    finally:
        session.query(VideoJob).delete()
        session.query(VideoIdea).delete()
        session.query(GflowAccount).delete()
        session.query(GflowWorker).delete()
        session.query(TargetFanpage).filter(TargetFanpage.repliz_account_id == "repliz_fp_usage_limit").delete()
        session.commit()
        session.close()


@pytest.fixture
def test_fanpage(db_session):
    fp = db_session.query(TargetFanpage).filter(TargetFanpage.repliz_account_id == "repliz_fp_usage_limit").first()
    if not fp:
        fp = TargetFanpage(
            repliz_account_id="repliz_fp_usage_limit",
            name="Usage Limit Fanpage",
            username="usagelimit",
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


def test_job_requires_i2v_unit():
    job_empty = VideoJob(clips=[])
    assert not ai_video_dispatch.job_requires_i2v(job_empty)

    job_t2v_only = VideoJob(clips=[
        {"index": 1, "mode": "t2v"},
        {"index": 2, "mode": "t2v"},
        {"index": 3, "mode": "t2v"},
    ])
    assert not ai_video_dispatch.job_requires_i2v(job_t2v_only)

    job_with_i2v = VideoJob(clips=[
        {"index": 1, "mode": "t2v"},
        {"index": 2, "mode": "i2v"},
    ])
    assert ai_video_dispatch.job_requires_i2v(job_with_i2v)

    job_implicit_mode = VideoJob(clips=[
        {"index": 1},
        {"index": 2},
    ])
    assert ai_video_dispatch.job_requires_i2v(job_implicit_mode)


def test_usage_limit_cooldown_and_no_strikes(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(
        worker_id="w1", profile="herman", authenticated=True, status="ok", credits=150,
        unusual_strikes=0, agent_only=False
    )
    idea = VideoIdea(fanpage_id=test_fanpage.id, template_key="motorsport_tech_explainer", topic="Topic", status="approved")
    db_session.add_all([worker, acc, idea])
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
        clips=[{"index": 1, "mode": "t2v"}],
        credits_per_clip=15,
        credits_estimate=15,
        status="claimed",
        worker_id="w1",
        account_id=acc.id,
        generation=1,
    )
    db_session.add(job)
    db_session.commit()

    before = ai_video_dispatch._utcnow()
    ai_video_dispatch.record_failure(
        db_session,
        job,
        "w1",
        1,
        "usage_limit",
        "Gagal — Anda telah mencapai batas penggunaan. Coba lagi nanti.",
    )
    after = ai_video_dispatch._utcnow()

    db_session.refresh(acc)
    db_session.refresh(job)

    # 6 to 8 hours cooldown
    assert acc.cooldown_until is not None
    assert before + timedelta(hours=6) <= acc.cooldown_until <= after + timedelta(hours=8)
    # No strikes, credits untouched
    assert acc.unusual_strikes == 0
    assert acc.credits == 150
    assert acc.last_unusual_at is None
    # Job requeued with account cleared and not_before None
    assert job.status == "queued"
    assert job.account_id is None
    assert job.not_before is None
    assert job.worker_id is None


def test_usage_limit_with_agent_only_message_sets_flag(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(
        worker_id="w1", profile="herman", authenticated=True, status="ok", credits=120,
        unusual_strikes=0, agent_only=False
    )
    idea = VideoIdea(fanpage_id=test_fanpage.id, template_key="motorsport_tech_explainer", topic="Topic", status="approved")
    db_session.add_all([worker, acc, idea])
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
        clips=[{"index": 1, "mode": "t2v"}],
        credits_per_clip=15,
        credits_estimate=15,
        status="claimed",
        worker_id="w1",
        account_id=acc.id,
        generation=1,
    )
    db_session.add(job)
    db_session.commit()

    msg = (
        "transient: agent-only composer: the agent asked for a second credit confirmation in one run "
        "… Gagal Anda telah mencapai batas penggunaan. Coba lagi nanti."
    )
    before = ai_video_dispatch._utcnow()
    ai_video_dispatch.record_failure(db_session, job, "w1", 1, "usage_limit", msg)
    after = ai_video_dispatch._utcnow()

    db_session.refresh(acc)
    db_session.refresh(job)

    assert acc.agent_only is True
    assert acc.cooldown_until is not None
    assert before + timedelta(hours=6) <= acc.cooldown_until <= after + timedelta(hours=8)
    assert acc.unusual_strikes == 0
    assert acc.credits == 120
    assert job.status == "queued"
    assert job.account_id is None
    assert job.not_before is None


def test_agent_only_cooldown_and_no_strikes(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(
        worker_id="w1", profile="herman", authenticated=True, status="ok", credits=90,
        unusual_strikes=0, agent_only=False
    )
    idea = VideoIdea(fanpage_id=test_fanpage.id, template_key="motorsport_tech_explainer", topic="Topic", status="approved")
    db_session.add_all([worker, acc, idea])
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
        clips=[{"index": 1, "mode": "t2v"}],
        credits_per_clip=15,
        credits_estimate=15,
        status="claimed",
        worker_id="w1",
        account_id=acc.id,
        generation=1,
    )
    db_session.add(job)
    db_session.commit()

    before = ai_video_dispatch._utcnow()
    ai_video_dispatch.record_failure(
        db_session,
        job,
        "w1",
        1,
        "agent_only",
        "agent-only composer: no finished video within 600s of submit (approvals=0)",
    )
    after = ai_video_dispatch._utcnow()

    db_session.refresh(acc)
    db_session.refresh(job)

    assert acc.agent_only is True
    assert acc.cooldown_until is not None
    assert before + timedelta(hours=2) <= acc.cooldown_until <= after + timedelta(hours=3)
    assert acc.unusual_strikes == 0
    assert acc.credits == 90
    assert job.status == "queued"
    assert job.account_id is None
    assert job.not_before is None


def test_transient_with_composer_agent_only_flags_agent_only_and_no_strike(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(
        worker_id="w1", profile="herman", authenticated=True, status="ok", credits=90,
        unusual_strikes=0, agent_only=False
    )
    idea = VideoIdea(fanpage_id=test_fanpage.id, template_key="motorsport_tech_explainer", topic="Topic", status="approved")
    db_session.add_all([worker, acc, idea])
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
        clips=[{"index": 1, "mode": "t2v"}],
        credits_per_clip=15,
        credits_estimate=15,
        status="claimed",
        worker_id="w1",
        account_id=acc.id,
        generation=1,
    )
    db_session.add(job)
    db_session.commit()

    before = ai_video_dispatch._utcnow()
    # Reason is transient, but message has "composer is agent-only"
    ai_video_dispatch.record_failure(
        db_session,
        job,
        "w1",
        1,
        "transient",
        "transient: composer is agent-only: exit 36",
    )
    after = ai_video_dispatch._utcnow()

    db_session.refresh(acc)
    db_session.refresh(job)

    assert acc.agent_only is True
    assert acc.cooldown_until is not None
    assert before + timedelta(hours=2) <= acc.cooldown_until <= after + timedelta(hours=3)
    assert acc.unusual_strikes == 0
    assert job.status == "queued"
    assert job.account_id is None
    assert job.not_before is None


def test_usage_limit_with_clips_done_restarts(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc = GflowAccount(
        worker_id="w1", profile="herman", authenticated=True, status="ok", credits=90,
        unusual_strikes=0, agent_only=False
    )
    idea = VideoIdea(fanpage_id=test_fanpage.id, template_key="motorsport_tech_explainer", topic="Topic", status="approved")
    db_session.add_all([worker, acc, idea])
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
        clips=[{"index": 1, "mode": "t2v"}, {"index": 2, "mode": "i2v"}],
        clips_done=[{"index": 1, "media_id": "m1"}],
        credits_per_clip=15,
        credits_estimate=30,
        status="claimed",
        worker_id="w1",
        account_id=acc.id,
        generation=1,
    )
    db_session.add(job)
    db_session.commit()

    ai_video_dispatch.record_failure(
        db_session,
        job,
        "w1",
        1,
        "usage_limit",
        "You've reached your usage limit. Try again later.",
    )

    db_session.refresh(job)
    assert job.status == "queued"
    assert job.generation == 2
    assert job.restarts == 1
    assert job.clips_done == []
    assert job.account_id is None
    assert job.not_before is None


def test_claim_job_agent_only_eligibility(db_session, test_fanpage):
    _ensure_settings(db_session, worker_enabled=True)
    worker = GflowWorker(worker_id="w1", version="1.0", running_job_ids=[])
    acc_agent_only = GflowAccount(
        worker_id="w1", profile="herman", authenticated=True, status="ok", credits=100,
        agent_only=True
    )
    idea = VideoIdea(fanpage_id=test_fanpage.id, template_key="motorsport_tech_explainer", topic="Topic", status="approved")
    db_session.add_all([worker, acc_agent_only, idea])
    db_session.commit()

    # 3-clip job with i2v clips
    job_i2v = VideoJob(
        fanpage_id=test_fanpage.id,
        idea_id=idea.id,
        template_key="motorsport_tech_explainer",
        title="Job i2v",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[
            {"index": 1, "mode": "t2v"},
            {"index": 2, "mode": "i2v"},
            {"index": 3, "mode": "i2v"},
        ],
        credits_per_clip=15,
        credits_estimate=45,
        status="queued",
    )
    db_session.add(job_i2v)
    db_session.commit()

    # herman is agent_only, so job_i2v should NOT be claimed
    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is None

    # Now add a t2v-only job
    job_t2v = VideoJob(
        fanpage_id=test_fanpage.id,
        idea_id=idea.id,
        template_key="motorsport_tech_explainer",
        title="Job t2v",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[
            {"index": 1, "mode": "t2v"},
            {"index": 2, "mode": "t2v"},
        ],
        credits_per_clip=15,
        credits_estimate=30,
        status="queued",
    )
    db_session.add(job_t2v)
    db_session.commit()

    # herman can claim the t2v job
    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    claimed_job, claimed_acc = claimed
    assert claimed_job.id == job_t2v.id
    assert claimed_acc.id == acc_agent_only.id
