from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.api.publish_jobs import _enrich_job
from app.models.design_templates import DesignTemplate
from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ScrapedArticle
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.redesign_pipeline import RedesignResult
from app.services.speaker_design import SpeakerDesignResult
from app.tasks.design_renderer import (
    render_design,
    render_discussion,
    render_facebook_photo,
)
from app.tasks.ig_recreate import render_ig_recreate


def _make_job(**kwargs):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    defaults = {
        "attempt_count": 0,
        "created_at": now,
        "updated_at": now,
        "status": PublishJobStatus.pending_design,
    }
    defaults.update(kwargs)
    return PublishJob(**defaults)


def _setup_render_mock(db, job, fanpage, template=None, article=None, post=None, decision=None):
    def mock_query(model):
        m = MagicMock()
        if model == PublishJob:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
        elif model == TargetFanpage:
            m.filter_by.return_value.first.return_value = fanpage
        elif model == ScrapedArticle:
            m.filter_by.return_value.first.return_value = article
        elif model == DesignTemplate:
            m.filter_by.return_value.first.return_value = template
        elif model == Post:
            m.filter_by.return_value.first.return_value = post
        elif getattr(model, "__name__", "") == "RadarStoryDecision":
            m.filter_by.return_value.first.return_value = decision
        else:
            m.filter_by.return_value.first.return_value = None
        return m

    db.query.side_effect = mock_query


def test_render_design_engine_speaker_chatgpt(tmp_path, monkeypatch):
    """Test 1a: render_design with SpeakerDesignResult(engine='chatgpt') sets job.design_engine='chatgpt'."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    job = _make_job(
        id=101,
        fanpage_id=1,
        source_article_id=1,
        content_type=ContentType.news_content,
        design_title="Headline",
    )
    fanpage = TargetFanpage(
        id=1,
        name="Test FP",
        mode2_gallery_niches=["MotoGP"],
        news_visual_engine="chatgpt",
        mode2_publish_mode=PublishMode.manual_review,
    )
    article = MagicMock(scraped_title="Headline", id=1)
    template = DesignTemplate(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1080,
        category="news",
    )
    _setup_render_mock(db, job, fanpage, template=template, article=article)

    speaker = SpeakerDesignResult(b"fake-bytes", engine="chatgpt", engine_note="ok")
    mock_resp = MagicMock(status_code=200, content=b"png-output")
    mock_resp.raise_for_status = MagicMock()

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.tasks.design_renderer._maybe_compose_speaker_design", return_value=speaker), \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])), \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        render_design(101)

    assert job.status == PublishJobStatus.pending_publish
    assert job.design_engine == "chatgpt"
    assert job.design_engine_note == "ok"


def test_render_design_engine_speaker_codex_fallback(tmp_path, monkeypatch):
    """Test 1b: render_design with engine='codex_fallback' and note stores engine and note."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    job = _make_job(
        id=102,
        fanpage_id=1,
        source_article_id=1,
        content_type=ContentType.news_content,
        design_title="Headline",
    )
    fanpage = TargetFanpage(
        id=1,
        name="Test FP",
        mode2_gallery_niches=["MotoGP"],
        news_visual_engine="chatgpt",
        mode2_publish_mode=PublishMode.manual_review,
    )
    article = MagicMock(scraped_title="Headline", id=1)
    template = DesignTemplate(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1080,
        category="news",
    )
    _setup_render_mock(db, job, fanpage, template=template, article=article)

    note = "ChatGPT rate limit -> codex fallback"
    speaker = SpeakerDesignResult(b"fake-bytes", engine="codex_fallback", engine_note=note)
    mock_resp = MagicMock(status_code=200, content=b"png-output")
    mock_resp.raise_for_status = MagicMock()

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.tasks.design_renderer._maybe_compose_speaker_design", return_value=speaker), \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])), \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        render_design(102)

    assert job.status == PublishJobStatus.pending_publish
    assert job.design_engine == "codex_fallback"
    assert job.design_engine_note == note


