import pytest
from datetime import datetime, timezone
import zoneinfo

from app.database import Base, engine, SessionLocal
from app.models.target_fanpages import TargetFanpage
import app.scripts.set_fanpage_timezones as script

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

def test_plan_fanpage_logic():
    now_utc = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    
    # Normal -> London
    tz, c, s, e = script.plan_fanpage("Some Page", "Asia/Jakarta", 4, 14, now_utc)
    assert tz == "Europe/London"
    assert c == "GB"
    assert s == 22
    assert e == 8
    
    # "Fight Today" -> New York
    tz, c, s, e = script.plan_fanpage("FIGHT TODAY", "Asia/Jakarta", 12, 20, now_utc)
    assert tz == "America/New_York"
    assert c == "US"
    assert s == 1
    assert e == 9
    
    # "GP Weekend" -> Paris
    tz, c, s, e = script.plan_fanpage("Gp Weekend Fans", "Asia/Jakarta", 4, 14, now_utc)
    assert tz == "Europe/Paris"
    assert c == "FR"
    assert s == 23
    assert e == 9
    
    # Idempotency
    tz2, c2, s2, e2 = script.plan_fanpage("Gp Weekend Fans", tz, s, e, now_utc)
    assert (tz2, c2, s2, e2) == (tz, c, s, e)
    
    # None start/end
    tz3, c3, s3, e3 = script.plan_fanpage("Some Page", "Asia/Jakarta", None, None, now_utc)
    assert s3 is None and e3 is None
    
    # After UK switch (2026-10-25)
    now_utc_post = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
    tz4, c4, s4, e4 = script.plan_fanpage("Some Page", "Asia/Jakarta", 4, 14, now_utc_post)
    assert s4 == 21
    assert e4 == 7

def test_script_db_apply(db_session):
    row1 = TargetFanpage(
        name="Fight Today Test DB",
        repliz_account_id="script_test_1",
        timezone="Asia/Jakarta",
        target_country="ID",
        publish_sleep_start_hour=12,
        publish_sleep_end_hour=20
    )
    row2 = TargetFanpage(
        name="GP Weekend Test DB",
        repliz_account_id="script_test_2",
        timezone="Asia/Jakarta",
        target_country="ID",
        publish_sleep_start_hour=4,
        publish_sleep_end_hour=14
    )
    db_session.add_all([row1, row2])
    db_session.commit()
    
    try:
        # First run: should convert
        script.main(["--apply"])
        
        db_session.refresh(row1)
        db_session.refresh(row2)
        
        assert row1.timezone == "America/New_York"
        assert row1.target_country == "US"
        assert row1.publish_sleep_start_hour != 12
        
        assert row2.timezone == "Europe/Paris"
        assert row2.target_country == "FR"
        assert row2.publish_sleep_start_hour != 4
        
        # Save state
        r1_tz = row1.timezone
        r1_s = row1.publish_sleep_start_hour
        r2_tz = row2.timezone
        r2_s = row2.publish_sleep_start_hour
        
        # Second run: idempotent
        script.main(["--apply"])
        
        db_session.refresh(row1)
        db_session.refresh(row2)
        
        assert row1.timezone == r1_tz
        assert row1.publish_sleep_start_hour == r1_s
        assert row2.timezone == r2_tz
        assert row2.publish_sleep_start_hour == r2_s
        
    finally:
        db_session.delete(row1)
        db_session.delete(row2)
        db_session.commit()
