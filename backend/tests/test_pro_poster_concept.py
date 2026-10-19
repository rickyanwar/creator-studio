"""Tests for pro_poster concept generator service."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.pro_poster.concept import (
    _build_concept_prompt,
    _format_reference_photos,
    build_concept,
    parse_concept_json,
)
from app.services.pro_poster.types import (
    EventContext,
    PhotoCandidate,
    PhotoCheck,
    PhotoSelection,
    PosterBrief,
    PosterConcept,
    Subject,
)


def _make_candidate(role="hero", path="/images/test.jpg"):
    return PhotoCandidate(
        source="gallery",
        gallery_image_id=1,
        path_or_url=path,
        role=role,
    )


def _make_check(shows="Nicolo Bulega celebrating on podium", role="hero", path="/images/test.jpg"):
    return PhotoCheck(
        candidate=_make_candidate(role=role, path=path),
        person="yes",
        event_match="yes",
        obstructed=False,
        watermark=False,
        quality=4,
        shows=shows,
    )


def _make_brief(series="WorldSBK", circuit="Jerez", subjects=(Subject(name="Nicolo Bulega", role="hero"),)):
    return PosterBrief(
        job_id=101,
        subjects=subjects,
        event=EventContext(
            series=series,
            vehicle="superbike",
            circuit=circuit,
            year=2026,
        ),
        story_type="result",
        mood="dramatic",
        badge_label=f"{series.upper()} NEWS",
    )


def _make_selection():
    hero = _make_check(shows="Nicolo Bulega celebrating with trophy", role="hero", path="/images/hero.jpg")
    portrait = _make_check(shows="Bulega looking straight into camera", role="hero", path="/images/portrait.jpg")
    ctx = _make_check(shows="Ducati Panigale V4R on Jerez track", role="context", path="/images/ctx.jpg")
    return PhotoSelection(hero=hero, portrait=portrait, context=(ctx,))


def test_build_concept_valid_json(monkeypatch):
    """Valid JSON from LLM is parsed into PosterConcept."""
    canned = {
        "key_moment": "Bulega takes historic victory at Jerez",
        "scene": "Bulega in Ducati leathers raising his arms in triumph, matching Image 1",
        "inset": "The red Ducati Panigale V4R accelerating out of the final corner",
        "graphic_words": ["P1", "WIN"],
        "palette": "Ducati red, dark charcoal, warm gold",
        "mood": "triumphant and dramatic",
    }
    monkeypatch.setattr(
        "app.services.pro_poster.concept._generate_with_fallback",
        lambda prompt, parse_fn, **kwargs: (parse_fn(json.dumps(canned)), "router"),
    )

    brief = _make_brief()
    selection = _make_selection()
    concept = build_concept(
        brief,
        selection,
        headline="Bulega takes P1 win at Jerez",
        story_text="Dominant race win for the Ducati rider.",
    )

    assert concept is not None
    assert concept.key_moment == "Bulega takes historic victory at Jerez"
    assert concept.scene == "Bulega in Ducati leathers raising his arms in triumph, matching Image 1"
    assert concept.inset == "The red Ducati Panigale V4R accelerating out of the final corner"
    assert concept.graphic_words == ("P1", "WIN")
    assert concept.palette == "Ducati red, dark charcoal, warm gold"
    assert concept.mood == "triumphant and dramatic"


def test_build_concept_graphic_word_not_in_text_dropped(monkeypatch):
    """Graphic words not literally in headline or story (case-insensitive, punct stripped) are dropped."""
    canned = {
        "key_moment": "Tense finish",
        "scene": "Driver in cockpit",
        "inset": "none",
        "graphic_words": ["P9", "SUPER", "100K", "CHAMPION"],
        "palette": "",
        "mood": "",
    }
    monkeypatch.setattr(
        "app.services.pro_poster.concept._generate_with_fallback",
        lambda prompt, parse_fn, **kwargs: (parse_fn(json.dumps(canned)), "router"),
    )

    brief = _make_brief()
    selection = _make_selection()
    # Headline has P9, story has 100K! with exclamation mark. SUPER and CHAMPION not in text.
    concept = build_concept(
        brief,
        selection,
        headline="Verstappen battles to P9 finish",
        story_text="A crowd of over 100K! cheered the comeback drive.",
    )

    assert concept is not None
    assert "P9" in concept.graphic_words
    assert "100K" in concept.graphic_words
    assert "SUPER" not in concept.graphic_words
    assert "CHAMPION" not in concept.graphic_words


def test_build_concept_at_most_three_graphic_words(monkeypatch):
    """Graphic words are limited to at most 3 items."""
    canned = {
        "key_moment": "Record broken",
        "scene": "Car crossing finish line",
        "inset": "none",
        "graphic_words": ["ONE", "TWO", "THREE", "FOUR", "FIVE"],
        "palette": "",
        "mood": "",
    }
    monkeypatch.setattr(
        "app.services.pro_poster.concept._generate_with_fallback",
        lambda prompt, parse_fn, **kwargs: (parse_fn(json.dumps(canned)), "router"),
    )

    brief = _make_brief()
    selection = _make_selection()
    concept = build_concept(
        brief,
        selection,
        headline="ONE TWO THREE FOUR FIVE",
        story_text="",
    )

    assert concept is not None
    assert len(concept.graphic_words) == 3
    assert concept.graphic_words == ("ONE", "TWO", "THREE")


def test_build_concept_empty_scene_gives_none(monkeypatch):
    """Empty scene in JSON causes validation failure and returns None."""
    canned = {
        "key_moment": "Some moment",
        "scene": "",
        "inset": "none",
        "graphic_words": [],
    }
    monkeypatch.setattr(
        "app.services.pro_poster.concept._generate_with_fallback",
        lambda prompt, parse_fn, **kwargs: (parse_fn(json.dumps(canned)), "router"),
    )

    brief = _make_brief()
    selection = _make_selection()
    concept = build_concept(brief, selection, headline="Headline", story_text="Story")
    assert concept is None


def test_build_concept_empty_key_moment_gives_none(monkeypatch):
    """Empty key_moment in JSON causes validation failure and returns None."""
    canned = {
        "key_moment": "   ",
        "scene": "A great scene",
        "inset": "none",
        "graphic_words": [],
    }
    monkeypatch.setattr(
        "app.services.pro_poster.concept._generate_with_fallback",
        lambda prompt, parse_fn, **kwargs: (parse_fn(json.dumps(canned)), "router"),
    )

    brief = _make_brief()
    selection = _make_selection()
    concept = build_concept(brief, selection, headline="Headline", story_text="Story")
    assert concept is None


def test_build_concept_invalid_json_gives_none(monkeypatch):
    """Malformed JSON returns None gracefully without raising."""
    monkeypatch.setattr(
        "app.services.pro_poster.concept._generate_with_fallback",
        lambda prompt, parse_fn, **kwargs: (parse_fn("Not a JSON string"), "router"),
    )

    brief = _make_brief()
    selection = _make_selection()
    concept = build_concept(brief, selection, headline="Headline", story_text="Story")
    assert concept is None


def test_build_concept_refs_numbered_in_send_order_with_shows():
    """Prompt numbers reference photos in the send order (hero, portrait, context) with shows text."""
    hero = _make_check(shows="Hero celebrating with trophy", role="hero", path="/images/hero.jpg")
    portrait = _make_check(shows="Hero front portrait looking at camera", role="hero", path="/images/portrait.jpg")
    ctx1 = _make_check(shows="Car on track at Portimao", role="context", path="/images/ctx1.jpg")
    ctx2 = _make_check(shows="Pit crew on pit wall", role="context", path="/images/ctx2.jpg")

    selection = PhotoSelection(hero=hero, portrait=portrait, context=(ctx1, ctx2))
    brief = _make_brief(series="GT World Challenge Europe", circuit="Portimao")

    prompt = _build_concept_prompt(brief, selection, headline="Verstappen wins Portimao", story_text="Report text")

    assert "- Image 1: Hero celebrating with trophy" in prompt
    assert "- Image 2: Hero front portrait looking at camera" in prompt
    assert "- Image 3: Car on track at Portimao" in prompt
    assert "- Image 4: Pit crew on pit wall" in prompt

    # Verify order in prompt text
    pos1 = prompt.index("Image 1: Hero celebrating with trophy")
    pos2 = prompt.index("Image 2: Hero front portrait looking at camera")
    pos3 = prompt.index("Image 3: Car on track at Portimao")
    pos4 = prompt.index("Image 4: Pit crew on pit wall")
    assert pos1 < pos2 < pos3 < pos4


def test_build_concept_face_ref_ordering_when_hero_none():
    """When hero is None, face_ref is Image 1 and context follows."""
    face_ref = _make_check(shows="Driver face reference from gala", role="hero", path="/images/face.jpg")
    ctx = _make_check(shows="Action shot on track", role="context", path="/images/ctx.jpg")

    selection = PhotoSelection(hero=None, face_ref=face_ref, context=(ctx,))
    brief = _make_brief()

    prompt = _build_concept_prompt(brief, selection, headline="Headline", story_text="Story")

    assert "- Image 1: Driver face reference from gala" in prompt
    assert "- Image 2: Action shot on track" in prompt
    assert prompt.index("Image 1") < prompt.index("Image 2")


def test_parse_concept_json_string_length_limits():
    """Test that fields exceeding length limits are truncated."""
    long_key = "K" * 500
    long_scene = "S" * 500
    long_inset = "I" * 400
    long_palette = "P" * 200
    long_mood = "M" * 200

    raw = json.dumps({
        "key_moment": long_key,
        "scene": long_scene,
        "inset": long_inset,
        "graphic_words": [],
        "palette": long_palette,
        "mood": long_mood,
    })

    concept = parse_concept_json(raw)
    assert len(concept.key_moment) == 400
    assert len(concept.scene) == 400
    assert len(concept.inset) == 300
    assert len(concept.palette) == 120
    assert len(concept.mood) == 120
