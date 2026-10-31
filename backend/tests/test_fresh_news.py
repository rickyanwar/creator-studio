"""Tests for fresh news first (TASK T7).

Verifies:
1. _plan_slot stale limit: event day -> 6 h; normal -> 12 h; destination override -> 4 h.
2. Supersede: destination full today with old untouched news delivery -> new news job gets that slot,
   old delivery skipped with superseded reason, old job skipped.
3. No supersede when old delivery has repliz_schedule_id, uncertain_since, attempt_count > 0,
   or old job is breaking.
4. Dispatch gate: Repliz news with slot in 2 h holds; in 30 min dispatches; news 13 h old skips;
   non-news (ai_video) with slot in 2 h dispatches ahead.
5. Visual queue: news job 7 h old on event day skipped; 5 h old remains candidate.
6. news_slot_available returns True when only a supersedable delivery makes a slot possible.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from unittest.mock import MagicMock, patch
import zoneinfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"

from app.models.design_templates import DesignTemplate
from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ScrapedArticle
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    DeviceLease,
    OperationalNotification,
    PhoneDevice,
    PublishDelivery,
    RecoveryIncident,
    SocialDestination,
)
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.social_delivery_dispatch import (
    REPLIZ_NEWS_SUBMIT_LEAD_MINUTES,
    _news_dispatch_gate,
    dispatch_due_deliveries,
)
from app.services.social_delivery_planner import (
    SUPERSEDE_REASON,
    _plan_slot,
    _supersedable_news_delivery,
    ensure_deliveries,
    news_slot_available,
    next_news_slot_utc,
)
from app.services.visual_fair_queue import pick_next_news_job


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.queues: dict[str, list] = {}

    def get(self, key: str):
        return self.data.get(key)

    def mget(self, keys: list[str]):
        return [self.data.get(k) for k in keys]

    def incr(self, key: str, amount: int = 1):
        val = int(self.data.get(key, 0)) + amount
        self.data[key] = str(val)
        return val

    def expire(self, key: str, time: int):
        return True

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.data:
            return None
        self.data[key] = str(value)
        return True

    def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self.data)

    def delete(self, *keys: str):
        count = 0
        for k in keys:
            if self.data.pop(k, None) is not None:
                count += 1
        return count


@pytest.fixture
def db_session():
    """Create in-memory SQLite schema and session."""
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
    DeviceLease.__table__.create(engine)
    RecoveryIncident.__table__.create(engine)
    OperationalNotification.__table__.create(engine)
    ScrapedArticle.__table__.create(engine)
    DesignTemplate.__table__.create(engine)

    sd_col.server_default = orig_sd
    de_col.server_default = orig_de

    session = Session(engine)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_plan_slot_stale_limit(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    """1. _plan_slot stale limit: event day -> 6; normal -> 12; destination override 4 -> 4."""
    fanpage = TargetFanpage(name="FP1", is_active=True)
    db_session.add(fanpage)
    db_session.flush()

    dest_normal = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Dest Normal",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        supported_media=["image"],
        is_active=True,
    )
    dest_custom = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Dest Custom",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        news_stale_hours=4,
        supported_media=["image"],
        is_active=True,
    )
    db_session.add_all([dest_normal, dest_custom])
    db_session.flush()

    job = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        created_at=datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc),
    )
    db_session.add(job)
    db_session.flush()

    now_utc = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)

    # Event day: multiplier returns 2 -> news_stale_hours 6
    monkeypatch.setattr("app.services.event_days.event_multiplier", lambda *args, **kwargs: 2)
    _, _, hours_event, _ = _plan_slot(db_session, dest_normal, job, now_utc, None, None)
    assert hours_event == 6

    # Normal day: multiplier returns 1 -> news_stale_hours 12
    monkeypatch.setattr("app.services.event_days.event_multiplier", lambda *args, **kwargs: 1)
    _, _, hours_normal, _ = _plan_slot(db_session, dest_normal, job, now_utc, None, None)
    assert hours_normal == 12

    # Destination news_stale_hours 4 -> 4 (on normal day and event day)
    _, _, hours_override, _ = _plan_slot(db_session, dest_custom, job, now_utc, None, None)
    assert hours_override == 4


def test_supersede_old_untouched_news(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    """2. Supersede: destination full today with old untouched news delivery -> new job gets that slot."""
    monkeypatch.setattr("app.services.event_days.event_multiplier", lambda *args, **kwargs: 1)

    fanpage = TargetFanpage(name="FP Supersede", is_active=True)
    db_session.add(fanpage)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Dest Supersede",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        phone_daily_target=0,
        repliz_daily_target=1,
        min_gap_minutes=60,
        supported_media=["image"],
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    now_utc = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    old_created = now_utc - timedelta(hours=3)

    # Old job created 3 h ago, delivery scheduled for 13:00 (in 1 h)
    job_old = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/old.png",
        ai_generated_caption="Old news",
        created_at=old_created,
        status=PublishJobStatus.pending_publish,
        is_breaking=False,
    )
    db_session.add(job_old)
    db_session.flush()

    slot_13 = datetime(2026, 10, 15, 13, 0, 0)
    deliv_old = PublishDelivery(
        publish_job_id=job_old.id,
        destination_id=dest.id,
        state="scheduled",
        intended_method="repliz",
        scheduled_for=slot_13,
        caption_snapshot="Old news",
        media_snapshot=["https://cdn.example.com/old.png"],
        media_kind="image",
        attempt_count=0,
    )
    db_session.add(deliv_old)
    db_session.commit()

    # New news job created now
    job_new = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/new.png",
        ai_generated_caption="Fresh news",
        created_at=now_utc,
        status=PublishJobStatus.pending_publish,
        is_breaking=False,
    )
    db_session.add(job_new)
    db_session.flush()

    # Ensure deliveries for new job
    deliveries = ensure_deliveries(db_session, job_new, now_utc=now_utc)
    assert len(deliveries) == 1
    deliv_new = deliveries[0]

    # New delivery gets that same slot and is scheduled (not skipped)
    assert deliv_new.state == "scheduled"
    assert deliv_new.scheduled_for == slot_13

    # Old delivery marked skipped with supersede message and cleared slot
    db_session.refresh(deliv_old)
    assert deliv_old.state == "skipped"
    assert deliv_old.scheduled_for is None
    assert deliv_old.last_error == SUPERSEDE_REASON.format(job_id=job_new.id)

    # Old job marked skipped
    db_session.refresh(job_old)
    assert job_old.status == PublishJobStatus.skipped
    assert job_old.last_error == deliv_old.last_error


def test_no_supersede_conditions(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    """3. No supersede when old delivery has repliz_schedule_id, uncertain_since, attempt_count=1, or is breaking."""
    monkeypatch.setattr("app.services.event_days.event_multiplier", lambda *args, **kwargs: 1)

    fanpage = TargetFanpage(name="FP No Supersede", is_active=True)
    db_session.add(fanpage)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Dest No Supersede",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        phone_daily_target=0,
        repliz_daily_target=1,
        min_gap_minutes=60,
        supported_media=["image"],
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    now_utc = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    old_created = now_utc - timedelta(hours=3)
    slot_13 = datetime(2026, 10, 15, 13, 0, 0)

    # Test cases: (field_name, field_value, is_breaking)
    cases = [
        ("repliz_schedule_id", "sched_existing", False),
        ("uncertain_since", datetime(2026, 10, 15, 11, 0, 0), False),
        ("attempt_count", 1, False),
        (None, None, True),  # breaking old job
    ]

    for idx, (field_name, field_value, is_breaking) in enumerate(cases):
        job_old = PublishJob(
            fanpage_id=fanpage.id,
            content_type=ContentType.news_content,
            design_image_url=f"https://cdn.example.com/old_{idx}.png",
            ai_generated_caption=f"Old news {idx}",
            created_at=old_created,
            status=PublishJobStatus.pending_publish,
            is_breaking=is_breaking,
        )
        db_session.add(job_old)
        db_session.flush()

        deliv_kwargs = {
            "publish_job_id": job_old.id,
            "destination_id": dest.id,
            "state": "scheduled",
            "intended_method": "repliz",
            "scheduled_for": slot_13,
            "caption_snapshot": f"Old news {idx}",
            "media_snapshot": [f"https://cdn.example.com/old_{idx}.png"],
            "media_kind": "image",
            "attempt_count": 0,
        }
        if field_name:
            deliv_kwargs[field_name] = field_value

        deliv_old = PublishDelivery(**deliv_kwargs)
        db_session.add(deliv_old)
        db_session.commit()

        # New job arriving now
        job_new = PublishJob(
            fanpage_id=fanpage.id,
            content_type=ContentType.news_content,
            design_image_url=f"https://cdn.example.com/new_{idx}.png",
            ai_generated_caption=f"Fresh news {idx}",
            created_at=now_utc,
            status=PublishJobStatus.pending_publish,
            is_breaking=False,
        )
        db_session.add(job_new)
        db_session.flush()

        deliveries = ensure_deliveries(db_session, job_new, now_utc=now_utc)
        assert len(deliveries) == 1
        deliv_new = deliveries[0]

        # New delivery must be skipped (no supersede occurred)
        assert deliv_new.state == "skipped"
        assert deliv_new.scheduled_for is None

        # Old delivery remains scheduled
        db_session.refresh(deliv_old)
        assert deliv_old.state == "scheduled"
        assert deliv_old.scheduled_for == slot_13

        # Clean up this case before next iteration
        db_session.delete(deliv_new)
        db_session.delete(deliv_old)
        db_session.delete(job_new)
        db_session.delete(job_old)
        db_session.commit()


def test_dispatch_gate_news_and_non_news(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    """4. Dispatch gate: slot in 2 h -> hold; slot in 30 min -> dispatched; 13 h old -> stale; non-news slot in 2 h -> dispatched."""
    monkeypatch.setattr("app.services.event_days.event_multiplier", lambda *args, **kwargs: 1)

    fanpage = TargetFanpage(name="FP Dispatch", repliz_account_id="rep_dispatch", is_active=True)
    db_session.add(fanpage)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Dest Dispatch",
        mode="repliz",
        repliz_account_id="rep_dispatch",
        timezone="UTC",
        daily_target=1,
        supported_media=["image", "video"],
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    now_utc = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    # 4a: Repliz news with slot in 2 h (14:00) -> hold, stays scheduled
    job_2h = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        created_at=now_utc - timedelta(hours=1),
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job_2h)
    db_session.flush()

    deliv_2h = PublishDelivery(
        publish_job_id=job_2h.id,
        destination_id=dest.id,
        state="scheduled",
        intended_method="repliz",
        scheduled_for=datetime(2026, 10, 15, 14, 0, 0),
        caption_snapshot="News 2h",
        media_snapshot=["https://cdn.example.com/2h.png"],
        media_kind="image",
    )
    db_session.add(deliv_2h)
    db_session.commit()

    mock_repliz = MagicMock()
    mock_repliz.create_image_schedule.side_effect = lambda **kw: {"id": "sched_2h"}

    processed = dispatch_due_deliveries(db_session, job_id=job_2h.id, now_utc=now_utc, repliz_client=mock_repliz)
    assert len(processed) == 0
    db_session.refresh(deliv_2h)
    assert deliv_2h.state == "scheduled"
    mock_repliz.create_image_schedule.assert_not_called()

    # 4b: Repliz news with slot in 30 min (12:30) -> dispatched
    deliv_2h.scheduled_for = datetime(2026, 10, 15, 12, 30, 0)
    db_session.commit()

    processed_30m = dispatch_due_deliveries(db_session, job_id=job_2h.id, now_utc=now_utc, repliz_client=mock_repliz)
    assert len(processed_30m) == 1
    db_session.refresh(deliv_2h)
    assert deliv_2h.state == "repliz_scheduled"
    assert mock_repliz.create_image_schedule.call_count == 1

    # 4c: News created 13 h ago on a normal day -> skipped stale: news too old to post at dispatch
    job_old = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        created_at=now_utc - timedelta(hours=13),
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job_old)
    db_session.flush()

    deliv_old = PublishDelivery(
        publish_job_id=job_old.id,
        destination_id=dest.id,
        state="scheduled",
        intended_method="repliz",
        scheduled_for=datetime(2026, 10, 15, 12, 10, 0),
        caption_snapshot="Old news",
        media_snapshot=["https://cdn.example.com/old.png"],
        media_kind="image",
        attempt_count=0,
    )
    db_session.add(deliv_old)
    db_session.commit()

    processed_old = dispatch_due_deliveries(db_session, job_id=job_old.id, now_utc=now_utc, repliz_client=mock_repliz)
    assert len(processed_old) == 1
    db_session.refresh(deliv_old)
    assert deliv_old.state == "skipped"
    assert deliv_old.last_error == "stale: news too old to post at dispatch"
    assert deliv_old.scheduled_for is None
    db_session.refresh(job_old)
    assert job_old.status == PublishJobStatus.skipped
    assert job_old.last_error == deliv_old.last_error

    # 4d: Non-news (ai_video) with slot in 2 h -> still dispatched ahead
    job_video = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.ai_video,
        created_at=now_utc - timedelta(hours=1),
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job_video)
    db_session.flush()

    deliv_video = PublishDelivery(
        publish_job_id=job_video.id,
        destination_id=dest.id,
        state="scheduled",
        intended_method="repliz",
        scheduled_for=datetime(2026, 10, 15, 14, 0, 0),
        caption_snapshot="Video 2h",
        media_snapshot=["https://cdn.example.com/video.mp4"],
        media_kind="video",
    )
    db_session.add(deliv_video)
    db_session.commit()

    mock_repliz.create_video_schedule.side_effect = lambda **kw: {"id": "sched_video"}
    processed_video = dispatch_due_deliveries(db_session, job_id=job_video.id, now_utc=now_utc, repliz_client=mock_repliz)
    assert len(processed_video) == 1
    db_session.refresh(deliv_video)
    assert deliv_video.state == "repliz_scheduled"


def test_visual_queue_freshness(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    """5. Visual queue: news job 7 h old on an event day -> skipped; 5 h old -> still a candidate."""
    monkeypatch.setattr("app.services.event_days.event_multiplier", lambda *args, **kwargs: 2)

    fanpage = TargetFanpage(
        id=100,
        name="FP Visual",
        mode2_publish_mode=PublishMode.auto,
        is_active=True,
    )
    db_session.add(fanpage)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Dest Visual",
        mode="repliz",
        timezone="Asia/Jakarta",
        daily_target=3,
        news_stale_hours=24,
        supported_media=["image"],
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    now_utc = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    # Job A: 7 h old on event day (cap = 6 h)
    job_7h = PublishJob(
        id=1001,
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        created_at=datetime(2026, 10, 15, 5, 0, 0),  # naive UTC, 7 h old
        status=PublishJobStatus.pending_design,
        is_breaking=False,
    )
    # Job B: 5 h old on event day (cap = 6 h)
    job_5h = PublishJob(
        id=1002,
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        created_at=datetime(2026, 10, 15, 7, 0, 0),  # naive UTC, 5 h old
        status=PublishJobStatus.pending_design,
        is_breaking=False,
    )
    db_session.add_all([job_7h, job_5h])
    db_session.commit()

    redis = FakeRedis()

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._next_slot", return_value=now_utc + timedelta(minutes=20)), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked = pick_next_news_job(db_session, redis, now_utc)

    # 7h job was skipped
    db_session.refresh(job_7h)
    assert job_7h.status == PublishJobStatus.skipped
    assert job_7h.last_error == "stale: news older than 6 h, not rendered"

    # 5h job was picked
    assert picked is not None
    assert picked.id == job_5h.id


def test_news_slot_available_with_supersede(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    """6. news_slot_available returns True when only a supersedable delivery makes a slot possible."""
    monkeypatch.setattr("app.services.event_days.event_multiplier", lambda *args, **kwargs: 1)

    fanpage = TargetFanpage(name="FP Available", is_active=True)
    db_session.add(fanpage)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Dest Available",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        supported_media=["image"],
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    now_utc = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    old_created = now_utc - timedelta(hours=3)

    job_old = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        created_at=old_created,
        status=PublishJobStatus.pending_publish,
        is_breaking=False,
    )
    db_session.add(job_old)
    db_session.flush()

    slot_13 = datetime(2026, 10, 15, 13, 0, 0)
    deliv_old = PublishDelivery(
        publish_job_id=job_old.id,
        destination_id=dest.id,
        state="scheduled",
        intended_method="repliz",
        scheduled_for=slot_13,
        caption_snapshot="Old",
        media_snapshot=["https://cdn.example.com/old.png"],
        media_kind="image",
        attempt_count=0,
    )
    db_session.add(deliv_old)
    db_session.commit()

    # New job
    job_new = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        created_at=now_utc,
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job_new)
    db_session.flush()

    # With supersedable delivery available, news_slot_available returns True
    assert news_slot_available(db_session, job_new, now_utc) is True

    # Also next_news_slot_utc should return slot_13 (aware UTC)
    next_slot = next_news_slot_utc(db_session, job_new, now_utc)
    assert next_slot == slot_13.replace(tzinfo=timezone.utc)

    # When old delivery is NOT supersedable (e.g. attempt_count=1)
    deliv_old.attempt_count = 1
    db_session.commit()
    assert news_slot_available(db_session, job_new, now_utc) is False


def test_stale_phone_news_with_pre_share_attempts_is_skipped(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    """A stale phone delivery whose earlier attempts stopped before SHARE is skipped, not left scheduled forever."""
    monkeypatch.setattr("app.services.event_days.event_multiplier", lambda *args, **kwargs: 1)
    fanpage = TargetFanpage(name="FP Phone Stale", is_active=True)
    db_session.add(fanpage)
    db_session.flush()
    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_profile",
        name="Dest Phone Stale",
        mode="phone",
        timezone="UTC",
        daily_target=1,
        supported_media=["image"],
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()
    now_utc = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    def _stale(method: str) -> PublishDelivery:
        job = PublishJob(
            fanpage_id=fanpage.id,
            content_type=ContentType.news_content,
            created_at=now_utc - timedelta(hours=13),
            status=PublishJobStatus.pending_publish,
        )
        db_session.add(job)
        db_session.flush()
        deliv = PublishDelivery(
            publish_job_id=job.id,
            destination_id=dest.id,
            state="scheduled",
            intended_method=method,
            scheduled_for=datetime(2026, 10, 15, 11, 0, 0),
            caption_snapshot="Old news",
            media_snapshot=["https://cdn.example.com/old.png"],
            media_kind="image",
            attempt_count=2,
        )
        db_session.add(deliv)
        db_session.commit()
        return deliv

    phone = _stale("phone")
    repliz = _stale("repliz")

    dispatch_due_deliveries(db_session, job_id=phone.publish_job_id, now_utc=now_utc)
    dispatch_due_deliveries(db_session, job_id=repliz.publish_job_id, now_utc=now_utc, repliz_client=MagicMock())
    db_session.refresh(phone)
    db_session.refresh(repliz)

    assert phone.state == "skipped"
    assert phone.last_error == "stale: news too old to post at dispatch"
    assert repliz.state == "scheduled"  # a Repliz attempt may have reached Repliz: keep the strict rule
