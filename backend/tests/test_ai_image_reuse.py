"""Edit once: one AI image reused across a brand, its group lane and same-profile fanpages.

Also F9: stale lane news is skipped like Page news.
"""

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import DefaultClause

from app.config import get_settings
from app.models.ai_video import VideoJob
from app.models.fb_group_share import FbShareBrandSettings, FbShareGroupJob, FbShareLane
from app.models.gallery import GalleryImage
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.radar import RadarStoryDecision
from app.models.social_publishing import SocialDestination
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services import ai_image_reuse
from app.services.fb_share import lanes
from app.services.fb_share.brand_settings import set_brand_share
from app.services.pro_poster.brief import brief_to_dict
from app.services.pro_poster.pipeline import _remember_poster, compose_pro_poster
from backend.tests.test_pro_poster_pipeline import _make_brief

sqlite3.register_adapter(list, json.dumps)
NOW = datetime.now(timezone.utc).replace(tzinfo=None)
PROMPT = "Make the sky golden hour"
STRANGER_ID = 3


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))
    return tmp_path


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with patch.object(SocialDestination.__table__.c.supported_media, "server_default", DefaultClause(text("'[]'"))), \
         patch.object(VideoJob.__table__.c.clips_done, "server_default", DefaultClause(text("'[]'"))):
        for model in (TargetFanpage, FbShareBrandSettings, FbShareGroupJob, FbShareLane, PublishJob,
                      SocialDestination, VideoJob, RadarStoryDecision, GalleryImage):
            model.__table__.create(engine, checkfirst=True)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        session.add_all([
            TargetFanpage(id=1, name="Brand", mode2_publish_mode=PublishMode.auto),
            TargetFanpage(id=STRANGER_ID, name="Stranger"),
        ])
        session.flush()
        set_brand_share(session, 1, True, {})
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture
def lane(db):
    lane = lanes.ensure_lane(db, 1)
    db.commit()
    return lane


def _job(db, fanpage_id, content_type=ContentType.ig_recreate, status=PublishJobStatus.rendering, **values):
    job = PublishJob(fanpage_id=fanpage_id, content_type=content_type, status=status,
                     created_at=values.pop("created_at", NOW), updated_at=NOW, is_deleted=False, **values)
    db.add(job)
    db.commit()
    return job


def _finished_sibling(db, storage, fanpage_id, *, source_key, format_key, prompt=PROMPT, qa_checked=True,
                      status=PublishJobStatus.published, content_type=ContentType.ig_recreate,
                      engine="chatgpt", name="sibling.jpg", **values):
    path = storage / name
    path.write_bytes(b"AI-IMAGE-" + name.encode())
    job = _job(db, fanpage_id, content_type=content_type, status=status,
               design_image_path=str(path), design_engine=engine, **values)
    ai_image_reuse.remember(job, str(path), source_key=source_key, format_key=format_key, prompt_text=prompt,
                            engine=engine, qa_checked=qa_checked)
    db.commit()
    return job, path


def _render_ig_edit(db, job, fanpage, *, prompt=PROMPT, qa=True, edit_result=None):
    from app.tasks import ig_recreate

    post = SimpleNamespace(id=job.post_id, image_local_paths=["/nowhere/original.jpg"])
    edit_result = edit_result or {"ok": True, "image_path": "/media/designs/new.jpg", "engine": "chatgpt", "reason": ""}
    with patch("app.services.ig_photo_edit.edit_ig_photo", return_value=edit_result) as edit, \
         patch("app.services.qa_toggles.qa_enabled", return_value=qa), \
         patch("app.tasks.publisher.publish_job.delay"):
        ig_recreate._render_ig_edit(db, job, post, fanpage, SimpleNamespace(chatgpt_edit_prompt=prompt))
    return edit


# ── IG photo edit ─────────────────────────────────────────────────────────────


