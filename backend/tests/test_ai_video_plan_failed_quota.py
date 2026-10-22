from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.database import Base, SessionLocal, engine
from app.models.ai_video import VideoIdea, VideoJob
from app.models.target_fanpages import TargetFanpage
import app.tasks.ai_video_plan as planner
from app.tasks.ai_video_plan import _daily_counts


# ── Unit tests for _daily_counts ──────────────────────────────────────────


def test_daily_counts_all_done():
    jobs = [
        SimpleNamespace(status="done", credits_estimate=45, credits_per_clip=15, clips_done=[1, 2, 3]),
        SimpleNamespace(status="done", credits_estimate=30, credits_per_clip=15, clips_done=[1, 2]),
        SimpleNamespace(status="done", credits_estimate=60, credits_per_clip=15, clips_done=[1, 2, 3, 4]),
    ]
    count, credits_spent = _daily_counts(jobs)
    assert count == 3
    assert credits_spent == 45 + 30 + 60


def test_daily_counts_done_and_one_failed():
    jobs = [
        SimpleNamespace(status="done", credits_estimate=45, credits_per_clip=15, clips_done=[1, 2, 3]),
        SimpleNamespace(status="done", credits_estimate=30, credits_per_clip=15, clips_done=[1, 2]),
        SimpleNamespace(status="failed", credits_estimate=45, credits_per_clip=15, clips_done=[{"index": 1}]),
    ]
    count, credits_spent = _daily_counts(jobs)
    assert count == 2
    assert credits_spent == 45 + 30 + 15


def test_daily_counts_one_done_three_failed():
    jobs = [
        SimpleNamespace(status="done", credits_estimate=45, credits_per_clip=15, clips_done=[1, 2, 3]),
        SimpleNamespace(status="failed", credits_estimate=45, credits_per_clip=15, clips_done=[1]),
        SimpleNamespace(status="failed", credits_estimate=45, credits_per_clip=15, clips_done=[1]),
        SimpleNamespace(status="failed", credits_estimate=45, credits_per_clip=15, clips_done=[1]),
    ]
    count, credits_spent = _daily_counts(jobs)
    assert count == 2
    assert credits_spent == 45 + 15 + 15 + 15


def test_daily_counts_failed_clips_done_none():
    jobs = [
        SimpleNamespace(status="failed", credits_estimate=45, credits_per_clip=15, clips_done=None),
    ]
    count, credits_spent = _daily_counts(jobs)
    assert count == 0
    assert credits_spent == 0


# ── Integration tests (Postgres) ──────────────────────────────────────────


@pytest.fixture(scope="module", autouse=True)
def create_tables():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    session.query(VideoJob).delete(synchronize_session=False)
    session.query(VideoIdea).delete(synchronize_session=False)
    session.query(TargetFanpage).delete(synchronize_session=False)
    session.commit()
    session.close()
    yield
    session = SessionLocal()
    session.query(VideoJob).delete(synchronize_session=False)
    session.query(VideoIdea).delete(synchronize_session=False)
    session.query(TargetFanpage).delete(synchronize_session=False)
    session.commit()
    session.close()


@pytest.fixture
def db():
    session = SessionLocal()
    fanpage_ids = []
    try:
        yield session, fanpage_ids
    finally:
        session.rollback()
        if fanpage_ids:
            session.query(VideoJob).filter(VideoJob.fanpage_id.in_(fanpage_ids)).delete(synchronize_session=False)
            session.query(VideoIdea).filter(VideoIdea.fanpage_id.in_(fanpage_ids)).delete(synchronize_session=False)
            session.query(TargetFanpage).filter(TargetFanpage.id.in_(fanpage_ids)).delete(synchronize_session=False)
            session.commit()
        session.close()


def _fanpage(session, fanpage_ids, daily_count=3):
    fanpage = TargetFanpage(
        repliz_account_id=f"quota-{uuid4().hex}",
        name=f"Quota {uuid4().hex}",
        is_active=True,
        is_connected=True,
        ai_video_enabled=True,
        ai_video_daily_count=daily_count,
        ai_video_min_score=None,
        ai_video_clips=3,
    )
    session.add(fanpage)
    session.flush()
    fanpage_ids.append(fanpage.id)
    return fanpage


def _idea(session, fanpage, topic):
    idea = VideoIdea(
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        topic=topic,
        status="approved",
        score=90,
        rationale="r",
    )
    session.add(idea)
    session.flush()
    return idea


