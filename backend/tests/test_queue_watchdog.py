from unittest.mock import MagicMock
import pytest
import redis

from app.tasks import queue_watchdog
from app.tasks.queue_watchdog import check_queue_backlog


class FakeRedis:
    def __init__(self, llen_data=None):
        self.llen_data = llen_data or {}
        self.keys = {}
        self.failing_queues = set()

    def llen(self, queue):
        if queue in self.failing_queues:
            raise redis.RedisError(f"Simulated error for queue {queue}")
        return self.llen_data.get(queue, 0)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys[key] = (value, ex)
        return True


@pytest.fixture
def mock_env(monkeypatch):
    messages = []
    fake_redis = FakeRedis()

    monkeypatch.setattr(queue_watchdog.redis, "from_url", lambda *args, **kwargs: fake_redis)

    mock_db = MagicMock()
    monkeypatch.setattr(queue_watchdog, "SessionLocal", lambda: mock_db)

    def fake_send_telegram_fallback(message, db):
        messages.append(message)
        return True

    monkeypatch.setattr(queue_watchdog, "send_telegram_fallback", fake_send_telegram_fallback)

    return fake_redis, messages, mock_db


def test_all_queues_under_limit(mock_env):
    fake_redis, messages, _ = mock_env
    fake_redis.llen_data = {"celery": 20, "crawl": 10, "visual": 5, "video": 2, "gallery": 50}

    res = check_queue_backlog()
    assert res == {}
    assert len(messages) == 0


def test_celery_over_limit_sends_alert(mock_env):
    fake_redis, messages, mock_db = mock_env
    fake_redis.llen_data = {"celery": 1609}

    res = check_queue_backlog()
    assert res == {"celery": 1609}
    assert len(messages) == 1

    msg = messages[0]
    assert "'celery'" in msg
    assert "1609" in msg
    assert "worker" in msg
    mock_db.close.assert_called_once()


def test_cooldown_suppresses_subsequent_alerts(mock_env):
    fake_redis, messages, _ = mock_env
    fake_redis.llen_data = {"celery": 1609}

    res1 = check_queue_backlog()
    assert res1 == {"celery": 1609}
    assert len(messages) == 1

    res2 = check_queue_backlog()
    assert res2 == {"celery": 1609}
    assert len(messages) == 1  # No second message sent within cooldown


def test_llen_raising_skips_queue_and_checks_others(mock_env):
    fake_redis, messages, _ = mock_env
    fake_redis.llen_data = {"celery": 1609, "crawl": 200}
    fake_redis.failing_queues.add("crawl")

    res = check_queue_backlog()
    assert "crawl" not in res
    assert res.get("celery") == 1609
    assert len(messages) == 1
    assert "'celery'" in messages[0]


def test_from_url_raising_returns_empty_and_does_not_raise(monkeypatch):
    monkeypatch.setattr(
        queue_watchdog.redis,
        "from_url",
        MagicMock(side_effect=redis.ConnectionError("Redis connection refused")),
    )

    res = check_queue_backlog()
    assert res == {}
