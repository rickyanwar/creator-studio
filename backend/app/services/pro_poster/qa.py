"""Vision QA of the generated poster (fail closed).

Checks facial identity against the hero reference photo, then verifies poster quality,
event accuracy, AI artifacts, composition safe zones, text cleanliness, and subject count
via a strict JSON Gemini Flash vision call.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.services import design_images, identity_check
from app.services.pro_poster.types import ArtDirection, PosterBrief, QAResult

logger = logging.getLogger(__name__)


def check_safety(
    image_bytes: bytes,
    brief: PosterBrief,
    *,
    hero_ref_bytes: bytes | None = None,
    headline: str | None = None,
) -> QAResult:
    """Free safety checks (identity via SFace/Gemini + event accuracy via Gemini Flash).

    Fails closed on wrong person, wrong series/vehicle, or unreadable response.
    Never retries; fails to typographic card fallback.
    """
    hero = next((s for s in brief.subjects if s.role == "hero"), None)
    identity_passed: bool | None = None

    if hero_ref_bytes is not None and hero is not None:
        identity_sface: float | None = None
        identity_reason = ""
        try:
            identity = identity_check.check_identity(
                hero_ref_bytes,
                image_bytes,
                subject=hero.name,
                context="pro_poster",
            )
            identity_passed = identity.passed
            identity_reason = identity.reason
            identity_sface = identity.sface
        except Exception as exc:
            logger.warning("pro_poster QA identity check error: %s", exc)
            identity_passed = False
            identity_reason = f"error: {exc}"
            identity_sface = None

        if not identity_passed and identity_sface is None:
            gear_ok, gear_reason = gear_identity(hero_ref_bytes, image_bytes, hero.name)
            if gear_ok:
                identity_passed = True
            else:
                identity_passed = False
                issue_msg = f"identity: {identity_reason}; gear: {gear_reason}"
                return QAResult(
                    passed=False,
                    scores={},
                    issues=(issue_msg,),
                    fix_hint="",
                    identity_passed=False,
                )
        elif not identity_passed:
            issue_msg = f"identity: {identity_reason}" if identity_reason else "identity: verification failed"
            return QAResult(
                passed=False,
                scores={},
                issues=(issue_msg,),
                fix_hint="",
                identity_passed=False,
            )

    must_avoid_val = ", ".join(brief.must_avoid) if brief.must_avoid else "none"
    vehicle_val = brief.event.vehicle or "none"
    circuit_val = brief.event.circuit or "unknown"
    headline_val = headline or ""

    prompt = (
        f"This is a designed sports news poster. Brief: series {brief.event.series}, "
        f"vehicle {vehicle_val}, circuit/venue {circuit_val}, headline \"{headline_val}\". "
        f"Must avoid: {must_avoid_val}. "
        "Does the image show a DIFFERENT series or a different type of vehicle than the brief, "
        "an official series/league logo or wordmark on banners, screens, walls, the track, the octagon mat or fence, podium backdrops or as an added graphic (e.g. the F1, Formula 1, FIA, MotoGP, FIM, Dorna or UFC logo), "
        "or anything from the must-avoid list? "
        "A small series logo that is part of an athlete's real gear (gloves, fight shorts, race suit, helmet) is fine. "
        "Team, manufacturer and sponsor logos (e.g. Ferrari, Red Bull, Ducati, Mercedes, Monster) are fine and never make it wrong. "
        'Reply with ONLY strict JSON {"wrong_event": true|false, "reason": "<short>"}'
    )
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": design_images._vision_datauri(image_bytes)}},
    ]

    try:
        raw = design_images._vision_chat(
            content,
            max_tokens=800,
            temperature=0,
            context="pro_poster_event_check",
        )
        data = _parse_qa_json(raw)
        wrong_event = data.get("wrong_event")
        if type(wrong_event) is not bool:
            raise ValueError(f"wrong_event must be boolean, got {wrong_event!r}")
        reason = str(data.get("reason") or "").strip()
    except Exception as exc:
        logger.warning("pro_poster event check error: %s", exc)
        return QAResult(
            passed=False,
            scores={},
            issues=("event: unreadable answer",),
            fix_hint="",
            identity_passed=identity_passed,
        )

    if wrong_event:
        issue_msg = f"event: {reason}" if reason else "event: wrong event detected"
        return QAResult(
            passed=False,
            scores={},
            issues=(issue_msg,),
            fix_hint="",
            identity_passed=identity_passed,
        )

    return QAResult(
        passed=True,
        scores={},
        issues=(),
        fix_hint="",
        identity_passed=identity_passed,
    )


# Minimum acceptable scores (1-5 scale) across QA categories
MIN_SCORES: dict[str, int] = {
    "event": 4,
    "ai_look": 4,
    "composition": 3,
    "text_zone": 4,
    "single_subject": 4,
    "story_fit": 4,
}


def _parse_qa_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            data = json.loads(text[start : end + 1])
        else:
            raise
    if not isinstance(data, dict):
        raise ValueError("QA response is not a JSON object")
    return data


def _format_brief_summary(
    brief: PosterBrief,
    direction: ArtDirection | None,
    headline: str | None = None,
    story_text: str | None = None,
) -> str:
    hero = next((s.name for s in brief.subjects if s.role == "hero"), None)
    must_avoid_str = ", ".join(brief.must_avoid) if brief.must_avoid else "none"
    layout_str = direction.layout if direction else "none"
    lines = [
        f"- Series: {brief.event.series}",
        f"- Vehicle: {brief.event.vehicle or 'none'}",
        f"- Circuit: {brief.event.circuit or 'none'}",
        f"- Session: {brief.event.session or 'none'}",
        f"- Must avoid: {must_avoid_str}",
        f"- Layout: {layout_str}",
        f"- Hero name: {hero or 'none'}",
    ]
    if headline:
        lines.append(f"- Headline: {headline}")
    if story_text:
        lines.append(f"- Story facts: {story_text[:800]}")
    return "\n".join(lines)


def _build_review_prompt(
    brief: PosterBrief,
    direction: ArtDirection | None,
    headline: str | None = None,
    story_text: str | None = None,
) -> str:
    summary = _format_brief_summary(brief, direction, headline=headline, story_text=story_text)
    return (
        "You are a professional poster QA inspector for social media graphics.\n"
        "Evaluate this generated poster strictly against the brief summary below.\n\n"
        f"Brief Summary:\n{summary}\n\n"
        "Criteria to score (1-5 each):\n"
        "- event: vehicles/series/venue/time of day match the brief and nothing from must_avoid appears "
        "(5 = perfect match; 1 = wrong vehicle/series/venue or must_avoid item appears).\n"
        "- ai_look: 5 = looks like a real photo-based poster by a professional designer; "
        "1 = obvious AI (waxy/plastic skin, warped logos or numbers, melted text, extra limbs, painterly look, over-sharpened HDR).\n"
        "- composition: 5 = subject well placed, faces in the top 63 %, clean dark band in the bottom 37 %, "
        "and the top 10% is free of added text and graphics (the fan page name goes there); "
        "1 = faces/main action in bottom 37 % or poor framing.\n"
        "- text_zone: 5 = any added words or numbers are at most three short graphic words that are spelled correctly "
        "and true to the headline/story facts; real logos and numbers that are part of the photographed kit or vehicle do not count; "
        "1 = gibberish, misspelled, extra or untrue text.\n"
        "- single_subject: 5 = the hero appears only once and no unexpected extra people; 1 = duplicate hero or unwanted extra people.\n"
        "- story_fit: 5 = the image clearly tells this story: the moment, the result or what is at stake, and the hero's pose and expression match it "
        "(a winner looks like a winner, a dejected story looks dejected); 1 = generic or it contradicts the story (e.g. a winner shown beaten).\n\n"
        "Reply with ONLY a strict JSON object with this exact format:\n"
        "{\n"
        '  "event": 1-5,\n'
        '  "ai_look": 1-5,\n'
        '  "composition": 1-5,\n'
        '  "text_zone": 1-5,\n'
        '  "single_subject": 1-5,\n'
        '  "story_fit": 1-5,\n'
        '  "issues": ["short issue 1", ...],\n'
        '  "fix_hint": "one sentence fix hint or empty string"\n'
        "}"
    )


def gear_identity(ref_bytes: bytes, out_bytes: bytes, hero_name: str) -> tuple[bool, str]:
    """Compare helmet design, race number, kit, and livery when face is obscured."""
    prompt = (
        f"Image 1 is a real reference photo of {hero_name}. Image 2 is a designed poster. "
        "Is the main person in image 2 the same athlete as in image 1? "
        "Faces may be hidden by helmets, so compare the helmet design, race number, team kit or fight gear, and the vehicle livery. "
        "If a face is clearly visible in both images, the faces must also match. "
        "If unsure, answer false. "
        'Reply with ONLY strict JSON {"same_athlete": true|false, "confidence": 0-1, "notes": "<short>"}'
    )
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": design_images._vision_datauri(ref_bytes)}},
        {"type": "image_url", "image_url": {"url": design_images._vision_datauri(out_bytes)}},
    ]
    try:
        raw = design_images._vision_chat(
            content,
            max_tokens=1500,
            temperature=0,
            context="pro_poster_gear_identity",
        )
        data = _parse_qa_json(raw)
        same_athlete = data.get("same_athlete")
        confidence = data.get("confidence")
        notes = str(data.get("notes") or "").strip()

        if type(same_athlete) is not bool:
            return False, f"error: same_athlete must be boolean, got {same_athlete!r}"

        if not isinstance(confidence, (int, float)):
            return False, f"error: confidence must be numeric, got {confidence!r}"

        if same_athlete is True and confidence >= 0.7:
            return True, notes or "match confirmed"

        if same_athlete is True and confidence < 0.7:
            msg = f"confidence {confidence} < 0.7"
            if notes:
                msg = f"{msg} ({notes})"
            return False, msg

        return False, notes or f"same_athlete={same_athlete}"
    except Exception as exc:
        logger.warning("pro_poster gear_identity error: %s", exc)
        return False, f"error: {exc}"


def _build_typographic_prompt(brief: PosterBrief) -> str:
    must_avoid_str = ", ".join(brief.must_avoid) if brief.must_avoid else "none"
    summary = (
        f"- Series: {brief.event.series}\n"
        f"- Vehicle: {brief.event.vehicle or 'none'}\n"
        f"- Circuit: {brief.event.circuit or 'none'}\n"
        f"- Session: {brief.event.session or 'none'}\n"
        f"- Must avoid: {must_avoid_str}"
    )
    return (
        "You are a professional poster QA inspector evaluating a typographic fallback card background.\n"
        "Check that this background image is suitable for text overlay.\n\n"
        f"Brief Summary:\n{summary}\n\n"
        "Criteria to score (1-5 each):\n"
        "- composition: 5 = clean background layout, clean dark band in bottom 37 %; 1 = cluttered or messy.\n"
        "- text_zone: 5 = no added text/letters anywhere; 1 = visible invented or gibberish text.\n\n"
        "Reply with ONLY a strict JSON object with this exact format:\n"
        "{\n"
        '  "composition": 1-5,\n'
        '  "text_zone": 1-5,\n'
        '  "issues": ["short issue 1", ...],\n'
        '  "fix_hint": "one sentence fix hint or empty string"\n'
        "}"
    )


def review(
    image_bytes: bytes,
    brief: PosterBrief,
    direction: ArtDirection | None = None,
    *,
    hero_ref_bytes: bytes | None = None,
    headline: str | None = None,
    story_text: str | None = None,
) -> QAResult:
    """Vision QA review of generated poster image.

    Checks identity first if hero_ref_bytes and a hero subject are provided.
    If check_identity does not pass and sface is None, falls back to gear_identity.
    Then performs a single vision QA call to score event, ai_look, composition,
    text_zone, single_subject, and story_fit against MIN_SCORES.
    """
    hero = next((s for s in brief.subjects if s.role == "hero"), None)
    identity_passed: bool | None = None

    if hero_ref_bytes is not None and hero is not None:
        identity_sface: float | None = None
        identity_reason = ""
        try:
            identity = identity_check.check_identity(
                hero_ref_bytes,
                image_bytes,
                subject=hero.name,
                context="pro_poster",
            )
            identity_passed = identity.passed
            identity_reason = identity.reason
            identity_sface = identity.sface
        except Exception as exc:
            logger.warning("pro_poster QA identity check error: %s", exc)
            identity_passed = False
            identity_reason = f"error: {exc}"
            identity_sface = None

        if not identity_passed and identity_sface is None:
            gear_ok, gear_reason = gear_identity(hero_ref_bytes, image_bytes, hero.name)
            if gear_ok:
                identity_passed = True
            else:
                identity_passed = False
                issue_msg = f"identity: {identity_reason}; gear: {gear_reason}"
                return QAResult(
                    passed=False,
                    scores={},
                    issues=(issue_msg,),
                    fix_hint="Keep the hero's face exactly as in Image 1.",
                    identity_passed=False,
                )
        elif not identity_passed:
            issue_msg = f"identity: {identity_reason}" if identity_reason else "identity: verification failed"
            return QAResult(
                passed=False,
                scores={},
                issues=(issue_msg,),
                fix_hint="Keep the hero's face exactly as in Image 1.",
                identity_passed=False,
            )

    prompt = _build_review_prompt(brief, direction, headline=headline, story_text=story_text)
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": design_images._vision_datauri(image_bytes)}},
    ]

    try:
        raw = design_images._vision_chat(content, max_tokens=1500, temperature=0, context="pro_poster_qa")
        data = _parse_qa_json(raw)
        scores: dict[str, int] = {}
        for k in MIN_SCORES:
            if k not in data:
                raise KeyError(f"missing QA score key: {k}")
            val = int(data[k])
            if not (1 <= val <= 5):
                raise ValueError(f"score {k} out of range: {val}")
            scores[k] = val
    except Exception as exc:
        logger.warning("pro_poster QA vision parse error: %s", exc)
        return QAResult(
            passed=False,
            scores={},
            issues=("qa: unreadable answer",),
            fix_hint="",
            identity_passed=identity_passed,
        )

    scores_passed = all(scores[k] >= MIN_SCORES[k] for k in MIN_SCORES)
    raw_issues = data.get("issues")
    issues_list = [str(x) for x in raw_issues] if isinstance(raw_issues, (list, tuple)) else []
    for k, min_val in MIN_SCORES.items():
        if scores[k] < min_val:
            if not any(k in x.lower() for x in issues_list):
                issues_list.append(f"{k} score {scores[k]} < {min_val}")

    fix_hint = str(data.get("fix_hint") or "").strip()
    passed = bool((identity_passed is not False) and scores_passed)

    return QAResult(
        passed=passed,
        scores=scores,
        issues=tuple(issues_list),
        fix_hint=fix_hint,
        identity_passed=identity_passed,
    )


def review_typographic(image_bytes: bytes, brief: PosterBrief) -> QAResult:
    """Lighter QA check for typographic fallback card backgrounds (composition & text_zone)."""
    prompt = _build_typographic_prompt(brief)
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": design_images._vision_datauri(image_bytes)}},
    ]

    required_keys = ("composition", "text_zone")
    try:
        raw = design_images._vision_chat(
            content, max_tokens=1000, temperature=0, context="pro_poster_qa_typographic"
        )
        data = _parse_qa_json(raw)
        scores: dict[str, int] = {}
        for k in required_keys:
            if k not in data:
                raise KeyError(f"missing QA typographic score key: {k}")
            val = int(data[k])
            if not (1 <= val <= 5):
                raise ValueError(f"score {k} out of range: {val}")
            scores[k] = val
    except Exception as exc:
        logger.warning("pro_poster typographic QA vision parse error: %s", exc)
        return QAResult(
            passed=False,
            scores={},
            issues=("qa: unreadable answer",),
            fix_hint="",
            identity_passed=None,
        )

    scores_passed = all(scores[k] >= MIN_SCORES[k] for k in required_keys)
    raw_issues = data.get("issues")
    issues_list = [str(x) for x in raw_issues] if isinstance(raw_issues, (list, tuple)) else []
    for k in required_keys:
        if scores[k] < MIN_SCORES[k]:
            if not any(k in x.lower() for x in issues_list):
                issues_list.append(f"{k} score {scores[k]} < {MIN_SCORES[k]}")

    fix_hint = str(data.get("fix_hint") or "").strip()
    return QAResult(
        passed=scores_passed,
        scores=scores,
        issues=tuple(issues_list),
        fix_hint=fix_hint,
        identity_passed=None,
    )
