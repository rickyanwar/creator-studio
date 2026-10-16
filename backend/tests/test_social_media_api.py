"""Integration and contract tests for Social Media, Content Projects, Devices, and Assignments APIs."""

from datetime import datetime, timezone
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
from app.main import app
from app.models.publish_jobs import PublishJob
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
    ]
    for table in tables:
        table.create(bind=engine)

    Session = sessionmaker(bind=engine)
    session = Session()
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
# 1. Authentication Guards
# ==============================================================================


def test_auth_required_for_all_endpoints(db_session):
    """Endpoints reject requests when no valid authentication credentials provided."""
    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = lambda: db_session
    raw_client = TestClient(app)

    endpoints = [
        ("GET", "/content-projects"),
        ("POST", "/content-projects"),
        ("GET", "/social-media/destinations"),
        ("POST", "/social-media/destinations"),
        ("GET", "/social-media/devices"),
        ("GET", "/social-media/devices/discovery"),
        ("POST", "/social-media/devices"),
        ("GET", "/social-media/assignments"),
        ("POST", "/social-media/assignments"),
    ]
    for method, path in endpoints:
        resp = raw_client.request(method, path)
        assert resp.status_code in (401, 403), f"{method} {path} should require auth, got {resp.status_code}"
    app.dependency_overrides.clear()


# ==============================================================================
# 2. Content Projects API
# ==============================================================================


def test_content_projects_crud(client):
    """Create local project without Repliz, list with destination count, and get summary."""
    # Initially empty list
    resp = client.get("/content-projects")
    assert resp.status_code == 200
    assert resp.json() == []

    # Create local project
    create_payload = {
        "name": "Motorsport News UK",
        "timezone": "Europe/London",
    }
    resp = client.post("/content-projects", json=create_payload)
    assert resp.status_code == 201
    proj = resp.json()
    assert proj["name"] == "Motorsport News UK"
    assert proj["repliz_account_id"] is None
    assert proj["is_connected"] is True
    assert proj["is_active"] is False
    assert proj["destination_count"] == 0
    proj_id = proj["id"]

    # Reject duplicate name
    resp = client.post("/content-projects", json=create_payload)
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]

    # Reject blank name
    resp = client.post("/content-projects", json={"name": "   "})
    assert resp.status_code in (400, 422)

    # Get by ID
    resp = client.get(f"/content-projects/{proj_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == proj_id
    assert resp.json()["destination_count"] == 0

    # Nonexistent project returns 404
    resp = client.get("/content-projects/99999")
    assert resp.status_code == 404


# ==============================================================================
# 3. Social Destinations Schema Validation
# ==============================================================================


def test_destination_schema_validation_rejects_invalid_inputs(client):
    """Destination creation strictly validates platform kind, mode, timezone, quotas, caps, media."""
    # Create project first
    proj_resp = client.post("/content-projects", json={"name": "Test Project"})
    proj_id = proj_resp.json()["id"]

    base = {
        "content_project_id": proj_id,
        "name": "Target",
        "kind": "facebook_profile",
        "mode": "phone",
        "daily_target": 1,
        "phone_daily_target": 1,
        "repliz_daily_target": 0,
        "min_gap_minutes": 15,
        "supported_media": ["image", "album", "video"],
        "is_active": False,
    }

    # Unsupported kind
    payload = dict(base, kind="instagram_page")
    assert client.post("/social-media/destinations", json=payload).status_code in (400, 422)

    # Unsupported mode
    payload = dict(base, mode="postal_carrier")
    assert client.post("/social-media/destinations", json=payload).status_code in (400, 422)

    # Invalid IANA timezone
    payload = dict(base, timezone="Mars/Phobos")
    assert client.post("/social-media/destinations", json=payload).status_code in (400, 422)

    # Nonpositive daily_target
    payload = dict(base, daily_target=0, phone_daily_target=0)
    assert client.post("/social-media/destinations", json=payload).status_code in (400, 422)

    # Nonpositive min_gap_minutes
    payload = dict(base, min_gap_minutes=0)
    assert client.post("/social-media/destinations", json=payload).status_code in (400, 422)

    # Quota sum mismatch
    payload = dict(base, daily_target=5, phone_daily_target=2, repliz_daily_target=2)
    assert client.post("/social-media/destinations", json=payload).status_code in (400, 422)

    # Invalid supported media
    payload = dict(base, supported_media=["audio_cassette"])
    assert client.post("/social-media/destinations", json=payload).status_code in (400, 422)


# ==============================================================================
# 4. Social Destinations Route Policy and Hierarchy Rules
# ==============================================================================


def test_destination_route_policy_and_hierarchy(client):
    """Enforces profile phone-only rule, page parent requirements, repliz constraints, and quota integrity."""
    proj_resp = client.post("/content-projects", json={"name": "Hierarchy Project"})
    proj_id = proj_resp.json()["id"]

    # 1. Profile cannot have parent_profile_id or repliz_account_id
    payload = {
        "content_project_id": proj_id,
        "name": "Invalid Profile",
        "kind": "facebook_profile",
        "mode": "phone",
        "parent_profile_id": 999,
        "daily_target": 1,
        "phone_daily_target": 1,
        "repliz_daily_target": 0,
    }
    resp = client.post("/social-media/destinations", json=payload)
    assert resp.status_code == 400
    assert "parent" in resp.json()["detail"].lower()

    payload = {
        "content_project_id": proj_id,
        "name": "Invalid Profile Repliz",
        "kind": "facebook_profile",
        "mode": "phone",
        "repliz_account_id": "rep_999",
        "daily_target": 1,
        "phone_daily_target": 1,
        "repliz_daily_target": 0,
    }
    resp = client.post("/social-media/destinations", json=payload)
    assert resp.status_code == 400
    assert "repliz" in resp.json()["detail"].lower()

    # Create valid inactive profile destination
    prof_payload = {
        "content_project_id": proj_id,
        "name": "Main Profile Driver",
        "kind": "facebook_profile",
        "mode": "phone",
        "facebook_actor_id": "prof_actor_100",
        "daily_target": 2,
        "phone_daily_target": 2,
        "repliz_daily_target": 0,
        "is_active": False,
    }
    resp = client.post("/social-media/destinations", json=prof_payload)
    assert resp.status_code == 201
    profile_id = resp.json()["id"]

    # 2. Page in Phone mode requires an existing parent Profile
    page_phone_no_parent = {
        "content_project_id": proj_id,
        "name": "Page Phone No Parent",
        "kind": "facebook_page",
        "mode": "phone",
        "daily_target": 1,
        "phone_daily_target": 1,
        "repliz_daily_target": 0,
    }
    resp = client.post("/social-media/destinations", json=page_phone_no_parent)
    assert resp.status_code == 400
    assert "parent" in resp.json()["detail"].lower()

    # Create valid Page Phone destination under profile
    page_phone_payload = {
        "content_project_id": proj_id,
        "name": "Page Phone Under Profile",
        "kind": "facebook_page",
        "mode": "phone",
        "parent_profile_id": profile_id,
        "facebook_actor_id": "page_actor_200",
        "daily_target": 2,
        "phone_daily_target": 2,
        "repliz_daily_target": 0,
        "is_active": False,
    }
    resp = client.post("/social-media/destinations", json=page_phone_payload)
    assert resp.status_code == 201
    page_phone_id = resp.json()["id"]

    # Page cannot point to another Page as parent
    page_invalid_parent = {
        "content_project_id": proj_id,
        "name": "Page Invalid Parent",
        "kind": "facebook_page",
        "mode": "phone",
        "parent_profile_id": page_phone_id,  # pointing to page instead of profile
        "daily_target": 1,
        "phone_daily_target": 1,
        "repliz_daily_target": 0,
    }
    resp = client.post("/social-media/destinations", json=page_invalid_parent)
    assert resp.status_code == 400
    assert "profile" in resp.json()["detail"].lower()

    # 3. Page Repliz mode requires repliz_account_id and rejects parent_profile_id
    page_repliz_payload = {
        "content_project_id": proj_id,
        "name": "Page Repliz Cloud",
        "kind": "facebook_page",
        "mode": "repliz",
        "repliz_account_id": "repliz_acc_777",
        "daily_target": 5,
        "phone_daily_target": 0,
        "repliz_daily_target": 5,
        "is_active": True,
    }
    resp = client.post("/social-media/destinations", json=page_repliz_payload)
    assert resp.status_code == 201
    page_repliz_id = resp.json()["id"]

    # Reject duplicate repliz_account_id
    duplicate_repliz = dict(page_repliz_payload, name="Duplicate Repliz")
    resp = client.post("/social-media/destinations", json=duplicate_repliz)
    assert resp.status_code == 400
    assert "already in use" in resp.json()["detail"].lower()

    # 4. Page Mixed mode requires parent Profile AND repliz_account_id
    page_mixed_payload = {
        "content_project_id": proj_id,
        "name": "Page Mixed Route",
        "kind": "facebook_page",
        "mode": "mixed",
        "parent_profile_id": profile_id,
        "repliz_account_id": "repliz_acc_888",
        "facebook_actor_id": "page_actor_300",
        "daily_target": 6,
        "phone_daily_target": 1,
        "repliz_daily_target": 5,
        "is_active": False,
    }
    resp = client.post("/social-media/destinations", json=page_mixed_payload)
    assert resp.status_code == 201

    # Check project destination count updated
    resp = client.get(f"/content-projects/{proj_id}")
    assert resp.status_code == 200
    assert resp.json()["destination_count"] == 4


# ==============================================================================
# 5. Destination Activation and Nonterminal Delivery Guards
# ==============================================================================


def test_destination_activation_guards(client, db_session):
    """Cannot activate Phone routes without stable actor IDs and verified device assignments."""
    proj = TargetFanpage(name="Act Proj", is_connected=True)
    db_session.add(proj)
    db_session.commit()

    # Attempt to create active phone route without assignments
    resp = client.post(
        "/social-media/destinations",
        json={
            "content_project_id": proj.id,
            "name": "Active Direct Profile",
            "kind": "facebook_profile",
            "mode": "phone",
            "facebook_actor_id": "act_101",
            "daily_target": 1,
            "phone_daily_target": 1,
            "repliz_daily_target": 0,
            "is_active": True,
        },
    )
    assert resp.status_code == 400
    assert "verified" in resp.json()["detail"].lower()

    # Create inactive draft
    resp = client.post(
        "/social-media/destinations",
        json={
            "content_project_id": proj.id,
            "name": "Draft Profile",
            "kind": "facebook_profile",
            "mode": "phone",
            "facebook_actor_id": "act_101",
            "daily_target": 1,
            "phone_daily_target": 1,
            "repliz_daily_target": 0,
            "is_active": False,
        },
    )
    assert resp.status_code == 201
    dest_id = resp.json()["id"]

    # Try activating via PATCH without verified assignment
    patch_resp = client.patch(f"/social-media/destinations/{dest_id}", json={"is_active": True})
    assert patch_resp.status_code == 400
    assert "verified" in patch_resp.json()["detail"].lower()


def test_active_destination_blocks_mutation_under_nonterminal_delivery(client, db_session):
    """Prevents changing kind, parent, project, or actor when nonterminal deliveries exist."""
    proj = TargetFanpage(name="Delivery Proj", is_connected=True)
    db_session.add(proj)
    db_session.commit()

    dest = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_page",
        name="Active Repliz Page",
        mode="repliz",
        repliz_account_id="rep_term_1",
        daily_target=3,
        phone_daily_target=0,
        repliz_daily_target=3,
        is_active=True,
    )
    db_session.add(dest)
    db_session.commit()

    # Create a nonterminal delivery
    deliv = PublishDelivery(
        publish_job_id=1,  # fake job id
        destination_id=dest.id,
        state="scheduled",
    )
    db_session.add(deliv)
    db_session.commit()

    # Attempting to mutate actor/kind while delivery is scheduled must fail
    resp = client.patch(
        f"/social-media/destinations/{dest.id}",
        json={"kind": "facebook_profile", "mode": "phone", "phone_daily_target": 3, "repliz_daily_target": 0},
    )
    assert resp.status_code == 400
    assert "nonterminal" in resp.json()["detail"].lower()