def test_render_design_engine_failed_ai_fallback_to_template(tmp_path, monkeypatch):
    """Test 1c: render_design with no speaker design and news toggle 'chatgpt' sets template + note."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    job = _make_job(
        id=103,
        fanpage_id=1,
        source_article_id=1,
        content_type=ContentType.news_content,
        design_title="Headline",
    )
    fanpage = TargetFanpage(
        id=1,
        name="Test FP",
        mode2_gallery_niches=["MotoGP"],
        news_visual_engine="chatgpt",
        mode2_publish_mode=PublishMode.manual_review,
    )
    article = MagicMock(scraped_title="Headline", id=1)
    template = DesignTemplate(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1080,
        category="news",
    )
    _setup_render_mock(db, job, fanpage, template=template, article=article)

    mock_resp = MagicMock(status_code=200, content=b"png-output")
    mock_resp.raise_for_status = MagicMock()

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.tasks.design_renderer._maybe_compose_speaker_design", return_value=None), \
         patch("app.tasks.design_renderer.select_image_for_job", return_value=("data:image/png;base64,AAAA", None, "scraped:url")), \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])), \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        render_design(103)

    assert job.status == PublishJobStatus.pending_publish
    assert job.design_engine == "template"
    assert job.design_engine_note == "AI image failed → template"


def test_render_design_engine_toggle_off_sets_template_no_note(tmp_path, monkeypatch):
    """Test 1d: render_design with toggle 'off' sets template and note None."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    job = _make_job(
        id=104,
        fanpage_id=1,
        source_article_id=1,
        content_type=ContentType.news_content,
        design_title="Headline",
    )
    fanpage = TargetFanpage(
        id=1,
        name="Test FP",
        mode2_gallery_niches=["MotoGP"],
        news_visual_engine="off",
        mode2_publish_mode=PublishMode.manual_review,
    )
    article = MagicMock(scraped_title="Headline", id=1)
    template = DesignTemplate(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1080,
        category="news",
    )
    _setup_render_mock(db, job, fanpage, template=template, article=article)

    mock_resp = MagicMock(status_code=200, content=b"png-output")
    mock_resp.raise_for_status = MagicMock()

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.tasks.design_renderer._maybe_compose_speaker_design", return_value=None), \
         patch("app.tasks.design_renderer.select_image_for_job", return_value=("data:image/png;base64,AAAA", None, "scraped:url")), \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])), \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        render_design(104)

    assert job.status == PublishJobStatus.pending_publish
    assert job.design_engine == "template"
    assert job.design_engine_note is None


