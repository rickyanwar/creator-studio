import io
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from PIL import Image
import pytest

from app.models.gallery import GalleryImage
from app.services.design_images import classify_and_gate_image
from app.services.gallery_labeler import gate_and_label_image
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
    with patch("app.services.gallery_labeler.gate_and_label_image", side_effect=RuntimeError("flaky vision")):
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


def test_gate_and_label_image_good_json(sample_jpeg_bytes):
    data = {
        "label": "FACE",
        "usable": True,
        "sport": "motorsport",
        "venue": {"type": "circuit", "visible": True},
        "shot_type": "portrait",
        "people_count": 1,
        "activity": "interview",
        "expression": "smiling",
        "context": {"session": "race", "rain": False, "night": False, "circuit_visible": True},
        "livery_colors": ["red"],
        "kit_colors": ["red"],
        "quality": {"blurry": False, "obstructed": False},
        "caption": "Charles Leclerc smiling after victory",
        "people": [{"name": "Charles Leclerc", "confidence": 0.9, "source": "visual"}],
        "keyword_mismatch": False,
        "keyword_person_visible": "yes",
    }
    with patch("app.services.design_images._vision_chat", return_value=json.dumps(data)):
        label, usable, labels = gate_and_label_image(
            sample_jpeg_bytes, subject="charles leclerc", keyword="charles leclerc"
        )
        assert label == "face"
        assert usable is True
        assert isinstance(labels, dict)
        assert labels["sport"] == "motorsport"
        assert labels["shot_type"] == "portrait"
        assert labels["people"][0]["name"] == "Charles Leclerc"


def test_gate_and_label_image_garbage_fails_closed(sample_jpeg_bytes):
    with patch("app.services.design_images._vision_chat", return_value="completely unparseable garbage"):
        label, usable, labels = gate_and_label_image(
            sample_jpeg_bytes, subject="charles leclerc", keyword="charles leclerc"
        )
        assert label is None
        assert usable is False
        assert labels is None


def test_gate_and_label_image_gate_ok_label_invalid_keeps_gate(sample_jpeg_bytes):
    data = {
        "label": "ACTION",
        "usable": True,
        "keyword_mismatch": "not_a_boolean",
    }
    with patch("app.services.design_images._vision_chat", return_value=json.dumps(data)):
        label, usable, labels = gate_and_label_image(
            sample_jpeg_bytes, subject="max verstappen", keyword="max verstappen"
        )
        assert label == "action"
        assert usable is True
        assert labels is None


def test_gate_and_label_image_allow_low_quality(sample_jpeg_bytes):
    data_acceptable = {
        "label": "FACE",
        "content_acceptable": True,
        "quality_usable": False,
        "sport": "motorsport",
    }
    with patch("app.services.design_images._vision_chat", return_value=json.dumps(data_acceptable)):
        label, usable, labels = gate_and_label_image(
            sample_jpeg_bytes, subject="marc marquez", keyword="marc marquez", allow_low_quality=True
        )
        assert label == "face"
        assert usable is True
        assert labels["sport"] == "motorsport"

    data_unacceptable = {
        "label": "FACE",
        "content_acceptable": False,
        "quality_usable": False,
        "sport": "motorsport",
    }
    with patch("app.services.design_images._vision_chat", return_value=json.dumps(data_unacceptable)):
        label, usable, labels = gate_and_label_image(
            sample_jpeg_bytes, subject="marc marquez", keyword="marc marquez", allow_low_quality=True
        )
        assert label == "face"
        assert usable is False


def test_downloader_stores_attributes_and_labeled_at(tmp_path, sample_jpeg_bytes):
    mock_labels = {
        "sport": "f1",
        "venue": {"type": "circuit", "visible": True},
        "shot_type": "portrait",
        "people_count": 1,
        "activity": "interview",
        "expression": "smiling",
        "context": {"session": "race", "rain": False, "night": False, "circuit_visible": True},
        "livery_colors": ["red"],
        "kit_colors": ["red"],
        "quality": {"blurry": False, "obstructed": False},
        "caption": "test caption",
        "people": [],
        "keyword_mismatch": False,
        "keyword_person_visible": "unsure",
    }
    with patch("app.services.gallery_labeler.gate_and_label_image", return_value=("face", True, mock_labels)):
        saved = _fetch_and_store(
            urls=["http://example.com/photo1.jpg"],
            dest_dir=tmp_path,
            max_num=1,
            min_size=(100, 100),
            skip_urls=set(),
            engine="test_engine",
            subject="test subject",
            fetcher=lambda url: sample_jpeg_bytes,
        )
        assert len(saved) == 1
        item = saved[0]
        assert item.label == "face"
        assert item.attributes == mock_labels

    from app.services.muse_labeler import merge_label_attributes
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    gi = GalleryImage(
        keyword="test subject",
        source_image_url=item.source_url,
        local_path=item.local_path,
        public_url="http://storage/test.jpg",
        width=item.width,
        height=item.height,
        source_engine=item.engine,
        label=item.label,
        attributes=merge_label_attributes(None, item.attributes) if item.attributes else None,
        labeled_at=now_utc if item.attributes else None,
        label_error=None if item.attributes else None,
    )
    assert gi.label == "face"
    assert gi.attributes["sport"] == "f1"
    assert gi.labeled_at == now_utc
    assert gi.label_error is None
