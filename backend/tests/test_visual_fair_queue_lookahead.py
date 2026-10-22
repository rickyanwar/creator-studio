"""Tests for ChatGPT idle 6-hour lookahead dispatcher (app.services.visual_fair_queue)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.database import Base
from app.models.ai_copy_events import AICopyEvent
from app.models.design_templates import DesignTemplate
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ScrapedArticle
from app.models.settings import Settings
from app.models.social_publishing import PublishDelivery, SocialDestination
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.visual_fair_queue import (
    CLAIM_KEY,
    LOOKAHEAD_HOURS,
    MAX_AHEAD_PER_PAGE,
    ahead_count,
    chatgpt_can_look_ahead,
    claim_page,
    lookahead_horizon,
    page_claimed,
    pick_next_news_job,
    release_page,
)
from app.tasks.design_renderer import dispatch_fair_visual_news


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
        self.ttls[key] = time
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


@pytest.fixture(scope="session")
def db_engine():
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        engine = create_engine(db_url)
        import app.models  # noqa: F401
        Base.metadata.create_all(bind=engine)
    else:
        engine = create_engine("sqlite:///:memory:")
        for t in [
            TargetFanpage.__table__,
            PublishJob.__table__,
            PublishDelivery.__table__,
            AICopyEvent.__table__,
            ScrapedArticle.__table__,
            DesignTemplate.__table__,
            Settings.__table__,
        ]:
            t.create(bind=engine, checkfirst=True)
    yield engine
    if not db_url:
        engine.dispose()


@pytest.fixture
def db_session(db_engine):
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def fake_redis():
    return FakeRedis()


# Offset all explicit ids so rows never collide with other test modules sharing the database.
_ID_BASE = 970_000_000


def _create_fanpage(db, id: int, name: str, mode: PublishMode = PublishMode.auto) -> TargetFanpage:
    fp = TargetFanpage(
        id=_ID_BASE + id,
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
        id=_ID_BASE + id,
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


def test_lookahead_job_picked(db_session, fake_redis):
    # 1. look_ahead True, slot 4 h ahead, ahead_count 2 -> job returned with _lookahead True
    fp = _create_fanpage(db_session, 1, "Page 1")
    _create_job(db_session, id=101, fanpage_id=fp.id)

    now = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    slot = now + timedelta(hours=4)

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=True), \
         patch("app.services.visual_fair_queue.ahead_count", return_value=2), \
         patch("app.services.visual_fair_queue._next_slot", return_value=slot), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked = pick_next_news_job(db_session, fake_redis, now)

    assert picked is not None
    assert picked.id == _ID_BASE + 101
    assert getattr(picked, "_lookahead", False) is True
    assert getattr(picked, "_jit_slot", None) == slot


def test_lookahead_slot_too_far_ahead_returns_none(db_session, fake_redis):
    # 2. look_ahead True, slot 7 h ahead -> None
    fp = _create_fanpage(db_session, 1, "Page 1")
    _create_job(db_session, id=101, fanpage_id=fp.id)

    now = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    slot = now + timedelta(hours=7)

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=True), \
         patch("app.services.visual_fair_queue.ahead_count", return_value=2), \
         patch("app.services.visual_fair_queue._next_slot", return_value=slot), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked = pick_next_news_job(db_session, fake_redis, now)

    assert picked is None


def test_lookahead_max_ahead_reached_returns_none(db_session, fake_redis):
    # 3. look_ahead True, slot 2 h ahead, ahead_count 6 -> None
    fp = _create_fanpage(db_session, 1, "Page 1")
    _create_job(db_session, id=101, fanpage_id=fp.id)

    now = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    slot = now + timedelta(hours=2)

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=True), \
         patch("app.services.visual_fair_queue.ahead_count", return_value=6), \
         patch("app.services.visual_fair_queue._next_slot", return_value=slot), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked = pick_next_news_job(db_session, fake_redis, now)

    assert picked is None


def test_normal_jit_behavior(db_session, fake_redis):
    # 4. look_ahead False, slot 2 h ahead -> None; slot 20 min ahead -> returned WITHOUT _lookahead (normal JIT)
    fp = _create_fanpage(db_session, 1, "Page 1")
    _create_job(db_session, id=101, fanpage_id=fp.id)

    now = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    slot_2h = now + timedelta(hours=2)

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=False), \
         patch("app.services.visual_fair_queue._next_slot", return_value=slot_2h), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked_2h = pick_next_news_job(db_session, fake_redis, now)

    assert picked_2h is None

    slot_20m = now + timedelta(minutes=20)
    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=False), \
         patch("app.services.visual_fair_queue._next_slot", return_value=slot_20m), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked_20m = pick_next_news_job(db_session, fake_redis, now)

    assert picked_20m is not None
    assert picked_20m.id == _ID_BASE + 101
    assert getattr(picked_20m, "_lookahead", False) is False
    assert getattr(picked_20m, "_jit_slot", None) == slot_20m


def test_claimed_page_behavior(db_session, fake_redis):
    # 5. claimed page: picked when look_ahead True, skipped when False
    fp = _create_fanpage(db_session, 1, "Page 1")
    _create_job(db_session, id=101, fanpage_id=fp.id)

    now = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    slot = now + timedelta(hours=2)

    claim_page(fake_redis, fp.id, slot, now)
    assert page_claimed(fake_redis, fp.id) is True

    # When look_ahead is True: picked
    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=True), \
         patch("app.services.visual_fair_queue.ahead_count", return_value=1), \
         patch("app.services.visual_fair_queue._next_slot", return_value=slot), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked_true = pick_next_news_job(db_session, fake_redis, now)

    assert picked_true is not None
    assert picked_true.id == _ID_BASE + 101
    assert getattr(picked_true, "_lookahead", False) is True

    # When look_ahead is False: skipped
    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=False), \
         patch("app.services.visual_fair_queue.ahead_count", return_value=1), \
         patch("app.services.visual_fair_queue._next_slot", return_value=slot), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked_false = pick_next_news_job(db_session, fake_redis, now)

    assert picked_false is None


def test_chatgpt_can_look_ahead(db_session):
    # 6. chatgpt_can_look_ahead: False at night; False when queue_len 1; False when unavailable;
    # False when 42 gptcli image events in last 3 h; True otherwise.
    now = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)

    # a) Night -> False
    with patch("app.services.engine_router.is_night", return_value=True):
        assert chatgpt_can_look_ahead(db_session, now) is False

    # b) queue_len > 0 -> False
    with patch("app.services.engine_router.is_night", return_value=False), \
         patch("app.services.engine_router.gptcli_queue_len", return_value=1):
        assert chatgpt_can_look_ahead(db_session, now) is False

    # c) unavailable -> False
    with patch("app.services.engine_router.is_night", return_value=False), \
         patch("app.services.engine_router.gptcli_queue_len", return_value=0), \
         patch("app.services.gptcli_client.gptcli_unavailable_until", return_value=now + timedelta(minutes=10)):
        assert chatgpt_can_look_ahead(db_session, now) is False

    # d) 42 gptcli image events in last 3 h -> False
    now_naive = now.replace(tzinfo=None)
    for i in range(42):
        ev = AICopyEvent(
            id=_ID_BASE + 5000 + i,
            context="image_gen",
            final_provider="gptcli",
            outcome="success",
            created_at=now_naive - timedelta(minutes=10 + i),
        )
        db_session.add(ev)
    db_session.commit()

    with patch("app.services.engine_router.is_night", return_value=False), \
         patch("app.services.engine_router.gptcli_queue_len", return_value=0), \
         patch("app.services.gptcli_client.gptcli_unavailable_until", return_value=None):
        assert chatgpt_can_look_ahead(db_session, now) is False

    # e) True otherwise
    db_session.query(AICopyEvent).filter(AICopyEvent.id >= 5000).delete()
    db_session.commit()

    with patch("app.services.engine_router.is_night", return_value=False), \
         patch("app.services.engine_router.gptcli_queue_len", return_value=0), \
         patch("app.services.gptcli_client.gptcli_unavailable_until", return_value=None):
        assert chatgpt_can_look_ahead(db_session, now) is True


def test_ahead_count_postgres(db_session):
    # 7. ahead_count with real rows (Postgres): 1 pending_publish job + 1 job whose delivery is repliz_scheduled
    # in the future + 1 job whose delivery is already published -> 2.
    fp = _create_fanpage(db_session, 201, "Page 201")
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    now_naive = now.replace(tzinfo=None)

    dest = SocialDestination(id=_ID_BASE + 201, content_project_id=fp.id, kind="facebook_page", name="Dest 201")
    db_session.add(dest)
    db_session.commit()

    # 1. pending_publish job
    j1 = _create_job(db_session, id=2001, fanpage_id=fp.id, status=PublishJobStatus.pending_publish)

    # 2. job whose delivery is repliz_scheduled in the future
    j2 = _create_job(db_session, id=2002, fanpage_id=fp.id, status=PublishJobStatus.pending_review)
    d2 = PublishDelivery(
        publish_job_id=j2.id,
        destination_id=dest.id,
        state="repliz_scheduled",
        scheduled_for=now_naive + timedelta(hours=2),
    )
    db_session.add(d2)

    # 3. job whose delivery is already published
    j3 = _create_job(db_session, id=2003, fanpage_id=fp.id, status=PublishJobStatus.published)
    d3 = PublishDelivery(
        publish_job_id=j3.id,
        destination_id=dest.id,
        state="published",
        scheduled_for=now_naive - timedelta(hours=1),
    )
    db_session.add(d3)
    db_session.commit()

    count = ahead_count(db_session, fp.id, now)
    assert count == 2


def test_dispatch_fair_visual_news_lookahead_vs_normal(db_session, fake_redis):
    # 8. dispatch: lookahead job -> claim_page NOT called; normal job -> called
    fp = _create_fanpage(db_session, 1, "Page 1")
    slot_utc = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)

    job_lookahead = _create_job(db_session, id=301, fanpage_id=fp.id)
    job_lookahead._lookahead = True
    job_lookahead._jit_slot = slot_utc

    claim_key = CLAIM_KEY.format(fanpage_id=fp.id)

    # Dispatch lookahead job: claim_page NOT called
    with patch("app.tasks.design_renderer._redis", return_value=fake_redis), \
         patch("app.tasks.design_renderer.SessionLocal", return_value=db_session), \
         patch("app.services.visual_fair_queue.news_job_waiting", return_value=False), \
         patch("app.services.visual_fair_queue.pick_next_news_job", return_value=job_lookahead), \
         patch("app.tasks.design_renderer._acquire_queued_lock", return_value=True), \
         patch("app.tasks.design_renderer.render_design.apply_async"):
        res = dispatch_fair_visual_news()

    assert res == f"dispatched {job_lookahead.id} lookahead"
    assert not fake_redis.exists(claim_key)

    # Dispatch normal job: claim_page IS called
    job_normal = _create_job(db_session, id=302, fanpage_id=fp.id)
    job_normal._jit_slot = slot_utc

    with patch("app.tasks.design_renderer._redis", return_value=fake_redis), \
         patch("app.tasks.design_renderer.SessionLocal", return_value=db_session), \
         patch("app.services.visual_fair_queue.news_job_waiting", return_value=False), \
         patch("app.services.visual_fair_queue.pick_next_news_job", return_value=job_normal), \
         patch("app.tasks.design_renderer._acquire_queued_lock", return_value=True), \
         patch("app.tasks.design_renderer.render_design.apply_async"):
        res2 = dispatch_fair_visual_news()

    assert res2 == f"dispatched {job_normal.id}"
    assert fake_redis.exists(claim_key)


def test_lookahead_horizon_chatgpt_mode(db_session):
    # 1. ChatGPT mode: chatgpt_can_look_ahead True -> horizon = now + 6 h
    now = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    with patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=True):
        horizon = lookahead_horizon(db_session, now)
    assert horizon == now + timedelta(hours=6)


def test_lookahead_horizon_night_codex_mode(db_session, fake_redis):
    # 2. Night 03:00 WIB (night 02:00–05:30), codex allowed, visual queue 0 -> horizon = 05:30 WIB today;
    # a slot at 04:40 WIB is picked with _lookahead; a slot at 06:10 WIB is NOT picked (break).
    wib = ZoneInfo("Asia/Jakarta")
    now = datetime(2026, 10, 22, 3, 0, tzinfo=wib).astimezone(timezone.utc)
    expected_horizon = datetime(2026, 10, 22, 5, 30, tzinfo=wib).astimezone(timezone.utc)

    with patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=False), \
         patch("app.services.visual_fair_queue.night_settings", return_value=("02:00", "05:30")), \
         patch("app.services.engine_router.codex_allowed", return_value=(True, "ok")), \
         patch("app.services.engine_router.visual_queue_len", return_value=0):
        horizon = lookahead_horizon(db_session, now)
    assert horizon == expected_horizon

    # Slot at 04:40 WIB is picked with _lookahead
    fp = _create_fanpage(db_session, 401, "Page 401")
    _create_job(db_session, id=4011, fanpage_id=fp.id)
    slot_440 = datetime(2026, 10, 22, 4, 40, tzinfo=wib).astimezone(timezone.utc)

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=False), \
         patch("app.services.visual_fair_queue.night_settings", return_value=("02:00", "05:30")), \
         patch("app.services.engine_router.codex_allowed", return_value=(True, "ok")), \
         patch("app.services.engine_router.visual_queue_len", return_value=0), \
         patch("app.services.visual_fair_queue.ahead_count", return_value=0), \
         patch("app.services.visual_fair_queue._next_slot", return_value=slot_440), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked = pick_next_news_job(db_session, fake_redis, now)

    assert picked is not None
    assert picked.id == _ID_BASE + 4011
    assert getattr(picked, "_lookahead", False) is True
    assert getattr(picked, "_jit_slot", None) == slot_440

    # A slot at 06:10 WIB is NOT picked (break)
    slot_610 = datetime(2026, 10, 22, 6, 10, tzinfo=wib).astimezone(timezone.utc)
    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=False), \
         patch("app.services.visual_fair_queue.night_settings", return_value=("02:00", "05:30")), \
         patch("app.services.engine_router.codex_allowed", return_value=(True, "ok")), \
         patch("app.services.engine_router.visual_queue_len", return_value=0), \
         patch("app.services.visual_fair_queue.ahead_count", return_value=0), \
         patch("app.services.visual_fair_queue._next_slot", return_value=slot_610), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked_late = pick_next_news_job(db_session, fake_redis, now)

    assert picked_late is None


def test_lookahead_night_codex_disallowed(db_session, fake_redis):
    # 3. Night but codex_allowed False -> None -> slot 04:40 not picked (only normal 30-min JIT)
    wib = ZoneInfo("Asia/Jakarta")
    now = datetime(2026, 10, 22, 3, 0, tzinfo=wib).astimezone(timezone.utc)

    with patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=False), \
         patch("app.services.visual_fair_queue.night_settings", return_value=("02:00", "05:30")), \
         patch("app.services.engine_router.codex_allowed", return_value=(False, "codex_resting")), \
         patch("app.services.engine_router.visual_queue_len", return_value=0):
        horizon = lookahead_horizon(db_session, now)
    assert horizon is None

    # Slot 04:40 not picked
    fp = _create_fanpage(db_session, 402, "Page 402")
    _create_job(db_session, id=4021, fanpage_id=fp.id)
    slot_440 = datetime(2026, 10, 22, 4, 40, tzinfo=wib).astimezone(timezone.utc)

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=False), \
         patch("app.services.visual_fair_queue.night_settings", return_value=("02:00", "05:30")), \
         patch("app.services.engine_router.codex_allowed", return_value=(False, "codex_resting")), \
         patch("app.services.engine_router.visual_queue_len", return_value=0), \
         patch("app.services.visual_fair_queue.ahead_count", return_value=0), \
         patch("app.services.visual_fair_queue._next_slot", return_value=slot_440), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked_440 = pick_next_news_job(db_session, fake_redis, now)

    assert picked_440 is None

    # Normal 30-min JIT slot (e.g. 03:20 WIB) is picked without _lookahead
    slot_320 = datetime(2026, 10, 22, 3, 20, tzinfo=wib).astimezone(timezone.utc)
    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=False), \
         patch("app.services.visual_fair_queue.night_settings", return_value=("02:00", "05:30")), \
         patch("app.services.engine_router.codex_allowed", return_value=(False, "codex_resting")), \
         patch("app.services.engine_router.visual_queue_len", return_value=0), \
         patch("app.services.visual_fair_queue._next_slot", return_value=slot_320), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked_320 = pick_next_news_job(db_session, fake_redis, now)

    assert picked_320 is not None
    assert getattr(picked_320, "_lookahead", False) is False
    assert getattr(picked_320, "_jit_slot", None) == slot_320


def test_lookahead_daytime_chatgpt_unavailable(db_session):
    # 4. Daytime, ChatGPT unavailable until now+2 h, codex allowed -> horizon = now+2 h
    now = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    until = now + timedelta(hours=2)

    with patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=False), \
         patch("app.services.visual_fair_queue.night_settings", return_value=("00:00", "05:30")), \
         patch("app.services.engine_router.is_night", return_value=False), \
         patch("app.services.gptcli_client.gptcli_unavailable_until", return_value=until), \
         patch("app.services.engine_router.codex_allowed", return_value=(True, "ok")), \
         patch("app.services.engine_router.visual_queue_len", return_value=0):
        horizon = lookahead_horizon(db_session, now)

    assert horizon == until


def test_lookahead_daytime_chatgpt_over_budget(db_session):
    # 5. Daytime, ChatGPT over budget (patch chatgpt_can_look_ahead False, unavailable None, not night) -> None
    now = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)

    with patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=False), \
         patch("app.services.visual_fair_queue.night_settings", return_value=("00:00", "05:30")), \
         patch("app.services.engine_router.is_night", return_value=False), \
         patch("app.services.gptcli_client.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.codex_allowed", return_value=(True, "ok")), \
         patch("app.services.engine_router.visual_queue_len", return_value=0):
        horizon = lookahead_horizon(db_session, now)

    assert horizon is None


def test_lookahead_codex_visual_queue_not_empty(db_session):
    # 6. visual_queue_len 1 in Codex mode -> None
    wib = ZoneInfo("Asia/Jakarta")
    now_night = datetime(2026, 10, 22, 3, 0, tzinfo=wib).astimezone(timezone.utc)

    # Night codex mode, visual_queue_len 1 -> None
    with patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=False), \
         patch("app.services.visual_fair_queue.night_settings", return_value=("02:00", "05:30")), \
         patch("app.services.engine_router.codex_allowed", return_value=(True, "ok")), \
         patch("app.services.engine_router.visual_queue_len", return_value=1):
        assert lookahead_horizon(db_session, now_night) is None

    # Daytime unavailable codex mode, visual_queue_len 1 -> None
    now_day = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    until = now_day + timedelta(hours=2)
    with patch("app.services.visual_fair_queue.chatgpt_can_look_ahead", return_value=False), \
         patch("app.services.visual_fair_queue.night_settings", return_value=("00:00", "05:30")), \
         patch("app.services.engine_router.is_night", return_value=False), \
         patch("app.services.gptcli_client.gptcli_unavailable_until", return_value=until), \
         patch("app.services.engine_router.codex_allowed", return_value=(True, "ok")), \
         patch("app.services.engine_router.visual_queue_len", return_value=1):
        assert lookahead_horizon(db_session, now_day) is None
