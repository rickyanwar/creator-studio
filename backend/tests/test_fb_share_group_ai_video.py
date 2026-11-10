"""SQLite regressions for Mode 8 on a Group Share lane (a hidden brand fanpage for groups)."""
from datetime import datetime
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.schema import DefaultClause
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker

from app.models.ai_video import VideoIdea, VideoJob
from app.models.fb_group_share import FbShareBrandSettings, FbShareGroupJob, FbShareLane
from app.models.publish_jobs import PublishJob
from app.models.settings import Settings
from app.models.social_publishing import SocialDestination
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.fb_share import group_jobs
from app.tasks import ai_video_finalize as finalize, ai_video_plan as planner, publisher


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_ARRAY", lambda *a, **k: "TEXT", raising=False)
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **k: "TEXT", raising=False)
    engine = create_engine("sqlite:///:memory:")
    tables = [TargetFanpage.__table__, VideoIdea.__table__, VideoJob.__table__,
              PublishJob.__table__, Settings.__table__, FbShareGroupJob.__table__, SocialDestination.__table__]
    defaults = [(column, column.server_default) for table in tables for column in table.columns
                if column.server_default is not None]
    try:
        for column, _ in defaults:
            if "::" in str(column.server_default.arg):
                column.server_default = DefaultClause(text("'[]'" if "ARRAY" in str(column.type) else "'{}'"))
        for table in tables:
            table.create(engine, checkfirst=True)
    finally:
        for column, default in defaults:
            column.server_default = default
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    monkeypatch.setattr(session, "close", lambda: None)
    monkeypatch.setattr(planner, "SessionLocal", lambda: session)
    monkeypatch.setattr(finalize, "SessionLocal", lambda: session)
    monkeypatch.setattr(planner, "polish_enabled", lambda page: False)
    monkeypatch.setattr(planner, "prepare_cover", lambda *a, **k: None)
    monkeypatch.setattr(planner, "resolve_narration", lambda *a: {})
    monkeypatch.setattr(planner, "fill_idea", lambda *a: ({}, "Title", "Caption", [{"index": 1}], "mock"))
    monkeypatch.setattr(planner, "research_ready", lambda *a: False)
    monkeypatch.setattr(planner, "write_ideas", Mock(return_value=0))
    yield session
    session.rollback()
    engine.dispose()


def page(db, **kwargs):
    values = dict(repliz_account_id="group-video", name="Group videos", is_active=True,
                  ai_video_enabled=True, ai_video_daily_count=1, ai_video_clips=3,
                  ai_video_publish_mode=PublishMode.manual_review, ai_video_cover_enabled=False,
                  ai_video_logo_enabled=False, ai_video_captions_enabled=False)
    values.update(kwargs)
    result = TargetFanpage(**values)
    db.add(result)
    db.flush()
    for number in range(4):
        db.add(VideoIdea(fanpage_id=result.id, template_key="motorsport_tech_explainer",
                         topic=f"topic {number}", status="approved", score=90))
    db.commit()
    return result


def video(db, fp, *, group=False, **kwargs):
    values = dict(fanpage_id=fp.id, template_key="motorsport_tech_explainer", title="old", caption="Caption",
                  model="omni-flash", aspect="9:16", clip_seconds=10, clips=[{"index": 1}],
                  credits_per_clip=15, credits_estimate=45, clips_done=[], status="done",
                  created_at=datetime.utcnow())
    values.update(kwargs)
    result = VideoJob(**values)
    db.add(result)
    db.flush()
    if group:
        group_jobs.mark_group_job(db, fanpage_id=fp.id, mode="ai_video", video_job_id=result.id)
    db.commit()
    return result


def lane_of(db, brand, **kwargs):
    lane = page(db, name=f"{brand.name} (groups)", repliz_account_id=None, is_connected=False, **kwargs)
    db.add(FbShareLane(brand_fanpage_id=brand.id, lane_fanpage_id=lane.id))
    db.add(FbShareBrandSettings(fanpage_id=brand.id, enabled=True, settings={}))
    db.commit()
    return lane


def test_lane_gets_marked_video_from_normal_pass_and_keeps_its_daily_count(db):
    brand = page(db, name="Brand", ai_video_enabled=False)
    lane = lane_of(db, brand)
    planner.plan_ai_videos.run()
    job = db.query(VideoJob).one()
    assert job.fanpage_id == lane.id
    mark = db.query(FbShareGroupJob).one()
    assert (mark.fanpage_id, mark.mode, mark.video_job_id) == (brand.id, "ai_video", job.id)
    # Lane videos are handed over, never stocked: the lane's daily count alone stops the next one.
    job.status = "done"
    db.commit()
    planner.plan_ai_videos.run()
    assert db.query(VideoJob).count() == 1


def test_page_still_builds_stock_after_daily_count(db):
    fp = page(db)
    video(db, fp)
    planner.plan_ai_videos.run()
    assert db.query(VideoJob).count() == 2
    assert db.query(FbShareGroupJob).count() == 0


def test_lane_of_disabled_brand_does_not_plan(db):
    brand = page(db, name="Brand", ai_video_enabled=False)
    lane = lane_of(db, brand)
    db.query(FbShareBrandSettings).update({"enabled": False})
    db.commit()
    planner.plan_ai_videos.run()
    assert db.query(VideoJob).filter_by(fanpage_id=lane.id).count() == 0


def test_idea_writer_includes_enabled_lane(db):
    db.add(Settings(ai_video_researcher="muse", ai_video_idea_bank_days=2))
    brand = page(db, name="Brand", ai_video_enabled=False)
    lane = lane_of(db, brand)
    db.query(VideoIdea).filter_by(fanpage_id=lane.id).delete()
    db.commit()
    planner.top_up_ai_video_ideas.run()
    # Lanes are hidden from Muse/Hermes, so the app's own writer fills an empty lane bank.
    assert [call.args[1].id for call in planner.write_ideas.call_args_list] == [lane.id]


@pytest.mark.parametrize("overlay_failure", [False, True])
def test_finalize_links_and_dispatches_group_in_manual_mode(db, monkeypatch, overlay_failure):
    fp = page(db, ai_video_logo_enabled=overlay_failure)
    job = video(db, fp, group=True, status="uploaded", video_path="/nonexistent/group.mp4",
                video_url="https://example.test/video.mp4")
    monkeypatch.setattr("app.services.ai_video_qc.technical_check", lambda *a: {"passed": True, "reasons": []})
    monkeypatch.setattr("app.services.ai_video_overlay.render_branded", Mock(side_effect=ValueError("overlay failed")))
    monkeypatch.setattr("app.services.speech_words.caption_words", lambda *a: [])
    monkeypatch.setattr("app.services.ai_video_overlay.speech_windows", lambda *a: [])
    monkeypatch.setattr("app.services.ai_video_overlay.estimate_words", lambda *a: [])
    delay = Mock()
    monkeypatch.setattr(publisher.publish_job, "delay", delay)
    finalize.finalize_ai_video.run(job.id)
    mark = db.query(FbShareGroupJob).one()
    assert mark.publish_job_id == job.publish_job_id
    assert job.status == "done"
    if overlay_failure:
        # Overlay-failed group videos stay in review and are never sent to groups.
        delay.assert_not_called()
    else:
        delay.assert_called_once_with(mark.publish_job_id)
