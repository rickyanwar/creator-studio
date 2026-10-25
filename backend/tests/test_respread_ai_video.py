from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"

from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.scripts.respread_ai_video_deliveries import parse_args, respread


@pytest.fixture
def db_session():
    """Create in-memory SQLite database for testing."""
    sd_col = SocialDestination.__table__.columns["supported_media"]
    orig_sd = sd_col.server_default
    sd_col.server_default = None

    de_col = DeliveryEvent.__table__.columns["details"]
    orig_de = de_col.server_default
    de_col.server_default = None

    engine = create_engine("sqlite:///:memory:")
    TargetFanpage.__table__.create(engine)
    SocialDestination.__table__.create(engine)
    PhoneDevice.__table__.create(engine)
    DestinationDeviceAssignment.__table__.create(engine)
    Post.__table__.create(engine)
    PublishJob.__table__.create(engine)
    PublishDelivery.__table__.create(engine)
    DeliveryEvent.__table__.create(engine)

    sd_col.server_default = orig_sd
    de_col.server_default = orig_de

    session = Session(engine)
    try:
        yield session
    finally:
        session.close()


class FakeReplizClient:
    def __init__(
        self,
        *,
        delete_error: Exception | None = None,
        get_schedule_result: dict | None = None,
        get_schedule_error: Exception | None = None,
        deleted_status_404: bool = True,
    ):
        self.delete_error = delete_error
        self.get_schedule_result = get_schedule_result
        self.get_schedule_error = get_schedule_error
        self.deleted_status_404 = deleted_status_404
        self.deleted_schedule_ids: list[str] = []

    def delete_schedule(self, schedule_id: str) -> dict:
        if self.delete_error:
            raise self.delete_error
        self.deleted_schedule_ids.append(schedule_id)
        return {"status": "success"}

    def get_schedule(self, schedule_id: str) -> dict:
        if self.get_schedule_error:
            raise self.get_schedule_error
        if self.get_schedule_result is not None:
            return self.get_schedule_result
        if self.deleted_status_404:
            req = httpx.Request("GET", f"https://api.repliz.com/schedule/{schedule_id}")
            resp = httpx.Response(404, request=req)
            raise httpx.HTTPStatusError("Not Found", request=req, response=resp)
        return {"status": "active"}


def _setup_page_and_dest(db_session: Session, cap: int = 3, tz: str = "UTC") -> tuple[TargetFanpage, SocialDestination]:
    project = TargetFanpage(
        name="Motorsport News",
        timezone=tz,
        is_active=True,
        ai_video_enabled=True,
        ai_video_daily_count=cap,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Motorsport FB",
        mode="repliz",
        timezone=tz,
        daily_target=10,
        min_gap_minutes=5,
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()
    return project, dest


def test_dry_run_reports_would_move_and_changes_nothing(db_session: Session):
    """1. Cap 3, 6 repliz_scheduled AI-video deliveries today, all > 30 min ahead

    -> dry run reports 3 would_move and changes nothing.
    """
    project, dest = _setup_page_and_dest(db_session, cap=3)
    now_utc = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)

    slots = [
        datetime(2026, 10, 26, 10, 0),
        datetime(2026, 10, 26, 11, 0),
        datetime(2026, 10, 26, 12, 0),
        datetime(2026, 10, 26, 13, 0),
        datetime(2026, 10, 26, 14, 0),
        datetime(2026, 10, 26, 15, 0),
    ]
    deliveries = []
    for i, s in enumerate(slots):
        job = PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ai_video,
            status=PublishJobStatus.publishing,
            repliz_schedule_id=f"sched_{i}",
        )
        db_session.add(job)
        db_session.flush()
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=job.id,
            state="repliz_scheduled",
            repliz_schedule_id=f"sched_{i}",
            scheduled_for=s,
        )
        db_session.add(deliv)
        deliveries.append(deliv)
    db_session.commit()

    client = FakeReplizClient()
    results = respread(db_session, client, now_utc=now_utc, apply=False, limit=0)

    # Reports 3 would_move
    assert len(results) == 3
    for r in results:
        assert r["action"] == "would_move"
        assert r["new_slot"] is None
        assert r["destination_id"] == dest.id

    # The 3 latest slots are the candidates
    assert {r["old_slot"] for r in results} == {slots[3], slots[4], slots[5]}

    # DB is completely unchanged
    db_deliveries = db_session.query(PublishDelivery).order_by(PublishDelivery.scheduled_for.asc()).all()
    assert len(db_deliveries) == 6
    for i, d in enumerate(db_deliveries):
        assert d.state == "repliz_scheduled"
        assert d.repliz_schedule_id == f"sched_{i}"
        assert d.scheduled_for == slots[i]

    # No client calls made
    assert client.deleted_schedule_ids == []


