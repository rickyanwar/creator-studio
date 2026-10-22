import zoneinfo
from datetime import date, datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session

# Allow SQLite to compile PostgreSQL ARRAY and JSONB types in memory if SQLite is used
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"

from app.api.deps import get_current_user, get_db
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import (
    DeliveryEvent,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.services.day_plan import destination_day_plan


@pytest.fixture(scope="module")
def setup_db():
    # If running with SQLite, temporarily clear server_defaults that sqlite can't parse
    sd_col = SocialDestination.__table__.columns["supported_media"]
    orig_sd = sd_col.server_default
    de_col = DeliveryEvent.__table__.columns["details"]
    orig_de = de_col.server_default
    if engine.dialect.name == "sqlite":
        sd_col.server_default = None
        de_col.server_default = None

    Base.metadata.create_all(bind=engine)

    if engine.dialect.name == "sqlite":
        sd_col.server_default = orig_sd
        de_col.server_default = orig_de
    yield


@pytest.fixture
def db_session(setup_db):
    session = SessionLocal()
    try:
        session.query(DeliveryEvent).delete()
        session.query(PublishDelivery).delete()
        session.query(PublishJob).delete()
        session.query(SocialDestination).delete()
        session.query(TargetFanpage).filter(TargetFanpage.name == "Day Plan Test Fanpage").delete()
        session.commit()
        yield session
    finally:
        session.rollback()
        session.query(DeliveryEvent).delete()
        session.query(PublishDelivery).delete()
        session.query(PublishJob).delete()
        session.query(SocialDestination).delete()
        session.query(TargetFanpage).filter(TargetFanpage.name == "Day Plan Test Fanpage").delete()
        session.commit()
        session.close()


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: "admin_user"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_destination_day_plan_logic_and_api(db_session, client):
    """Destination with mode_shares {news: 67, ig: 26, ai_video: 7}, daily_target: 15,

    two reservations today (ig, news) -> quotas, used, remaining, and upcoming_slots correct.
    """
    fanpage = TargetFanpage(
        name="Day Plan Test Fanpage",
        timezone="Asia/Jakarta",
        mode1_ig_repost_enabled=True,
        mode2_news_content_enabled=True,
        ai_video_enabled=True,
        publish_sleep_start_hour=1,
        publish_sleep_end_hour=6,
    )
    db_session.add(fanpage)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Day Plan Channel",
        mode="repliz",
        timezone="Asia/Jakarta",
        daily_target=15,
        phone_daily_target=0,
        repliz_daily_target=15,
        min_gap_minutes=20,
        mode_shares={"news": 67, "ig": 26, "ai_video": 7},
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    # Fixed "now": 2026-10-22 04:00 UTC = 2026-10-22 11:00 Asia/Jakarta
    now_utc = datetime(2026, 10, 22, 4, 0, 0, tzinfo=timezone.utc)
    target_tz = zoneinfo.ZoneInfo("Asia/Jakarta")

    # Reservation 1: IG repost today at 08:00 WIB (01:00 UTC)
    job_ig = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.ig_repost,
        status=PublishJobStatus.published,
    )
    db_session.add(job_ig)
    db_session.flush()
    deliv_ig = PublishDelivery(
        publish_job_id=job_ig.id,
        destination_id=dest.id,
        state="scheduled",
        scheduled_for=datetime(2026, 10, 22, 1, 0, 0),
    )
    db_session.add(deliv_ig)

    # Reservation 2: News content today at 09:00 WIB (02:00 UTC)
    job_news = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.published,
    )
    db_session.add(job_news)
    db_session.flush()
    deliv_news = PublishDelivery(
        publish_job_id=job_news.id,
        destination_id=dest.id,
        state="scheduled",
        scheduled_for=datetime(2026, 10, 22, 2, 0, 0),
    )
    db_session.add(deliv_news)
    db_session.commit()

    # 1. Test the service directly
    plan = destination_day_plan(db_session, dest, now_utc=now_utc)

    assert plan["destination_id"] == dest.id
    assert plan["name"] == "Day Plan Channel"
    assert plan["timezone"] == "Asia/Jakarta"
    assert plan["local_date"] == "2026-10-22"
    assert plan["daily_target"] == 15

    # Quotas: 15 * {news 67, ig 26, ai_video 7} -> news: 10, ig: 4, ai_video: 1
    assert plan["quotas"] == {"news": 10, "ig": 4, "ai_video": 1}

    # Used today: 1 ig, 1 news
    assert plan["used"].get("ig") == 1
    assert plan["used"].get("news") == 1
    assert plan["used"].get("ai_video", 0) == 0

    # Remaining: news: 9, ig: 3, ai_video: 1
    assert plan["remaining"] == {"news": 9, "ig": 3, "ai_video": 1}
    assert plan["remaining_total"] == 13

    # Upcoming slots: all today, all distinct, count <= remaining_total
    slots = plan["upcoming_slots"]
    assert len(slots) <= plan["remaining_total"]
    assert len(slots) > 0
    assert len(slots) == len(set(slots)), "Slots must all be distinct"
    for slot_str in slots:
        slot_dt = datetime.fromisoformat(slot_str)
        slot_local = slot_dt.astimezone(target_tz)
        assert slot_local.date() == date(2026, 10, 22), f"Slot {slot_str} must be on today's local date"

    # 2. Test the API endpoint GET /social-media/day-plan
    resp = client.get("/social-media/day-plan")
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)
    matching = [i for i in items if i["destination_id"] == dest.id]
    assert len(matching) == 1
    dest_item = matching[0]
    assert dest_item["name"] == "Day Plan Channel"
    assert dest_item["quotas"] == {"news": 10, "ig": 4, "ai_video": 1}
    assert dest_item["remaining"] == {"news": 9, "ig": 3, "ai_video": 1}
    assert dest_item["remaining_total"] == 13

    # Filter with ?destination_id=
    resp_filtered = client.get(f"/social-media/day-plan?destination_id={dest.id}")
    assert resp_filtered.status_code == 200
    filtered_items = resp_filtered.json()
    assert len(filtered_items) == 1
    assert filtered_items[0]["destination_id"] == dest.id
