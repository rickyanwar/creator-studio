from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.database import Base, SessionLocal, engine
from app.models.ai_video import VideoIdea, VideoJob
from app.models.target_fanpages import TargetFanpage
from app.schemas.fanpage import FanpageUpdate
from app.services.video_templates import clip_choices, fit_clips_to_credits, get_template


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


def _fanpage(session, fanpage_ids, **kwargs):
    params = dict(
        repliz_account_id=f"lencred-{uuid4().hex}",
        name=f"LenCred {uuid4().hex}",
        is_active=True,
        is_connected=True,
        ai_video_enabled=True,
        ai_video_daily_count=5,
        ai_video_min_score=None,
        ai_video_clips=3,
        ai_video_templates=["motorsport_tech_explainer"],
    )
    params.update(kwargs)
    fanpage = TargetFanpage(**params)
    session.add(fanpage)
    session.flush()
    fanpage_ids.append(fanpage.id)
    return fanpage


def _idea(session, fanpage, topic="topic", **kwargs):
    params = dict(
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        topic=topic,
        status="approved",
        score=90,
        rationale="r",
    )
    params.update(kwargs)
    idea = VideoIdea(**params)
    session.add(idea)
    session.flush()
    return idea


def test_clip_choices_seconds_range():
    tech = get_template("motorsport_tech_explainer")
    assert tech.clip_options == (2, 3)
    assert tech.clip_seconds == 10

    # range 20–30 -> [2, 3]
    fp = SimpleNamespace(ai_video_seconds_min=20, ai_video_seconds_max=30)
    assert clip_choices(fp, tech) == [2, 3]

    # 25–30 -> [3]
    fp = SimpleNamespace(ai_video_seconds_min=25, ai_video_seconds_max=30)
    assert clip_choices(fp, tech) == [3]

    # 5–10 -> [2]
    fp = SimpleNamespace(ai_video_seconds_min=5, ai_video_seconds_max=10)
    assert clip_choices(fp, tech) == [2]

    # 60–600 -> [3]
    fp = SimpleNamespace(ai_video_seconds_min=60, ai_video_seconds_max=600)
    assert clip_choices(fp, tech) == [3]

    # seconds None -> old clip-count behaviour unchanged
    fp_old1 = SimpleNamespace(ai_video_seconds_min=None, ai_video_seconds_max=None, ai_video_clips=2, ai_video_clips_min=None)
    assert clip_choices(fp_old1, tech) == [2]

    fp_old2 = SimpleNamespace(ai_video_seconds_min=None, ai_video_seconds_max=None, ai_video_clips=3, ai_video_clips_min=2)
    assert clip_choices(fp_old2, tech) == [2, 3]


def test_fit_clips_to_credits():
    tech = get_template("motorsport_tech_explainer")
    # budget None -> same
    assert fit_clips_to_credits(tech, 3, None) == 3
    assert fit_clips_to_credits(tech, 2, None) == 2

    # 3 clips budget 30 -> 2
    assert fit_clips_to_credits(tech, 3, 30) == 2

    # budget 20 -> None
    assert fit_clips_to_credits(tech, 3, 20) is None
    assert fit_clips_to_credits(tech, 2, 20) is None


def test_schema_rejects_seconds_min_above_max():
    with pytest.raises(ValidationError) as exc:
        FanpageUpdate(ai_video_seconds_min=30, ai_video_seconds_max=20)
    assert "ai_video_seconds_min must be <= ai_video_seconds_max" in str(exc.value)

    # valid when min <= max
    fp = FanpageUpdate(ai_video_seconds_min=20, ai_video_seconds_max=30)
    assert fp.ai_video_seconds_min == 20
    assert fp.ai_video_seconds_max == 30


def test_per_video_cap_shortens_video(db, monkeypatch):
    session, fanpage_ids = db
    fanpage = _fanpage(
        session,
        fanpage_ids,
        ai_video_seconds_min=20,
        ai_video_seconds_max=30,
        ai_video_max_credits_per_video=30,
    )
    idea = _idea(session, fanpage, clips=3)
    idea_id = idea.id
    session.commit()

    import app.tasks.ai_video_plan as planner

    monkeypatch.setattr(planner, "SessionLocal", SessionLocal)

    def mock_fill(db, idea, fp, clips):
        return (
            {"part": "rear wing"},
            "Generated title",
            "Generated caption",
            [{"index": i, "mode": "t2v", "prompt": f"prompt {i}"} for i in range(1, clips + 1)],
            "mock",
        )

    with patch.object(planner, "fill_idea", side_effect=mock_fill):
        planner.plan_ai_videos.run()

    verify = SessionLocal()
    job = verify.query(VideoJob).filter(VideoJob.idea_id == idea_id).first()
    assert job is not None
    assert len(job.clips) == 2
    assert job.credits_estimate == 30
    assert verify.get(VideoIdea, idea_id).status == "queued"
    verify.close()


def test_per_video_cap_below_minimum_fails_idea(db, monkeypatch):
    session, fanpage_ids = db
    fanpage = _fanpage(
        session,
        fanpage_ids,
        ai_video_seconds_min=20,
        ai_video_seconds_max=30,
        ai_video_max_credits_per_video=10,
    )
    idea = _idea(session, fanpage, clips=2)
    idea_id = idea.id
    session.commit()

    import app.tasks.ai_video_plan as planner

    monkeypatch.setattr(planner, "SessionLocal", SessionLocal)

    planner.plan_ai_videos.run()

    verify = SessionLocal()
    stored_idea = verify.get(VideoIdea, idea_id)
    assert stored_idea.status == "failed"
    assert "allows 10 per video" in stored_idea.error
    assert verify.query(VideoJob).filter(VideoJob.idea_id == idea_id).count() == 0
    verify.close()


def test_daily_cap_waits(db, monkeypatch):
    session, fanpage_ids = db
    fanpage = _fanpage(
        session,
        fanpage_ids,
        ai_video_daily_count=5,
        ai_video_max_credits_per_day=40,
    )
    existing_job = VideoJob(
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Existing job",
        caption="Existing caption",
        model="veo-2",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1, "mode": "t2v", "prompt": "p1"}, {"index": 2, "mode": "t2v", "prompt": "p2"}],
        credits_per_clip=15,
        credits_estimate=30,
        status="queued",
    )
    session.add(existing_job)
    idea = _idea(session, fanpage, topic="waiting idea", clips=2)
    idea_id = idea.id
    session.commit()

    import app.tasks.ai_video_plan as planner

    monkeypatch.setattr(planner, "SessionLocal", SessionLocal)

    planner.plan_ai_videos.run()

    verify = SessionLocal()
    stored_idea = verify.get(VideoIdea, idea_id)
    assert stored_idea.status == "approved"
    assert verify.query(VideoJob).filter(VideoJob.idea_id == idea_id).count() == 0
    verify.close()
