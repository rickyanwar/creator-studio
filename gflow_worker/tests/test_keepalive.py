from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import pytest

from gflow_worker import memory
from gflow_worker.main import Worker
from gflow_worker.tests.conftest import calls, payload


@pytest.fixture(autouse=True)
def mock_memory(monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)


def test_first_run_spreads(cfg, studio):
    now = datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc)
    worker = Worker(cfg, api=studio.api, clock=lambda: now)
    worker.accounts = [{"profile": "p1"}, {"profile": "p2"}, {"profile": "p3"}]

    schedule_file = cfg.work_dir / "keepalive.json"
    assert not schedule_file.exists()

    worker._sync_keepalive_schedule()

    assert schedule_file.exists()
    profiles = worker._keepalive_state["profiles"]
    assert set(profiles.keys()) == {"p1", "p2", "p3"}

    for p in ("p1", "p2", "p3"):
        entry = profiles[p]
        assert entry["last_at"] is None
        assert entry["last_result"] is None
        next_dt = datetime.fromisoformat(entry["next_at"])
        assert now + timedelta(minutes=30) <= next_dt <= now + timedelta(hours=cfg.keepalive_hours_max)


def test_new_profile_added(cfg, studio):
    now = datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc)
    p1_next = now + timedelta(hours=10)
    initial_state = {
        "profiles": {
            "p1": {"next_at": p1_next.isoformat(), "last_at": None, "last_result": None}
        },
        "next_any_at": None,
    }
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "keepalive.json").write_text(json.dumps(initial_state))

    worker = Worker(cfg, api=studio.api, clock=lambda: now)
    worker.accounts = [{"profile": "p1"}, {"profile": "p2"}]

    worker._sync_keepalive_schedule()

    profiles = worker._keepalive_state["profiles"]
    assert "p1" in profiles and "p2" in profiles
    assert profiles["p1"]["next_at"] == p1_next.isoformat()

    p2_next = datetime.fromisoformat(profiles["p2"]["next_at"])
    assert now + timedelta(minutes=cfg.keepalive_new_min) <= p2_next <= now + timedelta(minutes=cfg.keepalive_new_max)


def test_outside_window(cfg, studio, monkeypatch):
    # Window is 07:00-22:00 Asia/Jakarta (UTC+7).
    # 23:30 Asia/Jakarta = 16:30 UTC.
    now = datetime(2026, 10, 31, 16, 30, 0, tzinfo=timezone.utc)
    worker = Worker(cfg, api=studio.api, clock=lambda: now)
    worker.accounts = [{"profile": "p1", "authenticated": True, "credits": 100}]
    worker._keepalive_state = {
        "profiles": {
            "p1": {"next_at": (now - timedelta(minutes=10)).isoformat(), "last_at": None, "last_result": None}
        },
        "next_any_at": None,
    }
    worker._keepalive_file_existed = True

    worker.tick()

    assert worker._keepalive_thread is None
    assert studio.events("/session") == []
    renew_calls = [c for c in calls(cfg) if c[:2] == ["auth", "renew"]]
    assert renew_calls == []


def test_two_profiles_due(cfg, studio, monkeypatch):
    # 10:00 Asia/Jakarta = 03:00 UTC (inside window 07:00-22:00).
    now = datetime(2026, 10, 31, 3, 0, 0, tzinfo=timezone.utc)
    worker = Worker(cfg, api=studio.api, clock=lambda: now)
    worker.next_full_refresh = 999999999.0
    worker.accounts = [
        {"profile": "p1", "authenticated": True, "credits": 100},
        {"profile": "p2", "authenticated": True, "credits": 200},
    ]
    worker._keepalive_state = {
        "profiles": {
            "p1": {"next_at": (now - timedelta(minutes=15)).isoformat(), "last_at": None, "last_result": None},
            "p2": {"next_at": (now - timedelta(minutes=5)).isoformat(), "last_at": None, "last_result": None},
        },
        "next_any_at": None,
    }
    worker._keepalive_file_existed = True

    worker.tick()
    assert worker._keepalive_thread is not None
    worker._keepalive_thread.join(timeout=2)

    # p1 had earlier next_at, so p1 ran.
    sessions = studio.events("/session")
    assert len(sessions) == 1
    assert sessions[0]["profile"] == "p1"

    # next_any_at is now set >= now + GAP_MIN (20 min).
    next_any_raw = worker._keepalive_state["next_any_at"]
    assert next_any_raw is not None
    next_any_dt = datetime.fromisoformat(next_any_raw)
    assert next_any_dt >= now + timedelta(minutes=cfg.keepalive_gap_min)

    # Second tick immediately: p2 must NOT run because now < next_any_at.
    worker._keepalive_thread = None
    worker.tick()
    assert worker._keepalive_thread is None
    assert len(studio.events("/session")) == 1


def test_running_job(cfg, studio):
    now = datetime(2026, 10, 31, 3, 0, 0, tzinfo=timezone.utc)
    worker = Worker(cfg, api=studio.api, clock=lambda: now)
    worker.next_full_refresh = 999999999.0
    worker.accounts = [{"profile": "p1", "authenticated": True, "credits": 100}]
    worker._keepalive_state = {
        "profiles": {
            "p1": {"next_at": (now - timedelta(minutes=10)).isoformat(), "last_at": None, "last_result": None}
        },
        "next_any_at": None,
    }
    worker._keepalive_file_existed = True

    f = Future()
    worker.jobs = {1: (payload(), f)}

    worker.tick()

    assert worker._keepalive_thread is None
    assert studio.events("/session") == []


