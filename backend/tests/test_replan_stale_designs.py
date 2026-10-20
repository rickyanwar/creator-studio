"""Tests for replan_stale_designs script.

Verifies:
- (a) A dry run leaves the DB unchanged: the old skipped rows are still there and the job is still pending_publish.
- (b) With apply, a fresh job whose ensure_deliveries returns a scheduled delivery -> old skipped rows are gone, job stays pending_publish.
- (c) Apply, and ensure_deliveries returns a skipped stale delivery -> job becomes skipped.
- (d) A job older than 24 h -> skipped "too old", and ensure_deliveries is not called for it.
- (e) A job with a delivery that has repliz_schedule_id (or facebook_post_id) -> untouched.
- (f) The newest job of a page is processed first (assert on the call order of patched ensure_deliveries).
- (g) Fanpage filtering (--fanpage-id).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session

sqlite3.register_adapter(list, json.dumps)
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
    RecoveryIncident,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.scripts.replan_stale_designs import parse_args, replan_stale_designs


@pytest.fixture
def db_session():
    """Create in-memory SQLite schema and session."""
    sd_col = SocialDestination.__table__.columns["supported_media"]
    orig_sd = sd_col.server_default
    sd_col.server_default = None

    de_col = DeliveryEvent.__table__.columns["details"]
    orig_de = de_col.server_default
    de_col.server_default = None

    pj_del = PublishJob.__table__.columns["is_deleted"]
    orig_pj_del = pj_del.server_default
    pj_del.server_default = None

    engine = create_engine("sqlite:///:memory:")
    TargetFanpage.__table__.create(engine)
    SocialDestination.__table__.create(engine)
    PhoneDevice.__table__.create(engine)
    DestinationDeviceAssignment.__table__.create(engine)
    Post.__table__.create(engine)
    PublishJob.__table__.create(engine)
    PublishDelivery.__table__.create(engine)
    DeliveryEvent.__table__.create(engine)
    RecoveryIncident.__table__.create(engine)

    sd_col.server_default = orig_sd
    de_col.server_default = orig_de
    pj_del.server_default = orig_pj_del

    session = Session(engine)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _make_fanpage(db: Session, name: str = "Test FP") -> TargetFanpage:
    fp = TargetFanpage(
        name=name,
        repliz_account_id=f"rep_{name}",
        is_active=True,
    )
    db.add(fp)
    db.commit()
    return fp


def _make_destination(
    db: Session, fanpage: TargetFanpage, name: str = "Dest 1"
) -> SocialDestination:
    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name=name,
        mode="repliz",
        timezone="Europe/London",
        daily_target=5,
        phone_daily_target=0,
        repliz_daily_target=5,
        supported_media=["image", "album", "video"],
        news_stale_hours=24,
        is_active=True,
    )
    db.add(dest)
    db.commit()
    return dest


def _make_job(
    db: Session,
    fanpage: TargetFanpage,
    *,
    status: PublishJobStatus = PublishJobStatus.pending_publish,
    content_type: ContentType = ContentType.news_content,
    created_at: datetime | None = None,
    design_image_path: str = "/tmp/test.png",
    is_deleted: bool = False,
) -> PublishJob:
    job = PublishJob(
        fanpage_id=fanpage.id,
        status=status,
        content_type=content_type,
        created_at=created_at or datetime.now(timezone.utc),
        design_image_path=design_image_path,
        is_deleted=is_deleted,
    )
    db.add(job)
    db.commit()
    return job


def _make_delivery(
    db: Session,
    job: PublishJob,
    destination: SocialDestination,
    *,
    state: str = "skipped",
    last_error: str | None = "stale: no News slot within 24 h (slot is ...)",
    repliz_schedule_id: str | None = None,
    facebook_post_id: str | None = None,
    scheduled_for: datetime | None = None,
) -> PublishDelivery:
    delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=destination.id,
        state=state,
        last_error=last_error,
        repliz_schedule_id=repliz_schedule_id,
        facebook_post_id=facebook_post_id,
        scheduled_for=scheduled_for,
    )
    db.add(delivery)
    db.commit()
    return delivery


def test_dry_run_leaves_db_unchanged(db_session: Session):
    """(a) A dry run leaves the DB unchanged: old skipped rows are still there and job stays pending_publish."""
    fp = _make_fanpage(db_session)
    dest = _make_destination(db_session, fp)
    now = datetime.now(timezone.utc)
    job = _make_job(db_session, fp, created_at=now - timedelta(hours=2))
    old_delivery = _make_delivery(db_session, job, dest)

    scheduled_delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        state="scheduled",
        scheduled_for=now + timedelta(hours=3),
    )

    with patch(
        "app.scripts.replan_stale_designs.ensure_deliveries",
        return_value=[scheduled_delivery],
    ):
        replan_stale_designs(db_session, apply=False, now_utc=now)

    db_session.expire_all()
    j = db_session.query(PublishJob).filter_by(id=job.id).one()
    assert j.status == PublishJobStatus.pending_publish

    delivs = db_session.query(PublishDelivery).filter_by(publish_job_id=job.id).all()
    assert len(delivs) == 1
    assert delivs[0].id == old_delivery.id
    assert delivs[0].state == "skipped"


def test_apply_fresh_job_scheduled_deletes_old_deliveries(db_session: Session):
    """(b) With apply, fresh job whose ensure_deliveries returns scheduled delivery -> old rows gone, job pending_publish."""
    fp = _make_fanpage(db_session)
    dest = _make_destination(db_session, fp)
    now = datetime.now(timezone.utc)
    job = _make_job(db_session, fp, created_at=now - timedelta(hours=2))
    old_delivery = _make_delivery(db_session, job, dest)

    new_delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        state="scheduled",
        scheduled_for=now + timedelta(hours=3),
    )

    def fake_ensure(db, j, now_utc=None):
        db.add(new_delivery)
        db.flush()
        return [new_delivery]

    with patch(
        "app.scripts.replan_stale_designs.ensure_deliveries", side_effect=fake_ensure
    ):
        replan_stale_designs(db_session, apply=True, now_utc=now)

    db_session.expire_all()
    j = db_session.query(PublishJob).filter_by(id=job.id).one()
    assert j.status == PublishJobStatus.pending_publish

    delivs = db_session.query(PublishDelivery).filter_by(publish_job_id=job.id).all()
    assert len(delivs) == 1
    assert delivs[0].id == new_delivery.id
    assert delivs[0].state == "scheduled"
    assert delivs[0].scheduled_for is not None


def test_apply_fresh_job_stale_again_becomes_skipped(db_session: Session):
    """(c) Apply, and ensure_deliveries returns a skipped stale delivery -> job becomes skipped."""
    fp = _make_fanpage(db_session)
    dest = _make_destination(db_session, fp)
    now = datetime.now(timezone.utc)
    job = _make_job(db_session, fp, created_at=now - timedelta(hours=2))
    old_delivery = _make_delivery(db_session, job, dest)

    stale_err = "stale: no News slot within 24 h (slot is 2026-10-22)"
    new_delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        state="skipped",
        last_error=stale_err,
    )

    def fake_ensure(db, j, now_utc=None):
        db.add(new_delivery)
        db.flush()
        return [new_delivery]

    with patch(
        "app.scripts.replan_stale_designs.ensure_deliveries", side_effect=fake_ensure
    ):
        replan_stale_designs(db_session, apply=True, now_utc=now)

    db_session.expire_all()
    j = db_session.query(PublishJob).filter_by(id=job.id).one()
    assert j.status == PublishJobStatus.skipped
    assert j.last_error == stale_err
    delivs = db_session.query(PublishDelivery).filter_by(publish_job_id=job.id).all()
    assert len(delivs) == 1
    assert delivs[0].id == new_delivery.id
    assert delivs[0].last_error == stale_err


def test_job_older_than_hours_skipped_without_ensure_deliveries(db_session: Session):
    """(d) A job older than 24 h -> skipped "too old", and ensure_deliveries is not called for it."""
    fp = _make_fanpage(db_session)
    dest = _make_destination(db_session, fp)
    now = datetime.now(timezone.utc)
    job = _make_job(db_session, fp, created_at=now - timedelta(hours=25))
    stale_err = "stale: no News slot within 24 h (slot was yesterday)"
    delivery = _make_delivery(db_session, job, dest, last_error=stale_err)

    with patch("app.scripts.replan_stale_designs.ensure_deliveries") as mock_ensure:
        stats = replan_stale_designs(db_session, apply=True, hours=24, now_utc=now)
        mock_ensure.assert_not_called()

    db_session.expire_all()
    j = db_session.query(PublishJob).filter_by(id=job.id).one()
    assert j.status == PublishJobStatus.skipped
    assert j.last_error == stale_err
    assert stats[fp.id]["too_old"] == 1
    assert stats[fp.id]["planned"] == 0
    assert stats[fp.id]["no_slot"] == 0
    # Delivery row was not deleted for too old jobs
    assert (
        db_session.query(PublishDelivery).filter_by(id=delivery.id).first() is not None
    )


def test_job_with_external_id_untouched(db_session: Session):
    """(e) A job with a delivery that has repliz_schedule_id or facebook_post_id -> untouched."""
    fp = _make_fanpage(db_session)
    dest = _make_destination(db_session, fp)
    now = datetime.now(timezone.utc)

    # Job 1 with repliz_schedule_id
    job1 = _make_job(db_session, fp, created_at=now - timedelta(hours=2))
    deliv1 = _make_delivery(
        db_session, job1, dest, repliz_schedule_id="ext-sched-123"
    )

    # Job 2 with facebook_post_id
    job2 = _make_job(db_session, fp, created_at=now - timedelta(hours=3))
    deliv2 = _make_delivery(db_session, job2, dest, facebook_post_id="fb-post-456")

    with patch("app.scripts.replan_stale_designs.ensure_deliveries") as mock_ensure:
        replan_stale_designs(db_session, apply=True, now_utc=now)
        mock_ensure.assert_not_called()

    db_session.expire_all()
    j1 = db_session.query(PublishJob).filter_by(id=job1.id).one()
    assert j1.status == PublishJobStatus.pending_publish

    j2 = db_session.query(PublishJob).filter_by(id=job2.id).one()
    assert j2.status == PublishJobStatus.pending_publish

    assert (
        db_session.query(PublishDelivery).filter_by(id=deliv1.id).one().repliz_schedule_id
        == "ext-sched-123"
    )
    assert (
        db_session.query(PublishDelivery).filter_by(id=deliv2.id).one().facebook_post_id
        == "fb-post-456"
    )


def test_newest_job_of_page_processed_first(db_session: Session):
    """(f) The newest job of a page is processed first (assert on the call order of patched ensure_deliveries)."""
    fp = _make_fanpage(db_session)
    dest = _make_destination(db_session, fp)
    now = datetime.now(timezone.utc)

    job_older = _make_job(db_session, fp, created_at=now - timedelta(hours=6))
    job_newer = _make_job(db_session, fp, created_at=now - timedelta(hours=1))
    job_middle = _make_job(db_session, fp, created_at=now - timedelta(hours=3))

    _make_delivery(db_session, job_older, dest)
    _make_delivery(db_session, job_newer, dest)
    _make_delivery(db_session, job_middle, dest)

    called_job_ids = []

    def fake_ensure(db, job, now_utc=None):
        called_job_ids.append(job.id)
        d = PublishDelivery(
            publish_job_id=job.id,
            destination_id=dest.id,
            state="scheduled",
            scheduled_for=now + timedelta(hours=2),
        )
        return [d]

    with patch(
        "app.scripts.replan_stale_designs.ensure_deliveries", side_effect=fake_ensure
    ):
        replan_stale_designs(db_session, apply=True, now_utc=now)

    assert called_job_ids == [job_newer.id, job_middle.id, job_older.id]


def test_fanpage_id_filter(db_session: Session):
    """(g) Specifying fanpage_id only processes that fanpage."""
    fp1 = _make_fanpage(db_session, name="FP 1")
    fp2 = _make_fanpage(db_session, name="FP 2")
    dest1 = _make_destination(db_session, fp1, name="Dest FP 1")
    dest2 = _make_destination(db_session, fp2, name="Dest FP 2")
    now = datetime.now(timezone.utc)

    job1 = _make_job(db_session, fp1, created_at=now - timedelta(hours=2))
    job2 = _make_job(db_session, fp2, created_at=now - timedelta(hours=2))
    _make_delivery(db_session, job1, dest1)
    _make_delivery(db_session, job2, dest2)

    called_jobs = []

    def fake_ensure(db, job, now_utc=None):
        called_jobs.append(job.id)
        return [
            PublishDelivery(
                publish_job_id=job.id,
                destination_id=job.fanpage_id,
                state="scheduled",
                scheduled_for=now + timedelta(hours=2),
            )
        ]

    with patch(
        "app.scripts.replan_stale_designs.ensure_deliveries", side_effect=fake_ensure
    ):
        stats = replan_stale_designs(
            db_session, apply=True, fanpage_id=fp1.id, now_utc=now
        )

    assert called_jobs == [job1.id]
    assert fp1.id in stats
    assert fp2.id not in stats


def test_parse_args_defaults_and_flags():
    """Verify argument parsing logic."""
    args = parse_args([])
    assert args.apply is False
    assert args.hours == 24
    assert args.fanpage_id is None

    args = parse_args(["--apply", "--hours", "12", "--fanpage-id", "5"])
    assert args.apply is True
    assert args.hours == 12
    assert args.fanpage_id == 5


def test_end_to_end_real_ensure_deliveries(db_session: Session):
    """End-to-end integration test with unmocked ensure_deliveries."""
    fp = _make_fanpage(db_session, name="Integration FP")
    dest = _make_destination(db_session, fp, name="Integration Dest")
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    job = _make_job(db_session, fp, created_at=now - timedelta(hours=2))
    old_deliv = _make_delivery(db_session, job, dest, last_error="stale: old error")

    stats = replan_stale_designs(db_session, apply=True, now_utc=now)

    db_session.expire_all()
    j = db_session.query(PublishJob).filter_by(id=job.id).one()
    assert j.status == PublishJobStatus.pending_publish

    delivs = db_session.query(PublishDelivery).filter_by(publish_job_id=job.id).all()
    assert len(delivs) == 1
    assert delivs[0].state == "scheduled"
    assert delivs[0].scheduled_for is not None
    assert stats[fp.id]["planned"] == 1

