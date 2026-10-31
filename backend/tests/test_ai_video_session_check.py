from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
import pytest
from sqlalchemy import create_engine, text, DefaultClause
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from fastapi.testclient import TestClient

SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"

from app.database import get_db
from app.main import app
from app.models.ai_video import GflowWorker, GflowAccount, VideoIdea, VideoJob
from app.models.api_tokens import ApiToken
from app.models.settings import Settings
from app.models.target_fanpages import TargetFanpage, PublishMode
from app.services import api_tokens, ai_video_dispatch
from app.api.deps import get_current_user


import json, sqlite3
sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)


@pytest.fixture(autouse=True)
def _no_claim_gap(monkeypatch):
    monkeypatch.setattr(ai_video_dispatch, "MIN_CLAIM_GAP_MINUTES", 0)


@pytest.fixture
def mock_alert(monkeypatch):
    mock = MagicMock()
    try:
        import app.tasks.ai_video_alerts as alerts_mod
        monkeypatch.setattr(alerts_mod.send_gflow_alert, "delay", mock)
    except (ImportError, AttributeError):
        pass
    return mock


@pytest.fixture(scope="module")
def sqlite_engine():
    orig_defaults = {}
    time_cols = [
        (VideoJob, "created_at"),
        (GflowAccount, "created_at"),
        (VideoIdea, "created_at"),
        (ApiToken, "created_at"),
    ]
    for model, col in time_cols:
        c = model.__table__.columns[col]
        orig_defaults[(model, col)] = c.server_default
        c.server_default = DefaultClause(text("CURRENT_TIMESTAMP"))

    json_cols = [
        (VideoJob, "clips_done"),
        (GflowWorker, "running_job_ids"),
    ]
    for model, col in json_cols:
        c = model.__table__.columns[col]
        orig_defaults[(model, col)] = c.server_default
        c.server_default = DefaultClause(text("'[]'"))

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
        ApiToken.__table__,
    ]
    for table in tables:
        table.create(bind=engine)

    yield engine

    for table in reversed(tables):
        table.drop(bind=engine, checkfirst=True)
    engine.dispose()
    for (model, col), orig in orig_defaults.items():
        model.__table__.columns[col].server_default = orig


@pytest.fixture
def db_session(sqlite_engine):
    Session = sessionmaker(bind=sqlite_engine)
    session = Session()
    try:
        session.query(VideoJob).delete()
        session.query(VideoIdea).delete()
        session.query(GflowAccount).delete()
        session.query(GflowWorker).delete()
        session.query(ApiToken).delete()
        session.query(Settings).delete()
        session.query(TargetFanpage).delete()
        session.commit()
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def test_fanpage(db_session):
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
    token, plaintext = api_tokens.create_token(db_session, "test_worker_token", ["gflow:worker"])
    return plaintext


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_current_user] = lambda: "admin"
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    yield client
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


# 1. public_landing -> status needs_confirm, authenticated False, session_flagged_at set, one alert queued (message contains profile)
def test_session_check_public_landing_flags_confirm_and_alerts(db_session, mock_alert):
    worker = GflowWorker(worker_id="w1", running_job_ids=[])
    account = GflowAccount(worker_id="w1", profile="destri", email="destri@gmail.com", authenticated=True, status="ok")
    db_session.add_all([worker, account])
    db_session.commit()

    now = datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc)
    req = {"worker_id": "w1", "profile": "destri", "result": "public_landing"}
    status = ai_video_dispatch.record_session_check(db_session, "w1", req, now=now)

    db_session.refresh(account)
    assert status == "needs_confirm"
    assert account.status == "needs_confirm"
    assert account.authenticated is False
    assert account.session_flagged_at is not None
    assert "Google asks to confirm" in account.last_error
    assert mock_alert.call_count == 1
    args, _ = mock_alert.call_args
    assert "destri" in args[0]
    assert "needs you: Google is asking to confirm it's you" in args[0]


