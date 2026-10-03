"""Run from backend with PYTHONPATH=. ./venv/bin/python scripts/check_pinterest_low_quality.py."""

import tempfile
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import IntegrityError
from PIL import Image

from app.models.gallery import GalleryImage  # Load models before mocking settings.
from app.models.pinterest_content_ideas import PinterestContentIdea
from app.schemas.fanpage import FanpageBase
from app.services import design_images, image_downloader, pinterest_source


def check_gate():
    assert FanpageBase.model_fields["pinterest_allow_low_quality"].default is False
    prompts = []

    def vision(content, **kwargs):
        prompts.append(content[0]["text"])
        if '"content_acceptable"' in content[0]["text"]:
            return '{"label":"FACE","content_acceptable":false,"quality_usable":true}'
        return '{"label":"FACE","usable":false}'

    with patch.object(design_images, "_vision_chat", side_effect=vision), patch.object(
        design_images, "_vision_datauri", return_value="data:image/jpeg;base64,AA=="
    ):
        assert design_images.classify_and_gate_image(b"image")[1] is False
        assert design_images.classify_and_gate_image(b"image", allow_low_quality=True)[1] is False
    assert "subject is tiny" in prompts[0]
    assert "Ignore resolution, blur, and cropping for this field" in prompts[1]
    assert "subject is tiny" in prompts[1]
    assert "generic crowd/stage/logo-only" in prompts[1]
    assert '"content_acceptable"' in prompts[1]
    with patch.object(design_images, "_vision_chat", return_value=(
        '{"label":"FACE","content_acceptable":true,"quality_usable":false}'
    )), patch.object(design_images, "_vision_datauri", return_value="data:image/jpeg;base64,AA=="):
        assert design_images.classify_and_gate_image(b"image", allow_low_quality=True) == ("face", True)
    with patch.object(design_images, "_vision_datauri", return_value="data:image/jpeg;base64,AA=="):
        for output in ('{"label":"FACE","usable":true}',
                       '{"label":"FACE","content_acceptable":"true"}',
                       '{"label":"FACE","content_acceptable":null}',
                       '{"label":"FACE",', 'not JSON'):
            with patch.object(design_images, "_vision_chat", return_value=output):
                assert design_images.classify_and_gate_image(b"image", allow_low_quality=True)[1] is False
        with patch.object(design_images, "_vision_chat", side_effect=ConnectionError("offline")):
            assert design_images.classify_and_gate_image(b"image", allow_low_quality=True)[1] is True

    image = io.BytesIO()
    Image.new("RGB", (20, 20)).save(image, format="JPEG")
    response = MagicMock(content=image.getvalue())
    with tempfile.TemporaryDirectory() as folder, patch.object(
        image_downloader.httpx, "get", return_value=response
    ), patch.object(image_downloader, "get_settings", return_value=SimpleNamespace(
        hq_upscale_enabled=False, gallery_upscale_enabled=False,
    )), patch.object(design_images, "_vision_chat", return_value=(
        '{"label":"OTHER","content_acceptable":false,"quality_usable":true}'
    )), patch.object(design_images, "_vision_datauri", return_value="data:image/jpeg;base64,AA=="):
        assert image_downloader._fetch_and_store(
            ["https://example.com/unusable.jpg"], folder, 1, (400, 400), set(),
            "pinterest", allow_low_quality=True,
        ) == []
        assert not list(Path(folder).iterdir())


