import hashlib
import json
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from app.services import design_images, vision_cache
from app.services.pro_poster.photos import check_photo
from app.services.pro_poster.types import EventContext, PhotoCandidate, PosterBrief, Subject


@pytest.fixture
def photo_check_setup(tmp_path, monkeypatch):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"photo bytes")
    candidate = PhotoCandidate(source="gallery", gallery_image_id=1, path_or_url=str(path), role="hero")
    brief = PosterBrief(
        job_id=1, subjects=(Subject(name="Max Verstappen", role="hero"),),
        event=EventContext(series="Formula 1", vehicle="car"),
        story_type="result", mood="dramatic", badge_label="F1 NEWS", must_avoid=(),
    )
    entries = {}
    monkeypatch.setattr(vision_cache, "get_cached", lambda *key: entries.get(key))
    put = MagicMock(side_effect=lambda *args: entries.__setitem__(args[:3], args[3]))
    monkeypatch.setattr(vision_cache, "put_cached", put)
    monkeypatch.setattr(design_images, "_vision_datauri", lambda _: "data:image/jpeg;base64,abc")
    vision = MagicMock()
    monkeypatch.setattr(design_images, "_vision_chat", vision)
    return candidate, brief, entries, put, vision


@pytest.mark.parametrize(
    "person,event_match,watermark,role,reason,reused",
    [
        ("no", "yes", False, "hero", "person", True),
        ("no", "yes", False, "context", "person", False),
        ("yes", "no", False, "context", "event", True),
        ("yes", "yes", True, "context", "watermark", True),
        ("no", "no", True, "hero", "event", True),
        ("no", "unsure", False, "hero", "person", True),
    ],
)
def test_reject_cache_across_stories(photo_check_setup, person, event_match, watermark, role, reason, reused):
    candidate, brief, entries, put, vision = photo_check_setup
    vision.return_value = json.dumps({
        "person": person, "event_match": event_match, "watermark": watermark,
        "obstructed": False, "quality": 4, "face_frontal": True,
        "shows": "A driver", "story_fit": 5,
    })
    check_photo(candidate, brief, story="First story")
    rejects = [(key, value) for key, value in entries.items() if key[0] == "photo_reject"]
    assert len(rejects) == 1
    key, value = rejects[0]
    assert value["reason"] == reason
    expected_event = {
        "circuit": "unknown", "night": "unknown", "rain": "unknown", "series": "Formula 1",
        "session": "unknown", "vehicle": "car", "year": "unknown",
    }
    assert key[2] == hashlib.sha256(json.dumps(
        {"subject": "Max Verstappen", "event": expected_event}, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    vision.reset_mock()
    result = check_photo(replace(candidate, role=role, caption="Different caption"), brief, story="Second story")
    assert vision.call_count == (0 if reused else 1)
    if reused:
        assert result.notes == "cached reject: " + reason
        assert result.story_fit == 0
        assert result.shows == "A driver"


@pytest.mark.parametrize("person,event_match", [("yes", "yes"), ("unsure", "unsure"), ("error", "yes")])
def test_non_reject_does_not_store_reject(photo_check_setup, person, event_match):
    candidate, brief, entries, put, vision = photo_check_setup
    vision.return_value = json.dumps({
        "person": person, "event_match": event_match, "watermark": False,
        "obstructed": False, "quality": 4,
    })
    check_photo(candidate, brief)
    assert not any(key[0] == "photo_reject" for key in entries)
    if person == "error":
        put.assert_not_called()
