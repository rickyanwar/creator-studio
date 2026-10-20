from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine, text, DefaultClause
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"

from app.models.ai_video import GflowWorker, GflowAccount, VideoIdea, VideoJob
from app.models.settings import Settings
from app.models.target_fanpages import TargetFanpage, PublishMode
from app.services import ai_video_dispatch


@pytest.fixture(autouse=True)
def _no_claim_gap(monkeypatch):
    monkeypatch.setattr(ai_video_dispatch, "MIN_CLAIM_GAP_MINUTES", 0)


@pytest.fixture(scope="module")
def sqlite_engine():
    orig_clips_default = VideoJob.__table__.columns["clips_done"].server_default
    VideoJob.__table__.columns["clips_done"].server_default = DefaultClause(text("'[]'"))
    orig_running_default = GflowWorker.__table__.columns["running_job_ids"].server_default
    GflowWorker.__table__.columns["running_job_ids"].server_default = DefaultClause(text("'[]'"))

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        TargetFanpage.__table__,
        Settings.__table__,
        GflowWorker.__table__,
        GflowAccount.__table__,
        VideoIdea.__table__,
        VideoJob.__table__,
    ]
    for table in tables:
        table.create(bind=engine)

    yield engine

    for table in reversed(tables):
        table.drop(bind=engine, checkfirst=True)
    engine.dispose()
    VideoJob.__table__.columns["clips_done"].server_default = orig_clips_default
    GflowWorker.__table__.columns["running_job_ids"].server_default = orig_running_default


@pytest.fixture
def db_session(sqlite_engine):
    Session = sessionmaker(bind=sqlite_engine)
    session = Session()
    try:
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


def _ensure_settings(db, worker_enabled=True, reserve=0):
    s = db.query(Settings).first()
    if not s:
        s = Settings(id=1, ai_video_worker_enabled=worker_enabled, ai_video_credit_reserve=reserve)
        db.add(s)
    else:
        s.ai_video_worker_enabled = worker_enabled
        s.ai_video_credit_reserve = reserve
    db.commit()


def test_effective_credits_clips_after_probe(db_session, test_fanpage):
    # 1. effective_credits: credits 50 checked at T; one job on that account with 2 clips_done at T+5 min and 1 at T−5 min, credits_per_clip 15 → 20.
    t = datetime(2026, 10, 20, 12, 0, 0)
    acc = GflowAccount(
        worker_id="w1",
        profile="acc1",
        authenticated=True,
        status="ok",
        credits=50,
        credits_checked_at=t,
    )
    db_session.add(acc)
    db_session.commit()

    job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="J1",
        caption="C1",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}, {"index": 3}],
        credits_per_clip=15,
        credits_estimate=45,
        account_id=acc.id,
        status="generating",
        clips_done=[
            {"index": 1, "media_id": "m1", "at": (t - timedelta(minutes=5)).isoformat()},
            {"index": 2, "media_id": "m2", "at": (t + timedelta(minutes=5)).isoformat()},
            {"index": 3, "media_id": "m3", "at": (t + timedelta(minutes=5)).isoformat()},
        ],
    )
    db_session.add(job)
    db_session.commit()

    eff = ai_video_dispatch.effective_credits(db_session, acc)
    assert eff == 20


def test_effective_credits_none_handling(db_session):
    # 2. effective_credits: credits_checked_at None → returns credits; credits None → None.
    acc1 = GflowAccount(
        worker_id="w1",
        profile="acc1",
        authenticated=True,
        status="ok",
        credits=50,
        credits_checked_at=None,
    )
    acc2 = GflowAccount(
        worker_id="w1",
        profile="acc2",
        authenticated=True,
        status="ok",
        credits=None,
        credits_checked_at=datetime(2026, 10, 20, 12, 0, 0),
    )
    acc3 = GflowAccount(
        worker_id="w1",
        profile="acc3",
        authenticated=True,
        status="ok",
        credits=None,
        credits_checked_at=None,
    )
    db_session.add_all([acc1, acc2, acc3])
    db_session.commit()

    assert ai_video_dispatch.effective_credits(db_session, acc1) == 50
    assert ai_video_dispatch.effective_credits(db_session, acc2) is None
    assert ai_video_dispatch.effective_credits(db_session, acc3) is None


def test_pinned_job_unpinned_when_insufficient_credits(db_session, test_fanpage):
    # 3. Pinned job (4 clips, 1 done, 15/clip → needs 45) on account A with credits 50 but 2 clips spent after the probe (effective 20)
    # → claim_job does NOT claim it on A. The job gets generation + 1, clips_done == [], account_id None
    # (or it is claimed by account B in the same call, if B has enough credits), and restarts unchanged.
    _ensure_settings(db_session, worker_enabled=True, reserve=0)
    t = datetime(2026, 10, 20, 12, 0, 0)
    acc_a = GflowAccount(
        worker_id="w1",
        profile="acc_a",
        authenticated=True,
        status="ok",
        credits=50,
        credits_checked_at=t,
    )
    db_session.add(acc_a)
    db_session.commit()

    prior_job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Prior Job",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}],
        credits_per_clip=15,
        credits_estimate=30,
        account_id=acc_a.id,
        status="done",
        clips_done=[
            {"index": 1, "media_id": "m1", "at": (t + timedelta(minutes=1)).isoformat()},
            {"index": 2, "media_id": "m2", "at": (t + timedelta(minutes=2)).isoformat()},
        ],
    )
    pinned_job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Pinned Job",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}, {"index": 3}, {"index": 4}],
        clips_done=[{"index": 1, "media_id": "m_done", "at": (t - timedelta(minutes=10)).isoformat()}],
        credits_per_clip=15,
        credits_estimate=60,
        account_id=acc_a.id,
        status="queued",
        generation=1,
        restarts=0,
    )
    db_session.add_all([prior_job, pinned_job])
    db_session.commit()

    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is None

    db_session.refresh(pinned_job)
    assert pinned_job.generation == 2
    assert pinned_job.clips_done == []
    assert pinned_job.clip_attempts == 0
    assert pinned_job.account_id is None
    assert pinned_job.flow_project_id is None
    assert pinned_job.worker_id is None
    assert pinned_job.lease_until is None
    assert pinned_job.restarts == 0
    assert pinned_job.status == "queued"
    assert pinned_job.error is not None
    assert "restarted on another account: acc_a has 20 credits, needs 45" in pinned_job.error


