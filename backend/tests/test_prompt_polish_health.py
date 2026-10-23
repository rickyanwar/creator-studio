"""Tests for Argon prompt polish health, watchdog, API, and CLI script."""

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"

from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.ai_video import VideoIdea, VideoJob
from app.models.settings import Settings
from app.models.social_publishing import OperationalNotification
from app.models.target_fanpages import TargetFanpage
from app.scripts import gemini_web_health
from app.services.ai_video_prepare import PREPARED_VERSION, idea_fingerprint
from app.services.encryption import encrypt
from app.services.prompt_polish import PolishConfig
from app.services.prompt_polish_health import (
    LAST_OK_KEY,
    LAST_RUN_KEY,
    fetch_service_health,
    snapshot,
)
from app.tasks.prompt_polish_watchdog import check_prompt_polish

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# Strip Postgres server_defaults that SQLite cannot parse
_clips_col = VideoJob.__table__.columns.get("clips_done")
_orig_clips = _clips_col.server_default if _clips_col is not None else None
if _clips_col is not None:
    _clips_col.server_default = None

for _tbl in [
    TargetFanpage.__table__,
    VideoIdea.__table__,
    VideoJob.__table__,
    Settings.__table__,
    OperationalNotification.__table__,
]:
    _tbl.create(test_engine)

if _clips_col is not None:
    _clips_col.server_default = _orig_clips

TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


