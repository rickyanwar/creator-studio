"""Tests for GET /dashboard/stats brands_today and latest_posts (TASK B5)."""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause

from app.api.deps import get_current_user, get_db
from app.api import dashboard
from app.main import app
from app.models.social_publishing import PublishDelivery
from app.models.ai_copy_events import AICopyEvent
from app.models.gallery_fetch_events import GalleryFetchEvent
from app.models.posts import Post, MediaType, PostStatus
from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
from app.models.social_publishing import SocialDestination
from app.models.target_fanpages import TargetFanpage


import sqlite3
import json

sqlite3.register_adapter(list, json.dumps)


# Compile PostgreSQL-specific column types for SQLite
@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


SocialDestination.__table__.columns["supported_media"].server_default = DefaultClause(
    text("'[\"image\", \"album\", \"video\"]'")
)


@pytest.fixture
def db_session():
    """Isolated SQLite in-memory DB session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        TargetFanpage.__table__,
        SocialDestination.__table__,
        PublishDelivery.__table__,
        Post.__table__,
        PublishJob.__table__,
        AICopyEvent.__table__,
        GalleryFetchEvent.__table__,
    ]
    for table in tables:
        table.create(bind=engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """Test client with db and auth overrides."""
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: "admin_user"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_brands_today_timezone_midnight_and_counts(db_session, client, monkeypatch):
    """Verify published_today counts use each brand's timezone midnight."""
    # Freeze time at 2026-10-18 02:00:00 UTC
    # In Asia/Jakarta (UTC+7): 2026-10-18 09:00:00. Midnight was 2026-10-17 17:00:00 UTC.
    # In America/New_York (EDT, UTC-4): 2026-10-17 22:00:00. Midnight was 2026-10-17 04:00:00 UTC.
    # Note: 2026-10-18 00:30:00 UTC is:
    #   - Jakarta: Oct 18 07:30 (today)
    #   - New York: Oct 17 20:30 (today for NY, since NY is on Oct 17!)
    # But for a time at 2026-10-17 12:00:00 UTC:
    #   - Jakarta: Oct 17 19:00 (yesterday, before Oct 18 midnight 17:00 UTC)
    #   - New York: Oct 17 08:00 (today for NY, after NY Oct 17 midnight 04:00 UTC!)
    fixed_now = datetime(2026, 10, 18, 2, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(dashboard, "_utcnow", lambda: fixed_now)

    brand_jkt = TargetFanpage(
        id=1,
        name="Jakarta Brand",
        timezone="Asia/Jakarta",
        is_active=True,
        publish_daily_limit=20,
    )
    brand_ny = TargetFanpage(
        id=2,
        name="New York Brand",
        timezone="America/New_York",
        is_active=True,
        publish_daily_limit=15,
    )
    db_session.add_all([brand_jkt, brand_ny])
    db_session.commit()

    # Active channels for Jakarta brand: 2 channels with daily_target 5 and 7 -> sum 12
    chan_jkt1 = SocialDestination(
        id=10,
        content_project_id=brand_jkt.id,
        kind="facebook_page",
        name="JKT Page",
        is_active=True,
        daily_target=5,
    )
    chan_jkt2 = SocialDestination(
        id=11,
        content_project_id=brand_jkt.id,
        kind="facebook_profile",
        name="JKT Profile",
        is_active=True,
        daily_target=7,
    )
    # Inactive channel for NY brand (should not contribute to active_channels or daily_target)
    chan_ny_inact = SocialDestination(
        id=12,
        content_project_id=brand_ny.id,
        kind="facebook_page",
        name="NY Page Inactive",
        is_active=False,
        daily_target=10,
    )
    db_session.add_all([chan_jkt1, chan_jkt2, chan_ny_inact])
    db_session.commit()

    # Job at 2026-10-17 12:00:00 UTC:
    # Jakarta midnight is 2026-10-17 17:00:00 UTC -> 12:00 is BEFORE Jakarta midnight -> NOT published today for JKT.
    # New York midnight is 2026-10-17 04:00:00 UTC -> 12:00 is AFTER NY midnight -> IS published today for NY!
    job_12utc_jkt = PublishJob(
        id=101,
        fanpage_id=brand_jkt.id,
        status=PublishJobStatus.published,
        published_at=datetime(2026, 10, 17, 12, 0, 0),
        is_deleted=False,
    )
    job_12utc_ny = PublishJob(
        id=102,
        fanpage_id=brand_ny.id,
        status=PublishJobStatus.published,
        published_at=datetime(2026, 10, 17, 12, 0, 0),
        is_deleted=False,
    )
    # Job at 2026-10-18 01:00:00 UTC:
    # Jakarta midnight is 2026-10-17 17:00 UTC -> 01:00 is AFTER -> IS published today for JKT.
    # New York midnight is 2026-10-17 04:00 UTC -> 01:00 is AFTER -> IS published today for NY.
    job_01utc_jkt = PublishJob(
        id=103,
        fanpage_id=brand_jkt.id,
        status=PublishJobStatus.published,
        published_at=datetime(2026, 10, 18, 1, 0, 0),
        is_deleted=False,
    )

    # Waiting review jobs for JKT: 1 pending_review, 1 pending_design, 1 pending_publish
    w1 = PublishJob(
        id=104,
        fanpage_id=brand_jkt.id,
        status=PublishJobStatus.pending_review,
        is_deleted=False,
    )
    w2 = PublishJob(
        id=105,
        fanpage_id=brand_jkt.id,
        status=PublishJobStatus.pending_design,
        is_deleted=False,
    )
    w3 = PublishJob(
        id=106,
        fanpage_id=brand_jkt.id,
        status=PublishJobStatus.pending_publish,
        is_deleted=False,
    )

    # Failed job for NY today
    f_ny = PublishJob(
        id=107,
        fanpage_id=brand_ny.id,
        status=PublishJobStatus.failed,
        updated_at=datetime(2026, 10, 17, 20, 0, 0),
        is_deleted=False,
    )

    db_session.add_all([job_12utc_jkt, job_12utc_ny, job_01utc_jkt, w1, w2, w3, f_ny])
    db_session.commit()

    resp = client.get("/dashboard/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "brands_today" in data

    brands_by_id = {b["fanpage_id"]: b for b in data["brands_today"]}
    assert brand_jkt.id in brands_by_id
    assert brand_ny.id in brands_by_id

    # Jakarta brand checks
    b_jkt = brands_by_id[brand_jkt.id]
    assert b_jkt["name"] == "Jakarta Brand"
    assert b_jkt["timezone"] == "Asia/Jakarta"
    assert b_jkt["published_today"] == 1  # only job_01utc_jkt, job_12utc was yesterday local
    assert b_jkt["waiting_review"] == 3  # w1, w2, w3
    assert b_jkt["active_channels"] == 2
    assert b_jkt["daily_target"] == 12  # 5 + 7

    # New York brand checks
    b_ny = brands_by_id[brand_ny.id]
    assert b_ny["name"] == "New York Brand"
    assert b_ny["timezone"] == "America/New_York"
    assert b_ny["published_today"] == 1  # job_12utc_ny is today local in NY
    assert b_ny["failed_today"] == 1  # f_ny failed today in NY
    assert b_ny["active_channels"] == 0  # channel was inactive
    assert b_ny["daily_target"] == 15  # fallback to brand.publish_daily_limit


def test_inactive_brand_excluded_from_brands_today(db_session, client):
    """Inactive fanpages must NOT appear in brands_today."""
    active_fp = TargetFanpage(
        id=1,
        name="Active Page",
        timezone="Europe/London",
        is_active=True,
    )
    inactive_fp = TargetFanpage(
        id=2,
        name="Paused Brand",
        timezone="Europe/London",
        is_active=False,
    )
    db_session.add_all([active_fp, inactive_fp])
    db_session.commit()

    resp = client.get("/dashboard/stats")
    assert resp.status_code == 200
    data = resp.json()

    brand_ids = [b["fanpage_id"] for b in data["brands_today"]]
    assert active_fp.id in brand_ids
    assert inactive_fp.id not in brand_ids


def test_latest_posts_order_and_limit(db_session, client):
    """latest_posts returns last 6 published jobs across brands, ordered newest first."""
    fp1 = TargetFanpage(id=1, name="Formula News", is_active=True)
    fp2 = TargetFanpage(id=2, name="MotoGP Daily", is_active=True)
    db_session.add_all([fp1, fp2])
    db_session.commit()

    base_dt = datetime(2026, 10, 18, 10, 0, 0)
    jobs = []
    for i in range(8):
        job = PublishJob(
            id=200 + i,
            fanpage_id=fp1.id if i % 2 == 0 else fp2.id,
            content_type=ContentType.news_content,
            design_title=f"Breaking Story #{i} Headline with extra details that should be truncated if very long",
            design_image_url=f"https://cdn.example.com/designs/{i}.png",
            status=PublishJobStatus.published,
            published_at=base_dt + timedelta(minutes=i * 10),
            is_deleted=False,
        )
        jobs.append(job)

    # Add one non-published job that should be excluded
    jobs.append(
        PublishJob(
            id=300,
            fanpage_id=fp1.id,
            content_type=ContentType.ig_repost,
            status=PublishJobStatus.failed,
            published_at=base_dt + timedelta(hours=2),
            is_deleted=False,
        )
    )

    db_session.add_all(jobs)
    db_session.commit()

    resp = client.get("/dashboard/stats")
    assert resp.status_code == 200
    data = resp.json()

    assert "latest_posts" in data
    latest = data["latest_posts"]
    assert len(latest) == 6  # max 6 limit

    # Check order: newest first (i = 7, 6, 5, 4, 3, 2)
    ids = [p["id"] for p in latest]
    assert ids == [207, 206, 205, 204, 203, 202]

    first = latest[0]
    assert first["id"] == 207
    assert first["fanpage_id"] == fp2.id
    assert first["fanpage_name"] == "MotoGP Daily"
    assert first["content_type"] == "news_content"
    assert first["thumbnail_url"] == "https://cdn.example.com/designs/7.png"
    assert len(first["title"]) <= 80
    assert "Breaking Story #7" in first["title"]


def test_latest_posts_caption_and_thumbnail_fallbacks(db_session, client):
    """latest_posts falls back to ai_generated_caption or post caption, and video/post thumbnails."""
    fp = TargetFanpage(id=1, name="Fallback Brand", is_active=True)
    db_session.add(fp)
    db_session.commit()

    # Job 1: no design_title, but has ai_generated_caption and video_thumbnail_url
    job_vid = PublishJob(
        id=401,
        fanpage_id=fp.id,
        content_type=ContentType.youtube_clip,
        ai_generated_caption="A great clip from YouTube recap video",
        video_thumbnail_url="https://cdn.example.com/thumb.jpg",
        status=PublishJobStatus.published,
        published_at=datetime(2026, 10, 18, 11, 0, 0),
        is_deleted=False,
    )
    # Job 2: no design_title, no ai_generated_caption, but has Post with caption & images
    post = Post(
        id=501,
        ig_source_id=1,
        ig_media_id="ig_123",
        media_type=MediaType.image,
        original_caption="Original IG caption text",
        image_public_urls=["https://cdn.example.com/ig.jpg"],
        status=PostStatus.stored,
    )
    db_session.add(post)
    db_session.commit()

    job_ig = PublishJob(
        id=402,
        fanpage_id=fp.id,
        post_id=post.id,
        content_type=ContentType.ig_repost,
        status=PublishJobStatus.published,
        published_at=datetime(2026, 10, 18, 12, 0, 0),
        is_deleted=False,
    )
    db_session.add_all([job_vid, job_ig])
    db_session.commit()

    resp = client.get("/dashboard/stats")
    assert resp.status_code == 200
    data = resp.json()
    posts_by_id = {p["id"]: p for p in data["latest_posts"]}

    p_ig = posts_by_id[402]
    assert p_ig["title"] == "Original IG caption text"
    assert p_ig["thumbnail_url"] == "https://cdn.example.com/ig.jpg"

    p_vid = posts_by_id[401]
    assert p_vid["title"] == "A great clip from YouTube recap video"
    assert p_vid["thumbnail_url"] == "https://cdn.example.com/thumb.jpg"



def test_latest_posts_report_the_real_go_live_time(db_session, client):
    """A post handed to Repliz is listed with its delivery slot, so the UI can say "scheduled" until it is live."""
    fp = TargetFanpage(id=1, name="Go Live Brand", is_active=True)
    db_session.add(fp)
    db_session.commit()
    dest = SocialDestination(id=20, content_project_id=fp.id, kind="facebook_page", name="Page", is_active=True, daily_target=5)
    db_session.add(dest)
    db_session.commit()
    handed = datetime(2026, 10, 24, 0, 22)
    slot = datetime(2026, 10, 24, 1, 6)
    job = PublishJob(id=501, fanpage_id=fp.id, content_type=ContentType.news_content, design_title="**Antonelli** news",
                     status=PublishJobStatus.published, published_at=handed, is_deleted=False)
    db_session.add(job)
    db_session.commit()
    db_session.add(PublishDelivery(publish_job_id=job.id, destination_id=dest.id, state="repliz_scheduled", scheduled_for=slot,
                                   intended_method="repliz"))
    db_session.commit()
    post = next(p for p in client.get("/dashboard/stats").json()["latest_posts"] if p["id"] == 501)
    assert post["published_at"].startswith("2026-10-24T00:22")
    assert post["go_live_at"].startswith("2026-10-24T01:06")
