import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def no_group_jobs(monkeypatch):
    monkeypatch.setattr("app.services.fb_share.group_jobs.is_group_only", lambda *args: False)
from PIL import Image

from app.config import get_settings
from app.models.design_templates import DesignTemplate
from app.models.fanpage_sources import FanpageSource
from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services import ig_photo_edit as edit
from app.services.redesign_pipeline import RedesignResult
from app.services.visual_engine import GenerationResult, VisualEngineDeferred, VisualEngineError
from app.tasks import ig_recreate


def image_bytes(color):
    buf = io.BytesIO()
    Image.new("RGB", (80, 100), color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))
    photo = tmp_path / "original.png"
    photo.write_bytes(image_bytes("red"))
    job = PublishJob(
        id=901, fanpage_id=1, post_id=1, design_title="Existing headline",
        design_template_id=1, content_type=ContentType.ig_recreate,
        status=PublishJobStatus.pending_design, ai_generated_caption="Existing caption",
        design_analysis_json={"main_subject": "Original subject"},
    )
    fanpage = TargetFanpage(id=1, name="Page", visual_engine="chatgpt",
                            publish_mode=PublishMode.manual_review, qa_ig_enabled=True)
    post = Post(id=1, ig_source_id=2, image_local_paths=[str(photo)])
    link = SimpleNamespace(chatgpt_edit_prompt="Make the background rainy", visual_engine_override="chatgpt")
    template = DesignTemplate(id=1, template_json={"objects": []})
    decision = SimpleNamespace(preview_status="running", preview_error="old error")
    db = MagicMock()

    def query(model):
        result = MagicMock()
        result.filter.return_value.update.return_value = 1
        values = {PublishJob: job, TargetFanpage: fanpage, Post: post,
                  FanpageSource: link, DesignTemplate: template}
        value = decision if model.__name__ == "RadarStoryDecision" else values.get(model)
        result.filter_by.return_value.first.return_value = value
        result.filter_by.return_value.one_or_none.return_value = value
        return result

    db.query.side_effect = query
    monkeypatch.setattr(ig_recreate, "SessionLocal", lambda: db)
    monkeypatch.setattr("app.tasks.design_renderer._release_queued_lock", MagicMock())
    renderer = MagicMock(side_effect=AssertionError("template renderer called"))
    prepare = MagicMock(side_effect=AssertionError("template preparation called"))
    monkeypatch.setattr(ig_recreate.httpx, "post", renderer)
    monkeypatch.setattr("app.services.design_images.prepare_design_images", prepare)
    publish = MagicMock()
    monkeypatch.setattr("app.tasks.publisher.publish_job.delay", publish)
    generate = MagicMock(return_value=GenerationResult(image_bytes("blue"), "gptcli", {}, None))
    monkeypatch.setattr(edit, "generate_ex", generate)
    monkeypatch.setattr(edit, "face_info", lambda data: {"box": (1, 1, 10, 10)})
    identity = MagicMock(return_value=SimpleNamespace(passed=True, reason="same person"))
    monkeypatch.setattr(edit, "check_identity", identity)
    return SimpleNamespace(**locals())


def test_build_edit_prompt():
    prompt = edit.build_edit_prompt("Owner's exact request", subject="A person")
    assert prompt.startswith("Edit this exact photo. Owner instructions: Owner's exact request\n\n")
    for text in ("face, identity, age and expression", "natural skin texture", "no HDR",
                 "logos, sponsor marks and team names", "never invent new text or logos"):
        assert text in prompt


def test_clean_jpeg_strips_metadata_and_transposes(tmp_path):
    source, final = tmp_path / "source.jpg", tmp_path / "clean.jpg"
    image = Image.new("RGB", (100, 80), "red")
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "Original artist"
    image.save(source, "JPEG", exif=exif, icc_profile=b"test-profile")
    # Add a real JPEG APP1 XMP segment independently of Pillow's save support.
    xmp = b"http://ns.adobe.com/xap/1.0/\x00<xmp>metadata</xmp>"
    data = source.read_bytes()
    source.write_bytes(data[:2] + b"\xff\xe1" + (len(xmp) + 2).to_bytes(2, "big") + xmp + data[2:])
    assert b"<xmp>" in source.read_bytes()
    assert edit.clean_jpeg(str(source), str(final)) == str(final)
    with Image.open(final) as cleaned:
        assert cleaned.format == "JPEG"
        assert cleaned.size == (1080, 1350)
        assert not cleaned.getexif()
        assert not {"xmp", "icc_profile", "exif"} & cleaned.info.keys()
    assert b"<xmp>" not in final.read_bytes()


