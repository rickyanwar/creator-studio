"""AI highlight search over a YouTube transcript (Mode 7), via 9Router.

The prompt, the JSON-salvage parser and the clip-count/duration rules are
ported from jipraks/yt-short-clipper (yt_short_clipper_core/prompts.py and
highlight_finder.py):

    MIT License — Copyright (c) 2026 Aji Prakoso (jipraks)
    Permission is hereby granted, free of charge, to any person obtaining a
    copy of this software and associated documentation files (the
    "Software"), to deal in the Software without restriction, including
    without limitation the rights to use, copy, modify, merge, publish,
    distribute, sublicense, and/or sell copies of the Software, and to permit
    persons to whom the Software is furnished to do so, subject to the
    following conditions: The above copyright notice and this permission
    notice shall be included in all copies or substantial portions of the
    Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.

Changes from the original: a per-fanpage niche persona instead of podcasts;
clip length from the fanpage's settings; and — the big one — clips the model
returns off-length are FITTED at speech pauses (trimmed/extended) instead of
dropped. Found on a real sparse-commentary video (2026-09-25): only 3 of 8
picks landed inside 60-120s, one ran 185s.
"""

import json
import logging
import time
from dataclasses import dataclass

from app.services.yt_transcript import Word, nearest_pause, parse_timestamp, pause_points, text_between

logger = logging.getLogger(__name__)

# Measured 2026-09-25 on an 87k-char prompt: sonnet-4-6 24s (8/8 in range),
# opus-4-6-thinking 24s; the configured default (My-Combo) and
# gpt-oss-120b both timed out at 240s — so this chain never starts at the
# default model, and skips the one that can't handle long prompts.
_PREFERRED_MODELS = ("ag/claude-sonnet-4-6", "ag/claude-opus-4-6-thinking")
_SKIPPED_MODELS = {"ag/gpt-oss-120b-medium"}
_MODEL_TIMEOUT_S = 180.0
_TOTAL_BUDGET_S = 720.0   # stop trying new models after this — the video worker is single-slot
_TEMPERATURE = 0.4
_EXTRA_CANDIDATES = 3     # over-request, since fitting/overlap can drop some
_MIN_GAP_S = 2.0          # between two accepted clips from the same video

LANGUAGE_NAMES = {
    "en": "English", "id": "Indonesian", "ms": "Malay", "es": "Spanish", "pt": "Portuguese",
    "fr": "French", "de": "German", "it": "Italian", "nl": "Dutch", "ar": "Arabic", "tr": "Turkish",
    "th": "Thai", "vi": "Vietnamese", "tl": "Filipino", "ja": "Japanese", "ko": "Korean", "zh": "Chinese",
    "hi": "Hindi", "ru": "Russian", "pl": "Polish",
}


@dataclass(frozen=True)
class Highlight:
    start: float
    end: float
    title: str
    description: str
    hook_text: str
    score: int
    excerpt: str


@dataclass(frozen=True)
class ClipRules:
    count: int
    min_s: int
    max_s: int
    min_score: int


def language_name(code: str | None) -> str:
    return LANGUAGE_NAMES.get((code or "en").split("-")[0].lower(), code or "English")


