"""Tests for publisher H3 stale deliveries handling."""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session, sessionmaker

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
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.tasks.publisher import publish_job


@pytest.fixture
def db_engine():
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

    return engine


@pytest.fixture
def db_session(db_engine):
    SessionMaker = sessionmaker(bind=db_engine)
    session = SessionMaker()
    try:
        yield session
    finally:
        session.close()


def test_publisher_all_deliveries_stale_skipped(db_engine, db_session):
    """When all deliveries are skipped with 'stale: ...', job is marked skipped."""
    SessionMaker = sessionmaker(bind=db_engine)

    fp = TargetFanpage(name="Test FP", repliz_account_id="rep_1", is_active=True)
    db_session.add(fp)
    db_session.flush()

    dest1 = SocialDestination(
        content_project_id=fp.id,
        name="Dest 1",
        kind="facebook_page",
        mode="repliz",
        is_active=True,
    )
    dest2 = SocialDestination(
        content_project_id=fp.id,
        name="Dest 2",
        kind="facebook_page",
        mode="repliz",
        is_active=True,
    )
    db_session.add_all([dest1, dest2])
    db_session.flush()

    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job)
    db_session.flush()

    d1 = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest1.id,
        actual_method="repliz",
        state="skipped",
        last_error="stale: no News slot within 24 h (slot is 2026-10-21 15:00:00+00:00)",
    )
    d2 = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest2.id,
        actual_method="repliz",
        state="skipped",
        last_error="stale: no News slot within 24 h (slot is 2026-10-21 16:00:00+00:00)",
    )
    db_session.add_all([d1, d2])
    db_session.commit()

    with patch("app.tasks.publisher.SessionLocal", side_effect=SessionMaker), \
         patch("app.services.social_delivery_planner.ensure_deliveries", return_value=[d1, d2]), \
         patch("app.services.social_delivery_dispatch.dispatch_due_deliveries"):
        publish_job.apply(args=[job.id])

    db_session.expire_all()
    updated_job = db_session.query(PublishJob).filter_by(id=job.id).one()
    assert updated_job.status == PublishJobStatus.skipped
    assert updated_job.last_error == d1.last_error


def test_publisher_one_delivery_skipped_for_other_reason(db_engine, db_session):
    """When one delivery is skipped for non-stale reason, job stays pending_publish."""
    SessionMaker = sessionmaker(bind=db_engine)

    fp = TargetFanpage(name="Test FP 2", repliz_account_id="rep_2", is_active=True)
    db_session.add(fp)
    db_session.flush()

    dest1 = SocialDestination(
        content_project_id=fp.id,
        name="Dest 1",
        kind="facebook_page",
        mode="repliz",
        is_active=True,
    )
    dest2 = SocialDestination(
        content_project_id=fp.id,
        name="Dest 2",
        kind="facebook_page",
        mode="repliz",
        is_active=True,
    )
    db_session.add_all([dest1, dest2])
    db_session.flush()

    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job)
    db_session.flush()

    d1 = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest1.id,
        actual_method="repliz",
        state="skipped",
        last_error="stale: no News slot within 24 h",
    )
    d2 = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest2.id,
        actual_method="repliz",
        state="skipped",
        last_error="unsupported media: video required",
    )
    db_session.add_all([d1, d2])
    db_session.commit()

    with patch("app.tasks.publisher.SessionLocal", side_effect=SessionMaker), \
         patch("app.services.social_delivery_planner.ensure_deliveries", return_value=[d1, d2]), \
         patch("app.services.social_delivery_dispatch.dispatch_due_deliveries"):
        publish_job.apply(args=[job.id])

    db_session.expire_all()
    updated_job = db_session.query(PublishJob).filter_by(id=job.id).one()
    assert updated_job.status == PublishJobStatus.pending_publish
