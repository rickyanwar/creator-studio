"""Tests for engine router (app.services.engine_router)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.ai_copy_events import AICopyEvent
from app.models.settings import Settings
from app.services.codex_budget import CodexUsage
from app.services.engine_router import (
    CODEX_OVERFLOW_MAX_PCT,
    WIB,
    EnginePlan,
    codex_allowed,
    codex_daily_used,
    codex_share,
    gptcli_queue_len,
    is_night,
    parse_hhmm,
    plan_engines,
    render_queue_for,
    today_day_start,
    visual_queue_len,
)
from app.services.gptcli_client import GptcliConfig

NIGHT_TIME = datetime(2026, 10, 18, 3, 0, tzinfo=WIB)   # Sunday 03:00 WIB
DAY_TIME = datetime(2026, 10, 18, 14, 0, tzinfo=WIB)    # Sunday 14:00 WIB


@pytest.fixture
def mock_settings():
    return Settings(
        ai_day_start="05:30",
        visual_engine_daily_max=60,
        codex_share_percent=35,
        codex_share_window_jobs=40,
        codex_overflow_queue_len=3,
        codex_share_max_pct=60,
        codex_fallback_max_pct=85,
        codex_night_max_pct=70,
    )


@pytest.fixture
def mock_db(mock_settings):
    db = MagicMock()
    db.query.return_value.first.return_value = mock_settings
    return db


# -------------------------------------------------------------------------
# Helper tests
# -------------------------------------------------------------------------


def test_parse_hhmm():
    assert parse_hhmm("05:30") == (5, 30)
    assert parse_hhmm(None) == (5, 30)
    assert parse_hhmm("invalid") == (5, 30)
    assert parse_hhmm("08:15") == (8, 15)
    assert parse_hhmm("25:00", default="06:00") == (6, 0)
    assert parse_hhmm("12:99", default="07:45") == (7, 45)


def test_is_night():
    assert is_night(NIGHT_TIME, "05:30") is True
    assert is_night(DAY_TIME, "05:30") is False
    border_start = datetime(2026, 10, 18, 5, 30, tzinfo=WIB)
    assert is_night(border_start, "05:30") is False
    midnight = datetime(2026, 10, 18, 0, 0, tzinfo=WIB)
    assert is_night(midnight, "05:30") is True


def test_today_day_start():
    ds = today_day_start(NIGHT_TIME, "05:30")
    assert ds.tzinfo == timezone.utc
    assert ds == datetime(2026, 10, 18, 5, 30, tzinfo=WIB).astimezone(timezone.utc)


def test_visual_queue_len():
    with patch("app.services.engine_router.redis.from_url") as mock_r:
        mock_r.return_value.llen.return_value = 5
        assert visual_queue_len() == 5

    with patch("app.services.engine_router.redis.from_url", side_effect=Exception("Redis down")):
        assert visual_queue_len() == 0


def test_gptcli_queue_len_health_and_running_profile(mock_db):
    """queue_len + 1 when any profile has running=True."""
    mock_r = MagicMock()
    mock_r.get.return_value = None

    health_running = {
        "queue_len": 2,
        "profiles": {
            "utama": {"running": True},
            "cadangan": {"running": False},
        },
    }
    with patch("app.services.engine_router.redis.from_url", return_value=mock_r), \
         patch("app.services.gptcli_client.serve_health", return_value=health_running):
        assert gptcli_queue_len(mock_db) == 3
        mock_r.set.assert_called_once_with("gptcli:queue_len_cache", "3", ex=30)

    # Not running -> queue_len as-is
    mock_r.reset_mock()
    mock_r.get.return_value = None
    health_idle = {
        "queue_len": 2,
        "profiles": {
            "utama": {"running": False},
        },
    }
    with patch("app.services.engine_router.redis.from_url", return_value=mock_r), \
         patch("app.services.gptcli_client.serve_health", return_value=health_idle):
        assert gptcli_queue_len(mock_db) == 2
        mock_r.set.assert_called_once_with("gptcli:queue_len_cache", "2", ex=30)


def test_gptcli_queue_len_serve_health_none_treated_as_zero(mock_db):
    """serve_health None -> treated as 0."""
    mock_r = MagicMock()
    mock_r.get.return_value = None

    with patch("app.services.engine_router.redis.from_url", return_value=mock_r), \
         patch("app.services.gptcli_client.serve_health", return_value=None):
        assert gptcli_queue_len(mock_db) == 0


def test_gptcli_queue_len_redis_down_treated_as_zero(mock_db):
    """Redis down -> returns 0, does not raise."""
    with patch("app.services.engine_router.redis.from_url", side_effect=Exception("Redis down")):
        assert gptcli_queue_len(mock_db) == 0


def test_gptcli_queue_len_cache_avoids_serve_health_call(mock_db):
    """Within 30 s cache hit returns cached int and does not call serve_health."""
    mock_r = MagicMock()
    mock_r.get.return_value = "4"

    with patch("app.services.engine_router.redis.from_url", return_value=mock_r), \
         patch("app.services.gptcli_client.serve_health") as mock_health:
        assert gptcli_queue_len(mock_db) == 4
        mock_health.assert_not_called()
        mock_r.get.assert_called_once_with("gptcli:queue_len_cache")


def test_gptcli_queue_len_bad_data_treated_as_zero(mock_db):
    """Bad data in serve_health -> treated as 0 without raising."""
    mock_r = MagicMock()
    mock_r.get.return_value = None

    for bad in ["not-a-dict", {"queue_len": "corrupt"}, {"queue_len": None}]:
        with patch("app.services.engine_router.redis.from_url", return_value=mock_r), \
             patch("app.services.gptcli_client.serve_health", return_value=bad):
            assert gptcli_queue_len(mock_db) == 0


def test_codex_allowed_cases(mock_db, mock_settings):
    with patch("app.services.engine_router.codex_resting", return_value=True):
        ok, reason = codex_allowed(mock_db, DAY_TIME, night=False, settings=mock_settings)
        assert not ok
        assert reason == "codex_resting"

    with patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(75.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=0):
        ok, reason = codex_allowed(mock_db, NIGHT_TIME, night=True, settings=mock_settings)
        assert not ok
        assert reason == "codex_pct 75>=70"

    with patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(90.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=0):
        ok, reason = codex_allowed(mock_db, DAY_TIME, night=False, settings=mock_settings)
        assert not ok
        assert reason == "codex_pct 90>=85"

    with patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(50.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=60):
        ok, reason = codex_allowed(mock_db, DAY_TIME, night=False, settings=mock_settings)
        assert not ok
        assert reason == "codex_daily_cap"

    with patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(50.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):
        ok, reason = codex_allowed(mock_db, DAY_TIME, night=False, settings=mock_settings)
        assert ok
        assert reason == "ok"


# -------------------------------------------------------------------------
# Required 14 Tests
# -------------------------------------------------------------------------


def test_1_gptcli_off_does_not_touch_budget_or_redis(mock_db):
    """1. gpt-cli off -> ("9router",) reason gptcli_off, and get_codex_usage/visual_queue_len/codex_resting NOT called."""
    with patch("app.services.engine_router.get_gptcli_config") as mock_cfg, \
         patch("app.services.engine_router.gptcli_applies", return_value=False) as mock_applies, \
         patch("app.services.engine_router.get_codex_usage") as mock_usage, \
         patch("app.services.engine_router.visual_queue_len") as mock_queue, \
         patch("app.services.engine_router.codex_resting") as mock_resting:

        plan = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME)

        assert plan.order == ("9router",)
        assert plan.reason == "gptcli_off"
        assert plan.defer_until is None

        mock_applies.assert_called_once()
        mock_usage.assert_not_called()
        mock_queue.assert_not_called()
        mock_resting.assert_not_called()


def test_2_night_codex_ok(mock_db):
    """2. night, Codex ok -> ("9router",) night."""
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(50.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=5):

        plan = plan_engines(mock_db, fanpage_id=1, now=NIGHT_TIME)

        assert plan.order == ("9router",)
        assert plan.reason == "night"
        assert plan.defer_until is None


def test_3_night_window_pct_high_defers(mock_db):
    """3. night, window_pct 72 >= 70 -> order () and defer_until = today 05:30 WIB; with resets_at 04:00 WIB -> defer_until 04:00 WIB."""
    day_start_expected = datetime(2026, 10, 18, 5, 30, tzinfo=WIB).astimezone(timezone.utc)

    # Sub-case A: resets_at is None -> defer_until = today 05:30 WIB
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(72.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=0):

        plan = plan_engines(mock_db, fanpage_id=1, now=NIGHT_TIME)

        assert plan.order == ()
        assert plan.reason == "night"
        assert plan.defer_until == day_start_expected

    # Sub-case B: resets_at = 04:00 WIB -> defer_until = 04:00 WIB
    resets_at_0400 = datetime(2026, 10, 18, 4, 0, tzinfo=WIB).astimezone(timezone.utc)
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(72.0, None, resets_at_0400, "9router")), \
         patch("app.services.engine_router.codex_daily_used", return_value=0):

        plan = plan_engines(mock_db, fanpage_id=1, now=NIGHT_TIME)

        assert plan.order == ()
        assert plan.reason == "night"
        assert plan.defer_until == resets_at_0400


def test_4_night_daily_cap_reached_defers(mock_db):
    """4. night, daily cap reached -> defer."""
    day_start_expected = datetime(2026, 10, 18, 5, 30, tzinfo=WIB).astimezone(timezone.utc)

    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(40.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=60):

        plan = plan_engines(mock_db, fanpage_id=1, now=NIGHT_TIME)

        assert plan.order == ()
        assert plan.reason == "night"
        assert plan.defer_until == day_start_expected


def test_5_day_share_under_and_queue_overflow(mock_db):
    """5. day, share 0.10, queue 3 -> ("9router","gptcli") overflow."""
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_share", return_value=0.10), \
         patch("app.services.engine_router.visual_queue_len", return_value=3), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(40.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        plan = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME)

        assert plan.order == ("9router", "gptcli")
        assert plan.reason == "overflow"
        assert plan.defer_until is None


def test_5b_day_gptcli_queue_overflow(mock_db):
    """5b. day, visual queue 0 but gptcli queue 3 -> ("9router","gptcli") overflow."""
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_share", return_value=0.10), \
         patch("app.services.engine_router.visual_queue_len", return_value=0), \
         patch("app.services.engine_router.gptcli_queue_len", return_value=3) as mock_q, \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(40.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        plan = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME)

        assert plan.order == ("9router", "gptcli")
        assert plan.reason == "overflow"
        assert plan.defer_until is None
        mock_q.assert_called_once_with(mock_db)


def test_5c_day_queues_zero_not_overflow(mock_db):
    """5c. day, visual queue 0 and gptcli queue 0 -> not overflow (the existing behaviour)."""
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_share", return_value=0.10), \
         patch("app.services.engine_router.visual_queue_len", return_value=0), \
         patch("app.services.engine_router.gptcli_queue_len", return_value=0), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(40.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        plan = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME, rand=lambda: 0.99)

        assert plan.order == ("gptcli", "9router")
        assert plan.reason == "gptcli_first"


def test_6_day_share_exhausted_long_queue_overflow(mock_db):
    """6. day, share 0.35 (= percent) and queue 10 -> ("9router","gptcli") overflow up to 70%."""
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_share", return_value=0.35), \
         patch("app.services.engine_router.visual_queue_len", return_value=10), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(40.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        plan = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME)

        assert plan.order == ("9router", "gptcli")
        assert plan.reason == "overflow"
        assert plan.defer_until is None


def test_6b_codex_resting_skips_queue_len(mock_db):
    """Codex resting -> queue lengths are not even called."""
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_share", return_value=0.35), \
         patch("app.services.engine_router.visual_queue_len") as mock_vq, \
         patch("app.services.engine_router.gptcli_queue_len") as mock_q, \
         patch("app.services.engine_router.codex_resting", return_value=True), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(40.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        plan = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME)

        assert plan.order == ("gptcli",)
        assert plan.reason == "gptcli_first"
        mock_vq.assert_not_called()
        mock_q.assert_not_called()


def test_7_day_share_random_selection(mock_db):
    """7. day, share 0.0, rand 0.1 < 0.35 -> share; rand 0.9 -> gptcli_first."""
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_share", return_value=0.0), \
         patch("app.services.engine_router.visual_queue_len", return_value=0), \
         patch("app.services.engine_router.gptcli_queue_len", return_value=0), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(40.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        plan_share = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME, rand=lambda: 0.1)
        assert plan_share.order == ("9router", "gptcli")
        assert plan_share.reason == "share"

        plan_first = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME, rand=lambda: 0.9)
        assert plan_first.order == ("gptcli", "9router")
        assert plan_first.reason == "gptcli_first"


def test_8_day_window_pct_high_blocks_overflow(mock_db):
    """8. day, window_pct 72 >= overflow_max 70 -> gptcli_first even with a long queue."""
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_share", return_value=0.10), \
         patch("app.services.engine_router.visual_queue_len", return_value=10), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(72.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        plan = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME)

        assert plan.order == ("gptcli", "9router")
        assert plan.reason == "gptcli_first"


def test_9_day_gptcli_unavailable_and_no_engine(mock_db):
    """9. day, gpt serve unavailable -> ("9router",) gptcli_unavailable; and Codex at 86 % >= fallback 85 -> order () reason no_engine."""
    unavail_until = DAY_TIME + timedelta(hours=1)

    # Sub-case A: Codex allowed -> ("9router",), "gptcli_unavailable"
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=unavail_until), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(50.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        plan = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME)

        assert plan.order == ("9router",)
        assert plan.reason == "gptcli_unavailable"

    # Sub-case B: Codex at 86% >= fallback 85 -> order () reason no_engine
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=unavail_until), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(86.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        plan = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME)

        assert plan.order == ()
        assert plan.reason == "no_engine"


def test_10_exclude_engine_on_gptcli_first(mock_db):
    """10. exclude={"gptcli"} on gptcli_first -> ("9router",)."""
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_share", return_value=0.35), \
         patch("app.services.engine_router.visual_queue_len", return_value=0), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(40.0, None, None, "estimate")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        plan = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME, exclude={"gptcli"})

        assert plan.order == ("9router",)
        assert plan.reason == "gptcli_first"


def test_11_weekly_guard_blocks_share(mock_db):
    """11. weekly guard blocks the share."""
    # Monday 14:00 WIB -> weekday() = 0 -> threshold = (0+1)/7*100 + 15 = 29.2857%
    monday_time = datetime(2026, 10, 19, 14, 0, tzinfo=WIB)

    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_share", return_value=0.0), \
         patch("app.services.engine_router.visual_queue_len", return_value=5), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(40.0, 30.0, None, "9router")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        plan = plan_engines(mock_db, fanpage_id=1, now=monday_time)

        assert plan.order == ("gptcli", "9router")
        assert plan.reason == "gptcli_first"


def test_12_fanpage_outside_list_gptcli_off(mock_db):
    """12. fanpage outside the list (gptcli_applies False) -> gptcli_off."""
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok",
        fanpage_ids=(10, 20),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )

    with patch("app.services.engine_router.get_gptcli_config", return_value=cfg), \
         patch("app.services.engine_router.get_codex_usage") as mock_usage:

        plan = plan_engines(mock_db, fanpage_id=99, now=DAY_TIME)

        assert plan.order == ("9router",)
        assert plan.reason == "gptcli_off"
        mock_usage.assert_not_called()


def test_13_codex_share_sqlite():
    """13. codex_share with a real SQLite session: 4 events (share/9router, overflow/9router, gptcli_first/gptcli, gptcli_first/9router) -> 0.5."""
    engine = create_engine("sqlite:///:memory:")
    AICopyEvent.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    events = [
        AICopyEvent(context="image_gen", outcome="success", final_provider="9router", error_message="plan=share; ok"),
        AICopyEvent(context="image_gen", outcome="success", final_provider="9router", error_message="plan=overflow; ok"),
        AICopyEvent(context="image_gen", outcome="success", final_provider="gptcli", error_message="plan=gptcli_first; ok"),
        AICopyEvent(context="image_gen", outcome="recovered", final_provider="9router", error_message="plan=gptcli_first; ok"),
        AICopyEvent(context="image_gen", outcome="success", final_provider="9router", error_message="plan=night; ok"),
        AICopyEvent(context="news_copy", outcome="success", final_provider="9router", error_message="plan=share; ok"),
    ]
    for ev in events:
        session.add(ev)
    session.commit()

    share = codex_share(session, window_jobs=10)
    assert share == 0.5

    empty_session = sessionmaker(bind=create_engine("sqlite:///:memory:"))()
    AICopyEvent.__table__.create(empty_session.get_bind())
    assert codex_share(empty_session, window_jobs=10) == 0.0


def test_13b_codex_share_counts_bare_plan_markers():
    """visual_engine writes a bare "plan=overflow" when it has nothing else to add.
    Production 2026-10-19: these were not counted, so the Codex share stayed at 0 and
    overflow sent every daytime job to Codex while ChatGPT was never asked."""
    engine = create_engine("sqlite:///:memory:")
    AICopyEvent.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    for message, provider in [
        ("plan=overflow", "9router"),
        ("plan=overflow", "9router"),
        ("plan=gptcli_first", "gptcli"),
        ("plan=share; ok", "9router"),
        ("plan=night", "9router"),
        ("plan=overflowing", "9router"),  # not a plan marker
    ]:
        session.add(AICopyEvent(context="image_gen", outcome="success", final_provider=provider, error_message=message))
    session.commit()

    assert codex_share(session, window_jobs=10) == 0.75


def test_14_render_queue_for(mock_db):
    """14. render_queue_for: news on + applies -> 'visual'; news 'off' -> None; facebook; applies False -> None; exception -> None."""
    fanpage_news_on = SimpleNamespace(
        id=1,
        news_visual_engine="chatgpt",
        fb_visual_engine="off",
        visual_engine="off",
    )
    fanpage_news_off = SimpleNamespace(
        id=1,
        news_visual_engine="off",
        fb_visual_engine="off",
        visual_engine="off",
    )
    fanpage_fb_on = SimpleNamespace(
        id=1,
        news_visual_engine="off",
        fb_visual_engine="chatgpt",
        visual_engine="off",
    )
    fanpage_ig_on = SimpleNamespace(
        id=1,
        news_visual_engine="off",
        fb_visual_engine="off",
        visual_engine="chatgpt",
    )

    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True):
        assert render_queue_for(mock_db, fanpage_news_on, "news") == "visual"
        assert render_queue_for(mock_db, fanpage_news_off, "news") is None
        assert render_queue_for(mock_db, fanpage_fb_on, "facebook") == "visual"
        assert render_queue_for(mock_db, fanpage_ig_on, "instagram") == "visual"
        assert render_queue_for(mock_db, fanpage_news_on, "other") is None

    # applies False -> None
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=False):
        assert render_queue_for(mock_db, fanpage_news_on, "news") is None

    # exception -> None
    broken_fanpage = MagicMock()
    type(broken_fanpage).id = property(lambda self: (_ for _ in ()).throw(RuntimeError("crash")))
    assert render_queue_for(mock_db, broken_fanpage, "news") is None


# -------------------------------------------------------------------------
# Owner rule (2026-10-23): Codex overflow up to 70 % usage when queue is long
# -------------------------------------------------------------------------


def test_overflow_queue_long_usage_65_share_met_gives_overflow(mock_db):
    """queue long, usage 65 %, share already met (left 0) -> overflow (Codex first)."""
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_share", return_value=0.35), \
         patch("app.services.engine_router.visual_queue_len", return_value=5), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(65.0, None, None, "9router")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        plan = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME)
        assert plan.order == ("9router", "gptcli")
        assert plan.reason == "overflow"


def test_overflow_queue_long_usage_72_gives_gptcli_first(mock_db):
    """queue long, usage 72 % -> gptcli_first."""
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_share", return_value=0.0), \
         patch("app.services.engine_router.visual_queue_len", return_value=5), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(72.0, None, None, "9router")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        plan = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME)
        assert plan.order == ("gptcli", "9router")
        assert plan.reason == "gptcli_first"


def test_share_queue_short_usage_40_vs_55(mock_db):
    """queue short, usage 40 %, left > 0, rand()=0 -> share; usage 55 % -> gptcli_first (share stops at 50 %)."""
    mock_db.query.return_value.first.return_value.codex_share_max_pct = 50

    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_share", return_value=0.10), \
         patch("app.services.engine_router.visual_queue_len", return_value=0), \
         patch("app.services.engine_router.gptcli_queue_len", return_value=0), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        with patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(40.0, None, None, "9router")):
            plan_share = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME, rand=lambda: 0.0)
            assert plan_share.order == ("9router", "gptcli")
            assert plan_share.reason == "share"

        with patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(55.0, None, None, "9router")):
            plan_first = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME, rand=lambda: 0.0)
            assert plan_first.order == ("gptcli", "9router")
            assert plan_first.reason == "gptcli_first"


def test_overflow_queue_long_weekly_ahead_gives_gptcli_first(mock_db):
    """queue long but weekly ahead of pace -> gptcli_first."""
    monday_time = datetime(2026, 10, 19, 14, 0, tzinfo=WIB)
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_share", return_value=0.10), \
         patch("app.services.engine_router.visual_queue_len", return_value=5), \
         patch("app.services.engine_router.codex_resting", return_value=False), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(40.0, 35.0, None, "9router")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        plan = plan_engines(mock_db, fanpage_id=1, now=monday_time)
        assert plan.order == ("gptcli", "9router")
        assert plan.reason == "gptcli_first"


def test_overflow_queue_long_codex_resting_gives_gptcli_first(mock_db):
    """queue long but codex resting -> gptcli_first."""
    with patch("app.services.engine_router.get_gptcli_config"), \
         patch("app.services.engine_router.gptcli_applies", return_value=True), \
         patch("app.services.engine_router.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_share", return_value=0.10), \
         patch("app.services.engine_router.visual_queue_len") as mock_vq, \
         patch("app.services.engine_router.gptcli_queue_len") as mock_gq, \
         patch("app.services.engine_router.codex_resting", return_value=True), \
         patch("app.services.engine_router.get_codex_usage", return_value=CodexUsage(40.0, None, None, "9router")), \
         patch("app.services.engine_router.codex_daily_used", return_value=10):

        plan = plan_engines(mock_db, fanpage_id=1, now=DAY_TIME)
        assert plan.order == ("gptcli",)
        assert plan.reason == "gptcli_first"
        mock_vq.assert_not_called()
        mock_gq.assert_not_called()
