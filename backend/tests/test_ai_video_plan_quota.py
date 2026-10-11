from datetime import datetime
from uuid import uuid4
from unittest.mock import patch

import pytest

from app.database import Base, SessionLocal, engine
from app.models.ai_video import VideoIdea, VideoJob
from app.models.target_fanpages import TargetFanpage


@pytest.fixture(scope="module", autouse=True)
def create_tables():
    Base.metadata.create_all(bind=engine)


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


def _fanpage(session, fanpage_ids):
    fanpage = TargetFanpage(
        repliz_account_id=f"quota-{uuid4().hex}",
        name=f"Quota {uuid4().hex}",
        is_active=True,
        is_connected=True,
        ai_video_enabled=True,
        ai_video_daily_count=1,
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


def _fill_result():
    return (
        {"part": "rear wing"},
        "Generated title",
        "Generated caption",
        [{"index": 1, "mode": "t2v", "prompt": "prompt"}],
        "mock",
    )


def test_llm_unavailable_preserves_approval_and_stops_tick(db, monkeypatch):
    session, fanpage_ids = db
    first = _fanpage(session, fanpage_ids)
    second = _fanpage(session, fanpage_ids)
    first_idea = _idea(session, first, "first")
    second_idea = _idea(session, second, "second")
    first_idea_id, second_idea_id = first_idea.id, second_idea.id
    session.commit()

    import app.tasks.ai_video_plan as planner

    monkeypatch.setattr(planner, "SessionLocal", SessionLocal)
    with patch.object(planner, "fill_idea", side_effect=RuntimeError("router unavailable")) as fill:
        planner.plan_ai_videos.run()

    verify = SessionLocal()
    assert fill.call_count == 1
    assert verify.get(VideoIdea, first_idea_id).status == "approved"
    assert verify.get(VideoIdea, first_idea_id).error == "LLM unavailable, will retry: RuntimeError: router unavailable"
    assert verify.get(VideoIdea, second_idea_id).status == "approved"
    assert verify.query(VideoJob).filter(VideoJob.fanpage_id.in_(fanpage_ids)).count() == 0
    verify.close()


def test_content_value_error_fails_idea_and_continues_to_next_fanpage(db, monkeypatch):
    session, fanpage_ids = db
    first = _fanpage(session, fanpage_ids)
    second = _fanpage(session, fanpage_ids)
    first_idea = _idea(session, first, "first")
    second_idea = _idea(session, second, "second")
    first_idea_id, second_idea_id = first_idea.id, second_idea.id
    session.commit()

    import app.tasks.ai_video_plan as planner

    monkeypatch.setattr(planner, "SessionLocal", SessionLocal)
    with patch.object(planner, "fill_idea", side_effect=[ValueError("bad content"), _fill_result()]) as fill:
        planner.plan_ai_videos.run()

    verify = SessionLocal()
    assert fill.call_count == 2
    assert verify.get(VideoIdea, first_idea_id).status == "failed"
    assert "bad content" in verify.get(VideoIdea, first_idea_id).error
    assert verify.get(VideoIdea, second_idea_id).status == "queued"
    assert verify.query(VideoJob).filter(VideoJob.idea_id == second_idea_id).count() == 1
    verify.close()


def test_successful_retry_creates_job_and_clears_unavailable_error(db, monkeypatch):
    session, fanpage_ids = db
    fanpage = _fanpage(session, fanpage_ids)
    idea = _idea(session, fanpage, "retry")
    idea_id = idea.id
    session.commit()

    import app.tasks.ai_video_plan as planner

    monkeypatch.setattr(planner, "SessionLocal", SessionLocal)
    with patch.object(planner, "fill_idea", side_effect=RuntimeError("timeout")):
        planner.plan_ai_videos.run()

    verify = SessionLocal()
    assert verify.get(VideoIdea, idea_id).status == "approved"
    assert verify.get(VideoIdea, idea_id).error.startswith("LLM unavailable, will retry:")
    verify.close()


def test_failed_job_commit_rolls_back_and_leaves_idea_approved(db, monkeypatch):
    session, fanpage_ids = db
    fanpage = _fanpage(session, fanpage_ids)
    idea = _idea(session, fanpage, "commit failure")
    idea_id = idea.id
    session.commit()

    import app.tasks.ai_video_plan as planner

    monkeypatch.setattr(planner, "SessionLocal", lambda: session)
    original_commit = session.commit
    commit_count = 0

    def fail_job_commit():
        nonlocal commit_count
        commit_count += 1
        if commit_count == 2:
            raise RuntimeError("database unavailable")
        original_commit()

    monkeypatch.setattr(session, "commit", fail_job_commit)
    with patch.object(planner, "fill_idea", return_value=_fill_result()):
        planner.plan_ai_videos.run()

    verify = SessionLocal()
    stored = verify.get(VideoIdea, idea_id)
    assert stored.status == "approved"
    assert stored.error == "LLM unavailable, will retry: RuntimeError: database unavailable"
    assert verify.query(VideoJob).filter(VideoJob.idea_id == idea_id).count() == 0
    verify.close()

    with patch.object(planner, "fill_idea", return_value=_fill_result()):
        planner.plan_ai_videos.run()

    verify = SessionLocal()
    stored = verify.get(VideoIdea, idea_id)
    assert stored.status == "queued"
    assert stored.error is None
    assert verify.query(VideoJob).filter(VideoJob.idea_id == idea_id).count() == 1
    verify.close()
