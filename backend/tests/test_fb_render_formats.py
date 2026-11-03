import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.config import get_settings
from app.models.facebook_photo_ideas import FacebookPhotoIdea
from app.models.facebook_photo_sources import FacebookPhotoSource
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services import ig_photo_edit as edit
from app.services.visual_engine import GenerationResult, VisualEngineDeferred, VisualEngineError
from app.tasks import design_renderer as renderer
from .test_fb_recreate_speaker_design import _make_job, _setup_render_mock
from app.models.design_templates import DesignTemplate


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))
    photo = tmp_path / "original.jpg"
    exif = Image.Exif()
    exif[315] = "Source artist"
    Image.new("RGB", (1080, 1080), "red").save(photo, exif=exif)
    job = _make_job(id=901, fanpage_id=1, source_gallery_image_id=10,
                    design_title="Headline", design_subtitle="Gabriel Bortoleto",
                    ai_generated_caption="Existing caption")
    fanpage = TargetFanpage(id=1, name="Page", fb_visual_engine="off",
                            facebook_photo_publish_mode=PublishMode.manual_review,
                            qa_facebook_enabled=True)
    source = FacebookPhotoSource(id=2, format="repost", chatgpt_edit_prompt=None)
    idea = FacebookPhotoIdea(id=3, fanpage_id=1, gallery_image_id=10, source_id=2,
                             image_local_path=str(photo), design_subtitle="Gabriel Bortoleto")
    template = DesignTemplate(id=1, template_json={"objects": []},
                              canvas_width=1080, canvas_height=1080, category="quote")
    db = MagicMock()
    _setup_render_mock(db, job, fanpage, template)
    base_query = db.query.side_effect

    def query(model):
        result = base_query(model)
        if model == FacebookPhotoIdea:
            result.filter_by.return_value.order_by.return_value.first.return_value = idea
        elif model == FacebookPhotoSource:
            result.filter_by.return_value.first.return_value = source
        return result

    db.query.side_effect = query
    monkeypatch.setattr(renderer, "SessionLocal", lambda: db)
    monkeypatch.setattr(renderer, "_release_queued_lock", MagicMock())
    render = MagicMock(return_value=SimpleNamespace(content=b"rendered", raise_for_status=lambda: None))
    monkeypatch.setattr(renderer.httpx, "post", render)
    monkeypatch.setattr("app.services.design_images.resolve_template", lambda *a, **kw: template)
    monkeypatch.setattr("app.services.design_images.prepare_design_images",
                        lambda *a, **kw: ({"objects": []}, ["data:image/png;base64,AAAA"]))
    monkeypatch.setattr(renderer, "_pick_fitting_facebook_photo",
                        lambda *a: ("data:image/png;base64,AAAA", None, "search"))
    speaker = MagicMock(return_value=None)
    monkeypatch.setattr(renderer, "_maybe_compose_speaker_design", speaker)
    publish = MagicMock()
    monkeypatch.setattr("app.tasks.publisher.publish_job.delay", publish)
    buf = io.BytesIO()
    Image.new("RGB", (80, 100), "blue").save(buf, "PNG")
    generate = MagicMock(return_value=GenerationResult(buf.getvalue(), "gptcli", {}, None))
    monkeypatch.setattr(edit, "generate_ex", generate)
    monkeypatch.setattr(edit, "face_info", lambda data: {"box": (1, 1, 10, 10)})
    identity = MagicMock(return_value=SimpleNamespace(passed=True, reason="same person"))
    monkeypatch.setattr(edit, "check_identity", identity)
    return SimpleNamespace(**locals())


@pytest.mark.parametrize("auto", [False, True])
def test_repost_keeps_original_size_and_caption(scene, auto):
    if auto:
        scene.fanpage.facebook_photo_publish_mode = PublishMode.auto
    renderer.render_facebook_photo(scene.job.id)
    assert scene.job.design_engine == "original"
    assert scene.job.design_engine_note == "Repost of the original Facebook photo"
    assert scene.job.status.value == "pending_publish"
    assert scene.job.ai_generated_caption == "Existing caption"
    with Image.open(scene.job.design_image_path) as image:
        assert image.size == (1080, 1080)
        assert not image.getexif()
        assert not {"exif", "xmp", "icc_profile"} & image.info.keys()
    assert scene.job.design_image_url.endswith("/designs/" + scene.job.design_image_path.split("/")[-1])
    scene.render.assert_not_called()
    scene.speaker.assert_not_called()
    scene.generate.assert_not_called()
    assert scene.publish.call_count == int(auto)


