"""Tests for social project content generation eligibility and Repliz sync compatibility."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session, sessionmaker

# Allow SQLite to compile PostgreSQL ARRAY and JSONB types in memory
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"

from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import (
    DestinationDeviceAssignment,
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.services.social_project_eligibility import project_can_generate
from app.tasks.fanpage_sync import sync_fanpages_from_repliz
from app.tasks.publisher import publish_job


@pytest.fixture
def db_session():
    """Create clean in-memory SQLite schema and session."""
    sd_col = SocialDestination.__table__.columns["supported_media"]
    orig_sd = sd_col.server_default
    sd_col.server_default = None

    engine = create_engine("sqlite:///:memory:")
    TargetFanpage.__table__.create(engine)
    SocialDestination.__table__.create(engine)
    PhoneDevice.__table__.create(engine)
    DestinationDeviceAssignment.__table__.create(engine)
    PublishJob.__table__.create(engine)
    PublishDelivery.__table__.create(engine)

    sd_col.server_default = orig_sd

    session = Session(engine)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_profile_only_project_eligibility(db_session: Session):
    """Profile-only projects remain eligible even if disconnected from Repliz or phone offline."""
    project = TargetFanpage(
        name="Local Profile Project",
        repliz_account_id=None,
        is_connected=False,
        is_active=True,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_profile",
        name="Profile Dest",
        mode="phone",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=True,
    )
    # Phone device in maintenance/offline state
    device = PhoneDevice(
        name="Pixel 7",
        status="maintenance",
    )
    db_session.add_all([dest, device])
    db_session.flush()

    assignment = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=device.id,
        priority=0,
        is_active=True,
    )
    db_session.add(assignment)
    db_session.commit()

    eligible = (
        db_session.query(TargetFanpage)
        .filter(TargetFanpage.is_active == True, project_can_generate())
        .all()
    )
    assert len(eligible) == 1
    assert eligible[0].id == project.id


def test_page_phone_and_mixed_eligibility_after_repliz_disconnect(db_session: Session):
    """Page projects with phone or mixed routes stay eligible when Repliz disconnects."""
    phone_page = TargetFanpage(
        name="Page Phone Route",
        repliz_account_id="rep_page_phone",
        is_connected=False,
        is_active=True,
    )
    mixed_page = TargetFanpage(
        name="Page Mixed Route",
        repliz_account_id="rep_page_mixed",
        is_connected=False,
        is_active=True,
    )
    db_session.add_all([phone_page, mixed_page])
    db_session.flush()

    dest_phone = SocialDestination(
        content_project_id=phone_page.id,
        kind="facebook_page",
        name="Dest Phone",
        mode="phone",
        daily_target=2,
        phone_daily_target=2,
        repliz_daily_target=0,
        is_active=True,
    )
    dest_mixed = SocialDestination(
        content_project_id=mixed_page.id,
        kind="facebook_page",
        name="Dest Mixed",
        mode="mixed",
        daily_target=5,
        phone_daily_target=2,
        repliz_daily_target=3,
        is_active=True,
    )
    db_session.add_all([dest_phone, dest_mixed])
    db_session.commit()

    eligible = (
        db_session.query(TargetFanpage)
        .filter(TargetFanpage.is_active == True, project_can_generate())
        .all()
    )
    assert {fp.name for fp in eligible} == {"Page Phone Route", "Page Mixed Route"}


def test_disconnected_repliz_only_ineligible(db_session: Session):
    """Repliz-only Page without Phone route is ineligible when Repliz disconnects."""
    page = TargetFanpage(
        name="Repliz Only Disconnected",
        repliz_account_id="rep_repliz_only",
        is_connected=False,
        is_active=True,
    )
    db_session.add(page)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=page.id,
        kind="facebook_page",
        name="Dest Repliz",
        repliz_account_id="rep_repliz_only",
        mode="repliz",
        daily_target=3,
        phone_daily_target=0,
        repliz_daily_target=3,
        is_active=True,
    )
    db_session.add(dest)
    db_session.commit()

    eligible = (
        db_session.query(TargetFanpage)
        .filter(TargetFanpage.is_active == True, project_can_generate())
        .all()
    )
    assert len(eligible) == 0


def test_connected_repliz_eligible(db_session: Session):
    """Repliz-connected fanpage is eligible through legacy connection."""
    page = TargetFanpage(
        name="Repliz Connected Page",
        repliz_account_id="rep_connected",
        is_connected=True,
        is_active=True,
    )
    db_session.add(page)
    db_session.commit()

    eligible = (
        db_session.query(TargetFanpage)
        .filter(TargetFanpage.is_active == True, project_can_generate())
        .all()
    )
    assert len(eligible) == 1
    assert eligible[0].id == page.id


def test_inactive_fanpage_excluded_by_caller(db_session: Session):
    """Caller TargetFanpage.is_active filter excludes inactive projects even if connected."""
    page = TargetFanpage(
        name="Inactive Connected Page",
        repliz_account_id="rep_inactive",
        is_connected=True,
        is_active=False,
    )
    db_session.add(page)
    db_session.commit()

    eligible = (
        db_session.query(TargetFanpage)
        .filter(TargetFanpage.is_active == True, project_can_generate())
        .all()
    )
    assert len(eligible) == 0


def test_fanpage_sync_creates_target_fanpage_and_destination(db_session: Session, monkeypatch):
    """Sync creates TargetFanpage and corresponding SocialDestination with default Repliz mode."""
    mock_client = MagicMock()
    mock_client.list_facebook_accounts.return_value = [
        {
            "id": "repliz_101",
            "name": "Acme Motorsport",
            "username": "acmemotorsport",
            "picture": "https://example.com/pic.jpg",
            "isConnected": True,
        }
    ]
    monkeypatch.setattr(
        "app.services.repliz_client.get_repliz_client_from_db",
        lambda db: mock_client,
    )

    result = sync_fanpages_from_repliz(db=db_session)
    assert result == {"created": 1, "updated": 0, "missing": 0}

    fp = db_session.query(TargetFanpage).filter_by(repliz_account_id="repliz_101").first()
    assert fp is not None
    assert fp.name == "Acme Motorsport"
    assert fp.is_connected is True
    assert fp.is_active is False

    dest = (
        db_session.query(SocialDestination)
        .filter_by(repliz_account_id="repliz_101")
        .first()
    )
    assert dest is not None
    assert dest.content_project_id == fp.id
    assert dest.kind == "facebook_page"
    assert dest.name == "Acme Motorsport"
    assert dest.mode == "repliz"
    assert dest.phone_daily_target == 0
    assert dest.repliz_daily_target == dest.daily_target
    assert dest.is_active is False


def test_fanpage_sync_idempotency(db_session: Session, monkeypatch):
    """Running sync repeatedly does not duplicate rows."""
    mock_client = MagicMock()
    mock_client.list_facebook_accounts.return_value = [
        {
            "id": "repliz_102",
            "name": "Repeat Page",
            "username": "repeatpage",
            "isConnected": True,
        }
    ]
    monkeypatch.setattr(
        "app.services.repliz_client.get_repliz_client_from_db",
        lambda db: mock_client,
    )

    sync_fanpages_from_repliz(db=db_session)
    sync_fanpages_from_repliz(db=db_session)

    fps = db_session.query(TargetFanpage).filter_by(repliz_account_id="repliz_102").all()
    assert len(fps) == 1

    dests = db_session.query(SocialDestination).filter_by(repliz_account_id="repliz_102").all()
    assert len(dests) == 1


def test_fanpage_sync_no_assignment_or_mode_overwrite(db_session: Session, monkeypatch):
    """Sync preserves mode, quotas, parent Profile, actor ID, assignments, and active setting."""
    fp = TargetFanpage(
        name="Custom Configured Page",
        repliz_account_id="rep_custom",
        is_connected=True,
        is_active=True,
    )
    db_session.add(fp)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=fp.id,
        kind="facebook_page",
        name="Custom Configured Page",
        repliz_account_id="rep_custom",
        mode="mixed",
        daily_target=5,
        phone_daily_target=2,
        repliz_daily_target=3,
        parent_profile_id=123,
        facebook_actor_id="act_555",
        is_active=True,
    )
    device = PhoneDevice(name="Device 1", status="available")
    db_session.add_all([dest, device])
    db_session.flush()

    assignment = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=device.id,
        priority=0,
        is_active=True,
    )
    db_session.add(assignment)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.list_facebook_accounts.return_value = [
        {
            "id": "rep_custom",
            "name": "New Display Name From Repliz",
            "username": "newusername",
            "isConnected": False,
        }
    ]
    monkeypatch.setattr(
        "app.services.repliz_client.get_repliz_client_from_db",
        lambda db: mock_client,
    )

    sync_fanpages_from_repliz(db=db_session)

    # Reload destination
    db_session.expire_all()
    reloaded_dest = (
        db_session.query(SocialDestination)
        .filter_by(repliz_account_id="rep_custom")
        .first()
    )
    assert reloaded_dest.name == "New Display Name From Repliz"
    assert reloaded_dest.mode == "mixed"
    assert reloaded_dest.daily_target == 5
    assert reloaded_dest.phone_daily_target == 2
    assert reloaded_dest.repliz_daily_target == 3
    assert reloaded_dest.parent_profile_id == 123
    assert reloaded_dest.facebook_actor_id == "act_555"
    assert reloaded_dest.is_active is True

    # Assignments intact
    assignments = (
        db_session.query(DestinationDeviceAssignment)
        .filter_by(destination_id=reloaded_dest.id)
        .all()
    )
    assert len(assignments) == 1
    assert assignments[0].device_id == device.id


def test_fanpage_sync_no_marking_local_projects_disconnected(db_session: Session, monkeypatch):
    """Local projects with null Repliz IDs must never be marked disconnected."""
    local_proj = TargetFanpage(
        name="Local Phone Profile",
        repliz_account_id=None,
        is_connected=True,
        is_active=True,
    )
    db_session.add(local_proj)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=local_proj.id,
        kind="facebook_profile",
        name="Local Profile Dest",
        mode="phone",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=True,
    )
    db_session.add(dest)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.list_facebook_accounts.return_value = [
        {
            "id": "other_account_id",
            "name": "Other Repliz Page",
            "isConnected": True,
        }
    ]
    monkeypatch.setattr(
        "app.services.repliz_client.get_repliz_client_from_db",
        lambda db: mock_client,
    )

    sync_fanpages_from_repliz(db=db_session)

    db_session.expire_all()
    reloaded = db_session.query(TargetFanpage).filter_by(id=local_proj.id).first()
    assert reloaded.is_connected is True

    reloaded_dest = db_session.query(SocialDestination).filter_by(id=dest.id).first()
    assert reloaded_dest.is_active is True


def test_fanpage_sync_missing_accounts_marks_legacy_connection_only(db_session: Session, monkeypatch):
    """Accounts absent from Repliz have legacy connection set false without touching destination routes."""
    page = TargetFanpage(
        name="Missing Page",
        repliz_account_id="rep_missing",
        is_connected=True,
        is_active=True,
    )
    db_session.add(page)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=page.id,
        kind="facebook_page",
        name="Missing Page Phone Route",
        repliz_account_id="rep_missing",
        mode="phone",
        daily_target=2,
        phone_daily_target=2,
        repliz_daily_target=0,
        is_active=True,
    )
    db_session.add(dest)
    db_session.commit()

    mock_client = MagicMock()
    # Repliz returns other account; rep_missing is missing
    mock_client.list_facebook_accounts.return_value = [
        {
            "id": "rep_present",
            "name": "Present Page",
            "isConnected": True,
        }
    ]
    monkeypatch.setattr(
        "app.services.repliz_client.get_repliz_client_from_db",
        lambda db: mock_client,
    )

    sync_fanpages_from_repliz(db=db_session)

    db_session.expire_all()
    reloaded_page = db_session.query(TargetFanpage).filter_by(id=page.id).first()
    assert reloaded_page.is_connected is False

    reloaded_dest = db_session.query(SocialDestination).filter_by(id=dest.id).first()
    assert reloaded_dest.is_active is True


def test_fanpage_sync_preserves_user_project_reassignment(db_session: Session, monkeypatch):
    """Sync must preserve destination.content_project_id when reassigned to another project."""
    shared_project = TargetFanpage(
        name="Shared Brand Project",
        repliz_account_id=None,
        is_connected=False,
        is_active=True,
    )
    repliz_fanpage = TargetFanpage(
        name="Synced Repliz Page",
        repliz_account_id="rep_sync_preserve",
        is_connected=True,
        is_active=True,
    )
    db_session.add_all([shared_project, repliz_fanpage])
    db_session.flush()

    parent_profile = SocialDestination(
        content_project_id=shared_project.id,
        kind="facebook_profile",
        name="Admin Profile",
        facebook_actor_id="actor_prof_shared",
        mode="phone",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=True,
    )
    db_session.add(parent_profile)
    db_session.flush()

    page_dest = SocialDestination(
        content_project_id=shared_project.id,
        kind="facebook_page",
        name="Synced Repliz Page",
        repliz_account_id="rep_sync_preserve",
        parent_profile_id=parent_profile.id,
        facebook_actor_id="actor_page_synced",
        mode="phone",
        daily_target=2,
        phone_daily_target=2,
        repliz_daily_target=0,
        is_active=True,
    )
    db_session.add(page_dest)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.list_facebook_accounts.return_value = [
        {
            "id": "rep_sync_preserve",
            "name": "Synced Repliz Page (Updated Name)",
            "isConnected": True,
        }
    ]
    monkeypatch.setattr(
        "app.services.repliz_client.get_repliz_client_from_db",
        lambda db: mock_client,
    )

    sync_fanpages_from_repliz(db=db_session)

    db_session.expire_all()
    reloaded_dest = db_session.query(SocialDestination).filter_by(repliz_account_id="rep_sync_preserve").first()
    assert reloaded_dest is not None
    assert reloaded_dest.content_project_id == shared_project.id
    assert reloaded_dest.parent_profile_id == parent_profile.id
    assert reloaded_dest.name == "Synced Repliz Page (Updated Name)"


def test_ai_video_plan_respects_project_eligibility(db_session: Session):
    """AI video plan filter respects project_can_generate: excludes disconnected Repliz projects without phone routes."""
    disconnected_proj = TargetFanpage(
        name="Disconnected No Phone",
        repliz_account_id="rep_disc_1",
        is_connected=False,
        is_active=True,
        ai_video_enabled=True,
    )
    phone_route_proj = TargetFanpage(
        name="Disconnected With Phone",
        repliz_account_id="rep_disc_2",
        is_connected=False,
        is_active=True,
        ai_video_enabled=True,
    )
    connected_proj = TargetFanpage(
        name="Connected Repliz",
        repliz_account_id="rep_conn_3",
        is_connected=True,
        is_active=True,
        ai_video_enabled=True,
    )
    db_session.add_all([disconnected_proj, phone_route_proj, connected_proj])
    db_session.flush()

    dest_phone = SocialDestination(
        content_project_id=phone_route_proj.id,
        kind="facebook_page",
        name="Phone Dest",
        mode="phone",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=True,
    )
    dest_repliz_only = SocialDestination(
        content_project_id=disconnected_proj.id,
        kind="facebook_page",
        name="Repliz Only Dest",
        mode="repliz",
        daily_target=1,
        phone_daily_target=0,
        repliz_daily_target=1,
        is_active=True,
    )
    db_session.add_all([dest_phone, dest_repliz_only])
    db_session.commit()

    eligible_pages = (
        db_session.query(TargetFanpage)
        .filter(
            TargetFanpage.is_active == True,
            TargetFanpage.ai_video_enabled == True,
            project_can_generate(),
        )
        .all()
    )
    eligible_ids = {fp.id for fp in eligible_pages}
    assert disconnected_proj.id not in eligible_ids
    assert phone_route_proj.id in eligible_ids
    assert connected_proj.id in eligible_ids


def test_connected_profile_only_without_destinations_is_ineligible(db_session: Session):
    """Connected Profile-only project with no destinations is ineligible; disconnected phone project is eligible."""
    # Connected Profile project without Repliz ID and no destinations -> must NOT be eligible
    profile_only = TargetFanpage(
        name="Connected Profile Only No Dests",
        repliz_account_id=None,
        is_connected=True,
        is_active=True,
    )
    # Empty string repliz_account_id without destinations -> must NOT be eligible
    empty_repliz = TargetFanpage(
        name="Empty Repliz ID No Dests",
        repliz_account_id="   ",
        is_connected=True,
        is_active=True,
    )
    # Disconnected project with active phone destination route -> MUST be eligible
    phone_route_proj = TargetFanpage(
        name="Disconnected Active Phone Project",
        repliz_account_id=None,
        is_connected=False,
        is_active=True,
    )
    db_session.add_all([profile_only, empty_repliz, phone_route_proj])
    db_session.flush()

    dest = SocialDestination(
        content_project_id=phone_route_proj.id,
        kind="facebook_profile",
        name="Profile Phone Dest",
        mode="phone",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=True,
    )
    db_session.add(dest)
    db_session.commit()

    eligible = (
        db_session.query(TargetFanpage)
        .filter(TargetFanpage.is_active == True, project_can_generate())
        .all()
    )
    eligible_ids = {fp.id for fp in eligible}

    assert profile_only.id not in eligible_ids
    assert empty_repliz.id not in eligible_ids
    assert phone_route_proj.id in eligible_ids


def test_publish_job_rejects_orphan_project_without_account_or_destinations(db_session: Session, monkeypatch):
    """publish_job fails fast on an orphan project without destinations and without a Repliz account."""
    orphan_proj = TargetFanpage(
        name="Orphan Profile Project",
        repliz_account_id=None,
        is_connected=True,
        is_active=True,
    )
    db_session.add(orphan_proj)
    db_session.commit()

    job = PublishJob(
        fanpage_id=orphan_proj.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/orphan.png",
        ai_generated_caption="Orphan caption",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job)
    db_session.commit()

    mock_repliz = MagicMock()
    monkeypatch.setattr("app.services.repliz_client.get_repliz_client_from_db", lambda db: mock_repliz)

    SessionLocal = sessionmaker(bind=db_session.bind)
    monkeypatch.setattr("app.tasks.publisher.SessionLocal", SessionLocal)
    monkeypatch.setattr("app.database.SessionLocal", SessionLocal)

    publish_job(job.id)

    # Prove no Repliz client/write occurred
    mock_repliz.create_image_schedule.assert_not_called()

    db_session.expire_all()
    reloaded_job = db_session.query(PublishJob).filter_by(id=job.id).first()
    assert reloaded_job.status == PublishJobStatus.failed
    assert "no Repliz account and no active social destinations" in reloaded_job.last_error


def test_publish_job_preserves_legacy_account_path(db_session: Session, monkeypatch):
    """publish_job proceeds to legacy Repliz schedule when fanpage has repliz_account_id and no destinations."""
    legacy_page = TargetFanpage(
        name="Legacy Fanpage",
        repliz_account_id="rep_legacy_valid",
        is_connected=True,
        is_active=True,
    )
    db_session.add(legacy_page)
    db_session.commit()

    job = PublishJob(
        fanpage_id=legacy_page.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/news.png",
        ai_generated_caption="Legacy news caption",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job)
    db_session.commit()

    mock_repliz = MagicMock()
    mock_repliz.create_image_schedule.return_value = {"id": "sched_legacy_101"}
    monkeypatch.setattr("app.services.repliz_client.get_repliz_client_from_db", lambda db: mock_repliz)
    monkeypatch.setattr("app.tasks.publisher._next_schedule_at", lambda db, fid, **kw: datetime(2026, 10, 15, 12, 0, 0))

    SessionLocal = sessionmaker(bind=db_session.bind)
    monkeypatch.setattr("app.tasks.publisher.SessionLocal", SessionLocal)
    monkeypatch.setattr("app.database.SessionLocal", SessionLocal)

    publish_job(job.id)

    mock_repliz.create_image_schedule.assert_called_once()
    assert mock_repliz.create_image_schedule.call_args[1]["account_id"] == "rep_legacy_valid"

    db_session.expire_all()
    reloaded_job = db_session.query(PublishJob).filter_by(id=job.id).first()
    assert reloaded_job.status == PublishJobStatus.published
    assert reloaded_job.repliz_schedule_id == "sched_legacy_101"