def test_lane_job_reuses_brand_ig_edit_without_engine_call(db, storage, lane):
    brand_job, brand_path = _finished_sibling(db, storage, 1, source_key="ig:55", format_key="ig_edit", post_id=55)
    lane_job = _job(db, lane.id, post_id=55)

    edit = _render_ig_edit(db, lane_job, lane)

    edit.assert_not_called()
    assert lane_job.design_image_path != str(brand_path)
    assert Path(lane_job.design_image_path).read_bytes() == brand_path.read_bytes()
    assert f"reused AI image from job {brand_job.id}" in lane_job.design_engine_note
    assert lane_job.design_engine == "chatgpt"
    assert lane_job.status == PublishJobStatus.pending_publish
    record = lane_job.design_analysis_json["ai_image_reuse"]
    assert record["path"] == lane_job.design_image_path and record["qa_checked"] is True


def test_brand_job_reuses_lane_ig_edit_too(db, storage, lane):
    lane_job, _ = _finished_sibling(db, storage, lane.id, source_key="ig:55", format_key="ig_edit", post_id=55)
    brand_job = _job(db, 1, post_id=55)

    edit = _render_ig_edit(db, brand_job, db.get(TargetFanpage, 1))

    edit.assert_not_called()
    assert f"job {lane_job.id}" in brand_job.design_engine_note


def test_different_prompt_renders_normally(db, storage, lane):
    _finished_sibling(db, storage, 1, source_key="ig:55", format_key="ig_edit", post_id=55)
    lane_job = _job(db, lane.id, post_id=55)

    edit = _render_ig_edit(db, lane_job, lane, prompt="Make it black and white")

    edit.assert_called_once()
    assert lane_job.design_image_path == "/media/designs/new.jpg"
    assert "reused" not in lane_job.design_engine_note
    assert lane_job.design_analysis_json["ai_image_reuse"]["prompt_sha"] == ai_image_reuse.prompt_digest(
        "Make it black and white")


def test_unrelated_brand_never_reuses(db, storage, lane):
    _finished_sibling(db, storage, 1, source_key="ig:55", format_key="ig_edit", post_id=55)
    stranger_job = _job(db, STRANGER_ID, post_id=55)

    edit = _render_ig_edit(db, stranger_job, db.get(TargetFanpage, STRANGER_ID))

    edit.assert_called_once()


def test_same_facebook_profile_page_never_reuses_another_page(db, storage):
    # Two Pages on one Facebook profile: edit once is a Group Share feature, never Page <-> Page.
    db.add(TargetFanpage(id=2, name="Sister"))
    db.add_all([
        SocialDestination(id=10, content_project_id=1, name="Profile", kind="facebook_profile"),
        SocialDestination(id=11, content_project_id=2, name="Sister Page", kind="facebook_page", parent_profile_id=10),
    ])
    db.commit()
    _finished_sibling(db, storage, 1, source_key="ig:55", format_key="ig_edit", post_id=55)
    sister_job = _job(db, 2, post_id=55)

    edit = _render_ig_edit(db, sister_job, db.get(TargetFanpage, 2))

    edit.assert_called_once()


def test_sibling_still_rendering_renders_normally(db, storage, lane):
    _finished_sibling(db, storage, 1, source_key="ig:55", format_key="ig_edit", post_id=55,
                      status=PublishJobStatus.rendering)
    lane_job = _job(db, lane.id, post_id=55)

    edit = _render_ig_edit(db, lane_job, lane)

    edit.assert_called_once()


def test_unchecked_sibling_image_is_not_reused_when_checks_are_on(db, storage, lane):
    _finished_sibling(db, storage, 1, source_key="ig:55", format_key="ig_edit", post_id=55, qa_checked=False)
    lane_job = _job(db, lane.id, post_id=55)

    assert _render_ig_edit(db, lane_job, lane, qa=True).call_count == 1


def test_missing_sibling_file_renders_normally(db, storage, lane):
    _, path = _finished_sibling(db, storage, 1, source_key="ig:55", format_key="ig_edit", post_id=55)
    path.unlink()
    lane_job = _job(db, lane.id, post_id=55)

    assert _render_ig_edit(db, lane_job, lane).call_count == 1


def test_failed_edit_records_nothing(db, storage, lane):
    lane_job = _job(db, lane.id, post_id=55)
    with patch("app.services.ig_photo_edit.clean_jpeg", side_effect=lambda src, out, **kw: out):
        _render_ig_edit(db, lane_job, lane, edit_result={"ok": False, "image_path": None, "engine": "chatgpt",
                                                          "reason": "identity"})
    assert lane_job.design_engine == "original"
    assert "ai_image_reuse" not in (lane_job.design_analysis_json or {})