_PROMPT = """You are a top-tier short-form editor for the Facebook page "{page}" ({niche} niche), cutting viral clips from YouTube videos for Reels, TikTok and Shorts.

Your output goes straight into production. A duration or format mistake fails the entire job.

==================================================
TASK (NON-NEGOTIABLE)
=====================

From the transcript below, return EXACTLY {num_clips} segments.

* Never fewer. Never more. An empty array is forbidden.

If strong moments are hard to find, still return {num_clips} by merging adjacent moments or extending them with surrounding context.
{direction}
==================================================
OUTPUT LANGUAGE (DO NOT GET THIS WRONG)
=======================================

* "title", "description" and "hook_text" MUST be written in casual {language}, in an everyday spoken register.
* If {language} is the language spoken in the transcript, keep the speaker's own words when you quote them.
* Otherwise translate naturally into {language}. Never mix two languages inside one field.
* Field names, JSON structure and timestamp format stay exactly as specified, in English.

==================================================
HOW TO PICK CLIPS
=================

Prefer segments that have:
1. Conflict, tension, controversy or drama.
2. A personal confession or a moment of vulnerability.
3. A sharp statement or a bold opinion.
4. A punchline or a genuinely funny beat.
5. A complete story arc: setup -> build-up -> payoff.
6. A line that stands on its own as a viral hook.

Avoid filler chatter, topic transitions with no payoff, and long technical explanations with no emotion. When forced to choose, take EMOTION and CONFLICT over neutral education.

SEARCH THE WHOLE TRANSCRIPT: read it from the first line to the last before deciding. The best moments are rarely all near the start. Spread the picks across the full runtime. Segments must never overlap.

==================================================
DURATION (CRITICAL)
===================

* Every clip MUST run {min_s}-{max_s} seconds. Aim for about {aim_s} seconds.
* Compute duration from the transcript timestamps — never estimate it from how much text a segment contains.
* Shorter than {min_s}s -> extend it with context. Longer than {max_s}s -> trim the irrelevant edges without breaking the story.
* Copy timestamps from the transcript's own time markers. Do not invent times that never appear in it.

==================================================
REQUIRED FIELDS (EXACTLY 6)
===========================

1. "start_time"     (string)  -> "HH:MM:SS,mmm"
2. "end_time"       (string)  -> "HH:MM:SS,mmm"
3. "title"          (string)  -> max 60 characters, {language}, click-worthy
4. "description"    (string)  -> max 150 characters, {language}, why it travels
5. "virality_score" (integer) -> 1-10, a bare number
6. "hook_text"      (string)  -> max 15 words, {language}, names the person speaking; a quote or sharp statement, never a summary; no emoji

No extra fields, no prose outside the JSON.

==================================================
SCORING virality_score
======================

8-10: controversial, strongly emotional, a confession, a bold statement, or a hard punchline.
5-7:  an interesting insight, a reasonably engaging story, a light laugh.
1-4:  ordinary information, no emotion, no strong hook.
Spread the scores honestly — they decide which clips actually get produced.

==================================================
OUTPUT FORMAT (STRICT)
======================

Return ONLY a JSON array. Never use a double quote (") inside a field value — quote people with single quotes ('). One stray double quote breaks the ENTIRE response. No line breaks inside values.

[{{"start_time":"HH:MM:SS,mmm","end_time":"HH:MM:SS,mmm","title":"...","description":"...","virality_score":8,"hook_text":"..."}}]

==================================================
SOURCE
======

{context}

Transcript:
{transcript}

==================================================
FINAL CHECK
===========

1. Exactly {num_clips} objects. 2. Every clip runs {min_s}-{max_s} seconds by its own timestamps. 3. No overlaps, spread across the video. 4. title/description/hook_text in {language}. 5. Exactly the 6 fields, virality_score a bare integer. 6. No double quote inside any value.

Answer with the JSON array and nothing else."""

_DIRECTION_BLOCK = """
==================================================
DIRECTION FOR THIS SOURCE (HIGHEST PRIORITY)
============================================

{text}

Follow it as closely as the transcript allows when choosing MOMENTS. It does not change the clip count, the duration rule or the output format.
"""


def build_prompt(*, page: str, niche: str, language: str, context: str, transcript: str,
                 rules: ClipRules, direction: str | None) -> str:
    direction_text = " ".join((direction or "").split())[:500]
    return _PROMPT.format(
        page=page, niche=niche, language=language, context=context,
        transcript=transcript,
        num_clips=rules.count + _EXTRA_CANDIDATES,
        min_s=rules.min_s, max_s=rules.max_s, aim_s=(rules.min_s + rules.max_s) // 2,
        direction=_DIRECTION_BLOCK.format(text=direction_text) if direction_text else "",
    )


def parse_highlights_json(raw: str) -> list[dict]:
    """The model's JSON array, salvaging what's readable when one object is
    broken (an unescaped quote typically) — we over-request, so dropping one
    malformed clip beats failing the run. Raises ValueError if nothing is
    recoverable."""
    text = raw.strip()
    start = text.find("[")
    if start != -1:
        try:
            parsed = json.loads(text[start:text.rfind("]") + 1])
            if isinstance(parsed, list):
                return [c for c in parsed if isinstance(c, dict)]
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    recovered: list[dict] = []
    index = 0
    while (brace := text.find("{", index)) != -1:
        try:
            obj, end = decoder.raw_decode(text, brace)
        except json.JSONDecodeError:
            index = brace + 1
            continue
        if isinstance(obj, dict) and "start_time" in obj:
            recovered.append(obj)
        index = end
    if not recovered:
        raise ValueError(f"no clip objects in AI output ({len(raw)} chars): {raw[:200]!r}")
    return recovered


