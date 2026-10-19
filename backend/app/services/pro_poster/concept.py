"""Pro poster visual concept generator (art director pass before image generation).

Uses Gemini Flash to formulate a story-specific visual concept (key moment, scene,
inset, palette, mood, graphic words) based on the story and reference photos.
"""
from __future__ import annotations

import json
import logging
import re
import string
from typing import Any

from app.services.news_copywriter import _generate_with_fallback
from app.services.pro_poster.art_direction import event_line
from app.services.pro_poster.types import (
    PhotoCheck,
    PhotoSelection,
    PosterBrief,
    PosterConcept,
)

logger = logging.getLogger(__name__)


def _normalize_token(s: str) -> str:
    """Strip spaces and punctuation, lowercase for token comparison."""
    return re.sub(r"[\s" + re.escape(string.punctuation) + r"]+", "", s).lower()


def _format_reference_photos(selection: PhotoSelection) -> str:
    """Format reference photos numbered in exact order art_direction will send them."""
    refs: list[PhotoCheck] = []
    if selection.hero is not None:
        refs.append(selection.hero)
        if selection.portrait is not None:
            refs.append(selection.portrait)
    elif selection.face_ref is not None:
        refs.append(selection.face_ref)
    for ctx in selection.context:
        refs.append(ctx)

    if not refs:
        return "- No reference photos available."

    lines = []
    for idx, ref in enumerate(refs, start=1):
        shows = ref.shows.strip() if ref.shows and ref.shows.strip() else "Reference photo"
        lines.append(f"- Image {idx}: {shows}")
    return "\n".join(lines)


def _build_concept_prompt(
    brief: PosterBrief,
    selection: PhotoSelection,
    *,
    headline: str | None,
    story_text: str | None,
) -> str:
    """Build English prompt for the art director concept step."""
    cleaned_hl = " ".join(headline.replace("**", "").split()) if headline else ""
    story_snippet = story_text.strip()[:1500] if story_text else ""
    refs_block = _format_reference_photos(selection)
    ev_line = event_line(brief)

    return f"""You are a senior sports poster art director.
Your job is to decide the ONE strongest story-specific visual concept for a vertical 4:5 poster before image generation.

Story headline: {cleaned_hl or "None"}
Story text (first 1500 chars):
\"\"\"{story_snippet or "None"}\"\"\"

Series: {brief.event.series}
Event: {ev_line}
Mood: {brief.mood}
Story type: {brief.story_type}

Reference photos:
{refs_block}

Decide the visual concept and respond with STRICT JSON:
{{
  "key_moment": "...",
  "scene": "...",
  "inset": "... or none",
  "graphic_words": ["..."],
  "palette": "...",
  "mood": "..."
}}

Rules:
- The scene must use the reference photos for every real person, by image number.
- The inset must show a DIFFERENT moment or detail than the main image that adds context: the trophy or title moment, the car on track, the venue, or the rival. It must never show the same person in the same pose. If nothing fits, use "none".
- graphic_words: at most 3 short words or numbers that literally appear in the headline or the story text (e.g. "P9", "2X", "100K", "KO", "TREBLE").
- Never invent results, people or places.
- If there is no reference photo of a person, the scene must not show any identifiable person's face.
Respond ONLY with the JSON object."""


def parse_concept_json(
    raw: str,
    *,
    headline: str | None = None,
    story_text: str | None = None,
) -> PosterConcept:
    """Parse and validate JSON response into a PosterConcept.

    Raises ValueError if JSON is invalid or key_moment/scene is empty.
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except Exception as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("JSON must be an object")

    key_moment = str(data.get("key_moment") or "").strip()[:400]
    scene = str(data.get("scene") or "").strip()[:400]
    if not key_moment or not scene:
        raise ValueError("key_moment and scene must not be empty")

    inset_raw = str(data.get("inset") or "").strip()[:300]
    inset = "none" if not inset_raw or inset_raw.lower() == "none" else inset_raw

    palette = str(data.get("palette") or "").strip()[:120]
    mood = str(data.get("mood") or "").strip()[:120]

    raw_words = data.get("graphic_words") or []
    if isinstance(raw_words, str):
        raw_words = [raw_words]
    elif not isinstance(raw_words, (list, tuple)):
        raw_words = []

    corpus = f"{headline or ''} {story_text or ''}"
    norm_corpus = _normalize_token(corpus)

    valid_words: list[str] = []
    for w in raw_words:
        clean_w = str(w).strip()
        if not clean_w:
            continue
        norm_w = _normalize_token(clean_w)
        if norm_w and norm_w in norm_corpus:
            if clean_w not in valid_words:
                valid_words.append(clean_w)
            if len(valid_words) == 3:
                break

    return PosterConcept(
        key_moment=key_moment,
        scene=scene,
        inset=inset,
        graphic_words=tuple(valid_words),
        palette=palette,
        mood=mood,
    )


def build_concept(
    brief: PosterBrief,
    selection: PhotoSelection,
    *,
    headline: str | None = None,
    story_text: str | None = None,
) -> PosterConcept | None:
    """Generate a story-specific visual concept before image generation."""
    prompt = _build_concept_prompt(brief, selection, headline=headline, story_text=story_text)

    def parse_fn(raw: str) -> PosterConcept:
        return parse_concept_json(raw, headline=headline, story_text=story_text)

    try:
        concept, _provider = _generate_with_fallback(
            prompt,
            parse_fn,
            context="pro_poster_concept",
        )
        return concept
    except Exception as exc:
        logger.warning("Failed to build poster concept: %s", exc)
        return None