def test_destination_soft_delete(client, db_session):
    """DELETE /social-media/destinations/{id} sets is_active=False so history survives."""
    proj = TargetFanpage(name="Delete Proj", is_connected=True)
    db_session.add(proj)
    db_session.commit()

    dest = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_page",
        name="To Delete Page",
        mode="repliz",
        repliz_account_id="rep_del_1",
        daily_target=1,
        phone_daily_target=0,
        repliz_daily_target=1,
        is_active=True,
    )
    db_session.add(dest)
    db_session.commit()

    resp = client.delete(f"/social-media/destinations/{dest.id}")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # Still queryable in database
    db_dest = db_session.query(SocialDestination).filter_by(id=dest.id).first()
    assert db_dest is not None
    assert db_dest.is_active is False


# ==============================================================================
# 6. Devices API and Security
# ==============================================================================


def test_device_registration_and_validation(client):
    """Phone device registration rejects dangerous commands, unvalidated IPs, and available status without actor ID."""
    # Invalid IP
    bad_ip = {
        "name": "Bad IP Phone",
        "tailscale_ip": "not_an_ip",
    }
    assert client.post("/social-media/devices", json=bad_ip).status_code in (400, 422)

    # Shell injection attempt in adb_serial
    bad_serial = {
        "name": "Injected Phone",
        "adb_serial": "serial; rm -rf /",
    }
    assert client.post("/social-media/devices", json=bad_serial).status_code in (400, 422)

    # Status available requires valid nonempty adb_serial (connection identity)
    bad_available = {
        "name": "Premature Available Phone",
        "status": "available",
        "adb_serial": None,
    }
    resp = client.post("/social-media/devices", json=bad_available)
    assert resp.status_code in (400, 422)
    assert "adb_serial" in resp.text

    # Valid maintenance device registration without Facebook actor
    valid_dev = {
        "name": "Realme GT Master",
        "tailscale_node_id": "ts_node_realme_1",
        "tailscale_ip": "100.64.0.15",
        "adb_serial": "192.168.1.150:5555",
        "screen_width": 1080,
        "screen_height": 2400,
        "status": "maintenance",
    }
    resp = client.post("/social-media/devices", json=valid_dev)
    assert resp.status_code == 201
    dev = resp.json()
    assert dev["name"] == "Realme GT Master"
    assert dev["status"] == "maintenance"
    assert dev["expected_profile_actor_id"] is None
    dev_id = dev["id"]

    # Transition to available once adb_serial is present (Facebook actor optional)
    patch_resp = client.patch(f"/social-media/devices/{dev_id}", json={"status": "available"})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "available"
    assert patch_resp.json()["expected_profile_actor_id"] is None


# ==============================================================================
# 7. Assignments and Dry-run Verification
# ==============================================================================