@pytest.mark.parametrize("engine,meta,label", [
    ("gptcli", {}, "chatgpt"), ("9router", {}, "codex"),
    ("9router", {"fallback_from": "gptcli"}, "codex_fallback"),
])
@pytest.mark.parametrize("auto", [False, True])
def test_render_custom_edit(scene, engine, meta, label, auto):
    scene.generate.return_value = GenerationResult(image_bytes("blue"), engine, meta, None)
    if auto:
        scene.fanpage.publish_mode = PublishMode.auto
    # Direct-photo edits must not depend on a template or a title.
    scene.job.design_template_id = None
    scene.job.design_title = ""
    ig_recreate.render_ig_recreate(scene.job.id)
    scene.generate.assert_called_once()
    args, kwargs = scene.generate.call_args
    assert args[0] is scene.db
    assert args[1].refs == (str(scene.photo),)
    assert scene.link.chatgpt_edit_prompt in args[1].prompt
    assert kwargs == {"timeout": 300, "fanpage_id": 1}
    assert scene.identity.call_args.kwargs["context"] == "ig_edit"
    assert scene.job.design_engine == label
    assert scene.job.design_engine_note == "ChatGPT edit of the original photo (custom prompt)"
    assert scene.job.status == PublishJobStatus.pending_publish
    assert scene.job.ai_generated_caption == "Existing caption"
    assert scene.job.design_image_url.endswith("/designs/" + scene.job.design_image_path.split("/")[-1])
    assert scene.decision.preview_status == "done"
    with Image.open(scene.job.design_image_path) as image:
        assert image.getpixel((540, 675))[2] > 240
    scene.renderer.assert_not_called()
    scene.prepare.assert_not_called()
    assert scene.publish.call_count == int(auto)


@pytest.mark.parametrize("failure", ["identity", "identity_error", "engine", "empty", "invalid"])
def test_failure_posts_clean_original(scene, failure):
    if failure == "identity":
        scene.identity.return_value = SimpleNamespace(passed=False, reason="wrong person")
    elif failure == "identity_error":
        scene.identity.side_effect = RuntimeError("identity unavailable")
    elif failure == "engine":
        scene.generate.side_effect = VisualEngineError("transport", "engine failed")
    else:
        scene.generate.return_value = GenerationResult(b"" if failure == "empty" else b"bad image", "gptcli", {}, None)
    ig_recreate.render_ig_recreate(scene.job.id)
    assert scene.job.status == PublishJobStatus.pending_publish
    assert scene.job.design_engine == "original"
    assert "ChatGPT edit failed (" in scene.job.design_engine_note
    assert "_original_" in scene.job.design_image_path
    with Image.open(scene.job.design_image_path) as image:
        assert image.size == edit.OUTPUT_SIZE
        assert image.getpixel((540, 675))[0] > 240
    scene.generate.assert_called_once()
    scene.renderer.assert_not_called()
    scene.prepare.assert_not_called()


def test_deferred_does_not_post_original(scene):
    until = datetime.now(timezone.utc) + timedelta(hours=1)
    scene.generate.side_effect = VisualEngineDeferred(until, "night")
    ig_recreate.render_ig_recreate(scene.job.id)
    assert scene.job.status == PublishJobStatus.pending_design
    assert scene.job.design_not_before == until.replace(tzinfo=None)
    assert scene.job.design_image_path is None
    assert scene.job.design_engine is None
    scene.publish.assert_not_called()
    scene.identity.assert_not_called()


@pytest.mark.parametrize("prompt", [None, "", "  \n "])
def test_empty_prompt_uses_existing_redesign(scene, monkeypatch, prompt):
    scene.link.chatgpt_edit_prompt = prompt
    redesign = MagicMock(return_value=RedesignResult(
        ok=True, layout="solo", image_path="/card.png", image_url="/card.png",
        final_text_rendered_by="template", notes=[], problems=[], content_type="news", engine="chatgpt",
    ))
    monkeypatch.setattr("app.services.redesign_pipeline.redesign_card", redesign)
    ig_recreate.render_ig_recreate(scene.job.id)
    redesign.assert_called_once()
    scene.generate.assert_not_called()
    assert scene.job.status == PublishJobStatus.pending_publish


@pytest.mark.parametrize("qa_on,has_face", [(False, True), (True, False)])
def test_identity_only_when_enabled_and_face_present(scene, monkeypatch, qa_on, has_face):
    scene.fanpage.qa_ig_enabled = qa_on
    monkeypatch.setattr(edit, "face_info", lambda data: {} if has_face else None)
    ig_recreate.render_ig_recreate(scene.job.id)
    assert scene.job.design_engine == "chatgpt"
    scene.identity.assert_not_called()


def test_missing_photo_keeps_existing_failure_behavior(scene):
    scene.post.image_local_paths = []
    ig_recreate.render_ig_recreate(scene.job.id)
    assert scene.job.status == PublishJobStatus.pending_design
    assert scene.job.last_error == "post has no image"
    scene.generate.assert_not_called()
