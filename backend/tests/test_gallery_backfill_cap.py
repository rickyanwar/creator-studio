from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import gallery_label_budget as budget
from app.tasks import gallery_labels


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.expire = MagicMock()

    def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def decr(self, key):
        self.values[key] -= 1


def test_backfill_daily_cap_and_day_rollover():
    redis = FakeRedis()
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    assert all(budget.reserve_backfill(redis_client=redis, now_utc=now) for _ in range(100))
    assert not budget.reserve_backfill(redis_client=redis, now_utc=now)
    assert redis.values == {"gallery_label_backfill:2026-11-07": 100}
    redis.expire.assert_called_once_with("gallery_label_backfill:2026-11-07", budget.KEY_TTL_SECONDS)
    assert budget.reserve_backfill(redis_client=redis, now_utc=now + timedelta(days=1))


@pytest.mark.parametrize("operation", ["incr", "expire", "decr"])
def test_backfill_redis_errors_fail_closed(operation):
    redis = FakeRedis()
    setattr(redis, operation, MagicMock(side_effect=RuntimeError("Redis unavailable")))
    assert not budget.reserve_backfill(cap=0, redis_client=redis)


def test_backfill_client_creation_error_fails_closed(monkeypatch):
    monkeypatch.setattr(budget, "get_budget_redis_client", MagicMock(side_effect=RuntimeError("Redis unavailable")))
    assert not budget.reserve_backfill()


def test_sweep_skips_old_candidates_but_labels_new(monkeypatch, caplog):
    db = MagicMock()
    db.query().filter_by().first.return_value = SimpleNamespace(
        gallery_labeling_enabled=True, gallery_labeling_since=datetime(2026, 11, 1),
        gallery_labeling_daily_cap=1000,
    )
    candidates = [(SimpleNamespace(local_path=kind + ".jpg", keyword=kind, attributes={}), kind)
                  for kind in ("relabel", "backfill", "new", "backfill")]
    monkeypatch.setattr(gallery_labels, "_pick_label_candidates", lambda *args: candidates)
    monkeypatch.setattr(gallery_labels.os.path, "exists", lambda _: True)
    backfill = MagicMock(return_value=False)
    monkeypatch.setattr(budget, "reserve_backfill", backfill)
    reserve = MagicMock(return_value=True)
    monkeypatch.setattr(gallery_labels, "reserve", reserve)
    label = MagicMock(return_value={"caption": "A photo"})
    monkeypatch.setattr(gallery_labels, "label_image", label)
    monkeypatch.setattr(gallery_labels, "merge_label_attributes", lambda old, new: new)
    monkeypatch.setattr(gallery_labels.time, "sleep", lambda _: None)
    with caplog.at_level("INFO", logger=gallery_labels.__name__):
        assert gallery_labels.label_new_gallery_images(db=db, now=datetime(2026, 11, 7, 18, tzinfo=timezone.utc)) == 1
    assert backfill.call_count == 3
    reserve.assert_called_once_with(1000, db=db)
    label.assert_called_once_with("new.jpg", provider="router", keyword="new")
    assert caplog.text.count("backfill daily cap reached") == 1
    assert "new=1 relabel=0 backfill=0" in caplog.text
