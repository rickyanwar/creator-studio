"""Unit and contract tests for Social Brands API (social_brands.py)."""

from datetime import datetime, timedelta, timezone
from unittest import mock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause

from app.api.deps import get_current_user, get_db
from app.api import social_brands
from app.main import app
from app.models.publish_jobs import PublishJob
from app.models.settings import Settings
from app.models.social_publishing import (
    DestinationDeviceAssignment,
    DeviceLease,
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage


# Compile PostgreSQL-specific column types for SQLite
@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


# SQLite-safe server_default for supported_media
SocialDestination.__table__.columns["supported_media"].server_default = DefaultClause(
    text("'[\"image\", \"album\", \"video\"]'")
)


@pytest.fixture
def db_session():
    """Create a clean isolated SQLite database session with required tables."""
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
        Settings.__table__,
    ]
    for table in tables:
        table.create(bind=engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    # Create default settings row
    settings = Settings(id=1, gptcli_enabled=False, gptcli_fanpage_ids=[])
    session.add(settings)
    session.commit()

    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """FastAPI test client with DB override and authenticated user override."""
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: "admin_user"
    yield TestClient(app)
    app.dependency_overrides.clear()


# ==============================================================================
# 1. /social-media/brands
# ==============================================================================


def test_brands_summary_ordering_counts_and_attention(db_session, client, monkeypatch):
    """2 brands (one inactive), brand A has mixed channel with deliveries and issues."""
    fixed_now = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(social_brands, "_utcnow", lambda: fixed_now)

    # Brand A (active)
    brand_a = TargetFanpage(
        id=1,
        name="Brand A",
        is_active=True,
        timezone="Europe/London",
        mode1_ig_repost_enabled=True,
    )
    # Brand B (inactive)
    brand_b = TargetFanpage(
        id=2,
        name="Brand B",
        is_active=False,
        timezone="Europe/London",
    )
    db_session.add_all([brand_a, brand_b])
    db_session.commit()

    # Mixed channel for Brand A: no devices assigned, no repliz_account_id
    dest_a = SocialDestination(
        id=10,
        content_project_id=brand_a.id,
        kind="facebook_page",
        name="Channel A",
        mode="mixed",
        timezone="Europe/London",
        daily_target=4,
        phone_daily_target=2,
        repliz_daily_target=2,
        is_active=True,
        repliz_account_id=None,
    )
    db_session.add(dest_a)
    db_session.commit()

    # Create PublishJobs for deliveries
    for j_id in range(1, 5):
        db_session.add(PublishJob(id=j_id, fanpage_id=brand_a.id))
    db_session.commit()

    today_naive = datetime(2026, 10, 18, 10, 0, 0)
    # 1 published-today delivery
    d_pub = PublishDelivery(
        id=101,
        publish_job_id=1,
        destination_id=dest_a.id,
        state="published",
        published_at=today_naive,
    )
    # 1 scheduled-today delivery
    d_sched = PublishDelivery(
        id=102,
        publish_job_id=2,
        destination_id=dest_a.id,
        state="scheduled",
        scheduled_for=today_naive,
    )
    # 1 failed-today delivery
    d_fail = PublishDelivery(
        id=103,
        publish_job_id=3,
        destination_id=dest_a.id,
        state="failed",
        updated_at=today_naive,
        created_at=today_naive,
    )
    # 1 needs_human delivery (any date)
    d_human = PublishDelivery(
        id=104,
        publish_job_id=4,
        destination_id=dest_a.id,
        state="needs_human",
    )
    db_session.add_all([d_pub, d_sched, d_fail, d_human])
    db_session.commit()

    res = client.get("/social-media/brands")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2

    # Active brand first
    assert data[0]["id"] == brand_a.id
    assert data[0]["name"] == "Brand A"
    assert data[1]["id"] == brand_b.id

    # Brand A counts: (1, 1, 1, 1)
    a_today = data[0]["today"]
    assert a_today["published"] == 1
    assert a_today["scheduled"] == 1
    assert a_today["failed"] == 1
    assert a_today["needs_attention"] == 1

    # Brand A attention messages
    messages = [att["message"] for att in data[0]["attention"]]
    assert "No phone assigned" in messages
    assert "No Repliz account connected" in messages
    assert "1 post(s) need a human check" in messages

    # Content list has 9 entries
    assert len(data[0]["content"]) == 9

    # Brand B attention contains paused note
    b_messages = [att["message"] for att in data[1]["attention"]]
    assert "Brand is paused" in b_messages


# ==============================================================================
# 2. /social-media/brands/{brand_id}
# ==============================================================================


def test_brand_detail_and_ai_images_today(db_session, client, monkeypatch):
    """Brand detail 404 and ai_images_today counts."""
    fixed_now = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(social_brands, "_utcnow", lambda: fixed_now)

    # 404 for non-existent brand
    res_404 = client.get("/social-media/brands/999")
    assert res_404.status_code == 404
    assert res_404.json()["detail"] == "Brand not found"

    brand = TargetFanpage(id=1, name="Formula 1 Brand", is_active=True, timezone="Europe/London")
    db_session.add(brand)
    db_session.commit()

    today_utc = datetime(2026, 10, 18, 10, 0, 0)
    old_utc = datetime(2026, 10, 16, 10, 0, 0)  # 2 days ago

    # 2 chatgpt + 1 template updated today, 1 chatgpt updated 2 days ago
    j1 = PublishJob(id=1, fanpage_id=brand.id, design_engine="chatgpt", updated_at=today_utc)
    j2 = PublishJob(id=2, fanpage_id=brand.id, design_engine="chatgpt", updated_at=today_utc)
    j3 = PublishJob(id=3, fanpage_id=brand.id, design_engine="template", updated_at=today_utc)
    j4 = PublishJob(id=4, fanpage_id=brand.id, design_engine="chatgpt", updated_at=old_utc)
    db_session.add_all([j1, j2, j3, j4])
    db_session.commit()

    res = client.get(f"/social-media/brands/{brand.id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == brand.id
    assert data["ai_images_today"]["chatgpt"] == 2
    assert data["ai_images_today"]["template"] == 1
    assert data["ai_images_today"]["codex"] == 0
    assert data["ai_images_today"]["codex_fallback"] == 0
    assert "day_start_wib" in data and data["day_start_wib"]


# ==============================================================================
# 3. PUT /social-media/brands/{brand_id}/gptcli
# ==============================================================================


def test_brand_gptcli_toggle_idempotent_and_404(db_session, client):
    """Enable twice keeps 1 entry, disable removes it, other ids untouched, 404 unknown."""
    res_404 = client.put("/social-media/brands/999/gptcli", json={"enabled": True})
    assert res_404.status_code == 404

    brand = TargetFanpage(id=5, name="Brand 5", is_active=True)
    db_session.add(brand)

    settings = db_session.query(Settings).filter_by(id=1).first()
    settings.gptcli_fanpage_ids = [42]
    db_session.commit()

    # Enable for brand 5
    res1 = client.put(f"/social-media/brands/{brand.id}/gptcli", json={"enabled": True})
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["enabled_for_brand"] is True
    assert d1["gptcli_fanpage_ids"] == [5, 42]

    # Enable again (idempotent)
    res2 = client.put(f"/social-media/brands/{brand.id}/gptcli", json={"enabled": True})
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["gptcli_fanpage_ids"] == [5, 42]

    # Disable for brand 5
    res3 = client.put(f"/social-media/brands/{brand.id}/gptcli", json={"enabled": False})
    assert res3.status_code == 200
    d3 = res3.json()
    assert d3["enabled_for_brand"] is False
    assert d3["gptcli_fanpage_ids"] == [42]


# ==============================================================================
# 4. /social-media/destinations/{destination_id}/estimated-times
# ==============================================================================


def test_destination_estimated_times_and_validation(db_session, client, monkeypatch):
    """Estimated times calculation, monkeypatched clock, published slot first, 422, 404."""
    fixed_now = datetime(2026, 10, 18, 8, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(social_brands, "_utcnow", lambda: fixed_now)

    # 404 for unknown destination
    res_404 = client.get("/social-media/destinations/999/estimated-times")
    assert res_404.status_code == 404

    brand = TargetFanpage(id=1, name="Test Brand", is_active=True, timezone="Europe/London")
    db_session.add(brand)
    db_session.commit()

    # Destination with invalid timezone -> 422
    dest_inv = SocialDestination(
        id=90,
        content_project_id=brand.id,
        kind="facebook_page",
        name="Invalid TZ",
        timezone="Not/A_Real_Timezone",
    )
    db_session.add(dest_inv)
    db_session.commit()

    res_422 = client.get(f"/social-media/destinations/{dest_inv.id}/estimated-times")
    assert res_422.status_code == 422
    assert "Invalid destination timezone" in res_422.json()["detail"]

    # Destination with mixed 2/2, daily_target 4, Europe/London
    dest = SocialDestination(
        id=100,
        content_project_id=brand.id,
        kind="facebook_page",
        name="Valid London Channel",
        timezone="Europe/London",
        mode="mixed",
        daily_target=4,
        phone_daily_target=2,
        repliz_daily_target=2,
        is_active=True,
    )
    db_session.add(dest)
    db_session.commit()

    res_est = client.get(f"/social-media/destinations/{dest.id}/estimated-times")
    assert res_est.status_code == 200
    d_est = res_est.json()
    assert d_est["daily_target"] == 4
    assert len(d_est["slots"]) == 4

    # All slots sorted and status == "estimated"
    # methods follow i % 4 < 2 -> phone, else repliz
    for i, slot in enumerate(d_est["slots"]):
        assert slot["index"] == i
        assert slot["status"] == "estimated"
        expected_method = "phone" if (i % 4 < 2) else "repliz"
        assert slot["method"] == expected_method
        assert slot["at"].startswith("2026-10-18")

    # Now add an existing published delivery earlier today (6:00 UTC)
    job = PublishJob(id=10, fanpage_id=brand.id)
    db_session.add(job)
    db_session.commit()

    deliv_pub = PublishDelivery(
        id=501,
        publish_job_id=job.id,
        destination_id=dest.id,
        state="published",
        published_at=datetime(2026, 10, 18, 6, 0, 0),
        actual_method="phone",
    )
    db_session.add(deliv_pub)
    db_session.commit()

    res_with_pub = client.get(f"/social-media/destinations/{dest.id}/estimated-times")
    assert res_with_pub.status_code == 200
    d_with_pub = res_with_pub.json()
    assert len(d_with_pub["slots"]) == 4
    # First slot is published
    assert d_with_pub["slots"][0]["status"] == "published"
    assert d_with_pub["slots"][0]["delivery_id"] == 501
    assert d_with_pub["slots"][0]["method"] == "phone"


# ==============================================================================
# 5. /social-media/destinations/{destination_id}/recent
# ==============================================================================


def test_destination_recent_deliveries(db_session, client):
    """Recent deliveries ordering newest first, design_engine, limit validation."""
    res_404 = client.get("/social-media/destinations/999/recent")
    assert res_404.status_code == 404

    brand = TargetFanpage(id=1, name="Brand 1", is_active=True)
    dest = SocialDestination(id=1, content_project_id=1, kind="facebook_page", name="Dest 1")
    db_session.add_all([brand, dest])
    db_session.commit()

    # limit 0 -> 422
    res_0 = client.get(f"/social-media/destinations/{dest.id}/recent?limit=0")
    assert res_0.status_code == 422

    now = datetime(2026, 10, 18, 12, 0, 0)
    j1 = PublishJob(id=1, fanpage_id=1, design_title="Job 1 Title", design_engine="chatgpt")
    j2 = PublishJob(id=2, fanpage_id=1, design_title="Job 2 Title", design_engine="codex")
    db_session.add_all([j1, j2])
    db_session.commit()

    d1 = PublishDelivery(
        id=1,
        publish_job_id=1,
        destination_id=dest.id,
        scheduled_for=now - timedelta(hours=2),
    )
    d2 = PublishDelivery(
        id=2,
        publish_job_id=2,
        destination_id=dest.id,
        scheduled_for=now - timedelta(hours=1),
    )
    db_session.add_all([d1, d2])
    db_session.commit()

    res = client.get(f"/social-media/destinations/{dest.id}/recent?limit=20")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2
    # Newest first: d2 then d1
    assert data[0]["id"] == 2
    assert data[0]["design_engine"] == "codex"
    assert data[0]["title"] == "Job 2 Title"
    assert data[1]["id"] == 1
    assert data[1]["design_engine"] == "chatgpt"


# ==============================================================================
# 6. /social-media/repliz-accounts
# ==============================================================================


def test_repliz_accounts(db_session, client, monkeypatch):
    """Repliz accounts error handling and matching used_by."""
    # 1. get_repliz_client_from_db raises RuntimeError -> configured=False
    def _mock_runtime_err(db):
        raise RuntimeError("No creds")

    monkeypatch.setattr(social_brands, "get_repliz_client_from_db", _mock_runtime_err)
    res1 = client.get("/social-media/repliz-accounts")
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["configured"] is False
    assert d1["error"] is None
    assert d1["accounts"] == []

    # 2. list_facebook_accounts raises Exception -> error string
    class MockClientErr:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def list_facebook_accounts(self):
            raise ConnectionError("Timeout connecting to Repliz")

    monkeypatch.setattr(social_brands, "get_repliz_client_from_db", lambda db: MockClientErr())
    res2 = client.get("/social-media/repliz-accounts")
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["configured"] is True
    assert "Could not reach Repliz (ConnectionError)" in d2["error"]
    assert d2["accounts"] == []

    # 3. 2 accounts returned, one matching destination
    brand = TargetFanpage(id=1, name="B1")
    dest = SocialDestination(
        id=20,
        content_project_id=1,
        kind="facebook_page",
        name="Matched Dest",
        repliz_account_id="rep_acc_1",
    )
    db_session.add_all([brand, dest])
    db_session.commit()

    class MockClientOk:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def list_facebook_accounts(self):
            return [
                {
                    "_id": "rep_acc_1",
                    "name": "Repliz Page 1",
                    "username": "page1",
                    "picture": "http://img/1.png",
                },
                {
                    "id": "rep_acc_2",
                    "name": "Repliz Page 2",
                    "username": "page2",
                },
            ]

    monkeypatch.setattr(social_brands, "get_repliz_client_from_db", lambda db: MockClientOk())
    res3 = client.get("/social-media/repliz-accounts")
    assert res3.status_code == 200
    d3 = res3.json()
    assert d3["configured"] is True
    assert d3["error"] is None
    assert len(d3["accounts"]) == 2

    # First matches dest 20
    assert d3["accounts"][0]["id"] == "rep_acc_1"
    assert d3["accounts"][0]["used_by_destination_id"] == 20
    assert d3["accounts"][0]["used_by_destination_name"] == "Matched Dest"

    # Second is unassigned
    assert d3["accounts"][1]["id"] == "rep_acc_2"
    assert d3["accounts"][1]["used_by_destination_id"] is None
    assert d3["accounts"][1]["used_by_destination_name"] is None