def check_candidate():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "photo.jpg"
        candidate = SimpleNamespace(image_url="https://example.com/photo.jpg", description="caption")
        fanpage = SimpleNamespace(
            pinterest_allow_low_quality=True, mode2_gallery_niches=[], name="sports",
            pinterest_custom_prompt="", id=1,
        )
        item = SimpleNamespace(local_path=str(path), source_url=candidate.image_url,
                               filename=path.name, width=100, height=100, label="face")
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        settings = SimpleNamespace(storage_base_path=folder, storage_base_url="https://example.com")
        with patch("app.config.get_settings", return_value=settings), patch(
            "app.services.image_downloader._fetch_and_store", return_value=[item]
        ) as fetch:
            # Functions are imported into build_idea_from_candidate, not module globals.
            with patch.object(design_images, "vision_has_watermark", return_value=True), patch.object(
                design_images, "_is_low_quality_photo", side_effect=AssertionError("quality check called")
            ), patch.object(design_images, "vision_check_photo_quality", side_effect=AssertionError("quality check called")):
                path.write_bytes(b"image")
                assert pinterest_source.build_idea_from_candidate(db, fanpage, candidate, "curated") is None
                assert not path.exists()
                db.add.assert_not_called()
                db.commit.assert_not_called()
                assert fetch.call_args.kwargs["allow_low_quality"] is True

            with patch.object(design_images, "vision_has_watermark", return_value=False), patch.object(
                design_images, "_is_low_quality_photo", side_effect=AssertionError("quality check called")
            ), patch.object(design_images, "vision_check_photo_quality", side_effect=AssertionError("quality check called")), patch.object(
                design_images, "vision_check_pin_description",
                return_value={"valid": False, "title": "", "description": ""},
            ):
                path.write_bytes(b"image")
                assert pinterest_source.build_idea_from_candidate(db, fanpage, candidate, "curated") is None
                db.add.assert_not_called()

            with patch.object(design_images, "vision_has_watermark", return_value=False), patch.object(
                design_images, "_is_low_quality_photo", return_value=False
            ), patch.object(design_images, "vision_check_photo_quality", return_value=True), patch.object(
                design_images, "vision_check_pin_description",
                return_value={"valid": True, "title": "Sports", "description": "valid caption"},
            ):
                path.write_bytes(b"image")
                db.flush.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
                assert pinterest_source.build_idea_from_candidate(db, fanpage, candidate, "curated") is None
                db.rollback.assert_called_once()
                db.commit.assert_not_called()
                assert not path.exists()

            db.reset_mock()
            with patch.object(design_images, "vision_has_watermark", return_value=False), patch.object(
                design_images, "_is_low_quality_photo", return_value=False
            ), patch.object(design_images, "vision_check_photo_quality", return_value=True), patch.object(
                design_images, "vision_check_pin_description",
                return_value={"valid": True, "title": "Sports", "description": "valid caption"},
            ):
                path.write_bytes(b"image")
                events = []

                def assign_id():
                    assert isinstance(db.add.call_args.args[0], GalleryImage)
                    db.add.call_args.args[0].id = 42
                    events.append("flush")

                db.flush.side_effect = assign_id
                db.commit.side_effect = lambda: events.append("commit")
                idea = pinterest_source.build_idea_from_candidate(db, fanpage, candidate, "curated")
                assert isinstance(idea, PinterestContentIdea)
                assert idea.gallery_image_id == 42
                assert db.add.call_args.args[0] is idea
                assert db.add.call_count == 2
                assert events == ["flush", "commit"]
                db.rollback.assert_not_called()

                db.reset_mock()
                path.write_bytes(b"image")
                db.flush.side_effect = assign_id
                db.commit.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
                assert pinterest_source.build_idea_from_candidate(db, fanpage, candidate, "curated") is None
                db.rollback.assert_called_once()
                assert not path.exists()

            db.reset_mock()
            fanpage.pinterest_allow_low_quality = False
            with patch.object(design_images, "_is_low_quality_photo", return_value=True), patch.object(
                design_images, "vision_has_watermark", side_effect=AssertionError("watermark called")
            ):
                path.write_bytes(b"image")
                assert pinterest_source.build_idea_from_candidate(db, fanpage, candidate, "curated") is None
                db.add.assert_not_called()
                assert fetch.call_args.kwargs["allow_low_quality"] is False


if __name__ == "__main__":
    check_gate()
    check_candidate()
    print("Pinterest low-quality checks passed")
