"""Tests for Codex budget service (backend/app/services/codex_budget.py)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.settings import Settings
from app.services import codex_budget
from app.services.codex_budget import (
    CodexUsage,
    codex_resting,
    estimate_usage,
    get_codex_usage,
    mark_codex_rest,
    mark_codex_used,
    parse_usage_payload,
    wait_night_gap,
)


class FakeRedis:
    def __init__(self):
        self._data: dict[str, str] = {}
        self._ttl: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._data[key] = str(value)
        if ex is not None:
            self._ttl[key] = ex
        return True

    def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._data:
                del self._data[k]
                self._ttl.pop(k, None)
                count += 1
        return count


class FailingRedis:
    def get(self, key: str):
        raise ConnectionError("Redis connection refused on get")

    def set(self, key: str, value: str, ex: int | None = None):
        raise ConnectionError("Redis connection refused on set")


def _make_db(settings_row=None, event_count: int = 0):
    db = MagicMock()
    query_mock = MagicMock()
    query_mock.first.return_value = settings_row
    query_mock.filter.return_value.count.return_value = event_count
    db.query.return_value = query_mock
    return db


def _make_settings(**kwargs):
    row = MagicMock(spec=Settings)
    defaults = {
        "codex_window_capacity_est": 60,
        "codex_rest_pct": 95,
        "codex_night_min_gap_s": 240,
        "codex_night_gap_jitter_s": 60,
        "codex_usage_url": None,
    }
    defaults.update(kwargs)
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


# ── Test 1: estimate_usage ───────────────────────────────────────────────────

def test_estimate_usage_cap_and_ratio():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    settings = _make_settings(codex_window_capacity_est=60)

    # 30 events with capacity 60 -> 50.0
    db = _make_db(settings, event_count=30)
    u1 = estimate_usage(db, now)
    assert u1.window_pct == 50.0
    assert u1.weekly_pct is None
    assert u1.resets_at is None
    assert u1.source == "estimate"

    # 120 events with capacity 60 -> 100.0 cap
    db = _make_db(settings, event_count=120)
    u2 = estimate_usage(db, now)
    assert u2.window_pct == 100.0
    assert u2.source == "estimate"


def test_estimate_usage_counts_unknown_as_full_when_the_event_query_fails():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    db = _make_db(_make_settings(codex_window_capacity_est=60), event_count=0)
    db.query.return_value.filter.return_value.count.side_effect = RuntimeError("db down")

    assert estimate_usage(db, now).window_pct == 100.0


# ── Test 2: empty codex_usage_url ───────────────────────────────────────────

def test_empty_codex_usage_url_falls_back_to_estimate():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    settings = _make_settings(codex_usage_url=None, codex_window_capacity_est=60)
    db = _make_db(settings, event_count=30)

    with patch("httpx.get") as mock_get:
        u = get_codex_usage(db, now)
        mock_get.assert_not_called()
        assert u.source == "estimate"
        assert u.window_pct == 50.0

    settings_empty_str = _make_settings(codex_usage_url="   ", codex_window_capacity_est=60)
    db_empty = _make_db(settings_empty_str, event_count=30)
    with patch("httpx.get") as mock_get:
        u = get_codex_usage(db_empty, now)
        mock_get.assert_not_called()
        assert u.source == "estimate"


# ── Test 3: URL source success and caching ───────────────────────────────────

def test_url_source_success_and_caching():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    fake_redis = FakeRedis()
    settings = _make_settings(
        codex_usage_url="https://quota.example.com/usage",
        codex_window_capacity_est=60,
    )
    db = _make_db(settings, event_count=30)

    fixture = {
        "rate_limit": {
            "primary_window": {
                "used_percent": 42,
                "reset_at": 1792250000,
            },
            "secondary_window": {
                "used_percent": 18,
            },
        }
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fixture

    cfg_mock = MagicMock()
    cfg_mock.api_key = "test-secret-key"

    with patch("app.services.codex_budget._redis", return_value=fake_redis), \
         patch("app.services.codex_budget.get_nine_router_config", return_value=cfg_mock), \
         patch("httpx.get", return_value=mock_resp) as mock_get:

        # First call: hits httpx.get
        u1 = get_codex_usage(db, now)
        assert u1.window_pct == 42.0
        assert u1.weekly_pct == 18.0
        assert u1.resets_at == datetime.fromtimestamp(1792250000, tz=timezone.utc)
        assert u1.source == "9router"
        assert mock_get.call_count == 1
        mock_get.assert_called_with(
            "https://quota.example.com/usage",
            headers={"Authorization": "Bearer test-secret-key"},
            timeout=10,
        )

        # Second call within 60 s uses Redis cache: httpx.get called once total
        u2 = get_codex_usage(db, now)
        assert u2.window_pct == 42.0
        assert u2.weekly_pct == 18.0
        assert u2.resets_at == u1.resets_at
        assert u2.source == "9router"
        assert mock_get.call_count == 1


# ── Test 4: URL failures fallback to estimate ────────────────────────────────

def test_url_failures_fallback_to_estimate():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    fake_redis = FakeRedis()
    settings = _make_settings(
        codex_usage_url="https://quota.example.com/usage",
        codex_window_capacity_est=60,
    )
    db = _make_db(settings, event_count=15)  # 15 / 60 = 25.0%

    # 4a: HTTP 500
    mock_resp_500 = MagicMock()
    mock_resp_500.status_code = 500
    with patch("app.services.codex_budget._redis", return_value=fake_redis), \
         patch("httpx.get", return_value=mock_resp_500):
        u = get_codex_usage(db, now)
        assert u.source == "estimate"
        assert u.window_pct == 25.0

    # 4b: Invalid JSON
    mock_resp_bad_json = MagicMock()
    mock_resp_bad_json.status_code = 200
    mock_resp_bad_json.json.side_effect = ValueError("Invalid JSON body")
    with patch("app.services.codex_budget._redis", return_value=fake_redis), \
         patch("httpx.get", return_value=mock_resp_bad_json):
        u = get_codex_usage(db, now)
        assert u.source == "estimate"
        assert u.window_pct == 25.0

    # 4c: Unknown shape (parse_usage_payload returns None)
    mock_resp_unknown = MagicMock()
    mock_resp_unknown.status_code = 200
    mock_resp_unknown.json.return_value = {"random_key": "random_value"}
    with patch("app.services.codex_budget._redis", return_value=fake_redis), \
         patch("httpx.get", return_value=mock_resp_unknown):
        u = get_codex_usage(db, now)
        assert u.source == "estimate"
        assert u.window_pct == 25.0


# ── Test 5: nested usage shape parses ────────────────────────────────────────

def test_nested_usage_shape_parses():
    nested_fixture = {
        "usage": {
            "rate_limits": {
                "primary_window": {
                    "used_percent": 35.5,
                    "reset_at": 1792250000,
                },
                "secondary_window": {
                    "used_percent": 12.0,
                },
            }
        }
    }
    u = parse_usage_payload(nested_fixture)
    assert u is not None
    assert u.window_pct == 35.5
    assert u.weekly_pct == 12.0
    assert u.resets_at == datetime.fromtimestamp(1792250000, tz=timezone.utc)
    assert u.source == "9router"


def test_parse_usage_payload_reset_after_seconds():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    fixture = {
        "rate_limit": {
            "primary_window": {
                "used_percent": 75.0,
                "reset_after_seconds": 1800,
            }
        }
    }
    u = parse_usage_payload(fixture, now=now)
    assert u is not None
    assert u.window_pct == 75.0
    assert u.weekly_pct is None
    assert u.resets_at == now + timedelta(seconds=1800)


def test_parse_usage_payload_invalid_inputs():
    assert parse_usage_payload({}) is None
    assert parse_usage_payload({"rate_limit": {}}) is None
    assert parse_usage_payload({"rate_limit": {"primary_window": {"used_percent": "bad"}}}) is None
    assert parse_usage_payload({"rate_limit": {"primary_window": {"used_percent": True}}}) is None


# ── Test 6: codex_resting ───────────────────────────────────────────────────

def test_codex_resting():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    now_epoch = now.timestamp()
    fake_redis = FakeRedis()

    settings = _make_settings(
        codex_rest_pct=95,
        codex_usage_url=None,
        codex_window_capacity_est=100,
    )
    db = _make_db(settings, event_count=50)

    # 6a: Rest key in the future -> True
    fake_redis.set("codex:rest_until", str(now_epoch + 300))
    with patch("app.services.codex_budget._redis", return_value=fake_redis):
        assert codex_resting(db, now) is True

    # 6b: Rest key cleared, pct >= rest_pct (96% >= 95%) -> True and rest key is set
    fake_redis.delete("codex:rest_until")
    db_high = _make_db(settings, event_count=96)
    with patch("app.services.codex_budget._redis", return_value=fake_redis):
        assert codex_resting(db_high, now) is True
        stored_rest = fake_redis.get("codex:rest_until")
        assert stored_rest is not None
        assert float(stored_rest) > now_epoch

    # 6c: Rest key cleared, pct below rest_pct (50% < 95%) -> False
    fake_redis.delete("codex:rest_until")
    db_low = _make_db(settings, event_count=50)
    with patch("app.services.codex_budget._redis", return_value=fake_redis):
        assert codex_resting(db_low, now) is False
        assert fake_redis.get("codex:rest_until") is None


# ── Test 7: wait_night_gap ───────────────────────────────────────────────────

def test_wait_night_gap():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    now_epoch = now.timestamp()
    fake_redis = FakeRedis()
    settings = _make_settings(
        codex_night_min_gap_s=240,
        codex_night_gap_jitter_s=60,
    )
    db = _make_db(settings)
    mock_sleep = MagicMock()

    # 7a: No last_at -> 0 and sleep not called
    with patch("app.services.codex_budget._redis", return_value=fake_redis):
        slept = wait_night_gap(db, now, sleep=mock_sleep, rand=lambda a, b: 0)
        assert slept == 0.0
        mock_sleep.assert_not_called()

    # 7b: Last 60 s ago, min_gap 240, rand -> 0 -> sleeps 180
    fake_redis.set("codex:last_at", str(now_epoch - 60))
    with patch("app.services.codex_budget._redis", return_value=fake_redis):
        slept = wait_night_gap(db, now, sleep=mock_sleep, rand=lambda a, b: 0)
        assert slept == 180.0
        mock_sleep.assert_called_once_with(180.0)

    # 7c: Last 400 s ago -> 0
    mock_sleep.reset_mock()
    fake_redis.set("codex:last_at", str(now_epoch - 400))
    with patch("app.services.codex_budget._redis", return_value=fake_redis):
        slept = wait_night_gap(db, now, sleep=mock_sleep, rand=lambda a, b: 0)
        assert slept == 0.0
        mock_sleep.assert_not_called()

    # 7d: Sleep is capped at min_gap + jitter (240 + 60 = 300)
    mock_sleep.reset_mock()
    fake_redis.set("codex:last_at", str(now_epoch + 100))
    with patch("app.services.codex_budget._redis", return_value=fake_redis):
        slept = wait_night_gap(db, now, sleep=mock_sleep, rand=lambda a, b: 0)
        assert slept == 300.0
        mock_sleep.assert_called_once_with(300.0)


# ── Test 8: Redis raising does not raise out ─────────────────────────────────

def test_redis_errors_do_not_raise():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    failing_redis = FailingRedis()
    settings = _make_settings(
        codex_window_capacity_est=60,
        codex_rest_pct=95,
        codex_night_min_gap_s=240,
        codex_night_gap_jitter_s=60,
    )
    db = _make_db(settings, event_count=30)

    # When Redis client methods raise:
    with patch("app.services.codex_budget._redis", return_value=failing_redis):
        mark_codex_used(now)
        mark_codex_rest(now + timedelta(hours=1))
        slept = wait_night_gap(db, now)
        assert slept == 0.0
        u = get_codex_usage(db, now)
        assert u.source == "estimate"
        resting = codex_resting(db, now)
        assert resting is False  # 30/60 = 50% < 95%

    # When _redis() itself raises:
    with patch("app.services.codex_budget._redis", side_effect=ConnectionError("Cannot reach Redis")):
        mark_codex_used(now)
        mark_codex_rest(now + timedelta(hours=1))
        slept = wait_night_gap(db, now)
        assert slept == 0.0
        u = get_codex_usage(db, now)
        assert u.source == "estimate"
        resting = codex_resting(db, now)
        assert resting is False