# ── Facebook photo direct edit ────────────────────────────────────────────────


def _fb_marker(db, fanpage_id, fbid):
    gi = GalleryImage(keyword=f"facebook_photo:{fanpage_id}:{fbid}", source_image_url=f"u#{fanpage_id}:{fbid}",
                      local_path="/nowhere.jpg", public_url="/x.jpg", width=10, height=10, source_engine="facebook_photo",
                      is_used=True, is_deleted=True)
    db.add(gi)
    db.commit()
    return gi.id


def test_facebook_source_key_reads_fbid_from_marker(db):
    job = _job(db, 1, ContentType.facebook_recreate, source_gallery_image_id=_fb_marker(db, 1, "987"))
    assert ai_image_reuse.facebook_source_key(db, job) == "fb:987"
    assert ai_image_reuse.facebook_source_key(db, SimpleNamespace(source_gallery_image_id=None)) is None


def test_lane_facebook_edit_reuses_brand_image(db, storage, lane):
    from app.tasks import design_renderer

    brand_job, brand_path = _finished_sibling(
        db, storage, 1, source_key="fb:987", format_key="fb_edit", content_type=ContentType.facebook_recreate,
        source_gallery_image_id=_fb_marker(db, 1, "987"),
    )
    lane_job = _job(db, lane.id, ContentType.facebook_recreate, source_gallery_image_id=_fb_marker(db, lane.id, "987"))
    idea = SimpleNamespace(image_local_path="/nowhere/fb.jpg", design_subtitle=None)

    with patch("app.services.ig_photo_edit.edit_ig_photo") as edit, \
         patch("app.services.qa_toggles.qa_enabled", return_value=True), \
         patch("app.tasks.publisher.publish_job.delay"):
        design_renderer._render_facebook_direct(db, lane_job, lane, idea, prompt=PROMPT)

    edit.assert_not_called()
    assert Path(lane_job.design_image_path).read_bytes() == brand_path.read_bytes()
    assert lane_job.design_image_path != str(brand_path)
    assert f"reused AI image from job {brand_job.id}" in lane_job.design_engine_note


# ── News Pro poster ───────────────────────────────────────────────────────────


def _poster_sibling(db, storage, fanpage_id, *, qa_checked=True, status=PublishJobStatus.pending_publish):
    job, path = _finished_sibling(
        db, storage, fanpage_id, source_key="news:7", format_key="pro_poster", prompt="", qa_checked=qa_checked,
        status=status, content_type=ContentType.news_content, engine="gptcli", name="poster.png",
        source_article_id=7,
    )
    job.design_analysis_json = {**job.design_analysis_json, "pro_poster_brief": brief_to_dict(_make_brief(job.id))}
    db.commit()
    return job, path


def test_lane_pro_poster_reuses_brand_ai_image(db, storage, lane):
    brand_job, path = _poster_sibling(db, storage, 1)
    lane_job = _job(db, lane.id, ContentType.news_content, source_article_id=7)

    with patch("app.services.pro_poster.pipeline.generate_ex", side_effect=AssertionError("engine called")), \
         patch("app.services.pro_poster.pipeline.build_brief", side_effect=AssertionError("brief rebuilt")), \
         patch("app.services.pro_poster.pipeline.qa_enabled", return_value=True):
        res = compose_pro_poster(db, lane, lane_job, SimpleNamespace(id=7))

    assert res.image == path.read_bytes()
    assert res.engine == "gptcli" and res.attempts == 0 and res.fallback_reason is None
    assert res.brief.badge_label == _make_brief(1).badge_label
    analysis = lane_job.design_analysis_json
    assert analysis["pro_poster_reused_from"] == brand_job.id
    own_path = analysis["ai_image_reuse"]["path"]
    assert own_path != str(path) and Path(own_path).read_bytes() == path.read_bytes()