# 2. same public_landing again -> no second alert
def test_session_check_public_landing_again_no_second_alert(db_session, mock_alert):
    now = datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc)
    account = GflowAccount(
        worker_id="w1",
        profile="destri",
        authenticated=False,
        status="needs_confirm",
        session_flagged_at=now,
        last_error="Google asks to confirm the account (Flow /about page)",
    )
    db_session.add(account)
    db_session.commit()

    req = {"worker_id": "w1", "profile": "destri", "result": "public_landing"}
    status = ai_video_dispatch.record_session_check(db_session, "w1", req, now=now + timedelta(minutes=5))

    assert status == "needs_confirm"
    assert mock_alert.call_count == 0


# 3. heartbeat with authenticated=true does not reset needs_confirm / needs_login
def test_heartbeat_does_not_reset_needs_confirm_or_login(db_session):
    acc1 = GflowAccount(worker_id="w1", profile="p_confirm", authenticated=False, status="needs_confirm")
    acc2 = GflowAccount(worker_id="w1", profile="p_login", authenticated=False, status="needs_login")
    db_session.add_all([acc1, acc2])
    db_session.commit()

    payload = {
        "version": "1.0",
        "running_job_ids": [],
        "accounts": [
            {"profile": "p_confirm", "authenticated": True, "credits": 50},
            {"profile": "p_login", "authenticated": True, "credits": 60},
        ],
    }
    ai_video_dispatch.record_heartbeat(db_session, "w1", payload)

    db_session.refresh(acc1)
    db_session.refresh(acc2)
    assert acc1.status == "needs_confirm"
    assert acc1.authenticated is False
    assert acc2.status == "needs_login"
    assert acc2.authenticated is False


# 4. legacy renewed with credits=900 -> status ok, authenticated True, credits 900, credits_checked_at set
def test_session_check_renewed_clears_flagged_status(db_session):
    now = datetime(2026, 10, 31, 12, 0, 0)
    acc = GflowAccount(
        worker_id="w1",
        profile="p1",
        authenticated=False,
        status="needs_confirm",
        last_error="Google asks to confirm",
        session_flagged_at=now - timedelta(hours=1),
    )
    db_session.add(acc)
    db_session.commit()

    req = {"worker_id": "w1", "profile": "p1", "result": "renewed", "credits": 900, "email": "p1@flow.test"}
    status = ai_video_dispatch.record_session_check(db_session, "w1", req, now=now)

    db_session.refresh(acc)
    assert status == "ok"
    assert acc.status == "ok"
    assert acc.authenticated is True
    assert acc.last_error is None
    assert acc.credits == 900
    assert acc.credits_checked_at == now
    assert acc.email == "p1@flow.test"


# 5. labs_stale and launch_error are transient -> unchanged, no alert
def test_session_check_other_results(db_session, mock_alert):
    now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    acc1 = GflowAccount(worker_id="w1", profile="p_stale", authenticated=True, status="ok")
    acc2 = GflowAccount(worker_id="w1", profile="p_launch", authenticated=True, status="ok")
    db_session.add_all([acc1, acc2])
    db_session.commit()

    # labs_stale -> no change, no alert (gflow-cli says "retry later")
    req1 = {"worker_id": "w1", "profile": "p_stale", "result": "labs_stale"}
    st1 = ai_video_dispatch.record_session_check(db_session, "w1", req1, now=now)
    db_session.refresh(acc1)
    assert st1 == "ok"
    assert acc1.status == "ok"
    assert acc1.authenticated is True
    assert acc1.session_flagged_at is None
    assert mock_alert.call_count == 0

    # launch_error -> no change, no alert
    req2 = {"worker_id": "w1", "profile": "p_launch", "result": "launch_error"}
    st2 = ai_video_dispatch.record_session_check(db_session, "w1", req2, now=now)
    db_session.refresh(acc2)
    assert st2 == "ok"
    assert acc2.status == "ok"
    assert acc2.authenticated is True
    assert mock_alert.call_count == 0