def test_render_facebook_photo_ai_and_template_cases(tmp_path, monkeypatch):
    """Test 2: render_facebook_photo: one AI case + one template case."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    template = DesignTemplate(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1080,
        category="news",
    )

    # 2a: AI case
    job_ai = _make_job(
        id=201,
        fanpage_id=1,
        content_type=ContentType.facebook_recreate,
        design_title="FB Title",
    )
    fanpage_ai = TargetFanpage(
        id=1,
        name="Test FP",
        mode2_gallery_niches=["MotoGP"],
        fb_visual_engine="chatgpt",
        facebook_photo_publish_mode=PublishMode.manual_review,
    )
    _setup_render_mock(db, job_ai, fanpage_ai, template=template)

    speaker = SpeakerDesignResult(b"fake-bytes", engine="codex", engine_note="direct codex")
    mock_resp = MagicMock(status_code=200, content=b"png-output")
    mock_resp.raise_for_status = MagicMock()

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.tasks.design_renderer._maybe_compose_speaker_design", return_value=speaker), \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])), \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        render_facebook_photo(201)

    assert job_ai.status == PublishJobStatus.pending_publish
    assert job_ai.design_engine == "codex"
    assert job_ai.design_engine_note == "direct codex"

    # 2b: Template case (AI failed)
    job_tpl = _make_job(
        id=202,
        fanpage_id=1,
        content_type=ContentType.facebook_recreate,
        design_title="FB Title",
    )
    _setup_render_mock(db, job_tpl, fanpage_ai, template=template)

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.tasks.design_renderer._maybe_compose_speaker_design", return_value=None), \
         patch("app.tasks.design_renderer._pick_fitting_facebook_photo", return_value=("data:image/png;base64,AAAA", None, "gallery:1")), \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])), \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        render_facebook_photo(202)

    assert job_tpl.status == PublishJobStatus.pending_publish
    assert job_tpl.design_engine == "template"
    assert job_tpl.design_engine_note == "AI image failed → template"


def test_render_ig_recreate_ai_success_and_template_fallback(tmp_path, monkeypatch):
    """Test 3: render_ig_recreate: RedesignResult(ok=True) -> 'chatgpt'; AI failed -> 'template' + note."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    dummy_img = tmp_path / "post_img.jpg"
    dummy_img.write_bytes(b"dummy-jpg")

    post = Post(id=1, image_local_paths=[str(dummy_img)], ig_source_id=1)
    template = DesignTemplate(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1080,
        category="quote",
    )

    # 3a: AI success
    job_ai = _make_job(
        id=301,
        fanpage_id=1,
        post_id=1,
        design_template_id=1,
        content_type=ContentType.ig_recreate,
        design_title="IG Quote",
    )
    fanpage = TargetFanpage(
        id=1,
        name="Test FP",
        visual_engine="chatgpt",
        publish_mode=PublishMode.manual_review,
    )
    _setup_render_mock(db, job_ai, fanpage, template=template, post=post)

    res_success = RedesignResult(
        ok=True,
        layout="solo",
        image_path=str(tmp_path / "out.png"),
        image_url="http://storage/out.png",
        final_text_rendered_by="ai",
        notes=[],
        problems=[],
        content_type="quote",
        engine="chatgpt",
        engine_note="AI styled ok",
    )

    with patch("app.tasks.ig_recreate.SessionLocal", return_value=db), \
         patch("app.services.redesign_pipeline.redesign_card", return_value=res_success):
        render_ig_recreate(301)

    assert job_ai.status == PublishJobStatus.pending_publish
    assert job_ai.design_engine == "chatgpt"
    assert job_ai.design_engine_note == "AI styled ok"

    # 3b: AI failed -> fallback to template branch
    job_fail = _make_job(
        id=302,
        fanpage_id=1,
        post_id=1,
        design_template_id=1,
        content_type=ContentType.ig_recreate,
        design_title="IG Quote",
    )
    _setup_render_mock(db, job_fail, fanpage, template=template, post=post)

    res_fail = RedesignResult(
        ok=False,
        layout="solo",
        image_path=None,
        image_url=None,
        final_text_rendered_by="template",
        notes=[],
        problems=["Face check failed"],
        content_type="quote",
    )
    mock_resp = MagicMock(status_code=200, content=b"png-output")
    mock_resp.raise_for_status = MagicMock()

    with patch("app.tasks.ig_recreate.SessionLocal", return_value=db), \
         patch("app.services.redesign_pipeline.redesign_card", return_value=res_fail), \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])), \
         patch("app.services.design_images.focus_points_for", return_value=[None]), \
         patch("app.tasks.ig_recreate.httpx.post", return_value=mock_resp):
        render_ig_recreate(302)

    assert job_fail.status == PublishJobStatus.pending_publish
    assert job_fail.design_engine == "template"
    assert job_fail.design_engine_note == "AI image failed → template"