def test_apply_moves_excess_deliveries_and_respects_cap(db_session: Session):
    """2. Apply, with delete OK and get_schedule raising 404

    -> the 3 latest are scheduled on a later local day, with no schedule id;
    the 3 earliest are untouched; at most 3 AI videos per local day after the run.
    """
    project, dest = _setup_page_and_dest(db_session, cap=3)
    now_utc = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)

    slots = [
        datetime(2026, 10, 26, 10, 0),
        datetime(2026, 10, 26, 11, 0),
        datetime(2026, 10, 26, 12, 0),
        datetime(2026, 10, 26, 13, 0),
        datetime(2026, 10, 26, 14, 0),
        datetime(2026, 10, 26, 15, 0),
    ]
    deliveries = []
    for i, s in enumerate(slots):
        job = PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ai_video,
            status=PublishJobStatus.publishing,
            repliz_schedule_id=f"sched_{i}",
        )
        db_session.add(job)
        db_session.flush()
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=job.id,
            state="repliz_scheduled",
            repliz_schedule_id=f"sched_{i}",
            scheduled_for=s,
        )
        db_session.add(deliv)
        deliveries.append(deliv)
    db_session.commit()

    client = FakeReplizClient(deleted_status_404=True)
    results = respread(db_session, client, now_utc=now_utc, apply=True, limit=0)

    assert len(results) == 3
    for r in results:
        assert r["action"] == "moved"
        assert r["new_slot"] is not None

    # Repliz delete was called for the 3 latest
    assert set(client.deleted_schedule_ids) == {"sched_3", "sched_4", "sched_5"}

    # The 3 earliest are untouched
    for i in range(3):
        db_session.refresh(deliveries[i])
        assert deliveries[i].state == "repliz_scheduled"
        assert deliveries[i].repliz_schedule_id == f"sched_{i}"
        assert deliveries[i].scheduled_for == slots[i]

    # The 3 latest are scheduled on a later local day, with no schedule id
    for i in range(3, 6):
        db_session.refresh(deliveries[i])
        assert deliveries[i].state == "scheduled"
        assert deliveries[i].repliz_schedule_id is None
        assert deliveries[i].scheduled_for.date() > datetime(2026, 10, 26).date()
        assert deliveries[i].last_error == "respread: moved to respect the daily AI-video cap"

        # Parent job status is pending_publish and repliz_schedule_id cleared
        parent_job = deliveries[i].publish_job
        assert parent_job.status == PublishJobStatus.pending_publish
        assert parent_job.repliz_schedule_id is None

    # DeliveryEvent was created for each moved delivery
    events = db_session.query(DeliveryEvent).filter_by(event_type="respread_moved").all()
    assert len(events) == 3
    assert {e.delivery_id for e in events} == {deliveries[3].id, deliveries[4].id, deliveries[5].id}

    # At most 3 AI videos per local day after the run
    all_deliveries = db_session.query(PublishDelivery).all()
    by_day: dict[datetime.date, int] = {}
    for d in all_deliveries:
        day = d.scheduled_for.date()
        by_day[day] = by_day.get(day, 0) + 1
    for day, count in by_day.items():
        assert count <= 3, f"Day {day} has {count} deliveries, expected <= 3"


def test_apply_with_dict_status_deleted_or_cancelled_confirmed(db_session: Session):
    """2b. Apply, with get_schedule returning dict with status='canceled' -> confirmed deleted and moved."""
    project, dest = _setup_page_and_dest(db_session, cap=1)
    now_utc = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)

    for i in range(2):
        job = PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ai_video,
            status=PublishJobStatus.publishing,
            repliz_schedule_id=f"sched_{i}",
        )
        db_session.add(job)
        db_session.flush()
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=job.id,
            state="repliz_scheduled",
            repliz_schedule_id=f"sched_{i}",
            scheduled_for=datetime(2026, 10, 26, 10 + i, 0),
        )
        db_session.add(deliv)
    db_session.commit()

    client = FakeReplizClient(get_schedule_result={"status": "Canceled"})
    results = respread(db_session, client, now_utc=now_utc, apply=True, limit=0)

    assert len(results) == 1
    assert results[0]["action"] == "moved"


