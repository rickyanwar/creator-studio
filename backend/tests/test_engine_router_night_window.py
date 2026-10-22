from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.settings import Settings
from app.services.codex_budget import CodexUsage
from app.services.engine_router import WIB, is_night, plan_engines


def test_is_night_default_night_start():
    # night_start "00:00" / day_start "05:30"
    t_0030 = datetime(2026, 10, 18, 0, 30, tzinfo=WIB)
    t_0529 = datetime(2026, 10, 18, 5, 29, tzinfo=WIB)
    t_0530 = datetime(2026, 10, 18, 5, 30, tzinfo=WIB)
    t_2359 = datetime(2026, 10, 18, 23, 59, tzinfo=WIB)

    assert is_night(t_0030, "05:30", "00:00") is True
    assert is_night(t_0529, "05:30", "00:00") is True
    assert is_night(t_0530, "05:30", "00:00") is False
    assert is_night(t_2359, "05:30", "00:00") is False


def test_is_night_custom_night_start_0200():
    # night_start "02:00" / day_start "05:30"
    t_0159 = datetime(2026, 10, 18, 1, 59, tzinfo=WIB)
    t_0200 = datetime(2026, 10, 18, 2, 0, tzinfo=WIB)
    t_0529 = datetime(2026, 10, 18, 5, 29, tzinfo=WIB)
    t_0530 = datetime(2026, 10, 18, 5, 30, tzinfo=WIB)

    assert is_night(t_0159, "05:30", "02:00") is False
    assert is_night(t_0200, "05:30", "02:00") is True
    assert is_night(t_0529, "05:30", "02:00") is True
    assert is_night(t_0530, "05:30", "02:00") is False


def test_is_night_wrap_midnight():
    # wrap "23:00"–"05:30"
    t_2330 = datetime(2026, 10, 18, 23, 30, tzinfo=WIB)
    t_0300 = datetime(2026, 10, 18, 3, 0, tzinfo=WIB)
    t_1200 = datetime(2026, 10, 18, 12, 0, tzinfo=WIB)

    assert is_night(t_2330, "05:30", "23:00") is True
    assert is_night(t_0300, "05:30", "23:00") is True
    assert is_night(t_1200, "05:30", "23:00") is False


def test_is_night_equal_start_end():
    # equal "05:30"/"05:30": always False
    t_0000 = datetime(2026, 10, 18, 0, 0, tzinfo=WIB)
    t_0530 = datetime(2026, 10, 18, 5, 30, tzinfo=WIB)
    t_1200 = datetime(2026, 10, 18, 12, 0, tzinfo=WIB)
    t_2359 = datetime(2026, 10, 18, 23, 59, tzinfo=WIB)

    assert is_night(t_0000, "05:30", "05:30") is False
    assert is_night(t_0530, "05:30", "05:30") is False
    assert is_night(t_1200, "05:30", "05:30") is False
    assert is_night(t_2359, "05:30", "05:30") is False


def test_plan_engines_with_custom_night_start():
    settings = Settings(
        ai_day_start="05:30",
        ai_night_start="02:00",
        visual_engine_daily_max=60,
        codex_share_percent=35,
        codex_share_window_jobs=40,
        codex_overflow_queue_len=3,
        codex_share_max_pct=60,
        codex_fallback_max_pct=85,
        codex_night_max_pct=70,
    )
    db = MagicMock()
    db.query.return_value.first.return_value = settings

    t_0100 = datetime(2026, 10, 18, 1, 0, tzinfo=WIB)
    t_0300 = datetime(2026, 10, 18, 3, 0, tzinfo=WIB)

    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(10.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=0), \
         patch("app.services.engine_router.codex_share", return_value=0.0), \
         patch("app.services.engine_router.gptcli_queue_len", return_value=0):

        # At 01:00 WIB: night_start is 02:00 -> NOT night plan
        plan_day = plan_engines(db, fanpage_id=1, now=t_0100)
        assert plan_day.reason != "night"

        # At 03:00 WIB: night_start is 02:00 -> IS night plan
        plan_night = plan_engines(db, fanpage_id=1, now=t_0300)
        assert plan_night.reason == "night"


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Settings.__table__.create(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    row = Settings(
        id=1,
        crawl_interval_minutes=30,
        max_post_age_days=2,
        ai_provider_primary="gemini",
        ai_provider_fallback="groq",
        ai_fallback_after_failures=3,
        ai_fallback_reset_after_minutes=15,
        ai_night_start="00:00",
    )
    session.add(row)
    session.commit()
    yield session
    session.close()


@pytest.fixture
def client(test_db):
    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[get_current_user] = lambda: "testuser"
    c = TestClient(app)
    yield c
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


def test_settings_api_ai_night_start_roundtrip(client, test_db):
    res = client.get("/settings")
    assert res.status_code == 200
    data = res.json()
    assert data["ai_night_start"] == "00:00"

    put_res = client.put("/settings", json={"ai_night_start": "02:00"})
    assert put_res.status_code == 200
    assert put_res.json()["ai_night_start"] == "02:00"

    test_db.expire_all()
    row = test_db.query(Settings).filter_by(id=1).first()
    assert row.ai_night_start == "02:00"

    bad_res = client.put("/settings", json={"ai_night_start": "25:00"})
    assert bad_res.status_code == 422
