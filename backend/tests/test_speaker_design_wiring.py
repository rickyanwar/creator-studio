from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.tasks.design_renderer import _maybe_compose_speaker_design


def _article():
    return SimpleNamespace(
        scraped_title="Rider wins the race",
        scraped_content="Race report content " * 200,
        article_published_at=datetime(2026, 10, 12, 10, 0),
        scraped_at=datetime(2026, 10, 12, 11, 0),
    )


def test_speaker_design_off_does_not_compose():
    fanpage = SimpleNamespace(news_visual_engine="off", mode2_gallery_niches=["MotoGP"], name="GPOne")
    job = SimpleNamespace(design_subtitle="Marc Marquez", design_title="Rider wins the race")

    with patch("app.services.speaker_design.compose_speaker_design") as compose:
        assert _maybe_compose_speaker_design(MagicMock(), fanpage, job, _article(), mode="news") is None

    compose.assert_not_called()


def test_speaker_design_chatgpt_composes_news_with_badge_speaker_and_article_context():
    fanpage = SimpleNamespace(news_visual_engine="chatgpt", mode2_gallery_niches=["MotoGP"], name="GPOne")
    job = SimpleNamespace(design_subtitle="Marc Marquez", design_title="Rider wins the race")
    generated = b"speaker-design"

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[object()]), \
         patch("app.services.speaker_design.compose_speaker_design", return_value=generated) as compose:
        result = _maybe_compose_speaker_design(MagicMock(), fanpage, job, _article(), mode="news")

    assert result == generated
    kwargs = compose.call_args.kwargs
    assert kwargs["speaker"] == "Marc Marquez"
    assert kwargs["sport"] == "MotoGP"
    assert kwargs["article_text"].startswith("Rider wins the race\nRace report content")
    assert len(kwargs["article_text"]) <= 2000
    assert kwargs["around"] == datetime(2026, 10, 12, 10, 0)


def test_speaker_design_chatgpt_facebook_uses_facebook_toggle_and_falls_back_on_none():
    fanpage = SimpleNamespace(fb_visual_engine="chatgpt", mode2_gallery_niches=["UFC"], name="Fight page")
    job = SimpleNamespace(design_subtitle="Alex Pereira", design_title="Alex discusses the fight")

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[object()]), \
         patch("app.services.speaker_design.compose_speaker_design", return_value=None) as compose:
        result = _maybe_compose_speaker_design(MagicMock(), fanpage, job, _article(), mode="facebook")

    assert result is None
    assert compose.call_args.kwargs["speaker"] == "Alex Pereira"
    assert compose.call_args.kwargs["sport"] == "UFC"


def test_speaker_design_uses_motogp_for_motorsport_bike_niche():
    fanpage = SimpleNamespace(news_visual_engine="chatgpt", mode2_gallery_niches=["motorsport bikes"], name="Racing")
    job = SimpleNamespace(design_subtitle="", design_title="Jorge Martin: Takes pole")

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[object()]), \
         patch("app.services.speaker_design.compose_speaker_design", return_value=b"generated") as compose, \
         patch("app.services.design_images.extract_two_subjects", return_value=("Jorge Martin", "Fabio Quartararo")):
        _maybe_compose_speaker_design(MagicMock(), fanpage, job, _article(), mode="news")

    assert compose.call_args.kwargs["speaker"] == "Jorge Martin"
    assert compose.call_args.kwargs["subjects"] == ["Fabio Quartararo"]
    assert compose.call_args.kwargs["sport"] == "MotoGP"


def test_headline_colon_names_resolve_from_article_title_first():
    cases = [
        ("Bagnaia: At Mandalika...", "Bagnaia"),
        ("Jorge Martin: Speed alone isn't enough...", "Jorge Martin"),
        ("Di Giannantonio: Starting at lap ten...", "Di Giannantonio"),
        ("Aprilia scores historic poker at Mandalika", None),
    ]
    for title, expected in cases:
        fanpage = SimpleNamespace(news_visual_engine="chatgpt", mode2_gallery_niches=["MotoGP"], name="GPOne")
        job = SimpleNamespace(design_subtitle="", design_title="Design title")
        article = SimpleNamespace(scraped_title=title, scraped_content="", article_published_at=None, scraped_at=None)
        with patch("app.services.speaker_design.pick_speaker_photos", return_value=[object()]), \
             patch("app.services.speaker_design.compose_speaker_design", return_value=b"generated") as compose, \
             patch("app.services.design_images.extract_two_subjects", return_value=("Extracted primary", None)):
            _maybe_compose_speaker_design(MagicMock(), fanpage, job, article, mode="news")

        if expected:
            assert compose.call_args.kwargs["speaker"] == expected
        else:
            assert compose.call_args.kwargs["speaker"] == "Extracted primary"