def _job(session, fanpage, status="done", credits_estimate=45, credits_per_clip=15, clips_done=None):
    job = VideoJob(
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Existing job title",
        caption="Existing job caption",
        model="veo2",
        aspect="9:16",
        clip_seconds=5,
        clips=[{"index": 1}],
        credits_per_clip=credits_per_clip,
        credits_estimate=credits_estimate,
        status=status,
        clips_done=clips_done if clips_done is not None else [],
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(job)
    session.flush()
    return job


def _fill_result():
    return (
        {"part": "rear wing"},
        "Generated title",
        "Generated caption",
        [{"index": 1, "mode": "t2v", "prompt": "prompt"}],
        "mock",
    )


def test_plan_creates_job_when_failed_within_replacement_allowance(db, monkeypatch):
    session, fanpage_ids = db
    fanpage = _fanpage(session, fanpage_ids, daily_count=3)
    _job(session, fanpage, status="done")
    _job(session, fanpage, status="done")
    _job(session, fanpage, status="failed", clips_done=[{"index": 1}])
    idea = _idea(session, fanpage, "test-replacement")
    idea_id = idea.id
    session.commit()

    monkeypatch.setattr(planner, "SessionLocal", SessionLocal)
    with patch.object(planner, "fill_idea", return_value=_fill_result()):
        planner.plan_ai_videos.run()

    verify = SessionLocal()
    new_jobs = verify.query(VideoJob).filter(VideoJob.fanpage_id == fanpage.id, VideoJob.status == "queued").all()
    assert len(new_jobs) == 1
    assert new_jobs[0].idea_id == idea_id
    assert verify.get(VideoIdea, idea_id).status == "queued"
    verify.close()


def test_plan_does_not_create_job_when_daily_count_reached_with_done_jobs(db, monkeypatch):
    session, fanpage_ids = db
    fanpage = _fanpage(session, fanpage_ids, daily_count=3)
    _job(session, fanpage, status="done")
    _job(session, fanpage, status="done")
    _job(session, fanpage, status="done")
    idea = _idea(session, fanpage, "test-full-quota")
    idea_id = idea.id
    session.commit()

    monkeypatch.setattr(planner, "SessionLocal", SessionLocal)
    with patch.object(planner, "fill_idea", return_value=_fill_result()):
        planner.plan_ai_videos.run()

    verify = SessionLocal()
    new_jobs = verify.query(VideoJob).filter(VideoJob.fanpage_id == fanpage.id, VideoJob.status == "queued").all()
    assert len(new_jobs) == 0
    assert verify.get(VideoIdea, idea_id).status == "approved"
    verify.close()


def test_plan_failed_replacement_cap_boundary(db, monkeypatch):
    session, fanpage_ids = db
    # 1 done + 3 failed with daily_count 3 -> count is 1 + (3 - 2) = 2 < 3 -> new job created
    fanpage_a = _fanpage(session, fanpage_ids, daily_count=3)
    _job(session, fanpage_a, status="done")
    for _ in range(3):
        _job(session, fanpage_a, status="failed", clips_done=[{"index": 1}])
    idea_a = _idea(session, fanpage_a, "test-boundary-allow")
    idea_a_id = idea_a.id

    # 1 done + 4 failed with daily_count 3 -> count is 1 + (4 - 2) = 3 >= 3 -> no new job
    fanpage_b = _fanpage(session, fanpage_ids, daily_count=3)
    _job(session, fanpage_b, status="done")
    for _ in range(4):
        _job(session, fanpage_b, status="failed", clips_done=[{"index": 1}])
    idea_b = _idea(session, fanpage_b, "test-boundary-block")
    idea_b_id = idea_b.id

    session.commit()

    monkeypatch.setattr(planner, "SessionLocal", SessionLocal)
    with patch.object(planner, "fill_idea", return_value=_fill_result()):
        planner.plan_ai_videos.run()

    verify = SessionLocal()
    new_jobs_a = verify.query(VideoJob).filter(VideoJob.fanpage_id == fanpage_a.id, VideoJob.status == "queued").all()
    assert len(new_jobs_a) == 1
    assert new_jobs_a[0].idea_id == idea_a_id
    assert verify.get(VideoIdea, idea_a_id).status == "queued"

    new_jobs_b = verify.query(VideoJob).filter(VideoJob.fanpage_id == fanpage_b.id, VideoJob.status == "queued").all()
    assert len(new_jobs_b) == 0
    assert verify.get(VideoIdea, idea_b_id).status == "approved"
    verify.close()