class FakeRedis:
    """In-memory Redis fake with get, set(nx, ex), exists, delete."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = str(value)
        return True

    def exists(self, key):
        return 1 if key in self.store else 0

    def delete(self, key):
        if key in self.store:
            del self.store[key]
            return 1
        return 0


@pytest.fixture(autouse=True)
def patch_db_and_redis(monkeypatch):
    from cryptography.fernet import Fernet
    from app.config import get_settings

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(get_settings(), "fernet_key", key)

    r = FakeRedis()
    monkeypatch.setattr("app.services.prompt_polish_health._redis", lambda: r)
    monkeypatch.setattr("app.tasks.prompt_polish_watchdog._redis", lambda: r)
    monkeypatch.setattr("app.tasks.prompt_polish_watchdog.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.scripts.gemini_web_health.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.database.SessionLocal", TestSessionLocal)
    return r


@pytest.fixture
def fake_redis(patch_db_and_redis):
    return patch_db_and_redis


@pytest.fixture
def db():
    session = TestSessionLocal()
    fanpage_ids = []
    idea_ids = []
    job_ids = []
    try:
        yield session, fanpage_ids, idea_ids, job_ids
    finally:
        session.rollback()
        if job_ids:
            session.query(VideoJob).filter(VideoJob.id.in_(job_ids)).delete(synchronize_session=False)
        if idea_ids:
            session.query(VideoIdea).filter(VideoIdea.id.in_(idea_ids)).delete(synchronize_session=False)
        if fanpage_ids:
            session.query(TargetFanpage).filter(TargetFanpage.id.in_(fanpage_ids)).delete(synchronize_session=False)
        session.query(OperationalNotification).filter_by(dedupe_key="gemini_web:serve").delete(synchronize_session=False)
        session.query(Settings).delete(synchronize_session=False)
        session.commit()
        session.close()


def _fanpage(session, fanpage_ids, *, polish_on=True, is_active=True):
    fp = TargetFanpage(
        repliz_account_id=f"health-{uuid4().hex[:8]}",
        name=f"Fanpage-{uuid4().hex[:6]}",
        is_active=is_active,
        is_connected=True,
        ai_video_enabled=True,
        ai_video_prompt_polish_enabled=polish_on,
        ai_video_daily_count=1,
        ai_video_clips=3,
    )
    session.add(fp)
    session.flush()
    fanpage_ids.append(fp.id)
    return fp


def _idea(session, fanpage, idea_ids, *, status="approved", created_at=None, decided_at=None, error=None, attempts=0):
    idea = VideoIdea(
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        topic=f"Topic-{uuid4().hex[:6]}",
        status=status,
        score=90,
        polish_attempts=attempts,
        error=error,
        created_at=created_at or datetime.now(timezone.utc).replace(tzinfo=None),
        decided_at=decided_at,
    )
    session.add(idea)
    session.flush()
    idea_ids.append(idea.id)
    return idea


def _set_valid_prepared(session, idea):
    fp = idea_fingerprint(idea)
    idea.prepared = {
        "version": PREPARED_VERSION,
        "fingerprint": fp,
        "template_key": idea.template_key,
        "polished": True,
        "clips": [
            {"index": 1, "mode": "t2v", "prompt": "Polished prompt", "prompt_original": "Original prompt"}
        ],
        "changes": ["improved lighting"],
        "voice_sentence": "Voiceover line",
        "ambience_sentence": "Ambient noise",
    }
    idea.prepared_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.commit()


# ── 1. fetch_service_health tests ─────────────────────────────────────────────

def test_fetch_service_health_200():
    cfg = PolishConfig(base_url="http://test-service:8098", token="tok123", model="gemini-argon")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "proxy_ok": True, "ready_now": 2, "accounts": []}

    res = fetch_service_health(cfg, http_get=lambda url, **kw: mock_resp)
    assert res["ok"] is True
    assert res["proxy_ok"] is True
    assert "token" not in res
    assert "tok123" not in str(res)


def test_fetch_service_health_connect_error():
    cfg = PolishConfig(base_url="http://test-service:8098", token="tok123", model="gemini-argon")

    def raise_connect(url, **kw):
        raise httpx.ConnectError("Connection refused")

    res = fetch_service_health(cfg, http_get=raise_connect)
    assert res == {"error": "unreachable"}


def test_fetch_service_health_401():
    cfg = PolishConfig(base_url="http://test-service:8098", token="wrongtok", model="gemini-argon")
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json.return_value = {"error": "unauthorized"}

    res = fetch_service_health(cfg, http_get=lambda url, **kw: mock_resp)
    assert res == {"error": "unauthorized"}


def test_fetch_service_health_bad_response():
    cfg = PolishConfig(base_url="http://test-service:8098", token="tok123", model="gemini-argon")
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.json.return_value = {"error": "internal"}

    res = fetch_service_health(cfg, http_get=lambda url, **kw: mock_resp)
    assert res == {"error": "bad_response"}


def test_fetch_service_health_not_configured():
    assert fetch_service_health(None) == {"error": "not_configured"}


# ── 2. snapshot tests ─────────────────────────────────────────────────────────

def test_snapshot_brands_on_0(db):
    session, fanpage_ids, _, _ = db
    snap = snapshot(session)
    assert snap["brands_on"] == 0
    assert snap["ok"] is True
    assert snap["problems"] == []
    assert snap["ideas"] == {"prepared": 0, "waiting": 0, "failed_24h": 0}
    assert snap["starving"] == []


def test_snapshot_configured_false_with_brand_on(db):
    session, fanpage_ids, _, _ = db
    _fanpage(session, fanpage_ids, polish_on=True)
    snap = snapshot(session)
    assert snap["brands_on"] == 1
    assert snap["configured"] is False
    assert snap["ok"] is False
    assert "not_configured" in snap["problems"]


def test_snapshot_service_proxy_down(db):
    session, fanpage_ids, _, _ = db
    _fanpage(session, fanpage_ids, polish_on=True)
    st = Settings(prompt_polish_token_encrypted=encrypt("secret"))
    session.add(st)
    session.commit()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": False,
        "proxy_ok": False,
        "ready_now": 0,
        "accounts": [{"name": "acc1", "status": "ok", "available": True, "reason": None}],
    }

    snap = snapshot(session, http_get=lambda u, **kw: mock_resp)
    assert "proxy_down" in snap["problems"]


def test_snapshot_account_needs_login(db):
    session, fanpage_ids, _, _ = db
    _fanpage(session, fanpage_ids, polish_on=True)
    st = Settings(prompt_polish_token_encrypted=encrypt("secret"))
    session.add(st)
    session.commit()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": True,
        "proxy_ok": True,
        "ready_now": 0,
        "accounts": [
            {"name": "acc1", "status": "needs_login", "available": False, "reason": "needs_login"},
            {"name": "acc2", "status": "abuse_flag", "available": False, "reason": "abuse_flag"},
        ],
    }

    snap = snapshot(session, http_get=lambda u, **kw: mock_resp)
    assert "account_needs_login:acc1" in snap["problems"]
    assert "account_abuse_flag:acc2" in snap["problems"]


def test_snapshot_all_cooldown_vs_all_sleeping(db):
    session, fanpage_ids, _, _ = db
    _fanpage(session, fanpage_ids, polish_on=True)
    st = Settings(prompt_polish_token_encrypted=encrypt("secret"))
    session.add(st)
    session.commit()

    mock_cooldown = MagicMock()
    mock_cooldown.status_code = 200
    mock_cooldown.json.return_value = {
        "ok": True,
        "proxy_ok": True,
        "ready_now": 0,
        "accounts": [
            {"name": "acc1", "status": "ok", "available": False, "reason": "cooldown"},
            {"name": "acc2", "status": "ok", "available": False, "reason": "daily_cap"},
        ],
    }
    snap_cd = snapshot(session, http_get=lambda u, **kw: mock_cooldown)
    assert "no_account_ready" in snap_cd["problems"]

    mock_sleeping = MagicMock()
    mock_sleeping.status_code = 200
    mock_sleeping.json.return_value = {
        "ok": True,
        "proxy_ok": True,
        "ready_now": 0,
        "accounts": [
            {"name": "acc1", "status": "ok", "available": False, "reason": "sleeping"},
        ],
    }
    snap_sleep = snapshot(session, http_get=lambda u, **kw: mock_sleeping)
    assert "no_account_ready" not in snap_sleep["problems"]


def test_snapshot_starving_brand_after_60_min(db):
    session, fanpage_ids, idea_ids, _ = db
    fp = _fanpage(session, fanpage_ids, polish_on=True)
    st = Settings(prompt_polish_token_encrypted=encrypt("secret"))
    session.add(st)
    session.commit()

    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)

    idea = _idea(session, fp, idea_ids, status="approved", created_at=now.replace(tzinfo=None) - timedelta(minutes=30))
    session.commit()

    mock_ok = MagicMock()
    mock_ok.status_code = 200
    mock_ok.json.return_value = {"ok": True, "proxy_ok": True, "ready_now": 1, "accounts": []}

    snap1 = snapshot(session, http_get=lambda u, **kw: mock_ok, now=now)
    assert snap1["starving"] == []
    assert f"brand_starving:{fp.id}" not in snap1["problems"]

    idea.created_at = now.replace(tzinfo=None) - timedelta(minutes=70)
    session.commit()

    snap2 = snapshot(session, http_get=lambda u, **kw: mock_ok, now=now)
    assert fp.id in snap2["starving"]
    assert f"brand_starving:{fp.id}" in snap2["problems"]


def test_snapshot_polish_failed_in_last_24h(db):
    session, fanpage_ids, idea_ids, _ = db
    fp = _fanpage(session, fanpage_ids, polish_on=True)
    st = Settings(prompt_polish_token_encrypted=encrypt("secret"))
    session.add(st)
    session.commit()

    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    now_naive = now.replace(tzinfo=None)

    idea_recent = _idea(
        session,
        fp,
        idea_ids,
        status="failed",
        created_at=now_naive - timedelta(hours=2),
        error="Argon prompt polish failed after 3 rounds: bad prompt",
        attempts=3,
    )
    idea_older = _idea(
        session,
        fp,
        idea_ids,
        status="failed",
        created_at=now_naive - timedelta(days=3),
        error="Argon prompt polish failed after 3 rounds: old",
        attempts=3,
    )
    session.commit()

    mock_ok = MagicMock()
    mock_ok.status_code = 200
    mock_ok.json.return_value = {"ok": True, "proxy_ok": True, "ready_now": 1, "accounts": []}

    snap = snapshot(session, http_get=lambda u, **kw: mock_ok, now=now)
    assert snap["ideas"]["failed_24h"] == 1
    assert f"polish_failed:{idea_recent.id}" in snap["problems"]
    assert f"polish_failed:{idea_older.id}" not in snap["problems"]


# ── 3. Watchdog tests ─────────────────────────────────────────────────────────

def test_watchdog_healable_flow(db):
    session, fanpage_ids, _, _ = db
    _fanpage(session, fanpage_ids, polish_on=True)
    st = Settings(prompt_polish_base_url="http://nonexistent:8098", prompt_polish_token_encrypted=encrypt("token"))
    session.add(st)
    session.commit()

    notified = []

    def notifier(msg):
        notified.append(msg)

    t0 = datetime(2026, 10, 23, 10, 0, 0, tzinfo=timezone.utc)

    check_prompt_polish(db=session, now=t0, notifier=notifier)
    assert len(notified) == 0
    marker = session.query(OperationalNotification).filter_by(dedupe_key="gemini_web:serve").first()
    assert marker is not None
    assert marker.state == "open"
    assert "service_unreachable" in marker.message

    check_prompt_polish(db=session, now=t0 + timedelta(minutes=80), notifier=notifier)
    assert len(notified) == 0

    check_prompt_polish(db=session, now=t0 + timedelta(minutes=90), notifier=notifier)
    assert len(notified) == 1
    assert "Hermes could not fix it" in notified[0]

    check_prompt_polish(db=session, now=t0 + timedelta(minutes=100), notifier=notifier)
    assert len(notified) == 1

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "proxy_ok": True, "ready_now": 1, "accounts": []}

    with patch("app.services.prompt_polish_health.httpx.get", return_value=mock_resp):
        check_prompt_polish(db=session, now=t0 + timedelta(minutes=110), notifier=notifier)

    assert len(notified) == 2
    assert "Argon prompt polish is healthy again." in notified[1]
    marker = session.query(OperationalNotification).filter_by(dedupe_key="gemini_web:serve").first()
    assert marker.state == "resolved"


def test_watchdog_owner_only_notifies_immediately_once_per_24h(db):
    session, fanpage_ids, _, _ = db
    _fanpage(session, fanpage_ids, polish_on=True)

    notified = []
    t0 = datetime(2026, 10, 23, 10, 0, 0, tzinfo=timezone.utc)

    check_prompt_polish(db=session, now=t0, notifier=lambda m: notified.append(m))
    assert len(notified) == 1
    assert "no gemini-web-serve token is set" in notified[0]

    check_prompt_polish(db=session, now=t0 + timedelta(minutes=10), notifier=lambda m: notified.append(m))
    assert len(notified) == 1


def test_watchdog_recovery_silent_if_no_alert_was_sent(db):
    session, fanpage_ids, _, _ = db
    _fanpage(session, fanpage_ids, polish_on=True)
    st = Settings(prompt_polish_base_url="http://nonexistent:8098", prompt_polish_token_encrypted=encrypt("token"))
    session.add(st)
    session.commit()

    notified = []
    t0 = datetime(2026, 10, 23, 10, 0, 0, tzinfo=timezone.utc)

    check_prompt_polish(db=session, now=t0, notifier=lambda m: notified.append(m))
    assert len(notified) == 0

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "proxy_ok": True, "ready_now": 1, "accounts": []}

    with patch("app.services.prompt_polish_health.httpx.get", return_value=mock_resp):
        check_prompt_polish(db=session, now=t0 + timedelta(minutes=20), notifier=lambda m: notified.append(m))

    assert len(notified) == 0
    marker = session.query(OperationalNotification).filter_by(dedupe_key="gemini_web:serve").first()
    assert marker.state == "resolved"


def test_watchdog_hermes_fail_calls_telegram_fallback(db):
    session, fanpage_ids, _, _ = db
    _fanpage(session, fanpage_ids, polish_on=True)

    with patch("app.services.social_delivery_dispatch.notify_owner_via_hermes", return_value=(False, "cli error")):
        with patch("app.tasks.phone_watchdog.send_telegram_fallback") as mock_tg:
            check_prompt_polish(db=session, now=datetime.now(timezone.utc))
            mock_tg.assert_called_once()
            msg = mock_tg.call_args[0][0]
            assert "no gemini-web-serve token is set" in msg


# ── 4. API tests ──────────────────────────────────────────────────────────────

@pytest.fixture
def client(db):
    session, _, _, _ = db
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: "testuser"
    c = TestClient(app)
    yield c, session
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


def test_api_health_endpoint(client):
    c, session = client
    res = c.get("/prompt-polish/health")
    assert res.status_code == 200
    data = res.json()
    assert "ok" in data
    assert "brands_on" in data
    assert "problems" in data


def test_api_idea_states_and_404(client):
    c, session = client
    fanpage_ids = []
    idea_ids = []

    assert c.get("/prompt-polish/ideas/999999").status_code == 404

    fp_off = _fanpage(session, fanpage_ids, polish_on=False)
    idea_off = _idea(session, fp_off, idea_ids, status="approved")
    session.commit()
    res_off = c.get(f"/prompt-polish/ideas/{idea_off.id}").json()
    assert res_off["state"] == "off"
    assert res_off["idea_id"] == idea_off.id

    fp_on = _fanpage(session, fanpage_ids, polish_on=True)
    idea_waiting = _idea(session, fp_on, idea_ids, status="approved")
    session.commit()
    res_waiting = c.get(f"/prompt-polish/ideas/{idea_waiting.id}").json()
    assert res_waiting["state"] == "waiting"

    idea_pol = _idea(session, fp_on, idea_ids, status="approved")
    session.commit()
    _set_valid_prepared(session, idea_pol)
    res_pol = c.get(f"/prompt-polish/ideas/{idea_pol.id}").json()
    assert res_pol["state"] == "polished"
    assert len(res_pol["clips"]) == 1
    assert res_pol["clips"][0]["prompt_original"] == "Original prompt"
    assert res_pol["changes"] == ["improved lighting"]

    idea_failed = _idea(
        session,
        fp_on,
        idea_ids,
        status="failed",
        error="Argon prompt polish failed after 3 rounds: error",
        attempts=3,
    )
    session.commit()
    res_failed = c.get(f"/prompt-polish/ideas/{idea_failed.id}").json()
    assert res_failed["state"] == "failed"

    batch_url = f"/prompt-polish/ideas-state?ids={idea_off.id},{idea_waiting.id},{idea_pol.id},{idea_failed.id}"
    batch_res = c.get(batch_url).json()
    assert batch_res[str(idea_off.id)] == "off"
    assert batch_res[str(idea_waiting.id)] == "waiting"
    assert batch_res[str(idea_pol.id)] == "polished"
    assert batch_res[str(idea_failed.id)] == "failed"


def test_api_job_endpoint(client):
    c, session = client
    fanpage_ids = []
    idea_ids = []
    job_ids = []

    assert c.get("/prompt-polish/jobs/999999").status_code == 404

    fp = _fanpage(session, fanpage_ids, polish_on=True)
    idea = _idea(session, fp, idea_ids, status="approved")
    session.commit()
    _set_valid_prepared(session, idea)

    job_polished = VideoJob(
        fanpage_id=fp.id,
        idea_id=idea.id,
        template_key="motorsport_tech_explainer",
        title="Title",
        caption="Caption",
        model="veo-3",
        aspect="9:16",
        clip_seconds=5,
        credits_per_clip=10,
        credits_estimate=30,
        clips=[
            {"index": 1, "mode": "t2v", "prompt": "Polished prompt", "prompt_original": "Orig prompt"}
        ],
    )
    session.add(job_polished)
    session.flush()
    job_ids.append(job_polished.id)
    session.commit()

    res1 = c.get(f"/prompt-polish/jobs/{job_polished.id}").json()
    assert res1["job_id"] == job_polished.id
    assert res1["polished"] is True
    assert res1["clips"][0]["prompt_original"] == "Orig prompt"
    assert res1["changes"] == ["improved lighting"]

    job_unpolished = VideoJob(
        fanpage_id=fp.id,
        idea_id=None,
        template_key="motorsport_tech_explainer",
        title="Title",
        caption="Caption",
        model="veo-3",
        aspect="9:16",
        clip_seconds=5,
        credits_per_clip=10,
        credits_estimate=30,
        clips=[
            {"index": 1, "mode": "t2v", "prompt": "Raw prompt"}
        ],
    )
    session.add(job_unpolished)
    session.flush()
    job_ids.append(job_unpolished.id)
    session.commit()

    res2 = c.get(f"/prompt-polish/jobs/{job_unpolished.id}").json()
    assert res2["job_id"] == job_unpolished.id
    assert res2["polished"] is False
    assert res2["clips"][0]["prompt_original"] is None
    assert res2["changes"] == []


def _make_job(session, fanpage, idea, job_ids, *, polished=True):
    """Create a VideoJob linked to *idea* with or without prompt_original on clips."""
    clips = [
        {
            "index": 1,
            "mode": "t2v",
            "prompt": "Polished prompt" if polished else "Raw prompt",
            **({"prompt_original": "Original prompt"} if polished else {}),
        }
    ]
    job = VideoJob(
        fanpage_id=fanpage.id,
        idea_id=idea.id,
        template_key="motorsport_tech_explainer",
        title="Title",
        caption="Caption",
        model="veo-3",
        aspect="9:16",
        clip_seconds=5,
        credits_per_clip=10,
        credits_estimate=10,
        clips=clips,
    )
    session.add(job)
    session.flush()
    job_ids.append(job.id)
    session.commit()
    return job


def test_api_queued_idea_with_polished_job_shows_polished(client):
    """Queued idea whose VideoJob has prompt_original → 'polished' in both endpoints,
    even when idea.variables was mutated after prepare (fingerprint changed)."""
    c, session = client
    fanpage_ids, idea_ids, job_ids = [], [], []

    fp = _fanpage(session, fanpage_ids, polish_on=True)
    idea = _idea(session, fp, idea_ids, status="queued")
    session.commit()

    # Simulate what plan_ai_videos does: mutate variables so fingerprint no longer matches.
    idea.variables = {"injected": "by_plan"}
    session.commit()

    _make_job(session, fp, idea, job_ids, polished=True)

    # Single-idea endpoint
    res = c.get(f"/prompt-polish/ideas/{idea.id}").json()
    assert res["state"] == "polished", f"expected polished, got {res['state']}"
    assert any(cl.get("prompt_original") for cl in res["clips"])

    # Batch endpoint
    batch = c.get(f"/prompt-polish/ideas-state?ids={idea.id}").json()
    assert batch[str(idea.id)] == "polished"

    # Cleanup
    session.query(VideoJob).filter(VideoJob.id.in_(job_ids)).delete(synchronize_session=False)
    session.query(VideoIdea).filter(VideoIdea.id.in_(idea_ids)).delete(synchronize_session=False)
    session.query(TargetFanpage).filter(TargetFanpage.id.in_(fanpage_ids)).delete(synchronize_session=False)
    session.commit()


def test_api_queued_idea_job_no_prompt_original_switch_on_shows_waiting(client):
    """Queued idea whose job has no prompt_original + switch on → 'waiting'."""
    c, session = client
    fanpage_ids, idea_ids, job_ids = [], [], []

    fp = _fanpage(session, fanpage_ids, polish_on=True)
    idea = _idea(session, fp, idea_ids, status="queued")
    session.commit()

    _make_job(session, fp, idea, job_ids, polished=False)

    res = c.get(f"/prompt-polish/ideas/{idea.id}").json()
    assert res["state"] == "waiting", f"expected waiting, got {res['state']}"

    batch = c.get(f"/prompt-polish/ideas-state?ids={idea.id}").json()
    assert batch[str(idea.id)] == "waiting"

    # Cleanup
    session.query(VideoJob).filter(VideoJob.id.in_(job_ids)).delete(synchronize_session=False)
    session.query(VideoIdea).filter(VideoIdea.id.in_(idea_ids)).delete(synchronize_session=False)
    session.query(TargetFanpage).filter(TargetFanpage.id.in_(fanpage_ids)).delete(synchronize_session=False)
    session.commit()


def test_api_queued_idea_job_no_prompt_original_switch_off_shows_off(client):
    """Queued idea whose job has no prompt_original + switch off → 'off'."""
    c, session = client
    fanpage_ids, idea_ids, job_ids = [], [], []

    fp = _fanpage(session, fanpage_ids, polish_on=False)
    idea = _idea(session, fp, idea_ids, status="queued")
    session.commit()

    _make_job(session, fp, idea, job_ids, polished=False)

    res = c.get(f"/prompt-polish/ideas/{idea.id}").json()
    assert res["state"] == "off", f"expected off, got {res['state']}"

    batch = c.get(f"/prompt-polish/ideas-state?ids={idea.id}").json()
    assert batch[str(idea.id)] == "off"

    # Cleanup
    session.query(VideoJob).filter(VideoJob.id.in_(job_ids)).delete(synchronize_session=False)
    session.query(VideoIdea).filter(VideoIdea.id.in_(idea_ids)).delete(synchronize_session=False)
    session.query(TargetFanpage).filter(TargetFanpage.id.in_(fanpage_ids)).delete(synchronize_session=False)
    session.commit()


# ── 5. Script tests ───────────────────────────────────────────────────────────

def test_script_main_success_and_pretty(capsys):
    with patch("app.scripts.gemini_web_health.SessionLocal"):
        with patch(
            "app.scripts.gemini_web_health.snapshot",
            return_value={
                "generated_at": "2026-10-23T12:00:00Z",
                "configured": True,
                "brands_on": 1,
                "service": {
                    "accounts": [
                        {
                            "name": "acct1",
                            "status": "ok",
                            "available": True,
                            "reason": None,
                            "used_today": 5,
                            "cap_today": 20,
                        }
                    ]
                },
                "problems": [],
                "ok": True,
            },
        ):
            rc = gemini_web_health.main([])
            assert rc == 0
            out = capsys.readouterr().out
            data = json.loads(out)
            assert data["ok"] is True

            rc_pretty = gemini_web_health.main(["--pretty"])
            assert rc_pretty == 0
            out_pretty = capsys.readouterr().out
            assert "acct1" in out_pretty
            assert "5/20" in out_pretty


def test_script_main_exception(capsys):
    with patch("app.scripts.gemini_web_health.SessionLocal", side_effect=RuntimeError("DB dead")):
        rc = gemini_web_health.main([])
        assert rc == 2
        out = capsys.readouterr().out
        assert json.loads(out) == {"error": "health_unavailable"}


def test_prepare_ai_video_ideas_routed_to_gallery_queue():
    from app.tasks.celery_app import celery_app
    route = celery_app.conf.task_routes.get("app.tasks.ai_video_prepare.prepare_ai_video_ideas")
    assert route == {"queue": "gallery"}


def test_snapshot_account_needs_tos(db):
    session, fanpage_ids, _, _ = db
    _fanpage(session, fanpage_ids, polish_on=True)
    st = Settings(prompt_polish_token_encrypted=encrypt("secret"))
    session.add(st)
    session.commit()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": False,
        "proxy_ok": True,
        "ready_now": 0,
        "accounts": [
            {"name": "gem1", "status": "needs_tos", "available": False, "reason": "needs_tos"},
        ],
    }

    snap = snapshot(session, http_get=lambda u, **kw: mock_resp)
    assert "account_needs_tos:gem1" in snap["problems"]
    # needs_tos is not a temporary reason like sleeping, so all unavail triggers no_account_ready
    assert "no_account_ready" in snap["problems"]


def test_watchdog_account_needs_tos_message(db):
    session, fanpage_ids, _, _ = db
    _fanpage(session, fanpage_ids, polish_on=True)
    st = Settings(prompt_polish_token_encrypted=encrypt("secret"))
    session.add(st)
    session.commit()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": False,
        "proxy_ok": True,
        "ready_now": 0,
        "accounts": [
            {"name": "gem1", "status": "needs_tos", "available": False, "reason": "needs_tos"},
        ],
    }

    notified = []
    with patch("app.services.prompt_polish_health.httpx.get", return_value=mock_resp):
        check_prompt_polish(db=session, notifier=lambda m: notified.append(m))

    assert any(
        "Gemini account gem1 must accept the new Gemini Terms of Service. Run: python3 /opt/gemini-web-serve/login.py gem1 and accept them at gemini.google.com." in msg
        for msg in notified
    )

