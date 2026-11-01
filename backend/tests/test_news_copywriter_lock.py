from unittest.mock import MagicMock
import pytest

from app.tasks.news_copywriter import (
    _acquire_article_lock,
    _article_lock_key,
    _release_article_lock,
    copywrite_article,
)


class FakeRedis:
    def __init__(self):
        self.store = {}

    def set(self, key, val, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = val
        return True

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)


def test_article_lock_acquire_and_release(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("app.tasks.news_copywriter._redis", lambda: fake)

    assert _acquire_article_lock(42) is True
    assert _acquire_article_lock(42) is False
    _release_article_lock(42)
    assert _acquire_article_lock(42) is True


def test_article_lock_redis_unreachable(monkeypatch):
    def failing_redis():
        raise RuntimeError("Redis unreachable")

    monkeypatch.setattr("app.tasks.news_copywriter._redis", failing_redis)

    assert _acquire_article_lock(42) is True
    # release does not raise
    _release_article_lock(42)


def test_copywrite_article_skips_when_lock_held(monkeypatch):
    fake = FakeRedis()
    article_id = 100
    key = _article_lock_key(article_id)
    fake.store[key] = "1"

    monkeypatch.setattr("app.tasks.news_copywriter._redis", lambda: fake)
    mock_session_cls = MagicMock()
    monkeypatch.setattr("app.tasks.news_copywriter.SessionLocal", mock_session_cls)

    copywrite_article(article_id)

    assert not mock_session_cls.called
    assert key in fake.store


def test_copywrite_article_releases_lock_on_early_return(monkeypatch):
    fake = FakeRedis()
    article_id = 100
    key = _article_lock_key(article_id)

    monkeypatch.setattr("app.tasks.news_copywriter._redis", lambda: fake)
    mock_db = MagicMock()
    mock_db.query().filter_by().first.return_value = None
    mock_session_cls = MagicMock(return_value=mock_db)
    monkeypatch.setattr("app.tasks.news_copywriter.SessionLocal", mock_session_cls)

    copywrite_article(article_id)

    assert mock_session_cls.called
    assert mock_db.close.called
    assert key not in fake.store


def test_copywrite_article_releases_lock_on_exception(monkeypatch):
    fake = FakeRedis()
    article_id = 100
    key = _article_lock_key(article_id)

    monkeypatch.setattr("app.tasks.news_copywriter._redis", lambda: fake)
    mock_db = MagicMock()
    mock_db.query().filter_by().first.side_effect = RuntimeError("DB exploded")
    mock_session_cls = MagicMock(return_value=mock_db)
    monkeypatch.setattr("app.tasks.news_copywriter.SessionLocal", mock_session_cls)

    with pytest.raises(Exception):
        copywrite_article(article_id)

    assert mock_session_cls.called
    assert mock_db.close.called
    assert key not in fake.store
