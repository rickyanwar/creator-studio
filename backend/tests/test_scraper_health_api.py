"""Scraper health dashboard response and access checks."""

from fastapi.testclient import TestClient

from app.api import scraper_health
from app.main import app


class FakeDB:
    def __init__(self, mode):
        self.mode = mode

    def query(self, _model):
        return self

    def filter_by(self, **_kwargs):
        return self

    def first(self):
        return type("SettingsRow", (), {"scraper_mode": self.mode})()


def test_scraper_health_statuses_and_sanitization(monkeypatch):
    states = [
        {"last_success_at": "2026-10-01T00:00:00+00:00", "last_failure_at": "", "consecutive_failures": 0,
         "distinct_users": 0, "last_error_kind": "", "last_error": "", "unhealthy": False,
         "last_failure_username": "", "events": []},
        {"last_success_at": "", "last_failure_at": "2026-10-02T00:00:00+00:00", "consecutive_failures": 2,
         "distinct_users": 1, "last_error_kind": "blocked", "last_error": "Bearer sk-secret", "unhealthy": False,
         "last_failure_username": "testuser", "events": [{"at": "2026-10-02T00:00:00+00:00", "username": "testuser", "ok": False, "kind": "blocked", "reason": "Cloudflare check was not passed", "detail": "Bearer sk-secret"}]},
        {"last_success_at": "", "last_failure_at": "2026-10-03T00:00:00+00:00", "consecutive_failures": 5,
         "distinct_users": 2, "last_error_kind": "script", "last_error": "broken", "unhealthy": True,
         "last_failure_username": "otheruser", "events": []},
    ]

    def snapshot(*, strict):
        assert strict is False
        return {"tiers": dict(zip(scraper_health.TIERS, states)), "generated_at": "2026-10-05T00:00:00+00:00"}

    monkeypatch.setattr(scraper_health, "get_health", snapshot)
    result = scraper_health.scraper_health(FakeDB("flashapi"), "admin")
    assert result["available"] is True
    assert result["scraper_mode"] == "viewer"
    assert result["generated_at"] == "2026-10-05T00:00:00+00:00"
    assert [result["tiers"][tier]["status"] for tier in scraper_health.TIERS] == [
        "healthy", "degraded", "unhealthy"]
    assert result["tiers"]["anonyig"]["last_error"] == "Bearer [REDACTED]"
    assert result["tiers"]["igstoryviewer"]["distinct_users"] == 2

    states[0] = {**states[0], "last_success_at": ""}
    assert scraper_health.scraper_health(FakeDB("auto"), "admin")["tiers"]["gramsnap"]["status"] == "unknown"


def test_scraper_health_unavailable(monkeypatch, caplog):
    def fail(*, strict):
        raise RuntimeError("private token")

    monkeypatch.setattr(scraper_health, "get_health", fail)
    result = scraper_health.scraper_health(FakeDB("viewer"), "admin")
    assert result["available"] is False
    assert result["tiers"] == {}
    assert result["scraper_mode"] == "viewer"
    assert "private token" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_scraper_health_requires_auth():
    response = TestClient(app).get("/api/scraper-health")
    assert response.status_code in (401, 403)
