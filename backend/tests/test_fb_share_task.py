"""Tick isolation and Celery registration contracts."""

import sys
from types import ModuleType
from unittest.mock import Mock

import pytest

from app.tasks import fb_share
from app.tasks.celery_app import celery_app


class FakeRedis:
    def __init__(self):
        self.token = None

    def set(self, key, token, *, nx, ex):
        assert key == "fb_share:tick" and nx is True and ex == 900
        if self.token is not None:
            return False
        self.token = token
        return True

    def eval(self, script, count, key, token):
        assert "get" in script and "del" in script
        assert count == 1 and key == "fb_share:tick"
        if self.token == token:
            self.token = None
            return 1
        return 0


@pytest.fixture(autouse=True)
def redis_lock(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(fb_share, "_redis", lambda: client)
    return client


def test_failing_brand_does_not_stop_next(monkeypatch):
    feeder = ModuleType("app.services.fb_share.feeder")
    classify = ModuleType("app.services.fb_share.classify")
    feeder.collect_published_items = Mock(side_effect=[RuntimeError("bad brand"), 1])
    classify.classify_pending_items = Mock(return_value=1)
    from app.services import fb_share as package
    monkeypatch.setitem(sys.modules, feeder.__name__, feeder)
    monkeypatch.setitem(sys.modules, classify.__name__, classify)
    monkeypatch.setattr(package, "feeder", feeder, raising=False)
    monkeypatch.setattr(package, "classify", classify, raising=False)
    brands = [Mock(id=1), Mock(id=2)]
    db = Mock()
    monkeypatch.setattr(fb_share, "enabled_fanpage_ids", Mock(return_value=[1, 2]))
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = brands
    db.get.side_effect = brands
    monkeypatch.setattr(fb_share, "SessionLocal", lambda: db)
    plan = Mock()
    dispatch = Mock()
    client = Mock()
    monkeypatch.setattr(fb_share, "plan_brand", plan)
    monkeypatch.setattr(fb_share, "dispatch_due", dispatch)
    from app.services import phone_runner_client
    monkeypatch.setattr(phone_runner_client, "PhoneRunnerClient", lambda: client)
    fb_share.fb_share_tick.run()
    fb_share.enabled_fanpage_ids.assert_called_once_with(db)
    assert feeder.collect_published_items.call_count == 2
    assert classify.classify_pending_items.call_args.args == (db, brands[1])
    assert plan.call_args.args[:2] == (db, brands[1])
    assert dispatch.call_args.args[2] is client
    db.rollback.assert_called_once()
    db.close.assert_called_once()


def test_registration():
    assert "app.tasks.fb_share" in celery_app.conf.include
    assert celery_app.conf.beat_schedule["fb-share-tick"] == {
        "task": "app.tasks.fb_share.fb_share_tick", "schedule": 300,
    }
    assert celery_app.conf.task_routes["app.tasks.fb_share.*"] == {"queue": "ai"}
    assert fb_share.fb_share_tick.soft_time_limit == 600
    assert fb_share.fb_share_tick.time_limit == 660


def test_concurrent_tick_returns_without_work(monkeypatch, redis_lock):
    db = Mock()
    monkeypatch.setattr(fb_share, "enabled_fanpage_ids", Mock(return_value=[]))
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    sessions = Mock(return_value=db)
    monkeypatch.setattr(fb_share, "SessionLocal", sessions)

    def dispatch(*args):
        assert redis_lock.token is not None
        fb_share.fb_share_tick.run()
        sessions.assert_called_once()

    monkeypatch.setattr(fb_share, "dispatch_due", dispatch)
    fb_share.fb_share_tick.run()
    assert redis_lock.token is None
    db.close.assert_called_once()


@pytest.mark.parametrize("replace_token", [False, True])
def test_lock_release_on_failure_preserves_other_owner(monkeypatch, redis_lock, replace_token):
    def session():
        if replace_token:
            redis_lock.token = "other-owner"
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(fb_share, "SessionLocal", session)
    with pytest.raises(RuntimeError):
        fb_share.fb_share_tick.run()
    assert redis_lock.token == ("other-owner" if replace_token else None)
