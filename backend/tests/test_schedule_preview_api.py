"""Standalone API tests for GET /api/social-media/destinations/{id}/schedule-preview.

Scoped SQLite fixture with compiler restores/disposals, testing:
- Unauthenticated / unauthorized rejection (401/403)
- 404 on non-existent destination
- Limit bounds validation (Query ge=1, le=12 -> 422 on 0 or 13)
- 6 ordered non-uniform slots returned
- Australia/Sydney timezone with sleep window handled
- Read-only safety (no rows created or updated)
- Helper integration (actual compute_audience_slot algorithm)
- Legacy project reservations respected and spaced properly
"""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause

from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.social_publishing import (
    DestinationDeviceAssignment,
    DeviceLease,
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.services.publishing_schedule import compute_audience_slot


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def sqlite_db_session():
    """Create isolated in-memory SQLite session with clean table setup and disposal."""
    orig_supported_media_default = SocialDestination.__table__.columns["supported_media"].server_default
    SocialDestination.__table__.columns["supported_media"].server_default = DefaultClause(
        text("'[\"image\", \"album\", \"video\"]'")
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        TargetFanpage.__table__,
        PhoneDevice.__table__,
        SocialDestination.__table__,
        DestinationDeviceAssignment.__table__,
        PublishJob.__table__,
        PublishDelivery.__table__,
        DeviceLease.__table__,
    ]
    for table in tables:
        table.create(bind=engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        yield session
    finally:
        session.close()
        for table in reversed(tables):
            table.drop(bind=engine, checkfirst=True)
        engine.dispose()
        SocialDestination.__table__.columns["supported_media"].server_default = orig_supported_media_default


@pytest.fixture
def auth_client(sqlite_db_session):
    """Test client with db and user override."""
    app.dependency_overrides[get_db] = lambda: sqlite_db_session
    app.dependency_overrides[get_current_user] = lambda: "admin_tester"
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def unauth_client(sqlite_db_session):
    """Test client with db override only; no current user override."""
    app.dependency_overrides[get_db] = lambda: sqlite_db_session
    # Do not override get_current_user so real auth dependency runs
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_schedule_preview_unauthorized(unauth_client, sqlite_db_session):
    """Unauthenticated access must be rejected with 401 or 403."""
    resp = unauth_client.get("/social-media/destinations/1/schedule-preview")
    assert resp.status_code in (401, 403)


def test_schedule_preview_not_found(auth_client, sqlite_db_session):
    """Unknown destination returns 404."""
    resp = auth_client.get("/social-media/destinations/9999/schedule-preview")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Destination not found"


def test_schedule_preview_limit_bounds(auth_client, sqlite_db_session):
    """Query param limit must be between 1 and 12 inclusive."""
    project = TargetFanpage(
        id=101,
        name="Formula Weekend Test",
        timezone="Australia/Sydney",
        target_country="AU",
    )
    sqlite_db_session.add(project)
    sqlite_db_session.commit()

    dest = SocialDestination(
        id=201,
        content_project_id=101,
        kind="facebook_profile",
        name="Formula Profile",
        mode="phone",
        timezone="Australia/Sydney",
        daily_target=3,
        min_gap_minutes=20,
        is_active=False,
    )
    sqlite_db_session.add(dest)
    sqlite_db_session.commit()

    # limit = 0 -> 422
    resp_zero = auth_client.get(f"/social-media/destinations/{dest.id}/schedule-preview?limit=0")
    assert resp_zero.status_code == 422

    # limit = 13 -> 422
    resp_thirteen = auth_client.get(f"/social-media/destinations/{dest.id}/schedule-preview?limit=13")
    assert resp_thirteen.status_code == 422

    # limit = 1 -> 200
    resp_one = auth_client.get(f"/social-media/destinations/{dest.id}/schedule-preview?limit=1")
    assert resp_one.status_code == 200
    assert len(resp_one.json()["slots_utc"]) == 1

    # limit = 12 -> 200
    resp_twelve = auth_client.get(f"/social-media/destinations/{dest.id}/schedule-preview?limit=12")
    assert resp_twelve.status_code == 200
    assert len(resp_twelve.json()["slots_utc"]) == 12


def test_schedule_preview_ordered_non_uniform_slots_sydney_sleep(auth_client, sqlite_db_session):
    """Verify 6 ordered non-uniform slots, timezone AU/Sydney, and project sleep window."""
    project = TargetFanpage(
        id=102,
        name="Sydney Motors",
        timezone="Australia/Sydney",
        target_country="AU",
        publish_sleep_start_hour=23,
        publish_sleep_end_hour=6,
    )
    sqlite_db_session.add(project)
    sqlite_db_session.commit()

    dest = SocialDestination(
        id=202,
        content_project_id=102,
        kind="facebook_profile",
        name="Sydney Driver",
        mode="phone",
        timezone="Australia/Sydney",
        daily_target=4,
        min_gap_minutes=25,
        is_active=False,
    )
    sqlite_db_session.add(dest)
    sqlite_db_session.commit()

    resp = auth_client.get(f"/social-media/destinations/{dest.id}/schedule-preview?limit=6")
    assert resp.status_code == 200
    data = resp.json()

    assert data["timezone"] == "Australia/Sydney"
    assert data["target_country"] == "AU"
    assert data["daily_limit"] == 4
    assert data["min_gap_minutes"] == 25
    assert data["sleep_start"] == 23
    assert data["sleep_end"] == 6
    assert data["basis"] == "local_time_estimate"
    assert data["note"] == "Estimated local activity windows; no account audience Insights connected."

    slots_utc = data["slots_utc"]
    assert len(slots_utc) == 6

    # Parse and check strictly ascending order and intervals
    dt_slots = [datetime.fromisoformat(s) for s in slots_utc]
    for i in range(len(dt_slots) - 1):
        assert dt_slots[i] < dt_slots[i + 1]

    # Verify spacing >= min_gap_minutes
    min_gap_sec = 25 * 60
    intervals = []
    for i in range(len(dt_slots) - 1):
        diff = (dt_slots[i + 1] - dt_slots[i]).total_seconds()
        intervals.append(diff)
        assert diff >= min_gap_sec

    # Check non-uniform pacing (not exact identical gaps)
    unique_intervals = {round(diff, -1) for diff in intervals}
    assert len(unique_intervals) > 1, f"Intervals should be variable, got {intervals}"


def test_schedule_preview_read_only(auth_client, sqlite_db_session):
    """Preview endpoint must be strictly read-only: no database rows created or altered."""
    project = TargetFanpage(
        id=103,
        name="Read Only Fanpage",
        timezone="Europe/London",
        target_country="GB",
    )
    sqlite_db_session.add(project)
    sqlite_db_session.commit()

    dest = SocialDestination(
        id=203,
        content_project_id=103,
        kind="facebook_profile",
        name="London Destination",
        mode="phone",
        timezone="Europe/London",
        daily_target=2,
        min_gap_minutes=30,
        is_active=False,
    )
    sqlite_db_session.add(dest)
    sqlite_db_session.commit()

    # Count rows before
    deliveries_before = sqlite_db_session.query(PublishDelivery).count()
    jobs_before = sqlite_db_session.query(PublishJob).count()
    dest_before = sqlite_db_session.query(SocialDestination).count()

    resp = auth_client.get(f"/social-media/destinations/{dest.id}/schedule-preview?limit=6")
    assert resp.status_code == 200

    # Count rows after
    assert sqlite_db_session.query(PublishDelivery).count() == deliveries_before
    assert sqlite_db_session.query(PublishJob).count() == jobs_before
    assert sqlite_db_session.query(SocialDestination).count() == dest_before


def test_schedule_preview_respects_legacy_and_ledger_reservations(auth_client, sqlite_db_session, monkeypatch):
    """Preview respects existing legacy PublishJobs and ledger deliveries when finding slots."""
    import app.api.social_media as sm_api

    project = TargetFanpage(
        id=104,
        name="Legacy Fanpage",
        timezone="UTC",
        target_country="GB",
        repliz_account_id="rep_104",
    )
    sqlite_db_session.add(project)
    sqlite_db_session.commit()

    dest = SocialDestination(
        id=204,
        content_project_id=104,
        kind="facebook_page",
        name="UTC Destination",
        mode="repliz",
        repliz_account_id="rep_104",
        timezone="UTC",
        daily_target=2,
        min_gap_minutes=60,
        is_active=False,
    )
    sqlite_db_session.add(dest)
    sqlite_db_session.commit()

    # Freeze clock to fixed aware UTC datetime across API endpoint
    fixed_now = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return fixed_now.astimezone(tz)
            return fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(sm_api, "datetime", FrozenDatetime)

    # Add a legacy PublishJob reservation tomorrow at 12:00 UTC
    future_slot = (fixed_now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    legacy_job = PublishJob(
        id=501,
        fanpage_id=104,
        scheduled_for=future_slot.replace(tzinfo=None),
        status=PublishJobStatus.pending_publish,
        is_deleted=False,
    )
    sqlite_db_session.add(legacy_job)

    # Add a ledger PublishDelivery reservation on day 2 at 15:00 UTC
    ledger_job = PublishJob(
        id=502,
        fanpage_id=104,
        status=PublishJobStatus.pending_publish,
        is_deleted=False,
    )
    sqlite_db_session.add(ledger_job)
    ledger_slot = (fixed_now + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    delivery = PublishDelivery(
        id=601,
        publish_job_id=502,
        destination_id=dest.id,
        scheduled_for=ledger_slot.replace(tzinfo=None),
        state="scheduled",
    )
    sqlite_db_session.add(delivery)
    sqlite_db_session.commit()

    resp = auth_client.get(f"/social-media/destinations/{dest.id}/schedule-preview?limit=6")
    assert resp.status_code == 200
    data = resp.json()

    slots_utc = [datetime.fromisoformat(s) for s in data["slots_utc"]]
    # Every generated slot must be at least min_gap_minutes (60 min) away from future_slot and ledger_slot
    for s in slots_utc:
        diff_legacy = abs((s - future_slot).total_seconds())
        assert diff_legacy >= 60 * 60, f"Slot {s} was too close to legacy reservation {future_slot}"
        diff_ledger = abs((s - ledger_slot).total_seconds())
        assert diff_ledger >= 60 * 60, f"Slot {s} was too close to ledger delivery {ledger_slot}"


def test_schedule_preview_exact_six_slots_parity_and_namespace_regression(auth_client, sqlite_db_session, monkeypatch):
    """6 preview slots exactly match sequential compute_audience_slot calls with dest-{id} namespace.

    Negative control: proves old underscore 'dest_{id}' namespace yields divergent slot jitter.
    """
    import app.api.social_media as sm_api

    project = TargetFanpage(
        id=105,
        name="Sydney Formula Fanpage",
        timezone="Australia/Sydney",
        target_country="AU",
        publish_sleep_start_hour=22,
        publish_sleep_end_hour=8,
    )
    sqlite_db_session.add(project)
    sqlite_db_session.commit()

    dest = SocialDestination(
        id=205,
        content_project_id=105,
        kind="facebook_profile",
        name="Sydney Formula Profile",
        mode="phone",
        timezone="Australia/Sydney",
        daily_target=31,
        min_gap_minutes=5,
        is_active=False,
    )
    sqlite_db_session.add(dest)
    sqlite_db_session.commit()

    # Freeze clock to fixed aware UTC datetime across API endpoint
    fixed_now = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return fixed_now.astimezone(tz)
            return fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(sm_api, "datetime", FrozenDatetime)

    # 1. Compute expected 6 slots sequentially using helper with matching dest-{id} namespace
    expected_slots_hyphen = []
    simulated_hyphen = []
    for _ in range(6):
        next_s = compute_audience_slot(
            now_utc=fixed_now,
            timezone_name="Australia/Sydney",
            daily_target=31,
            min_gap_minutes=5,
            existing_slots_utc=simulated_hyphen,
            sleep_start_hour=22,
            sleep_end_hour=8,
            namespace=f"dest-{dest.id}",
        )
        simulated_hyphen.append(next_s)
        expected_slots_hyphen.append(next_s)

    # 2. Call schedule-preview API and verify exact parity on all 6 slots
    resp = auth_client.get(f"/social-media/destinations/{dest.id}/schedule-preview?limit=6")
    assert resp.status_code == 200
    api_slots = [datetime.fromisoformat(s) for s in resp.json()["slots_utc"]]

    assert len(api_slots) == 6
    for idx in range(6):
        assert api_slots[idx] == expected_slots_hyphen[idx], (
            f"Slot {idx} mismatch: api={api_slots[idx]} vs expected={expected_slots_hyphen[idx]}"
        )

    # 3. Negative control: verify old underscore namespace produces divergent slots
    simulated_underscore = []
    divergent_slots_underscore = []
    for _ in range(6):
        old_s = compute_audience_slot(
            now_utc=fixed_now,
            timezone_name="Australia/Sydney",
            daily_target=31,
            min_gap_minutes=5,
            existing_slots_utc=simulated_underscore,
            sleep_start_hour=22,
            sleep_end_hour=8,
            namespace=f"dest_{dest.id}",
        )
        simulated_underscore.append(old_s)
        divergent_slots_underscore.append(old_s)

    # All 6 slots must differ from old underscore namespace slots
    assert api_slots != divergent_slots_underscore
    for idx in range(6):
        assert api_slots[idx] != divergent_slots_underscore[idx], (
            f"Slot {idx} should differ from old underscore namespace slot"
        )
