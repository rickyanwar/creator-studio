from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from app.services.ig_viewer_health import (
    TIERS, HEALTH_TTL_SECONDS, HealthUnavailableError, _UPDATE_LUA,
    ALERT_RECENT_HOURS,
    get_health, is_unhealthy, record_tier_result,
)

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.sets = {}
        self.ttls = {}
        self.scripts = []

    def eval(self, script, count, key, users, action, timestamp, username, error, kind, ttl):
        assert count == 2
        self.scripts.append(script)
        if action == "success":
            self.hset(key, {"last_success_at": timestamp, "consecutive_failures": 0,
                            "streak_started_at": ""})
            self.delete(users)
        else:
            self.hashes.setdefault(key, {})["consecutive_failures"] = int(
                self.hashes.get(key, {}).get("consecutive_failures", 0)) + 1
            if not self.hashes[key].get("streak_started_at"):
                self.hashes[key]["streak_started_at"] = timestamp
            self.hset(key, {"last_failure_at": timestamp, "last_error": error,
                            "last_error_kind": kind})
            self.sadd(users, username)
        self.ttls[key] = ttl
        if users in self.sets:
            self.ttls[users] = ttl

    @contextmanager
    def pipeline(self, transaction=False):
        assert transaction
        pending = []

        class Pipeline:
            def hgetall(self, key):
                pending.append(("hgetall", key))

            def scard(self, key):
                pending.append(("scard", key))

            def execute(self):
                return [getattr(self_redis, method)(key) for method, key in pending]

        self_redis = self
        yield Pipeline()

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    def hgetall(self, key):
        return self.hashes.get(key, {}).copy()

    def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)

    def scard(self, key):
        return len(self.sets.get(key, set()))

    def delete(self, key):
        self.sets.pop(key, None)
        self.hashes.pop(key, None)
        self.ttls.pop(key, None)


def test_streak_and_reset():
    r = FakeRedis()
    earlier = NOW - timedelta(minutes=60)
    record_tier_result("gramsnap", "alice", False, "script", "x" * 400,
                       redis_client=r, now=earlier)
    record_tier_result("gramsnap", "alice", False, "blocked", "challenge",
                       redis_client=r, now=NOW)
    record_tier_result("gramsnap", "bob", False, "script", "selector",
                       redis_client=r, now=NOW)
    state = get_health(redis_client=r, now=NOW)["tiers"]["gramsnap"]
    assert state["consecutive_failures"] == 3
    assert state["distinct_users"] == 2
    assert state["streak_started_at"] == earlier.isoformat()
    assert state["last_failure_at"] == NOW.isoformat()
    assert len(r.hashes["ig_viewer:health:gramsnap"]["last_error"]) <= 300
    assert r.scripts and all(script == _UPDATE_LUA for script in r.scripts)
    assert "HINCRBY" in _UPDATE_LUA and "SADD" in _UPDATE_LUA
    assert r.ttls["ig_viewer:health:gramsnap"] == HEALTH_TTL_SECONDS
    assert r.ttls["ig_viewer:health:gramsnap:users"] == HEALTH_TTL_SECONDS

    record_tier_result("gramsnap", "bob", True, redis_client=r, now=NOW)
    state = get_health(redis_client=r, now=NOW)["tiers"]["gramsnap"]
    assert state["consecutive_failures"] == 0
    assert state["distinct_users"] == 0
    assert state["streak_started_at"] == ""
    assert state["last_success_at"] == NOW.isoformat()
    assert "ig_viewer:health:gramsnap:users" not in r.ttls
    record_tier_result("gramsnap", "bob", False, "script", "again", redis_client=r, now=NOW)
    assert get_health(redis_client=r, now=NOW)["tiers"]["gramsnap"]["streak_started_at"] == NOW.isoformat()


def test_alert_boundaries():
    base = {"consecutive_failures": 5, "distinct_users": 2,
            "streak_started_at": (NOW - timedelta(minutes=60)).isoformat(),
            "last_failure_at": NOW.isoformat(),
            "last_error_kind": "script"}
    assert is_unhealthy(base, NOW)
    assert is_unhealthy({**base, "last_error_kind": "blocked"}, NOW)
    assert not is_unhealthy({**base, "consecutive_failures": 4}, NOW)
    assert not is_unhealthy({**base, "distinct_users": 1}, NOW)
    assert not is_unhealthy({**base, "streak_started_at": (NOW - timedelta(minutes=59)).isoformat()}, NOW)
    assert not is_unhealthy({**base, "last_error_kind": "site_down"}, NOW)
    # stale: last failure > 6 h ago → not unhealthy
    stale_failure = (NOW - timedelta(hours=ALERT_RECENT_HOURS, seconds=1)).isoformat()
    assert not is_unhealthy({**base, "last_failure_at": stale_failure}, NOW)
    # boundary: exactly 6 h ago → still unhealthy
    boundary_failure = (NOW - timedelta(hours=ALERT_RECENT_HOURS)).isoformat()
    assert is_unhealthy({**base, "last_failure_at": boundary_failure}, NOW)
    # missing last_failure_at → not unhealthy
    assert not is_unhealthy({**base, "last_failure_at": ""}, NOW)