def test_pro_poster_stranger_or_rendering_sibling_does_not_reuse(db, storage, lane):
    _poster_sibling(db, storage, 1, status=PublishJobStatus.rendering)
    lane_job = _job(db, lane.id, ContentType.news_content, source_article_id=7)
    stranger_job = _job(db, STRANGER_ID, ContentType.news_content, source_article_id=8)

    from app.services.pro_poster.pipeline import _reuse_sibling_poster
    with patch("app.services.pro_poster.pipeline.qa_enabled", return_value=False):
        assert _reuse_sibling_poster(db, lane, lane_job, SimpleNamespace(id=7)) is None
        assert _reuse_sibling_poster(db, db.get(TargetFanpage, STRANGER_ID), stranger_job,
                                     SimpleNamespace(id=7)) is None


def test_pro_poster_keeps_raw_image_only_when_siblings_exist(db, storage, lane):
    brand_job = _job(db, 1, ContentType.news_content, source_article_id=7)
    stranger_job = _job(db, STRANGER_ID, ContentType.news_content, source_article_id=7)

    _remember_poster(db, db.get(TargetFanpage, 1), brand_job, SimpleNamespace(id=7), b"\x89PNGposter",
                     engine="gptcli", qa_checked=True, layout="hero_solo")
    _remember_poster(db, db.get(TargetFanpage, STRANGER_ID), stranger_job, SimpleNamespace(id=7), b"\x89PNGposter",
                     engine="gptcli", qa_checked=True, layout="hero_solo")

    record = brand_job.design_analysis_json["ai_image_reuse"]
    assert record["source_key"] == "news:7" and record["layout"] == "hero_solo"
    assert Path(record["path"]).read_bytes() == b"\x89PNGposter"
    assert not (stranger_job.design_analysis_json or {}).get("ai_image_reuse")


def test_renderer_note_says_pro_poster_was_reused():
    assert ai_image_reuse.reused_note("pro poster", 42) == "pro poster; reused AI image from job 42"
    assert len(ai_image_reuse.reused_note("x" * 300, 42)) == ai_image_reuse.NOTE_MAX_LEN


# ── F9: no stale news for groups ──────────────────────────────────────────────


def test_stale_lane_news_is_skipped_fresh_lane_news_still_renders(db, lane):
    from app.tasks import design_renderer as renderer

    lane.mode2_publish_mode = PublishMode.manual_review
    db.commit()
    stale = _job(db, lane.id, ContentType.news_content, status=PublishJobStatus.pending_design,
                 created_at=NOW - timedelta(hours=renderer.NEWS_STALE_HOURS + 2), source_article_id=1)
    fresh = _job(db, lane.id, ContentType.news_content, status=PublishJobStatus.pending_design,
                 created_at=NOW - timedelta(hours=1), source_article_id=2)
    assert db.query(FbShareGroupJob).filter(FbShareGroupJob.publish_job_id.in_([stale.id, fresh.id])).count() == 2

    with patch.object(renderer, "SessionLocal", return_value=db), \
         patch.object(renderer, "_acquire_queued_lock", return_value=True), \
         patch("app.services.engine_router.render_queue_for", return_value=None), \
         patch("app.services.visual_fair_queue.visual_news_fanpage_ids", return_value=set()), \
         patch.object(renderer.render_design, "delay") as render:
        renderer.render_pending_designs.run()

    assert db.get(PublishJob, stale.id).status == PublishJobStatus.skipped
    assert db.get(PublishJob, stale.id).last_error.startswith("stale")
    assert db.get(PublishJob, fresh.id).status == PublishJobStatus.pending_design
    render.assert_called_once_with(fresh.id)


# ── T4b: IG redesign card (render_ig_recreate → redesign_card) ────────────────


@pytest.fixture
def ig_links(db):
    from app.models.fanpage_sources import FanpageSource

    FanpageSource.__table__.create(db.get_bind(), checkfirst=True)

    def _link(fanpage_id, prompt=None):
        db.add(FanpageSource(fanpage_id=fanpage_id, ig_source_id=9, chatgpt_edit_prompt=prompt))
        db.commit()
    return _link


def _ig_post(storage):
    source = storage / "ig_source.jpg"
    source.write_bytes(b"IG-SOURCE")
    return SimpleNamespace(id=55, ig_source_id=9, ig_source=None, image_local_paths=[str(source)],
                           original_caption="")


def _analysis(content_type="news"):
    return SimpleNamespace(type=content_type, mood="neutral", weather="", secondary_kind="none",
                           main_subject="Marc Marquez", speaker="", secondary="", scenario="none",
                           target_team="", moment_summary="Marquez wins")