def test_assignment_upsert_verification_and_activation_flow(client, db_session, monkeypatch):
    """Browser creates unverified assignment; dry-run callback verifies hierarchy; destination can then activate."""
    monkeypatch.setenv("PHONE_RUNNER_TOKEN", "secret-runner-token")
    auth_headers = {"X-Dry-Run-Callback": "secret-runner-token"}

    proj = TargetFanpage(name="Assign Proj", is_connected=True)
    db_session.add(proj)
    db_session.commit()

    # Create profile destination
    prof = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_profile",
        name="Main Profile",
        mode="phone",
        facebook_actor_id="actor_prof_1",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=False,
    )
    db_session.add(prof)

    # Create phone device
    dev = PhoneDevice(
        name="Realme Test",
        adb_serial="serial_123",
        expected_profile_actor_id="actor_prof_1",
        status="available",
        calibration_verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(dev)
    db_session.commit()

    # 1. Browser creates unverified assignment (arbitrary claims ignored)
    assign_payload = {
        "destination_id": prof.id,
        "device_id": dev.id,
        "priority": 0,
        "is_active": True,
        "verified_profile_actor_id": "arbitrary_browser_claim",
    }
    resp = client.post("/social-media/assignments", json=assign_payload)
    assert resp.status_code == 200
    assign = resp.json()
    assert assign["verified_at"] is None
    assert assign["verified_profile_actor_id"] is None

    # Destination cannot activate yet
    act_resp = client.patch(f"/social-media/destinations/{prof.id}", json={"is_active": True})
    assert act_resp.status_code == 400
    assert "verified" in act_resp.json()["detail"].lower()

    # 1b. Spoofed callback header is rejected with 401
    spoof_resp = client.post(
        "/social-media/assignments",
        json=assign_payload,
        headers={"X-Dry-Run-Callback": "invalid-spoofed-token"},
    )
    assert spoof_resp.status_code == 401

    # 2. Verification without runner token is rejected with 401
    no_token_resp = client.post(
        "/social-media/assignments/verify",
        json={
            "destination_id": prof.id,
            "device_id": dev.id,
            "verified_profile_actor_id": "actor_prof_1",
        },
    )
    assert no_token_resp.status_code == 401

    # Verification with wrong token is rejected with 401
    wrong_token_resp = client.post(
        "/social-media/assignments/verify",
        headers={"X-Dry-Run-Callback": "wrong-token"},
        json={
            "destination_id": prof.id,
            "device_id": dev.id,
            "verified_profile_actor_id": "actor_prof_1",
        },
    )
    assert wrong_token_resp.status_code == 401

    # Verification with mismatched actor ID is rejected
    mismatch_resp = client.post(
        "/social-media/assignments/verify",
        headers=auth_headers,
        json={
            "destination_id": prof.id,
            "device_id": dev.id,
            "verified_profile_actor_id": "wrong_actor",
            "verified_target_actor_id": "wrong_actor",
        },
    )
    assert mismatch_resp.status_code == 400
    assert "must match" in mismatch_resp.json()["detail"]

    # Missing target actor ID is rejected
    missing_target_resp = client.post(
        "/social-media/assignments/verify",
        headers=auth_headers,
        json={
            "destination_id": prof.id,
            "device_id": dev.id,
            "verified_profile_actor_id": "actor_prof_1",
        },
    )
    assert missing_target_resp.status_code == 400
    assert "verified_target_actor_id is required" in missing_target_resp.json()["detail"]

    # Conflicting target actor ID is rejected
    conflict_target_resp = client.post(
        "/social-media/assignments/verify",
        headers=auth_headers,
        json={
            "destination_id": prof.id,
            "device_id": dev.id,
            "verified_profile_actor_id": "actor_prof_1",
            "verified_target_actor_id": "other_target_actor",
        },
    )
    assert conflict_target_resp.status_code == 400
    assert "must match" in conflict_target_resp.json()["detail"]

    # 3. Successful verification with valid runner token
    verify_resp = client.post(
        "/social-media/assignments/verify",
        headers=auth_headers,
        json={
            "destination_id": prof.id,
            "device_id": dev.id,
            "verified_profile_actor_id": "actor_prof_1",
            "verified_target_actor_id": "actor_prof_1",
        },
    )
    assert verify_resp.status_code == 200
    verified_assign = verify_resp.json()
    assert verified_assign["verified_at"] is not None
    assert verified_assign["verified_profile_actor_id"] == "actor_prof_1"
    assert verified_assign["verified_target_actor_id"] == "actor_prof_1"

    # 4. Now destination can be activated!
    act_resp = client.patch(f"/social-media/destinations/{prof.id}", json={"is_active": True})
    assert act_resp.status_code == 200
    assert act_resp.json()["is_active"] is True

    # 5. Page assignment verification tests hierarchy
    page = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_page",
        name="Child Page",
        parent_profile_id=prof.id,
        mode="phone",
        facebook_actor_id="actor_page_1",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=False,
    )
    db_session.add(page)
    db_session.commit()

    client.post(
        "/social-media/assignments",
        json={"destination_id": page.id, "device_id": dev.id, "priority": 0},
    )

    # Verify page assignment: requires parent profile actor AND page actor
    bad_page_verify = client.post(
        "/social-media/assignments/verify",
        headers=auth_headers,
        json={
            "destination_id": page.id,
            "device_id": dev.id,
            "verified_profile_actor_id": "actor_prof_1",
            "verified_target_actor_id": "wrong_page_actor",
        },
    )
    assert bad_page_verify.status_code == 400

    good_page_verify = client.post(
        "/social-media/assignments/verify",
        headers=auth_headers,
        json={
            "destination_id": page.id,
            "device_id": dev.id,
            "verified_profile_actor_id": "actor_prof_1",
            "verified_target_actor_id": "actor_page_1",
        },
    )
    assert good_page_verify.status_code == 200
    assert good_page_verify.json()["verified_at"] is not None


    # Now page can also be activated
    act_page = client.patch(f"/social-media/destinations/{page.id}", json={"is_active": True})
    assert act_page.status_code == 200
    assert act_page.json()["is_active"] is True

    # 6. Deactivating last verified assignment while page is active fails with 409
    del_fail = client.delete(f"/social-media/assignments/{page.id}/{dev.id}")
    assert del_fail.status_code == 409

    # Deactivating page destination allows deactivating assignment
    client.patch(f"/social-media/destinations/{page.id}", json={"is_active": False})
    del_resp = client.delete(f"/social-media/assignments/{page.id}/{dev.id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["is_active"] is False


def test_page_profile_project_isolation_create_and_update(client):
    """Page using phone or mixed mode must have parent profile from same project; repliz-only may omit parent."""
    # Create two content projects
    p1_resp = client.post("/content-projects", json={"name": "Project Alpha"})
    assert p1_resp.status_code == 201
    proj1_id = p1_resp.json()["id"]

    p2_resp = client.post("/content-projects", json={"name": "Project Beta"})
    assert p2_resp.status_code == 201
    proj2_id = p2_resp.json()["id"]

    # Profile in Project 1
    prof1_resp = client.post(
        "/social-media/destinations",
        json={
            "content_project_id": proj1_id,
            "name": "Profile Project 1",
            "kind": "facebook_profile",
            "mode": "phone",
            "facebook_actor_id": "actor_prof_p1",
            "daily_target": 1,
            "phone_daily_target": 1,
            "repliz_daily_target": 0,
            "is_active": False,
        },
    )
    assert prof1_resp.status_code == 201
    prof1_id = prof1_resp.json()["id"]

    # Profile in Project 2
    prof2_resp = client.post(
        "/social-media/destinations",
        json={
            "content_project_id": proj2_id,
            "name": "Profile Project 2",
            "kind": "facebook_profile",
            "mode": "phone",
            "facebook_actor_id": "actor_prof_p2",
            "daily_target": 1,
            "phone_daily_target": 1,
            "repliz_daily_target": 0,
            "is_active": False,
        },
    )
    assert prof2_resp.status_code == 201
    prof2_id = prof2_resp.json()["id"]

    # 1. Create phone Page in proj1 with parent from proj2 -> accepted (201)
    cross_phone_resp = client.post(
        "/social-media/destinations",
        json={
            "content_project_id": proj1_id,
            "name": "Cross Project Page Phone",
            "kind": "facebook_page",
            "mode": "phone",
            "parent_profile_id": prof2_id,
            "facebook_actor_id": "actor_page_cross",
            "daily_target": 1,
            "phone_daily_target": 1,
            "repliz_daily_target": 0,
            "is_active": False,
        },
    )
    assert cross_phone_resp.status_code == 201
    assert cross_phone_resp.json()["parent_profile_id"] == prof2_id
    assert cross_phone_resp.json()["content_project_id"] == proj1_id

    # 2. Create mixed Page in proj1 with parent from proj2 -> accepted (201)
    cross_mixed_resp = client.post(
        "/social-media/destinations",
        json={
            "content_project_id": proj1_id,
            "name": "Cross Project Page Mixed",
            "kind": "facebook_page",
            "mode": "mixed",
            "parent_profile_id": prof2_id,
            "repliz_account_id": "rep_cross_mixed",
            "facebook_actor_id": "actor_page_cross_mix",
            "daily_target": 2,
            "phone_daily_target": 1,
            "repliz_daily_target": 1,
            "is_active": False,
        },
    )
    assert cross_mixed_resp.status_code == 201
    assert cross_mixed_resp.json()["parent_profile_id"] == prof2_id
    assert cross_mixed_resp.json()["content_project_id"] == proj1_id

    # 3. Create repliz-only Page in proj1 without parent -> accepted (201)
    repliz_page_resp = client.post(
        "/social-media/destinations",
        json={
            "content_project_id": proj1_id,
            "name": "Repliz Page No Parent",
            "kind": "facebook_page",
            "mode": "repliz",
            "repliz_account_id": "rep_p1_solo",
            "daily_target": 1,
            "phone_daily_target": 0,
            "repliz_daily_target": 1,
            "is_active": False,
        },
    )
    assert repliz_page_resp.status_code == 201

    # 4. Same-project Page/Profile remains accepted on create
    same_page_resp = client.post(
        "/social-media/destinations",
        json={
            "content_project_id": proj1_id,
            "name": "Same Project Page Phone",
            "kind": "facebook_page",
            "mode": "phone",
            "parent_profile_id": prof1_id,
            "facebook_actor_id": "actor_page_same",
            "daily_target": 1,
            "phone_daily_target": 1,
            "repliz_daily_target": 0,
            "is_active": False,
        },
    )
    assert same_page_resp.status_code == 201
    page1_id = same_page_resp.json()["id"]

    # 5. Update page parent to cross-project profile -> accepted (200)
    up_cross_parent = client.patch(
        f"/social-media/destinations/{page1_id}",
        json={"parent_profile_id": prof2_id},
    )
    assert up_cross_parent.status_code == 200
    assert up_cross_parent.json()["parent_profile_id"] == prof2_id

    # Reset parent back to prof1 so page has parent in proj1
    client.patch(
        f"/social-media/destinations/{page1_id}",
        json={"parent_profile_id": prof1_id},
    )

    # 6. Update page content_project_id to proj2 without changing parent -> accepted (200)
    up_cross_proj = client.patch(
        f"/social-media/destinations/{page1_id}",
        json={"content_project_id": proj2_id},
    )
    assert up_cross_proj.status_code == 200
    assert up_cross_proj.json()["content_project_id"] == proj2_id
    assert up_cross_proj.json()["parent_profile_id"] == prof1_id

    # 7. Update both page content_project_id and parent_profile_id to proj2 -> accepted (200)
    up_both_resp = client.patch(
        f"/social-media/destinations/{page1_id}",
        json={"content_project_id": proj2_id, "parent_profile_id": prof2_id},
    )
    assert up_both_resp.status_code == 200
    assert up_both_resp.json()["content_project_id"] == proj2_id
    assert up_both_resp.json()["parent_profile_id"] == prof2_id


def test_page_parent_profile_must_still_be_a_profile(client):
    """Parent destination across projects must still be a facebook_profile, not a page."""
    p1 = client.post("/content-projects", json={"name": "Project One"}).json()["id"]
    p2 = client.post("/content-projects", json={"name": "Project Two"}).json()["id"]

    page_in_p2 = client.post(
        "/social-media/destinations",
        json={
            "content_project_id": p2,
            "name": "Repliz Page In P2",
            "kind": "facebook_page",
            "mode": "repliz",
            "repliz_account_id": "rep_p2_solo",
            "daily_target": 1,
            "phone_daily_target": 0,
            "repliz_daily_target": 1,
            "is_active": False,
        },
    ).json()["id"]

    create_resp = client.post(
        "/social-media/destinations",
        json={
            "content_project_id": p1,
            "name": "Phone Page In P1",
            "kind": "facebook_page",
            "mode": "phone",
            "parent_profile_id": page_in_p2,
            "facebook_actor_id": "actor_p1_invalid_parent",
            "daily_target": 1,
            "phone_daily_target": 1,
            "repliz_daily_target": 0,
            "is_active": False,
        },
    )
    assert create_resp.status_code == 400
    assert "must be a facebook profile" in create_resp.json()["detail"].lower()

    prof1 = client.post(
        "/social-media/destinations",
        json={
            "content_project_id": p1,
            "name": "Valid Profile P1",
            "kind": "facebook_profile",
            "mode": "phone",
            "facebook_actor_id": "actor_prof1_val",
            "daily_target": 1,
            "phone_daily_target": 1,
            "repliz_daily_target": 0,
            "is_active": False,
        },
    ).json()["id"]

    page_in_p1 = client.post(
        "/social-media/destinations",
        json={
            "content_project_id": p1,
            "name": "Valid Phone Page In P1",
            "kind": "facebook_page",
            "mode": "phone",
            "parent_profile_id": prof1,
            "facebook_actor_id": "actor_p1_val_page",
            "daily_target": 1,
            "phone_daily_target": 1,
            "repliz_daily_target": 0,
            "is_active": False,
        },
    ).json()["id"]

    update_resp = client.patch(
        f"/social-media/destinations/{page_in_p1}",
        json={"parent_profile_id": page_in_p2},
    )
    assert update_resp.status_code == 400
    assert "must be a facebook profile" in update_resp.json()["detail"].lower()


def test_inactive_assignment_priority_reuse_and_active_collision(client, db_session):
    """Deactivated assignment priority can be reused by another active device; two active rows cannot share priority."""
    proj = TargetFanpage(name="Priority Reuse Proj", is_connected=True)
    db_session.add(proj)
    db_session.commit()

    dest = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_profile",
        name="Target Profile",
        mode="phone",
        facebook_actor_id="actor_prio_target",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=False,
    )
    db_session.add(dest)

    dev_a = PhoneDevice(
        name="Device A",
        adb_serial="serial_dev_a",
        expected_profile_actor_id="actor_prio_target",
        status="available",
        calibration_verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    dev_b = PhoneDevice(
        name="Device B",
        adb_serial="serial_dev_b",
        expected_profile_actor_id="actor_prio_target",
        status="available",
        calibration_verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add_all([dev_a, dev_b])
    db_session.commit()

    # 1. Create active priority-0 assignment for device A
    resp_a = client.post(
        "/social-media/assignments",
        json={"destination_id": dest.id, "device_id": dev_a.id, "priority": 0, "is_active": True},
    )
    assert resp_a.status_code == 200
    assert resp_a.json()["priority"] == 0
    assert resp_a.json()["is_active"] is True

    # 2. Assert two active assignments cannot share a priority (device B with priority 0 fails)
    resp_b_coll = client.post(
        "/social-media/assignments",
        json={"destination_id": dest.id, "device_id": dev_b.id, "priority": 0, "is_active": True},
    )
    assert resp_b_coll.status_code == 400
    assert "already assigned" in resp_b_coll.json()["detail"].lower()

    # 3. Deactivate priority-0 assignment for device A
    del_a = client.delete(f"/social-media/assignments/{dest.id}/{dev_a.id}")
    assert del_a.status_code == 200
    assert del_a.json()["is_active"] is False

    # 4. Create active priority-0 assignment for device B and assert success
    resp_b = client.post(
        "/social-media/assignments",
        json={"destination_id": dest.id, "device_id": dev_b.id, "priority": 0, "is_active": True},
    )
    assert resp_b.status_code == 200
    assert resp_b.json()["priority"] == 0
    assert resp_b.json()["is_active"] is True

    # 5. Attempting to reactivate device A at priority 0 while device B is active at priority 0 fails
    reactivate_a_fail = client.post(
        "/social-media/assignments",
        json={"destination_id": dest.id, "device_id": dev_a.id, "priority": 0, "is_active": True},
    )
    assert reactivate_a_fail.status_code == 400
    assert "already assigned" in reactivate_a_fail.json()["detail"].lower()

    # 6. Deactivate device B, then reactivate device A at priority 0 successfully
    del_b = client.delete(f"/social-media/assignments/{dest.id}/{dev_b.id}")
    assert del_b.status_code == 200
    assert del_b.json()["is_active"] is False

    reactivate_a_ok = client.post(
        "/social-media/assignments",
        json={"destination_id": dest.id, "device_id": dev_a.id, "priority": 0, "is_active": True},
    )
    assert reactivate_a_ok.status_code == 200
    assert reactivate_a_ok.json()["priority"] == 0
    assert reactivate_a_ok.json()["is_active"] is True


def test_patch_clear_nullable_fields_and_switch_to_repliz(client, db_session):
    """Verify PATCH can clear nullable fields and switch a Page from Phone to Repliz mode."""
    fp = TargetFanpage(name="Proj Patch", is_active=True)
    db_session.add(fp)
    db_session.flush()

    prof = SocialDestination(
        content_project_id=fp.id,
        kind="facebook_profile",
        name="Main Profile",
        facebook_actor_id="actor_prof_1",
        mode="phone",
        timezone="Europe/London",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        min_gap_minutes=5,
        is_active=False,
    )
    db_session.add(prof)
    db_session.flush()

    page = SocialDestination(
        content_project_id=fp.id,
        kind="facebook_page",
        name="My Page",
        facebook_actor_id="actor_page_1",
        parent_profile_id=prof.id,
        repliz_account_id="rep_acc_1",
        mode="mixed",
        timezone="Europe/London",
        daily_target=2,
        phone_daily_target=1,
        repliz_daily_target=1,
        min_gap_minutes=5,
        is_active=False,
    )
    db_session.add(page)
    db_session.commit()

    # 1. Clear repliz_account_id by sending explicit null, switch to phone mode
    patch1 = client.patch(
        f"/social-media/destinations/{page.id}",
        json={"repliz_account_id": None, "mode": "phone", "phone_daily_target": 2, "repliz_daily_target": 0},
    )
    assert patch1.status_code == 200, patch1.text
    assert patch1.json()["repliz_account_id"] is None
    assert patch1.json()["mode"] == "phone"
    assert patch1.json()["parent_profile_id"] == prof.id

    # 2. Clear facebook_actor_id by sending explicit null
    patch2 = client.patch(
        f"/social-media/destinations/{page.id}",
        json={"facebook_actor_id": None},
    )
    assert patch2.status_code == 200, patch2.text
    assert patch2.json()["facebook_actor_id"] is None

    # 3. Clear parent_profile_id and switch to Repliz mode (which forbids parent profile and requires repliz_account_id)
    patch3 = client.patch(
        f"/social-media/destinations/{page.id}",
        json={
            "mode": "repliz",
            "parent_profile_id": None,
            "repliz_account_id": "rep_new_99",
            "daily_target": 3,
        },
    )
    assert patch3.status_code == 200, patch3.text
    assert patch3.json()["mode"] == "repliz"
    assert patch3.json()["parent_profile_id"] is None
    assert patch3.json()["repliz_account_id"] == "rep_new_99"
    assert patch3.json()["phone_daily_target"] == 0
    assert patch3.json()["repliz_daily_target"] == 3


def test_deactivate_assignment_maintains_active_route(client, db_session):
    """Verify deactivating the last active verified device assignment on an active Phone/Mixed destination returns 409."""
    fp = TargetFanpage(name="Proj Deactivate", is_active=True)
    db_session.add(fp)
    db_session.flush()

    prof = SocialDestination(
        content_project_id=fp.id,
        kind="facebook_profile",
        name="Active Profile",
        facebook_actor_id="actor_p1",
        mode="phone",
        timezone="Europe/London",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        min_gap_minutes=5,
        is_active=True,
    )
    dev1 = PhoneDevice(
        name="Device 1",
        adb_serial="ser1",
        calibration_verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
        status="available",
    )
    dev2 = PhoneDevice(
        name="Device 2",
        adb_serial="ser2",
        calibration_verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
        status="available",
    )
    db_session.add_all([prof, dev1, dev2])
    db_session.flush()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    asgn1 = DestinationDeviceAssignment(
        destination_id=prof.id,
        device_id=dev1.id,
        priority=0,
        is_active=True,
        verified_at=now,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
    )
    asgn2 = DestinationDeviceAssignment(
        destination_id=prof.id,
        device_id=dev2.id,
        priority=1,
        is_active=True,
        verified_at=now,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
    )
    db_session.add_all([asgn1, asgn2])
    db_session.commit()

    # Deactivating asgn2 succeeds because asgn1 remains verified and active
    del2 = client.delete(f"/social-media/assignments/{prof.id}/{dev2.id}")
    assert del2.status_code == 200
    assert del2.json()["is_active"] is False

    # Deactivating asgn1 fails with 409 because prof is active and asgn1 is its last active verified assignment
    del1 = client.delete(f"/social-media/assignments/{prof.id}/{dev1.id}")
    assert del1.status_code == 409
    assert "last active verified device assignment" in del1.json()["detail"].lower()

    # Deactivating prof destination first allows deactivating asgn1
    patch_dest = client.patch(f"/social-media/destinations/{prof.id}", json={"is_active": False})
    assert patch_dest.status_code == 200
    assert patch_dest.json()["is_active"] is False

    del1_ok = client.delete(f"/social-media/assignments/{prof.id}/{dev1.id}")
    assert del1_ok.status_code == 200
    assert del1_ok.json()["is_active"] is False


def test_upsert_assignment_last_verified_device_deactivation_guard(client, db_session):
    """Verify POST /social-media/assignments cannot bypass last active verified assignment guard."""
    proj = TargetFanpage(name="P_PostGuard", is_connected=True)
    db_session.add(proj)
    db_session.commit()

    dev1 = PhoneDevice(
        name="Device Guard 1",
        adb_serial="ser_guard_1",
        expected_profile_actor_id="actor_guard",
        status="available",
        calibration_verified_at=datetime.now(timezone.utc),
    )
    dev2 = PhoneDevice(
        name="Device Guard 2",
        adb_serial="ser_guard_2",
        expected_profile_actor_id="actor_guard",
        status="available",
        calibration_verified_at=datetime.now(timezone.utc),
    )
    db_session.add_all([dev1, dev2])
    db_session.commit()

    prof = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_profile",
        name="Profile Guard",
        mode="phone",
        facebook_actor_id="actor_guard",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=True,
    )
    db_session.add(prof)
    db_session.commit()

    now = datetime.now(timezone.utc)
    asgn1 = DestinationDeviceAssignment(
        destination_id=prof.id,
        device_id=dev1.id,
        priority=0,
        is_active=True,
        verified_at=now,
        verified_profile_actor_id="actor_guard",
        verified_target_actor_id="actor_guard",
    )
    asgn2 = DestinationDeviceAssignment(
        destination_id=prof.id,
        device_id=dev2.id,
        priority=1,
        is_active=True,
        verified_at=now,
        verified_profile_actor_id="actor_guard",
        verified_target_actor_id="actor_guard",
    )
    db_session.add_all([asgn1, asgn2])
    db_session.commit()

    # 1. Deactivating dev2 via POST /assignments succeeds because dev1 remains verified and active
    resp2 = client.post(
        "/social-media/assignments",
        json={"destination_id": prof.id, "device_id": dev2.id, "priority": 1, "is_active": False},
    )
    assert resp2.status_code == 200
    assert resp2.json()["is_active"] is False

    # 2. Deactivating dev1 via POST /assignments fails with 409 (sole active verified device for active destination)
    resp1 = client.post(
        "/social-media/assignments",
        json={"destination_id": prof.id, "device_id": dev1.id, "priority": 0, "is_active": False},
    )
    assert resp1.status_code == 409
    assert "last active verified device assignment" in resp1.json()["detail"].lower()

    # 3. Deactivating destination first allows deactivation of dev1 via POST /assignments
    patch_dest = client.patch(f"/social-media/destinations/{prof.id}", json={"is_active": False})
    assert patch_dest.status_code == 200
    assert patch_dest.json()["is_active"] is False

    resp1_ok = client.post(
        "/social-media/assignments",
        json={"destination_id": prof.id, "device_id": dev1.id, "priority": 0, "is_active": False},
    )
    assert resp1_ok.status_code == 200
    assert resp1_ok.json()["is_active"] is False


def test_device_dry_run_api_flow(client, db_session):
    """Verify POST /social-media/devices/{device_id}/dry-run triggers runner, updates calibration and assignment."""
    proj = TargetFanpage(name="DryRun Proj", is_connected=True)
    db_session.add(proj)
    db_session.commit()

    dev = PhoneDevice(
        name="Realme DryRun",
        adb_serial="serial_dryrun_1",
        expected_profile_actor_id="actor_dry_1",
        status="available",
        calibration_verified_at=None,
    )
    db_session.add(dev)
    db_session.commit()

    prof = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_profile",
        name="Main Profile DryRun",
        mode="phone",
        facebook_actor_id="actor_dry_1",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=False,
    )
    db_session.add(prof)
    db_session.commit()

    asgn = DestinationDeviceAssignment(
        destination_id=prof.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_at=None,
    )
    db_session.add(asgn)
    db_session.commit()

    # 1. Successful device dry-run without destination verifies device calibration
    mock_success_device = {
        "success": True,
        "status": "verified",
        "stages": [{"stage": "identity_verification", "passed": True}],
        "evidence": {
            "resolution": "1080x2400",
            "app_card_version": "1.0.0",
            "facebook_version": "450.0.0",
            "portal_version": "0.7.25",
            "stopped_before_publish": True,
            "verified_main_profile_id": "actor_dry_1",
            "active_composer_actor_id": "actor_dry_1",
        },
    }
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value=mock_success_device):
        resp = client.post(f"/social-media/devices/{dev.id}/dry-run", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["calibration_verified_at"] is not None
        assert data["assignment_verified"] is False
        db_session.refresh(dev)
        assert dev.screen_width == 1080
        assert dev.screen_height == 2400
        assert dev.app_card_version == "1.0.0"

    # 2. Successful dry-run with destination verifies both device and assignment
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value=mock_success_device):
        resp = client.post(f"/social-media/devices/{dev.id}/dry-run", json={"destination_id": prof.id})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["assignment_verified"] is True

        db_session.refresh(asgn)
        assert asgn.verified_at is not None
        assert asgn.verified_profile_actor_id == "actor_dry_1"

    # 3. Failed dry-run returns 400 and fails closed
    mock_failed_run = {
        "success": False,
        "status": "geometry_mismatch",
        "evidence": {"stopped_before_publish": True},
    }
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value=mock_failed_run):
        fail_resp = client.post(f"/social-media/devices/{dev.id}/dry-run", json={})
        assert fail_resp.status_code == 400
        assert "geometry_mismatch" in fail_resp.json()["detail"]