def fit_clip(start: float, end: float, pauses: list[float], video_s: float, rules: ClipRules) -> tuple[float, float] | None:
    """Snap a model-picked range onto speech pauses and into [min_s, max_s]:
    a start at the nearest pause just before/after it, an end at a pause
    that lands the length in range (trimming a long pick, extending a short
    one). None if the range can't be made valid."""
    start, end = max(0.0, start), min(video_s, end)
    if end - start < 5:
        return None
    snapped = nearest_pause(pauses, start, start - 4, start + 3)
    start = snapped if snapped is not None else start
    lo, hi = start + rules.min_s, start + rules.max_s
    duration = end - start
    if duration > rules.max_s:
        target = hi - 3
    elif duration < rules.min_s:
        target = lo + 3
    else:
        target = end
    snapped_end = nearest_pause(pauses, target, lo, min(hi, video_s))
    end = snapped_end if snapped_end is not None else min(max(end, lo), hi)
    if end > video_s:
        start, end = max(0.0, video_s - (end - start)), video_s
    return (start, end) if rules.min_s - 1 <= end - start <= rules.max_s + 0.5 else None


def validate(raw_clips: list[dict], words: list[Word], video_s: float, rules: ClipRules) -> list[Highlight]:
    pauses = pause_points(words)
    fitted: list[Highlight] = []
    for c in raw_clips:
        try:
            s, e = parse_timestamp(str(c["start_time"])), parse_timestamp(str(c["end_time"]))
            score = int(str(c.get("virality_score", 5)).strip() or 5)
        except (KeyError, ValueError):
            continue
        span = fit_clip(s, e, pauses, video_s, rules)
        title = " ".join(str(c.get("title") or "").split())[:120]
        if not span or not title:
            continue
        fitted.append(Highlight(
            start=round(span[0], 2), end=round(span[1], 2), title=title,
            description=" ".join(str(c.get("description") or "").split())[:300],
            hook_text=" ".join(str(c.get("hook_text") or "").split())[:200],
            score=max(1, min(10, score)),
            excerpt=text_between(words, span[0], span[1])[:1500],
        ))
    accepted: list[Highlight] = []
    for h in sorted(fitted, key=lambda h: -h.score):
        if h.score < rules.min_score or len(accepted) >= rules.count:
            continue
        if all(h.end + _MIN_GAP_S <= a.start or h.start >= a.end + _MIN_GAP_S for a in accepted):
            accepted.append(h)
    return sorted(accepted, key=lambda h: h.start)


def _model_chain() -> list[str]:
    from app.services.ai_caption import ROUTER_MODEL_FALLBACKS

    rest = [m for m in ROUTER_MODEL_FALLBACKS if m not in _PREFERRED_MODELS and m not in _SKIPPED_MODELS]
    return list(_PREFERRED_MODELS) + rest


def find_highlights(prompt: str, words: list[Word], video_s: float, rules: ClipRules,
                    fanpage_id: int | None = None) -> list[Highlight]:
    """Run the prompt down the model chain until one answer parses. Returns
    the validated highlights (possibly empty — a parseable answer with no
    usable clip means the video has nothing worth clipping, not that the
    next model should be paid to try). Raises RuntimeError if every model
    errors or returns garbage within the time budget."""
    from app.services.ai_caption import call_router_model, log_ai_copy_event

    started = time.monotonic()
    tried: list[str] = []
    last_error = "no model tried"
    for model in _model_chain():
        if time.monotonic() - started > _TOTAL_BUDGET_S:
            last_error = f"time budget ({_TOTAL_BUDGET_S:.0f}s) exhausted; last error: {last_error}"
            break
        tried.append(model)
        try:
            raw = call_router_model(prompt, model, timeout=_MODEL_TIMEOUT_S, temperature=_TEMPERATURE)
            highlights = validate(parse_highlights_json(raw), words, video_s, rules)
        except Exception as exc:
            last_error = f"{model}: {exc}"
            logger.warning("yt_highlights: %s", last_error[:300])
            continue
        log_ai_copy_event(
            context="yt_highlight", fanpage_id=fanpage_id, article_id=None,
            outcome="success" if len(tried) == 1 else "recovered", models_tried=tried,
            final_provider="router", error_message=None if len(tried) == 1 else last_error,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        logger.info("yt_highlights: %s → %d usable clip(s) in %.0fs", model, len(highlights), time.monotonic() - started)
        return highlights
    log_ai_copy_event(
        context="yt_highlight", fanpage_id=fanpage_id, article_id=None, outcome="failed",
        models_tried=tried, final_provider=None, error_message=last_error,
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    raise RuntimeError(f"highlight search failed: {last_error}")
