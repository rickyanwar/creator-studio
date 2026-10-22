"""Double face check for gpt-cli images (owner plan 2026-10-17): local SFace first, then Gemini (FlashFast)
always. Fail closed: unsure or error = reject, and the caller falls back to Codex."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Literal

from app.services import ai_caption, design_images, speaker_design, vision_retry

SFACE_REJECT = 0.25          # face clearly found in the output and score below this → reject without Gemini
GEMINI_MIN_CONFIDENCE = 0.6


@dataclass(frozen=True)
class IdentityResult:
    passed: bool
    sface: float | None      # None = no usable face on either side / model unavailable
    gemini: Literal["yes", "no", "error", "skipped"]
    reason: str


def check_identity(
    ref_bytes: bytes,
    out_bytes: bytes,
    *,
    subject: str,
    context: str,
    fanpage_id: int | None = None,
) -> IdentityResult:
    t0 = time.monotonic()
    s: float | None = None
    passed = False
    gemini: Literal["yes", "no", "error", "skipped"] = "error"
    reason = ""
    models_tried: list[str] = ["sface"]

    try:
        # Step 1: Local SFace check
        try:
            model = speaker_design._get_sface_model()
            ref_face = speaker_design.face_info(ref_bytes)
            out_face = speaker_design.face_info(out_bytes)
            if model is not None and ref_face is not None and out_face is not None:
                s = float(speaker_design.identity_score(ref_bytes, out_bytes))
            else:
                s = None
        except Exception:
            s = None

        # Step 2: Clear reject threshold
        if s is not None and s < SFACE_REJECT:
            passed = False
            gemini = "skipped"
            reason = f"sface {s:.2f} < {SFACE_REJECT}"
            models_tried = ["sface"]
        else:
            # Step 3: Vision (Gemini FlashFast)
            models_tried = ["sface", "FlashFast"]
            try:
                def _do_vision_check() -> tuple[bool, float, str]:
                    prompt = (
                        f"image 1 is the real reference photo of {subject}; "
                        "image 2 is a generated graphic; "
                        f"compare the face of the main person in image 2 with {subject} in image 1; "
                        "ignore clothing, helmet, cap, background, colour grade and style; "
                        "if a helmet/visor hides most of the face, judge only the visible features; "
                        "if unsure answer false; "
                        'reply with ONLY strict JSON {"same_person": true|false, "confidence": 0-1, "notes": "<short>"}'
                    )
                    content = [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": design_images._vision_datauri(ref_bytes)}},
                        {"type": "image_url", "image_url": {"url": design_images._vision_datauri(out_bytes)}},
                    ]
                    raw = design_images._vision_chat(
                        content,
                        max_tokens=1500,
                        temperature=0,
                        context="visual_identity",
                    )
                    m = re.search(r"\{.*\}", raw, re.DOTALL)
                    if not m:
                        raise ValueError("no json object found")
                    try:
                        parsed = json.loads(m.group(0))
                    except json.JSONDecodeError:
                        m_first = re.search(r"\{.*?\}", raw, re.DOTALL)
                        if not m_first:
                            raise
                        parsed = json.loads(m_first.group(0))

                    if not isinstance(parsed, dict):
                        raise ValueError("json is not an object")

                    same_person = parsed.get("same_person")
                    if type(same_person) is not bool:
                        raise ValueError(f"same_person must be boolean, got {type(same_person).__name__}")

                    confidence = parsed.get("confidence")
                    if confidence is None or isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                        raise ValueError("confidence must be a number")
                    confidence = float(confidence)

                    notes = str(parsed.get("notes") or "").strip()[:120]
                    return same_person, confidence, notes

                same_person, confidence, notes = vision_retry.run_vision_check(
                    _do_vision_check,
                    label=f"identity:{subject}",
                )
                passed = same_person is True and confidence >= GEMINI_MIN_CONFIDENCE
                gemini = "yes" if same_person is True else "no"
                reason = f"gemini same_person={same_person} conf={confidence:.2f}" + (f" {notes}" if notes else "")
            except Exception as exc:
                passed = False
                gemini = "error"
                reason = f"gemini error: {type(exc).__name__}"

    except Exception as exc:
        passed = False
        gemini = "error"
        reason = f"unexpected error: {type(exc).__name__}"

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    s_text = f"{s:.2f}" if s is not None else "none"

    try:
        ai_caption.log_ai_copy_event(
            context="image_identity",
            fanpage_id=fanpage_id,
            article_id=None,
            outcome="success" if passed else "failed",
            models_tried=models_tried,
            final_provider="gptcli",
            error_message=f"sface={s_text} gemini={gemini} {reason} ctx={context}",
            latency_ms=elapsed_ms,
        )
    except Exception:
        pass

    return IdentityResult(
        passed=passed,
        sface=s,
        gemini=gemini,
        reason=reason,
    )