@pytest.mark.parametrize("engine,meta,label", [
    ("gptcli", {}, "chatgpt"), ("9router", {}, "codex"),
    ("9router", {"fallback_from": "gptcli"}, "codex_fallback"),
])
def test_custom_edit_uses_original_reference(scene, engine, meta, label):
    scene.source.format = "chatgpt"
    scene.source.chatgpt_edit_prompt = "Make the background rainy"
    scene.generate.return_value = GenerationResult(scene.generate.return_value.image, engine, meta, None)
    renderer.render_facebook_photo(scene.job.id)
    scene.generate.assert_called_once()
    assert scene.generate.call_args.args[1].refs == (str(scene.photo),)
    assert scene.source.chatgpt_edit_prompt in scene.generate.call_args.args[1].prompt
    assert scene.identity.call_args.kwargs["subject"] == "Gabriel Bortoleto"
    assert scene.job.design_engine == label
    assert scene.job.design_engine_note == "ChatGPT edit of the original Facebook photo (custom prompt)"
    assert scene.job.ai_generated_caption == "Existing caption"
    scene.render.assert_not_called()
    scene.speaker.assert_not_called()


@pytest.mark.parametrize("failure", ["identity", "identity_error", "engine"])
def test_edit_failure_posts_original(scene, failure):
    scene.source.format = "chatgpt"
    scene.source.chatgpt_edit_prompt = "Rain"
    if failure == "identity":
        scene.identity.return_value = SimpleNamespace(passed=False, reason="wrong person")
    elif failure == "identity_error":
        scene.identity.side_effect = RuntimeError("identity unavailable")
    else:
        scene.generate.side_effect = VisualEngineError("transport", "engine failed")
    renderer.render_facebook_photo(scene.job.id)
    assert scene.job.design_engine == "original"
    assert "ChatGPT edit failed (" in scene.job.design_engine_note
    with Image.open(scene.job.design_image_path) as image:
        assert image.size == (1080, 1080)
        assert image.getpixel((500, 500))[0] > 240
    scene.render.assert_not_called()


def test_edit_deferred_does_not_publish_original(scene):
    scene.source.format = "chatgpt"
    scene.source.chatgpt_edit_prompt = "Rain"
    until = datetime.now(timezone.utc) + timedelta(hours=1)
    scene.generate.side_effect = VisualEngineDeferred(until, "night")
    renderer.render_facebook_photo(scene.job.id)
    assert scene.job.status.value == "pending_design"
    assert scene.job.design_not_before == until.replace(tzinfo=None)
    assert scene.job.design_image_path is None
    scene.publish.assert_not_called()
    scene.render.assert_not_called()


@pytest.mark.parametrize("prompt", [None, "", "  \n "])
def test_empty_prompt_forces_speaker_and_restores_fanpage(scene, prompt):
    scene.source.format = "chatgpt"
    scene.source.chatgpt_edit_prompt = prompt
    scene.speaker.side_effect = lambda db, fanpage, *a, **kw: (
        b"speaker" if fanpage.fb_visual_engine == "chatgpt" else None
    )
    renderer.render_facebook_photo(scene.job.id)
    scene.speaker.assert_called_once()
    assert scene.job.last_image_marker == "speaker_design"
    assert scene.fanpage.fb_visual_engine == "off"


def test_template_skips_speaker_even_with_fanpage_chatgpt(scene):
    scene.source.format = "template"
    scene.fanpage.fb_visual_engine = "chatgpt"
    renderer.render_facebook_photo(scene.job.id)
    scene.speaker.assert_not_called()
    assert scene.job.design_engine == "template"
    assert scene.job.design_engine_note is None


@pytest.mark.parametrize("fmt", ["follow", None])
def test_follow_preserves_existing_path(scene, fmt):
    scene.source.format = fmt
    renderer.render_facebook_photo(scene.job.id)
    scene.speaker.assert_called_once()
    scene.render.assert_called_once()
    assert scene.job.design_engine == "template"


@pytest.mark.parametrize("fmt", ["repost", "chatgpt"])
def test_missing_original_uses_template_with_note(scene, fmt):
    scene.source.format = fmt
    scene.source.chatgpt_edit_prompt = "Rain"
    scene.idea.image_local_path = None
    renderer.render_facebook_photo(scene.job.id)
    assert scene.job.design_engine == "template"
    assert "original photo missing" in scene.job.design_engine_note
    scene.render.assert_called_once()
    scene.generate.assert_not_called()


def test_clean_jpeg_none_preserves_transposed_size(tmp_path):
    source, final = tmp_path / "source.jpg", tmp_path / "clean.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (120, 80)).save(source, exif=exif, icc_profile=b"profile")
    edit.clean_jpeg(str(source), str(final), size=None)
    with Image.open(final) as image:
        assert image.size == (80, 120)
        assert not image.getexif()
        assert "icc_profile" not in image.info