def test_device_dry_run_leased_device_refusal_and_release(client, db_session):
    """Verify dry-run rejects leased devices with 409 and releases lease on completion/failure."""
    from datetime import timedelta
    dev = PhoneDevice(
        name="Leased Device",
        adb_serial="serial_leased_1",
        expected_profile_actor_id="actor_lease_1",
        status="available",
    )
    db_session.add(dev)
    db_session.commit()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Active lease held by another task
    active_lease = DeviceLease(
        device_id=dev.id,
        delivery_id=123,
        acquired_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    db_session.add(active_lease)
    db_session.commit()

    # Must reject with 409
    resp = client.post(f"/social-media/devices/{dev.id}/dry-run", json={})
    assert resp.status_code == 409
    assert "currently leased" in resp.json()["detail"]

    # Delete existing lease to simulate free device
    db_session.delete(active_lease)
    db_session.commit()

    # Dry-run failure should acquire and then release lease in finally
    mock_failed = {"success": False, "status": "portal_unready", "detail": "Connection refused"}
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value=mock_failed):
        resp2 = client.post(f"/social-media/devices/{dev.id}/dry-run", json={})
        assert resp2.status_code == 400

    # Verify no dangling calibration lease left in DB
    remaining_lease = db_session.query(DeviceLease).filter_by(device_id=dev.id).first()
    assert remaining_lease is None