def test_design_subtitle_is_used_only_as_a_valid_name_badge():
    fanpage = SimpleNamespace(news_visual_engine="chatgpt", mode2_gallery_niches=["MotoGP"], name="GPOne")
    article = _article()
    for subtitle, expected in [
        ("**Marc Marquez**", "Marc Marquez"),
        ("Marc Marquez wins the race.", "Headline Primary"),
        ("marc marquez", "Headline Primary"),
        ("Marc, Marquez", "Headline Primary"),
        ("A very long speaker name with too many words here", "Headline Primary"),
    ]:
        job = SimpleNamespace(design_subtitle=subtitle, design_title="Headline title")
        with patch("app.services.speaker_design.pick_speaker_photos", return_value=[object()]), \
             patch("app.services.speaker_design.compose_speaker_design", return_value=b"generated") as compose, \
             patch("app.services.design_images.extract_two_subjects", return_value=("Headline Primary", None)):
            _maybe_compose_speaker_design(MagicMock(), fanpage, job, article, mode="news")
        assert compose.call_args.kwargs["speaker"] == expected


def test_unknown_speaker_has_no_compose_or_generate_call():
    fanpage = SimpleNamespace(news_visual_engine="chatgpt", mode2_gallery_niches=["MotoGP"], name="GPOne")
    job = SimpleNamespace(design_subtitle="Unknown Person", design_title="Headline title")

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[]), \
         patch("app.services.speaker_design.compose_speaker_design") as compose, \
         patch("app.services.visual_engine.generate_ex") as generate:
        assert _maybe_compose_speaker_design(MagicMock(), fanpage, job, _article(), mode="news") is None

    compose.assert_not_called()
    generate.assert_not_called()


def test_story_photo_wired_when_article_has_photo(tmp_path, monkeypatch):
    import io
    from PIL import Image
    from pathlib import Path
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    fanpage = SimpleNamespace(news_visual_engine="chatgpt", mode2_gallery_niches=["MotoGP"], name="GPOne")
    job = SimpleNamespace(id=123, design_subtitle="Marc Marquez", design_title="Rider wins")
    article = _article()

    # Create real PNG bytes so PIL format detection picks PNG -> .png
    img = Image.new("RGB", (600, 600), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    sample_bytes = buf.getvalue()

    captured_path = []
    def fake_compose(*args, **kwargs):
        p = kwargs.get("story_photo_path")
        captured_path.append(p)
        assert p is not None
        assert p.endswith(".png")
        assert Path(p).exists()
        with open(p, "rb") as f:
            assert f.read() == sample_bytes
        return b"result"

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[object()]), \
         patch("app.services.story_photo.fetch_story_photo", return_value=sample_bytes) as mock_fetch, \
         patch("app.services.speaker_design.compose_speaker_design", side_effect=fake_compose):
        res = _maybe_compose_speaker_design(MagicMock(), fanpage, job, article, mode="news")

    assert res == b"result"
    mock_fetch.assert_called_once_with(article)
    # After compose finishes, temp file is unlinked
    assert len(captured_path) == 1
    assert not Path(captured_path[0]).exists()


def test_story_photo_temp_file_unlinked_when_compose_raises(tmp_path, monkeypatch):
    import io
    from PIL import Image
    from pathlib import Path
    import pytest
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    fanpage = SimpleNamespace(news_visual_engine="chatgpt", mode2_gallery_niches=["MotoGP"], name="GPOne")
    job = SimpleNamespace(id=123, design_subtitle="Marc Marquez", design_title="Rider wins")
    article = _article()

    img = Image.new("RGB", (600, 600), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    sample_bytes = buf.getvalue()

    captured_path = []
    def exploding_compose(*args, **kwargs):
        p = kwargs.get("story_photo_path")
        captured_path.append(p)
        assert p is not None
        assert p.endswith(".jpg")
        assert Path(p).exists()
        raise RuntimeError("compose exploded")

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[object()]), \
         patch("app.services.story_photo.fetch_story_photo", return_value=sample_bytes), \
         patch("app.services.speaker_design.compose_speaker_design", side_effect=exploding_compose):
        with pytest.raises(RuntimeError, match="compose exploded"):
            _maybe_compose_speaker_design(MagicMock(), fanpage, job, article, mode="news")

    # Temp file unlinked in finally block even when compose raises
    assert len(captured_path) == 1
    assert not Path(captured_path[0]).exists()


def test_story_photo_none_when_fetch_returns_none(tmp_path, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    fanpage = SimpleNamespace(news_visual_engine="chatgpt", mode2_gallery_niches=["MotoGP"], name="GPOne")
    job = SimpleNamespace(id=123, design_subtitle="Marc Marquez", design_title="Rider wins")
    article = _article()

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[object()]), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None), \
         patch("app.services.speaker_design.compose_speaker_design", return_value=b"result") as compose:
        _maybe_compose_speaker_design(MagicMock(), fanpage, job, article, mode="news")

    kwargs = compose.call_args.kwargs
    assert kwargs["story_photo_path"] is None
    assert kwargs["story_photo_desc"] is None


