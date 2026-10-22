"""Vision QA of the generated poster (fail closed).

Checks facial identity against the hero reference photo, then verifies
event accuracy via a strict JSON Gemini Flash vision call.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.services import design_images, identity_check
from app.services.pro_poster.types import PosterBrief, QAResult
from app.services.vision_retry import run_vision_check

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
        def _do_event_check() -> tuple[bool, str]:
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
            return wrong_event, reason

        wrong_event, reason = run_vision_check(
            _do_event_check,
            label="pro_poster_event_check",
        )
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