def test_device_dry_run_preexisting_geometry_mismatch_fails(client, db_session):
    """Verify pre-existing geometry mismatch fails closed and leaves calibration unchanged."""
    dev = PhoneDevice(
        name="Pre-geom Device",
        adb_serial="serial_pregeom_1",
        expected_profile_actor_id="actor_pre_1",
        screen_width=1080,
        screen_height=1920,
        status="available",
        calibration_verified_at=None,
    )
    db_session.add(dev)
    db_session.commit()

    # Observed resolution 1080x2400 differs from 1080x1920
    mock_mismatch = {
        "success": True,
        "status": "ready",
        "stages": [{"stage": "geometry", "passed": True}],
        "evidence": {
            "resolution": "1080x2400",
            "app_card_version": "1.0.0",
            "facebook_version": "450.0.0",
            "portal_version": "0.7.25",
            "stopped_before_publish": True,
            "verified_main_profile_id": "actor_pre_1",
        },
    }
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value=mock_mismatch):
        resp = client.post(f"/social-media/devices/{dev.id}/dry-run", json={})
        assert resp.status_code == 400
        assert "geometry mismatch" in resp.json()["detail"].lower()

    db_session.refresh(dev)
    assert dev.calibration_verified_at is None
    assert dev.screen_height == 1920


