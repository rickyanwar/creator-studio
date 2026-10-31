from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"

from app.models.fanpage_news_sources import FanpageNewsSource
from app.models.gallery import GalleryKeyword
from app.models.news_sources import NewsSource, RenderMode
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
from app.services.event_days import is_event_window, is_live_event_news
from app.services.mode_quota import pick_slot
from app.services.social_delivery_dispatch import (
    _news_dispatch_gate,
    claim_paced_delivery_for_dispatch,
    replan_stale_scheduled_deliveries,
)
from app.services.social_delivery_planner import ensure_deliveries
from app.tasks.news_scraper import effective_scrape_interval_minutes, scrape_all_sources


@pytest.fixture
def db_session():
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
    GalleryKeyword.__table__.create(engine)
    NewsSource.__table__.create(engine)
    FanpageNewsSource.__table__.create(engine)

    sd_col.server_default = orig_sd
    de_col.server_default = orig_de

    session = Session(engine)
    try:
        yield session
    finally:
        session.close()


def test_1_event_window_motogp_and_f1(db_session: Session):
    """1. MotoGP race on Sunday -> Thursday..Monday are event-window days; Wednesday is not. F1: Thursday is now in window."""
    sun = date(2026, 11, 1)  # Sunday
    kw_mgp = GalleryKeyword(keyword="Francesco Bagnaia", niche="motogp", next_event_date=sun)
    kw_f1 = GalleryKeyword(keyword="Max Verstappen", niche="f1", next_event_date=sun)
    db_session.add_all([kw_mgp, kw_f1])
    db_session.commit()

    fp_mgp = TargetFanpage(name="MotoGP Hub", mode2_gallery_niches=["MotoGP"])
    fp_f1 = TargetFanpage(name="F1 Hub", mode2_gallery_niches=["F1"])
    db_session.add_all([fp_mgp, fp_f1])
    db_session.commit()

    # MotoGP: Sunday is race. Window is H-3 (Thu) to H+1 (Mon)
    assert not is_event_window(db_session, fp_mgp, date(2026, 10, 28))  # Wed
    assert is_event_window(db_session, fp_mgp, date(2026, 10, 29))  # Thu
    assert is_event_window(db_session, fp_mgp, date(2026, 10, 30))  # Fri
    assert is_event_window(db_session, fp_mgp, date(2026, 10, 31))  # Sat
    assert is_event_window(db_session, fp_mgp, date(2026, 11, 1))  # Sun
    assert is_event_window(db_session, fp_mgp, date(2026, 11, 2))  # Mon
    assert not is_event_window(db_session, fp_mgp, date(2026, 11, 3))  # Tue

    # F1: H-3 (Thu) now in window
    assert not is_event_window(db_session, fp_f1, date(2026, 10, 28))  # Wed
    assert is_event_window(db_session, fp_f1, date(2026, 10, 29))  # Thu
    assert is_event_window(db_session, fp_f1, date(2026, 10, 30))  # Fri
    assert is_event_window(db_session, fp_f1, date(2026, 11, 1))  # Sun
    assert is_event_window(db_session, fp_f1, date(2026, 11, 2))  # Mon
    assert not is_event_window(db_session, fp_f1, date(2026, 11, 3))  # Tue


