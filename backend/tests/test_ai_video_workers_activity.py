import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models.ai_video import GflowWorker, GflowAccount, VideoIdea, VideoJob
from app.models.target_fanpages import TargetFanpage, PublishMode
from app.services import ai_video_dispatch
from app.api.deps import get_current_user


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
        session.query(TargetFanpage).filter(TargetFanpage.repliz_account_id == "repliz_fp_activity").delete()
        session.commit()
        yield session
    finally:
        session.query(VideoJob).delete()
        session.query(VideoIdea).delete()
        session.query(GflowAccount).delete()
        session.query(GflowWorker).delete()
        session.query(TargetFanpage).filter(TargetFanpage.repliz_account_id == "repliz_fp_activity").delete()
        session.commit()
        session.close()


@pytest.fixture
def test_fanpage(db_session):
    fp = db_session.query(TargetFanpage).filter(TargetFanpage.repliz_account_id == "repliz_fp_activity").first()
    if not fp:
        fp = TargetFanpage(
            repliz_account_id="repliz_fp_activity",
            name="Ayok sehat",
            username="ayoksehat",
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
    c = TestClient(app)
    yield c
    app.dependency_overrides.pop(get_current_user, None)


def test_worker_activity_offline(db_session, client):
    now = _now()
    worker = GflowWorker(
        worker_id="vps-gflow-offline",
        last_seen_at=now - timedelta(minutes=15),
        version="1.0.0",
        max_concurrent=1,
        mem_available_mb=4000,
        running_job_ids=[],
    )
    db_session.add(worker)
    db_session.commit()

    res = client.get("/ai-video/workers")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    w = data[0]
    assert w["online"] is False
    assert w["activity"]["state"] == "offline"
    assert w["activity"]["job"] is None
    assert w["activity"]["resting_until"] is None
    assert w["activity"]["next_account_free_at"] is None
    assert w["activity"]["queued"] == 0


def test_worker_activity_working(db_session, test_fanpage, client):
    now = _now()
    worker = GflowWorker(
        worker_id="vps-gflow-working",
        last_seen_at=now,
        version="1.0.0",
        max_concurrent=1,
        mem_available_mb=3500,
        running_job_ids=[77],
    )
    account = GflowAccount(
        worker_id="vps-gflow-working",
        profile="grazia",
        email="grazia@flow.local",
        enabled=True,
        authenticated=True,
        status="ok",
        credits=100,
    )
    db_session.add_all([worker, account])
    db_session.commit()

    job = VideoJob(
        id=77,
        fanpage_id=test_fanpage.id,
        template_key="tpl1",
        title="Healthy Breakfast Secrets",
        caption="Check out these quick morning recipes!",
        model="veo2",
        aspect="9:16",
        clip_seconds=5,
        clips=[{"prompt": "p1"}, {"prompt": "p2"}, {"prompt": "p3"}, {"prompt": "p4"}],
        credits_per_clip=10,
        credits_estimate=40,
        status="generating",
        account_id=account.id,
        worker_id="vps-gflow-working",
        clips_done=[{"index": 1}, {"index": 2}],
        started_at=now - timedelta(minutes=2),
        last_progress_at=now - timedelta(seconds=20),
    )
    db_session.add(job)
    db_session.commit()

    res = client.get("/ai-video/workers")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    w = data[0]
    assert w["online"] is True
    act = w["activity"]
    assert act["state"] == "working"
    assert act["job"] is not None
    assert act["job"]["id"] == 77
    assert act["job"]["title"] == "Healthy Breakfast Secrets"
    assert act["job"]["fanpage_name"] == "Ayok sehat"
    assert act["job"]["account"] == "grazia"
    assert act["job"]["status"] == "generating"
    assert act["job"]["clips_done"] == 2
    assert act["job"]["clips_total"] == 4
    assert act["job"]["started_at"] is not None
    assert act["job"]["last_progress_at"] is not None
    assert act["resting_until"] is None


def test_worker_activity_resting(db_session, client):
    now = _now()
    paused_until = now + timedelta(minutes=2, seconds=30)
    worker = GflowWorker(
        worker_id="vps-gflow-resting",
        last_seen_at=now,
        version="1.0.0",
        max_concurrent=1,
        mem_available_mb=3800,
        running_job_ids=[],
        paused_until=paused_until,
    )
    db_session.add(worker)
    db_session.commit()

    res = client.get("/ai-video/workers")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    w = data[0]
    assert w["online"] is True
    act = w["activity"]
    assert act["state"] == "resting"
    assert act["job"] is None
    assert act["resting_until"] is not None
    assert act["next_account_free_at"] is None


def test_worker_activity_waiting(db_session, test_fanpage, client):
    now = _now()
    cooldown_target = now + timedelta(minutes=12)
    worker = GflowWorker(
        worker_id="vps-gflow-waiting",
        last_seen_at=now,
        version="1.0.0",
        max_concurrent=1,
        mem_available_mb=3800,
        running_job_ids=[],
    )
    account = GflowAccount(
        worker_id="vps-gflow-waiting",
        profile="runner1",
        email="runner1@flow.local",
        enabled=True,
        authenticated=True,
        status="ok",
        credits=50,
        cooldown_until=cooldown_target,
    )
    queued_job = VideoJob(
        fanpage_id=test_fanpage.id,
        template_key="tpl1",
        title="Queued Topic",
        caption="Queued caption",
        model="veo2",
        aspect="9:16",
        clip_seconds=5,
        clips=[{"prompt": "p1"}],
        credits_per_clip=10,
        credits_estimate=10,
        status="queued",
    )
    db_session.add_all([worker, account, queued_job])
    db_session.commit()

    res = client.get("/ai-video/workers")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    w = data[0]
    assert w["online"] is True
    act = w["activity"]
    assert act["state"] == "waiting"
    assert act["job"] is None
    assert act["queued"] == 1
    assert act["next_account_free_at"] is not None


def test_worker_activity_idle(db_session, client):
    now = _now()
    worker = GflowWorker(
        worker_id="vps-gflow-idle",
        last_seen_at=now,
        version="1.0.0",
        max_concurrent=1,
        mem_available_mb=4000,
        running_job_ids=[],
    )
    db_session.add(worker)
    db_session.commit()

    res = client.get("/ai-video/workers")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    w = data[0]
    assert w["online"] is True
    act = w["activity"]
    assert act["state"] == "idle"
    assert act["job"] is None
    assert act["resting_until"] is None
    assert act["queued"] == 0
    assert act["next_account_free_at"] is None


def test_worker_activity_checking(db_session, client):
    now = _now()
    worker = GflowWorker(
        worker_id="vps-gflow-checking",
        last_seen_at=now,
        version="1.0.0",
        max_concurrent=1,
        mem_available_mb=4000,
        running_job_ids=[],
        phase={"kind": "checking_credits", "done": 2, "total": 5},
    )
    db_session.add(worker)
    db_session.commit()

    res = client.get("/ai-video/workers")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    w = data[0]
    assert w["online"] is True
    act = w["activity"]
    assert act["state"] == "checking"
    assert act["checking"] == {"done": 2, "total": 5}
    assert act["job"] is None


def test_worker_activity_stalled(db_session, test_fanpage, client):
    now = _now()
    lease_time = now + timedelta(minutes=15)
    worker = GflowWorker(
        worker_id="vps-gflow-stalled",
        last_seen_at=now,
        version="1.0.0",
        max_concurrent=1,
        mem_available_mb=4000,
        running_job_ids=[],
    )
    job = VideoJob(
        worker_id="vps-gflow-stalled",
        fanpage_id=test_fanpage.id,
        template_key="tpl1",
        title="Stalled Job",
        caption="Stalled caption",
        model="veo2",
        aspect="9:16",
        clip_seconds=5,
        clips=[{"prompt": "p1"}],
        credits_per_clip=10,
        credits_estimate=10,
        status="claimed",
        started_at=now - timedelta(minutes=1),
        lease_until=lease_time,
    )
    db_session.add_all([worker, job])
    db_session.commit()

    res = client.get("/ai-video/workers")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    w = data[0]
    assert w["online"] is True
    act = w["activity"]
    assert act["state"] == "stalled"
    assert act["job"] is not None
    assert act["job"]["id"] == job.id
    assert act["retry_at"] == lease_time.isoformat()


def test_heartbeat_requeues_orphan_job_after_grace_period(db_session, test_fanpage):
    now = ai_video_dispatch._utcnow()
    worker = GflowWorker(worker_id="w-orphan", last_seen_at=now)
    db_session.add(worker)

    # Job claimed 4 minutes ago, last activity 4 minutes ago
    claimed_4m = VideoJob(
        worker_id="w-orphan",
        fanpage_id=test_fanpage.id,
        template_key="tpl",
        title="Orphan Job",
        caption="cap",
        model="veo2",
        aspect="9:16",
        clip_seconds=5,
        clips=[{"prompt": "p1"}],
        credits_per_clip=10,
        credits_estimate=10,
        status="claimed",
        started_at=now - timedelta(minutes=4),
        last_progress_at=now - timedelta(minutes=4),
        lease_until=now + timedelta(minutes=16),
        clips_done=[],
    )
    # Job claimed 1 minute ago (within 2 minutes)
    claimed_1m = VideoJob(
        worker_id="w-orphan",
        fanpage_id=test_fanpage.id,
        template_key="tpl",
        title="Fresh Job",
        caption="cap",
        model="veo2",
        aspect="9:16",
        clip_seconds=5,
        clips=[{"prompt": "p1"}],
        credits_per_clip=10,
        credits_estimate=10,
        status="claimed",
        started_at=now - timedelta(minutes=1),
        last_progress_at=now - timedelta(minutes=1),
        lease_until=now + timedelta(minutes=19),
        clips_done=[],
    )
    db_session.add_all([claimed_4m, claimed_1m])
    db_session.commit()

    payload = {
        "version": "1.0.0",
        "running_job_ids": [],
        "phase": {"kind": "checking_credits", "done": 1, "total": 2},
    }
    ai_video_dispatch.record_heartbeat(db_session, "w-orphan", payload)
    db_session.refresh(claimed_4m)
    db_session.refresh(claimed_1m)

    # 4 minutes after claim without running_job_ids -> requeued
    assert claimed_4m.status == "queued"
    assert claimed_4m.worker_id is None
    assert claimed_4m.lease_until is None

    # Within 2 minutes (1 min) -> untouched
    assert claimed_1m.status == "claimed"
    assert claimed_1m.worker_id == "w-orphan"

    worker_row = db_session.query(GflowWorker).filter(GflowWorker.worker_id == "w-orphan").first()
    assert worker_row.phase == {"kind": "checking_credits", "done": 1, "total": 2}
