"""Concurrency and race-safety regression suite for audience-aware delivery scheduling.

Covers:
1. Session 1 identity map refresh with .populate_existing() when concurrent Session 2 claims row.
2. Multiset timestamp reservation removal preserving duplicate-peer and claimed-peer slots.
3. Recent send detection without PG NULLS FIRST shadowing (scalar aggregate / coalesce / EXISTS).
4. Active immediate publishing delivery blocking regardless of min_gap elapsed.
5. Future Repliz pre-schedule published_at excluded from live-send guard (not masking past sends).
6. Immediate Repliz accepted state included in live-send minGap guard.
7. Two immediate concurrent claims serialized under destination lock (exactly 1 acquired).
8. Due Phone delivery fires while future Phone delivery gets zero runner calls.
9. Future Repliz pre-scheduling allows full quota (1 Phone + 5 Repliz accepted).
10. Stale delivery replan produces stable future slots with no repeated rescheduling.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
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
from app.models.target_fanpages import TargetFanpage
from app.services.social_delivery_dispatch import (
    PhonePublishResult,
    claim_delivery_for_dispatch,
    claim_paced_delivery_for_dispatch,
    dispatch_due_deliveries,
    replan_recovering_phone_deliveries,
    replan_stale_scheduled_deliveries,
)


@pytest.fixture
def concurrency_db_factory():
    """Factory yielding pairs of independent sessions backed by a shared in-memory SQLite DB."""
    SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
    SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"

    sd_col = SocialDestination.__table__.columns["supported_media"]
    orig_sd = sd_col.server_default
    sd_col.server_default = None

    de_col = DeliveryEvent.__table__.columns["details"]
    orig_de = de_col.server_default
    de_col.server_default = None

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TargetFanpage.__table__.create(engine)
    SocialDestination.__table__.create(engine)
    PhoneDevice.__table__.create(engine)
    DestinationDeviceAssignment.__table__.create(engine)
    DeviceLease.__table__.create(engine)
    RecoveryIncident.__table__.create(engine)
    OperationalNotification.__table__.create(engine)
    Post.__table__.create(engine)
    PublishJob.__table__.create(engine)
    PublishDelivery.__table__.create(engine)
    DeliveryEvent.__table__.create(engine)

    SessionFactory = sessionmaker(bind=engine)

    sessions = []

    def make_session():
        s = SessionFactory()
        sessions.append(s)
        return s

    try:
        yield make_session
    finally:
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass
        engine.dispose()
        sd_col.server_default = orig_sd
        de_col.server_default = orig_de


def _setup_destination_and_fanpage(db, dest_id=1, name="Test Dest", mode="mixed", min_gap=30, daily_target=6):
    fanpage = TargetFanpage(
        id=dest_id,
        name=f"Fanpage-{dest_id}",
        target_country="AU",
        publish_sleep_start_hour=22,
        publish_sleep_end_hour=8,
    )
    db.add(fanpage)
    db.flush()

    parent_prof = db.query(SocialDestination).filter_by(id=9999).first()
    if not parent_prof:
        parent_prof = SocialDestination(
            id=9999,
            content_project_id=fanpage.id,
            kind="facebook_profile",
            name="Parent Profile",
            facebook_actor_id="parent_actor_1",
            mode="phone",
            timezone="Australia/Sydney",
            daily_target=1,
            phone_daily_target=1,
            repliz_daily_target=0,
            min_gap_minutes=15,
            supported_media=["image", "album", "video"],
            is_active=True,
        )
        db.add(parent_prof)
        db.flush()

    dest = SocialDestination(
        id=dest_id,
        content_project_id=fanpage.id,
        kind="facebook_page",
        name=name,
        facebook_actor_id=f"page_actor_{dest_id}",
        parent_profile_id=parent_prof.id,
        repliz_account_id=f"repliz-acc-{dest_id}",
        mode=mode,
        timezone="Australia/Sydney",
        daily_target=daily_target,
        phone_daily_target=1 if mode == "mixed" else (daily_target if mode == "phone_only" else 0),
        repliz_daily_target=daily_target - 1 if mode == "mixed" else (daily_target if mode == "repliz_only" else 0),
        min_gap_minutes=min_gap,
        supported_media=["image", "album", "video"],
        is_active=True,
    )
    db.add(dest)
    db.flush()

    dev = db.query(PhoneDevice).filter_by(id=dest_id).first()
    if not dev:
        dev = PhoneDevice(
            id=dest_id,
            name=f"Phone-{dest_id}",
            adb_serial=f"serial-{dest_id}",
            expected_profile_actor_id="parent_actor_1",
            status="available",
            calibration_verified_at=datetime(2026, 1, 1, 0, 0, 0),
        )
        db.add(dev)
        db.flush()

    assign = (
        db.query(DestinationDeviceAssignment)
        .filter_by(destination_id=dest.id, device_id=dev.id)
        .first()
    )
    if not assign:
        assign = DestinationDeviceAssignment(
            destination_id=dest.id,
            device_id=dev.id,
            priority=0,
            is_active=True,
            verified_profile_actor_id="parent_actor_1",
            verified_target_actor_id=f"page_actor_{dest_id}",
            verified_at=datetime(2026, 1, 1, 0, 0, 0),
        )
        db.add(assign)
        db.flush()

    return fanpage, dest


def _create_delivery(
    db,
    dest_id=1,
    job_id=100,
    deliv_id=1000,
    method="phone",
    state="scheduled",
    scheduled_for=None,
    published_at=None,
    claimed_at=None,
    claim_token=None,
    attempt_count=0,
    media_kind="image",
):
    job = db.query(PublishJob).filter_by(id=job_id).first()
    if not job:
        job = PublishJob(
            id=job_id,
            fanpage_id=dest_id,
            status=PublishJobStatus.pending_publish,
            content_type=ContentType.news_content,
            scheduled_for=scheduled_for,
        )
        db.add(job)
        db.flush()

    deliv = PublishDelivery(
        id=deliv_id,
        publish_job_id=job_id,
        destination_id=dest_id,
        intended_method=method,
        actual_method=None,
        state=state,
        scheduled_for=scheduled_for,
        published_at=published_at,
        claimed_at=claimed_at,
        claim_token=claim_token,
        attempt_count=attempt_count,
        media_kind=media_kind,
        media_snapshot=["https://example.com/img.jpg"],
        caption_snapshot="Post Caption",
    )
    db.add(deliv)
    db.flush()
    return job, deliv


# ── 1. Stale Replan Identity Map Refresh (.populate_existing()) ───────────────


def test_stale_replan_populate_existing_refreshes_concurrent_session_claim(concurrency_db_factory):
    """Session 1 initially loads scheduled row; Session 2 marks it publishing and commits.

    Session 1 stale loaded object must NOT be rescheduled or changed.
    Genuine untouched peer delivery still moves to a valid future slot, and its parent
    PublishJob.scheduled_for is synchronized.
    """
    s1 = concurrency_db_factory()
    s2 = concurrency_db_factory()

    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    stale_time = (now - timedelta(minutes=25)).replace(tzinfo=None)

    _setup_destination_and_fanpage(s1, dest_id=1)
    j1, d1 = _create_delivery(s1, dest_id=1, job_id=101, deliv_id=1001, scheduled_for=stale_time)
    j2, d2 = _create_delivery(s1, dest_id=1, job_id=102, deliv_id=1002, scheduled_for=stale_time)
    s1.commit()

    # Session 1 pre-loads deliveries into its ORM identity map
    preloaded = s1.query(PublishDelivery).order_by(PublishDelivery.id.asc()).all()
    assert len(preloaded) == 2
    assert preloaded[0].state == "scheduled"
    assert preloaded[0].claim_token is None

    from sqlalchemy import event

    # Hook engine: right before Session 1 locks destination, Session 2 claims d1
    def before_cursor_exec(conn, cursor, statement, parameters, context, executemany):
        if "social_destinations" in statement.lower():
            d1_s2 = s2.query(PublishDelivery).filter_by(id=1001).first()
            if d1_s2 and d1_s2.state == "scheduled":
                d1_s2.state = "publishing"
                d1_s2.claim_token = "w2-token-xyz"
                d1_s2.attempt_count = 1
                d1_s2.claimed_at = (now - timedelta(minutes=1)).replace(tzinfo=None)
                s2.commit()

    engine = s1.get_bind()
    event.listen(engine, "before_cursor_execute", before_cursor_exec)

    try:
        # Session 1 executes replan_stale_scheduled_deliveries
        replanned = replan_stale_scheduled_deliveries(s1, now_utc=now)
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_exec)

    # Only genuine untouched d2 should be replanned
    replanned_ids = [d.id for d in replanned]
    assert 1001 not in replanned_ids, "Claimed d1 must not be replanned by stale worker"
    assert 1002 in replanned_ids, "Genuine untouched d2 must be replanned"

    # d1 must remain in its publishing state with original scheduled_for
    d1_fresh = s1.query(PublishDelivery).filter_by(id=1001).first()
    assert d1_fresh.state == "publishing"
    assert d1_fresh.claim_token == "w2-token-xyz"
    assert d1_fresh.attempt_count == 1
    assert d1_fresh.scheduled_for == stale_time

    # j1 scheduled_for must remain unchanged
    j1_fresh = s1.query(PublishJob).filter_by(id=101).first()
    assert j1_fresh.scheduled_for == stale_time

    # d2 must be moved to valid future slot
    d2_fresh = s1.query(PublishDelivery).filter_by(id=1002).first()
    assert d2_fresh.scheduled_for is not None
    assert d2_fresh.scheduled_for > now.replace(tzinfo=None)

    # j2 scheduled_for must be synchronized with d2
    j2_fresh = s1.query(PublishJob).filter_by(id=102).first()
    assert j2_fresh.scheduled_for == d2_fresh.scheduled_for


# ── 2. Multiset Removal Preserves Duplicate-Peer Reservations ─────────────────


def test_stale_replan_multiset_preserves_duplicate_timestamp_reservations(concurrency_db_factory):
    """When multiple peers share the exact same scheduled timestamp, replanning one

    must not erase the other's reservation from existing_slots_utc.
    """
    s1 = concurrency_db_factory()
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    stale_time = (now - timedelta(minutes=30)).replace(tzinfo=None)

    _setup_destination_and_fanpage(s1, dest_id=1, min_gap=20)
    # d1: stale untouched
    j1, d1 = _create_delivery(s1, dest_id=1, job_id=201, deliv_id=2001, scheduled_for=stale_time)
    # d2: claimed peer at identical timestamp
    j2, d2 = _create_delivery(
        s1,
        dest_id=1,
        job_id=202,
        deliv_id=2002,
        scheduled_for=stale_time,
        state="publishing",
        claim_token="active-worker-tok",
    )
    s1.commit()

    replanned = replan_stale_scheduled_deliveries(s1, now_utc=now)
    assert len(replanned) == 1
    assert replanned[0].id == 2001

    # d2 should remain unchanged with its reservation intact
    d2_fresh = s1.query(PublishDelivery).filter_by(id=2002).first()
    assert d2_fresh.state == "publishing"
    assert d2_fresh.scheduled_for == stale_time


def test_recovering_phone_multiset_preserves_duplicate_timestamp_reservations(concurrency_db_factory):
    """In replan_recovering_phone_deliveries, multiset removal must only remove one

    reservation per waiting delivery, preserving peer reservations at the same timestamp.
    """
    s1 = concurrency_db_factory()
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    same_time = (now + timedelta(hours=2)).replace(tzinfo=None)

    _setup_destination_and_fanpage(s1, dest_id=1, min_gap=20)

    # d1: waiting for device at same_time
    j1, d1 = _create_delivery(
        s1,
        dest_id=1,
        job_id=211,
        deliv_id=2011,
        state="waiting_for_device",
        method="phone",
        scheduled_for=same_time,
    )
    # d2: scheduled peer at identical timestamp
    j2, d2 = _create_delivery(
        s1,
        dest_id=1,
        job_id=212,
        deliv_id=2012,
        state="scheduled",
        method="phone",
        scheduled_for=same_time,
    )
    s1.commit()

    replanned = replan_recovering_phone_deliveries(s1, now_utc=now)
    assert len(replanned) == 1
    assert replanned[0].id == 2011

    # d2 must not have been modified
    d2_fresh = s1.query(PublishDelivery).filter_by(id=2012).first()
    assert d2_fresh.state == "scheduled"
    assert d2_fresh.scheduled_for == same_time


# ── 3. Recent-Send PG NULLS FIRST Guard Regression ────────────────────────────


def test_recent_peer_null_published_does_not_mask_recent_published(concurrency_db_factory):
    """Peer with published_at IS NULL (e.g. old publishing row) must NOT shadow

    a recent published row under min_gap guard.
    """
    s1 = concurrency_db_factory()
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    _setup_destination_and_fanpage(s1, dest_id=1, min_gap=30)
    # Peer 1: old publishing row without published_at (claimed 2 hours ago)
    _create_delivery(
        s1,
        dest_id=1,
        job_id=301,
        deliv_id=3001,
        state="publishing",
        method="phone",
        claimed_at=naive_now - timedelta(hours=2),
        published_at=None,
    )
    # Peer 2: published 5 minutes ago
    _create_delivery(
        s1,
        dest_id=1,
        job_id=302,
        deliv_id=3002,
        state="published",
        method="phone",
        published_at=naive_now - timedelta(minutes=5),
    )
    # Candidate: due now
    j3, d3 = _create_delivery(
        s1,
        dest_id=1,
        job_id=303,
        deliv_id=3003,
        state="scheduled",
        method="phone",
        scheduled_for=naive_now,
    )
    s1.commit()

    claimed_deliv, token = claim_paced_delivery_for_dispatch(s1, d3.id, now_utc=now)
    assert claimed_deliv is None, "Candidate must be blocked because Peer 2 published 5m ago (< 30m minGap)"
    assert token is None


# ── 4. Active Immediate Publishing Blocks Regardless of min_gap ───────────────


def test_active_immediate_publishing_blocks_regardless_of_mingap(concurrency_db_factory):
    """If another immediate Phone delivery is currently 'publishing', candidate must

    be blocked regardless of elapsed min_gap (in-progress lock safety).
    """
    s1 = concurrency_db_factory()
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    # Destination with min_gap = 10, but peer claimed 15m ago (min_gap elapsed)
    _setup_destination_and_fanpage(s1, dest_id=1, min_gap=10)
    # Active publishing delivery for the same destination (still in-progress)
    _create_delivery(
        s1,
        dest_id=1,
        job_id=401,
        deliv_id=4001,
        state="publishing",
        method="phone",
        claimed_at=naive_now - timedelta(minutes=15),
    )
    # Candidate delivery due now
    j2, d2 = _create_delivery(
        s1,
        dest_id=1,
        job_id=402,
        deliv_id=4002,
        state="scheduled",
        method="phone",
        scheduled_for=naive_now,
    )
    s1.commit()

    claimed_deliv, token = claim_paced_delivery_for_dispatch(s1, d2.id, now_utc=now)
    assert claimed_deliv is None, "Active publishing Phone delivery must block adjacent immediate claim"
    assert token is None


# ── 5. Future Repliz Pre-Schedule Does Not Block Immediate Phone ──────────────


def test_future_repliz_published_at_does_not_block_immediate_phone(concurrency_db_factory):
    """A Repliz delivery submitted ahead of time (published_at set to submission time,

    but scheduled_for in future) must NOT be misinterpreted as an actual live send.
    """
    s1 = concurrency_db_factory()
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    _setup_destination_and_fanpage(s1, dest_id=1, min_gap=30)
    # Repliz delivery pre-scheduled 5 minutes ago for tomorrow
    _create_delivery(
        s1,
        dest_id=1,
        job_id=501,
        deliv_id=5001,
        state="repliz_scheduled",
        method="repliz",
        published_at=naive_now - timedelta(minutes=5),
        scheduled_for=naive_now + timedelta(hours=18),
    )
    # Immediate Phone delivery due now
    j2, d2 = _create_delivery(
        s1,
        dest_id=1,
        job_id=502,
        deliv_id=5002,
        state="scheduled",
        method="phone",
        scheduled_for=naive_now,
    )
    s1.commit()

    claimed_deliv, token = claim_paced_delivery_for_dispatch(s1, d2.id, now_utc=now)
    assert claimed_deliv is not None, "Future Repliz pre-submission must not block immediate Phone delivery"
    assert token is not None
    assert claimed_deliv.state == "publishing"


# ── 6. Future Repliz Does Not Mask Recent Past Send ───────────────────────────


def test_future_repliz_does_not_mask_recent_past_send(concurrency_db_factory):
    """Presence of a future Repliz pre-schedule must NOT mask a genuine past live send."""
    s1 = concurrency_db_factory()
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    _setup_destination_and_fanpage(s1, dest_id=1, min_gap=30)
    # Genuine past live send: 10 minutes ago (< 30m minGap)
    _create_delivery(
        s1,
        dest_id=1,
        job_id=601,
        deliv_id=6001,
        state="published",
        method="phone",
        published_at=naive_now - timedelta(minutes=10),
    )
    # Future Repliz pre-schedule: 2 minutes ago
    _create_delivery(
        s1,
        dest_id=1,
        job_id=602,
        deliv_id=6002,
        state="repliz_scheduled",
        method="repliz",
        published_at=naive_now - timedelta(minutes=2),
        scheduled_for=naive_now + timedelta(hours=6),
    )
    # Candidate Phone delivery due now
    j3, d3 = _create_delivery(
        s1,
        dest_id=1,
        job_id=603,
        deliv_id=6003,
        state="scheduled",
        method="phone",
        scheduled_for=naive_now,
    )
    s1.commit()

    claimed_deliv, token = claim_paced_delivery_for_dispatch(s1, d3.id, now_utc=now)
    assert claimed_deliv is None, "Candidate must be blocked by the genuine past live send (10m < 30m)"
    assert token is None


# ── 7. Immediate Repliz Accepted Counts as Recent Live Send ───────────────────


def test_immediate_repliz_accepted_counts_as_recent_live_send(concurrency_db_factory):
    """An immediate Repliz delivery (scheduled_for <= now) accepted 5m ago DOES count

    as a recent live send and enforces min_gap.
    """
    s1 = concurrency_db_factory()
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    _setup_destination_and_fanpage(s1, dest_id=1, min_gap=30)
    # Immediate Repliz accepted 5 minutes ago for immediate execution
    _create_delivery(
        s1,
        dest_id=1,
        job_id=701,
        deliv_id=7001,
        state="repliz_scheduled",
        method="repliz",
        published_at=naive_now - timedelta(minutes=5),
        scheduled_for=naive_now - timedelta(minutes=5),
    )
    # Candidate Phone delivery due now
    j2, d2 = _create_delivery(
        s1,
        dest_id=1,
        job_id=702,
        deliv_id=7002,
        state="scheduled",
        method="phone",
        scheduled_for=naive_now,
    )
    s1.commit()

    claimed_deliv, token = claim_paced_delivery_for_dispatch(s1, d2.id, now_utc=now)
    assert claimed_deliv is None, "Candidate must be blocked by recent immediate Repliz go-live"
    assert token is None


# ── 8. Two Immediate Concurrent Claims Serialized Under Lock ──────────────────


def test_two_immediate_concurrent_claims_serialized_under_destination_lock(concurrency_db_factory):
    """When two immediate scheduled deliveries for the same destination are claimed,

    destination lock serializes them: exactly 1 claim is acquired, other is rejected.
    """
    s1 = concurrency_db_factory()
    s2 = concurrency_db_factory()
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    _setup_destination_and_fanpage(s1, dest_id=1, min_gap=30)
    j1, d1 = _create_delivery(s1, dest_id=1, job_id=801, deliv_id=8001, scheduled_for=naive_now, method="phone")
    j2, d2 = _create_delivery(s1, dest_id=1, job_id=802, deliv_id=8002, scheduled_for=naive_now, method="phone")
    s1.commit()

    # Session 1 claims d1
    claimed_1, tok_1 = claim_paced_delivery_for_dispatch(s1, d1.id, now_utc=now)
    assert claimed_1 is not None
    assert tok_1 is not None

    # Session 2 tries to claim d2 concurrently under destination pacing
    claimed_2, tok_2 = claim_paced_delivery_for_dispatch(s2, d2.id, now_utc=now)
    assert claimed_2 is None, "Session 2 must be blocked because d1 is publishing"
    assert tok_2 is None


# ── 9. Due First Phone Fires, Future Phone Zero ───────────────────────────────


def test_due_first_phone_fires_future_phone_zero(concurrency_db_factory):
    """Due Phone delivery dispatches immediately; future Phone delivery gets zero runner calls."""
    s1 = concurrency_db_factory()
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    _setup_destination_and_fanpage(s1, dest_id=1, min_gap=30)
    # d1: due now
    j1, d1 = _create_delivery(s1, dest_id=1, job_id=901, deliv_id=9001, scheduled_for=naive_now, method="phone")
    # d2: scheduled for 1 hour in the future
    j2, d2 = _create_delivery(
        s1,
        dest_id=1,
        job_id=902,
        deliv_id=9002,
        scheduled_for=naive_now + timedelta(hours=1),
        method="phone",
    )
    s1.commit()

    mock_runner = MagicMock()
    mock_runner.publish_delivery.return_value = PhonePublishResult(
        status="published",
        facebook_post_id="post-9001",
        actor_id="page_actor_1",
    )

    processed = dispatch_due_deliveries(s1, now_utc=now, runner_client=mock_runner)
    assert len(processed) == 1
    assert processed[0].id == 9001
    assert mock_runner.publish_delivery.call_count == 1

    d2_fresh = s1.query(PublishDelivery).filter_by(id=9002).first()
    assert d2_fresh.state == "scheduled"
    assert d2_fresh.claim_token is None


# ── 10. Future Repliz Pre-Submit Quota 1+5 Accepted ───────────────────────────


def test_future_repliz_presubmit_all_quota_accepted(concurrency_db_factory):
    """Mixed mode 1 Phone due now + 5 future Repliz deliveries all get accepted in dispatch sweep."""
    s1 = concurrency_db_factory()
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    _setup_destination_and_fanpage(s1, dest_id=1, mode="mixed", min_gap=30, daily_target=6)
    # 1 due Phone delivery
    _create_delivery(s1, dest_id=1, job_id=1001, deliv_id=10001, scheduled_for=naive_now, method="phone")

    # 5 future Repliz deliveries spread through the day
    for i in range(1, 6):
        _create_delivery(
            s1,
            dest_id=1,
            job_id=1001 + i,
            deliv_id=10001 + i,
            scheduled_for=naive_now + timedelta(hours=i * 2),
            method="repliz",
        )
    s1.commit()

    mock_runner = MagicMock()
    mock_runner.publish_delivery.return_value = PhonePublishResult(
        status="published",
        facebook_post_id="phone-post-1",
        actor_id="page_actor_1",
    )

    mock_repliz = MagicMock()
    mock_repliz.create_image_schedule.return_value = {"_id": "repliz-sched-ok"}

    processed = dispatch_due_deliveries(s1, now_utc=now, runner_client=mock_runner, repliz_client=mock_repliz)
    assert len(processed) == 6
    assert mock_runner.publish_delivery.call_count == 1
    assert mock_repliz.create_image_schedule.call_count == 5


# ── 11. Stale Delivery Replan Idempotent (No Repeated Reschedule) ──────────────


def test_no_repeated_reschedule_forever(concurrency_db_factory):
    """Overdue delivery replanned to future slot is NOT replanned again on immediate next tick."""
    s1 = concurrency_db_factory()
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    stale_time = (now - timedelta(minutes=20)).replace(tzinfo=None)

    _setup_destination_and_fanpage(s1, dest_id=1, min_gap=30)
    j1, d1 = _create_delivery(s1, dest_id=1, job_id=1101, deliv_id=11001, scheduled_for=stale_time)
    s1.commit()

    # First tick replans to future
    replanned_1 = replan_stale_scheduled_deliveries(s1, now_utc=now)
    assert len(replanned_1) == 1
    new_slot = replanned_1[0].scheduled_for
    assert new_slot > now.replace(tzinfo=None)

    # Immediate second tick with same now_utc
    replanned_2 = replan_stale_scheduled_deliveries(s1, now_utc=now)
    assert len(replanned_2) == 0, "Delivery already in future slot must not be repeatedly replanned"

    d1_fresh = s1.query(PublishDelivery).filter_by(id=11001).first()
    assert d1_fresh.scheduled_for == new_slot


# ── 12. Schema Daily Target Bounds Regressions ────────────────────────────────


def test_social_destination_create_bounds_daily_target_1000_1001():
    from pydantic import ValidationError
    from app.schemas.social_media import SocialDestinationCreate

    # 1000 accepted
    c_ok = SocialDestinationCreate(
        content_project_id=1,
        name="Valid 1000",
        kind="page",
        mode="phone",
        daily_target=1000,
        phone_daily_target=1000,
        repliz_daily_target=0,
    )
    assert c_ok.daily_target == 1000

    # 1001 rejected
    with pytest.raises(ValidationError):
        SocialDestinationCreate(
            content_project_id=1,
            name="Invalid 1001",
            kind="page",
            mode="phone",
            daily_target=1001,
            phone_daily_target=1001,
            repliz_daily_target=0,
        )


def test_social_destination_update_bounds_daily_target_1000_1001_and_none():
    from pydantic import ValidationError
    from app.schemas.social_media import SocialDestinationUpdate

    # 1000 accepted
    u_ok = SocialDestinationUpdate(daily_target=1000)
    assert u_ok.daily_target == 1000

    # None accepted (preserves explicit null semantics)
    u_none = SocialDestinationUpdate(daily_target=None)
    assert u_none.daily_target is None

    # 1001 rejected
    with pytest.raises(ValidationError):
        SocialDestinationUpdate(daily_target=1001)
