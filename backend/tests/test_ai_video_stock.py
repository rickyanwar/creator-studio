"""Tests for Mode 8 AI video stock logic, release horizon, and publisher hold rules.

Verifies:
1. Planner keeps creating jobs after made_today == daily_count while stock < daily_count * AI_VIDEO_STOCK_DAYS;
   stops when stock >= daily_count * AI_VIDEO_STOCK_DAYS.
2. Planner still stops when open_count >= 2.
3. publish_job on an ai_video job whose next slot is 3 days away leaves it pending_publish and creates no delivery.
4. publish_job on an ai_video job with a slot in 2 hours creates the delivery as before.
5. youtube_clip jobs are never held even when next slot is far away.
6. video_slot_soon returns True when the page has no active destination and on an exception.
7. video_stock helper correctly counts non-deleted pending_publish / pending_review ai_video PublishJobs.
8. recover_stuck_auto_publishes orders ai_video jobs by created_at ascending (oldest stock first).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from unittest.mock import patch
import pytest

from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session, sessionmaker

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"

from app.models.ai_video import VideoIdea, VideoJob
from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import PublishMode, TargetFanpage
import app.tasks.ai_video_plan as planner
from app.tasks.ai_video_plan import AI_VIDEO_STOCK_DAYS, plan_ai_videos, video_stock
from app.tasks.publisher import publish_job, recover_stuck_auto_publishes
from app.services.social_delivery_planner import (
    VIDEO_STOCK_RELEASE_HOURS,
    video_slot_soon,
)


@pytest.fixture
def db_setup():
    orig_sd = SocialDestination.__table__.columns["supported_media"].server_default
    orig_de = DeliveryEvent.__table__.columns["details"].server_default
    orig_vj = VideoJob.__table__.columns["clips_done"].server_default
    orig_pj = PublishJob.__table__.columns["is_deleted"].server_default

    SocialDestination.__table__.columns["supported_media"].server_default = None
    DeliveryEvent.__table__.columns["details"].server_default = None
    VideoJob.__table__.columns["clips_done"].server_default = None
    PublishJob.__table__.columns["is_deleted"].server_default = None

    engine = create_engine("sqlite:///:memory:")
    TargetFanpage.__table__.create(engine)
    SocialDestination.__table__.create(engine)
    PhoneDevice.__table__.create(engine)
    DestinationDeviceAssignment.__table__.create(engine)
    Post.__table__.create(engine)
    PublishJob.__table__.create(engine)
    PublishDelivery.__table__.create(engine)
    DeliveryEvent.__table__.create(engine)
    VideoIdea.__table__.create(engine)
    VideoJob.__table__.create(engine)

    SessionMaker = sessionmaker(bind=engine)
    session = SessionMaker()

    try:
        yield session, SessionMaker
    finally:
        session.rollback()
        session.close()
        SocialDestination.__table__.columns["supported_media"].server_default = orig_sd
        DeliveryEvent.__table__.columns["details"].server_default = orig_de
        VideoJob.__table__.columns["clips_done"].server_default = orig_vj
        PublishJob.__table__.columns["is_deleted"].server_default = orig_pj


def _mock_fill_result():
    return (
        {"part": "rear wing"},
        "Generated title",
        "Generated caption",
        [{"index": 1, "mode": "t2v", "prompt": "prompt"}],
        "mock",
    )


def test_planner_creates_job_when_made_today_reaches_daily_count_while_stock_below_cap(db_setup):
    session, SessionMaker = db_setup

    fp = TargetFanpage(
        repliz_account_id="repliz-stock-1",
        name="Stock FP 1",
        timezone="UTC",
        is_active=True,
        is_connected=True,
        ai_video_enabled=True,
        ai_video_daily_count=1,
        ai_video_min_score=None,
        ai_video_clips=3,
        ai_video_cover_enabled=False,
    )
    session.add(fp)
    session.flush()

    # 1 done job today -> made_today == 1 == ai_video_daily_count
    done_job = VideoJob(
        fanpage_id=fp.id,
        template_key="motorsport_tech_explainer",
        title="Done Video",
        caption="Done Caption",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[],
        credits_per_clip=15,
        credits_estimate=45,
        status="done",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(done_job)

    # Pre-populate stock with (AI_VIDEO_STOCK_DAYS - 1) = 19 jobs
    for _ in range(AI_VIDEO_STOCK_DAYS - 1):
        session.add(
            PublishJob(
                fanpage_id=fp.id,
                content_type=ContentType.ai_video,
                status=PublishJobStatus.pending_publish,
                is_deleted=False,
            )
        )

    idea1 = VideoIdea(
        fanpage_id=fp.id,
        template_key="motorsport_tech_explainer",
        topic="Stock Topic 1",
        status="approved",
        score=90,
        rationale="r",
    )
    session.add(idea1)
    session.commit()

    assert video_stock(session, fp.id) == AI_VIDEO_STOCK_DAYS - 1

    # Planner should create a job because stock (19) < daily_count * AI_VIDEO_STOCK_DAYS (20)
    with patch.object(planner, "SessionLocal", SessionMaker), patch.object(
        planner, "fill_idea", return_value=_mock_fill_result()
    ):
        plan_ai_videos.run()

    verify_s1 = SessionMaker()
    queued = verify_s1.query(VideoJob).filter(VideoJob.status == "queued").all()
    assert len(queued) == 1

    # Now add one more stock job so stock == daily_count * AI_VIDEO_STOCK_DAYS (20)
    verify_s1.query(VideoJob).filter(VideoJob.status == "queued").delete()
    verify_s1.add(
        PublishJob(
            fanpage_id=fp.id,
            content_type=ContentType.ai_video,
            status=PublishJobStatus.pending_publish,
            is_deleted=False,
        )
    )
    idea2 = VideoIdea(
        fanpage_id=fp.id,
        template_key="motorsport_tech_explainer",
        topic="Stock Topic 2",
        status="approved",
        score=95,
        rationale="r2",
    )
    verify_s1.add(idea2)
    verify_s1.commit()

    assert video_stock(verify_s1, fp.id) == AI_VIDEO_STOCK_DAYS

    # Planner should stop because stock >= daily_count * AI_VIDEO_STOCK_DAYS
    with patch.object(planner, "SessionLocal", SessionMaker), patch.object(
        planner, "fill_idea", return_value=_mock_fill_result()
    ):
        plan_ai_videos.run()

    verify_s2 = SessionMaker()
    queued_after = verify_s2.query(VideoJob).filter(VideoJob.status == "queued").all()
    assert len(queued_after) == 0


def test_planner_stops_at_open_count_two(db_setup):
    session, SessionMaker = db_setup

    fp = TargetFanpage(
        repliz_account_id="repliz-stock-2",
        name="Stock FP 2",
        timezone="UTC",
        is_active=True,
        is_connected=True,
        ai_video_enabled=True,
        ai_video_daily_count=5,
        ai_video_min_score=None,
        ai_video_clips=3,
        ai_video_cover_enabled=False,
    )
    session.add(fp)
    session.flush()

    # 2 in-flight open jobs
    open1 = VideoJob(
        fanpage_id=fp.id,
        template_key="motorsport_tech_explainer",
        title="Open 1",
        caption="Open 1",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[],
        credits_per_clip=15,
        credits_estimate=45,
        status="queued",
    )
    open2 = VideoJob(
        fanpage_id=fp.id,
        template_key="motorsport_tech_explainer",
        title="Open 2",
        caption="Open 2",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[],
        credits_per_clip=15,
        credits_estimate=45,
        status="generating",
    )
    idea = VideoIdea(
        fanpage_id=fp.id,
        template_key="motorsport_tech_explainer",
        topic="Open test topic",
        status="approved",
        score=90,
        rationale="r",
    )
    session.add_all([open1, open2, idea])
    session.commit()

    with patch.object(planner, "SessionLocal", SessionMaker), patch.object(
        planner, "fill_idea", return_value=_mock_fill_result()
    ):
        plan_ai_videos.run()

    verify = SessionMaker()
    queued = verify.query(VideoJob).filter(VideoJob.status == "queued").all()
    assert len(queued) == 1  # Only open1, no new job planned


def test_publish_job_holds_ai_video_when_slot_three_days_away(db_setup):
    session, SessionMaker = db_setup

    fp = TargetFanpage(
        name="Test FP",
        repliz_account_id="rep_1",
        is_active=True,
        is_connected=True,
    )
    session.add(fp)
    session.flush()

    dest = SocialDestination(
        content_project_id=fp.id,
        name="Dest 1",
        kind="facebook_page",
        mode="repliz",
        timezone="UTC",
        daily_target=5,
        phone_daily_target=0,
        repliz_daily_target=5,
        supported_media=["video"],
        is_active=True,
    )
    session.add(dest)
    session.flush()

    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_publish,
        video_url="https://example.com/ai_video.mp4",
        is_deleted=False,
    )
    session.add(job)
    session.commit()

    now = datetime.now(timezone.utc)
    slot_3d = now + timedelta(days=3)

    with patch("app.tasks.publisher.SessionLocal", SessionMaker), patch(
        "app.services.social_delivery_planner._plan_slot",
        return_value=(slot_3d, False, 24, []),
    ):
        publish_job.apply(args=[job.id])

    verify = SessionMaker()
    reloaded_job = verify.get(PublishJob, job.id)
    assert reloaded_job.status == PublishJobStatus.pending_publish
    deliv_count = (
        verify.query(PublishDelivery).filter_by(publish_job_id=job.id).count()
    )
    assert deliv_count == 0


def test_publish_job_creates_delivery_for_ai_video_when_slot_in_two_hours(db_setup):
    session, SessionMaker = db_setup

    fp = TargetFanpage(
        name="Test FP 2",
        repliz_account_id="rep_2",
        is_active=True,
        is_connected=True,
    )
    session.add(fp)
    session.flush()

    dest = SocialDestination(
        content_project_id=fp.id,
        name="Dest 2",
        kind="facebook_page",
        mode="repliz",
        timezone="UTC",
        daily_target=5,
        phone_daily_target=0,
        repliz_daily_target=5,
        supported_media=["video"],
        is_active=True,
    )
    session.add(dest)
    session.flush()

    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_publish,
        video_url="https://example.com/ai_video_soon.mp4",
        is_deleted=False,
    )
    session.add(job)
    session.commit()

    now = datetime.now(timezone.utc)
    slot_2h = now + timedelta(hours=2)

    with patch("app.tasks.publisher.SessionLocal", SessionMaker), patch(
        "app.services.social_delivery_planner._plan_slot",
        return_value=(slot_2h, False, 24, []),
    ), patch("app.services.social_delivery_dispatch.dispatch_due_deliveries"):
        publish_job.apply(args=[job.id])

    verify = SessionMaker()
    deliveries = verify.query(PublishDelivery).filter_by(publish_job_id=job.id).all()
    assert len(deliveries) == 1
    assert deliveries[0].media_kind == "video"


def test_youtube_clip_never_held_even_when_slot_far(db_setup):
    session, SessionMaker = db_setup

    fp = TargetFanpage(
        name="Test FP YT",
        repliz_account_id="rep_yt",
        is_active=True,
        is_connected=True,
    )
    session.add(fp)
    session.flush()

    dest = SocialDestination(
        content_project_id=fp.id,
        name="Dest YT",
        kind="facebook_page",
        mode="repliz",
        timezone="UTC",
        daily_target=5,
        phone_daily_target=0,
        repliz_daily_target=5,
        supported_media=["video"],
        is_active=True,
    )
    session.add(dest)
    session.flush()

    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.youtube_clip,
        status=PublishJobStatus.pending_publish,
        video_url="https://example.com/clip.mp4",
        is_deleted=False,
    )
    session.add(job)
    session.commit()

    now = datetime.now(timezone.utc)
    slot_3d = now + timedelta(days=3)

    with patch("app.tasks.publisher.SessionLocal", SessionMaker), patch(
        "app.services.social_delivery_planner._plan_slot",
        return_value=(slot_3d, False, 24, []),
    ), patch("app.services.social_delivery_dispatch.dispatch_due_deliveries"):
        publish_job.apply(args=[job.id])

    verify = SessionMaker()
    deliveries = verify.query(PublishDelivery).filter_by(publish_job_id=job.id).all()
    assert len(deliveries) == 1


def test_video_slot_soon_returns_true_no_dest_and_on_exception(db_setup):
    session, _ = db_setup

    fp = TargetFanpage(
        name="No Dest FP",
        repliz_account_id="rep_nodest",
        is_active=True,
        is_connected=True,
    )
    session.add(fp)
    session.flush()

    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_publish,
        video_url="https://example.com/v.mp4",
        is_deleted=False,
    )
    session.add(job)
    session.commit()

    # 1. Page has no active destinations -> True
    assert video_slot_soon(session, job) is True

    # 2. Page has active destination, but _plan_slot raises -> fail-open True
    dest = SocialDestination(
        content_project_id=fp.id,
        name="Dest Active",
        kind="facebook_page",
        mode="repliz",
        timezone="UTC",
        daily_target=5,
        supported_media=["video"],
        is_active=True,
    )
    session.add(dest)
    session.commit()

    with patch(
        "app.services.social_delivery_planner._plan_slot",
        side_effect=RuntimeError("Transient error"),
    ):
        assert video_slot_soon(session, job) is True

    # 3. job has no fanpage_id -> True
    job_no_fp = PublishJob(
        fanpage_id=None,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_publish,
        is_deleted=False,
    )
    assert video_slot_soon(session, job_no_fp) is True


def test_video_stock_helper(db_setup):
    session, _ = db_setup

    fp1 = TargetFanpage(name="FP1", is_active=True)
    fp2 = TargetFanpage(name="FP2", is_active=True)
    session.add_all([fp1, fp2])
    session.flush()

    # Valid stock rows for fp1: 1 pending_publish, 1 pending_review
    j1 = PublishJob(
        fanpage_id=fp1.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_publish,
        is_deleted=False,
    )
    j2 = PublishJob(
        fanpage_id=fp1.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_review,
        is_deleted=False,
    )
    # Deleted row for fp1 -> excluded
    j3 = PublishJob(
        fanpage_id=fp1.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_publish,
        is_deleted=True,
    )
    # Other status for fp1 -> excluded
    j4 = PublishJob(
        fanpage_id=fp1.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.published,
        is_deleted=False,
    )
    # Other content_type for fp1 -> excluded
    j5 = PublishJob(
        fanpage_id=fp1.id,
        content_type=ContentType.youtube_clip,
        status=PublishJobStatus.pending_publish,
        is_deleted=False,
    )
    # Valid row for fp2 -> excluded from fp1 count
    j6 = PublishJob(
        fanpage_id=fp2.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_publish,
        is_deleted=False,
    )
    session.add_all([j1, j2, j3, j4, j5, j6])
    session.commit()

    assert video_stock(session, fp1.id) == 2
    assert video_stock(session, fp2.id) == 1


def test_recover_stuck_auto_publishes_orders_ai_video_oldest_first(db_setup):
    session, SessionMaker = db_setup

    fp = TargetFanpage(
        name="Auto FP",
        repliz_account_id="rep_auto",
        is_active=True,
        is_connected=True,
        ai_video_publish_mode=PublishMode.auto,
    )
    session.add(fp)
    session.flush()

    now = datetime.now(timezone.utc)
    # Create older job and newer job
    job_old = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_publish,
        created_at=now - timedelta(days=2),
        is_deleted=False,
    )
    job_new = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.ai_video,
        status=PublishJobStatus.pending_publish,
        created_at=now - timedelta(hours=1),
        is_deleted=False,
    )
    session.add_all([job_new, job_old])
    session.commit()

    dispatched = []
    with patch("app.tasks.publisher.SessionLocal", SessionMaker), patch.object(
        publish_job, "delay", side_effect=lambda jid: dispatched.append(jid)
    ):
        recover_stuck_auto_publishes()

    assert dispatched == [job_old.id, job_new.id]


def test_recovery_rechecks_only_the_oldest_stock_per_page(monkeypatch):
    """A big stock must not flood the queue: at most STOCK_RECHECK_PER_PAGE videos per page per run."""
    from unittest.mock import MagicMock
    import app.tasks.publisher as pub

    rows = [(i, 7) for i in range(1, 6)] + [(i, 9) for i in range(10, 13)]
    db = MagicMock()
    q = db.query.return_value
    q.join.return_value = q
    q.filter.return_value = q
    q.order_by.return_value = q

    def all_side():
        # the ai_video query is the one selecting two columns; return rows only for it
        return rows if len(db.query.call_args.args) == 2 else []

    q.all.side_effect = all_side
    monkeypatch.setattr(pub, "SessionLocal", lambda: db)
    sent = []
    monkeypatch.setattr(pub.publish_job, "delay", lambda job_id: sent.append(job_id))
    pub.recover_stuck_auto_publishes()
    assert sent == [1, 2, 10, 11]
