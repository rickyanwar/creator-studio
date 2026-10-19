"""Tests for /settings/gptcli/status endpoint (TASK-GC2).

Verifies:
- Route registration on /settings/gptcli/status.
- Sanitized fields extraction from gpt serve /health.
- Unreachable handling when serve_health returns None.
- Security: never leaks chat_url, request ids, tokens, or prompts.
- Empty profiles or missing fields handled gracefully.
- Authentication dependency enforced.
"""

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db
from app.api.settings import router
from app.main import app
from app.models.settings import Settings
from app.services.gptcli_client import GptcliConfig


@pytest.fixture
def status_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Settings.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    row = Settings(id=1, gptcli_enabled=True, gptcli_base_url="http://172.17.0.1:8099")
    session.add(row)
    session.commit()
    yield session
    session.close()


def test_gptcli_status_route_registration():
    """Route is registered on /settings/gptcli/status with GET."""
    assert any(
        r.path == "/settings/gptcli/status" and "GET" in r.methods
        for r in router.routes
    )


def test_gptcli_status_sanitized_fields(status_db):
    """Sanitized fields are mapped correctly from healthy gpt serve profiles."""
    health_payload = {
        "ok": True,
        "sleeping": False,
        "queue_len": 3,
        "hb_age_s": 4.5,
        "profiles": {
            "utama": {
                "running": True,
                "running_request_id": "cs-secret-job-uuid-1234",
                "running_s": 45.2,
                "today_count": 15,
                "soft_cap_daily": 100,
                "quota_until": "2026-10-19T18:00:00Z",
                "logged_in": True,
                "chat_url": "https://chatgpt.com/c/secret-thread-url",
                "prompt": "secret internal prompt text",
            }
        },
    }

    app.dependency_overrides[get_db] = lambda: status_db
    app.dependency_overrides[get_current_user] = lambda: "admin"
    client = TestClient(app)

    try:
        with patch("app.services.gptcli_client.serve_health", return_value=health_payload):
            resp = client.get("/settings/gptcli/status")
            assert resp.status_code == 200
            data = resp.json()

            assert data["reachable"] is True
            assert data["ok"] is True
            assert data["sleeping"] is False
            assert data["queue_len"] == 3
            assert data["running"] is True
            assert data["running_for_s"] == 45.2
            assert data["today_count"] == 15
            assert data["daily_cap"] == 100
            assert data["quota_until"] == "2026-10-19T18:00:00Z"
            assert data["logged_in"] is True
            assert data["heartbeat_age_s"] == 4.5

            # Exact field set
            expected_keys = {
                "reachable", "ok", "sleeping", "queue_len", "running",
                "running_for_s", "today_count", "daily_cap", "quota_until",
                "logged_in", "heartbeat_age_s"
            }
            assert set(data.keys()) == expected_keys

            # Security: no chat URLs, tokens, request ids, or prompts leaked
            raw_text = resp.text
            assert "cs-secret-job-uuid-1234" not in raw_text
            assert "secret-thread-url" not in raw_text
            assert "chat_url" not in raw_text
            assert "running_request_id" not in raw_text
            assert "secret internal prompt text" not in raw_text
            assert "prompt" not in data
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_gptcli_status_unreachable(status_db):
    """When serve_health returns None, reachable is False with safe defaults."""
    app.dependency_overrides[get_db] = lambda: status_db
    app.dependency_overrides[get_current_user] = lambda: "admin"
    client = TestClient(app)

    try:
        with patch("app.services.gptcli_client.serve_health", return_value=None):
            resp = client.get("/settings/gptcli/status")
            assert resp.status_code == 200
            data = resp.json()

            assert data["reachable"] is False
            assert data["ok"] is False
            assert data["sleeping"] is False
            assert data["queue_len"] == 0
            assert data["running"] is False
            assert data["running_for_s"] is None
            assert data["today_count"] is None
            assert data["daily_cap"] is None
            assert data["quota_until"] is None
            assert data["logged_in"] is None
            assert data["heartbeat_age_s"] is None
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_gptcli_status_empty_profiles(status_db):
    """When health returns empty profiles dict, defaults are used safely."""
    health_payload = {
        "ok": True,
        "sleeping": True,
        "queue_len": 0,
        "hb_age_s": 10.0,
        "profiles": {},
    }

    app.dependency_overrides[get_db] = lambda: status_db
    app.dependency_overrides[get_current_user] = lambda: "admin"
    client = TestClient(app)

    try:
        with patch("app.services.gptcli_client.serve_health", return_value=health_payload):
            resp = client.get("/settings/gptcli/status")
            assert resp.status_code == 200
            data = resp.json()

            assert data["reachable"] is True
            assert data["ok"] is True
            assert data["sleeping"] is True
            assert data["queue_len"] == 0
            assert data["running"] is False
            assert data["running_for_s"] is None
            assert data["today_count"] is None
            assert data["daily_cap"] is None
            assert data["quota_until"] is None
            assert data["logged_in"] is None
            assert data["heartbeat_age_s"] == 10.0
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_gptcli_status_requires_auth(status_db):
    """Access without auth fails with 401/403 Unauthorized/Forbidden."""
    app.dependency_overrides[get_db] = lambda: status_db
    client = TestClient(app)

    try:
        resp = client.get("/settings/gptcli/status")
        assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides.pop(get_db, None)
