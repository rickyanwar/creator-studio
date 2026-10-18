from datetime import datetime, timezone, timedelta
import bcrypt
import pytest
from alembic.command import upgrade, downgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db, get_current_user
from app.database import Base
import app.models as models
from app.models.auth_events import AuthEvent
from app.models.log_handled import LogHandled
from app.models.users import User
from app.models.target_fanpages import TargetFanpage
from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
from app.models.ai_copy_events import AICopyEvent
from app.main import app


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


# ── Alembic & Metadata Tests ──────────────────────────────────────────────────

def test_alembic_single_head_and_revision():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1
    assert script.get_revision("c80000000029") is not None

    rev29 = script.get_revision("c80000000029")
    assert rev29.down_revision == "c80000000028"
    assert hasattr(rev29.module, "upgrade")
    assert hasattr(rev29.module, "downgrade")


def test_models_metadata_and_export():
    expected_tables = {"log_handled", "auth_events"}
    assert expected_tables.issubset(Base.metadata.tables.keys())

    assert "LogHandled" in models.__all__
    assert getattr(models, "LogHandled") is LogHandled
    assert "AuthEvent" in models.__all__
    assert getattr(models, "AuthEvent") is AuthEvent

    # Check unique constraint on log_handled
    lh = LogHandled.__table__
    uniques = {u.name for u in lh.constraints if hasattr(u, "columns")}
    assert "uq_log_handled_kind_id" in uniques


def test_migration_offline_sql(capsys):
    config = Config("alembic.ini")

    upgrade(config, "c80000000028:c80000000029", sql=True)
    up_sql, _ = capsys.readouterr()
    assert "CREATE TABLE log_handled" in up_sql
    assert "CREATE TABLE auth_events" in up_sql

    downgrade(config, "c80000000029:c80000000028", sql=True)
    down_sql, _ = capsys.readouterr()
    assert "DROP TABLE auth_events;" in down_sql
    assert "DROP TABLE log_handled;" in down_sql


# ── Fixtures for API Tests ────────────────────────────────────────────────────

