"""Crawl wave orchestration without external services."""

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.tasks import crawler
from app.services.ig_viewer_scraper import ViewerBusyError


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex:
            self.ttls[key] = ex
        return True

    def eval(self, script, keys, *args):
        key = args[0]
        owner = args[keys]
        if self.get(key) != owner:
            return 0
        if script == crawler._REFRESH_WAVE:
            self.ttls[key] = args[keys + 1]
        elif script == crawler._FINISH_WAVE:
            del self.values[key]
            self.values[args[1]] = args[keys + 1]
        else:
            del self.values[key]
        return 1


@pytest.fixture
def setup_wave(monkeypatch):
    r = FakeRedis()
    db = MagicMock()
    dispatch = MagicMock()
    monkeypatch.setattr(crawler, "_wave_redis", lambda: r)
    monkeypatch.setattr(crawler, "SessionLocal", lambda: db)
    monkeypatch.setattr(crawler.crawl_wave_step, "apply_async", dispatch)
    monkeypatch.setattr(crawler, "_in_sleep_window", lambda: False)
    return r, db, dispatch


def test_one_wave_ordered_nulls_first(setup_wave):
    r, db, dispatch = setup_wave
    # Query emits IDs in SQL order; assert SQL order includes NULLS FIRST and ID tie-break.
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
        SimpleNamespace(id=4), SimpleNamespace(id=2), SimpleNamespace(id=3),
    ]
    crawler.crawl_all_sources.run(manual=True)
    order = db.query.return_value.filter.return_value.order_by.call_args.args
    assert "NULLS FIRST" in str(order[0]).upper()
    assert "ig_sources.id" in str(order[1])
    assert dispatch.call_args.kwargs["args"][1:3] == [[4, 2, 3], 0]
    crawler.crawl_all_sources.run(manual=True)
    dispatch.assert_called_once()
    assert db.close.call_count == 1
    assert r.ttls[crawler.WAVE_KEY] == 1800


def test_interval_uses_wave_end_not_last_source(setup_wave):
    r, db, dispatch = setup_wave
    r.set(crawler.WAVE_END_KEY, (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    db.query.return_value.filter_by.return_value.first.return_value = SimpleNamespace(crawl_interval_minutes=25)
    crawler.crawl_all_sources.run()
    dispatch.assert_not_called()
    assert db.query.return_value.scalar.call_count == 0


def test_interval_falls_back_to_last_checked_when_no_wave_end(setup_wave):
    _, db, dispatch = setup_wave
    db.query.return_value.filter_by.return_value.first.return_value = SimpleNamespace(crawl_interval_minutes=25)
    db.query.return_value.scalar.return_value = datetime.now(timezone.utc) - timedelta(minutes=1)
    crawler.crawl_all_sources.run()
    db.query.return_value.scalar.assert_called_once()
    dispatch.assert_not_called()


def test_next_step_and_finish(setup_wave, monkeypatch, caplog):
    r, db, dispatch = setup_wave
    r.set(crawler.WAVE_KEY, "owner", ex=1800)
    monkeypatch.setattr(crawler, "_crawl_source", lambda db, source_id: None)
    monkeypatch.setattr(crawler.random, "randint", lambda a, b: 42)
    started = datetime.now(timezone.utc).timestamp()
    crawler.crawl_wave_step.run("owner", [1], 0, False, started)
    assert dispatch.call_args.kwargs["args"] == ["owner", [1], 1, False]
    assert dispatch.call_args.kwargs["countdown"] == 42
    assert db.close.call_count == 1
    with caplog.at_level(logging.INFO):
        crawler.crawl_wave_step.run("owner", [1], 1, False, started)
    assert r.get(crawler.WAVE_KEY) is None
    assert datetime.fromisoformat(r.get(crawler.WAVE_END_KEY)) >= datetime.fromtimestamp(started, timezone.utc)
    assert "sources crawled=1 failed=0" in caplog.text


def test_busy_retries_same_index(setup_wave, monkeypatch):
    r, db, dispatch = setup_wave
    r.set(crawler.WAVE_KEY, "owner")
    monkeypatch.setattr(crawler, "_crawl_source", lambda db, source_id: (_ for _ in ()).throw(ViewerBusyError("busy")))
    crawler.crawl_wave_step.run("owner", [9, 10], 0, False, datetime.now(timezone.utc).timestamp())
    assert dispatch.call_args.kwargs["args"] == ["owner", [9, 10], 0, False]
    assert dispatch.call_args.kwargs["countdown"] == 60
    assert dispatch.call_args.kwargs["kwargs"]["busy_attempts"] == 1
    db.rollback.assert_called_once()


def test_busy_deadline_skips_with_warning(setup_wave, monkeypatch, caplog):
    r, db, dispatch = setup_wave
    r.set(crawler.WAVE_KEY, "owner")
    monkeypatch.setattr(crawler, "_crawl_source", lambda db, source_id: (_ for _ in ()).throw(ViewerBusyError("busy")))
    with caplog.at_level(logging.WARNING):
        crawler.crawl_wave_step.run("owner", [9, 10], 0, False,
                                    datetime.now(timezone.utc).timestamp() - crawler.WAVE_DEADLINE, busy_attempts=3)
    assert "deadline reached; skipping source 9 after 4 busy attempts" in caplog.text
    assert dispatch.call_args.kwargs["args"][2] == 1
    assert dispatch.call_args.kwargs["kwargs"]["failed"] == 1


def test_superseded_stops(setup_wave, monkeypatch):
    r, db, dispatch = setup_wave
    r.set(crawler.WAVE_KEY, "new-owner")
    crawl = MagicMock()
    monkeypatch.setattr(crawler, "_crawl_source", crawl)
    crawler.crawl_wave_step.run("old-owner", [9], 0, False, 0)
    crawl.assert_not_called()
    dispatch.assert_not_called()
    assert r.get(crawler.WAVE_KEY) == "new-owner"


def test_manual_next_step_no_delay(setup_wave, monkeypatch):
    r, _, dispatch = setup_wave
    r.set(crawler.WAVE_KEY, "owner")
    monkeypatch.setattr(crawler, "_crawl_source", lambda db, source_id: None)
    crawler.crawl_wave_step.run("owner", [9], 0, True, datetime.now(timezone.utc).timestamp())
    assert dispatch.call_args.kwargs["countdown"] == 0


def test_other_failure_records_error_and_continues(setup_wave, monkeypatch):
    r, db, dispatch = setup_wave
    r.set(crawler.WAVE_KEY, "owner")
    source = SimpleNamespace(last_checked_at=None, last_crawl_error=None)
    db.query.return_value.filter_by.return_value.first.return_value = source
    monkeypatch.setattr(crawler, "_crawl_source", lambda db, source_id: (_ for _ in ()).throw(ValueError("broken")))
    crawler.crawl_wave_step.run("owner", [9], 0, True, datetime.now(timezone.utc).timestamp())
    assert source.last_crawl_error == "broken"
    assert source.last_checked_at is not None
    assert dispatch.call_args.kwargs["kwargs"]["failed"] == 1