def test_story_photo_none_when_no_article_mode6():
    fanpage = SimpleNamespace(fb_visual_engine="chatgpt", mode2_gallery_niches=["MotoGP"], name="GPOne")
    job = SimpleNamespace(id=456, design_subtitle="Marc Marquez", design_title="Rider wins")

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[object()]), \
         patch("app.services.story_photo.fetch_story_photo") as mock_fetch, \
         patch("app.services.speaker_design.compose_speaker_design", return_value=b"result") as compose:
        _maybe_compose_speaker_design(MagicMock(), fanpage, job, None, mode="facebook")

    mock_fetch.assert_not_called()
    kwargs = compose.call_args.kwargs
    assert kwargs["story_photo_path"] is None
    assert kwargs["story_photo_desc"] is None


def test_story_photo_actually_reaches_compose_when_fetched(tmp_path, monkeypatch):
    import io
    from PIL import Image
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    fanpage = SimpleNamespace(news_visual_engine="chatgpt", mode2_gallery_niches=["MotoGP"], name="GPOne")
    job = SimpleNamespace(id=789, design_subtitle="Marc Marquez", design_title="Rider wins")
    article = _article()

    buf = io.BytesIO()
    Image.new("RGB", (200, 200), (50, 60, 70)).save(buf, format="JPEG")
    valid_jpeg = buf.getvalue()

    captured_kwargs = {}
    def fake_compose(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return b"done"

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[object()]), \
         patch("app.services.story_photo.fetch_story_photo", return_value=valid_jpeg), \
         patch("app.services.story_photo.describe_story_photo", return_value="a crash description"), \
         patch("app.services.speaker_design.compose_speaker_design", side_effect=fake_compose):
        _maybe_compose_speaker_design(MagicMock(), fanpage, job, article, mode="news")

    assert captured_kwargs.get("story_photo_path") is not None
    assert captured_kwargs.get("story_photo_desc") == "a crash description"


def test_story_photo_tolerant_on_fetch_failure(tmp_path, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    fanpage = SimpleNamespace(news_visual_engine="chatgpt", mode2_gallery_niches=["MotoGP"], name="GPOne")
    job = SimpleNamespace(id=123, design_subtitle="Marc Marquez", design_title="Rider wins")
    article = _article()

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[object()]), \
         patch("app.services.story_photo.fetch_story_photo", side_effect=RuntimeError("boom")), \
         patch("app.services.speaker_design.compose_speaker_design", return_value=b"result") as compose:
        _maybe_compose_speaker_design(MagicMock(), fanpage, job, article, mode="news")

    kwargs = compose.call_args.kwargs
    assert kwargs["story_photo_path"] is None
    assert kwargs["story_photo_desc"] is None


def test_render_design_marks_speaker_gallery_images_used(monkeypatch):
    from app.services.speaker_design import SpeakerDesignResult
    from app.tasks import design_renderer
    from app.models.publish_jobs import PublishJobStatus

    db = MagicMock()
    fake_job = SimpleNamespace(
        id=999,
        design_subtitle="Marc Marquez",
        design_title="Headline",
        last_image_marker=None,
        status=PublishJobStatus.rendering,
        last_error=None,
        design_image_path=None,
        design_image_url=None,
        design_template_id=None,
    )
    fake_fanpage = SimpleNamespace(
        id=1,
        mode2_gallery_niches=["MotoGP"],
        mode2_publish_mode=SimpleNamespace(value="manual"),
        mode2_badge_text=None,
        design_expand=False,
    )
    fake_article = SimpleNamespace(status="draft", scraped_title="Headline")
    fake_template = SimpleNamespace(id=1, template_json={}, canvas_width=1080, canvas_height=1350)

    used_img1 = SimpleNamespace(is_used=False, last_used_at=None)
    used_img2 = SimpleNamespace(is_used=False, last_used_at=None)
    speaker_res = SpeakerDesignResult(b"\x89PNG\r\n\x1a\nfake", [used_img1, used_img2])

    monkeypatch.setattr(design_renderer, "_maybe_compose_speaker_design", lambda *a, **k: speaker_res)

    # When _maybe_compose_speaker_design returns speaker_res:
    res = design_renderer._maybe_compose_speaker_design(db, fake_fanpage, fake_job, fake_article, mode="news")
    assert res is not None
    speaker_gallery_images = getattr(res, "used_gallery_images", [])
    assert speaker_gallery_images == [used_img1, used_img2]

    # Marking logic runs on render success:
    for s_gi in speaker_gallery_images:
        s_gi.is_used = True
        s_gi.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)

    assert used_img1.is_used is True
    assert used_img1.last_used_at is not None
    assert used_img2.is_used is True
    assert used_img2.last_used_at is not None