def _redesign(db, job, fanpage, post, *, analysis=None, raw=b"\x89PNGraw-ai"):
    from app.services import redesign_pipeline
    from app.services.visual_engine import GenerationResult

    plan = SimpleNamespace(hero=SimpleNamespace(path="/hero.jpg", url=None, source="gallery"), inset=None,
                           inset_is_distinct=False, insets=[], extra=[], notes=[])
    finish = []

    def _fake_finish(db_, **kwargs):
        finish.append(kwargs)
        return f"/designs/final_{job.id}.png", "http://x/final.png"

    with patch.object(redesign_pipeline, "generate_ex",
                      return_value=GenerationResult(raw, "gptcli", {}, None)) as engine, \
         patch.object(redesign_pipeline, "plan_photos", return_value=plan), \
         patch.object(redesign_pipeline, "choose_layout", return_value=SimpleNamespace(layout="solo")), \
         patch.object(redesign_pipeline, "qa_enabled", return_value=False), \
         patch.object(redesign_pipeline, "_finish_card", side_effect=_fake_finish):
        res = redesign_pipeline.redesign_card(db, post=post, fanpage=fanpage, job=job,
                                              pre_analysis=analysis or _analysis())
    return res, engine, finish


def test_lane_ig_redesign_reuses_brand_raw_ai_image(db, storage, lane, ig_links):
    ig_links(1)
    ig_links(lane.id)
    post = _ig_post(storage)
    brand_job = _job(db, 1, post_id=55)
    brand_res, brand_engine, _ = _redesign(db, brand_job, db.get(TargetFanpage, 1), post)
    brand_job.status = PublishJobStatus.pending_publish  # render_ig_recreate does this on success
    db.commit()
    brand_engine.assert_called_once()
    assert brand_res.ok and brand_res.engine == "chatgpt"
    brand_record = brand_job.design_analysis_json["ai_image_reuse"]
    assert brand_record["format_key"] == "ig_redesign" and brand_record["layout"] == "solo"
    assert Path(brand_record["path"]).read_bytes() == b"\x89PNGraw-ai"

    lane_job = _job(db, lane.id, post_id=55)
    res, engine, finish = _redesign(db, lane_job, lane, post)

    engine.assert_not_called()
    assert res.ok and res.engine == "chatgpt"
    assert res.engine_note == f"reused AI image from job {brand_job.id}"
    assert finish[0]["gen_bytes"] == b"\x89PNGraw-ai" and finish[0]["job"] is lane_job
    own = lane_job.design_analysis_json["ai_image_reuse"]["path"]
    assert own != brand_record["path"] and Path(own).read_bytes() == b"\x89PNGraw-ai"


@pytest.mark.parametrize("lane_prompt, lane_type", [("Black and white", "news"), ("Golden hour", "quote")])
def test_ig_redesign_different_prompt_or_type_does_not_reuse(db, storage, lane, ig_links, lane_prompt, lane_type):
    ig_links(1, "Golden hour")
    ig_links(lane.id, lane_prompt)
    post = _ig_post(storage)
    _redesign(db, _job(db, 1, post_id=55, status=PublishJobStatus.pending_publish), db.get(TargetFanpage, 1), post)
    db.commit()

    lane_job = _job(db, lane.id, post_id=55)
    res, engine, _ = _redesign(db, lane_job, lane, post, analysis=_analysis(lane_type))

    engine.assert_called_once()
    assert res.ok and "reused" not in res.engine_note


def test_ig_redesign_keeps_no_raw_image_without_siblings(db, storage, ig_links):
    ig_links(STRANGER_ID)
    job = _job(db, STRANGER_ID, post_id=55)

    res, engine, _ = _redesign(db, job, db.get(TargetFanpage, STRANGER_ID), _ig_post(storage))

    engine.assert_called_once()
    assert res.ok and "ai_image_reuse" not in (job.design_analysis_json or {})
    assert not list((storage / "designs").glob(f"job_{job.id}_*")) if (storage / "designs").exists() else True


def test_ig_redesign_preview_never_reuses(db, storage, lane, ig_links):
    from app.services.redesign_pipeline import _reuse_key

    assert _reuse_key(db, post=_ig_post(storage), fanpage=lane, job=None, analysis=_analysis(), sport="MotoGP") is None


