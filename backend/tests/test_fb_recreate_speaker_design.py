from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.config import get_settings
from app.models.design_templates import DesignTemplate
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ScrapedArticle
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.speaker_design import SpeakerDesignResult
from app.tasks.design_renderer import (
    _maybe_compose_speaker_design,
    _name_badge,
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
    }
    defaults.update(kwargs)
    return PublishJob(**defaults)


def _setup_render_mock(db, job, fanpage, template=None):
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


def test_quote_reaches_compose_speaker_design_and_sets_engine(tmp_path, monkeypatch):
    """(a) Quote job with person subtitle reaches compose_speaker_design and gets design_engine."""
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    job = _make_job(
        id=12996,
        fanpage_id=5,
        design_title="I talk to Max often.",
        design_subtitle="Gabriel Bortoleto",
    )
    fanpage = TargetFanpage(
        id=5,
        name="intan",
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
    _setup_render_mock(db, job, fanpage, template=template)

    speaker_res = SpeakerDesignResult(b"png-composite", engine="chatgpt", engine_note="")
    mock_resp = MagicMock(status_code=200, content=b"final-rendered-png")
    mock_resp.raise_for_status = MagicMock()

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.services.speaker_design.pick_speaker_photos", return_value=[MagicMock()]), \
         patch("app.services.speaker_design.compose_speaker_design", return_value=speaker_res) as mock_compose, \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])), \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        render_facebook_photo(job.id)

    mock_compose.assert_called_once()
    kwargs = mock_compose.call_args.kwargs
    assert kwargs["speaker"] == "Gabriel Bortoleto"
    assert kwargs["content_kind"] == "facebook"
    assert kwargs["article_text"] == ""

    assert job.status == PublishJobStatus.pending_publish
    assert job.design_engine == "chatgpt"
    assert job.design_engine_note is None
    assert job.last_image_marker == "speaker_design"


def test_speaker_design_none_falls_back_to_template_with_note(tmp_path, monkeypatch):
    """(b) When speaker design returns None, template path is used and design_engine_note is set."""
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    job = _make_job(
        id=12989,
        fanpage_id=5,
        design_title="He then returned to Milton Keynes for his lunch in the staff canteen.",
        design_subtitle="Christian Horner",
    )
    fanpage = TargetFanpage(
        id=5,
        name="intan",
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
    _setup_render_mock(db, job, fanpage, template=template)

    mock_resp = MagicMock(status_code=200, content=b"final-rendered-png")
    mock_resp.raise_for_status = MagicMock()

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.services.speaker_design.pick_speaker_photos", return_value=[MagicMock()]), \
         patch("app.services.speaker_design.compose_speaker_design", return_value=None), \
         patch("app.tasks.design_renderer._pick_fitting_facebook_photo", return_value=("data:image/png;base64,AAAA", None, "gallery:10")), \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])), \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        render_facebook_photo(job.id)

    assert job.status == PublishJobStatus.pending_publish
    assert job.design_engine == "template"
    assert job.design_engine_note == "AI image failed → template"
    assert job.last_image_marker == "gallery:10"


def test_fb_visual_engine_off_never_calls_compose(tmp_path, monkeypatch):
    """(c) fb_visual_engine='off' never calls compose_speaker_design."""
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    job = _make_job(
        id=12996,
        fanpage_id=5,
        design_title="I talk to Max often.",
        design_subtitle="Gabriel Bortoleto",
    )
    fanpage = TargetFanpage(
        id=5,
        name="intan",
        mode2_gallery_niches=["motorsport"],
        fb_visual_engine="off",
        facebook_photo_publish_mode=PublishMode.manual_review,
    )
    template = DesignTemplate(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1080,
        category="quote",
    )
    _setup_render_mock(db, job, fanpage, template=template)

    mock_resp = MagicMock(status_code=200, content=b"final-rendered-png")
    mock_resp.raise_for_status = MagicMock()

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.services.speaker_design.compose_speaker_design") as mock_compose, \
         patch("app.tasks.design_renderer._pick_fitting_facebook_photo", return_value=("data:image/png;base64,AAAA", None, "gallery:10")), \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])), \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp):
        render_facebook_photo(job.id)

    mock_compose.assert_not_called()
    assert job.status == PublishJobStatus.pending_publish
    assert job.design_engine == "template"
    assert job.design_engine_note is None


def test_discussion_label_not_treated_as_speaker_name():
    """Discussion labels (DISCUSSION, HOT TAKE) must not be treated as speaker names."""
    assert _name_badge("DISCUSSION") is None
    assert _name_badge("HOT TAKE") is None
    assert _name_badge("discussion") is None
    assert _name_badge("hot take") is None
    assert _name_badge("Gabriel Bortoleto") == "Gabriel Bortoleto"
    assert _name_badge("Christian Horner") == "Christian Horner"


def test_discussion_job_without_person_does_not_call_compose():
    """Discussion card with subtitle DISCUSSION and no person in title keeps template."""
    fanpage = TargetFanpage(
        id=5,
        name="intan",
        mode2_gallery_niches=["motorsport"],
        fb_visual_engine="chatgpt",
    )
    job = _make_job(
        id=12999,
        design_title="Who holds the most fastest laps in Austin?",
        design_subtitle="DISCUSSION",
    )
    with patch("app.services.design_images.extract_two_subjects", return_value=(None, None)), \
         patch("app.services.speaker_design.compose_speaker_design") as mock_compose:
        res = _maybe_compose_speaker_design(MagicMock(), fanpage, job, None, mode="facebook")

    assert res is None
    mock_compose.assert_not_called()
