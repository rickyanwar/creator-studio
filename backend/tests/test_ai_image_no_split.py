from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.config import get_settings
from app.models.design_templates import DesignTemplate
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ScrapedArticle
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.speaker_design import SpeakerDesignResult
from app.tasks.design_renderer import (
    render_card_png,
    render_design,
    render_facebook_photo,
)


def _make_job(**kwargs):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    defaults = {
        "attempt_count": 0,
        "created_at": now,
        "updated_at": now,
        "status": PublishJobStatus.pending_design,
        "content_type": ContentType.facebook_recreate,
        "source_article_id": None,
        "design_title": "Test Title",
        "design_subtitle": "Test Subtitle",
        "design_caption": "Test Caption",
    }
    defaults.update(kwargs)
    return PublishJob(**defaults)


def _setup_fb_render_mock(db, job, fanpage, template=None):
    def mock_query(model):
        m = MagicMock()
        if model == PublishJob:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
        elif model == TargetFanpage:
            m.filter_by.return_value.first.return_value = fanpage
        elif model == ScrapedArticle:
            m.filter_by.return_value.first.return_value = None
        elif model == DesignTemplate:
            m.filter_by.return_value.first.return_value = template
        else:
            m.filter_by.return_value.first.return_value = None
        return m

    db.query.side_effect = mock_query


def test_render_card_png_ai_image_true_passes_smart_false():
    """(a) render_card_png(..., ai_image=True) passes smart=False to prepare_design_images."""
    job = MagicMock(design_title="Title", design_subtitle="Sub", design_caption="Cap", source_article=None)
    fanpage = MagicMock(name="Fanpage", design_expand=False)
    template = MagicMock(template_json={"objects": []}, canvas_width=1080, canvas_height=1080)
    image_src = "data:image/png;base64,AAAA"

    mock_resp = MagicMock(status_code=200, content=b"fake-png-bytes")
    mock_resp.raise_for_status = MagicMock()

    with patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, [image_src])) as mock_prep, \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        res = render_card_png(
            job,
            fanpage,
            template,
            image_src,
            ai_image=True,
        )

    assert res == b"fake-png-bytes"
    mock_prep.assert_called_once()
    assert mock_prep.call_args.kwargs.get("smart") is False


def test_render_card_png_default_ai_image_false_passes_smart_true():
    """(b) render_card_png(...) default ai_image=False passes smart=True to prepare_design_images."""
    job = MagicMock(design_title="Title", design_subtitle="Sub", design_caption="Cap", source_article=None)
    fanpage = MagicMock(name="Fanpage", design_expand=False)
    template = MagicMock(template_json={"objects": []}, canvas_width=1080, canvas_height=1080)
    image_src = "data:image/png;base64,AAAA"

    mock_resp = MagicMock(status_code=200, content=b"fake-png-bytes")
    mock_resp.raise_for_status = MagicMock()

    with patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, [image_src])) as mock_prep, \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        res = render_card_png(
            job,
            fanpage,
            template,
            image_src,
        )

    assert res == b"fake-png-bytes"
    mock_prep.assert_called_once()
    assert mock_prep.call_args.kwargs.get("smart") is True


def test_render_design_passes_ai_image_flag(tmp_path, monkeypatch):
    """Verify render_design passes ai_image=True for pro_poster / speaker_design and False for gallery."""
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    job = MagicMock(id=501, fanpage_id=1, source_article_id=1, design_title="T", design_analysis_json={})
    fanpage = MagicMock(id=1, accent_color="#E8362B")
    article = MagicMock(id=1, scraped_title="S")
    template = MagicMock(id=1, template_json={"objects": []}, canvas_width=1080, canvas_height=1080)

    def fake_query(model):
        m = MagicMock()
        name = getattr(model, "__name__", str(model))
        if "Settings" in name:
            m.first.return_value = MagicMock(pro_poster_enabled=False)
            return m
        if "PublishJob" in name:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
            return m
        if "TargetFanpage" in name:
            m.filter_by.return_value.first.return_value = fanpage
            return m
        if "ScrapedArticle" in name:
            m.filter_by.return_value.first.return_value = article
            return m
        if "DesignTemplate" in name:
            m.filter_by.return_value.first.return_value = template
            return m
        return m

    db.query.side_effect = fake_query

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.tasks.design_renderer.select_image_for_job", return_value=("data:image/png;base64,A", None, "gallery:1")), \
         patch("app.services.design_images.single_photo_face_fits", return_value=True), \
         patch("app.tasks.design_renderer.render_card_png", return_value=b"png") as mock_render:
        render_design(501)

    mock_render.assert_called_once()
    assert mock_render.call_args.kwargs["ai_image"] is False

    mock_render.reset_mock()
    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.tasks.design_renderer._maybe_compose_speaker_design", return_value=b"fake-speaker-bytes"), \
         patch("app.tasks.design_renderer.render_card_png", return_value=b"png") as mock_render:
        render_design(501)

    mock_render.assert_called_once()
    assert mock_render.call_args.kwargs["ai_image"] is True