# 5b. authenticated (gflow-cli 0.92 success) clears needs_login and stores credits
def test_session_check_authenticated_clears_needs_login(db_session, mock_alert):
    now = datetime(2026, 10, 31, 12, 0, 0)
    acc = GflowAccount(
        worker_id="w1",
        profile="p_auth",
        authenticated=False,
        status="needs_login",
        last_error="Session check: no_session",
        session_flagged_at=now - timedelta(hours=2),
    )
    db_session.add(acc)
    db_session.commit()

    req = {"worker_id": "w1", "profile": "p_auth", "result": "authenticated", "credits": 450}
    status = ai_video_dispatch.record_session_check(db_session, "w1", req, now=now)

    db_session.refresh(acc)
    assert status == "ok"
    assert acc.status == "ok"
    assert acc.authenticated is True
    assert acc.last_error is None
    assert acc.credits == 450
    assert acc.credits_checked_at == now
    assert mock_alert.call_count == 0


# 5c. verification_error is transient -> status stays ok, no alert
def test_session_check_verification_error_is_transient(db_session, mock_alert):
    now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    acc = GflowAccount(worker_id="w1", profile="p_verify", authenticated=True, status="ok")
    db_session.add(acc)
    db_session.commit()

    req = {"worker_id": "w1", "profile": "p_verify", "result": "verification_error"}
    status = ai_video_dispatch.record_session_check(db_session, "w1", req, now=now)

    db_session.refresh(acc)
    assert status == "ok"
    assert acc.status == "ok"
    assert acc.authenticated is True
    assert acc.session_flagged_at is None
    assert mock_alert.call_count == 0


# 5d. no_session -> needs_login + alert
def test_session_check_no_session_flags_needs_login(db_session, mock_alert):
    now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    acc = GflowAccount(worker_id="w1", profile="p_gone", authenticated=True, status="ok")
    db_session.add(acc)
    db_session.commit()

    req = {"worker_id": "w1", "profile": "p_gone", "result": "no_session"}
    status = ai_video_dispatch.record_session_check(db_session, "w1", req, now=now)

    db_session.refresh(acc)
    assert status == "needs_login"
    assert acc.status == "needs_login"
    assert acc.authenticated is False
    assert acc.session_flagged_at is not None
    assert mock_alert.call_count == 1
    args, _ = mock_alert.call_args
    assert "is signed out" in args[0]
    assert "Session check: no_session" in args[0]


# 5e. an unknown result value never changes the account or alerts the owner
def test_session_check_unknown_result_is_ignored(db_session, mock_alert):
    now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    acc = GflowAccount(worker_id="w1", profile="p_new", authenticated=True, status="ok")
    db_session.add(acc)
    db_session.commit()

    req = {"worker_id": "w1", "profile": "p_new", "result": "weird_new_value"}
    status = ai_video_dispatch.record_session_check(db_session, "w1", req, now=now)

    db_session.refresh(acc)
    assert status == "ok"
    assert acc.status == "ok"
    assert acc.authenticated is True
    assert acc.session_flagged_at is None
    assert mock_alert.call_count == 0


# 6. three different accounts flagged within 1 h -> worker paused_until >= now + 6h, pause alert queued once; existing longer pause not shortened
def test_pause_rule_three_accounts_within_one_hour(db_session, mock_alert):
    now = datetime(2026, 10, 31, 14, 0, 0)
    worker = GflowWorker(worker_id="w1", running_job_ids=[], paused_until=None)
    acc1 = GflowAccount(worker_id="w1", profile="a1", status="ok", authenticated=True)
    acc2 = GflowAccount(worker_id="w1", profile="a2", status="ok", authenticated=True)
    acc3 = GflowAccount(worker_id="w1", profile="a3", status="ok", authenticated=True)
    db_session.add_all([worker, acc1, acc2, acc3])
    db_session.commit()

    # Flag 1
    ai_video_dispatch.record_session_check(db_session, "w1", {"worker_id": "w1", "profile": "a1", "result": "public_landing"}, now=now)
    db_session.refresh(worker)
    assert worker.paused_until is None

    # Flag 2
    ai_video_dispatch.record_session_check(db_session, "w1", {"worker_id": "w1", "profile": "a2", "result": "no_session"}, now=now + timedelta(minutes=10))
    db_session.refresh(worker)
    assert worker.paused_until is None

    # Flag 3 (threshold reached -> 3 accounts within 1h)
    ai_video_dispatch.record_session_check(db_session, "w1", {"worker_id": "w1", "profile": "a3", "result": "public_landing"}, now=now + timedelta(minutes=20))
    db_session.refresh(worker)
    assert worker.paused_until is not None
    assert worker.paused_until >= now + timedelta(hours=6)

    # Check pause alert was called
    pause_alerts = [call for call in mock_alert.call_args_list if "Flow worker paused for 6 hours" in call[0][0]]
    assert len(pause_alerts) == 1

    # An existing longer pause is not shortened
    longer = now + timedelta(hours=10)
    worker.paused_until = longer
    db_session.commit()

    acc4 = GflowAccount(worker_id="w1", profile="a4", status="ok", authenticated=True)
    db_session.add(acc4)
    db_session.commit()

    ai_video_dispatch.record_session_check(db_session, "w1", {"worker_id": "w1", "profile": "a4", "result": "public_landing"}, now=now + timedelta(minutes=25))
    db_session.refresh(worker)
    assert worker.paused_until == longer