def test_2_live_news_posts_right_away_even_if_full_and_sleeping(db_session: Session):
    """2. news job on event day -> delivery scheduled_for within 60-90s of now, even when target full & inside sleep window."""
    today = date(2026, 10, 29)  # Thursday
    race_day = date(2026, 11, 1)
    kw = GalleryKeyword(keyword="Pecco", niche="motogp", next_event_date=race_day)
    db_session.add(kw)

    fp = TargetFanpage(
        name="FP Live",
        mode2_gallery_niches=["motogp"],
        mode2_news_content_enabled=True,
        publish_sleep_start_hour=0,
        publish_sleep_end_hour=6,
    )
    db_session.add(fp)
    db_session.commit()

    dest = SocialDestination(
        content_project_id=fp.id,
        kind="facebook_page",
        name="Dest Live",
        mode="repliz",
        timezone="UTC",
        daily_target=2,
        min_gap_minutes=15,
        supported_media=["image"],
        is_active=True,
    )
    db_session.add(dest)
    db_session.commit()

    # Fill daily target for today with 4 deliveries (effective target is 2*2=4)
    for i in range(4):
        pj = PublishJob(
            fanpage_id=fp.id,
            content_type=ContentType.ig_repost,
            status=PublishJobStatus.pending_publish,
        )
        db_session.add(pj)
        db_session.flush()
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=pj.id,
            state="scheduled",
            scheduled_for=datetime(2026, 10, 29, 1, i, tzinfo=timezone.utc).replace(tzinfo=None),
        )
        db_session.add(deliv)
    db_session.commit()

    # Now is 03:30 UTC — directly inside sleep window (0..6)
    now_utc = datetime(2026, 10, 29, 3, 30, tzinfo=timezone.utc)
    news_job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        design_image_url="https://example.com/live.png",
        ai_generated_caption="Live breaking headline",
        created_at=now_utc,
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(news_job)
    db_session.commit()

    deliveries = ensure_deliveries(db_session, news_job, now_utc=now_utc)
    assert len(deliveries) == 1
    d = deliveries[0]
    assert d.scheduled_for is not None
    sched_utc = d.scheduled_for.replace(tzinfo=timezone.utc)
    delta_s = (sched_utc - now_utc).total_seconds()
    assert 60 <= delta_s <= 90
    assert sched_utc.hour == 3  # Did not push past sleep window


def test_3_two_live_news_min_gap_10_min(db_session: Session):
    """3. two live news for the same destination 2 min apart -> second scheduled_for >= first + 10 min."""
    race_day = date(2026, 11, 1)
    kw = GalleryKeyword(keyword="Pecco", niche="motogp", next_event_date=race_day)
    db_session.add(kw)

    fp = TargetFanpage(name="FP Gap", mode2_gallery_niches=["motogp"], mode2_news_content_enabled=True)
    db_session.add(fp)
    db_session.commit()

    dest = SocialDestination(
        content_project_id=fp.id,
        kind="facebook_page",
        name="Dest Gap",
        mode="repliz",
        timezone="UTC",
        daily_target=5,
        min_gap_minutes=15,
        supported_media=["image"],
        is_active=True,
    )
    db_session.add(dest)
    db_session.commit()

    t0 = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)
    news1 = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        design_image_url="https://example.com/news1.png",
        created_at=t0,
    )
    db_session.add(news1)
    db_session.commit()

    delivs1 = ensure_deliveries(db_session, news1, now_utc=t0)
    sched1 = delivs1[0].scheduled_for.replace(tzinfo=timezone.utc)
    assert sched1 == t0 + timedelta(seconds=60)

    # 2 minutes later
    t1 = t0 + timedelta(minutes=2)
    news2 = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        design_image_url="https://example.com/news2.png",
        created_at=t1,
    )
    db_session.add(news2)
    db_session.commit()

    delivs2 = ensure_deliveries(db_session, news2, now_utc=t1)
    sched2 = delivs2[0].scheduled_for.replace(tzinfo=timezone.utc)
    assert sched2 >= sched1 + timedelta(minutes=10)


