"""Round-robin claim order across fan pages, and sweep not restarting a pinned job whose account is busy."""
from datetime import timedelta

import pytest

from app.database import Base, SessionLocal, engine
from app.models.ai_video import GflowAccount, GflowWorker, VideoIdea, VideoJob
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.settings import Settings
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services import ai_video_dispatch

TAG = "repliz_fp_round_robin"


@pytest.fixture(autouse=True)
def _no_claim_gap(monkeypatch):
    monkeypatch.setattr(ai_video_dispatch, "MIN_CLAIM_GAP_MINUTES", 0)


@pytest.fixture(scope="module")
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


def _clean(session):
    session.query(PublishJob).delete()
    session.query(VideoJob).delete()
    session.query(VideoIdea).delete()
    session.query(GflowAccount).delete()
    session.query(GflowWorker).delete()
    session.query(TargetFanpage).filter(TargetFanpage.repliz_account_id.like(f"{TAG}%")).delete(synchronize_session=False)
    session.commit()


@pytest.fixture
def db_session(setup_db):
    session = SessionLocal()
    try:
        _clean(session)
        yield session
    finally:
        _clean(session)
        session.close()


def _fanpage(db, suffix, daily_count=1):
    fp = TargetFanpage(
        repliz_account_id=f"{TAG}_{suffix}", name=f"Page {suffix}", username=f"page{suffix}",
        is_active=True, publish_mode=PublishMode.auto, ai_video_enabled=True,
        ai_video_daily_count=daily_count,
    )
    db.add(fp)
    db.commit()
    db.refresh(fp)
    return fp


def _add_video_stock(db, fp, count: int, status=PublishJobStatus.pending_publish):
    for _ in range(count):
        db.add(PublishJob(
            fanpage_id=fp.id,
            content_type=ContentType.ai_video,
            status=status,
            is_deleted=False,
        ))
    db.commit()


def _settings(db):
    s = db.query(Settings).first()
    if not s:
        db.add(Settings(id=1, ai_video_worker_enabled=True, ai_video_credit_reserve=0))
    else:
        s.ai_video_worker_enabled = True
        s.ai_video_credit_reserve = 0
    db.commit()


def _job(db, fp, *, status="queued", created_offset_min=0, started_offset_min=None, account_id=None):
    now = ai_video_dispatch._utcnow()
    job = VideoJob(
        fanpage_id=fp.id, template_key="motorsport_tech_explainer", title=f"job {fp.name}", caption="c",
        model="omni-flash", aspect="9:16", clip_seconds=10, clips=[{"index": 1}],
        credits_per_clip=15, credits_estimate=15, status=status, account_id=account_id,
        created_at=now - timedelta(minutes=created_offset_min),
        started_at=(now - timedelta(minutes=started_offset_min)) if started_offset_min is not None else None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_page_with_fewer_videos_today_goes_first(db_session):
    _settings(db_session)
    db_session.add(GflowWorker(worker_id="w1", version="1", running_job_ids=[]))
    db_session.add(GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=500))
    db_session.commit()
    page_a = _fanpage(db_session, "a")
    page_b = _fanpage(db_session, "b")
    # Page A already had two videos today; page B none.
    _job(db_session, page_a, status="done", started_offset_min=30)
    _job(db_session, page_a, status="done", started_offset_min=10)
    old_a = _job(db_session, page_a, created_offset_min=120)  # oldest queued job, but page A is ahead
    new_b = _job(db_session, page_b, created_offset_min=5)

    claimed = ai_video_dispatch.claim_job(db_session, "w1")

    assert claimed is not None
    assert claimed[0].id == new_b.id
    assert old_a.status == "queued"


def test_equal_counts_fall_back_to_least_recently_served(db_session):
    _settings(db_session)
    db_session.add(GflowWorker(worker_id="w1", version="1", running_job_ids=[]))
    db_session.add(GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=500))
    db_session.commit()
    page_a = _fanpage(db_session, "a")
    page_b = _fanpage(db_session, "b")
    _job(db_session, page_a, status="done", started_offset_min=5)    # A served 5 min ago
    _job(db_session, page_b, status="done", started_offset_min=60)   # B served 60 min ago
    _job(db_session, page_a, created_offset_min=90)
    job_b = _job(db_session, page_b, created_offset_min=10)

    claimed = ai_video_dispatch.claim_job(db_session, "w1")

    assert claimed[0].id == job_b.id