def test_unverified_delete_leaves_schedule_as_is(db_session: Session):
    """3. get_schedule returns {"status": "scheduled"}

    -> unverified, state stays repliz_scheduled, schedule id kept.
    """
    project, dest = _setup_page_and_dest(db_session, cap=3)
    now_utc = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)

    slots = [
        datetime(2026, 10, 26, 10, 0),
        datetime(2026, 10, 26, 11, 0),
        datetime(2026, 10, 26, 12, 0),
        datetime(2026, 10, 26, 13, 0),
    ]
    deliveries = []
    for i, s in enumerate(slots):
        job = PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ai_video,
            status=PublishJobStatus.publishing,
            repliz_schedule_id=f"sched_{i}",
        )
        db_session.add(job)
        db_session.flush()
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=job.id,
            state="repliz_scheduled",
            repliz_schedule_id=f"sched_{i}",
            scheduled_for=s,
        )
        db_session.add(deliv)
        deliveries.append(deliv)
    db_session.commit()

    # get_schedule returns active/scheduled status
    client = FakeReplizClient(get_schedule_result={"status": "scheduled"})
    results = respread(db_session, client, now_utc=now_utc, apply=True, limit=0)

    assert len(results) == 1
    assert results[0]["action"] == "unverified"
    assert results[0]["delivery_id"] == deliveries[3].id

    # State stays repliz_scheduled, schedule id kept
    db_session.refresh(deliveries[3])
    assert deliveries[3].state == "repliz_scheduled"
    assert deliveries[3].repliz_schedule_id == "sched_3"
    assert deliveries[3].scheduled_for == slots[3]
    assert deliveries[3].last_error == "respread: Repliz delete unverified, left as is"


def test_delete_exception_leaves_delivery_kept(db_session: Session):
    """4. delete raises -> kept, nothing changed."""
    project, dest = _setup_page_and_dest(db_session, cap=3)
    now_utc = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)

    slots = [
        datetime(2026, 10, 26, 10, 0),
        datetime(2026, 10, 26, 11, 0),
        datetime(2026, 10, 26, 12, 0),
        datetime(2026, 10, 26, 13, 0),
    ]
    deliveries = []
    for i, s in enumerate(slots):
        job = PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ai_video,
            status=PublishJobStatus.publishing,
            repliz_schedule_id=f"sched_{i}",
        )
        db_session.add(job)
        db_session.flush()
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=job.id,
            state="repliz_scheduled",
            repliz_schedule_id=f"sched_{i}",
            scheduled_for=s,
        )
        db_session.add(deliv)
        deliveries.append(deliv)
    db_session.commit()

    client = FakeReplizClient(delete_error=RuntimeError("Connection reset by peer"))
    results = respread(db_session, client, now_utc=now_utc, apply=True, limit=0)

    assert len(results) == 1
    assert results[0]["action"] == "kept"
    assert "delete failed:" in results[0]["note"]

    # Nothing changed
    db_session.refresh(deliveries[3])
    assert deliveries[3].state == "repliz_scheduled"
    assert deliveries[3].repliz_schedule_id == "sched_3"
    assert deliveries[3].scheduled_for == slots[3]
    assert deliveries[3].last_error is None