def test_4_live_news_does_not_consume_quota_for_other_modes(db_session: Session):
    """4. live news does not reduce the count left for other modes that day."""
    race_day = date(2026, 11, 1)
    kw = GalleryKeyword(keyword="Pecco", niche="motogp", next_event_date=race_day)
    db_session.add(kw)

    fp = TargetFanpage(
        name="FP Quota",
        mode2_gallery_niches=["motogp"],
        mode1_ig_repost_enabled=True,
        mode2_news_content_enabled=True,
    )
    db_session.add(fp)
    db_session.commit()

    dest = SocialDestination(
        content_project_id=fp.id,
        kind="facebook_page",
        name="Dest Quota",
        mode="repliz",
        timezone="UTC",
        daily_target=4,
        min_gap_minutes=15,
        supported_media=["image"],
        is_active=True,
        mode_shares={"ig": 50, "news": 50},
    )
    db_session.add(dest)
    db_session.commit()

    t0 = datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)

    # Baseline: what slot would an image job get if no news existed?
    image_job_baseline = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.ig_repost,
        watermarked_image_urls=["https://example.com/img.png"],
        created_at=t0,
    )
    slot_no_news = pick_slot(
        db=db_session,
        destination=dest,
        job=image_job_baseline,
        fanpage=fp,
        now_utc=t0,
        all_reservations=[],
    )

    # Now schedule 3 live news jobs
    for i in range(3):
        t_i = t0 + timedelta(minutes=12 * i)
        nj = PublishJob(
            fanpage_id=fp.id,
            content_type=ContentType.news_content,
            design_image_url=f"https://example.com/n{i}.png",
            created_at=t_i,
        )
        db_session.add(nj)
        db_session.commit()
        ensure_deliveries(db_session, nj, now_utc=t_i)

    # Schedule image job after the 3 live news
    image_job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.ig_repost,
        watermarked_image_urls=["https://example.com/img.png"],
        created_at=t0 + timedelta(minutes=36),
    )
    db_session.add(image_job)
    db_session.commit()

    delivs_img = ensure_deliveries(db_session, image_job, now_utc=t0 + timedelta(minutes=36))
    img_sched = delivs_img[0].scheduled_for.replace(tzinfo=timezone.utc)

    assert img_sched == slot_no_news


def test_5_non_event_day_uses_standard_grid(db_session: Session):
    """5. non-event day -> old behaviour unchanged (grid slot)."""
    # Race on 2026-11-15, today is 2026-10-29 -> far outside window
    kw = GalleryKeyword(keyword="Pecco", niche="motogp", next_event_date=date(2026, 11, 15))
    db_session.add(kw)

    fp = TargetFanpage(
        name="FP Normal",
        mode2_gallery_niches=["motogp"],
        mode2_news_content_enabled=True,
    )
    db_session.add(fp)
    db_session.commit()

    dest = SocialDestination(
        content_project_id=fp.id,
        kind="facebook_page",
        name="Dest Normal",
        mode="repliz",
        timezone="UTC",
        daily_target=4,
        min_gap_minutes=15,
        supported_media=["image"],
        is_active=True,
    )
    db_session.add(dest)
    db_session.commit()

    t0 = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)
    nj = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        design_image_url="https://example.com/normal.png",
        created_at=t0,
    )
    db_session.add(nj)
    db_session.commit()

    delivs = ensure_deliveries(db_session, nj, now_utc=t0)
    sched = delivs[0].scheduled_for.replace(tzinfo=timezone.utc)
    # On normal grid, slot is not 60 seconds from now
    assert (sched - t0).total_seconds() > 90


