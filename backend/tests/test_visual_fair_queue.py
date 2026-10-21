"""Tests for fair visual queue dispatcher (app.services.visual_fair_queue)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.design_templates import DesignTemplate
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ScrapedArticle
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.visual_fair_queue import (
    CLAIM_KEY,
    LAST_JOB_KEY,
    claim_page,
    fair_counter_key,
    mark_served,
    news_job_waiting,
    page_claimed,
    pick_next_news_job,
    served_today,
    visual_news_fanpage_ids,
    wib_date,
)
from app.tasks.design_renderer import (
    dispatch_fair_visual_news,
    render_pending_designs,
)


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.queues: dict[str, list] = {}
        self.ttls: dict[str, int | None] = {}

    def get(self, key: str):
        return self.data.get(key)

    def mget(self, keys: list[str]):
        return [self.data.get(k) for k in keys]

    def incr(self, key: str, amount: int = 1):
        val = int(self.data.get(key, 0)) + amount
        self.data[key] = str(val)
        return val

    def expire(self, key: str, time: int):
        return True

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.data:
            return None
        self.data[key] = str(value)
        if ex is not None:
            self.ttls[key] = ex
        return True

    def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self.data)

    def delete(self, *keys: str):
        count = 0
        for k in keys:
            if self.data.pop(k, None) is not None:
                count += 1
        return count

    def llen(self, key: str):
        return len(self.queues.get(key, []))


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def db_engine():
    engine = create_engine("sqlite:///:memory:")
    PublishJob.__table__.create(bind=engine)
    TargetFanpage.__table__.create(bind=engine)
    ScrapedArticle.__table__.create(bind=engine)
    DesignTemplate.__table__.create(bind=engine)
    return engine


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _create_fanpage(db, id: int, name: str, mode: PublishMode = PublishMode.auto) -> TargetFanpage:
    fp = TargetFanpage(
        id=id,
        name=name,
        mode2_publish_mode=mode,
    )
    db.add(fp)
    db.commit()
    return fp


def _create_job(
    db,
    id: int,
    fanpage_id: int,
    status: PublishJobStatus = PublishJobStatus.pending_design,
    content_type: ContentType = ContentType.news_content,
    created_at: datetime | None = None,
    design_not_before: datetime | None = None,
    is_breaking: bool = False,
    last_error: str | None = None,
) -> PublishJob:
    job = PublishJob(
        id=id,
        fanpage_id=fanpage_id,
        status=status,
        content_type=content_type,
        created_at=created_at or datetime.now(timezone.utc).replace(tzinfo=None),
        design_not_before=design_not_before,
        is_breaking=is_breaking,
        last_error=last_error,
    )
    db.add(job)
    db.commit()
    return job


# -------------------------------------------------------------------------
# (a) Page A served 3, Page B served 0 -> B's newest job picked
# -------------------------------------------------------------------------
def test_fair_picker_least_served_first(db_session, fake_redis):
    fp_a = _create_fanpage(db_session, 1, "Page A")
    fp_b = _create_fanpage(db_session, 2, "Page B")

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    mark_served(fake_redis, fp_a.id, now)
    mark_served(fake_redis, fp_a.id, now)
    mark_served(fake_redis, fp_a.id, now)

    t0 = datetime(2026, 10, 20, 10, 0)
    t1 = datetime(2026, 10, 20, 10, 30)
    t2 = datetime(2026, 10, 20, 11, 0)

    _create_job(db_session, id=101, fanpage_id=fp_a.id, created_at=t2)
    _create_job(db_session, id=102, fanpage_id=fp_b.id, created_at=t0)
    job_b_newest = _create_job(db_session, id=103, fanpage_id=fp_b.id, created_at=t1)

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked = pick_next_news_job(db_session, fake_redis, now)

    assert picked is not None
    assert picked.id == job_b_newest.id


# -------------------------------------------------------------------------
# (b) Breaking job on A beats B
# -------------------------------------------------------------------------
def test_fair_picker_breaking_job_beats_least_served(db_session, fake_redis):
    fp_a = _create_fanpage(db_session, 1, "Page A")
    fp_b = _create_fanpage(db_session, 2, "Page B")

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    mark_served(fake_redis, fp_a.id, now)
    mark_served(fake_redis, fp_a.id, now)

    t0 = datetime(2026, 10, 20, 10, 0)
    t1 = datetime(2026, 10, 20, 11, 0)

    breaking_job_a = _create_job(db_session, id=201, fanpage_id=fp_a.id, created_at=t0, is_breaking=True)
    _create_job(db_session, id=202, fanpage_id=fp_b.id, created_at=t1, is_breaking=False)

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked = pick_next_news_job(db_session, fake_redis, now)

    assert picked is not None
    assert picked.id == breaking_job_a.id


# -------------------------------------------------------------------------
# (c) Only page A has jobs -> A is picked (work-conserving)
# -------------------------------------------------------------------------
def test_fair_picker_work_conserving_single_page(db_session, fake_redis):
    fp_a = _create_fanpage(db_session, 1, "Page A")
    _create_fanpage(db_session, 2, "Page B")

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    for _ in range(5):
        mark_served(fake_redis, fp_a.id, now)

    job_a = _create_job(db_session, id=301, fanpage_id=fp_a.id)

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked = pick_next_news_job(db_session, fake_redis, now)

    assert picked is not None
    assert picked.id == job_a.id


# -------------------------------------------------------------------------
# (d') LAST_JOB_KEY points to job still pending_design + lock held -> "busy"
# -------------------------------------------------------------------------
def test_dispatch_fair_visual_news_busy_when_last_job_waiting(db_engine, fake_redis):
    Session = sessionmaker(bind=db_engine)
    db = Session()
    fp = _create_fanpage(db, 1, "Page A")
    job = _create_job(db, id=400, fanpage_id=fp.id, status=PublishJobStatus.pending_design)
    job_id = job.id
    db.close()

    fake_redis.set(LAST_JOB_KEY, job_id)
    fake_redis.set(f"render_design:queued:{job_id}", "1")

    with patch("app.tasks.design_renderer.SessionLocal", side_effect=Session), \
         patch("app.tasks.design_renderer._redis", return_value=fake_redis), \
         patch("app.tasks.design_renderer.render_design.apply_async") as mock_apply:
        res = dispatch_fair_visual_news()

    assert res == "busy"
    mock_apply.assert_not_called()


# -------------------------------------------------------------------------
# (d'') LAST_JOB_KEY points to job now rendering -> dispatches next job, updates LAST_JOB_KEY
# -------------------------------------------------------------------------
def test_dispatch_fair_visual_news_dispatches_when_last_job_rendering(db_engine, fake_redis):
    Session = sessionmaker(bind=db_engine)
    db = Session()
    fp = _create_fanpage(db, 1, "Page A")
    job1 = _create_job(db, id=401, fanpage_id=fp.id, status=PublishJobStatus.rendering)
    job2 = _create_job(db, id=402, fanpage_id=fp.id, status=PublishJobStatus.pending_design)
    job1_id = job1.id
    job2_id = job2.id
    db.close()

    fake_redis.set(LAST_JOB_KEY, job1_id)
    fake_redis.set(f"render_design:queued:{job1_id}", "1")

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    with patch("app.tasks.design_renderer.SessionLocal", side_effect=Session), \
         patch("app.tasks.design_renderer._redis", return_value=fake_redis), \
         patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True), \
         patch("app.tasks.design_renderer.render_design.apply_async") as mock_apply:
        res = dispatch_fair_visual_news()

    assert res == f"dispatched {job2_id}"
    mock_apply.assert_called_once_with(args=[job2_id], queue="visual", expires=1800)
    assert fake_redis.get(LAST_JOB_KEY) == str(job2_id)


# -------------------------------------------------------------------------
# (d''') LAST_JOB_KEY points to pending_design job with lock expired/missing -> dispatches
# -------------------------------------------------------------------------
def test_dispatch_fair_visual_news_dispatches_when_lock_not_set(db_engine, fake_redis):
    Session = sessionmaker(bind=db_engine)
    db = Session()
    fp = _create_fanpage(db, 1, "Page A")
    job = _create_job(db, id=403, fanpage_id=fp.id, status=PublishJobStatus.pending_design)
    job_id = job.id
    db.close()

    fake_redis.set(LAST_JOB_KEY, job_id)
    # lock key is NOT set

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    with patch("app.tasks.design_renderer.SessionLocal", side_effect=Session), \
         patch("app.tasks.design_renderer._redis", return_value=fake_redis), \
         patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True), \
         patch("app.tasks.design_renderer.render_design.apply_async") as mock_apply:
        res = dispatch_fair_visual_news()

    assert res == f"dispatched {job_id}"
    mock_apply.assert_called_once_with(args=[job_id], queue="visual", expires=1800)
    assert fake_redis.get(LAST_JOB_KEY) == str(job_id)


# -------------------------------------------------------------------------
# (e) No waiting job -> apply_async called once with queue="visual", counter -> 1, LAST_JOB_KEY set
# -------------------------------------------------------------------------
def test_dispatch_fair_visual_news_dispatches_when_empty(db_engine, fake_redis):
    Session = sessionmaker(bind=db_engine)
    db = Session()
    fp = _create_fanpage(db, 1, "Page A")
    fp_id = fp.id
    job = _create_job(db, id=404, fanpage_id=fp_id)
    job_id = job.id
    db.close()

    # The dispatcher counts with the real clock, so read the counter for today, not a fixed date.
    now = datetime.now(timezone.utc)

    with patch("app.tasks.design_renderer.SessionLocal", side_effect=Session), \
         patch("app.tasks.design_renderer._redis", return_value=fake_redis), \
         patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True), \
         patch("app.tasks.design_renderer.render_design.apply_async") as mock_apply:
        res = dispatch_fair_visual_news()

    assert res == f"dispatched {job_id}"
    mock_apply.assert_called_once_with(args=[job_id], queue="visual", expires=1800)
    counts = served_today(fake_redis, [fp_id], now)
    assert counts[fp_id] == 1
    assert fake_redis.get(LAST_JOB_KEY) == str(job_id)


# -------------------------------------------------------------------------
# (f) _has_news_slot returns False for first job -> skipped, next returned
# -------------------------------------------------------------------------
def test_has_news_slot_skips_and_advances(db_session, fake_redis):
    fp = _create_fanpage(db_session, 1, "Page A")
    t0 = datetime(2026, 10, 20, 10, 0)
    t1 = datetime(2026, 10, 20, 11, 0)
    job1 = _create_job(db_session, id=501, fanpage_id=fp.id, created_at=t1)
    job2 = _create_job(db_session, id=502, fanpage_id=fp.id, created_at=t0)

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    def mock_slot(db, job, now_utc):
        return job.id != job1.id

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._has_news_slot", side_effect=mock_slot):
        picked = pick_next_news_job(db_session, fake_redis, now)

    assert picked is not None
    assert picked.id == job2.id

    db_session.refresh(job1)
    assert job1.status == PublishJobStatus.skipped
    assert job1.last_error == "stale: no News slot within 24 h, AI poster not rendered"


# -------------------------------------------------------------------------
# (g) Job older than 24 h or with future design_not_before is never picked
# -------------------------------------------------------------------------
def test_stale_or_deferred_jobs_ignored(db_session, fake_redis):
    fp = _create_fanpage(db_session, 1, "Page A")
    now_utc = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    now_naive = datetime(2026, 10, 20, 12, 0)

    # Job older than 24h
    _create_job(
        db_session,
        id=601,
        fanpage_id=fp.id,
        created_at=now_naive - timedelta(hours=25),
    )
    # Job with future design_not_before
    _create_job(
        db_session,
        id=602,
        fanpage_id=fp.id,
        created_at=now_naive - timedelta(hours=1),
        design_not_before=now_naive + timedelta(hours=2),
    )

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked = pick_next_news_job(db_session, fake_redis, now_utc)

    assert picked is None


# -------------------------------------------------------------------------
# (h) render_pending_designs excludes visual fanpages, enqueues non-visual
# -------------------------------------------------------------------------
def test_render_pending_designs_excludes_visual_fanpages(db_engine, fake_redis):
    Session = sessionmaker(bind=db_engine)
    db = Session()
    fp_visual = _create_fanpage(db, 1, "Visual Page")
    fp_normal = _create_fanpage(db, 2, "Normal Page")
    visual_id = fp_visual.id
    normal_id = fp_normal.id

    job_visual = _create_job(db, id=701, fanpage_id=visual_id)
    job_normal = _create_job(db, id=702, fanpage_id=normal_id)
    job_normal_id = job_normal.id
    db.close()

    def mock_queue_for(db_conn, fanpage, kind):
        fid = getattr(fanpage, "id", None)
        if fid == visual_id:
            return "visual"
        return None

    with patch("app.tasks.design_renderer.SessionLocal", side_effect=Session), \
         patch("app.tasks.design_renderer._redis", return_value=fake_redis), \
         patch("app.services.visual_fair_queue.render_queue_for", side_effect=mock_queue_for), \
         patch("app.services.engine_router.render_queue_for", side_effect=mock_queue_for), \
         patch("app.tasks.design_renderer.render_design.delay") as mock_delay, \
         patch("app.tasks.design_renderer.render_design.apply_async") as mock_apply:
        render_pending_designs()

    # Normal job queued with delay(), visual job NOT queued
    mock_delay.assert_called_once_with(job_normal_id)
    mock_apply.assert_not_called()


# -------------------------------------------------------------------------
# (i) wib_date around midnight WIB (e.g. 2026-10-20 17:30 UTC -> "2026-10-21")
# -------------------------------------------------------------------------
def test_wib_date_midnight_crossover():
    dt_after_midnight = datetime(2026, 10, 20, 17, 30, tzinfo=timezone.utc)
    assert wib_date(dt_after_midnight) == "2026-10-21"

    dt_before_midnight = datetime(2026, 10, 20, 16, 59, tzinfo=timezone.utc)
    assert wib_date(dt_before_midnight) == "2026-10-20"


# -------------------------------------------------------------------------
# (j) Two candidates: first-ranked has lock held -> second returned
# -------------------------------------------------------------------------
def test_pick_next_news_job_skips_locked_job(db_session, fake_redis):
    fp = _create_fanpage(db_session, 1, "Page A")
    t0 = datetime(2026, 10, 20, 10, 0)
    t1 = datetime(2026, 10, 20, 11, 0)
    job1 = _create_job(db_session, id=901, fanpage_id=fp.id, created_at=t1)
    job2 = _create_job(db_session, id=902, fanpage_id=fp.id, created_at=t0)

    fake_redis.set(f"render_design:queued:{job1.id}", "1")

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked = pick_next_news_job(db_session, fake_redis, now)

    assert picked is not None
    assert picked.id == job2.id


# -------------------------------------------------------------------------
# Additional coverage: helpers and edge cases
# -------------------------------------------------------------------------
def test_news_job_waiting_helpers(db_session, fake_redis):
    # Missing / None
    assert not news_job_waiting(db_session, fake_redis)

    # Invalid int
    fake_redis.set(LAST_JOB_KEY, "bad_id")
    assert not news_job_waiting(db_session, fake_redis)

    # Missing in DB
    fake_redis.set(LAST_JOB_KEY, "9999")
    assert not news_job_waiting(db_session, fake_redis)

    # Exists in DB, not pending_design
    fp = _create_fanpage(db_session, 1, "Page A")
    job = _create_job(db_session, id=950, fanpage_id=fp.id, status=PublishJobStatus.rendering)
    fake_redis.set(LAST_JOB_KEY, job.id)
    fake_redis.set(f"render_design:queued:{job.id}", "1")
    assert not news_job_waiting(db_session, fake_redis)

    # pending_design but queue lock missing
    job.status = PublishJobStatus.pending_design
    db_session.commit()
    fake_redis.delete(f"render_design:queued:{job.id}")
    assert not news_job_waiting(db_session, fake_redis)

    # pending_design AND queue lock held -> True
    fake_redis.set(f"render_design:queued:{job.id}", "1")
    assert news_job_waiting(db_session, fake_redis)

    # Exception safety (e.g. redis error)
    broken_redis = MagicMock()
    broken_redis.get.side_effect = Exception("redis broken")
    assert not news_job_waiting(db_session, broken_redis)


def test_fair_counter_key():
    now = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    assert fair_counter_key(42, now) == "visual_fair:news:2026-10-20:42"


def test_served_today_missing_and_error(fake_redis):
    now = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    assert served_today(fake_redis, [], now) == {}
    assert served_today(fake_redis, [1, 2], now) == {1: 0, 2: 0}

    broken_redis = MagicMock()
    broken_redis.mget.side_effect = Exception("error")
    assert served_today(broken_redis, [1, 2], now) == {1: 0, 2: 0}


def test_dispatch_fair_visual_news_idle_and_locked(db_engine, fake_redis):
    Session = sessionmaker(bind=db_engine)

    # Idle case: no jobs
    with patch("app.tasks.design_renderer.SessionLocal", side_effect=Session), \
         patch("app.tasks.design_renderer._redis", return_value=fake_redis), \
         patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"):
        assert dispatch_fair_visual_news() == "idle"

    # Locked case: job exists but lock acquisition fails
    db = Session()
    fp = _create_fanpage(db, 1, "Page A")
    job = _create_job(db, id=801, fanpage_id=fp.id)
    db.close()

    with patch("app.tasks.design_renderer.SessionLocal", side_effect=Session), \
         patch("app.tasks.design_renderer._redis", return_value=fake_redis), \
         patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True), \
         patch("app.tasks.design_renderer._acquire_queued_lock", return_value=False), \
         patch("app.tasks.design_renderer.render_design.apply_async") as mock_apply:
        assert dispatch_fair_visual_news() == "locked"
        mock_apply.assert_not_called()


def test_jit_page_a_near_slot_page_b_far(db_session, fake_redis):
    # (a) Page A's next slot is in 30 min, page B's in 5 h -> A's newest job is returned, and B's jobs stay pending_design, untouched.
    fp_a = _create_fanpage(db_session, 10, "Page A")
    fp_b = _create_fanpage(db_session, 20, "Page B")

    t_old = datetime(2026, 10, 20, 10, 0)
    t_new = datetime(2026, 10, 20, 11, 0)
    job_a_old = _create_job(db_session, id=101, fanpage_id=fp_a.id, created_at=t_old)
    job_a_new = _create_job(db_session, id=102, fanpage_id=fp_a.id, created_at=t_new)
    job_b = _create_job(db_session, id=201, fanpage_id=fp_b.id, created_at=t_new)

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    slot_a = now + timedelta(minutes=20)
    slot_b = now + timedelta(hours=5)

    def mock_next_slot(db, job, now_utc):
        if job.fanpage_id == fp_a.id:
            return slot_a
        return slot_b

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._next_slot", side_effect=mock_next_slot):
        picked = pick_next_news_job(db_session, fake_redis, now)

    assert picked is not None
    assert picked.id == job_a_new.id
    assert getattr(picked, "_jit_slot", None) == slot_a

    # B's job remains pending_design and untouched
    db_session.refresh(job_b)
    assert job_b.status == PublishJobStatus.pending_design
    assert job_b.last_error is None


def test_jit_page_claimed_skips_to_next(db_session, fake_redis):
    # (b) Page A is claimed -> skipped, and B is returned when B is within the JIT window.
    fp_a = _create_fanpage(db_session, 10, "Page A")
    fp_b = _create_fanpage(db_session, 20, "Page B")

    t = datetime(2026, 10, 20, 11, 0)
    job_a = _create_job(db_session, id=101, fanpage_id=fp_a.id, created_at=t)
    job_b = _create_job(db_session, id=201, fanpage_id=fp_b.id, created_at=t)

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    slot_a = now + timedelta(minutes=20)
    slot_b = now + timedelta(minutes=25)

    # Claim Page A
    claim_page(fake_redis, fp_a.id, slot_a, now)
    assert page_claimed(fake_redis, fp_a.id)

    def mock_next_slot(db, job, now_utc):
        return slot_a if job.fanpage_id == fp_a.id else slot_b

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._next_slot", side_effect=mock_next_slot):
        picked = pick_next_news_job(db_session, fake_redis, now)

    assert picked is not None
    assert picked.id == job_b.id
    assert getattr(picked, "_jit_slot", None) == slot_b


def test_jit_page_multiple_jobs_picks_newest(db_session, fake_redis):
    # (c) Two jobs on A, both fresh, slot in 20 min -> the NEWEST is returned.
    fp_a = _create_fanpage(db_session, 10, "Page A")

    t_old = datetime(2026, 10, 20, 10, 30)
    t_new = datetime(2026, 10, 20, 11, 30)
    job_old = _create_job(db_session, id=101, fanpage_id=fp_a.id, created_at=t_old)
    job_new = _create_job(db_session, id=102, fanpage_id=fp_a.id, created_at=t_new)

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    slot = now + timedelta(minutes=20)

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._next_slot", return_value=slot):
        picked = pick_next_news_job(db_session, fake_redis, now)

    assert picked is not None
    assert picked.id == job_new.id
    assert getattr(picked, "_jit_slot", None) == slot


def test_jit_breaking_job_bypasses_jit(db_session, fake_redis):
    # (d) A breaking job on a page whose slot is 5 h away -> still returned (breaking bypasses JIT).
    fp_b = _create_fanpage(db_session, 20, "Page B")
    t = datetime(2026, 10, 20, 11, 0)
    job_breaking = _create_job(db_session, id=201, fanpage_id=fp_b.id, created_at=t, is_breaking=True)

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    slot_far = now + timedelta(hours=5)

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._next_slot", return_value=slot_far), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked = pick_next_news_job(db_session, fake_redis, now)

    assert picked is not None
    assert picked.id == job_breaking.id


def test_jit_next_slot_none_fallback(db_session, fake_redis):
    # (e) _next_slot returns None -> the old _has_news_slot behaviour applies.
    fp = _create_fanpage(db_session, 10, "Page A")
    t = datetime(2026, 10, 20, 11, 0)
    job1 = _create_job(db_session, id=101, fanpage_id=fp.id, created_at=t)

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    # When _has_news_slot is True -> job returned
    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._next_slot", return_value=None), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked = pick_next_news_job(db_session, fake_redis, now)
        assert picked is not None
        assert picked.id == job1.id

    # When _has_news_slot is False -> job marked skipped
    job2 = _create_job(db_session, id=102, fanpage_id=fp.id, created_at=t)
    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._next_slot", return_value=None), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=False):
        picked = pick_next_news_job(db_session, fake_redis, now)
        assert picked is None
        db_session.refresh(job2)
        assert job2.status == PublishJobStatus.skipped
        assert "stale: no News slot within 24 h" in job2.last_error


def test_dispatcher_claims_page_with_right_ttl(db_engine, fake_redis):
    # (f) The dispatcher sets the claim key with the right TTL when _jit_slot is present.
    Session = sessionmaker(bind=db_engine)
    db = Session()
    fp = _create_fanpage(db, 1, "Page A")
    job = _create_job(db, id=901, fanpage_id=fp.id)
    db.close()

    slot_utc_holder = []

    def mock_pick(db, r, now_utc):
        j = db.query(PublishJob).filter_by(id=901).first()
        slot = now_utc + timedelta(minutes=40)
        j._jit_slot = slot
        slot_utc_holder.append(slot)
        return j

    with patch("app.tasks.design_renderer.SessionLocal", side_effect=Session), \
         patch("app.tasks.design_renderer._redis", return_value=fake_redis), \
         patch("app.services.visual_fair_queue.news_job_waiting", return_value=False), \
         patch("app.services.visual_fair_queue.pick_next_news_job", side_effect=mock_pick), \
         patch("app.tasks.design_renderer._acquire_queued_lock", return_value=True), \
         patch("app.tasks.design_renderer.render_design.apply_async"):
        res = dispatch_fair_visual_news()
        assert res == "dispatched 901"

    claim_key = CLAIM_KEY.format(fanpage_id=1)
    assert fake_redis.get(claim_key) == slot_utc_holder[0].isoformat()
    ttl = fake_redis.ttls.get(claim_key)
    assert ttl == 3000

    # Direct claim_page checks with fixed now
    fixed_now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    fixed_slot = fixed_now + timedelta(minutes=40)
    claim_page(fake_redis, 2, fixed_slot, fixed_now)
    assert fake_redis.ttls.get(CLAIM_KEY.format(fanpage_id=2)) == 3000

    # Min TTL check (max(900, seconds + 600)): slot in 2 minutes -> 120 + 600 = 720 < 900 -> 900
    slot_near = fixed_now + timedelta(minutes=2)
    claim_page(fake_redis, 3, slot_near, fixed_now)
    assert fake_redis.ttls.get(CLAIM_KEY.format(fanpage_id=3)) == 900