def test_assignment_verify_runner_token_constant_time(client, db_session, monkeypatch):
    """Verify /assignments/verify requires valid runner token and rejects spoofed headers."""
    monkeypatch.setenv("PHONE_RUNNER_TOKEN", "valid-runner-secret-key")

    proj = TargetFanpage(name="Token Test Proj", is_connected=True)
    db_session.add(proj)
    db_session.commit()

    dev = PhoneDevice(
        name="Token Dev",
        adb_serial="serial_tok_1",
        expected_profile_actor_id="actor_tok_1",
        calibration_verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
        status="available",
    )
    db_session.add(dev)
    db_session.commit()

    prof = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_profile",
        name="Profile Tok",
        mode="phone",
        facebook_actor_id="actor_tok_1",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=False,
    )
    db_session.add(prof)
    db_session.commit()

    asgn = DestinationDeviceAssignment(
        destination_id=prof.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
    )
    db_session.add(asgn)
    db_session.commit()

    verify_payload = {
        "destination_id": prof.id,
        "device_id": dev.id,
        "verified_profile_actor_id": "actor_tok_1",
        "verified_target_actor_id": "actor_tok_1",
    }

    # 1. Missing runner token header -> 401
    resp = client.post("/social-media/assignments/verify", json=verify_payload)
    assert resp.status_code == 401
    assert "Invalid or missing runner token" in resp.json()["detail"]

    # 2. Invalid/spoofed token -> 401
    resp = client.post(
        "/social-media/assignments/verify",
        json=verify_payload,
        headers={"X-Dry-Run-Callback": "invalid-spoofed-token"},
    )
    assert resp.status_code == 401

    # 3. Valid runner token -> 200
    resp = client.post(
        "/social-media/assignments/verify",
        json=verify_payload,
        headers={"X-Dry-Run-Callback": "valid-runner-secret-key"},
    )
    assert resp.status_code == 200
    assert resp.json()["verified_at"] is not None


def test_dry_run_two_roots_one_physical_device_and_device_default_integrity(client, db_session):
    """One physical device shared by two roots; dry-run for Page B verifies Page B without mutating device default."""
    proj = TargetFanpage(name="Shared Device Proj", is_connected=True)
    db_session.add(proj)
    db_session.commit()

    # Device calibrated default root is Root A
    dev = PhoneDevice(
        name="Shared Realme Phone",
        adb_serial="serial_shared_phone_1",
        expected_profile_actor_id="actor_root_a",
        status="available",
        calibration_verified_at=None,
    )
    db_session.add(dev)

    # Root A & Page A
    root_a = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_profile",
        name="Root Profile A",
        mode="phone",
        facebook_actor_id="actor_root_a",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=False,
    )
    db_session.add(root_a)
    db_session.commit()

    page_a = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_page",
        name="Page Dest A",
        mode="phone",
        facebook_actor_id="actor_page_a",
        parent_profile_id=root_a.id,
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=False,
    )
    db_session.add(page_a)

    # Root B & Page B
    root_b = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_profile",
        name="Root Profile B",
        mode="phone",
        facebook_actor_id="actor_root_b",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=False,
    )
    db_session.add(root_b)
    db_session.commit()

    page_b = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_page",
        name="Page Dest B",
        mode="phone",
        facebook_actor_id="actor_page_b",
        parent_profile_id=root_b.id,
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=False,
    )
    db_session.add(page_b)

    asgn_a = DestinationDeviceAssignment(
        destination_id=page_a.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
    )
    asgn_b = DestinationDeviceAssignment(
        destination_id=page_b.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
    )
    db_session.add_all([asgn_a, asgn_b])
    db_session.commit()

    # 1. Page B dry-run on device (device default is Root A, Page B is under Root B)
    mock_runner_page_b = {
        "success": True,
        "status": "verified",
        "stages": [{"stage": "identity_check", "passed": True}],
        "evidence": {
            "resolution": "1080x2400",
            "app_card_version": "1.2.0",
            "facebook_version": "452.0.0",
            "portal_version": "0.7.30",
            "stopped_before_publish": True,
            "verified_main_profile_id": "actor_root_b",
            "active_composer_actor_id": "actor_page_b",
        },
    }

    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value=mock_runner_page_b):
        resp_b = client.post(f"/social-media/devices/{dev.id}/dry-run", json={"destination_id": page_b.id})
        assert resp_b.status_code == 200
        data_b = resp_b.json()
        assert data_b["status"] == "success"
        assert data_b["assignment_verified"] is True

    # Assert only assignment B was verified, and device default root was NOT modified
    db_session.refresh(asgn_b)
    db_session.refresh(asgn_a)
    db_session.refresh(dev)

    assert asgn_b.verified_at is not None
    assert asgn_b.verified_profile_actor_id == "actor_root_b"
    assert asgn_b.verified_target_actor_id == "actor_page_b"

    assert asgn_a.verified_at is None
    assert asgn_a.verified_profile_actor_id is None

    assert dev.expected_profile_actor_id == "actor_root_a"  # Untouched default root!
    assert dev.app_card_version == "1.2.0"
    assert dev.facebook_app_version == "452.0.0"
    assert dev.mobilerun_portal_version == "0.7.30"
    assert dev.calibration_verified_at is not None

    # 2. Device-only dry-run must still require device default Root A
    mock_runner_wrong_root = {
        "success": True,
        "status": "verified",
        "stages": [{"stage": "identity_check", "passed": True}],
        "evidence": {
            "resolution": "1080x2400",
            "app_card_version": "1.2.0",
            "facebook_version": "452.0.0",
            "portal_version": "0.7.30",
            "stopped_before_publish": True,
            "verified_main_profile_id": "actor_root_b",
            "active_composer_actor_id": "actor_root_b",
        },
    }
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value=mock_runner_wrong_root):
        resp_dev_fail = client.post(f"/social-media/devices/{dev.id}/dry-run", json={})
        assert resp_dev_fail.status_code == 400
        assert "Main profile mismatch" in resp_dev_fail.json()["detail"]

    # Device-only dry-run succeeds when host runner confirms device default Root A
    mock_runner_correct_root_a = {
        "success": True,
        "status": "verified",
        "stages": [{"stage": "identity_check", "passed": True}],
        "evidence": {
            "resolution": "1080x2400",
            "app_card_version": "1.2.0",
            "facebook_version": "452.0.0",
            "portal_version": "0.7.30",
            "stopped_before_publish": True,
            "verified_main_profile_id": "actor_root_a",
            "active_composer_actor_id": "actor_root_a",
        },
    }
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value=mock_runner_correct_root_a):
        resp_dev_ok = client.post(f"/social-media/devices/{dev.id}/dry-run", json={})
        assert resp_dev_ok.status_code == 200
        assert resp_dev_ok.json()["status"] == "success"


def test_dry_run_strict_evidence_boolean_and_version_checks(client, db_session):
    """stopped_before_publish and stages.passed must be boolean True; version strings must be present."""
    proj = TargetFanpage(name="Strict Evidence Proj", is_connected=True)
    db_session.add(proj)
    db_session.commit()

    dev = PhoneDevice(
        name="Strict Dev",
        adb_serial="serial_strict_1",
        expected_profile_actor_id="actor_strict_root",
        status="available",
    )
    db_session.add(dev)
    db_session.commit()

    base_evidence = {
        "resolution": "1080x2400",
        "app_card_version": "1.0.0",
        "facebook_version": "450.0.0",
        "portal_version": "0.7.25",
        "stopped_before_publish": True,
        "verified_main_profile_id": "actor_strict_root",
        "active_composer_actor_id": "actor_strict_root",
    }

    # 1. stopped_before_publish as truthy string "true" or 1 -> 500 error
    for truthy_val in ["true", "True", 1, "1"]:
        ev = dict(base_evidence, stopped_before_publish=truthy_val)
        mock_resp = {"success": True, "stages": [{"stage": "s1", "passed": True}], "evidence": ev}
        with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value=mock_resp):
            r = client.post(f"/social-media/devices/{dev.id}/dry-run", json={})
            assert r.status_code == 500
            assert "stopped_before_publish not proven" in r.json()["detail"]

    # 2. stages[0].passed as truthy string "true" or 1 -> 400 error
    for truthy_val in ["true", "True", 1, "1"]:
        mock_resp = {
            "success": True,
            "stages": [{"stage": "composer_check", "passed": truthy_val}],
            "evidence": base_evidence,
        }
        with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value=mock_resp):
            r = client.post(f"/social-media/devices/{dev.id}/dry-run", json={})
            assert r.status_code == 400
            assert "stage 'composer_check' failed" in r.json()["detail"]

    # 3. Missing/empty version values
    for missing_ver_key in ["app_card_version", "facebook_version", "portal_version"]:
        ev = dict(base_evidence)
        ev[missing_ver_key] = ""
        mock_resp = {"success": True, "stages": [{"stage": "s1", "passed": True}], "evidence": ev}
        with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value=mock_resp):
            r = client.post(f"/social-media/devices/{dev.id}/dry-run", json={})
            assert r.status_code == 400
            assert "missing required App Card, Facebook, or Portal version" in r.json()["detail"]