# 7. /jobs/{id}/fail with reason identity_check -> 2xx, job requeued without clip-attempt increment, account needs_confirm
def test_job_fail_identity_check(client, db_session, test_fanpage, worker_token, mock_alert):
    worker = GflowWorker(worker_id="w1", running_job_ids=[1])
    account = GflowAccount(worker_id="w1", profile="p1", status="ok", authenticated=True)
    db_session.add_all([worker, account])
    db_session.commit()

    job = VideoJob(
        id=1,
        fanpage_id=test_fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Test Job",
        caption="Test Caption",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1, "prompt": "p"}],
        credits_per_clip=15,
        credits_estimate=15,
        status="generating",
        worker_id="w1",
        account_id=account.id,
        generation=1,
        clip_attempts=1,
    )
    db_session.add(job)
    db_session.commit()

    res = client.post(
        f"/worker/gflow/jobs/{job.id}/fail",
        headers={"Authorization": f"Bearer {worker_token}"},
        json={
            "worker_id": "w1",
            "generation": 1,
            "reason": "identity_check",
            "message": "Google asks to confirm the account (Flow /about page)",
        },
    )
    assert res.status_code == 200

    db_session.refresh(job)
    db_session.refresh(account)
    assert job.status == "queued"
    assert job.clip_attempts == 1  # Not incremented
    assert job.worker_id is None
    assert account.status == "needs_confirm"
    assert account.authenticated is False
    assert account.session_flagged_at is not None


# 8. /session endpoint via TestClient with worker auth -> 200 and status in body; unknown profile -> {"status": "unknown"}
def test_session_endpoint_auth_and_response(client, db_session, worker_token):
    account = GflowAccount(worker_id="w1", profile="p_auth_test", status="ok", authenticated=True)
    db_session.add(account)
    db_session.commit()

    # 403 when missing auth
    res = client.post("/worker/gflow/session", json={"worker_id": "w1", "profile": "p_auth_test", "result": "public_landing"})
    assert res.status_code == 403

    # 200 with valid worker auth
    res = client.post(
        "/worker/gflow/session",
        headers={"Authorization": f"Bearer {worker_token}"},
        json={"worker_id": "w1", "profile": "p_auth_test", "result": "public_landing"},
    )
    assert res.status_code == 200
    assert res.json() == {"status": "needs_confirm"}

    # Unknown profile -> {"status": "unknown"}
    res = client.post(
        "/worker/gflow/session",
        headers={"Authorization": f"Bearer {worker_token}"},
        json={"worker_id": "w1", "profile": "p_unknown", "result": "public_landing"},
    )
    assert res.status_code == 200
    assert res.json() == {"status": "unknown"}


# 9. heartbeat phase is stored on the worker
def test_heartbeat_phase_stored_on_worker(client, db_session, worker_token):
    worker = GflowWorker(worker_id="w_phase", running_job_ids=[])
    db_session.add(worker)
    db_session.commit()

    res = client.post(
        "/worker/gflow/heartbeat",
        headers={"Authorization": f"Bearer {worker_token}"},
        json={
            "worker_id": "w_phase",
            "version": "1.0",
            "phase": "keepalive_renew",
            "running_job_ids": [],
            "accounts": [],
        },
    )
    assert res.status_code == 200

    db_session.refresh(worker)
    assert worker.phase == "keepalive_renew"
