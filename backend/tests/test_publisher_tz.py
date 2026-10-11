import pytest
import uuid
from datetime import datetime, timezone, timedelta
import zoneinfo

from app.database import Base, engine, SessionLocal
from app.models.target_fanpages import TargetFanpage
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.tasks.publisher import _next_schedule_at, _push_past_sleep

@pytest.fixture(scope="module")
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield

@pytest.fixture
def db_session(setup_db):
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()

def test_push_past_sleep():
    tz = zoneinfo.ZoneInfo("Europe/London")
    dt = datetime(2026, 10, 20, 3, 30, tzinfo=tz) 
    pushed = _push_past_sleep(dt, start=0, end=6)
    assert pushed == datetime(2026, 10, 20, 6, 0, tzinfo=tz)

    dt = datetime(2026, 10, 20, 23, 30, tzinfo=tz) 
    pushed = _push_past_sleep(dt, start=22, end=6)
    assert pushed == datetime(2026, 10, 21, 6, 0, tzinfo=tz)

    dt = datetime(2026, 10, 25, 3, 30, tzinfo=tz)
    pushed = _push_past_sleep(dt, start=0, end=6)
    assert pushed.hour == 6
    assert pushed.minute == 0
    assert pushed.tzinfo == tz
    
    tz_ny = zoneinfo.ZoneInfo("America/New_York")
    dt = datetime(2026, 11, 1, 3, 30, tzinfo=tz_ny)
    pushed = _push_past_sleep(dt, start=0, end=6)
    assert pushed.hour == 6

def test_next_schedule_at_dst(db_session, monkeypatch):
    uid = uuid.uuid4().hex[:8]
    fanpage = TargetFanpage(
        repliz_account_id=f"test_lon_{uid}",
        name="Test London",
        timezone="Europe/London",
        publish_daily_limit=5,
        publish_sleep_start_hour=0,
        publish_sleep_end_hour=6
    )
    db_session.add(fanpage)
    db_session.commit()

    tz = zoneinfo.ZoneInfo("Europe/London")
    mock_now = datetime(2026, 10, 24, 23, 50, tzinfo=tz).astimezone(timezone.utc)

    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz == timezone.utc:
                return mock_now
            return super().now(tz)
    
    monkeypatch.setattr("app.tasks.publisher.datetime", MockDatetime)

    next_sched = _next_schedule_at(db_session, fanpage.id, breaking=True)
    next_sched_local = next_sched.replace(tzinfo=timezone.utc).astimezone(tz)
    assert next_sched_local.hour == 23
    assert next_sched_local.minute == 51

    mock_now = datetime(2026, 10, 25, 2, 30, tzinfo=tz).astimezone(timezone.utc)
    next_sched = _next_schedule_at(db_session, fanpage.id, breaking=True)
    local_sched = next_sched.replace(tzinfo=timezone.utc).astimezone(tz)
    assert local_sched.hour == 6
    assert local_sched.minute == 0
    assert local_sched.date().day == 25

def test_next_schedule_at_daily_cap(db_session, monkeypatch):
    uid = uuid.uuid4().hex[:8]
    fanpage = TargetFanpage(
        repliz_account_id=f"test_jkt_{uid}",
        name="Test Jakarta",
        timezone="Asia/Jakarta",
        publish_daily_limit=1,
        publish_sleep_start_hour=0,
        publish_sleep_end_hour=6
    )
    db_session.add(fanpage)
    db_session.commit()

    tz = zoneinfo.ZoneInfo("Asia/Jakarta")
    mock_now = datetime(2026, 10, 20, 10, 0, tzinfo=tz).astimezone(timezone.utc)

    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz == timezone.utc:
                return mock_now
            return super().now(tz)
    
    monkeypatch.setattr("app.tasks.publisher.datetime", MockDatetime)
    
    job = PublishJob(
        fanpage_id=fanpage.id,
        scheduled_for=mock_now.replace(tzinfo=None) - timedelta(hours=1)
    )
    db_session.add(job)
    db_session.commit()

    next_sched = _next_schedule_at(db_session, fanpage.id)
    local_sched = next_sched.replace(tzinfo=timezone.utc).astimezone(tz)
    
    assert local_sched.date().day == 21
    assert local_sched.hour == 6
    assert local_sched.minute == 0

