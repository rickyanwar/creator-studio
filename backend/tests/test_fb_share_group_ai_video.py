"""SQLite regressions for the separate Mode 8 Group Share production lane."""
from datetime import datetime
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.schema import DefaultClause
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker

from app.models.ai_video import VideoIdea, VideoJob
from app.models.fb_group_share import FbShareBrandSettings, FbShareGroupJob
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.settings import Settings
from app.models.social_publishing import SocialDestination
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.fb_share import group_jobs
from app.tasks import ai_video_finalize as finalize, ai_video_plan as planner, publisher


def pytest_configure(config):
    """Load with -p to complete older explicit-table SQLite fixtures."""
    def create_group_tables(target, connection, **kwargs):
        if connection.dialect.name == "sqlite":
            FbShareGroupJob.__table__.create(connection, checkfirst=True)
            FbShareBrandSettings.__table__.create(connection, checkfirst=True)

    event.listen(TargetFanpage.__table__, "after_create", create_group_tables)


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
    settings = {"sources": {"ai_video": {"source": "groups_only", "groups_only_per_day": 2}}}
    monkeypatch.setattr(group_jobs, "get_brand_share", lambda *a: (True, settings))
    monkeypatch.setattr(planner, "get_brand_share", lambda *a: (True, settings))
    session.info["share"] = settings
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


def block_page_lane(db, monkeypatch, fp):
    video(db, fp)
    monkeypatch.setattr(planner, "video_stock", lambda *a: planner.AI_VIDEO_STOCK_DAYS)


def test_group_branch_marks_one_per_tick_and_one_open(db, monkeypatch):
    fp = page(db)
    block_page_lane(db, monkeypatch, fp)
    planner.plan_ai_videos.run()
    assert db.query(FbShareGroupJob).count() == 1
    marked = db.query(VideoJob).filter(VideoJob.id.in_(group_jobs.group_video_job_ids())).one()
    assert marked.status == "queued"
    planner.plan_ai_videos.run()
    assert db.query(FbShareGroupJob).count() == 1
    marked.status = "done"
    db.commit()
    planner.plan_ai_videos.run()
    assert db.query(FbShareGroupJob).count() == 2
    marked = db.query(VideoJob).filter_by(status="queued").one()
    marked.status = "done"
    db.commit()
    planner.plan_ai_videos.run()
    assert db.query(FbShareGroupJob).count() == 2


@pytest.mark.parametrize("source,enabled,quota", [("follow", True, 2), ("off", True, 2),
                                                  ("both", False, 2), ("both", True, 0)])
def test_disabled_group_source_does_not_plan(db, monkeypatch, source, enabled, quota):
    fp = page(db)
    block_page_lane(db, monkeypatch, fp)
    settings = db.info["share"]
    settings["sources"]["ai_video"].update(source=source, groups_only_per_day=quota)
    monkeypatch.setattr(group_jobs, "get_brand_share", lambda *a: (enabled, settings))
    planner.plan_ai_videos.run()
    assert db.query(FbShareGroupJob).count() == 0


@pytest.mark.parametrize("cap,expected", [(74, 0), (75, 1)])
def test_group_branch_respects_combined_credit_cap(db, monkeypatch, cap, expected):
    fp = page(db, ai_video_max_credits_per_day=cap)
    block_page_lane(db, monkeypatch, fp)
    planner.plan_ai_videos.run()
    assert db.query(FbShareGroupJob).count() == expected
    assert sum(j.credits_estimate for j in db.query(VideoJob)) <= cap


def test_group_jobs_do_not_use_page_counts_or_stock(db, monkeypatch):
    fp = page(db)
    job = video(db, fp, group=True, status="queued", credits_estimate=45)
    publish = PublishJob(fanpage_id=fp.id, content_type=ContentType.ai_video,
                         status=PublishJobStatus.pending_review)
    db.add(publish)
    db.flush()
    job.publish_job_id = publish.id
    group_jobs.link_video_publish_job(db, video_job_id=job.id, publish_job_id=publish.id)
    db.commit()
    assert planner.video_stock(db, fp.id) == 0
    db.info["share"]["sources"]["ai_video"]["source"] = "follow"
    planner.plan_ai_videos.run()
    assert db.query(VideoJob).filter(VideoJob.id.notin_(group_jobs.group_video_job_ids())).count() == 1
    jobs = db.query(VideoJob).filter(VideoJob.id.notin_(group_jobs.group_video_job_ids())).all()
    assert planner._daily_counts(jobs) == (1, 45)


def test_page_lane_also_respects_credits_spent_by_groups(db):
    fp = page(db, ai_video_max_credits_per_day=45)
    video(db, fp, group=True)
    db.info["share"]["sources"]["ai_video"]["source"] = "follow"
    planner.plan_ai_videos.run()
    assert db.query(VideoJob).count() == 1


def test_group_branch_respects_per_video_credit_limit(db, monkeypatch):
    fp = page(db, ai_video_max_credits_per_video=29)
    block_page_lane(db, monkeypatch, fp)
    planner.plan_ai_videos.run()
    assert db.query(FbShareGroupJob).count() == 0


def test_group_branch_uses_separate_daily_template_budget(db, monkeypatch):
    fp = page(db)
    block_page_lane(db, monkeypatch, fp)
    video(db, fp, group=True, credits_estimate=90)
    planner.plan_ai_videos.run()
    assert db.query(FbShareGroupJob).count() == 1


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