def test_render_discussion_engine_is_template(tmp_path, monkeypatch):
    """Test 4: render_discussion sets design_engine='template' and design_engine_note=None."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    job = _make_job(
        id=401,
        fanpage_id=1,
        content_type=ContentType.discussion,
        design_title="Discussion Topic",
        design_caption="Topic Subject",
    )
    fanpage = TargetFanpage(
        id=1,
        name="Test FP",
        mode2_gallery_niches=["MotoGP"],
        discussion_publish_mode=PublishMode.manual_review,
    )
    template = DesignTemplate(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1080,
        category="discussion",
    )
    _setup_render_mock(db, job, fanpage, template=template)

    mock_resp = MagicMock(status_code=200, content=b"png-output")
    mock_resp.raise_for_status = MagicMock()

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.services.design_images.find_gallery_datauri", return_value=("data:image/png;base64,AAAA", MagicMock(id=1))), \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])), \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        render_discussion(401)

    assert job.status == PublishJobStatus.pending_publish
    assert job.design_engine == "template"
    assert job.design_engine_note is None


def test_enrich_job_returns_both_engine_fields():
    """Test 5: _enrich_job returns both design_engine and design_engine_note."""
    job = _make_job(
        id=501,
        fanpage_id=1,
        status=PublishJobStatus.pending_publish,
        content_type=ContentType.news_content,
        design_engine="codex_fallback",
        design_engine_note="fallback note",
    )
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = None

    out = _enrich_job(job, db)
    assert out.design_engine == "codex_fallback"
    assert out.design_engine_note == "fallback note"


def test_migration_mentions_both_columns_in_upgrade_and_downgrade():
    """Test 6: Migration c80000000025 mentions both columns in upgrade and downgrade."""
    migration_path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "c80000000025_gptcli_engine.py"
    assert migration_path.exists(), f"Migration file not found at {migration_path}"
    content = migration_path.read_text(encoding="utf-8")

    upgrade_part, downgrade_part = content.split("def downgrade()")
    assert 'op.add_column("publish_jobs", sa.Column("design_engine"' in upgrade_part
    assert 'op.add_column("publish_jobs", sa.Column("design_engine_note"' in upgrade_part

    assert 'op.drop_column("publish_jobs", "design_engine")' in downgrade_part
    assert 'op.drop_column("publish_jobs", "design_engine_note")' in downgrade_part


def test_manual_designer_upload_sets_template_and_note(tmp_path, monkeypatch):
    """Test manual designer upload sets engine='template' and note='Designer (manual)'."""
    import asyncio
    from app.api.publish_jobs import upload_design_image
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))
    job = _make_job(
        id=601,
        fanpage_id=1,
        content_type=ContentType.news_content,
        status=PublishJobStatus.pending_design,
    )
    fanpage = TargetFanpage(id=1, name="Test FP")
    db = MagicMock()
    _setup_render_mock(db, job, fanpage)

    fake_file = MagicMock()
    fake_file.read = MagicMock(return_value=asyncio.Future())
    fake_file.read.return_value.set_result(b"pngdata")

    with patch("app.tasks.publisher.publish_job.delay"):
        asyncio.run(upload_design_image(job_id=601, db=db, _=MagicMock(), file=fake_file))

    assert job.design_engine == "template"
    assert job.design_engine_note == "Designer (manual)"


def test_reedit_job_resets_engine_and_note():
    """Test reedit_job resets design_engine and design_engine_note to None."""
    from app.api.publish_jobs import reedit_job
    job = _make_job(
        id=701,
        fanpage_id=1,
        content_type=ContentType.news_content,
        status=PublishJobStatus.published,
        design_image_url="http://storage/design.png",
        design_engine="chatgpt",
        design_engine_note="AI styled ok",
    )
    fanpage = TargetFanpage(id=1, name="Test FP")
    db = MagicMock()
    _setup_render_mock(db, job, fanpage)

    with patch("app.tasks.design_renderer.render_design.delay"), \
         patch("app.api.publish_jobs._enrich_job", return_value=job):
        reedit_job(job_id=701, db=db, _=MagicMock())

    assert job.design_engine is None
    assert job.design_engine_note is None


def test_note_truncation_to_200_chars(tmp_path, monkeypatch):
    """Test that notes longer than 200 chars are truncated to 200 chars."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    job = _make_job(
        id=801,
        fanpage_id=1,
        source_article_id=1,
        content_type=ContentType.news_content,
        design_title="Headline",
    )
    fanpage = TargetFanpage(
        id=1,
        name="Test FP",
        mode2_gallery_niches=["MotoGP"],
        news_visual_engine="chatgpt",
        mode2_publish_mode=PublishMode.manual_review,
    )
    article = MagicMock(scraped_title="Headline", id=1)
    template = DesignTemplate(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1080,
        category="news",
    )
    _setup_render_mock(db, job, fanpage, template=template, article=article)

    long_note = "A" * 350
    speaker = SpeakerDesignResult(b"fake-bytes", engine="codex_fallback", engine_note=long_note)
    mock_resp = MagicMock(status_code=200, content=b"png-output")
    mock_resp.raise_for_status = MagicMock()

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.tasks.design_renderer._maybe_compose_speaker_design", return_value=speaker), \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])), \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        render_design(801)

    assert job.design_engine_note == "A" * 200
    assert len(job.design_engine_note) == 200