def test_daily_cap_by_local_day(db_session, monkeypatch):
    uid = uuid.uuid4().hex[:8]
    fanpage = TargetFanpage(
        repliz_account_id=f"test_lon2_{uid}",
        name="Test London 2",
        timezone="Europe/London",
        publish_daily_limit=1,
        publish_sleep_start_hour=None,
        publish_sleep_end_hour=None
    )
    db_session.add(fanpage)
    db_session.commit()

    tz = zoneinfo.ZoneInfo("Europe/London")
    mock_now = datetime(2026, 10, 20, 10, 0, tzinfo=tz).astimezone(timezone.utc) # 09:00 UTC

    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz == timezone.utc:
                return mock_now
            return super().now(tz)
    
    monkeypatch.setattr("app.tasks.publisher.datetime", MockDatetime)
    
    # Existing job for this fanpage with scheduled_for = 2026-10-20 22:30 UTC (23:30 London)
    job = PublishJob(
        fanpage_id=fanpage.id,
        scheduled_for=datetime(2026, 10, 20, 22, 30)
    )
    db_session.add(job)
    db_session.commit()

    # The next slot must be on the 21st (local time). So the earliest is 00:00 London on the 21st (which is 23:00 UTC on the 20th).
    # Since there's no sleep window, it will try to place it at start of day (midnight) or max(earliest, floor_utc) which is midnight.
    # Oh wait, gap is added. But it hops day, so it will be max(day_start_utc, last_today+gap)... wait no, last_today is for that day. If it hops to next day, count=0, so last_today for the 21st is None!
    # Let's see: on 21st, last_today is None, earliest_today_utc is max(floor_utc, day_start_utc). day_start_utc is 23:00 UTC on the 20th. So candidate_utc is 23:00 UTC.
    next_sched = _next_schedule_at(db_session, fanpage.id)
    assert next_sched == datetime(2026, 10, 20, 23, 0)

def test_spring_forward_gap(db_session, monkeypatch):
    tz = zoneinfo.ZoneInfo("Europe/London")
    # spring forward is 2027-03-28 01:00 UTC (which is 01:00 GMT -> 02:00 BST).
    # The local times from 01:00:00 to 01:59:59 do not exist.
    # candidate 2027-03-28 00:30 local -> pushed past 1 to end of sleep -> 01:00 local ... wait.
    # The prompt says: "Spring-forward gap: Europe/London sleep 22->1, candidate 2027-03-28 00:30 local -> result 01:00 UTC (02:00 BST), no exception."
    # The gap is local 01:00 to 02:00. End of sleep is 1. If it replaces hour=1, minute=0, that is inside the gap.
    # astimezone(timezone.utc) will convert the non-existent time 01:00 local to 01:00 UTC (which is 02:00 BST).
    dt_local = datetime(2027, 3, 28, 0, 30, tzinfo=tz)
    pushed = _push_past_sleep(dt_local, start=22, end=1)
    
    pushed_utc = pushed.astimezone(timezone.utc).replace(tzinfo=None)
    assert pushed_utc == datetime(2027, 3, 28, 1, 0)

def test_invalid_timezone_fallback(db_session, monkeypatch):
    uid = uuid.uuid4().hex[:8]
    fanpage = TargetFanpage(
        repliz_account_id=f"test_mars_{uid}",
        name="Test Mars",
        timezone="Mars/Olympus",
        publish_daily_limit=5,
        publish_sleep_start_hour=0,
        publish_sleep_end_hour=6
    )
    db_session.add(fanpage)
    db_session.commit()

    tz = zoneinfo.ZoneInfo("Asia/Jakarta")
    mock_now = datetime(2026, 10, 20, 10, 0, tzinfo=tz).astimezone(timezone.utc)

    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz == timezone.utc:
                return mock_now
            return super().now(tz)
    
    monkeypatch.setattr("app.tasks.publisher.datetime", MockDatetime)

    # Should fall back to Jakarta and not raise an exception
    next_sched = _next_schedule_at(db_session, fanpage.id, breaking=True)
    next_sched_local = next_sched.replace(tzinfo=timezone.utc).astimezone(tz)
    
    assert next_sched_local.hour == 10
    assert next_sched_local.minute == 1
