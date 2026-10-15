"""Tests for social delivery planner (S1a).

Verifies immutable delivery snapshots, local-day slot allocation, method quotas,
row idempotency, timezone rollover/min gap, media capabilities, absent media
rejection, privacy (no plaintext caption in events), and conflicting creation attempts.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session

# Allow SQLite to adapt list objects and compile PostgreSQL ARRAY and JSONB types in memory
sqlite3.register_adapter(list, json.dumps)
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"

from app.models.posts import MediaType, Post, PostStatus
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.services.social_delivery_planner import (
    DeliveryPayload,
    build_delivery_payload,
    compute_caption_hash,
    compute_media_hash,
    ensure_deliveries,
)


@pytest.fixture
def db_session():
    """Create clean in-memory SQLite schema and session."""
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
        session.rollback()
        session.close()


def test_hash_helpers_canonical():
    """Verify caption and media hashes use canonical UTF-8 and JSON."""
    c_hash1 = compute_caption_hash("Hello World")
    c_hash2 = compute_caption_hash("Hello World")
    assert c_hash1 == c_hash2
    assert len(c_hash1) == 64

    # Canonical media hash is key-order independent and compact
    m_hash1 = compute_media_hash(["https://a.com/1.jpg", "https://b.com/2.jpg"])
    m_hash2 = compute_media_hash(["https://a.com/1.jpg", "https://b.com/2.jpg"])
    assert m_hash1 == m_hash2
    assert len(m_hash1) == 64


def test_build_delivery_payload_ig_repost_album_and_single(db_session: Session):
    """Verify build_delivery_payload handles watermarked images and single/album detection."""
    # Album post with 2 images
    post = Post(
        ig_source_id=1,
        ig_media_id="ig_123",
        media_type=MediaType.album,
        image_public_urls=["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"],
        original_caption="IG caption original",
    )
    db_session.add(post)
    db_session.flush()

    job_album = PublishJob(
        fanpage_id=1,
        post_id=post.id,
        content_type=ContentType.ig_repost,
        ai_generated_caption="AI rewritten caption",
        watermarked_image_urls=["https://cdn.example.com/wm1.jpg", "https://cdn.example.com/wm2.jpg"],
    )
    payload = build_delivery_payload(job_album, post=post)
    assert payload.caption == "AI rewritten caption"
    assert payload.media_kind == "album"
    assert payload.media_refs == ("https://cdn.example.com/wm1.jpg", "https://cdn.example.com/wm2.jpg")
    assert payload.content_type == "ig_repost"

    # Single image fallback to post public URLs
    job_single = PublishJob(
        fanpage_id=1,
        post_id=post.id,
        content_type=ContentType.ig_repost,
        watermarked_image_urls=None,
    )
    post.media_type = MediaType.image
    post.image_public_urls = ["https://cdn.example.com/single.jpg"]
    payload_single = build_delivery_payload(job_single, post=post)
    assert payload_single.caption == "IG caption original"
    assert payload_single.media_kind == "image"
    assert payload_single.media_refs == ("https://cdn.example.com/single.jpg",)


def test_build_delivery_payload_video_and_design(db_session: Session):
    """Verify video and design content types extract proper media and kind."""
    job_video = PublishJob(
        fanpage_id=1,
        content_type=ContentType.youtube_clip,
        video_url="https://cdn.example.com/clip.mp4",
        design_title="Awesome Reel",
        ai_generated_caption="Check this clip",
    )
    payload_vid = build_delivery_payload(job_video)
    assert payload_vid.media_kind == "video"
    assert payload_vid.media_refs == ("https://cdn.example.com/clip.mp4",)
    assert payload_vid.title == "Awesome Reel"

    job_design = PublishJob(
        fanpage_id=1,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/news.png",
        design_title="Breaking Headline",
        ai_generated_caption="News summary",
    )
    payload_design = build_delivery_payload(job_design)
    assert payload_design.media_kind == "image"
    assert payload_design.media_refs == ("https://cdn.example.com/news.png",)
    assert payload_design.title == "Breaking Headline"
    assert payload_design.alt == "Breaking Headline"


def test_one_content_item_going_to_profile_and_page(db_session: Session):
    """Verify one PublishJob feeds both Profile and Page with distinct methods and frozen payload."""
    project = TargetFanpage(name="Dual Project", repliz_account_id="repliz_p1", is_active=True)
    db_session.add(project)
    db_session.flush()

    profile_dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_profile",
        name="Main Profile",
        mode="phone",
        timezone="UTC",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        supported_media=["image", "album", "video"],
        is_active=True,
    )
    page_dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Official Page",
        mode="repliz",
        timezone="UTC",
        daily_target=2,
        phone_daily_target=0,
        repliz_daily_target=2,
        supported_media=["image", "album", "video"],
        repliz_account_id="rep_page_1",
        is_active=True,
    )
    db_session.add_all([profile_dest, page_dest])
    db_session.flush()

    job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/breaking.png",
        design_title="Big Announcement",
        ai_generated_caption="Announcement body text",
    )
    db_session.add(job)
    db_session.flush()

    now_fixed = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    deliveries = ensure_deliveries(db_session, job, now_utc=now_fixed)

    assert len(deliveries) == 2

    # Check profile delivery
    prof_d = next(d for d in deliveries if d.destination_id == profile_dest.id)
    assert prof_d.intended_method == "phone"
    assert prof_d.state == "waiting_for_device"  # no device assigned yet
    assert prof_d.caption_snapshot == "Announcement body text"
    assert prof_d.media_snapshot == ["https://cdn.example.com/breaking.png"]
    assert prof_d.media_kind == "image"
    assert prof_d.content_type_snapshot == "news_content"
    assert prof_d.title_snapshot == "Big Announcement"
    assert prof_d.caption_sha256 == compute_caption_hash("Announcement body text")

    # Check page delivery
    page_d = next(d for d in deliveries if d.destination_id == page_dest.id)
    assert page_d.intended_method == "repliz"
    assert page_d.state == "scheduled"
    assert page_d.caption_snapshot == "Announcement body text"
    assert page_d.media_snapshot == ["https://cdn.example.com/breaking.png"]
    assert page_d.media_kind == "image"

    # Verify DeliveryEvent recorded without plaintext caption in details
    events = db_session.query(DeliveryEvent).all()
    assert len(events) == 2
    for ev in events:
        assert ev.event_type == "planned"
        assert "Announcement body text" not in str(ev.details)
        assert ev.details["caption_sha256"] == prof_d.caption_sha256


def test_separate_page_method_quotas(db_session: Session):
    """Verify mixed mode Page alternates between Phone and Repliz according to local day quotas."""
    project = TargetFanpage(name="Mixed Project", repliz_account_id="rep_mixed", is_active=True)
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Mixed Page",
        mode="mixed",
        timezone="UTC",
        daily_target=3,
        phone_daily_target=1,
        repliz_daily_target=2,
        min_gap_minutes=10,
        supported_media=["image", "album", "video"],
        repliz_account_id="rep_mixed_dest",
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    # Assign an available device so phone method can be 'scheduled'
    device = PhoneDevice(name="Test Phone", status="available")
    db_session.add(device)
    db_session.flush()
    assignment = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=device.id,
        priority=0,
        is_active=True,
    )
    db_session.add(assignment)
    db_session.flush()

    # Create 3 jobs for the same local day
    now_utc = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
    jobs = []
    for i in range(3):
        job = PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.news_content,
            design_image_url=f"https://cdn.example.com/img_{i}.png",
            ai_generated_caption=f"Post number {i}",
        )
        db_session.add(job)
        jobs.append(job)
    db_session.flush()

    # Plan deliveries sequentially
    d1 = ensure_deliveries(db_session, jobs[0], now_utc=now_utc)[0]
    d2 = ensure_deliveries(db_session, jobs[1], now_utc=now_utc)[0]
    d3 = ensure_deliveries(db_session, jobs[2], now_utc=now_utc)[0]

    # Slot 0 on day: phone (quota 1)
    assert d1.intended_method == "phone"
    assert d1.state == "scheduled"
    assert d1.device_id == device.id

    # Slot 1 on day: repliz (quota 2)
    assert d2.intended_method == "repliz"
    assert d2.state == "scheduled"
    assert d2.device_id is None

    # Slot 2 on day: repliz (quota 2)
    assert d3.intended_method == "repliz"
    assert d3.state == "scheduled"


def test_existing_row_idempotency(db_session: Session):
    """Verify calling ensure_deliveries on the same job returns existing rows unchanged."""
    project = TargetFanpage(name="Idempotent Project", repliz_account_id="rep_idem", is_active=True)
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Page 1",
        mode="repliz",
        timezone="UTC",
        daily_target=2,
        phone_daily_target=0,
        repliz_daily_target=2,
        supported_media=["image", "album", "video"],
        repliz_account_id="rep_page_idem",
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/post.png",
        ai_generated_caption="Idempotent test",
    )
    db_session.add(job)
    db_session.flush()

    first_run = ensure_deliveries(db_session, job)
    assert len(first_run) == 1
    d_id = first_run[0].id

    # Second call returns existing rows
    second_run = ensure_deliveries(db_session, job)
    assert len(second_run) == 1
    assert second_run[0].id == d_id

    # Total rows in DB remain 1
    total_deliveries = db_session.query(PublishDelivery).count()
    assert total_deliveries == 1


def test_timezone_rollover_and_min_gap(db_session: Session):
    """Verify destination timezone rollover and min_gap spacing are enforced."""
    project = TargetFanpage(name="TZ Project", repliz_account_id="rep_tz", is_active=True)
    db_session.add(project)
    db_session.flush()

    # Destination in Tokyo (UTC+9), daily target 1, min gap 60 min
    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Tokyo Page",
        mode="repliz",
        timezone="Asia/Tokyo",
        daily_target=1,
        phone_daily_target=0,
        repliz_daily_target=1,
        min_gap_minutes=60,
        supported_media=["image"],
        repliz_account_id="rep_tokyo",
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    # 2026-10-15 01:00 UTC = 2026-10-15 10:00 JST
    t0 = datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc)
    job1 = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/1.png",
    )
    job2 = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/2.png",
    )
    db_session.add_all([job1, job2])
    db_session.flush()

    d1 = ensure_deliveries(db_session, job1, now_utc=t0)[0]
    # First delivery scheduled at 01:00 UTC
    assert d1.scheduled_for == datetime(2026, 10, 15, 1, 0)

    # Second delivery with same now_utc cannot fit on Oct 15 in Tokyo (target 1/day reached)
    # Must roll over to next calendar day in Tokyo: 2026-10-16 00:00 JST = 2026-10-15 15:00 UTC
    d2 = ensure_deliveries(db_session, job2, now_utc=t0)[0]
    assert d2.scheduled_for == datetime(2026, 10, 15, 15, 0)


def test_planner_cap_rollover_honors_sleep_window(db_session: Session):
    """Verify daily cap rollover lands after the sleep window rather than midnight."""
    project = TargetFanpage(
        name="Sleep Project",
        repliz_account_id="rep_sleep",
        is_active=True,
        publish_sleep_start_hour=23,
        publish_sleep_end_hour=7,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="UTC Page",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        phone_daily_target=0,
        repliz_daily_target=1,
        min_gap_minutes=60,
        supported_media=["image"],
        repliz_account_id="rep_sleep_page",
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    t0 = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    job1 = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/1.png",
    )
    job2 = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/2.png",
    )
    db_session.add_all([job1, job2])
    db_session.flush()

    d1 = ensure_deliveries(db_session, job1, now_utc=t0)[0]
    assert d1.scheduled_for == datetime(2026, 10, 15, 12, 0)

    # Job 2 cannot fit on Oct 15 (daily target 1 reached).
    # Normal rollover lands at 00:00 midnight; with sleep window [23..7), it lands at 07:00!
    d2 = ensure_deliveries(db_session, job2, now_utc=t0)[0]
    assert d2.scheduled_for == datetime(2026, 10, 16, 7, 0)


def test_immutable_snapshots_after_job_edit(db_session: Session):
    """Verify delivery snapshots are frozen and cannot be mutated by subsequent job edits."""
    project = TargetFanpage(name="Snapshot Project", repliz_account_id="rep_snap", is_active=True)
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Page Snap",
        mode="repliz",
        timezone="UTC",
        daily_target=5,
        phone_daily_target=0,
        repliz_daily_target=5,
        supported_media=["image"],
        repliz_account_id="rep_snap_page",
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/original.png",
        design_title="Original Headline",
        ai_generated_caption="Original caption text",
    )
    db_session.add(job)
    db_session.flush()

    deliveries = ensure_deliveries(db_session, job)
    d = deliveries[0]
    orig_caption = d.caption_snapshot
    orig_media = list(d.media_snapshot)
    orig_caption_hash = d.caption_sha256

    # Mutate the job fields in database
    job.ai_generated_caption = "COMPLETELY CHANGED CAPTION"
    job.design_image_url = "https://cdn.example.com/tampered.png"
    job.design_title = "Changed Title"
    db_session.commit()

    # Re-running ensure_deliveries must not mutate delivery snapshot
    deliveries_recheck = ensure_deliveries(db_session, job)
    d_after = deliveries_recheck[0]

    assert d_after.caption_snapshot == "Original caption text"
    assert d_after.caption_snapshot == orig_caption
    assert d_after.media_snapshot == ["https://cdn.example.com/original.png"]
    assert d_after.media_snapshot == orig_media
    assert d_after.caption_sha256 == orig_caption_hash
    assert d_after.title_snapshot == "Original Headline"


def test_no_media_rejected(db_session: Session):
    """Verify build_delivery_payload and ensure_deliveries reject absent media."""
    project = TargetFanpage(name="No Media Project", repliz_account_id="rep_nomedia", is_active=True)
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Page",
        mode="repliz",
        supported_media=["image"],
        repliz_account_id="rep_nomedia_p",
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    # Job with no design image
    job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        design_image_url=None,
        design_image_path=None,
        ai_generated_caption="Caption with no media",
    )
    db_session.add(job)
    db_session.flush()

    with pytest.raises(ValueError, match="absent media"):
        build_delivery_payload(job)

    with pytest.raises(ValueError, match="absent media"):
        ensure_deliveries(db_session, job)


def test_unsupported_media_kept_waiting(db_session: Session):
    """Verify unsupported media keeps delivery waiting with reason and never routes to Repliz."""
    project = TargetFanpage(name="Filter Project", repliz_account_id="rep_filt", is_active=True)
    db_session.add(project)
    db_session.flush()

    # Destination only supports images
    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Images Only Page",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        phone_daily_target=0,
        repliz_daily_target=1,
        supported_media=["image"],
        repliz_account_id="rep_filt_p",
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    # Video job
    job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.youtube_clip,
        video_url="https://cdn.example.com/clip.mp4",
        ai_generated_caption="Video post",
    )
    db_session.add(job)
    db_session.flush()

    deliveries = ensure_deliveries(db_session, job)
    assert len(deliveries) == 1
    d = deliveries[0]

    # Stays waiting with reason, NOT scheduled
    assert d.state == "waiting_for_slot"
    assert "Unsupported media kind 'video'" in (d.last_error or "")
    assert d.media_kind == "video"
    assert d.intended_method == "repliz"


def test_two_conflicting_creation_attempts(db_session: Session):
    """Verify unique constraint and savepoint recover cleanly on concurrent creation attempts."""
    project = TargetFanpage(name="Conflict Project", repliz_account_id="rep_conf", is_active=True)
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Page Conf",
        mode="repliz",
        supported_media=["image"],
        repliz_account_id="rep_conf_p",
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/img.png",
        ai_generated_caption="Test",
    )
    db_session.add(job)
    db_session.flush()

    # Create delivery first
    d1 = ensure_deliveries(db_session, job)[0]

    # Re-calling ensure_deliveries succeeds idempotently
    d2 = ensure_deliveries(db_session, job)[0]
    assert d1.id == d2.id


def test_concurrent_integrity_error_recovery(db_session: Session):
    """Verify savepoint handles concurrent commit conflict and recovers existing row."""
    project = TargetFanpage(name="Race Project", repliz_account_id="rep_race", is_active=True)
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Page Race",
        mode="repliz",
        supported_media=["image"],
        repliz_account_id="rep_race_p",
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/img.png",
        ai_generated_caption="Race test",
    )
    db_session.add(job)
    db_session.flush()

    # Pre-insert delivery manually to simulate a concurrent transaction commit
    existing_del = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        state="scheduled",
        intended_method="repliz",
        caption_snapshot="Race test",
        media_snapshot=["https://cdn.example.com/img.png"],
        media_kind="image",
    )
    db_session.add(existing_del)
    db_session.flush()

    # Calling ensure_deliveries returns existing row without error
    deliveries = ensure_deliveries(db_session, job)
    assert len(deliveries) == 1
    assert deliveries[0].id == existing_del.id