def test_dry_run_rejections_fail_closed_and_do_not_mutate(client, db_session):
    """Wrong root, wrong Page, main-profile context with Page composer, missing composer ID, wrong parent kind fail closed."""
    proj = TargetFanpage(name="Fail Closed Proj", is_connected=True)
    db_session.add(proj)
    db_session.commit()

    dev = PhoneDevice(
        name="Fail Closed Dev",
        adb_serial="serial_fail_closed_1",
        expected_profile_actor_id="actor_root_1",
        status="available",
    )
    db_session.add(dev)

    root_prof = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_profile",
        name="Root Profile",
        mode="phone",
        facebook_actor_id="actor_root_1",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=False,
    )
    db_session.add(root_prof)
    db_session.commit()

    page_dest = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_page",
        name="Page Dest",
        mode="phone",
        facebook_actor_id="actor_page_1",
        parent_profile_id=root_prof.id,
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=False,
    )
    db_session.add(page_dest)
    db_session.commit()

    asgn = DestinationDeviceAssignment(
        destination_id=page_dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
    )
    db_session.add(asgn)
    db_session.commit()

    valid_stages = [{"stage": "verification", "passed": True}]
    valid_ev_base = {
        "resolution": "1080x2400",
        "app_card_version": "1.0.0",
        "facebook_version": "450.0.0",
        "portal_version": "0.7.25",
        "stopped_before_publish": True,
    }

    # 1. Wrong root returned for Page destination
    ev_wrong_root = dict(valid_ev_base, verified_main_profile_id="wrong_root_id", active_composer_actor_id="actor_page_1")
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value={"success": True, "stages": valid_stages, "evidence": ev_wrong_root}):
        r = client.post(f"/social-media/devices/{dev.id}/dry-run", json={"destination_id": page_dest.id})
        assert r.status_code == 400
        assert "Main profile mismatch" in r.json()["detail"]

    # 2. Wrong Page returned for Page destination
    ev_wrong_page = dict(valid_ev_base, verified_main_profile_id="actor_root_1", active_composer_actor_id="wrong_page_id")
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value={"success": True, "stages": valid_stages, "evidence": ev_wrong_page}):
        r = client.post(f"/social-media/devices/{dev.id}/dry-run", json={"destination_id": page_dest.id})
        assert r.status_code == 400
        assert "Target actor mismatch" in r.json()["detail"]

    # 3. Main-profile context with Page composer on Profile destination
    asgn_prof = DestinationDeviceAssignment(
        destination_id=root_prof.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
    )
    db_session.add(asgn_prof)
    db_session.commit()

    ev_page_composer_on_profile = dict(valid_ev_base, verified_main_profile_id="actor_root_1", active_composer_actor_id="actor_page_1")
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value={"success": True, "stages": valid_stages, "evidence": ev_page_composer_on_profile}):
        r = client.post(f"/social-media/devices/{dev.id}/dry-run", json={"destination_id": root_prof.id})
        assert r.status_code == 400
        assert "Target actor mismatch" in r.json()["detail"]

    # 4. Missing composer ID
    ev_missing_composer = dict(valid_ev_base, verified_main_profile_id="actor_root_1", active_composer_actor_id="")
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value={"success": True, "stages": valid_stages, "evidence": ev_missing_composer}):
        r = client.post(f"/social-media/devices/{dev.id}/dry-run", json={"destination_id": root_prof.id})
        assert r.status_code == 400
        assert "missing active_composer_actor_id" in r.json()["detail"]

    # 5. Missing verified_main_profile_id
    ev_missing_root = dict(valid_ev_base, verified_main_profile_id="", active_composer_actor_id="actor_root_1")
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value={"success": True, "stages": valid_stages, "evidence": ev_missing_root}):
        r = client.post(f"/social-media/devices/{dev.id}/dry-run", json={"destination_id": root_prof.id})
        assert r.status_code == 400
        assert "missing verified_main_profile_id" in r.json()["detail"]

    # 6. Absent assignment for destination
    other_dest = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_profile",
        name="Unassigned Profile",
        mode="phone",
        facebook_actor_id="actor_unassigned",
        is_active=False,
    )
    db_session.add(other_dest)
    db_session.commit()
    r = client.post(f"/social-media/devices/{dev.id}/dry-run", json={"destination_id": other_dest.id})
    assert r.status_code == 400
    assert "Device is not assigned to this destination" in r.json()["detail"]

    # 7. Wrong parent kind (e.g. parent is facebook_page instead of facebook_profile)
    page_parent = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_page",
        name="Invalid Parent Kind",
        mode="phone",
        facebook_actor_id="actor_fake_page_parent",
        is_active=False,
    )
    db_session.add(page_parent)
    db_session.commit()

    bad_page = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_page",
        name="Child of Bad Parent",
        mode="phone",
        facebook_actor_id="actor_child_1",
        parent_profile_id=page_parent.id,
        is_active=False,
    )
    db_session.add(bad_page)
    db_session.commit()

    asgn_bad_parent = DestinationDeviceAssignment(
        destination_id=bad_page.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
    )
    db_session.add(asgn_bad_parent)
    db_session.commit()

    r = client.post(f"/social-media/devices/{dev.id}/dry-run", json={"destination_id": bad_page.id})
    assert r.status_code == 400
    assert "Parent destination kind must be facebook_profile" in r.json()["detail"]

    # 8. Missing parent profile ID on Page destination
    orphan_page = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_page",
        name="Orphan Page",
        mode="phone",
        facebook_actor_id="actor_orphan_1",
        parent_profile_id=None,
        is_active=False,
    )
    db_session.add(orphan_page)
    db_session.commit()
    asgn_orphan = DestinationDeviceAssignment(
        destination_id=orphan_page.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
    )
    db_session.add(asgn_orphan)
    db_session.commit()
    r = client.post(f"/social-media/devices/{dev.id}/dry-run", json={"destination_id": orphan_page.id})
    assert r.status_code == 400
    assert "missing parent_profile_id" in r.json()["detail"]

    # Verify device and assignments remain uncalibrated and unverified
    db_session.refresh(dev)
    db_session.refresh(asgn)
    db_session.refresh(asgn_prof)
    assert dev.calibration_verified_at is None
    assert asgn.verified_at is None
    assert asgn_prof.verified_at is None
    # No dangling lease
    assert db_session.query(DeviceLease).filter_by(device_id=dev.id).count() == 0


def test_devices_discovery_api_success(client, db_session):
    mock_payload = {
        "devices": [
            {
                "name": "realme 9i 5G",
                "tailscale_ip": "100.64.96.50",
                "tailscale_node_id": "nTestPhone1",
                "online": True,
                "adb_serial": "100.64.96.50:5555",
                "adb_state": "device",
                "runner_allowed": True,
            }
        ],
        "warnings": ["adb_unavailable"],
        "connection": {
            "backend_state": "Running",
            "tailnet_name": "example.test",
            "account_name": "account@example.test",
        },
    }
    with mock.patch("app.api.social_media.PhoneRunnerClient.discover_devices", return_value=mock_payload):
        resp = client.get("/social-media/devices/discovery")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["devices"]) == 1
    assert data["devices"][0]["name"] == "realme 9i 5G"
    assert data["devices"][0]["tailscale_node_id"] == "nTestPhone1"
    assert data["warnings"] == ["adb_unavailable"]
    assert data["connection"]["tailnet_name"] == "example.test"
    assert data["connection"]["account_name"] == "account@example.test"


def test_devices_discovery_path_ordering_and_no_db_mutation(client, db_session):
    before_count = db_session.query(PhoneDevice).count()

    mock_payload = {"devices": [], "warnings": [], "connection": None}
    with mock.patch("app.api.social_media.PhoneRunnerClient.discover_devices", return_value=mock_payload):
        resp = client.get("/social-media/devices/discovery")
    assert resp.status_code == 200
    assert resp.json() == {"devices": [], "warnings": [], "connection": None}

    after_count = db_session.query(PhoneDevice).count()
    assert before_count == after_count


def test_devices_discovery_api_failure_503(client, db_session):
    with mock.patch("app.api.social_media.PhoneRunnerClient.discover_devices", side_effect=Exception("network down")):
        resp = client.get("/social-media/devices/discovery")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Unable to load phones from the server. You can enter connection details manually."


def test_devices_discovery_unauth_rejects_before_client_call(client, db_session):
    """Discovery route requires valid CurrentUser; rejects unauthenticated with 401/403 before calling runner."""
    orig_overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides.pop(get_current_user, None)
        before_count = db_session.query(PhoneDevice).count()
        with mock.patch("app.api.social_media.PhoneRunnerClient.discover_devices") as mock_discover:
            resp = client.get("/social-media/devices/discovery")
            assert resp.status_code in (401, 403)
            mock_discover.assert_not_called()
        assert db_session.query(PhoneDevice).count() == before_count
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(orig_overrides)


def test_phone_device_neutral_registration_and_serial_requirement(client, db_session):
    """Phone device is platform-neutral; status available requires valid adb_serial, not Facebook actor."""
    # 1. Maintenance device registers without actor or serial
    resp_maint = client.post("/social-media/devices", json={"name": "Blank Maintenance Phone", "status": "maintenance"})
    assert resp_maint.status_code == 201
    dev_m = resp_maint.json()
    assert dev_m["status"] == "maintenance"
    assert dev_m["adb_serial"] is None
    assert dev_m["expected_profile_actor_id"] is None

    # 2. Transitioning to available without adb_serial is rejected
    resp_bad_patch = client.patch(f"/social-media/devices/{dev_m['id']}", json={"status": "available"})
    assert resp_bad_patch.status_code == 400
    assert "adb_serial" in resp_bad_patch.json()["detail"]

    # 3. Direct creation as available without adb_serial is rejected
    resp_bad_create = client.post("/social-media/devices", json={"name": "No Serial Available", "status": "available"})
    assert resp_bad_create.status_code in (400, 422)
    assert "adb_serial" in resp_bad_create.text

    # 4. Direct creation as available with adb_serial and NO Facebook actor succeeds
    resp_avail = client.post(
        "/social-media/devices",
        json={
            "name": "Neutral Available Phone",
            "adb_serial": "100.64.99.1:5555",
            "tailscale_ip": "100.64.99.1",
            "status": "available",
        },
    )
    assert resp_avail.status_code == 201
    avail_dev = resp_avail.json()
    assert avail_dev["status"] == "available"
    assert avail_dev["adb_serial"] == "100.64.99.1:5555"
    assert avail_dev["expected_profile_actor_id"] is None
    assert avail_dev["expected_profile_name"] is None

    # 5. Patching explicit null adb_serial while status is available rejects
    resp_null_serial = client.patch(f"/social-media/devices/{avail_dev['id']}", json={"adb_serial": None})
    assert resp_null_serial.status_code == 400
    assert "adb_serial" in resp_null_serial.json()["detail"]

    # 6. Patching explicit null adb_serial with status=maintenance succeeds
    resp_maint_null = client.patch(
        f"/social-media/devices/{avail_dev['id']}",
        json={"status": "maintenance", "adb_serial": None},
    )
    assert resp_maint_null.status_code == 200
    assert resp_maint_null.json()["status"] == "maintenance"
    assert resp_maint_null.json()["adb_serial"] is None