def test_6_news_dispatch_gate_live_event_stale_checks(db_session: Session):
    """6. _news_dispatch_gate -> go for live news; a 7 h old live news -> stale/skipped."""
    today = date(2026, 10, 29)
    race_day = date(2026, 11, 1)
    kw = GalleryKeyword(keyword="Pecco", niche="motogp", next_event_date=race_day)
    db_session.add(kw)

    fp = TargetFanpage(name="FP Gate", mode2_gallery_niches=["motogp"], mode2_news_content_enabled=True)
    db_session.add(fp)
    db_session.commit()

    dest = SocialDestination(
        content_project_id=fp.id,
        kind="facebook_page",
        name="Dest Gate",
        mode="repliz",
        timezone="UTC",
        daily_target=4,
        is_active=True,
    )
    db_session.add(dest)
    db_session.commit()

    now_utc = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

    # 1. Fresh live news (created 1 hour ago, scheduled 2 hours in the future)
    # Should return "go" (skipping the 45-min repliz hold)
    job_fresh = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        created_at=now_utc - timedelta(hours=1),
        is_breaking=False,
    )
    db_session.add(job_fresh)
    db_session.flush()

    deliv_fresh = PublishDelivery(
        destination_id=dest.id,
        publish_job_id=job_fresh.id,
        intended_method="repliz",
        state="scheduled",
        scheduled_for=(now_utc + timedelta(hours=2)).replace(tzinfo=None),
    )
    db_session.add(deliv_fresh)
    db_session.commit()

    assert _news_dispatch_gate(db_session, deliv_fresh, now=now_utc) == "go"

    # 2. 7-hour old live news (event news freshness is 6 hours)
    job_stale = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        created_at=now_utc - timedelta(hours=7),
        is_breaking=False,
    )
    db_session.add(job_stale)
    db_session.flush()

    deliv_stale = PublishDelivery(
        destination_id=dest.id,
        publish_job_id=job_stale.id,
        intended_method="repliz",
        state="scheduled",
        scheduled_for=(now_utc + timedelta(minutes=5)).replace(tzinfo=None),
    )
    db_session.add(deliv_stale)
    db_session.commit()

    assert _news_dispatch_gate(db_session, deliv_stale, now=now_utc) == "stale"


def test_7_claim_uses_10_min_gap_for_live_news(db_session: Session):
    """7. claim uses a 10-min gap for live news, the destination gap for others."""
    today = date(2026, 10, 29)
    race_day = date(2026, 11, 1)
    kw = GalleryKeyword(keyword="Pecco", niche="motogp", next_event_date=race_day)
    db_session.add(kw)

    fp = TargetFanpage(name="FP Claim", mode2_gallery_niches=["motogp"], mode2_news_content_enabled=True)
    db_session.add(fp)
    db_session.commit()

    dest = SocialDestination(
        content_project_id=fp.id,
        kind="facebook_page",
        name="Dest Claim",
        mode="repliz",
        timezone="UTC",
        daily_target=4,
        min_gap_minutes=30,  # 30-min destination gap
        is_active=True,
    )
    db_session.add(dest)
    db_session.commit()

    now_utc = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    naive_now = now_utc.replace(tzinfo=None)

    # Prior delivery published 15 minutes ago
    prior_job = PublishJob(fanpage_id=fp.id, content_type=ContentType.ig_repost)
    db_session.add(prior_job)
    db_session.flush()
    prior_deliv = PublishDelivery(
        destination_id=dest.id,
        publish_job_id=prior_job.id,
        state="published",
        published_at=naive_now - timedelta(minutes=15),
    )
    db_session.add(prior_deliv)

    # Candidate 1: regular IG job due now. Destination gap is 30m, 15m elapsed -> claim blocked
    ig_job = PublishJob(fanpage_id=fp.id, content_type=ContentType.ig_repost)
    db_session.add(ig_job)
    db_session.flush()
    ig_deliv = PublishDelivery(
        destination_id=dest.id,
        publish_job_id=ig_job.id,
        state="scheduled",
        scheduled_for=naive_now,
    )
    db_session.add(ig_deliv)
    db_session.commit()

    cand, token = claim_paced_delivery_for_dispatch(db_session, ig_deliv.id, now_utc=now_utc)
    assert cand is None  # Blocked by 30-min gap

    # Candidate 2: live event news due now. Effective gap is min(30, 10) = 10m. 15m elapsed -> claim succeeds!
    news_job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        created_at=now_utc - timedelta(minutes=5),
    )
    db_session.add(news_job)
    db_session.flush()
    news_deliv = PublishDelivery(
        destination_id=dest.id,
        publish_job_id=news_job.id,
        state="scheduled",
        scheduled_for=naive_now,
    )
    db_session.add(news_deliv)
    db_session.commit()

    cand2, token2 = claim_paced_delivery_for_dispatch(db_session, news_deliv.id, now_utc=now_utc)
    assert cand2 is not None
    assert token2 is not None