def test_recent_hours_stale_streak_not_unhealthy():
    """Streak with last_failure 7 h ago is NOT unhealthy (stale)."""
    base = {"consecutive_failures": 5, "distinct_users": 2,
            "streak_started_at": (NOW - timedelta(hours=8)).isoformat(),
            "last_failure_at": (NOW - timedelta(hours=7)).isoformat(),
            "last_error_kind": "script"}
    assert not is_unhealthy(base, NOW)


def test_recent_hours_fresh_streak_unhealthy():
    """Streak with last_failure 5 h ago IS unhealthy."""
    base = {"consecutive_failures": 5, "distinct_users": 2,
            "streak_started_at": (NOW - timedelta(hours=6)).isoformat(),
            "last_failure_at": (NOW - timedelta(hours=5)).isoformat(),
            "last_error_kind": "script"}
    assert is_unhealthy(base, NOW)


def test_all_tiers_down():
    r = FakeRedis()
    for tier in TIERS:
        for _ in range(3):
            record_tier_result(tier, "alice", False, "site_down", "503", redis_client=r, now=NOW)
    health = get_health(redis_client=r, now=NOW)
    assert health["all_tiers_down"]
    assert not any(s["unhealthy"] for s in health["tiers"].values())
    record_tier_result("anonyig", "alice", True, redis_client=r, now=NOW)
    assert not get_health(redis_client=r, now=NOW)["all_tiers_down"]


def test_recorded_streak_becomes_unhealthy_at_boundary():
    r = FakeRedis()
    for i in range(5):
        record_tier_result("gramsnap", "alice" if i < 4 else "bob", False,
                           "blocked", "challenge", redis_client=r,
                           now=NOW - timedelta(minutes=60) if i == 0 else NOW)
    health = get_health(redis_client=r, now=NOW)
    assert health["tiers"]["gramsnap"]["unhealthy"]
    assert not health["all_tiers_down"]


def test_redis_errors_swallowed_and_logged(caplog):
    class BrokenRedis:
        def pipeline(self, **kwargs):
            raise ConnectionError("offline")

        def eval(self, *args):
            raise ConnectionError("offline")

    r = BrokenRedis()
    record_tier_result("gramsnap", "alice", False, "script", "x", redis_client=r, now=NOW)
    record_tier_result("gramsnap", "alice", True, redis_client=r, now=NOW)
    assert not get_health(redis_client=r, now=NOW)["all_tiers_down"]
    assert "ConnectionError" in caplog.text
    assert "offline" not in caplog.text
    with pytest.raises(HealthUnavailableError):
        get_health(redis_client=r, now=NOW, strict=True)


def test_strict_read_rejects_partial_pipeline_results():
    class BrokenPipeline(FakeRedis):
        @contextmanager
        def pipeline(self, transaction=False):
            class Pipeline:
                def hgetall(self, key):
                    pass

                def scard(self, key):
                    pass

                def execute(self):
                    return [{}]

            yield Pipeline()

    with pytest.raises(HealthUnavailableError):
        get_health(redis_client=BrokenPipeline(), now=NOW, strict=True)


def test_invalid_inputs_do_not_write(caplog):
    r = FakeRedis()
    record_tier_result("unknown", "alice", False, "script", redis_client=r, now=NOW)
    record_tier_result("gramsnap", "alice", False, "bogus", redis_client=r, now=NOW)
    record_tier_result("gramsnap", "alice", True, "bogus", redis_client=r, now=NOW)
    assert not r.scripts
    assert "ignored invalid" in caplog.text


def test_naive_datetime_is_utc():
    r = FakeRedis()
    naive = NOW.replace(tzinfo=None)
    record_tier_result("gramsnap", "alice", False, "script", redis_client=r, now=naive)
    assert get_health(redis_client=r, now=naive)["generated_at"] == NOW.isoformat()
    assert r.hashes["ig_viewer:health:gramsnap"]["streak_started_at"] == NOW.isoformat()


def test_redis_timeout_options():
    from app.services.ig_viewer_health import _redis, REDIS_TIMEOUT_SECONDS
    with patch("redis.from_url") as factory, patch("app.config.get_settings") as settings:
        settings.return_value.redis_url = "redis://localhost"
        _redis(None)
    factory.assert_called_once_with("redis://localhost", decode_responses=True,
                                    socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
                                    socket_timeout=REDIS_TIMEOUT_SECONDS)
