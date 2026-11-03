"""Regression coverage for parent job status decisions and the recovery sweep."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import PublishDelivery, SocialDestination
from app.models.target_fanpages import TargetFanpage
from app.services.job_status_sync import apply_job_status, decide_job_status
from app.tasks.publisher import sync_finished_publish_jobs


NOW = datetime(2026, 11, 3, 12)


def _delivery(state="published", id=1, **overrides):
    fields = dict(
        id=id, state=state, last_error=None, published_at=None,
        scheduled_for=None, actual_method="phone", repliz_schedule_id=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _job(status=PublishJobStatus.pending_publish, **overrides):
    fields = dict(
        status=status, last_error="previous error", published_at=None,
        cleanup_at=None, repliz_schedule_id=None, scheduled_for=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_empty_deliveries_have_no_decision():
    assert decide_job_status([]) == (None, None)


def test_all_published_deliveries_publish_job():
    assert decide_job_status([_delivery(id=7), _delivery(id=3)]) == (
        PublishJobStatus.published, None,
    )


def test_published_and_repliz_scheduled_deliveries_publish_job():
    assert decide_job_status([_delivery(), _delivery("repliz_scheduled", id=2)]) == (
        PublishJobStatus.published, None,
    )


def test_all_stale_skips_use_lowest_id_error_verbatim():
    deliveries = [
        _delivery("skipped", id=7, last_error="stale: newer delivery"),
        _delivery("skipped", id=3, last_error="stale: oldest delivery"),
    ]
    assert decide_job_status(deliveries) == (
        PublishJobStatus.skipped, "stale: oldest delivery",
    )


def test_published_delivery_wins_over_failed_delivery():
    assert decide_job_status([_delivery("failed"), _delivery(id=2)]) == (
        PublishJobStatus.published, None,
    )


def test_failed_deliveries_use_lowest_id_failure():
    deliveries = [
        _delivery("failed", id=7, last_error="later error"),
        _delivery("failed", id=3, last_error="first error"),
    ]
    assert decide_job_status(deliveries) == (
        PublishJobStatus.failed, "delivery 3 failed: first error",
    )


def test_failed_delivery_without_error_uses_fallback_text():
    assert decide_job_status([_delivery("failed", id=4)]) == (
        PublishJobStatus.failed, "delivery 4 failed: no error text",
    )


def test_failure_wins_over_non_stale_skip():
    deliveries = [
        _delivery("skipped", id=1, last_error="unsupported media"),
        _delivery("failed", id=7, last_error="device error"),
    ]
    assert decide_job_status(deliveries) == (
        PublishJobStatus.failed, "delivery 7 failed: device error",
    )


def test_non_stale_skips_use_lowest_id_error_with_prefix():
    deliveries = [
        _delivery("skipped", id=7, last_error="later skip"),
        _delivery("skipped", id=3, last_error="unsupported media"),
    ]
    assert decide_job_status(deliveries) == (
        PublishJobStatus.skipped, "delivery 3 skipped: unsupported media",
    )


@pytest.mark.parametrize("other_state", ["failed", "published"])
def test_uncertain_delivery_prevents_any_decision(other_state):
    assert decide_job_status([
        _delivery(other_state), _delivery("uncertain", id=2),
    ]) == (None, None)


@pytest.mark.parametrize("state", [
    "needs_human", "scheduled", "waiting_for_slot", "waiting_for_device", "publishing",
])
def test_unfinished_delivery_with_failure_has_no_decision(state):
    assert decide_job_status([
        _delivery("failed"), _delivery(state, id=2),
    ]) == (None, None)


def test_repliz_scheduled_with_failure_has_no_decision():
    assert decide_job_status([
        _delivery("repliz_scheduled"), _delivery("failed", id=2),
    ]) == (None, None)


def test_apply_preserves_old_phone_publish_time_and_cleanup_go_live_floor():
    published_at = NOW - timedelta(days=2)
    scheduled_for = published_at + timedelta(hours=1)
    job = _job()
    delivery = _delivery(published_at=published_at, scheduled_for=scheduled_for)

    assert apply_job_status(job, [delivery], NOW) is True
    assert job.status == PublishJobStatus.published
    assert job.published_at == published_at
    assert job.last_error is None
    assert job.cleanup_at == max(published_at, scheduled_for) + timedelta(hours=6)


def test_apply_converts_aware_delivery_times_to_naive_utc():
    offset = timezone(timedelta(hours=7))
    job = _job()
    deliveries = [
        _delivery(published_at=datetime(2026, 11, 1, 14, tzinfo=offset)),
        _delivery(
            "repliz_scheduled", id=2,
            published_at=datetime(2026, 11, 1, 16, tzinfo=offset),
            scheduled_for=datetime(2026, 11, 2, 10, tzinfo=offset),
        ),
    ]

    assert apply_job_status(job, deliveries, NOW) is True
    assert job.published_at == datetime(2026, 11, 1, 9)
    assert job.cleanup_at == datetime(2026, 11, 2, 9)
    assert job.published_at.tzinfo is None
    assert job.cleanup_at.tzinfo is None


def test_apply_uses_now_when_no_delivery_has_publish_time():
    job = _job()
    assert apply_job_status(job, [_delivery(), _delivery(id=2)], NOW) is True
    assert job.published_at == NOW
    assert job.cleanup_at == NOW + timedelta(hours=6)


def test_apply_copies_single_repliz_schedule_fields():
    job = _job()
    scheduled_for = NOW + timedelta(days=1)
    delivery = _delivery(
        "repliz_scheduled", actual_method="repliz",
        repliz_schedule_id="schedule-123", scheduled_for=scheduled_for,
    )
    assert apply_job_status(job, [delivery], NOW) is True
    assert job.status == PublishJobStatus.published
    assert job.repliz_schedule_id == "schedule-123"
    assert job.scheduled_for == scheduled_for


def test_apply_does_not_move_already_published_job_timestamp():
    published_at = NOW - timedelta(days=4)
    job = _job(PublishJobStatus.published, published_at=published_at)
    before = vars(job).copy()
    assert apply_job_status(job, [_delivery(published_at=NOW)], NOW) is False
    assert job.published_at == published_at
    assert vars(job) == before


def test_apply_never_downgrades_published_job_to_failed():
    job = _job(PublishJobStatus.published, published_at=NOW - timedelta(days=4))
    before = vars(job).copy()
    assert apply_job_status(job, [_delivery("failed")], NOW) is False
    assert job.status == PublishJobStatus.published
    assert vars(job) == before


@pytest.mark.parametrize("status", [
    PublishJobStatus.pending_review, PublishJobStatus.failed, PublishJobStatus.skipped,
])
def test_apply_leaves_non_syncable_job_untouched(status):
    job = _job(status)
    before = vars(job).copy()
    assert apply_job_status(job, [_delivery(published_at=NOW)], NOW) is False
    assert vars(job) == before


def test_apply_marks_all_failed_job_once():
    job = _job()
    deliveries = [_delivery("failed", id=3, last_error="device unavailable")]
    assert apply_job_status(job, deliveries, NOW) is True
    assert job.status == PublishJobStatus.failed
    assert job.last_error == "delivery 3 failed: device unavailable"
    before = vars(job).copy()
    assert apply_job_status(job, deliveries, NOW) is False
    assert vars(job) == before


def test_apply_leaves_publishing_job_with_uncertain_delivery_untouched():
    job = _job(PublishJobStatus.publishing)
    before = vars(job).copy()
    assert apply_job_status(job, [_delivery("uncertain")], NOW) is False
    assert job.status == PublishJobStatus.publishing
    assert vars(job) == before


@pytest.fixture
def db_session(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_ARRAY", lambda *a, **kw: "TEXT", raising=False)
    column = SocialDestination.__table__.columns["supported_media"]
    monkeypatch.setattr(column, "server_default", None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        TargetFanpage.__table__, SocialDestination.__table__,
        PublishJob.__table__, PublishDelivery.__table__,
    ])
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def sweep_data(db_session):
    fanpage = TargetFanpage(name="Status sync project", is_active=True)
    db_session.add(fanpage)
    db_session.flush()
    destination = SocialDestination(
        content_project_id=fanpage.id, kind="facebook_profile",
        name="Status sync destination", mode="phone", timezone="UTC",
        daily_target=1, min_gap_minutes=15, supported_media=["image"],
    )
    db_session.add(destination)
    db_session.flush()

    def create_job(state=None, **overrides):
        fields = dict(
            fanpage_id=fanpage.id, content_type=ContentType.news_content,
            status=PublishJobStatus.pending_publish, is_deleted=False,
        )
        fields.update(overrides)
        job = PublishJob(**fields)
        db_session.add(job)
        db_session.flush()
        if state is not None:
            db_session.add(PublishDelivery(
                publish_job_id=job.id, destination_id=destination.id,
                state=state, actual_method="phone",
                last_error="device error" if state == "failed" else None,
                published_at=NOW - timedelta(days=3) if state == "published" else None,
            ))
        db_session.commit()
        return job

    with patch("app.tasks.publisher.SessionLocal", return_value=db_session), \
         patch.object(db_session, "close"):
        yield create_job


def test_sweep_updates_only_finished_jobs_and_preserves_old_publish_time(db_session, sweep_data):
    failed = sweep_data("failed")
    uncertain = sweep_data("uncertain")
    published = sweep_data("published")
    scheduled = sweep_data("scheduled")
    empty = sweep_data()

    assert sync_finished_publish_jobs() == 2
    db_session.expire_all()
    assert failed.status == PublishJobStatus.failed
    assert failed.last_error == f"delivery {failed.deliveries[0].id} failed: device error"
    assert uncertain.status == PublishJobStatus.pending_publish
    assert published.status == PublishJobStatus.published
    assert published.published_at == NOW - timedelta(days=3)
    assert scheduled.status == PublishJobStatus.pending_publish
    assert empty.status == PublishJobStatus.pending_publish


def test_sweep_ignores_deleted_jobs(db_session, sweep_data):
    job = sweep_data("failed", is_deleted=True, last_error="keep deleted error")
    assert sync_finished_publish_jobs() == 0
    db_session.refresh(job)
    assert job.status == PublishJobStatus.pending_publish
    assert job.last_error == "keep deleted error"


def test_sweep_ignores_pending_review_jobs(db_session, sweep_data):
    job = sweep_data(
        "failed", status=PublishJobStatus.pending_review, last_error="keep review error",
    )
    assert sync_finished_publish_jobs() == 0
    db_session.refresh(job)
    assert job.status == PublishJobStatus.pending_review
    assert job.last_error == "keep review error"


def test_sweep_second_run_changes_no_jobs(db_session, sweep_data):
    failed = sweep_data("failed")
    published = sweep_data("published")
    assert sync_finished_publish_jobs() == 2
    db_session.refresh(failed)
    db_session.refresh(published)
    before = (failed.status, failed.last_error, published.published_at, published.cleanup_at)
    assert sync_finished_publish_jobs() == 0
    db_session.refresh(failed)
    db_session.refresh(published)
    assert (failed.status, failed.last_error, published.published_at, published.cleanup_at) == before