def test_pinned_job_reclaimed_by_account_b(db_session, test_fanpage):
    # 4. Same as 3, but account B (1050 credits, never used) exists → claim_job returns (job, B).
    _ensure_settings(db_session, worker_enabled=True, reserve=0)
    t = datetime(2026, 10, 20, 12, 0, 0)
    acc_a = GflowAccount(
        worker_id="w1",
        profile="acc_a",
        authenticated=True,
        status="ok",
        credits=50,
        credits_checked_at=t,
    )
    acc_b = GflowAccount(
        worker_id="w1",
        profile="acc_b",
        authenticated=True,
        status="ok",
        credits=1050,
        last_used_at=None,
    )
    db_session.add_all([acc_a, acc_b])
    db_session.commit()

    prior_job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Prior Job",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}],
        credits_per_clip=15,
        credits_estimate=30,
        account_id=acc_a.id,
        status="done",
        clips_done=[
            {"index": 1, "media_id": "m1", "at": (t + timedelta(minutes=1)).isoformat()},
            {"index": 2, "media_id": "m2", "at": (t + timedelta(minutes=2)).isoformat()},
        ],
    )
    pinned_job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Pinned Job",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}, {"index": 3}, {"index": 4}],
        clips_done=[{"index": 1, "media_id": "m_done", "at": (t - timedelta(minutes=10)).isoformat()}],
        credits_per_clip=15,
        credits_estimate=60,
        account_id=acc_a.id,
        status="queued",
        generation=1,
        restarts=0,
    )
    db_session.add_all([prior_job, pinned_job])
    db_session.commit()

    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    c_job, c_acc = claimed
    assert c_job.id == pinned_job.id
    assert c_acc.id == acc_b.id
    assert c_job.status == "claimed"
    assert c_job.generation == 2
    assert c_job.restarts == 0
    assert c_job.clips_done == []
    assert c_job.account_id == acc_b.id


def test_pinned_job_claimed_when_sufficient_credits(db_session, test_fanpage):
    # 5. Pinned job where A can afford the rest → claimed on A as before (no regression).
    _ensure_settings(db_session, worker_enabled=True, reserve=0)
    t = datetime(2026, 10, 20, 12, 0, 0)
    acc_a = GflowAccount(
        worker_id="w1",
        profile="acc_a",
        authenticated=True,
        status="ok",
        credits=100,
        credits_checked_at=t,
    )
    db_session.add(acc_a)
    db_session.commit()

    pinned_job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Pinned Job",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}],
        clips_done=[{"index": 1, "media_id": "m1", "at": (t - timedelta(minutes=5)).isoformat()}],
        credits_per_clip=15,
        credits_estimate=30,
        account_id=acc_a.id,
        status="queued",
        generation=1,
        restarts=0,
    )
    db_session.add(pinned_job)
    db_session.commit()

    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    c_job, c_acc = claimed
    assert c_job.id == pinned_job.id
    assert c_acc.id == acc_a.id
    assert c_job.status == "claimed"
    assert c_job.generation == 1
    assert len(c_job.clips_done) == 1
    assert c_job.account_id == acc_a.id


def test_unpinned_job_skips_insufficient_effective_credits(db_session, test_fanpage):
    # 6. Unpinned job (estimate 60): sticky account A has raw credits 70 but effective 40 → A is not chosen; B with 1050 is chosen.
    _ensure_settings(db_session, worker_enabled=True, reserve=0)
    t = datetime(2026, 10, 20, 12, 0, 0)
    acc_a = GflowAccount(
        worker_id="w1",
        profile="acc_a",
        authenticated=True,
        status="ok",
        credits=70,
        credits_checked_at=t,
        last_used_at=t - timedelta(minutes=10),
    )
    acc_b = GflowAccount(
        worker_id="w1",
        profile="acc_b",
        authenticated=True,
        status="ok",
        credits=1050,
        last_used_at=None,
    )
    db_session.add_all([acc_a, acc_b])
    db_session.commit()

    prior_job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Prior Job",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}],
        credits_per_clip=15,
        credits_estimate=30,
        account_id=acc_a.id,
        status="done",
        clips_done=[
            {"index": 1, "media_id": "m1", "at": (t + timedelta(minutes=1)).isoformat()},
            {"index": 2, "media_id": "m2", "at": (t + timedelta(minutes=2)).isoformat()},
        ],
    )
    unpinned_job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Unpinned Job",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}, {"index": 2}, {"index": 3}, {"index": 4}],
        credits_per_clip=15,
        credits_estimate=60,
        account_id=None,
        status="queued",
    )
    db_session.add_all([prior_job, unpinned_job])
    db_session.commit()

    claimed = ai_video_dispatch.claim_job(db_session, "w1")
    assert claimed is not None
    c_job, c_acc = claimed
    assert c_job.id == unpinned_job.id
    assert c_acc.id == acc_b.id
    assert c_job.account_id == acc_b.id