def test_wib_day_start_is_midnight_jakarta():
    from datetime import datetime
    # 2026-10-21 03:00 UTC = 10:00 WIB → the WIB day started 2026-10-20 17:00 UTC.
    assert ai_video_dispatch._wib_day_start_utc(datetime(2026, 10, 21, 3, 0)) == datetime(2026, 10, 20, 17, 0)
    # 2026-10-21 18:00 UTC = 01:00 WIB on the 22nd → day started 2026-10-21 17:00 UTC.
    assert ai_video_dispatch._wib_day_start_utc(datetime(2026, 10, 21, 18, 0)) == datetime(2026, 10, 21, 17, 0)


def test_sweep_keeps_pinned_job_whose_account_is_busy_or_resting(db_session):
    _settings(db_session)
    now = ai_video_dispatch._utcnow()
    db_session.add(GflowWorker(worker_id="w1", version="1", running_job_ids=[]))
    acc = GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=500,
                       cooldown_until=now + timedelta(minutes=10))
    db_session.add(acc)
    db_session.commit()
    page = _fanpage(db_session, "a")
    _job(db_session, page, status="generating", account_id=acc.id)   # account busy with another job
    pinned = _job(db_session, page, account_id=acc.id)
    pinned.clips = [{"index": 1}, {"index": 2}]
    pinned.clips_done = [{"index": 1, "media_id": "m1"}]
    pinned.last_progress_at = now - timedelta(hours=ai_video_dispatch.PIN_MAX_HOURS + 1)
    db_session.commit()
    generation_before = pinned.generation

    ai_video_dispatch.sweep(db_session)
    db_session.refresh(pinned)

    assert pinned.generation == generation_before
    assert pinned.account_id == acc.id


def test_stock_heavy_page_waits_behind_thinner_page(db_session):
    _settings(db_session)
    db_session.add(GflowWorker(worker_id="w1", version="1", running_job_ids=[]))
    db_session.add(GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=500))
    db_session.commit()
    page_a = _fanpage(db_session, "a", daily_count=3)
    page_b = _fanpage(db_session, "b", daily_count=1)
    _add_video_stock(db_session, page_a, 6)
    _job(db_session, page_a, status="done", started_offset_min=2000)
    _job(db_session, page_b, status="done", started_offset_min=1500)
    old_a = _job(db_session, page_a, created_offset_min=120)
    job_b = _job(db_session, page_b, created_offset_min=10)

    claimed = ai_video_dispatch.claim_job(db_session, "w1")

    assert claimed is not None
    assert claimed[0].id == job_b.id
    assert old_a.status == "queued"


def test_stock_heavy_page_still_claimed_if_only_queued_job(db_session):
    _settings(db_session)
    db_session.add(GflowWorker(worker_id="w1", version="1", running_job_ids=[]))
    db_session.add(GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=500))
    db_session.commit()
    page_a = _fanpage(db_session, "a", daily_count=3)
    _add_video_stock(db_session, page_a, 6)
    job_a = _job(db_session, page_a, created_offset_min=10)

    claimed = ai_video_dispatch.claim_job(db_session, "w1")

    assert claimed is not None
    assert claimed[0].id == job_a.id


def test_stock_below_threshold_follows_normal_round_robin(db_session):
    _settings(db_session)
    db_session.add(GflowWorker(worker_id="w1", version="1", running_job_ids=[]))
    db_session.add(GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=500))
    db_session.commit()
    page_a = _fanpage(db_session, "a", daily_count=3)
    page_b = _fanpage(db_session, "b", daily_count=1)
    _add_video_stock(db_session, page_a, 5)
    _job(db_session, page_a, status="done", started_offset_min=2000)
    _job(db_session, page_b, status="done", started_offset_min=1500)
    job_a = _job(db_session, page_a, created_offset_min=10)
    job_b = _job(db_session, page_b, created_offset_min=10)

    claimed = ai_video_dispatch.claim_job(db_session, "w1")

    assert claimed is not None
    assert claimed[0].id == job_a.id
