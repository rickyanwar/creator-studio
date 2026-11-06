import base64
from unittest.mock import MagicMock

import pytest

from app.services import design_images as di, speaker_design, vision_cache


@pytest.fixture
def cache(monkeypatch):
    values = {}
    get = MagicMock(side_effect=lambda kind, digest, key: values.get((kind, digest, key)))
    put = MagicMock(side_effect=lambda kind, digest, key, result: values.__setitem__((kind, digest, key), result))
    monkeypatch.setattr(vision_cache, "get_cached", get)
    monkeypatch.setattr(vision_cache, "put_cached", put)
    chat = MagicMock()
    monkeypatch.setattr(di, "_vision_chat", chat)
    datauri = MagicMock(return_value="data:image/jpeg;base64,eA==")
    monkeypatch.setattr(di, "_vision_datauri", datauri)
    return values, get, put, chat, datauri


def test_watermark_cache_reuses_parsed_result(cache):
    values, get, put, chat, datauri = cache
    chat.return_value = '{"has_watermark": false}'
    assert di.vision_has_watermark(b"photo") is False
    assert di.vision_has_watermark(b"photo") is False
    chat.assert_called_once()
    put.assert_called_once_with("watermark", vision_cache.image_digest(b"photo"), "v1", {"has_watermark": False})
    datauri.assert_called_once_with(b"photo", max_dim=512)


@pytest.mark.parametrize("reply", ["invalid", "{}", '{"has_watermark": "false"}'])
def test_watermark_unparseable_not_cached(cache, reply):
    values, get, put, chat, datauri = cache
    chat.return_value = reply
    assert di.vision_has_watermark(b"photo") is True
    put.assert_not_called()


def test_watermark_error_not_cached(cache):
    values, get, put, chat, datauri = cache
    chat.side_effect = RuntimeError("offline")
    assert di.vision_has_watermark(b"photo") is True
    put.assert_not_called()


def test_focus_cache_keeps_raw_coordinates_and_reclamps(cache):
    values, get, put, chat, datauri = cache
    chat.return_value = '{"x": 0.5, "y": 0.95}'
    assert di.vision_focus_point(b"photo", safe_cy_ceiling=0.7) == [0.5, 0.7]
    assert di.vision_focus_point(b"photo", safe_cy_ceiling=0.6) == [0.5, 0.6]
    chat.assert_called_once()
    put.assert_called_once_with("focus_point", vision_cache.image_digest(b"photo"), "split=False", {"x": 0.5, "y": 0.95})


def test_focus_split_has_separate_cache(cache):
    values, get, put, chat, datauri = cache
    chat.return_value = '{"x": 0.1, "y": 0.95}'
    assert di.vision_focus_point(b"photo") == [0.15, 0.7]
    assert di.vision_focus_point(b"photo", for_split=True) == [0.15, 0.75]
    assert chat.call_count == 2


def test_focus_out_of_range_not_cached(cache):
    values, get, put, chat, datauri = cache
    chat.return_value = '{"x": 2, "y": 0.5}'
    assert di.vision_focus_point(b"photo") is None
    put.assert_not_called()


@pytest.mark.parametrize("answer", ["yes", "no"])
def test_speaker_definite_answer_cached(cache, answer):
    values, get, put, chat, datauri = cache
    chat.return_value = answer
    assert speaker_design.verify_speaker_photo(b"photo", " Rider ") == answer
    assert speaker_design.verify_speaker_photo(b"photo", "rider") == answer
    chat.assert_called_once()
    put.assert_called_once_with("speaker_check", vision_cache.image_digest(b"photo"), "rider", {"answer": answer})
    datauri.assert_called_once_with(b"photo")


def test_speaker_unsure_not_cached(cache):
    values, get, put, chat, datauri = cache
    chat.return_value = "unsure"
    assert speaker_design.verify_speaker_photo(b"photo", "Rider") == "unsure"
    put.assert_not_called()


@pytest.mark.parametrize("kind", ["watermark", "focus", "speaker"])
def test_cache_errors_preserve_vision_result(cache, kind):
    values, get, put, chat, datauri = cache
    get.side_effect = RuntimeError("cache offline")
    put.side_effect = RuntimeError("cache offline")
    if kind == "watermark":
        chat.return_value = '{"has_watermark": false}'
        assert di.vision_has_watermark(b"photo") is False
    elif kind == "focus":
        chat.return_value = '{"x": 0.5, "y": 0.5}'
        assert di.vision_focus_point(b"photo") == [0.5, 0.5]
    else:
        chat.return_value = "yes"
        assert speaker_design.verify_speaker_photo(b"photo", "Rider") == "yes"


def test_subject_side_uses_shared_transport(cache):
    values, get, put, chat, datauri = cache
    chat.return_value = "LEFT"
    uri = "data:image/jpeg;base64," + base64.b64encode(b"photo").decode()
    assert di.analyze_subject_side(uri) == "left"
    assert chat.call_args.kwargs == {"max_tokens": 1500, "context": "vision_subject_side"}
    datauri.assert_called_once_with(b"photo", max_dim=512)