def test_8_replan_stale_scheduled_deliveries_leaves_live_news_alone(db_session: Session):
    """8. replan_stale_scheduled_deliveries leaves live news alone."""
    today = date(2026, 10, 29)
    race_day = date(2026, 11, 1)
    kw = GalleryKeyword(keyword="Pecco", niche="motogp", next_event_date=race_day)
    db_session.add(kw)

    fp = TargetFanpage(name="FP Replan", mode2_gallery_niches=["motogp"], mode2_news_content_enabled=True)
    db_session.add(fp)
    db_session.commit()

    dest = SocialDestination(
        content_project_id=fp.id,
        kind="facebook_page",
        name="Dest Replan",
        mode="repliz",
        timezone="UTC",
        daily_target=4,
        min_gap_minutes=15,
        is_active=True,
    )
    db_session.add(dest)
    db_session.commit()

    now_utc = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    naive_now = now_utc.replace(tzinfo=None)
    stale_time = naive_now - timedelta(minutes=25)

    # 1. Live event news overdue by 25 minutes
    news_job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        created_at=now_utc - timedelta(hours=1),
    )
    db_session.add(news_job)
    db_session.flush()
    news_deliv = PublishDelivery(
        destination_id=dest.id,
        publish_job_id=news_job.id,
        state="scheduled",
        attempt_count=0,
        scheduled_for=stale_time,
    )
    db_session.add(news_deliv)

    # 2. Regular IG delivery overdue by 25 minutes
    ig_job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.ig_repost,
        created_at=now_utc - timedelta(hours=1),
    )
    db_session.add(ig_job)
    db_session.flush()
    ig_deliv = PublishDelivery(
        destination_id=dest.id,
        publish_job_id=ig_job.id,
        state="scheduled",
        attempt_count=0,
        scheduled_for=stale_time,
    )
    db_session.add(ig_deliv)
    db_session.commit()

    replanned = replan_stale_scheduled_deliveries(db_session, now_utc=now_utc)
    # Only the IG delivery was replanned
    replanned_ids = [d.id for d in replanned]
    assert ig_deliv.id in replanned_ids
    assert news_deliv.id not in replanned_ids

    # Refresh news delivery: scheduled_for is still the original stale_time
    db_session.refresh(news_deliv)
    assert news_deliv.scheduled_for == stale_time


def test_9_scraper_due_interval_in_event_window(db_session: Session):
    """9. scraper: a source due after 15 min during event window, after scrape_interval_minutes otherwise."""
    race_day = date(2026, 11, 1)
    kw = GalleryKeyword(keyword="Pecco", niche="motogp", next_event_date=race_day)
    db_session.add(kw)

    fp = TargetFanpage(name="FP Scraper", mode2_gallery_niches=["motogp"], is_active=True)
    db_session.add(fp)
    db_session.commit()

    src = NewsSource(
        name="Crash.net",
        category_url="https://crash.net/rss",
        is_active=True,
        scrape_interval_minutes=60,
    )
    db_session.add(src)
    db_session.commit()

    link = FanpageNewsSource(fanpage_id=fp.id, news_source_id=src.id, is_active=True)
    db_session.add(link)
    db_session.commit()

    # Event day (Thu 2026-10-29): effective interval must be min(60, 15) = 15
    event_now = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)
    eff_event = effective_scrape_interval_minutes(db_session, src, now=event_now)
    assert eff_event == 15

    # Non-event day (Wed 2026-10-28): effective interval is 60
    normal_now = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)
    eff_normal = effective_scrape_interval_minutes(db_session, src, now=normal_now)
    assert eff_normal == 60