# ── T4b: AI speaker design (news + Facebook photo) ────────────────────────────


def _speaker(db, fanpage, job, *, mode="news", article=None, speaker="Marc Marquez", composed=b"\x89PNGspeaker"):
    from app.services.speaker_design import SpeakerDesignResult
    from app.tasks import design_renderer

    job.design_subtitle = speaker
    result = SpeakerDesignResult(composed, engine="chatgpt") if composed else None
    with patch("app.services.speaker_design.pick_speaker_photos", return_value=True), \
         patch("app.services.speaker_design.compose_speaker_design", return_value=result) as compose, \
         patch("app.services.speaker_design.qa_enabled", return_value=True), \
         patch("app.services.design_images.extract_two_subjects", return_value=(speaker, None)), \
         patch("app.services.design_images.extract_secondary_subject", return_value=None), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        res = design_renderer._maybe_compose_speaker_design(db, fanpage, job, article, mode=mode, force=True)
    return res, compose


def test_lane_news_speaker_design_reuses_brand_image(db, storage, lane):
    article = SimpleNamespace(id=7, scraped_title="Marquez wins", scraped_content="", article_published_at=NOW)
    brand_job = _job(db, 1, ContentType.news_content, status=PublishJobStatus.pending_publish, source_article_id=7)
    brand_res, brand_compose = _speaker(db, db.get(TargetFanpage, 1), brand_job, article=article)
    db.commit()
    brand_compose.assert_called_once()
    brand_record = brand_job.design_analysis_json["ai_image_reuse"]
    assert brand_record["source_key"] == "news:7" and brand_record["format_key"] == "speaker"
    assert Path(brand_record["path"]).read_bytes() == b"\x89PNGspeaker"

    lane_job = _job(db, lane.id, ContentType.news_content, source_article_id=7)
    res, compose = _speaker(db, lane, lane_job, article=article)

    compose.assert_not_called()
    assert bytes(res) == b"\x89PNGspeaker" and res.engine == "chatgpt"
    assert res.engine_note == f"reused AI image from job {brand_job.id}"
    own = lane_job.design_analysis_json["ai_image_reuse"]["path"]
    assert own != brand_record["path"] and Path(own).read_bytes() == b"\x89PNGspeaker"


def test_lane_facebook_speaker_design_reuses_by_fbid(db, storage, lane):
    brand_job = _job(db, 1, ContentType.facebook_recreate, status=PublishJobStatus.pending_publish,
                     source_gallery_image_id=_fb_marker(db, 1, "987"))
    _speaker(db, db.get(TargetFanpage, 1), brand_job, mode="facebook")
    db.commit()
    assert brand_job.design_analysis_json["ai_image_reuse"]["source_key"] == "fb:987"

    lane_job = _job(db, lane.id, ContentType.facebook_recreate, source_gallery_image_id=_fb_marker(db, lane.id, "987"))
    res, compose = _speaker(db, lane, lane_job, mode="facebook")

    compose.assert_not_called()
    assert f"job {brand_job.id}" in res.engine_note


def test_speaker_design_other_speaker_does_not_reuse(db, storage, lane):
    article = SimpleNamespace(id=7, scraped_title="Marquez wins", scraped_content="", article_published_at=NOW)
    _speaker(db, db.get(TargetFanpage, 1), _job(db, 1, ContentType.news_content,
                                                 status=PublishJobStatus.pending_publish, source_article_id=7),
             article=article)
    db.commit()

    _, compose = _speaker(db, lane, _job(db, lane.id, ContentType.news_content, source_article_id=7),
                          article=article, speaker="Pecco Bagnaia")

    compose.assert_called_once()


def test_speaker_design_without_siblings_keeps_no_raw_file(db, storage):
    article = SimpleNamespace(id=7, scraped_title="Marquez wins", scraped_content="", article_published_at=NOW)
    job = _job(db, STRANGER_ID, ContentType.news_content, source_article_id=7)

    res, compose = _speaker(db, db.get(TargetFanpage, STRANGER_ID), job, article=article)

    compose.assert_called_once()
    assert bytes(res) == b"\x89PNGspeaker"
    assert "ai_image_reuse" not in (job.design_analysis_json or {})
