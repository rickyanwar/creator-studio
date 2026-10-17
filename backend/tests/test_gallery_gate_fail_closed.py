import io
from unittest.mock import MagicMock, patch

from PIL import Image
import pytest

from app.services.design_images import classify_and_gate_image
from app.services.image_downloader import _fetch_and_store


@pytest.fixture
def sample_jpeg_bytes() -> bytes:
    img = Image.new("RGB", (200, 200), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_classify_and_gate_not_json_fails_closed(sample_jpeg_bytes):
    with patch("app.services.design_images._vision_chat", return_value="not json at all"):
        label, usable = classify_and_gate_image(sample_jpeg_bytes)
        assert label == "other"
        assert usable is False


def test_classify_and_gate_missing_usable_key_fails_closed(sample_jpeg_bytes):
    with patch("app.services.design_images._vision_chat", return_value='{"label": "FACE"}'):
        label, usable = classify_and_gate_image(sample_jpeg_bytes)
        assert label == "face"
        assert usable is False


def test_classify_and_gate_usable_true_accepted(sample_jpeg_bytes):
    with patch("app.services.design_images._vision_chat", return_value='{"label": "ACTION", "usable": true}'):
        label, usable = classify_and_gate_image(sample_jpeg_bytes)
        assert label == "action"
        assert usable is True


def test_classify_and_gate_exception_fails_closed(sample_jpeg_bytes):
    with patch("app.services.design_images._vision_chat", side_effect=RuntimeError("vision network down")):
        label, usable = classify_and_gate_image(sample_jpeg_bytes)
        assert label == "other"
        assert usable is False


def test_classify_and_gate_allow_low_quality_content_acceptable(sample_jpeg_bytes):
    reply = '{"label":"FACE","content_acceptable":true,"quality_usable":false}'
    with patch("app.services.design_images._vision_chat", return_value=reply):
        label, usable = classify_and_gate_image(sample_jpeg_bytes, allow_low_quality=True)
        assert label == "face"
        assert usable is True


def test_fetch_and_store_caller_fails_closed_when_gate_raises(tmp_path, sample_jpeg_bytes):
    with patch("app.services.design_images.classify_and_gate_image", side_effect=RuntimeError("flaky vision")):
        saved = _fetch_and_store(
            urls=["http://example.com/photo1.jpg"],
            dest_dir=tmp_path,
            max_num=5,
            min_size=(100, 100),
            skip_urls=set(),
            engine="test_engine",
            fetcher=lambda url: sample_jpeg_bytes,
        )
        assert saved == []
        assert list(tmp_path.glob("*.jpg")) == []