def test_rows_within_30min_or_with_facebook_post_id_never_moved(db_session: Session):
    """5. A row within 30 min of its slot, or with facebook_post_id -> never moved."""
    project, dest = _setup_page_and_dest(db_session, cap=2)
    now_utc = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)

    # 4 deliveries today (cap is 2):
    # d1: slot at 10:15 (within 30m of 10:00)
    # d2: slot at 11:00, but has facebook_post_id
    # d3: slot at 12:00, movable
    # d4: slot at 13:00, movable
    j1 = PublishJob(fanpage_id=project.id, content_type=ContentType.ai_video)
    j2 = PublishJob(fanpage_id=project.id, content_type=ContentType.ai_video)
    j3 = PublishJob(fanpage_id=project.id, content_type=ContentType.ai_video)
    j4 = PublishJob(fanpage_id=project.id, content_type=ContentType.ai_video)
    db_session.add_all([j1, j2, j3, j4])
    db_session.flush()

    d1 = PublishDelivery(
        destination_id=dest.id,
        publish_job_id=j1.id,
        state="repliz_scheduled",
        repliz_schedule_id="sched_1",
        scheduled_for=datetime(2026, 10, 26, 10, 15),
    )
    d2 = PublishDelivery(
        destination_id=dest.id,
        publish_job_id=j2.id,
        state="repliz_scheduled",
        repliz_schedule_id="sched_2",
        facebook_post_id="fb_post_999",
        scheduled_for=datetime(2026, 10, 26, 11, 0),
    )
    d3 = PublishDelivery(
        destination_id=dest.id,
        publish_job_id=j3.id,
        state="repliz_scheduled",
        repliz_schedule_id="sched_3",
        scheduled_for=datetime(2026, 10, 26, 12, 0),
    )
    d4 = PublishDelivery(
        destination_id=dest.id,
        publish_job_id=j4.id,
        state="repliz_scheduled",
        repliz_schedule_id="sched_4",
        scheduled_for=datetime(2026, 10, 26, 13, 0),
    )
    db_session.add_all([d1, d2, d3, d4])
    db_session.commit()

    client = FakeReplizClient()
    results = respread(db_session, client, now_utc=now_utc, apply=True, limit=0)

    # Count is 4, cap is 2 -> needed is 2.
    # Movable rows are only d4 and d3 (d1 is within 30 min, d2 has facebook_post_id).
    # d1 and d2 must NEVER be moved
    moved_ids = {r["delivery_id"] for r in results}
    assert d1.id not in moved_ids
    assert d2.id not in moved_ids

    # d1 and d2 remain untouched in DB
    db_session.refresh(d1)
    db_session.refresh(d2)
    assert d1.state == "repliz_scheduled"
    assert d1.scheduled_for == datetime(2026, 10, 26, 10, 15)
    assert d2.state == "repliz_scheduled"
    assert d2.facebook_post_id == "fb_post_999"


def test_limit_flag_moves_at_most_n_deliveries(db_session: Session):
    """6. --limit 1 -> only one row moved."""
    project, dest = _setup_page_and_dest(db_session, cap=3)
    now_utc = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)

    slots = [
        datetime(2026, 10, 26, 10, 0),
        datetime(2026, 10, 26, 11, 0),
        datetime(2026, 10, 26, 12, 0),
        datetime(2026, 10, 26, 13, 0),
        datetime(2026, 10, 26, 14, 0),
        datetime(2026, 10, 26, 15, 0),
    ]
    deliveries = []
    for i, s in enumerate(slots):
        job = PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ai_video,
            status=PublishJobStatus.publishing,
            repliz_schedule_id=f"sched_{i}",
        )
        db_session.add(job)
        db_session.flush()
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=job.id,
            state="repliz_scheduled",
            repliz_schedule_id=f"sched_{i}",
            scheduled_for=s,
        )
        db_session.add(deliv)
        deliveries.append(deliv)
    db_session.commit()

    client = FakeReplizClient()
    results = respread(db_session, client, now_utc=now_utc, apply=True, limit=1)

    # Exactly 1 row moved
    assert len(results) == 1
    assert results[0]["action"] == "moved"
    assert results[0]["delivery_id"] == deliveries[5].id

    # Only 1 deleted on Repliz
    assert client.deleted_schedule_ids == ["sched_5"]

    # 5 remain on today
    deliveries_today = (
        db_session.query(PublishDelivery)
        .filter(
            PublishDelivery.destination_id == dest.id,
            PublishDelivery.scheduled_for >= datetime(2026, 10, 26, 0, 0),
            PublishDelivery.scheduled_for < datetime(2026, 10, 27, 0, 0),
        )
        .count()
    )
    assert deliveries_today == 5


def test_parse_args():
    """Verify CLI argument parser."""
    parsed = parse_args([])
    assert parsed.apply is False
    assert parsed.limit == 0
    assert parsed.destination is None

    parsed = parse_args(["--apply", "--limit", "5", "--destination", "42"])
    assert parsed.apply is True
    assert parsed.limit == 5
    assert parsed.destination == 42
