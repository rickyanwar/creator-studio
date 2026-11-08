"""Regression checks for group-only review gates, batching, quotas and cleanup."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import JSON, create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import DefaultClause

from app.models.ai_video import VideoJob
from app.models.fb_group_share import FbShareBrandSettings, FbShareGroupJob, FbShareItem, FbShareTask
from app.models.ig_sources import IGSource
from app.models.posts import Post, PostStatus
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.radar import DecisionStatus, RadarPost, RadarStory, RadarStoryDecision
from app.models.settings import Settings
from app.models.social_publishing import PublishDelivery
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.fb_share import feeder, group_jobs
from app.tasks import ai_video_finalize, cleanup, publisher, radar_dispatch


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_ARRAY", lambda *a, **k: "TEXT", raising=False)
    for model in (Post, RadarPost):
        for column in model.__table__.columns:
            if isinstance(column.type, ARRAY):
                monkeypatch.setattr(column, "type", JSON())
    engine = create_engine("sqlite://")
    models = (TargetFanpage, FbShareBrandSettings, FbShareGroupJob, FbShareItem, FbShareTask,
              PublishJob, PublishDelivery, VideoJob, Settings, IGSource, Post, RadarStory,
              RadarPost, RadarStoryDecision)
    for model in models:
        defaults = [(c, c.server_default) for c in model.__table__.columns
                    if c.server_default is not None and "::" in str(c.server_default.arg)]
        try:
            for column, _ in defaults:
                column.server_default = DefaultClause(text("'[]'"))
            model.__table__.create(engine, checkfirst=True)
        finally:
            for column, default in defaults:
                column.server_default = default
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    monkeypatch.setattr(session, "close", lambda: None)
    monkeypatch.setattr(group_jobs, "get_brand_share", lambda *a: (True, {"niches": ["f1"]}))
    session.add(TargetFanpage(id=1, name="Brand", radar_daily_max=1,
                             ai_video_publish_mode=PublishMode.manual_review,
                             ai_video_cover_enabled=False, ai_video_logo_enabled=False,
                             ai_video_captions_enabled=False))
    session.commit()
    yield session
    session.rollback()
    engine.dispose()


def group_job(db, **kwargs):
    values = dict(fanpage_id=1, content_type=ContentType.ai_video,
                  status=PublishJobStatus.pending_publish, video_path="clip.mp4")
    values.update(kwargs)
    job = PublishJob(**values)
    db.add(job)
    db.flush()
    group_jobs.mark_group_job(db, fanpage_id=1, mode="ai_video", publish_job_id=job.id)
    db.flush()
    return job


@pytest.mark.parametrize("status,error", [
    (PublishJobStatus.pending_review, "QC: wrong physics"),
    (PublishJobStatus.pending_review, "overlay failed: missing logo"),
    (PublishJobStatus.pending_review, "held by owner"),
    (PublishJobStatus.pending_publish, "held by owner"),
])
def test_held_group_job_is_untouched(db, status, error):
    job = group_job(db, status=status, last_error=error)
    assert group_jobs.hand_over(db, job, datetime.utcnow()) is None
    assert (job.status, job.last_error) == (status, error)
    assert db.query(FbShareItem).count() == 0
    assert db.query(FbShareGroupJob).one().handed_at is None


@pytest.mark.parametrize("error", [None, ""])
def test_manual_review_image_without_error_is_handed_over(db, monkeypatch, error):
    job = group_job(db, content_type=ContentType.discussion, design_image_path="card.png",
                    status=PublishJobStatus.pending_review, last_error=error)
    monkeypatch.setattr(feeder, "load_image_bytes", lambda *a: b"image")
    monkeypatch.setattr("app.services.radar_clustering.compute_phash", lambda *a: "abcd")
    item = group_jobs.hand_over(db, job, datetime.utcnow())
    assert (item.media_kind, item.phash) == ("photo", "abcd")
    assert job.status == PublishJobStatus.skipped
    assert group_jobs.hand_over(db, job, datetime.utcnow()).id == item.id


@pytest.mark.parametrize("failure", ["vision", "overlay", None])
def test_finalize_dispatches_only_passed_group_video(db, monkeypatch, failure):
    page = db.get(TargetFanpage, 1)
    page.ai_video_logo_enabled = failure == "overlay"
    db.add(Settings(id=1, ai_video_vision_qc_enabled=True))
    video = VideoJob(fanpage_id=1, template_key="test", title="Video", caption="Caption",
                     model="test", aspect="9:16", clip_seconds=10, clips=[{"index": 1}],
                     clips_done=[], credits_per_clip=15, credits_estimate=15,
                     status="uploaded", video_path="/nonexistent/video.mp4")
    db.add(video)
    db.flush()
    group_jobs.mark_group_job(db, fanpage_id=1, mode="ai_video", video_job_id=video.id)
    db.commit()
    monkeypatch.setattr(ai_video_finalize, "SessionLocal", lambda: db)
    monkeypatch.setattr("app.services.qa_toggles.qa_enabled", lambda *a: True)
    monkeypatch.setattr("app.services.ai_video_qc.technical_check", lambda *a: {"passed": True, "reasons": []})
    monkeypatch.setattr("app.services.ai_video_qc.contact_sheet", lambda *a: b"sheet")
    monkeypatch.setattr("app.services.ai_video_qc.vision_check", lambda *a: {
        "passed": failure != "vision", "issues": ["wrong physics"] if failure == "vision" else []})
    monkeypatch.setattr("app.services.speech_words.caption_words", lambda *a: [])
    monkeypatch.setattr("app.services.ai_video_overlay.speech_windows", lambda *a: [])
    monkeypatch.setattr("app.services.ai_video_overlay.estimate_words", lambda *a: [])
    monkeypatch.setattr("app.services.ai_video_overlay.render_branded", Mock(side_effect=ValueError("overlay failed")))
    delay = Mock()
    monkeypatch.setattr(publisher.publish_job, "delay", delay)
    ai_video_finalize.finalize_ai_video.run(video.id)
    job = db.get(PublishJob, video.publish_job_id)
    assert group_jobs.is_group_only(db, job.id)
    if failure:
        delay.assert_not_called()
        assert job.status == PublishJobStatus.pending_review
        assert group_jobs.hand_over(db, job, datetime.utcnow()) is None
    else:
        delay.assert_called_once_with(job.id)


def test_feeder_oldest_ten_commit_each_and_continue_after_failure(db, monkeypatch, caplog):
    now = datetime.utcnow()
    jobs = [group_job(db) for _ in range(12)]
    for index, job in enumerate(jobs):
        db.query(FbShareGroupJob).filter_by(publish_job_id=job.id).one().created_at = now - timedelta(minutes=index)
    db.commit()
    commit = Mock(wraps=db.commit)
    rollback = Mock(wraps=db.rollback)
    monkeypatch.setattr(db, "commit", commit)
    monkeypatch.setattr(db, "rollback", rollback)
    original = group_jobs.hand_over
    seen = []

    def hand_over(session, job, instant):
        assert commit.call_count == len(seen) - (1 if jobs[-2].id in seen else 0)
        seen.append(job.id)
        item = original(session, job, instant)
        if job.id == jobs[-2].id:
            raise ValueError("one broken job")
        return item

    monkeypatch.setattr(group_jobs, "hand_over", hand_over)
    assert feeder.collect_group_only_items(db, db.get(TargetFanpage, 1), now) == 9
    assert seen == [job.id for job in reversed(jobs[2:])]
    assert commit.call_count == 9
    assert rollback.call_count == 1
    assert db.query(FbShareItem).count() == 9
    assert db.query(FbShareGroupJob).filter_by(publish_job_id=jobs[-2].id).one().handed_at is None
    assert str(jobs[-2].id) in caplog.text


def radar_story(db, shortcode, now, *, status=DecisionStatus.QUEUED, reason=None):
    story = RadarStory(first_seen_at=now, last_member_at=now, created_at=now)
    db.add(story)
    db.flush()
    story.posts.append(RadarPost(ig_source_id=1, ig_username="source", shortcode=shortcode,
                                taken_at=now, media_type="image", image_source_urls=[]))
    decision = RadarStoryDecision(story_id=story.id, fanpage_id=1, rule="fast", status=status,
                                  reason=reason, created_at=now)
    db.add(decision)
    db.add(Post(ig_source_id=1, ig_media_id=shortcode, media_type="image",
                ig_post_url=f"https://www.instagram.com/p/{shortcode}/", status=PostStatus.stored))
    db.commit()
    return story, decision


def test_radar_marks_dispatch_before_publish_job_and_excludes_it_from_page_cap(db, monkeypatch):
    now = datetime.utcnow()
    db.add(IGSource(id=1, ig_username="source"))
    db.commit()
    monkeypatch.setattr("app.services.radar_time.utcnow_naive", lambda: now)
    dispatch = Mock()
    monkeypatch.setattr(radar_dispatch.recreate_post_for_fanpage, "apply_async", dispatch)
    monkeypatch.setattr(radar_dispatch, "remaining_groups_only_today", lambda *a: 1)
    radar_story(db, "page", now, status=DecisionStatus.DISPATCHED)
    story, group = radar_story(db, "group", now)
    radar_dispatch._process_story(db, story, "shared", 0, 10)
    assert group.reason.startswith("group_only:")
    assert group.status == DecisionStatus.DISPATCHED and group.publish_job_id is None
    assert dispatch.call_args.kwargs["kwargs"]["group_only"] is True
    # Removing the Page reservation leaves a free Page slot despite the group dispatch.
    db.query(RadarStoryDecision).filter_by(reason=None, status=DecisionStatus.DISPATCHED).update({"status": DecisionStatus.FAILED})
    db.commit()
    story, page = radar_story(db, "next", now)
    radar_dispatch._process_story(db, story, "shared", 0, 10)
    assert page.status == DecisionStatus.DISPATCHED
    assert "group_only" not in dispatch.call_args.kwargs["kwargs"]


def test_radar_pending_group_decisions_count_against_groups_only_cap(db, monkeypatch):
    now = datetime.utcnow()
    db.add(IGSource(id=1, ig_username="source"))
    db.commit()
    monkeypatch.setattr("app.services.radar_time.utcnow_naive", lambda: now)
    dispatch = Mock()
    monkeypatch.setattr(radar_dispatch.recreate_post_for_fanpage, "apply_async", dispatch)
    # One extra group post allowed today; no marker rows exist yet (recreate has not run).
    monkeypatch.setattr(radar_dispatch, "remaining_groups_only_today", lambda *a: 1)
    radar_story(db, "page", now, status=DecisionStatus.DISPATCHED)
    story, first = radar_story(db, "group-1", now)
    radar_dispatch._process_story(db, story, "shared", 0, 10)
    story, second = radar_story(db, "group-2", now)
    radar_dispatch._process_story(db, story, "shared", 0, 10)
    assert first.status == DecisionStatus.DISPATCHED and first.reason.startswith("group_only:")
    assert second.status == DecisionStatus.SKIPPED_DAILY_MAX
    assert dispatch.call_count == 1


@pytest.mark.parametrize("open_task", [False, True])
def test_cleanup_post_with_finished_group_job(db, monkeypatch, tmp_path, open_task):
    now = datetime.utcnow()
    post = Post(ig_source_id=1, ig_media_id="cleanup", media_type="image", ig_post_url="https://example.test/cleanup",
                status=PostStatus.stored, image_local_paths=["image.jpg"], crawled_at=now - timedelta(days=20))
    db.add(post)
    db.flush()
    db.add(TargetFanpage(id=2, name="Page destination"))
    page_job = PublishJob(fanpage_id=2, post_id=post.id, content_type=ContentType.ig_repost,
                          status=PublishJobStatus.published, cleanup_at=now - timedelta(days=1))
    db.add(page_job)
    job = group_job(db, post_id=post.id)
    item = group_jobs.hand_over(db, job, now - timedelta(days=20))
    item.status = "done"
    db.add(FbShareTask(item_id=item.id, group_id=1, destination_id=1, delivery_mode="phone_auto",
                       scheduled_for=now - timedelta(days=10), updated_at=now - timedelta(days=9),
                       state="publishing" if open_task else "published"))
    db.commit()
    directory = tmp_path / "posts" / str(post.uuid)
    directory.mkdir(parents=True)
    (directory / "image.jpg").write_bytes(b"image")
    monkeypatch.setattr(cleanup, "SessionLocal", lambda: db)
    monkeypatch.setattr(cleanup, "settings", SimpleNamespace(storage_base_path=str(tmp_path)))
    cleanup.cleanup_old_media.run()
    assert directory.exists() is open_task
    assert post.status == (PostStatus.stored if open_task else PostStatus.cleaned)