def test_per_assignment_root_verification_neutral_phone_multi_account(client, db_session):
    """Platform-neutral phone supports distinct root profiles across assignments without setting global device actor."""
    proj = TargetFanpage(name="Multi Profile Neutral Project", is_connected=True)
    db_session.add(proj)
    db_session.commit()

    # Available phone device with NO Facebook default actor configured
    dev = PhoneDevice(
        name="Platform Neutral Phone",
        adb_serial="serial_neutral_multi_99",
        expected_profile_actor_id=None,
        expected_profile_name=None,
        status="available",
        calibration_verified_at=None,
    )
    db_session.add(dev)

    # Root Profile A + Page A
    root_a = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_profile",
        name="Root Profile A",
        mode="phone",
        facebook_actor_id="actor_root_a",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=False,
    )
    db_session.add(root_a)
    db_session.commit()

    page_a = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_page",
        name="Page Dest A",
        mode="phone",
        facebook_actor_id="actor_page_a",
        parent_profile_id=root_a.id,
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=False,
    )
    db_session.add(page_a)

    # Root Profile B + Page B
    root_b = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_profile",
        name="Root Profile B",
        mode="phone",
        facebook_actor_id="actor_root_b",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=False,
    )
    db_session.add(root_b)
    db_session.commit()

    page_b = SocialDestination(
        content_project_id=proj.id,
        kind="facebook_page",
        name="Page Dest B",
        mode="phone",
        facebook_actor_id="actor_page_b",
        parent_profile_id=root_b.id,
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=False,
    )
    db_session.add(page_b)

    asgn_a = DestinationDeviceAssignment(
        destination_id=page_a.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
    )
    asgn_b = DestinationDeviceAssignment(
        destination_id=page_b.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
    )
    db_session.add_all([asgn_a, asgn_b])
    db_session.commit()

    # 1. Uncalibrated / unverified phone NOT eligible for Facebook publishing or destination activation
    from app.api.social_media import _is_active_verified_assignment
    from app.services.social_delivery_dispatch import select_and_lease_phone_device

    assert not _is_active_verified_assignment(asgn_a, page_a, db_session)
    assert not _is_active_verified_assignment(asgn_b, page_b, db_session)

    act_resp = client.patch(f"/social-media/destinations/{page_a.id}", json={"is_active": True})
    assert act_resp.status_code == 400
    assert "Cannot activate phone route without verified device assignment and calibration" in act_resp.json()["detail"]

    job = PublishJob(
        fanpage_id=proj.id,
        content_type="news_content",
        status="pending_publish",
    )
    db_session.add(job)
    db_session.flush()

    delivery_unverified = PublishDelivery(
        publish_job_id=job.id,
        destination_id=page_a.id,
        state="scheduled",
        intended_method="phone",
        scheduled_for=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(delivery_unverified)
    db_session.commit()

    leased_dev, lease_err = select_and_lease_phone_device(db_session, delivery_unverified)
    assert leased_dev is None
    assert "no eligible and available" in (lease_err or "").lower() or "no active" in (lease_err or "").lower()

    # 2. Legacy device-only dry-run rejects without configured legacy actor
    resp_legacy_fail = client.post(f"/social-media/devices/{dev.id}/dry-run", json={})
    assert resp_legacy_fail.status_code == 400
    assert "Device has no expected_profile_actor_id configured" in resp_legacy_fail.json()["detail"]

    # 3. Dry-run with wrong root actor rejects
    mock_runner_wrong_root = {
        "success": True,
        "status": "verified",
        "stages": [{"stage": "identity_check", "passed": True}],
        "evidence": {
            "resolution": "1080x2400",
            "app_card_version": "1.2.0",
            "facebook_version": "452.0.0",
            "portal_version": "0.7.30",
            "stopped_before_publish": True,
            "verified_main_profile_id": "wrong_root_actor",
            "active_composer_actor_id": "actor_page_a",
        },
    }
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value=mock_runner_wrong_root):
        resp_wrong_root = client.post(f"/social-media/devices/{dev.id}/dry-run", json={"destination_id": page_a.id})
        assert resp_wrong_root.status_code == 400
        assert "Main profile mismatch" in resp_wrong_root.json()["detail"]

    # 4. Dry-run with wrong target actor rejects
    mock_runner_wrong_target = {
        "success": True,
        "status": "verified",
        "stages": [{"stage": "identity_check", "passed": True}],
        "evidence": {
            "resolution": "1080x2400",
            "app_card_version": "1.2.0",
            "facebook_version": "452.0.0",
            "portal_version": "0.7.30",
            "stopped_before_publish": True,
            "verified_main_profile_id": "actor_root_a",
            "active_composer_actor_id": "wrong_target_actor",
        },
    }
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value=mock_runner_wrong_target):
        resp_wrong_target = client.post(f"/social-media/devices/{dev.id}/dry-run", json={"destination_id": page_a.id})
        assert resp_wrong_target.status_code == 400
        assert "Target actor mismatch" in resp_wrong_target.json()["detail"]

    # 5. Successful dry-run for Page A under Root A
    mock_runner_page_a = {
        "success": True,
        "status": "verified",
        "stages": [{"stage": "identity_check", "passed": True}],
        "evidence": {
            "resolution": "1080x2400",
            "app_card_version": "1.2.0",
            "facebook_version": "452.0.0",
            "portal_version": "0.7.30",
            "stopped_before_publish": True,
            "verified_main_profile_id": "actor_root_a",
            "active_composer_actor_id": "actor_page_a",
        },
    }
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value=mock_runner_page_a):
        resp_dry_a = client.post(f"/social-media/devices/{dev.id}/dry-run", json={"destination_id": page_a.id})
        assert resp_dry_a.status_code == 200
        assert resp_dry_a.json()["status"] == "success"
        assert resp_dry_a.json()["assignment_verified"] is True

    db_session.refresh(asgn_a)
    db_session.refresh(asgn_b)
    db_session.refresh(dev)

    assert dev.expected_profile_actor_id is None  # Remains unconfigured default
    assert dev.calibration_verified_at is not None
    assert asgn_a.verified_at is not None
    assert asgn_a.verified_profile_actor_id == "actor_root_a"
    assert asgn_a.verified_target_actor_id == "actor_page_a"
    assert asgn_b.verified_at is None  # Page B not yet verified

    assert _is_active_verified_assignment(asgn_a, page_a, db_session)
    assert not _is_active_verified_assignment(asgn_b, page_b, db_session)

    # 6. Successful dry-run for Page B under Root B on the same phone
    mock_runner_page_b = {
        "success": True,
        "status": "verified",
        "stages": [{"stage": "identity_check", "passed": True}],
        "evidence": {
            "resolution": "1080x2400",
            "app_card_version": "1.2.0",
            "facebook_version": "452.0.0",
            "portal_version": "0.7.30",
            "stopped_before_publish": True,
            "verified_main_profile_id": "actor_root_b",
            "active_composer_actor_id": "actor_page_b",
        },
    }
    with mock.patch("app.api.social_media.PhoneRunnerClient.dry_run", return_value=mock_runner_page_b):
        resp_dry_b = client.post(f"/social-media/devices/{dev.id}/dry-run", json={"destination_id": page_b.id})
        assert resp_dry_b.status_code == 200
        assert resp_dry_b.json()["status"] == "success"
        assert resp_dry_b.json()["assignment_verified"] is True

    db_session.refresh(asgn_b)
    db_session.refresh(dev)

    assert dev.expected_profile_actor_id is None  # Remains unconfigured default
    assert asgn_b.verified_at is not None
    assert asgn_b.verified_profile_actor_id == "actor_root_b"
    assert asgn_b.verified_target_actor_id == "actor_page_b"

    assert _is_active_verified_assignment(asgn_a, page_a, db_session)
    assert _is_active_verified_assignment(asgn_b, page_b, db_session)

    # 7. Destination activation succeeds now that assignment and device calibration are verified
    act_resp_ok = client.patch(f"/social-media/destinations/{page_a.id}", json={"is_active": True})
    assert act_resp_ok.status_code == 200
    assert act_resp_ok.json()["is_active"] is True

    # 8. Delivery leasing selects and leases verified phone device
    leased_dev, lease_err = select_and_lease_phone_device(db_session, delivery_unverified)
    assert leased_dev is not None
    assert leased_dev.id == dev.id
    assert lease_err is None