@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(bind=engine)
    TargetFanpage.__table__.create(bind=engine)
    PublishJob.__table__.create(bind=engine)
    AICopyEvent.__table__.create(bind=engine)
    AuthEvent.__table__.create(bind=engine)
    LogHandled.__table__.create(bind=engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    pw_hash = bcrypt.hashpw(b"secret123", bcrypt.gensalt()).decode()
    user = User(username="admin", password_hash=pw_hash, is_active=True)
    session.add(user)
    session.commit()

    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    user = db_session.query(User).filter_by(username="admin").first()
    app.dependency_overrides[get_current_user] = lambda: user
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── Auth Event Recording Tests ────────────────────────────────────────────────

def test_login_success_records_auth_event(client, db_session):
    res = client.post(
        "/auth/login",
        json={"username": "admin", "password": "secret123"},
        headers={"user-agent": "TestBrowser/1.0"},
    )
    assert res.status_code == 200

    events = db_session.query(AuthEvent).all()
    assert len(events) == 1
    ev = events[0]
    assert ev.username == "admin"
    assert ev.success is True
    assert ev.user_agent == "TestBrowser/1.0"
    assert ev.ip is not None or ev.ip == "testclient"


def test_login_failed_records_auth_event(client, db_session):
    res = client.post(
        "/auth/login",
        json={"username": "admin", "password": "wrongpassword"},
        headers={"user-agent": "BadBot/2.0"},
    )
    assert res.status_code == 401

    events = db_session.query(AuthEvent).all()
    assert len(events) == 1
    ev = events[0]
    assert ev.username == "admin"
    assert ev.success is False
    assert ev.user_agent == "BadBot/2.0"


def test_login_continues_if_auth_event_fails(client, db_session, monkeypatch):
    from app.api import auth

    def broken_record(*args, **kwargs):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(auth, "_record_auth_event", broken_record)

    res = client.post(
        "/auth/login",
        json={"username": "admin", "password": "secret123"},
    )
    assert res.status_code == 200
    assert "access_token" in res.json()


# ── Logs Handled & Category Access Tests ──────────────────────────────────────

def test_logs_entries_handled_and_access(client, db_session):
    now = datetime.now(timezone.utc)

    # 1. Target fanpages
    fp1 = TargetFanpage(name="Formula News", is_active=True)
    fp2 = TargetFanpage(name="Moto Racing", is_active=True)
    db_session.add_all([fp1, fp2])
    db_session.commit()

    # 2. Failed publish job
    job = PublishJob(
        fanpage_id=fp1.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.failed,
        last_error="Image download failed (404)",
        updated_at=now,
    )
    db_session.add(job)

    # 3. Degraded AI event
    ai_ev = AICopyEvent(
        context="news_copy",
        fanpage_id=fp1.id,
        outcome="recovered",
        models_tried="primary/model",
        final_provider="fallback/model",
        error_message="Primary timed out",
        created_at=now,
    )
    db_session.add(ai_ev)

    # 4. Auth events
    auth_fail = AuthEvent(
        username="hacker",
        success=False,
        ip="192.168.1.100",
        user_agent="curl/7.68.0",
        created_at=now,
    )
    auth_ok = AuthEvent(
        username="admin",
        success=True,
        ip="127.0.0.1",
        user_agent="Mozilla/5.0",
        created_at=now,
    )
    db_session.add_all([auth_fail, auth_ok])
    db_session.commit()

    # GET /logs with category=all
    res = client.get("/logs?category=all")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 4
    assert data["error_count"] == 1  # 1 failed job
    assert data["warning_count"] == 2  # 1 AI recovered + 1 failed login

    # Check stable kind + id + handled flag
    items_by_id = {i["id"]: i for i in data["logs"]}
    job_item = items_by_id[f"job_failed_{job.id}"]
    assert job_item["kind"] == "job"
    assert job_item["entry_kind"] == "job"
    assert job_item["entry_id"] == job.id
    assert job_item["handled"] is False

    ai_item = items_by_id[f"ai_recovered_{ai_ev.id}"]
    assert ai_item["kind"] == "ai"
    assert ai_item["entry_kind"] == "ai"
    assert ai_item["entry_id"] == ai_ev.id
    assert ai_item["handled"] is False

    auth_item = items_by_id[f"auth_{auth_fail.id}"]
    assert auth_item["kind"] == "auth"
    assert auth_item["entry_kind"] == "auth"
    assert auth_item["entry_id"] == auth_fail.id
    assert auth_item["category"] == "access"
    assert auth_item["severity"] == "warning"
    assert auth_item["handled"] is False

    # POST /logs/handled
    mark_res = client.post(
        "/logs/handled",
        json={"entry_kind": "job", "entry_id": job.id},
    )
    assert mark_res.status_code == 200
    assert mark_res.json()["handled"] is True

    # Check idempotent mark handled
    mark_again = client.post(
        "/logs/handled",
        json={"entry_kind": "job", "entry_id": job.id},
    )
    assert mark_again.status_code == 200

    # GET /logs should now show handled=True and error_count decremented
    res2 = client.get("/logs?category=all")
    data2 = res2.json()
    assert data2["error_count"] == 0  # job is now handled!
    items2 = {i["id"]: i for i in data2["logs"]}
    assert items2[f"job_failed_{job.id}"]["handled"] is True

    # DELETE /logs/handled/{entry_kind}/{entry_id} to undo
    del_res = client.delete(f"/logs/handled/job/{job.id}")
    assert del_res.status_code == 200
    assert del_res.json()["handled"] is False

    # Check unhandled again
    res3 = client.get("/logs?category=all")
    assert res3.json()["error_count"] == 1
    items3 = {i["id"]: i for i in res3.json()["logs"]}
    assert items3[f"job_failed_{job.id}"]["handled"] is False


def test_logs_brand_filter(client, db_session):
    now = datetime.now(timezone.utc)
    fp1 = TargetFanpage(name="Formula News", is_active=True)
    fp2 = TargetFanpage(name="Moto Racing", is_active=True)
    db_session.add_all([fp1, fp2])
    db_session.commit()

    j1 = PublishJob(
        fanpage_id=fp1.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.failed,
        last_error="FP1 error",
        updated_at=now,
    )
    j2 = PublishJob(
        fanpage_id=fp2.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.failed,
        last_error="FP2 error",
        updated_at=now,
    )
    db_session.add_all([j1, j2])
    db_session.commit()

    res_fp1 = client.get(f"/logs?fanpage_id={fp1.id}")
    assert res_fp1.status_code == 200
    logs1 = res_fp1.json()["logs"]
    assert len(logs1) == 1
    assert logs1[0]["entry_id"] == j1.id

    res_fp2 = client.get(f"/logs?fanpage_id={fp2.id}")
    assert res_fp2.status_code == 200
    logs2 = res_fp2.json()["logs"]
    assert len(logs2) == 1
    assert logs2[0]["entry_id"] == j2.id


def test_logs_category_access_tab(client, db_session):
    now = datetime.now(timezone.utc)
    auth_fail = AuthEvent(
        username="badguy",
        success=False,
        ip="10.0.0.1",
        user_agent="hacktool",
        created_at=now,
    )
    auth_ok = AuthEvent(
        username="admin",
        success=True,
        ip="10.0.0.2",
        user_agent="Chrome",
        created_at=now,
    )
    db_session.add_all([auth_fail, auth_ok])
    db_session.commit()

    res = client.get("/logs?category=access")
    assert res.status_code == 200
    items = res.json()["logs"]
    assert len(items) == 2
    types = {i["type"] for i in items}
    assert "login_failed" in types
    assert "login_success" in types
