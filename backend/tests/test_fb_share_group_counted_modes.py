"""Counted modes keep Page production separate from extra Group Share jobs."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, true, event
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles

from app.models.target_fanpages import TargetFanpage, PublishMode
from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
from app.models.fb_group_share import FbShareBrandSettings, FbShareGroupJob
from app.models.discussion_content_ideas import DiscussionContentIdea
from app.models.pinterest_content_ideas import PinterestContentIdea
from app.models.facebook_photo_ideas import FacebookPhotoIdea
from app.models.facebook_photo_sources import FacebookPhotoSource
from app.models.gallery import GalleryImage
from app.models.yt_clip_ideas import YtClipIdea
from app.models.yt_videos import YtVideo
from app.services.fb_share import group_jobs as core
from app.services.fb_share.brand_settings import set_brand_share
from app.tasks import discussion, pinterest, facebook_photo, yt_clip, design_renderer, yt_clip_render

NOW = datetime.now(timezone.utc).replace(tzinfo=None, hour=12, minute=0)


@compiles(ARRAY, "sqlite")
@compiles(JSONB, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


MODES = [
    (discussion, "discussion", "discussion", "generate_discussion_content", "render_discussion"),
    (pinterest, "pinterest", "pinterest_content", "generate_pinterest_content", "render_pinterest"),
    (facebook_photo, "fb_recreate", "facebook_recreate", "generate_facebook_photo_content", "render_facebook_photo"),
    (yt_clip, "yt_clips", "youtube_clip", "generate_yt_clip_content", "render_youtube_clip"),
]


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    for model in (TargetFanpage, PublishJob, FbShareBrandSettings, FbShareGroupJob,
                  DiscussionContentIdea, PinterestContentIdea, FacebookPhotoIdea,
                  FacebookPhotoSource, GalleryImage, YtVideo, YtClipIdea):
        model.__table__.create(engine)
    with Session(engine) as session:
        @event.listens_for(session, "before_flush")
        def stamp_created_at(session, context, instances):
            for row in session.new:
                if isinstance(row, (PublishJob, FbShareGroupJob)):
                    row.created_at = NOW
                if isinstance(row, PublishJob):
                    row.is_deleted = False
        monkeypatch.setattr(session, "close", lambda: None)
        yield session
    engine.dispose()


def prepare(db, monkeypatch, mode):
    module, key, content, tick, render = mode
    prefix = {"fb_recreate": "facebook_photo", "yt_clips": "yt_clip"}.get(key, key)
    page = TargetFanpage(id=1, name="Racing", is_active=True,
                        **{f"{prefix}_enabled": True, f"{prefix}_daily_count": 1,
                           f"{prefix}_publish_mode": PublishMode.manual_review},
                        mode2_caption_hashtag_count=0)
    db.add(page)
    for index in range(8):
        fields = dict(fanpage_id=1, status="pending")
        if key == "discussion":
            idea = DiscussionContentIdea(**fields, label="DISCUSSION", question=f"Question {index}?",
                                         subject_name="Driver", caption="Caption", source_type="manual")
        elif key == "pinterest":
            idea = PinterestContentIdea(**fields, gallery_image_id=1, title=f"Title {index}",
                                       description="Caption", source_type="manual")
        elif key == "fb_recreate":
            idea = FacebookPhotoIdea(**fields, gallery_image_id=index + 1, category="news", design_title=f"Title {index}")
        else:
            if index == 0:
                db.add(YtVideo(id=1, fanpage_id=1, video_id="abcdefghijk", title="Race", status="analyzed"))
            idea = YtClipIdea(**fields, yt_video_row_id=1, video_id="abcdefghijk",
                              title=f"Driver race {index}", start_s=index * 60, end_s=index * 60 + 30)
        db.add(idea)
    db.commit()
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.replace(tzinfo=timezone.utc) if tz else NOW
    monkeypatch.setattr(module, "datetime", Clock)
    if module == yt_clip:
        monkeypatch.setattr(module, "_now", lambda: NOW)
        monkeypatch.setattr(module, "_enabled_fanpages", lambda db: [page])
    else:
        monkeypatch.setattr(module, "_topup_queue", lambda *a: 0)
    monkeypatch.setattr("app.services.social_project_eligibility.project_can_generate", lambda: true())
    monkeypatch.setattr("app.services.design_images.resolve_template", lambda *a, **kw: None)
    monkeypatch.setattr("app.services.ai_caption.generate_caption", lambda *a, **kw: ("Driver races. Thoughts?", "gemini"))
    monkeypatch.setattr("app.services.ai_caption.build_caption_prompt", lambda *a, **kw: "Prompt")
    monkeypatch.setattr("app.services.pinterest_classifier.classify_pinterest_content", lambda *a: "news")
    monkeypatch.setattr("app.services.engine_router.render_queue_for", lambda *a, **kw: None)
    task = getattr(yt_clip_render if module == yt_clip else design_renderer, render)
    dispatch = Mock()
    monkeypatch.setattr(task, "apply_async", dispatch)
    return page, dispatch


@pytest.mark.parametrize("mode", MODES, ids=[m[1] for m in MODES])
@pytest.mark.parametrize("enabled,source,quota", [
    (True, "both", 2), (True, "groups_only", 7),
    (True, "follow", 5), (True, "off", 5), (False, "both", 5),
])
def test_counted_passes(db, monkeypatch, mode, enabled, source, quota):
    module, key, content, tick, _ = mode
    _, dispatch = prepare(db, monkeypatch, mode)
    set_brand_share(db, 1, enabled, {"sources": {key: {"source": source, "groups_only_per_day": quota}}})
    # An existing group job must not consume the one available Page slot.
    prior = PublishJob(fanpage_id=1, content_type=ContentType(content), created_at=NOW,
                       status=PublishJobStatus.skipped)
    db.add(prior)
    db.flush()
    core.mark_group_job(db, fanpage_id=1, mode=key, publish_job_id=prior.id)
    db.query(FbShareGroupJob).update({"created_at": NOW})
    db.commit()
    # The quota includes the prior mark.
    expected = min(3, max(0, quota - 1)) if enabled and source in ("both", "groups_only") else 0
    getattr(module, tick).run()
    jobs = db.query(PublishJob).all()
    assert len(jobs) == 2 + expected
    assert db.query(FbShareGroupJob).count() == 1 + expected
    assert db.query(PublishJob).filter(PublishJob.id.notin_(core.group_publish_job_ids())).count() == 1
    assert dispatch.call_count == expected
    # A second tick does not create another Page job or exceed the group budget.
    getattr(module, tick).run()
    assert db.query(PublishJob).filter(PublishJob.id.notin_(core.group_publish_job_ids())).count() == 1
    assert db.query(FbShareGroupJob).count() <= max(1, quota)


def test_youtube_idea_cap_includes_groups(db, monkeypatch):
    page, _ = prepare(db, monkeypatch, MODES[-1])
    set_brand_share(db, 1, True, {"sources": {"yt_clips": {"source": "both", "groups_only_per_day": 5}}})
    monkeypatch.setattr("app.services.yt_downloader.blocked_until", lambda db: None)
    monkeypatch.setattr(yt_clip, "_pending_idea_count", lambda *a: 8)
    monkeypatch.setattr(yt_clip, "_next_video_to_analyse", lambda *a: SimpleNamespace(id=123))
    dispatch = Mock()
    monkeypatch.setattr(yt_clip.analyze_video, "delay", dispatch)
    assert yt_clip._dispatch_analysis(db, page)
    dispatch.assert_called_once_with(123)


@pytest.mark.parametrize("mode", MODES, ids=[m[1] for m in MODES])
@pytest.mark.parametrize("marked", [False, True])
@pytest.mark.parametrize("direct", [False, True])
def test_manual_render_hands_over(db, monkeypatch, tmp_path, mode, marked, direct):
    module, key, content, _, render = mode
    page, _ = prepare(db, monkeypatch, mode)
    job = PublishJob(fanpage_id=1, content_type=ContentType(content),
                     status=PublishJobStatus.pending_design, design_title="Race", design_caption="Driver")
    db.add(job)
    db.flush()
    if marked:
        core.mark_group_job(db, fanpage_id=1, mode=key, publish_job_id=job.id)
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    if module == pinterest:
        image = GalleryImage(local_path=str(photo), public_url="https://example.test/photo.jpg",
                             source_image_url="https://example.test/photo.jpg", width=100, height=100,
                             keyword="Driver", source_engine="manual")
        db.add(image)
        db.flush()
        job.source_gallery_image_id = image.id
    db.commit()
    monkeypatch.setattr(design_renderer, "SessionLocal", lambda: db)
    monkeypatch.setattr(design_renderer.settings, "storage_base_path", str(tmp_path))
    monkeypatch.setattr(design_renderer, "_release_queued_lock", lambda *a: None)
    template = SimpleNamespace(id=1, template_json={"objects": []}, canvas_width=100, canvas_height=100)
    monkeypatch.setattr("app.services.design_images.resolve_template", lambda *a, **kw: template)
    monkeypatch.setattr("app.services.design_images.find_gallery_datauri", lambda *a, **kw: ("data:image/png;base64,AAAA", None))
    monkeypatch.setattr("app.services.design_images.prepare_design_images", lambda *a, **kw: ({"objects": []}, []))
    monkeypatch.setattr("app.services.design_images.focus_points_for", lambda *a, **kw: [])
    monkeypatch.setattr("app.services.design_images.watermark_datauri", lambda *a: None)
    monkeypatch.setattr("app.services.design_images.photo_crops_well", lambda *a: not direct)
    monkeypatch.setattr(design_renderer, "_pick_fitting_facebook_photo", lambda *a: ("photo", None, "search"))
    monkeypatch.setattr(design_renderer.httpx, "post", lambda *a, **kw: SimpleNamespace(content=b"png", raise_for_status=lambda: None))
    monkeypatch.setattr(yt_clip_render, "SessionLocal", lambda: db)
    monkeypatch.setattr(yt_clip, "yt_private_dir", lambda *a: tmp_path)
    monkeypatch.setattr(yt_clip_render, "_render", lambda *a: None)
    publish = Mock()
    monkeypatch.setattr("app.tasks.publisher.publish_job.delay", publish)
    if direct and module == facebook_photo:
        monkeypatch.setattr("app.services.ig_photo_edit.clean_jpeg", lambda *a, **kw: str(photo))
        design_renderer._render_facebook_direct(db, job, page, SimpleNamespace(image_local_path=str(photo)))
    else:
        getattr(yt_clip_render if module == yt_clip else design_renderer, render).run(job.id)
    assert job.status == PublishJobStatus.pending_publish
    assert publish.call_args_list == ([((job.id,), {})] if marked else [])
