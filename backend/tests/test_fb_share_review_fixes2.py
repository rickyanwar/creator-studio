"""Regression checks for Page visibility, lane reservations and caption retries."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json

import pytest

from app.models.ai_video import VideoIdea, VideoJob, VideoClipQc
from app.models.fb_group_share import FbShareGroupJob, FbShareLane
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.services.fb_share import group_jobs
from app.services.fb_share.brand_settings import set_brand_share
from tests.test_fb_share_lanes_api import db, client


class FakeRedis:
    """Thread-safe in-memory Redis subset with key expiry on a controllable clock."""

    def __init__(self):
        self.values = {}
        self.expires_at = {}
        self.expiries = {}
        self.lock = Lock()
        self.now = 0.0

    def _alive(self, key):
        deadline = self.expires_at.get(key)
        if deadline is not None and self.now >= deadline:
            self.values.pop(key, None)
            self.expires_at.pop(key, None)
        return key in self.values

    def set(self, key, value, nx=False, ex=None, px=None):
        with self.lock:
            if nx and self._alive(key):
                return False
            self.values[key] = value
            ttl = ex if ex is not None else (px / 1000 if px is not None else None)
            self.expiries[key] = ttl
            if ttl is None:
                self.expires_at.pop(key, None)
            else:
                self.expires_at[key] = self.now + ttl
            return True

    def get(self, key):
        with self.lock:
            return self.values.get(key) if self._alive(key) else None

    def delete(self, *keys):
        with self.lock:
            removed = 0
            for key in keys:
                if self._alive(key):
                    removed += 1
                self.values.pop(key, None)
                self.expires_at.pop(key, None)
            return removed

    def incr(self, key):
        with self.lock:
            self.values[key] = int(self.values[key]) + 1 if self._alive(key) else 1
            return self.values[key]

    def decr(self, key):
        with self.lock:
            self.values[key] = int(self.values[key]) - 1 if self._alive(key) else -1
            return self.values[key]

    def expire(self, key, seconds):
        with self.lock:
            self.expiries[key] = seconds
            self.expires_at[key] = self.now + seconds

    def scan_iter(self, match="*"):
        from fnmatch import fnmatchcase
        with self.lock:
            keys = [key for key in list(self.values) if self._alive(key) and fnmatchcase(key, match)]
        return iter(keys)

    def reservations(self):
        return [key for key in self.scan_iter(f"{group_jobs.LANE_RESERVATION_PREFIX}*")]


def test_concurrent_reservations_release_and_non_lane(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(group_jobs, "redis_client", lambda: redis)
    monkeypatch.setattr("app.services.fb_share.lanes.lane_brand_id", lambda db, fid: 1 if fid == 2 else None)
    monkeypatch.setattr(group_jobs, "get_brand_share", lambda *a: (True, {"sources": {"ig": {"groups_only_per_day": 1}}}))
    db = Mock()
    db.get.return_value = SimpleNamespace(timezone="America/New_York")
    db.query.return_value.filter.return_value.count.return_value = 0
    now = datetime(2026, 11, 10, 2, tzinfo=timezone.utc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(pool.map(lambda _: group_jobs.reserve_lane_slot(db, 2, "ig", now), range(2)))
    assert sum(token is not None for token in tokens) == 1
    token = next(token for token in tokens if token is not None)
    assert token.startswith("fb_share:lane_res:1:ig:2026-11-09:")
    assert redis.expiries[token] == group_jobs.LANE_RESERVATION_TTL_SECONDS
    assert not [key for key in redis.values if key.startswith(group_jobs.LANE_LOCK_PREFIX) and redis.get(key)]
    group_jobs.release_lane_slot(token)
    assert redis.reservations() == []
    assert group_jobs.reserve_lane_slot(db, 2, "ig", now).startswith("fb_share:lane_res:1:ig:2026-11-09:")
    monkeypatch.setattr(group_jobs, "redis_client", Mock(side_effect=AssertionError("non-lane used Redis")))
    assert group_jobs.reserve_lane_slot(db, 1, "ig", now) is False


@pytest.mark.parametrize("used,allowed", [(0, True), (1, False)])
def test_reservation_redis_failure_uses_count(monkeypatch, used, allowed, caplog):
    monkeypatch.setattr("app.services.fb_share.lanes.lane_brand_id", lambda *a: 1)
    monkeypatch.setattr(group_jobs, "get_brand_share", lambda *a: (True, {"sources": {"news": {"groups_only_per_day": 1}}}))
    monkeypatch.setattr(group_jobs, "redis_client", Mock(side_effect=RuntimeError("offline")))
    db = Mock()
    db.get.return_value = SimpleNamespace(timezone="bad/zone")
    db.query.return_value.filter.return_value.count.return_value = used
    assert (group_jobs.reserve_lane_slot(db, 2, "news", datetime.now(timezone.utc)) is not None) is allowed
    assert "using marker count" in caplog.text


def test_ai_video_global_lists_hide_lanes_but_scoped_access_works(client, db):
    from sqlalchemy import text
    from sqlalchemy.schema import DefaultClause
    lane_id = client.post("/fanpages/1/group-share/lane").json()["lane_fanpage_id"]
    with patch.object(VideoJob.__table__.c.clips_done, "server_default", DefaultClause(text("'[]'"))):
        for model in (VideoIdea, VideoJob, VideoClipQc):
            model.__table__.create(db.bind, checkfirst=True)
    for fid in (1, lane_id):
        idea = VideoIdea(fanpage_id=fid, template_key="test", topic="Topic", status="proposed")
        db.add(idea)
        db.flush()
        db.add(VideoJob(fanpage_id=fid, idea_id=idea.id, template_key="test", title="Title", caption="Caption",
                        model="test", aspect="9:16", clip_seconds=8, credits_per_clip=1, credits_estimate=1,
                        status="queued", clips=[]))
    db.commit()
    for path in ("/ai-video/jobs", "/ai-video/ideas"):
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert [row["fanpage_id"] for row in response.json()] == [1]
        response = client.get(path, params={"fanpage_id": lane_id})
        assert [row["fanpage_id"] for row in response.json()] == [lane_id]
    for params in ({}, {"fanpage_id": lane_id}):
        counts = client.get("/ai-video/counts", params=params).json()
        assert counts == {"jobs_total": 1, "jobs_by_status": {"queued": 1},
                          "ideas_by_status": {"proposed": 1}, "ideas_active": 1}


def test_dashboard_excludes_lane_and_legacy_group_jobs(client, db):
    from app.database import Base
    from app.models.gallery_fetch_events import GalleryFetchEvent
    from sqlalchemy import text
    from sqlalchemy.schema import DefaultClause
    lane_id = client.post("/fanpages/1/group-share/lane").json()["lane_fanpage_id"]
    for name in ("publish_jobs", "publish_deliveries", "delivery_events", "posts", "ig_sources",
                 "ai_copy_events", "gallery_fetch_events"):
        table = Base.metadata.tables[name]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for column in table.columns:
                if column.server_default is not None and "::json" in str(column.server_default.arg):
                    stack.enter_context(patch.object(column, "server_default", DefaultClause(text("'{}'"))))
            table.create(db.bind, checkfirst=True)
    before = client.get("/dashboard/stats")
    assert before.status_code == 200, before.text
    now = datetime.now(timezone.utc)
    for fid in (1, lane_id):
        for status in (PublishJobStatus.pending_review, PublishJobStatus.failed, PublishJobStatus.published):
            job = PublishJob(fanpage_id=fid, status=status, published_at=now, updated_at=now)
            db.add(job)
            db.flush()
            if fid == 1:
                db.add(FbShareGroupJob(fanpage_id=1, mode="ig", publish_job_id=job.id))
    db.commit()
    after = client.get("/dashboard/stats")
    assert after.status_code == 200, after.text
    for field in ("pending_review", "failed_today", "published_today", "brands_today", "latest_posts"):
        assert after.json()[field] == before.json()[field]


@pytest.mark.parametrize("caption,tags", [
    ("", ["#A", "#B"]), ("x" * 281, ["#A", "#B"]), ("Inline #tag", ["#A", "#B"]),
    ('Changed "quote".', ["#A", "#B"]), ("Caption.", []), ("Caption.", ["#A"]),
    ("Caption.", ["#A", "#B", "#C", "#D"]),
])
def test_invalid_classification_retries_then_skips(monkeypatch, caption, tags):
    from app.services.fb_share import classify
    redis = FakeRedis()
    monkeypatch.setattr(classify, "redis_client", lambda: redis)
    monkeypatch.setattr(classify, "get_brand_share", lambda *a: (True, {"niches": ["f1"]}))
    row = SimpleNamespace(id=7, media_kind="photo", caption="Long original Page caption", hashtags="#Old",
                          sentiment=None, subjects=None, content_type=None, status="new")
    db = Mock()
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [row]
    output = json.dumps({"items": [{"id": 7, "group_caption": caption, "group_hashtags": tags}]})
    for attempt in range(1, 4):
        assert classify.classify_pending_items(db, SimpleNamespace(id=1), generate=lambda _: output) == 0
        assert row.sentiment is None and row.subjects is None and row.content_type is None
        assert row.caption == "Long original Page caption" and row.hashtags == "#Old"
        assert row.status == ("skipped" if attempt == 3 else "new")
    assert redis.values["fb_share:classify_fail:7"] == 3
    assert redis.expiries["fb_share:classify_fail:7"] == 7 * 24 * 60 * 60
