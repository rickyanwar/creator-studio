from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.vision_check_cache import VisionCheckCache
from app.services import design_images, vision_cache
from app.services.pro_poster.photos import check_photo
from app.services.pro_poster.types import (
    EventContext,
    PhotoCandidate,
    PhotoCheck,
    PosterBrief,
    Subject,
)


@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VisionCheckCache.__table__.create(engine, checkfirst=True)
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(vision_cache, "SessionLocal", testing_session)
    yield


@pytest.fixture
def make_brief():
    def _brief(hero_name="Max Verstappen", series="Formula 1", circuit="Monza", year=2026):
        return PosterBrief(
            job_id=1,
            subjects=(Subject(name=hero_name, role="hero"),),
            event=EventContext(series=series, circuit=circuit, year=year),
            story_type="news",
            mood="dramatic",
            badge_label="F1 NEWS",
        )

    return _brief


@pytest.fixture
def make_candidate(tmp_path: Path):
    def _cand(filename="photo.jpg", content: bytes = b"fake-photo-bytes"):
        p = tmp_path / filename
        p.write_bytes(content)
        return PhotoCandidate(
            source="gallery",
            gallery_image_id=1,
            path_or_url=str(p),
            role="hero",
        )

    return _cand


def test_verify_subject_cache_hit_and_miss():
    image_bytes = b"sample-image-bytes-1"
    with patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,xyz"), \
         patch("app.services.design_images._vision_chat") as mock_chat:
        mock_chat.return_value = '{"match": true, "confidence": 0.95}'

        # 1. First call -> model called, result cached
        res1 = design_images.vision_verify_subject(image_bytes, "Max Verstappen", "Formula 1")
        assert res1["match"] is True
        assert res1["confidence"] == 0.95
        assert mock_chat.call_count == 1

        # 2. Second call with same bytes + subject + niche -> cache hit, no model call
        res2 = design_images.vision_verify_subject(image_bytes, "Max Verstappen", "Formula 1")
        assert res2["match"] is True
        assert res2["confidence"] == 0.95
        assert mock_chat.call_count == 1

        # 3. Different subject -> miss, calls model
        mock_chat.return_value = '{"match": false, "confidence": 0.90}'
        res3 = design_images.vision_verify_subject(image_bytes, "Lewis Hamilton", "Formula 1")
        assert res3["match"] is False
        assert mock_chat.call_count == 2


def test_verify_subject_confident_negative_cached():
    image_bytes = b"sample-image-bytes-negative"
    with patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,xyz"), \
         patch("app.services.design_images._vision_chat") as mock_chat:
        mock_chat.return_value = '{"match": false, "confidence": 0.85}'

        res1 = design_images.vision_verify_subject(image_bytes, "Charles Leclerc", "Formula 1")
        assert res1["match"] is False
        assert res1["confidence"] == 0.85
        assert mock_chat.call_count == 1

        # Second call returns cached confident negative
        res2 = design_images.vision_verify_subject(image_bytes, "Charles Leclerc", "Formula 1")
        assert res2["match"] is False
        assert mock_chat.call_count == 1


def test_verify_subject_unsure_or_error_not_cached():
    image_bytes = b"sample-image-bytes-unsure"
    with patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,xyz"), \
         patch("app.services.design_images._vision_chat") as mock_chat:
        # Low confidence (< 0.7 floor)
        mock_chat.return_value = '{"match": true, "confidence": 0.40}'
        res1 = design_images.vision_verify_subject(image_bytes, "Lando Norris", "Formula 1")
        assert res1["confidence"] == 0.40
        assert mock_chat.call_count == 1

        # Second call must call model again because low confidence is NOT cached
        mock_chat.return_value = '{"match": true, "confidence": 0.85}'
        res2 = design_images.vision_verify_subject(image_bytes, "Lando Norris", "Formula 1")
        assert res2["confidence"] == 0.85
        assert mock_chat.call_count == 2

        # Error result from exception is NOT cached
        mock_chat.side_effect = RuntimeError("network error")
        res3 = design_images.vision_verify_subject(b"another-image", "Lando Norris", "Formula 1")
        assert res3["match"] is False
        assert res3["confidence"] == 0.0

        # Next call tries again
        mock_chat.side_effect = None
        mock_chat.return_value = '{"match": true, "confidence": 0.90}'
        res4 = design_images.vision_verify_subject(b"another-image", "Lando Norris", "Formula 1")
        assert res4["match"] is True


def test_verify_subject_cache_db_failure_fallback():
    image_bytes = b"sample-image-bytes-db-fail"
    with patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,xyz"), \
         patch("app.services.design_images._vision_chat") as mock_chat, \
         patch("app.services.vision_cache.get_cached", side_effect=Exception("DB pool exhausted")):
        mock_chat.return_value = '{"match": true, "confidence": 0.90}'
        res = design_images.vision_verify_subject(image_bytes, "George Russell", "Formula 1")
        assert res["match"] is True
        assert mock_chat.call_count == 1


