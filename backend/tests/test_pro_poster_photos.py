"""Tests for pro poster photo gathering, vision verification, and selection."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


from app.models.gallery import GalleryImage
from app.services.pro_poster.photos import (
    _matches_person,
    _shows_has_surname,
    _similar,
    check_photo,
    gallery_caption,
    gather_candidates,
    select_photos,
)
from app.services.pro_poster.types import (
    EventContext,
    PhotoCandidate,
    PhotoCheck,
    PhotoSelection,
    PosterBrief,
    Subject,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    GalleryImage.__table__.create(engine, checkfirst=True)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def make_photo(tmp_path: Path):
    def _create(name: str = "photo.jpg", content: bytes = b"dummy-jpg-bytes") -> str:
        p = tmp_path / name
        p.write_bytes(content)
        return str(p)

    return _create


@pytest.fixture
def sample_brief() -> PosterBrief:
    return PosterBrief(
        job_id=101,
        subjects=(
            Subject(name="Max Verstappen", role="hero", team="Red Bull Racing"),
        ),
        event=EventContext(
            series="Formula 1",
            vehicle="Formula 1 car",
            circuit="Monza",
            session="race",
            year=2026,
            night=False,
            rain=False,
        ),
        story_type="result",
        mood="dramatic",
        badge_label="F1 NEWS",
        must_avoid=("MotoGP bikes", "Singapore night race"),
    )


# ---------------------------------------------------------------------------
# Test 1: wrong person -> rejected
# ---------------------------------------------------------------------------

def test_wrong_person_rejected(make_photo, sample_brief):
    photo_path = make_photo("wrong_person.jpg")
    cand = PhotoCandidate(source="gallery", gallery_image_id=1, path_or_url=photo_path, role="hero")

    with patch("app.services.design_images._vision_chat") as mock_vision, \
         patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        mock_vision.return_value = json.dumps({
            "person": "no",
            "event_match": "no",
            "obstructed": False,
            "watermark": False,
            "quality": 4,
            "notes": "Shows Christian Horner, not Max Verstappen",
        })

        check = check_photo(cand, sample_brief)
        assert check.person == "no"
        assert check.usable_as_hero is False
        assert check.usable_as_context is False

        with patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand]):
            sel = select_photos(None, sample_brief)
            assert sel.hero is None
            assert len(sel.rejected) == 1
            assert sel.rejected[0].candidate == cand


# ---------------------------------------------------------------------------
# Test 2: F1 photo for a GT3 brief (event_match no) -> not context
# ---------------------------------------------------------------------------

def test_f1_photo_for_gt3_brief_not_context(make_photo):
    gt3_brief = PosterBrief(
        job_id=102,
        subjects=(Subject(name="Kelvin van der Linde", role="hero"),),
        event=EventContext(
            series="GT World Challenge Europe",
            vehicle="GT3 car",
            circuit="Portimao",
            session="race",
        ),
        story_type="result",
        mood="serious",
        badge_label="GT WORLD CHALLENGE",
        must_avoid=("F1 cars",),
    )
    photo_path = make_photo("f1_car.jpg")
    cand = PhotoCandidate(source="gallery", gallery_image_id=2, path_or_url=photo_path, role="context")

    with patch("app.services.design_images._vision_chat") as mock_vision, \
         patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        mock_vision.return_value = json.dumps({
            "person": "no",
            "event_match": "no",
            "obstructed": False,
            "watermark": False,
            "quality": 5,
            "notes": "Shows a Formula 1 car at Singapore, not a GT3 car at Portimao",
        })

        check = check_photo(cand, gt3_brief)
        assert check.event_match == "no"
        assert check.usable_as_context is False

        with patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand]):
            sel = select_photos(None, gt3_brief)
            assert sel.context == ()
            assert len(sel.rejected) == 1
            assert sel.rejected[0].candidate == cand


# ---------------------------------------------------------------------------
# Test 3: obstructed hero rejected
# ---------------------------------------------------------------------------

def test_obstructed_hero_rejected(make_photo, sample_brief):
    photo_path = make_photo("obstructed_verstappen.jpg")
    cand = PhotoCandidate(source="gallery", gallery_image_id=3, path_or_url=photo_path, role="hero")

    with patch("app.services.design_images._vision_chat") as mock_vision, \
         patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        mock_vision.return_value = json.dumps({
            "person": "yes",
            "event_match": "yes",
            "obstructed": True,
            "watermark": False,
            "quality": 4,
            "notes": "Face partially covered by team personnel in foreground",
        })

        check = check_photo(cand, sample_brief)
        assert check.person == "yes"
        assert check.obstructed is True
        assert check.usable_as_hero is False

        with patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand]):
            sel = select_photos(None, sample_brief)
            assert sel.hero is None
            assert len(sel.rejected) == 1


# ---------------------------------------------------------------------------
# Test 4: vision error -> fail closed
# ---------------------------------------------------------------------------

def test_vision_error_fail_closed(make_photo, sample_brief):
    photo_path = make_photo("corrupt.jpg")
    cand = PhotoCandidate(source="gallery", gallery_image_id=4, path_or_url=photo_path, role="hero")

    with patch("app.services.design_images._vision_chat", side_effect=RuntimeError("9Router connection reset")), \
         patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        check = check_photo(cand, sample_brief)
        assert check.person == "error"
        assert check.event_match == "error"
        assert check.quality == 1
        assert check.usable_as_hero is False
        assert check.usable_as_context is False

    with patch("app.services.design_images._vision_chat", return_value="this is not valid json at all"), \
         patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        check = check_photo(cand, sample_brief)
        assert check.person == "error"
        assert check.event_match == "error"
        assert check.quality == 1
        assert check.usable_as_hero is False

    with patch("app.services.design_images._vision_chat", return_value=json.dumps({"person": "maybe", "event_match": "yes", "obstructed": False, "watermark": False, "quality": 3})), \
         patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        check = check_photo(cand, sample_brief)
        assert check.person == "error"
        assert check.event_match == "error"
        assert check.quality == 1


# ---------------------------------------------------------------------------
# Test 5: face_frontal parsing and selection rules
# ---------------------------------------------------------------------------

def test_face_frontal_parsing(make_photo, sample_brief):
    photo_path = make_photo("face_frontal_test.jpg")
    cand = PhotoCandidate(source="gallery", gallery_image_id=10, path_or_url=photo_path, role="hero")

    cases = [
        ({"face_frontal": True}, True),
        ({"face_frontal": False}, False),
        ({}, False),  # missing -> False
        ({"face_frontal": "yes"}, False),  # "yes" -> False
        ({"face_frontal": "true"}, False),  # "true" string -> False
        ({"face_frontal": 1}, False),  # 1 int -> False
        ({"face_frontal": None}, False),  # None -> False
    ]

    for payload_extra, expected in cases:
        base_payload = {
            "person": "yes",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 4,
            "notes": "Testing face_frontal parsing",
        }
        base_payload.update(payload_extra)
        with patch("app.services.design_images._vision_chat", return_value=json.dumps(base_payload)), \
             patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
            check = check_photo(cand, sample_brief)
            assert check.face_frontal is expected, f"Failed for {payload_extra}, expected {expected}, got {check.face_frontal}"


def test_hero_chosen_frontal_first(make_photo, sample_brief):
    p1 = make_photo("hero_action_helmet.jpg", b"action-helmet")
    p2 = make_photo("hero_portrait_frontal.jpg", b"portrait-frontal")

    cand1 = PhotoCandidate(source="gallery", gallery_image_id=11, path_or_url=p1, role="hero")
    cand2 = PhotoCandidate(source="gallery", gallery_image_id=12, path_or_url=p2, role="hero")

    def mock_vision(content, **kwargs):
        uri = content[1]["image_url"]["url"]
        if "action-helmet" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 5,
                "face_frontal": False,
                "notes": "Quality 5 action photo, helmet on (not frontal)",
            })
        return json.dumps({
            "person": "yes",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 3,
            "face_frontal": True,
            "notes": "Quality 3 front-facing portrait",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", side_effect=lambda b: f"data:image/jpeg;base64,{b.decode()}"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand1, cand2]):
        sel = select_photos(None, sample_brief)

        # cand2 (face_frontal=True, quality=3) must beat cand1 (face_frontal=False, quality=5)
        assert sel.hero is not None
        assert sel.hero.candidate == cand2
        assert sel.hero.face_frontal is True
        assert sel.portrait is None


def test_portrait_set_when_hero_not_frontal(make_photo, sample_brief):
    p_hero = make_photo("hero_non_frontal.jpg", b"hero-non-frontal")
    p_port1 = make_photo("portrait_high_q.jpg", b"port-high-q")
    p_port2 = make_photo("portrait_low_q.jpg", b"port-low-q")

    cand_hero = PhotoCandidate(source="gallery", gallery_image_id=21, path_or_url=p_hero, role="hero")
    cand_port1 = PhotoCandidate(source="gallery", gallery_image_id=22, path_or_url=p_port1, role="hero")
    cand_port2 = PhotoCandidate(source="gallery", gallery_image_id=23, path_or_url=p_port2, role="hero")

    def mock_vision(content, **kwargs):
        uri = content[1]["image_url"]["url"]
        if "hero-non-frontal" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 4,
                "face_frontal": False,
                "notes": "Hero in action, helmeted",
            })
        elif "port-high-q" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "no",  # different event
                "obstructed": False,
                "watermark": False,
                "quality": 5,
                "face_frontal": True,
                "notes": "Frontal face portrait quality 5",
            })
        return json.dumps({
            "person": "yes",
            "event_match": "no",
            "obstructed": False,
            "watermark": False,
            "quality": 3,
            "face_frontal": True,
            "notes": "Frontal face portrait quality 3",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", side_effect=lambda b: f"data:image/jpeg;base64,{b.decode()}"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand_hero, cand_port1, cand_port2]):
        sel = select_photos(None, sample_brief)

        assert sel.hero is not None
        assert sel.hero.candidate == cand_hero
        assert sel.hero.face_frontal is False
        assert sel.portrait is not None
        assert sel.portrait.candidate == cand_port1
        assert sel.portrait.quality == 5
        assert sel.portrait.face_frontal is True


def test_portrait_stays_none_when_hero_is_frontal(make_photo, sample_brief):
    p_hero = make_photo("hero_frontal.jpg", b"hero-frontal")
    p_extra = make_photo("extra_portrait.jpg", b"extra-portrait")

    cand_hero = PhotoCandidate(source="gallery", gallery_image_id=31, path_or_url=p_hero, role="hero")
    cand_extra = PhotoCandidate(source="gallery", gallery_image_id=32, path_or_url=p_extra, role="hero")

    def mock_vision(content, **kwargs):
        uri = content[1]["image_url"]["url"]
        if "hero-frontal" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 5,
                "face_frontal": True,
                "notes": "Hero on podium, face frontal",
            })
        return json.dumps({
            "person": "yes",
            "event_match": "no",
            "obstructed": False,
            "watermark": False,
            "quality": 4,
            "face_frontal": True,
            "notes": "Extra portrait",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", side_effect=lambda b: f"data:image/jpeg;base64,{b.decode()}"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand_hero, cand_extra]):
        sel = select_photos(None, sample_brief)

        assert sel.hero is not None
        assert sel.hero.candidate == cand_hero
        assert sel.hero.face_frontal is True
        assert sel.portrait is None


def test_portrait_never_hero_and_not_in_rejected_or_context(make_photo, sample_brief):
    p_hero = make_photo("hero_action.jpg", b"hero-act")
    p_port = make_photo("hero_portrait.jpg", b"hero-port")
    p_ctx = make_photo("track_ctx.jpg", b"track-ctx")

    cand_hero = PhotoCandidate(source="gallery", gallery_image_id=41, path_or_url=p_hero, role="hero")
    cand_port = PhotoCandidate(source="gallery", gallery_image_id=42, path_or_url=p_port, role="hero")
    cand_ctx = PhotoCandidate(source="gallery", gallery_image_id=43, path_or_url=p_ctx, role="context")

    def mock_vision(content, **kwargs):
        uri = content[1]["image_url"]["url"]
        if "hero-act" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 4,
                "face_frontal": False,
                "notes": "Hero action non-frontal",
            })
        elif "hero-port" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "no",
                "obstructed": False,
                "watermark": False,
                "quality": 4,
                "face_frontal": True,
                "notes": "Portrait photo",
            })
        return json.dumps({
            "person": "no",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 4,
            "face_frontal": False,
            "notes": "Track context",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", side_effect=lambda b: f"data:image/jpeg;base64,{b.decode()}"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand_hero, cand_port, cand_ctx]):
        sel = select_photos(None, sample_brief)

        assert sel.hero is not None
        assert sel.hero.candidate == cand_hero
        assert sel.portrait is not None
        assert sel.portrait.candidate == cand_port

        # portrait is never hero
        assert sel.portrait != sel.hero
        assert sel.portrait.candidate != sel.hero.candidate

        # portrait is not in rejected or context
        assert sel.portrait not in sel.rejected
        assert sel.portrait not in sel.context
        assert cand_port not in [r.candidate for r in sel.rejected]
        assert cand_port not in [c.candidate for c in sel.context]

        # hero is not in rejected or context
        assert sel.hero not in sel.rejected
        assert sel.hero not in sel.context

        # context contains only cand_ctx
        assert len(sel.context) == 1
        assert sel.context[0].candidate == cand_ctx


def test_face_ref_prefers_frontal(make_photo, sample_brief):
    p_high_non_frontal = make_photo("ref_q5_non_frontal.jpg", b"ref-q5")
    p_lower_frontal = make_photo("ref_q3_frontal.jpg", b"ref-q3")

    cand_q5 = PhotoCandidate(source="gallery", gallery_image_id=51, path_or_url=p_high_non_frontal, role="hero")
    cand_q3 = PhotoCandidate(source="gallery", gallery_image_id=52, path_or_url=p_lower_frontal, role="hero")

    def mock_vision(content, **kwargs):
        uri = content[1]["image_url"]["url"]
        if "ref-q5" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "no",
                "obstructed": False,
                "watermark": False,
                "quality": 5,
                "face_frontal": False,
                "notes": "Wrong event, quality 5, seen from behind / profile",
            })
        return json.dumps({
            "person": "yes",
            "event_match": "no",
            "obstructed": False,
            "watermark": False,
            "quality": 3,
            "face_frontal": True,
            "notes": "Wrong event, quality 3, clearly frontal face",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", side_effect=lambda b: f"data:image/jpeg;base64,{b.decode()}"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand_q5, cand_q3]):
        sel = select_photos(None, sample_brief)

        assert sel.hero is None
        assert sel.face_ref is not None
        # Frontal quality 3 must beat non-frontal quality 5
        assert sel.face_ref.candidate == cand_q3
        assert sel.face_ref.face_frontal is True
        assert sel.face_ref not in sel.rejected
        assert cand_q5 in [r.candidate for r in sel.rejected]


# ---------------------------------------------------------------------------
# Test 6: context excludes hero
# ---------------------------------------------------------------------------

def test_context_excludes_hero(make_photo, sample_brief):
    p1 = make_photo("vers_action.jpg", b"vers_action_content")
    p2 = make_photo("monza_track.jpg", b"monza_track_content")

    cand1 = PhotoCandidate(source="gallery", gallery_image_id=21, path_or_url=p1, role="hero")
    cand2 = PhotoCandidate(source="gallery", gallery_image_id=22, path_or_url=p2, role="context")

    def mock_vision(content, **kwargs):
        # Both cand1 and cand2 pass event_match yes, quality >= 3
        # cand1 also has person yes
        if "vers_action" in content[1]["image_url"]["url"]:
            return json.dumps({
                "person": "yes",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 5,
                "notes": "Hero on podium",
            })
        return json.dumps({
            "person": "no",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 4,
            "notes": "Track action",
        })

    def fake_uri(b):
        return f"data:image/jpeg;base64,{b.decode()}"

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", side_effect=fake_uri), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand1, cand2]):
        sel = select_photos(None, sample_brief)

        assert sel.hero is not None
        assert sel.hero.candidate == cand1
        # Context must contain cand2, but NEVER cand1 (the hero)
        assert len(sel.context) == 1
        assert sel.context[0].candidate == cand2
        assert all(c.candidate != cand1 for c in sel.context)


# ---------------------------------------------------------------------------
# Test 7: candidates never match on getty_hints location
# ---------------------------------------------------------------------------

def test_candidates_never_match_on_getty_hints_location(db_session, make_photo, sample_brief):
    # Image A: has Verstappen in getty_hints['location'] and caption, but NOT in people/keyword/extra_keywords
    p_a = make_photo("getty_hint_person.jpg", b"getty_person")
    img_a = GalleryImage(
        keyword="Ferrari",
        extra_keywords=[],
        source_image_url="http://test-a",
        local_path=p_a,
        public_url="http://test-a.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={
            "getty_hints": {"location": "Max Verstappen"},
            "caption": "Max Verstappen walks past garage",
            "event": {"series": "Formula 1"},
            "people": [],
        },
    )

    # Image B: legitimate match on people
    p_b = make_photo("legit_person.jpg", b"legit_person")
    img_b = GalleryImage(
        keyword="Red Bull",
        extra_keywords=[],
        source_image_url="http://test-b",
        local_path=p_b,
        public_url="http://test-b.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={
            "people": [{"name": "Max Verstappen", "confidence": 0.95}],
            "event": {"series": "Formula 1", "event_name": "Italian Grand Prix", "year": 2026},
            "shot_type": "half_body",
        },
    )

    # Image C: circuit is Monza, but Monza is ONLY in getty_hints['location'], not event_name or caption
    p_c = make_photo("getty_hint_circuit.jpg", b"getty_circuit")
    img_c = GalleryImage(
        keyword="F1 Car",
        extra_keywords=[],
        source_image_url="http://test-c",
        local_path=p_c,
        public_url="http://test-c.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={
            "event": {"series": "Formula 1", "event_name": "Italian Grand Prix"},
            "getty_hints": {"location": "Monza, Italy"},
            "caption": "car speeding along straight",
            "shot_type": "car_on_track",
        },
    )

    # Image D: circuit Monza is in caption
    p_d = make_photo("legit_circuit.jpg", b"legit_circuit")
    img_d = GalleryImage(
        keyword="F1 Action",
        extra_keywords=[],
        source_image_url="http://test-d",
        local_path=p_d,
        public_url="http://test-d.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={
            "event": {"series": "Formula 1", "event_name": "Italian Grand Prix", "year": 2026},
            "caption": "Racing at Monza circuit in FP1",
            "shot_type": "car_on_track",
        },
    )

    db_session.add_all([img_a, img_b, img_c, img_d])
    db_session.commit()

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[]):
        candidates = gather_candidates(db_session, sample_brief)

        paths = [c.path_or_url for c in candidates]
        # img_a must NOT be in candidates (was only in getty_hints['location'])
        assert p_a not in paths
        # img_b MUST be in candidates
        assert p_b in paths
        # img_c must NOT be in candidates (circuit Monza was only in getty_hints['location'])
        assert p_c not in paths
        # img_d MUST be in candidates (caption mentions Monza)
        assert p_d in paths


# ---------------------------------------------------------------------------
# Additional Test: story photo role and fallback to context
# ---------------------------------------------------------------------------

def test_story_photo_role_and_fallback_to_context(make_photo, sample_brief):
    p_story = make_photo("story.jpg", b"story_content")
    p_gallery = make_photo("gallery_hero.jpg", b"gallery_hero_content")

    cand_story = PhotoCandidate(source="story", gallery_image_id=None, path_or_url=p_story, role="hero")
    cand_gallery = PhotoCandidate(source="gallery", gallery_image_id=99, path_or_url=p_gallery, role="hero")

    def mock_vision(content, **kwargs):
        uri = content[1]["image_url"]["url"]
        if "story_content" in uri:
            # Story photo fails person check, but matches event!
            return json.dumps({
                "person": "no",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 4,
                "notes": "Red Bull RB20 on track, no driver face visible",
            })
        return json.dumps({
            "person": "yes",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 5,
            "notes": "Max Verstappen portrait",
        })

    def fake_uri(b):
        return f"data:image/jpeg;base64,{b.decode()}"

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", side_effect=fake_uri), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand_story, cand_gallery]):
        sel = select_photos(None, sample_brief, story_photo_path=p_story)

        assert sel.hero is not None
        assert sel.hero.candidate == cand_gallery
        # Story photo should be accepted as context!
        assert len(sel.context) == 1
        assert sel.context[0].candidate == cand_story


# ---------------------------------------------------------------------------
# Additional Test: candidate deduplication and exclusions
# ---------------------------------------------------------------------------

def test_candidate_exclusions_and_deduplication(db_session, make_photo, sample_brief):
    p_valid = make_photo("valid.jpg")
    p_blurry = make_photo("blurry.jpg")
    p_obstructed = make_photo("obstructed.jpg")
    p_archival = make_photo("archival.jpg")
    p_deleted = make_photo("deleted.jpg")

    img_valid = GalleryImage(
        id=101,
        keyword="Max Verstappen",
        extra_keywords=[],
        source_image_url="http://valid",
        local_path=p_valid,
        public_url="http://valid.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={"people": [{"name": "Max Verstappen"}], "shot_type": "half_body"},
    )
    img_blurry = GalleryImage(
        id=102,
        keyword="Max Verstappen",
        extra_keywords=[],
        source_image_url="http://blurry",
        local_path=p_blurry,
        public_url="http://blurry.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={"people": [{"name": "Max Verstappen"}], "quality": {"blurry": True}},
    )
    img_obstructed = GalleryImage(
        id=103,
        keyword="Max Verstappen",
        extra_keywords=[],
        source_image_url="http://obstructed",
        local_path=p_obstructed,
        public_url="http://obstructed.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={"people": [{"name": "Max Verstappen"}], "quality": {"obstructed": True}},
    )
    img_archival = GalleryImage(
        id=104,
        keyword="Max Verstappen",
        extra_keywords=[],
        source_image_url="http://archival",
        local_path=p_archival,
        public_url="http://archival.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={"people": [{"name": "Max Verstappen"}], "archival_photo": True},
    )
    img_deleted = GalleryImage(
        id=105,
        keyword="Max Verstappen",
        extra_keywords=[],
        source_image_url="http://deleted",
        local_path=p_deleted,
        public_url="http://deleted.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=True,
        attributes={"people": [{"name": "Max Verstappen"}]},
    )

    db_session.add_all([img_valid, img_blurry, img_obstructed, img_archival, img_deleted])
    db_session.commit()

    # speaker_design returns the same img_valid as well
    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[img_valid]):
        candidates = gather_candidates(db_session, sample_brief)

        paths = [c.path_or_url for c in candidates]
        # Valid photo present
        assert p_valid in paths
        # No duplicates by gallery id
        gids = [c.gallery_image_id for c in candidates if c.gallery_image_id is not None]
        assert len(gids) == len(set(gids))
        # Excluded photos not present
        assert p_blurry not in paths
        assert p_obstructed not in paths
        assert p_archival not in paths
        assert p_deleted not in paths


# ---------------------------------------------------------------------------
# Additional Test: max checks capped at 24
# ---------------------------------------------------------------------------

def test_max_checks_capped_at_24(make_photo, sample_brief):
    candidates = []
    for i in range(30):
        p = make_photo(f"photo_{i}.jpg", f"photo_{i}".encode())
        candidates.append(
            PhotoCandidate(source="gallery", gallery_image_id=i + 1, path_or_url=p, role="context")
        )

    check_calls = 0

    def mock_vision(content, **kwargs):
        nonlocal check_calls
        check_calls += 1
        return json.dumps({
            "person": "no",
            "event_match": "no",
            "obstructed": False,
            "watermark": False,
            "quality": 2,
            "notes": "Mismatch",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,x"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=candidates):
        sel = select_photos(None, sample_brief)

        assert check_calls == 24
        assert len(sel.rejected) == 24


# ---------------------------------------------------------------------------
# Additional Test: brief without hero subject
# ---------------------------------------------------------------------------

def test_brief_without_hero_subject(make_photo):
    no_hero_brief = PosterBrief(
        job_id=103,
        subjects=(),
        event=EventContext(
            series="Formula 1",
            vehicle="Formula 1 car",
            circuit="Monza",
            session="race",
        ),
        story_type="preview",
        mood="neutral",
        badge_label="F1 PREVIEW",
    )
    p_story = make_photo("story_preview.jpg", b"story_preview")

    candidates = gather_candidates(None, no_hero_brief, story_photo_path=p_story)
    assert len(candidates) == 1
    assert candidates[0].role == "context"
    assert candidates[0].source == "story"

    with patch("app.services.design_images._vision_chat") as mock_vision, \
         patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,x"):
        mock_vision.return_value = json.dumps({
            "person": "no",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 4,
            "notes": "Monza circuit atmosphere",
        })
        sel = select_photos(None, no_hero_brief, story_photo_path=p_story)
        assert sel.hero is None
        assert len(sel.context) == 1
        assert sel.context[0].candidate == candidates[0]


# ---------------------------------------------------------------------------
# Additional Test: context matching when circuit is None
# ---------------------------------------------------------------------------

def test_context_matching_when_circuit_is_none(db_session, make_photo):
    brief_no_circuit = PosterBrief(
        job_id=104,
        subjects=(),
        event=EventContext(
            series="MotoGP",
            vehicle="MotoGP bike",
            circuit=None,
        ),
        story_type="breaking",
        mood="dramatic",
        badge_label="MOTOGP NEWS",
    )
    p1 = make_photo("motogp_action.jpg", b"motogp_1")
    p2 = make_photo("f1_action.jpg", b"f1_2")

    img_motogp = GalleryImage(
        keyword="MotoGP",
        extra_keywords=[],
        source_image_url="http://motogp-1",
        local_path=p1,
        public_url="http://motogp-1.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={
            "event": {"series": "MotoGP", "event_name": "Spanish GP", "year": 2026},
            "shot_type": "car_on_track",
        },
    )
    img_f1 = GalleryImage(
        keyword="F1",
        extra_keywords=[],
        source_image_url="http://f1-1",
        local_path=p2,
        public_url="http://f1-1.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={
            "event": {"series": "Formula 1"},
            "shot_type": "car_on_track",
        },
    )
    db_session.add_all([img_motogp, img_f1])
    db_session.commit()

    candidates = gather_candidates(db_session, brief_no_circuit)
    paths = [c.path_or_url for c in candidates]
    assert p1 in paths
    assert p2 not in paths


# ---------------------------------------------------------------------------
# Task PP2B Test: accent-insensitive and distinctive surname match
# ---------------------------------------------------------------------------

def test_accent_insensitive_and_distinctive_surname_matching(db_session, make_photo):
    brief = PosterBrief(
        job_id=201,
        subjects=(Subject(name="Nicolò Bulega", role="hero"),),
        event=EventContext(
            series="WorldSBK",
            vehicle="Superbike",
            circuit="Jerez",
        ),
        story_type="result",
        mood="happy",
        badge_label="WORLDSBK NEWS",
    )

    p_unaccented = make_photo("nicolo_bulega.jpg", b"nicolo")
    p_people = make_photo("people_bulega.jpg", b"people")
    p_surname = make_photo("surname_bulega.jpg", b"surname")
    p_other = make_photo("other_rider.jpg", b"other")

    # Image 1: keyword is unaccented "Nicolo Bulega"
    img1 = GalleryImage(
        keyword="Nicolo Bulega",
        extra_keywords=[],
        source_image_url="http://img1",
        local_path=p_unaccented,
        public_url="http://img1.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={"people": [{"name": "Nicolo Bulega"}]},
    )

    # Image 2: people field has unaccented "Nicolo Bulega"
    img2 = GalleryImage(
        keyword="Ducati Racing",
        extra_keywords=[],
        source_image_url="http://img2",
        local_path=p_people,
        public_url="http://img2.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={"people": [{"name": "Nicolo Bulega"}]},
    )

    # Image 3: keyword is surname alone "Bulega" (>= 5 letters)
    img3 = GalleryImage(
        keyword="Bulega",
        extra_keywords=[],
        source_image_url="http://img3",
        local_path=p_surname,
        public_url="http://img3.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={"people": []},
    )

    # Image 4: someone else
    img4 = GalleryImage(
        keyword="Toprak Razgatlioglu",
        extra_keywords=[],
        source_image_url="http://img4",
        local_path=p_other,
        public_url="http://img4.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={"people": [{"name": "Toprak Razgatlioglu"}]},
    )

    db_session.add_all([img1, img2, img3, img4])
    db_session.commit()

    # Direct unit checks on _matches_person
    assert _matches_person(img1, ["Nicolò Bulega"]) is True
    assert _matches_person(img2, ["Nicolò Bulega"]) is True
    assert _matches_person(img3, ["Nicolò Bulega"]) is True
    assert _matches_person(img4, ["Nicolò Bulega"]) is False

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[]):
        candidates = gather_candidates(db_session, brief)
        paths = [c.path_or_url for c in candidates if c.role == "hero"]
        assert p_unaccented in paths
        assert p_people in paths
        assert p_surname in paths
        assert p_other not in paths


# ---------------------------------------------------------------------------
# Task PP2B Test: series alias matching
# ---------------------------------------------------------------------------

def test_series_alias_matching(db_session, make_photo):
    wsbk_brief = PosterBrief(
        job_id=202,
        subjects=(),
        event=EventContext(
            series="WorldSBK",
            vehicle="Superbike",
            circuit=None,
        ),
        story_type="result",
        mood="dramatic",
        badge_label="WORLDSBK NEWS",
    )

    p_wsbk = make_photo("wsbk_alias.jpg", b"wsbk")
    p_superbike = make_photo("superbike_alias.jpg", b"superbike")
    p_gtwc = make_photo("gtwc_alias.jpg", b"gtwc")

    img_wsbk = GalleryImage(
        keyword="Superbike",
        extra_keywords=[],
        source_image_url="http://wsbk",
        local_path=p_wsbk,
        public_url="http://wsbk.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={"event": {"series": "WSBK"}},
    )

    img_superbike = GalleryImage(
        keyword="Superbike",
        extra_keywords=[],
        source_image_url="http://superbike",
        local_path=p_superbike,
        public_url="http://superbike.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={"event": {"series": "Superbike World Championship"}},
    )

    img_gtwc = GalleryImage(
        keyword="GTWC",
        extra_keywords=[],
        source_image_url="http://gtwc",
        local_path=p_gtwc,
        public_url="http://gtwc.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={"event": {"series": "GTWC"}},
    )

    db_session.add_all([img_wsbk, img_superbike, img_gtwc])
    db_session.commit()

    candidates = gather_candidates(db_session, wsbk_brief)
    paths = [c.path_or_url for c in candidates if c.role == "context"]
    assert p_wsbk in paths
    assert p_superbike in paths
    assert p_gtwc not in paths

    gtwc_brief = PosterBrief(
        job_id=203,
        subjects=(),
        event=EventContext(
            series="GT World Challenge Europe",
            vehicle="GT3",
            circuit=None,
        ),
        story_type="result",
        mood="dramatic",
        badge_label="GT WORLD CHALLENGE",
    )
    gtwc_candidates = gather_candidates(db_session, gtwc_brief)
    gtwc_paths = [c.path_or_url for c in gtwc_candidates if c.role == "context"]
    assert p_gtwc in gtwc_paths
    assert p_wsbk not in gtwc_paths
    assert p_superbike not in gtwc_paths


# ---------------------------------------------------------------------------
# Task PP2B Test: quality-2 story photo becomes context only when nothing better
# ---------------------------------------------------------------------------

def test_quality_2_story_photo_becomes_context_only_when_no_other_context(make_photo, sample_brief):
    p_story = make_photo("story_q2.jpg", b"story_q2_bytes")
    p_good_ctx = make_photo("good_context.jpg", b"good_ctx_bytes")

    cand_story = PhotoCandidate(source="story", gallery_image_id=None, path_or_url=p_story, role="hero")
    cand_good_ctx = PhotoCandidate(source="gallery", gallery_image_id=901, path_or_url=p_good_ctx, role="context")

    # Case A: Quality 2 story photo with person=yes, event_match=yes.
    # No other context candidates pass. Story photo becomes context (hero is None).
    def mock_vision_no_other(content, **kwargs):
        return json.dumps({
            "person": "yes",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 2,
            "notes": "Low-res story photo showing Verstappen at Monza",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision_no_other), \
         patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,x"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand_story]):
        sel = select_photos(None, sample_brief, story_photo_path=p_story)
        assert sel.hero is None
        assert len(sel.context) == 1
        assert sel.context[0].candidate == cand_story

    # Case B: Quality 2 story photo, BUT another context candidate passes with quality 4.
    # Story photo must NOT become context.
    def mock_vision_with_better(content, **kwargs):
        uri = content[1]["image_url"]["url"]
        if "story_q2_bytes" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 2,
                "notes": "Quality 2 story photo",
            })
        return json.dumps({
            "person": "no",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 4,
            "notes": "Quality 4 track action",
        })

    def fake_uri(b):
        return f"data:image/jpeg;base64,{b.decode()}"

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision_with_better), \
         patch("app.services.design_images._vision_datauri", side_effect=fake_uri), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand_story, cand_good_ctx]):
        sel = select_photos(None, sample_brief, story_photo_path=p_story)
        assert sel.hero is None
        assert len(sel.context) == 1
        assert sel.context[0].candidate == cand_good_ctx
        assert cand_story in [r.candidate for r in sel.rejected]


# ---------------------------------------------------------------------------
# Task PP7B Tests: captions, prompt updates, helmet riders, face reference
# ---------------------------------------------------------------------------

def test_gallery_caption_extraction():
    # Getty URL example from TASK-PP7B
    getty_url = (
        "https://media.gettyimages.com/id/12345/photo/"
        "misano-adriatico-italy-nicolo-bulega-of-aruba-it-racing-ducati-with-ducati-panigale-v4r-seen.jpg"
    )
    row_getty = SimpleNamespace(source_image_url=getty_url)
    assert (
        gallery_caption(row_getty)
        == "misano adriatico italy nicolo bulega of aruba it racing ducati with ducati panigale v4r seen"
    )

    # URL with query string
    url_qs = "https://example.com/photo/monza-italy-max-verstappen-red-bull-racing.jpg?size=612x612&token=abc"
    row_qs = SimpleNamespace(source_image_url=url_qs)
    assert gallery_caption(row_qs) == "monza italy max verstappen red bull racing"

    # URL with underscores
    url_underscores = "https://example.com/photo/nicolo_bulega_superbike_action_jerez.png"
    assert gallery_caption(SimpleNamespace(source_image_url=url_underscores)) == "nicolo bulega superbike action jerez"

    # Empty / None / missing
    assert gallery_caption(SimpleNamespace(source_image_url="")) is None
    assert gallery_caption(SimpleNamespace(source_image_url=None)) is None
    assert gallery_caption(None) is None
    assert gallery_caption({}) is None
    assert gallery_caption(SimpleNamespace()) is None

    # Too short (< 12 characters)
    assert gallery_caption(SimpleNamespace(source_image_url="https://example.com/photo/bulega-moto.jpg")) is None
    assert gallery_caption(SimpleNamespace(source_image_url="https://example.com/short.jpg")) is None


def test_gather_candidates_caption(db_session, make_photo, sample_brief):
    p_hero = make_photo("hero_cap.jpg")
    p_story = make_photo("story_cap.jpg")

    img = GalleryImage(
        id=301,
        keyword="Max Verstappen",
        extra_keywords=[],
        source_image_url="https://example.com/photo/monza-italy-max-verstappen-red-bull-racing.jpg",
        local_path=p_hero,
        public_url="http://test.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        is_deleted=False,
        attributes={"people": [{"name": "Max Verstappen"}]},
    )
    db_session.add(img)
    db_session.commit()

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[img]):
        candidates = gather_candidates(db_session, sample_brief, story_photo_path=p_story)

        story_cand = next(c for c in candidates if c.source == "story")
        gallery_cand = next(c for c in candidates if c.gallery_image_id == 301)

        assert story_cand.caption is None
        assert gallery_cand.caption == "monza italy max verstappen red bull racing"


def test_check_photo_prompt_caption_and_rules(make_photo, sample_brief):
    p = make_photo("test_prompt.jpg")
    cand_with_caption = PhotoCandidate(
        source="gallery",
        gallery_image_id=1,
        path_or_url=p,
        role="hero",
        caption="misano adriatico italy nicolo bulega of aruba it racing ducati",
    )
    cand_without_caption = PhotoCandidate(
        source="story",
        gallery_image_id=None,
        path_or_url=p,
        role="hero",
        caption=None,
    )

    prompts: list[str] = []

    def mock_vision(content, **kwargs):
        prompts.append(content[0]["text"])
        return json.dumps({
            "person": "yes",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 4,
            "notes": "Verified",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,x"):
        # Check candidate with caption
        check_photo(cand_with_caption, sample_brief)
        assert len(prompts) == 1
        p1 = prompts[0]

        # Verify agency caption line is included
        assert "- Photo agency caption (trusted): misano adriatico italy nicolo bulega of aruba it racing ducati" in p1

        # Verify helmet rider rule wording
        assert "When the face is hidden by a helmet or visor, answer 'yes' only if the agency caption names Max Verstappen AND the helmet, race number and team colours are consistent with him; otherwise 'unsure'." in p1

        # Verify event circuit rule wording
        assert "A different circuit, session or round of the same series is fine ('yes') unless the brief says night or rain and the photo clearly contradicts it." in p1

        # Verify question 6 and face_frontal in JSON schema
        assert "6. Is the hero's face clearly visible and turned toward the camera (front or three-quarter view, eyes visible, not from behind, not hidden by a helmet or visor)? (face_frontal: true/false)" in p1
        assert '"face_frontal": bool' in p1

        # Check candidate without caption
        check_photo(cand_without_caption, sample_brief)
        assert len(prompts) == 2
        p2 = prompts[1]
        assert "- Photo agency caption (trusted):" not in p2
        assert "When the face is hidden by a helmet or visor" in p2
        assert "A different circuit, session or round of the same series is fine ('yes')" in p2


def test_select_photos_helmet_photo_becomes_hero(make_photo):
    bulega_brief = PosterBrief(
        job_id=401,
        subjects=(Subject(name="Nicolò Bulega", role="hero", team="Aruba.it Racing - Ducati"),),
        event=EventContext(
            series="WorldSBK",
            vehicle="Superbike",
            circuit="Jerez",
            session="race",
            year=2026,
        ),
        story_type="result",
        mood="dramatic",
        badge_label="WORLDSBK NEWS",
    )
    p = make_photo("bulega_helmet.jpg", b"bulega_helmet")
    cand = PhotoCandidate(
        source="gallery",
        gallery_image_id=41314,
        path_or_url=p,
        role="hero",
        caption="misano adriatico italy nicolo bulega of aruba it racing ducati with ducati panigale v4r seen",
    )

    with patch("app.services.design_images._vision_chat") as mock_vision, \
         patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,x"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand]):
        mock_vision.return_value = json.dumps({
            "person": "yes",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 5,
            "notes": "Bulega on #11 Ducati Panigale V4R, helmet matches agency caption",
        })

        sel = select_photos(None, bulega_brief)
        assert sel.hero is not None
        assert sel.hero.candidate == cand
        assert sel.face_ref is None
        assert cand not in [r.candidate for r in sel.rejected]


def test_select_photos_face_ref_when_all_event_mismatch(make_photo):
    # Job 12602 scenario: Verstappen in GT World Challenge Portimao
    gtwc_brief = PosterBrief(
        job_id=402,
        subjects=(Subject(name="Max Verstappen", role="hero"),),
        event=EventContext(
            series="GT World Challenge Europe",
            vehicle="GT3 car",
            circuit="Portimao",
            session="race",
            year=2026,
        ),
        story_type="result",
        mood="serious",
        badge_label="GT WORLD CHALLENGE",
    )
    p_q3 = make_photo("f1_vers_q3.jpg", b"vers_q3")
    p_q5 = make_photo("f1_vers_q5.jpg", b"vers_q5")

    cand_q3 = PhotoCandidate(
        source="gallery",
        gallery_image_id=501,
        path_or_url=p_q3,
        role="hero",
        caption="singapore gp max verstappen red bull f1",
    )
    cand_q5 = PhotoCandidate(
        source="gallery",
        gallery_image_id=502,
        path_or_url=p_q5,
        role="hero",
        caption="singapore gp podium max verstappen portrait",
    )

    def mock_vision(content, **kwargs):
        uri = content[1]["image_url"]["url"]
        if "vers_q5" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "no",
                "obstructed": False,
                "watermark": False,
                "quality": 5,
                "notes": "Verstappen in F1 gear (wrong event)",
            })
        return json.dumps({
            "person": "yes",
            "event_match": "no",
            "obstructed": False,
            "watermark": False,
            "quality": 3,
            "notes": "Verstappen in garage (wrong event)",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", side_effect=lambda b: f"data:image/jpeg;base64,{b.decode()}"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand_q3, cand_q5]):
        sel = select_photos(None, gtwc_brief)

        # Hero must be None because event_match == 'no'
        assert sel.hero is None
        # face_ref must be the highest quality candidate (cand_q5, quality 5)
        assert sel.face_ref is not None
        assert sel.face_ref.candidate == cand_q5
        assert sel.face_ref.quality == 5
        # face_ref must NOT be in rejected
        assert sel.face_ref not in sel.rejected
        assert cand_q5 not in [r.candidate for r in sel.rejected]
        # Lower quality cand_q3 MUST be in rejected
        assert cand_q3 in [r.candidate for r in sel.rejected]


def test_select_photos_face_ref_ties_pick_first_checked(make_photo, sample_brief):
    p_tie1 = make_photo("f1_tie1.jpg", b"tie1")
    p_tie2 = make_photo("f1_tie2.jpg", b"tie2")

    cand_tie1 = PhotoCandidate(source="gallery", gallery_image_id=601, path_or_url=p_tie1, role="hero")
    cand_tie2 = PhotoCandidate(source="gallery", gallery_image_id=602, path_or_url=p_tie2, role="hero")

    def mock_vision(content, **kwargs):
        return json.dumps({
            "person": "yes",
            "event_match": "no",
            "obstructed": False,
            "watermark": False,
            "quality": 4,
            "notes": "Equal quality wrong event",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,x"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand_tie1, cand_tie2]):
        sel = select_photos(None, sample_brief)

        assert sel.hero is None
        assert sel.face_ref is not None
        # First checked should win ties
        assert sel.face_ref.candidate == cand_tie1
        assert sel.face_ref not in sel.rejected
        assert cand_tie2 in [r.candidate for r in sel.rejected]


def test_select_photos_hero_found_leaves_face_ref_none(make_photo, sample_brief):
    p_hero = make_photo("hero_event.jpg", b"hero_event")
    p_other = make_photo("other_event.jpg", b"other_event")

    cand_hero = PhotoCandidate(source="gallery", gallery_image_id=701, path_or_url=p_hero, role="hero")
    cand_other = PhotoCandidate(source="gallery", gallery_image_id=702, path_or_url=p_other, role="hero")

    def mock_vision(content, **kwargs):
        uri = content[1]["image_url"]["url"]
        if "hero_event" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 4,
                "notes": "Matches event and person",
            })
        return json.dumps({
            "person": "yes",
            "event_match": "no",
            "obstructed": False,
            "watermark": False,
            "quality": 5,
            "notes": "Matches person only",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", side_effect=lambda b: f"data:image/jpeg;base64,{b.decode()}"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand_hero, cand_other]):
        sel = select_photos(None, sample_brief)

        assert sel.hero is not None
        assert sel.hero.candidate == cand_hero
        # When hero is found, face_ref must be None
        assert sel.face_ref is None


def test_select_photos_obstructed_watermark_low_quality_never_face_ref(make_photo, sample_brief):
    p1 = make_photo("obstructed_ref.jpg", b"p1")
    p2 = make_photo("watermark_ref.jpg", b"p2")
    p3 = make_photo("low_quality_ref.jpg", b"p3")

    cand1 = PhotoCandidate(source="gallery", gallery_image_id=801, path_or_url=p1, role="hero")
    cand2 = PhotoCandidate(source="gallery", gallery_image_id=802, path_or_url=p2, role="hero")
    cand3 = PhotoCandidate(source="gallery", gallery_image_id=803, path_or_url=p3, role="hero")

    def mock_vision(content, **kwargs):
        uri = content[1]["image_url"]["url"]
        if "p1" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "no",
                "obstructed": True,
                "watermark": False,
                "quality": 5,
                "notes": "Obstructed face",
            })
        elif "p2" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "no",
                "obstructed": False,
                "watermark": True,
                "quality": 5,
                "notes": "Watermark overlay",
            })
        return json.dumps({
            "person": "yes",
            "event_match": "no",
            "obstructed": False,
            "watermark": False,
            "quality": 2,
            "notes": "Low quality",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", side_effect=lambda b: f"data:image/jpeg;base64,{b.decode()}"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand1, cand2, cand3]):
        sel = select_photos(None, sample_brief)

        assert sel.hero is None
        assert sel.face_ref is None
        assert len(sel.rejected) == 3


# ---------------------------------------------------------------------------
# Task PP9A Tests: story prompt line, shows/story_fit parsing, ranking, caps
# ---------------------------------------------------------------------------

def test_check_photo_prompt_includes_story_line(make_photo, sample_brief):
    photo_path = make_photo("test_story.jpg")
    cand = PhotoCandidate(source="gallery", gallery_image_id=901, path_or_url=photo_path, role="hero")

    prompts: list[str] = []

    def mock_vision(content, **kwargs):
        prompts.append(content[0]["text"])
        return json.dumps({
            "person": "yes",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 5,
            "face_frontal": True,
            "shows": "Verstappen celebrates victory",
            "story_fit": 5,
            "notes": "Good photo",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,x"):
        # 1. story is given
        short_story = "Max Verstappen held off a late charge to win the Italian Grand Prix at Monza."
        check_photo(cand, sample_brief, story=short_story)
        assert len(prompts) == 1
        assert f"- Story: {short_story}" in prompts[0]
        assert "7. In one short line, what does the photo show (who, doing what, where)? (shows: string, max 120 characters)" in prompts[0]
        assert "8. How well does this photo support THIS story as a poster reference, 1 to 5? (5 = the exact moment, place or emotion of the story; 1 = unrelated) (story_fit: integer)" in prompts[0]
        assert '"shows": "string (max 120 chars)"' in prompts[0]
        assert '"story_fit": 1-5' in prompts[0]

        # 2. story longer than 600 chars is sliced to 600
        long_story = "A" * 800
        check_photo(cand, sample_brief, story=long_story)
        assert len(prompts) == 2
        assert f"- Story: {'A' * 600}" in prompts[1]
        assert "A" * 601 not in prompts[1]

        # 3. story is None -> no story line
        check_photo(cand, sample_brief, story=None)
        assert len(prompts) == 3
        assert "- Story:" not in prompts[2]

        # 4. select_photos passes story to check_photo
        with patch("app.services.pro_poster.photos.gather_candidates", return_value=[cand]):
            select_photos(None, sample_brief, story="Passed down story")
            assert len(prompts) == 4
            assert "- Story: Passed down story" in prompts[3]


def test_shows_and_story_fit_parsing(make_photo, sample_brief):
    photo_path = make_photo("test_parsing.jpg")
    cand = PhotoCandidate(source="gallery", gallery_image_id=902, path_or_url=photo_path, role="hero")

    test_cases = [
        # Valid
        ({"shows": "Verstappen on podium", "story_fit": 4}, "Verstappen on podium", 4),
        # Whitespace stripping & truncation at 120
        ({"shows": "   " + "x" * 150 + "   ", "story_fit": 3}, "x" * 120, 3),
        # Missing (defaults: shows="", story_fit=0)
        ({}, "", 0),
        ({"shows": None, "story_fit": None}, "", 0),
        # Out of range clamped to 1..5
        ({"shows": "Action", "story_fit": 6}, "Action", 5),
        ({"shows": "Action", "story_fit": 10}, "Action", 5),
        ({"shows": "Action", "story_fit": 0}, "Action", 1),
        ({"shows": "Action", "story_fit": -5}, "Action", 1),
        # String numbers
        ({"shows": "Action", "story_fit": "4"}, "Action", 4),
        ({"shows": "Action", "story_fit": "7"}, "Action", 5),
        ({"shows": "Action", "story_fit": "0"}, "Action", 1),
        # Invalid story_fit non-fail-closed -> 0
        ({"shows": "Action", "story_fit": "not_a_number"}, "Action", 0),
        ({"shows": "Action", "story_fit": [1, 2]}, "Action", 0),
        ({"shows": "Action", "story_fit": True}, "Action", 0),
    ]

    for payload_extra, exp_shows, exp_story_fit in test_cases:
        payload = {
            "person": "yes",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 4,
            "face_frontal": True,
            "notes": "Defensive parsing test",
        }
        payload.update(payload_extra)

        with patch("app.services.design_images._vision_chat", return_value=json.dumps(payload)), \
             patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,x"):
            check = check_photo(cand, sample_brief)
            assert check.person == "yes"
            assert check.event_match == "yes"
            assert check.shows == exp_shows, f"Failed shows for {payload_extra}"
            assert check.story_fit == exp_story_fit, f"Failed story_fit for {payload_extra}"


def test_hero_ranking_uses_story_fit_after_face_frontal(make_photo, sample_brief):
    p1 = make_photo("cand1.jpg", b"cand1")
    p2 = make_photo("cand2.jpg", b"cand2")
    p3 = make_photo("cand3.jpg", b"cand3")

    c1 = PhotoCandidate(source="gallery", gallery_image_id=911, path_or_url=p1, role="hero")
    c2 = PhotoCandidate(source="gallery", gallery_image_id=912, path_or_url=p2, role="hero")
    c3 = PhotoCandidate(source="gallery", gallery_image_id=913, path_or_url=p3, role="hero")

    def mock_vision(content, **kwargs):
        uri = content[1]["image_url"]["url"]
        if "cand1" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 5,
                "face_frontal": False,
                "story_fit": 5,
            })
        elif "cand2" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 5,
                "face_frontal": True,
                "story_fit": 3,
            })
        return json.dumps({
            "person": "yes",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 3,
            "face_frontal": True,
            "story_fit": 5,
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", side_effect=lambda b: f"data:image/jpeg;base64,{b.decode()}"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[c1, c2, c3]):
        sel = select_photos(None, sample_brief)
        assert sel.hero is not None
        assert sel.hero.candidate == c3
        assert sel.hero.face_frontal is True
        assert sel.hero.story_fit == 5


def test_context_ranking_by_story_fit(make_photo, sample_brief):
    p1 = make_photo("ctx1.jpg", b"ctx1")
    p2 = make_photo("ctx2.jpg", b"ctx2")
    p3 = make_photo("ctx3.jpg", b"ctx3")

    c1 = PhotoCandidate(source="gallery", gallery_image_id=921, path_or_url=p1, role="context")
    c2 = PhotoCandidate(source="gallery", gallery_image_id=922, path_or_url=p2, role="context")
    c3 = PhotoCandidate(source="gallery", gallery_image_id=923, path_or_url=p3, role="context")

    def mock_vision(content, **kwargs):
        uri = content[1]["image_url"]["url"]
        if "ctx1" in uri:
            return json.dumps({
                "person": "no",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 3,
                "story_fit": 5,
                "shows": "Podium celebration with champagne",
            })
        elif "ctx2" in uri:
            return json.dumps({
                "person": "no",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 5,
                "story_fit": 4,
                "shows": "Car speeding down the main straight",
            })
        return json.dumps({
            "person": "no",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 5,
            "story_fit": 2,
            "shows": "Crowd in grandstand",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", side_effect=lambda b: f"data:image/jpeg;base64,{b.decode()}"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[c1, c2, c3]):
        sel = select_photos(None, sample_brief)
        assert len(sel.context) == 2
        assert sel.context[0].candidate == c1
        assert sel.context[0].story_fit == 5
        assert sel.context[1].candidate == c2
        assert sel.context[1].story_fit == 4
        assert c3 in [r.candidate for r in sel.rejected]


def test_near_duplicate_of_hero_shows_skipped(make_photo, sample_brief):
    p_hero = make_photo("hero.jpg", b"hero")
    p_ctx_dup1 = make_photo("ctx_dup1.jpg", b"ctx_dup1")
    p_ctx_dup2 = make_photo("ctx_dup2.jpg", b"ctx_dup2")
    p_ctx_diff = make_photo("ctx_diff.jpg", b"ctx_diff")

    c_hero = PhotoCandidate(source="gallery", gallery_image_id=931, path_or_url=p_hero, role="hero")
    c_dup1 = PhotoCandidate(source="gallery", gallery_image_id=932, path_or_url=p_ctx_dup1, role="context")
    c_dup2 = PhotoCandidate(source="gallery", gallery_image_id=933, path_or_url=p_ctx_dup2, role="context")
    c_diff = PhotoCandidate(source="gallery", gallery_image_id=934, path_or_url=p_ctx_diff, role="context")

    def mock_vision(content, **kwargs):
        uri = content[1]["image_url"]["url"]
        if "hero" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 5,
                "face_frontal": True,
                "story_fit": 5,
                "shows": "Max Verstappen celebrating on the Monza podium with trophy",
            })
        elif "ctx_dup1" in uri:
            return json.dumps({
                "person": "no",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 5,
                "story_fit": 5,
                "shows": "max verstappen celebrating on the monza podium with trophy!",
            })
        elif "ctx_dup2" in uri:
            return json.dumps({
                "person": "no",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 5,
                "story_fit": 5,
                "shows": "Max Verstappen celebrating on the Monza podium with team trophy",
            })
        return json.dumps({
            "person": "no",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 4,
            "story_fit": 4,
            "shows": "Red Bull racing mechanics celebrating at pit wall",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", side_effect=lambda b: f"data:image/jpeg;base64,{b.decode()}"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[c_hero, c_dup1, c_dup2, c_diff]):
        sel = select_photos(None, sample_brief)
        assert sel.hero is not None
        assert sel.hero.candidate == c_hero

        assert len(sel.context) == 1
        assert sel.context[0].candidate == c_diff
        assert c_dup1 in [r.candidate for r in sel.rejected]
        assert c_dup2 in [r.candidate for r in sel.rejected]


def test_similar_helper():
    assert _similar("", "") is False
    assert _similar("Verstappen on podium", "") is False
    assert _similar("", "Verstappen on podium") is False
    assert _similar("Verstappen on podium!", "verstappen on podium") is True
    assert _similar("Nicolò Bulega lifts the trophy", "nicolo bulega lifts the trophy") is True
    assert _similar("Max Verstappen celebrating on Monza podium", "Max Verstappen celebrating at Monza podium") is True
    assert _similar("Max Verstappen celebrating on Monza podium", "Ferrari car driving on track") is False


def test_non_hero_context_wins_ties(make_photo, sample_brief):
    # sample_brief has hero Subject(name="Max Verstappen") -> surname "Verstappen"
    p_hero = make_photo("hero_chosen.jpg", b"hero_chosen")
    p1 = make_photo("ctx_with_surname.jpg", b"ctx_surname")
    p2 = make_photo("ctx_no_surname.jpg", b"ctx_no_surname")

    c_hero = PhotoCandidate(source="gallery", gallery_image_id=940, path_or_url=p_hero, role="hero")
    c1 = PhotoCandidate(source="gallery", gallery_image_id=941, path_or_url=p1, role="context")
    c2 = PhotoCandidate(source="gallery", gallery_image_id=942, path_or_url=p2, role="context")

    def mock_vision(content, **kwargs):
        uri = content[1]["image_url"]["url"]
        if "hero_chosen" in uri:
            return json.dumps({
                "person": "yes",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 5,
                "face_frontal": True,
                "story_fit": 5,
                "shows": "Max Verstappen official portrait",
            })
        elif "ctx_surname" in uri:
            # Shows hero surname, higher quality (5), story_fit (4)
            return json.dumps({
                "person": "no",
                "event_match": "yes",
                "obstructed": False,
                "watermark": False,
                "quality": 5,
                "story_fit": 4,
                "shows": "Verstappen holding trophy on podium",
            })
        # Does not show hero surname, lower quality (4), but same story_fit (4)
        return json.dumps({
            "person": "no",
            "event_match": "yes",
            "obstructed": False,
            "watermark": False,
            "quality": 4,
            "story_fit": 4,
            "shows": "Red Bull RB20 on track at Ascari chicane",
        })

    with patch("app.services.design_images._vision_chat", side_effect=mock_vision), \
         patch("app.services.design_images._vision_datauri", side_effect=lambda b: f"data:image/jpeg;base64,{b.decode()}"), \
         patch("app.services.pro_poster.photos.gather_candidates", return_value=[c_hero, c1, c2]):
        sel = select_photos(None, sample_brief)
        assert sel.hero is not None
        assert sel.hero.candidate == c_hero
        # Both c1 and c2 have story_fit=4. c2 does NOT contain hero surname "Verstappen", so c2 must win!
        assert len(sel.context) == 2
        assert sel.context[0].candidate == c2
        assert sel.context[1].candidate == c1


def test_candidate_caps_twelve_hero_ten_context(db_session, make_photo, sample_brief):
    hero_imgs = []
    for i in range(15):
        p = make_photo(f"hero_cap_{i}.jpg")
        hero_imgs.append(
            GalleryImage(
                id=1000 + i,
                keyword="Max Verstappen",
                extra_keywords=[],
                source_image_url=f"http://hero-{i}",
                local_path=p,
                public_url=f"http://hero-{i}.jpg",
                width=1000,
                height=800,
                source_engine="bing",
                is_deleted=False,
                attributes={"people": [{"name": "Max Verstappen"}], "shot_type": "half_body"},
            )
        )

    context_imgs = []
    for i in range(15):
        p = make_photo(f"ctx_cap_{i}.jpg")
        context_imgs.append(
            GalleryImage(
                id=2000 + i,
                keyword="Formula 1",
                extra_keywords=[],
                source_image_url=f"http://ctx-{i}",
                local_path=p,
                public_url=f"http://ctx-{i}.jpg",
                width=1000,
                height=800,
                source_engine="bing",
                is_deleted=False,
                attributes={
                    "event": {"series": "Formula 1", "event_name": "Italian GP Monza", "year": 2026},
                    "shot_type": "car_on_track",
                },
            )
        )

    db_session.add_all(hero_imgs + context_imgs)
    db_session.commit()

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[]):
        candidates = gather_candidates(db_session, sample_brief)
        hero_cands = [c for c in candidates if c.role == "hero" and c.source == "gallery"]
        context_cands = [c for c in candidates if c.role == "context" and c.source == "gallery"]

        assert len(hero_cands) == 12
        assert len(context_cands) == 10