def test_render_facebook_photo_smart_flag(tmp_path, monkeypatch):
    """(c) render_facebook_photo with speaker_design passes smart=False; with gallery photo keeps smart=True."""
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    job = _make_job(
        id=12996,
        fanpage_id=5,
        design_title="Quote Title",
        design_subtitle="Speaker Name",
    )
    fanpage = TargetFanpage(
        id=5,
        name="testpage",
        mode2_gallery_niches=["motorsport"],
        fb_visual_engine="chatgpt",
        facebook_photo_publish_mode=PublishMode.manual_review,
    )
    template = DesignTemplate(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1080,
        category="quote",
    )
    _setup_fb_render_mock(db, job, fanpage, template=template)

    speaker_res = SpeakerDesignResult(b"png-composite", engine="chatgpt", engine_note="")
    mock_resp = MagicMock(status_code=200, content=b"final-rendered-png")
    mock_resp.raise_for_status = MagicMock()

    # Case 1: speaker design present -> smart=False
    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.services.speaker_design.pick_speaker_photos", return_value=[MagicMock()]), \
         patch("app.services.speaker_design.compose_speaker_design", return_value=speaker_res), \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])) as mock_prep, \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        render_facebook_photo(job.id)

    mock_prep.assert_called_once()
    assert mock_prep.call_args.kwargs.get("smart") is False

    # Case 2: no speaker design, fallback to gallery photo -> smart=True
    mock_prep.reset_mock()
    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.services.speaker_design.pick_speaker_photos", return_value=[MagicMock()]), \
         patch("app.services.speaker_design.compose_speaker_design", return_value=None), \
         patch("app.tasks.design_renderer._pick_fitting_facebook_photo", return_value=("data:image/png;base64,AAAA", None, "gallery:10")), \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])) as mock_prep, \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        render_facebook_photo(job.id)

    mock_prep.assert_called_once()
    assert mock_prep.call_args.kwargs.get("smart") is True


def _render_card_png_with(ai_image: bool):
    job = MagicMock(design_title="Title", design_subtitle="Sub", design_caption="Cap", source_article=None)
    fanpage = MagicMock(name="Fanpage", design_expand=False)
    template = MagicMock(template_json={"objects": []}, canvas_width=1080, canvas_height=1350)
    image_src = "data:image/png;base64,AAAA"
    mock_resp = MagicMock(status_code=200, content=b"png")
    mock_resp.raise_for_status = MagicMock()
    with patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, [image_src])), \
         patch("app.services.design_images.fix_unsafe_single_photo_face", return_value=None) as mock_fix, \
         patch("app.services.design_images.focus_points_for", return_value=[[0.5, 0.0]]) as mock_focus, \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        render_card_png(job, fanpage, template, image_src, ai_image=ai_image)
    return mock_fix, mock_focus


def test_render_card_png_ai_image_keeps_top_and_skips_face_fill():
    """An AI-made image is used as generated: crop anchored to the top, no content-aware fill."""
    mock_fix, mock_focus = _render_card_png_with(ai_image=True)
    mock_fix.assert_not_called()
    assert mock_focus.call_args.kwargs.get("keep_top") is True


def test_render_card_png_photo_keeps_face_fix_and_default_focus():
    """A normal photo keeps the face fill safety net and the face-aimed focus."""
    mock_fix, mock_focus = _render_card_png_with(ai_image=False)
    mock_fix.assert_called_once()
    assert mock_focus.call_args.kwargs.get("keep_top") is False