def test_photo_check_cache_hit_and_rebuild(make_brief, make_candidate):
    brief = make_brief()
    cand = make_candidate()

    with patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,xyz"), \
         patch("app.services.design_images._vision_chat") as mock_chat:
        mock_chat.return_value = (
            '{"person": "yes", "event_match": "yes", "obstructed": false, '
            '"watermark": false, "quality": 4, "face_frontal": true, '
            '"shows": "Max smiling", "story_fit": 5, "notes": "clean"}'
        )

        # 1. First call -> model called, result cached
        check1 = check_photo(cand, brief, story="Grand prix victory")
        assert check1.person == "yes"
        assert check1.event_match == "yes"
        assert check1.quality == 4
        assert check1.candidate is cand
        assert mock_chat.call_count == 1

        # 2. Second call with same candidate + brief + story -> hit
        check2 = check_photo(cand, brief, story="Grand prix victory")
        assert check2.person == "yes"
        assert check2.event_match == "yes"
        assert check2.quality == 4
        assert check2.shows == "Max smiling"
        assert check2.candidate is cand
        assert mock_chat.call_count == 1  # No second vision call

        # 3. Different story -> miss
        check3 = check_photo(cand, brief, story="Qualifying crash")
        assert mock_chat.call_count == 2


def test_photo_check_unsure_or_error_not_cached(make_brief, make_candidate):
    brief = make_brief()
    cand = make_candidate()

    with patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,xyz"), \
         patch("app.services.design_images._vision_chat") as mock_chat:
        # person is unsure
        mock_chat.return_value = (
            '{"person": "unsure", "event_match": "yes", "obstructed": false, '
            '"watermark": false, "quality": 3, "face_frontal": false, '
            '"shows": "helmeted driver", "story_fit": 2, "notes": "helmet obscures"}'
        )
        check1 = check_photo(cand, brief)
        assert check1.person == "unsure"
        assert mock_chat.call_count == 1

        # Second call must call model again because unsure is not cached
        mock_chat.return_value = (
            '{"person": "yes", "event_match": "yes", "obstructed": false, '
            '"watermark": false, "quality": 4, "face_frontal": true, '
            '"shows": "face visible", "story_fit": 4, "notes": "confirmed"}'
        )
        check2 = check_photo(cand, brief)
        assert check2.person == "yes"
        assert mock_chat.call_count == 2

        # person='error' due to malformed vision response
        mock_chat.return_value = "invalid-not-json"
        check3 = check_photo(cand, brief, story="some-story")
        assert check3.person == "error"

        # Not cached -> next call calls model again
        mock_chat.return_value = (
            '{"person": "no", "event_match": "yes", "obstructed": false, '
            '"watermark": false, "quality": 3, "face_frontal": false, '
            '"shows": "different driver", "story_fit": 1, "notes": "wrong person"}'
        )
        check4 = check_photo(cand, brief, story="some-story")
        assert check4.person == "no"


def test_photo_check_db_failure_fallback(make_brief, make_candidate):
    brief = make_brief()
    cand = make_candidate()

    with patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,xyz"), \
         patch("app.services.design_images._vision_chat") as mock_chat, \
         patch("app.services.vision_cache.get_cached", side_effect=Exception("DB down")):
        mock_chat.return_value = (
            '{"person": "yes", "event_match": "yes", "obstructed": false, '
            '"watermark": false, "quality": 5, "face_frontal": true, '
            '"shows": "Max celebrating", "story_fit": 5, "notes": "great"}'
        )
        check = check_photo(cand, brief)
        assert check.person == "yes"
        assert mock_chat.call_count == 1


def test_vision_cache_ttl_expiration():
    digest = vision_cache.image_digest(b"ttl-test-image")
    key = "ttl-key"
    kind = "verify_subject"
    result = {"match": True, "confidence": 0.99}

    vision_cache.put_cached(kind, digest, key, result)
    assert vision_cache.get_cached(kind, digest, key) == result

    # Simulate entry older than 90 days
    db = vision_cache.SessionLocal()
    row = db.query(VisionCheckCache).filter_by(kind=kind, image_sha256=digest, key=key).first()
    row.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=91)
    db.commit()
    db.close()

    # Entry counts as a miss
    assert vision_cache.get_cached(kind, digest, key) is None


def test_vision_cache_hit_logs(caplog):
    digest = vision_cache.image_digest(b"log-test-image")
    key = "log-key"
    kind = "verify_subject"
    result = {"match": True, "confidence": 0.92}

    vision_cache.put_cached(kind, digest, key, result)
    with caplog.at_level(logging.INFO):
        cached = vision_cache.get_cached(kind, digest, key)
        assert cached == result
        assert "vision cache hit: verify_subject" in caplog.text