def test_renewed(cfg, studio, monkeypatch):
    now = datetime(2026, 10, 31, 3, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("FAKE_GF_AUTH_P1", "renewed")
    monkeypatch.setenv("FAKE_GF_CREDITS_P1", "ok")
    monkeypatch.setenv("FAKE_GF_CREDITS_VAL_P1", "550")
    monkeypatch.setenv("FAKE_GF_CREDITS_EMAIL_P1", "destri@example.test")

    worker = Worker(cfg, api=studio.api, clock=lambda: now)
    worker.next_full_refresh = 999999999.0
    worker.accounts = [{"profile": "p1", "authenticated": False, "credits": None}]
    worker._keepalive_state = {
        "profiles": {
            "p1": {"next_at": (now - timedelta(minutes=5)).isoformat(), "last_at": None, "last_result": None}
        },
        "next_any_at": None,
    }
    worker._keepalive_file_existed = True

    worker.tick()
    assert worker._keepalive_thread is not None
    worker._keepalive_thread.join(timeout=2)

    cu_calls = [c for c in calls(cfg) if c[:2] == ["credits", "user"]]
    assert len(cu_calls) == 1
    assert "--profile" in cu_calls[0] and cu_calls[0][cu_calls[0].index("--profile") + 1] == "p1"

    sessions = studio.events("/session")
    assert len(sessions) == 1
    s = sessions[0]
    assert s["worker_id"] == cfg.worker_id
    assert s["profile"] == "p1"
    assert s["result"] == "renewed"
    assert s["credits"] == 550
    assert s["authenticated"] is True
    assert s["email"] == "destri@example.test"

    assert worker.accounts[0]["authenticated"] is True
    assert worker.accounts[0]["credits"] == 550


def test_public_landing(cfg, studio, monkeypatch):
    now = datetime(2026, 10, 31, 3, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("FAKE_GF_AUTH_P1", "public_landing")

    worker = Worker(cfg, api=studio.api, clock=lambda: now)
    worker.next_full_refresh = 999999999.0
    worker.accounts = [{"profile": "p1", "authenticated": True, "credits": 200}]
    worker._keepalive_state = {
        "profiles": {
            "p1": {"next_at": (now - timedelta(minutes=5)).isoformat(), "last_at": None, "last_result": None}
        },
        "next_any_at": None,
    }
    worker._keepalive_file_existed = True

    worker.tick()
    assert worker._keepalive_thread is not None
    worker._keepalive_thread.join(timeout=2)

    cu_calls = [c for c in calls(cfg) if c[:2] == ["credits", "user"]]
    assert len(cu_calls) == 0

    sessions = studio.events("/session")
    assert len(sessions) == 1
    s = sessions[0]
    assert s["worker_id"] == cfg.worker_id
    assert s["profile"] == "p1"
    assert s["result"] == "public_landing"
    assert s["credits"] is None
    assert s["authenticated"] is False

    assert worker.accounts[0]["authenticated"] is False


def test_launch_error(cfg, studio, monkeypatch):
    now = datetime(2026, 10, 31, 3, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("FAKE_GF_AUTH_P1", "launch_error")

    worker = Worker(cfg, api=studio.api, clock=lambda: now)
    worker.next_full_refresh = 999999999.0
    worker.accounts = [{"profile": "p1", "authenticated": True, "credits": 200}]
    worker._keepalive_state = {
        "profiles": {
            "p1": {"next_at": (now - timedelta(minutes=5)).isoformat(), "last_at": None, "last_result": None}
        },
        "next_any_at": None,
    }
    worker._keepalive_file_existed = True

    worker.tick()
    assert worker._keepalive_thread is not None
    worker._keepalive_thread.join(timeout=2)

    assert studio.events("/session") == []

    p1_entry = worker._keepalive_state["profiles"]["p1"]
    next_dt = datetime.fromisoformat(p1_entry["next_at"])
    assert now + timedelta(minutes=30) <= next_dt <= now + timedelta(minutes=90)
    assert worker.accounts[0]["authenticated"] is True


def test_schedule_persists(cfg, studio, monkeypatch):
    now = datetime(2026, 10, 31, 3, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("FAKE_GF_AUTH_P1", "renewed")

    worker1 = Worker(cfg, api=studio.api, clock=lambda: now)
    worker1.next_full_refresh = 999999999.0
    worker1.accounts = [{"profile": "p1", "authenticated": True, "credits": 100}]
    worker1._keepalive_state = {
        "profiles": {
            "p1": {"next_at": (now - timedelta(minutes=5)).isoformat(), "last_at": None, "last_result": None}
        },
        "next_any_at": None,
    }
    worker1._keepalive_file_existed = True

    worker1.tick()
    assert worker1._keepalive_thread is not None
    worker1._keepalive_thread.join(timeout=2)

    saved_next_at = worker1._keepalive_state["profiles"]["p1"]["next_at"]

    # New worker instance in the same work_dir
    worker2 = Worker(cfg, api=studio.api, clock=lambda: now)
    assert worker2._keepalive_file_existed is True
    assert worker2._keepalive_state["profiles"]["p1"]["last_result"] == "renewed"
    assert worker2._keepalive_state["profiles"]["p1"]["next_at"] == saved_next_at
