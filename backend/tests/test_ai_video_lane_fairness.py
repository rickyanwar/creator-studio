"""Group Share lanes take turns with brand pages in the Mode 8 claim round-robin."""
from datetime import timedelta
import json
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker

from app.models.ai_video import GflowAccount, GflowWorker, VideoIdea, VideoJob
from app.models.fb_group_share import FbShareGroupJob, FbShareLane
from app.models.publish_jobs import PublishJob
from app.models.settings import Settings
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services import ai_video_dispatch

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"


@pytest.fixture(autouse=True)
def _no_claim_gap(monkeypatch):
    monkeypatch.setattr(ai_video_dispatch, "MIN_CLAIM_GAP_MINUTES", 0)


@pytest.fixture
def db_session(monkeypatch):
    engine = create_engine("sqlite://")
    models = (Settings, GflowWorker, GflowAccount, TargetFanpage, PublishJob, VideoIdea, VideoJob)
    # Postgres-only casts in server defaults ("'[]'::json") do not compile on SQLite.
    for model in models:
        for column in model.__table__.columns:
            if "::" in str(getattr(column.server_default, "arg", "")):
                monkeypatch.setattr(column, "server_default", None)
    for model in models:
        model.__table__.create(engine, checkfirst=True)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def _fanpage(db, suffix):
    fp = TargetFanpage(
        name=f"Page {suffix}", username=f"page_{suffix}", is_active=True,
        publish_mode=PublishMode.auto, ai_video_enabled=True, ai_video_daily_count=3,
    )
    db.add(fp)
    db.commit()
    db.refresh(fp)
    return fp


def _job(db, fp, *, status="queued", created_offset_min=0, started_offset_min=None):
    now = ai_video_dispatch._utcnow()
    job = VideoJob(
        fanpage_id=fp.id, template_key="motorsport_tech_explainer", title=f"job {fp.name}", caption="c",
        model="omni-flash", aspect="9:16", clip_seconds=10, clips=[{"index": 1}],
        credits_per_clip=15, credits_estimate=15, status=status,
        created_at=now - timedelta(minutes=created_offset_min),
        started_at=(now - timedelta(minutes=started_offset_min)) if started_offset_min is not None else None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@pytest.fixture
def worker(db_session):
    s = db_session.query(Settings).first()
    if not s:
        db_session.add(Settings(id=1, ai_video_worker_enabled=True, ai_video_credit_reserve=0))
    else:
        s.ai_video_worker_enabled = True
        s.ai_video_credit_reserve = 0
    db_session.add(GflowWorker(worker_id="w1", version="1", running_job_ids=[]))
    db_session.add(GflowAccount(worker_id="w1", profile="p1", authenticated=True, status="ok", credits=500))
    db_session.commit()
    return "w1"


@pytest.fixture
def brand_and_lane(db_session):
    brand = _fanpage(db_session, "brand")
    lane = _fanpage(db_session, "lane")
    db_session.add(FbShareLane(brand_fanpage_id=brand.id, lane_fanpage_id=lane.id))
    db_session.commit()
    return brand, lane


def test_lane_jobs_are_marked_group_jobs(db_session, brand_and_lane):
    _, lane = brand_and_lane
    job = _job(db_session, lane)
    assert db_session.query(FbShareGroupJob).filter_by(video_job_id=job.id).count() == 1


def test_lane_with_more_videos_today_waits_behind_brand(db_session, worker, brand_and_lane):
    brand, lane = brand_and_lane
    # The lane already started two (group) videos today; the brand none.
    _job(db_session, lane, status="done", started_offset_min=30)
    _job(db_session, lane, status="done", started_offset_min=10)
    old_lane = _job(db_session, lane, created_offset_min=120)
    new_brand = _job(db_session, brand, created_offset_min=5)

    claimed = ai_video_dispatch.claim_job(db_session, worker)

    assert claimed is not None
    assert claimed[0].id == new_brand.id
    assert old_lane.status == "queued"


def test_lane_served_recently_waits_behind_brand_served_earlier(db_session, worker, brand_and_lane):
    brand, lane = brand_and_lane
    _job(db_session, lane, status="done", started_offset_min=5)
    _job(db_session, brand, status="done", started_offset_min=60)
    _job(db_session, lane, created_offset_min=90)
    brand_job = _job(db_session, brand, created_offset_min=10)

    claimed = ai_video_dispatch.claim_job(db_session, worker)

    assert claimed[0].id == brand_job.id


def test_lanes_take_turns_with_each_other(db_session, worker, brand_and_lane):
    _, lane_a = brand_and_lane
    other_brand = _fanpage(db_session, "brand2")
    lane_b = _fanpage(db_session, "lane2")
    db_session.add(FbShareLane(brand_fanpage_id=other_brand.id, lane_fanpage_id=lane_b.id))
    db_session.commit()
    _job(db_session, lane_a, status="done", started_offset_min=20)
    _job(db_session, lane_a, created_offset_min=120)
    lane_b_job = _job(db_session, lane_b, created_offset_min=5)

    claimed = ai_video_dispatch.claim_job(db_session, worker)

    assert claimed[0].id == lane_b_job.id
