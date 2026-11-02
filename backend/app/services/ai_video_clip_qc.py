"""Per-clip logic QC for Mode 8 videos."""

from __future__ import annotations

import io
import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.services.ai_video_qc import _duration, contact_sheet, contact_sheet_times
from app.services.vision_retry import run_vision_check

logger = logging.getLogger(__name__)

CLIP_QC_MAX_ATTEMPTS = 4
CLIP_QC_WAIT_RETRY_S = 120
CLIP_QC_WAIT_LIMIT_S = 6 * 3600


def max_attempts_for_job(job) -> int:
    return job.clip_qc_max_attempts or CLIP_QC_MAX_ATTEMPTS


def clip_qc_dir() -> Path:
    path = Path(get_settings().storage_base_path) / "ai_videos" / "clip_qc"
    path.mkdir(parents=True, exist_ok=True)
    return path


def clip_qc_path(job_id: int, generation: int, clip_index: int, attempt: int) -> Path:
    return clip_qc_dir() / f"job{job_id}_g{generation}_c{clip_index}_a{attempt}.mp4"


def clip_qc_sheet_path(clip_path) -> Path:
    """The contact sheet QC looked at, saved next to the attempt clip."""
    path = Path(clip_path)
    return path.with_name(f"{path.stem}_sheet.jpg")


def last_frame_jpeg(path: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".jpg") as output:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.1", "-i", path,
                        "-frames:v", "1", "-q:v", "3", output.name], check=True, timeout=60)
        return Path(output.name).read_bytes()


def _frame_times_line(frame_times: list[float] | None) -> str:
    if not frame_times:
        return ""
    times = ", ".join(f"frame {index} = {seconds:.1f} s" for index, seconds in enumerate(frame_times, start=1))
    return f"\nFrame times: {times}."


# Owner 2026-11-01: hard cuts between the prompt's shots were reported as reversals and vanished parts.
SHOT_RULES = """The prompt's [00:00-00:03]-style time marks split the clip into separate shots joined by hard cuts. Use the frame
times to see which frames belong to which shot. Between two shots the camera set-up changes on purpose: a new angle, the
other end of the vehicle, a close-up of a part, or a different framing is a cut, never a reversal, a turn, a shape
change or a vanished part. Judge direction of travel, shape and parts only between frames of the SAME shot. Across a
cut, check only that it is the same vehicle with the same livery. A part that is outside the frame or hidden by the
camera angle has not vanished.

Effects the prompt asks for are intended and never errors. When the prompt describes an X-ray, semi-transparent,
cutaway or exploded view, the parts it names may turn see-through and solid again, be hidden behind other parts,
separate, float apart, rotate, slide out beside the vehicle and come back together. As the see-through effect switches
on or off, inner parts such as gears, shafts and windings appear or disappear and the bodywork around them fades out or
back in; that is the effect, not a part vanishing or changing design.
The camera's position, angle, distance and framing are never errors either, even when they differ from the prompt."""


def build_logic_prompt(clip_prompt: str, *, has_previous_frame: bool, frame_times: list[float] | None = None) -> str:
    previous = " The last image is the last frame of the previous clip; this clip continues from it." if has_previous_frame else ""
    previous_livery = " or from the previous clip's last frame" if has_previous_frame else ""
    return f'''You check ONE 10-second clip of an AI-generated vertical video for LOGIC errors only. The image grid shows frames of the clip in time order, left to right, top row then bottom row.{previous}{_frame_times_line(frame_times)}
The clip was generated from this prompt:
<<<
{clip_prompt}
>>>
{SHOT_RULES}
Report only clear, visible logic errors of these kinds:
1. A vehicle drives backwards, slides sideways, or reverses its direction of travel between frames.
2. A vehicle, rider, person or object changes shape or size, gains or loses wheels or parts, or merges with another object.
3. Something floats, passes through a solid object, or appears or vanishes without a cause.
4. A tyre leaves the ground while the vehicle drives normally, or a motorcycle leans to the wrong side for its turn.
5. The livery, colours or lettering differ from the prompt{previous_livery}.
6. A mechanism moves in a way the prompt does not describe, or a part the prompt says stays still moves.
7. An official series or governing-body logo appears (F1, FIA, MotoGP, FIM, Dorna, UFC). Team and sponsor logos are allowed.
Never report these, they are intended or not logic: X-ray or semi-transparent cutaways, exploded views where parts separate, float and then reassemble, glowing lines, slow motion, motion blur, depth of field, film grain, camera moves, hard cuts when the prompt asks for cuts, small soft unreadable lettering, where the team name lettering is placed on the vehicle, lighting, colour grading, beauty or style, and anything you cannot see clearly.
Mark an error "major" only when an ordinary viewer would clearly notice it; otherwise mark it "minor". When you are unsure, do not report it.
Return ONLY JSON, no markdown: {{"issues":[{{"problem":"what is wrong, where, in which frames","severity":"major"}}]}} - an empty list when the clip is logical.'''


def _outer_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.splitlines()[1:-1]).strip()
    start, end, depth = cleaned.find("{"), -1, 0
    if start < 0:
        raise ValueError("no JSON object")
    for index in range(start, len(cleaned)):
        if cleaned[index] == "{": depth += 1
        elif cleaned[index] == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end < 0:
        raise ValueError("no JSON object")
    value = json.loads(cleaned[start:end])
    if not isinstance(value, dict):
        raise ValueError("JSON is not an object")
    return value


def parse_logic_answer(text: str) -> list[dict]:
    issues = _outer_json(text).get("issues")
    if not isinstance(issues, list):
        raise ValueError("issues is not a list")
    return [item for item in issues if isinstance(item, dict) and isinstance(item.get("problem"), str)
            and item.get("severity") in ("major", "minor")]


def logic_check(clip_path: str, clip_prompt: str, previous_clip_path: str | None) -> list[dict]:
    from app.services.design_images import _vision_chat, _vision_datauri

    sheet = contact_sheet(clip_path, n=8)
    try:
        clip_qc_sheet_path(clip_path).write_bytes(sheet)
    except OSError:
        logger.warning("Could not save the QC contact sheet for %s", clip_path, exc_info=True)
    frame_times = contact_sheet_times(_duration(clip_path), n=8)
    prompt = build_logic_prompt(clip_prompt, has_previous_frame=bool(previous_clip_path), frame_times=frame_times)
    content = [{"type": "text", "text": prompt},
               {"type": "image_url", "image_url": {"url": _vision_datauri(sheet, max_dim=1024)}}]
    if previous_clip_path:
        content.append({"type": "image_url", "image_url": {"url": _vision_datauri(last_frame_jpeg(previous_clip_path), max_dim=1024)}})

    def check():
        return parse_logic_answer(_vision_chat(content, max_tokens=1500, context="ai_video_clip_qc"))

    return run_vision_check(check, label="ai_video_clip_qc")


def best_attempt(rows):
    return min(rows, key=lambda row: (row.major_count or 0, row.minor_count or 0, row.attempt))


def accept_attempt(db, job, row, *, accepted_best: bool):
    clips = []
    for clip in job.clips or []:
        value = dict(clip)
        if value.get("index") == row.clip_index:
            value["prompt"] = row.prompt
            value["qc"] = {"attempt": row.attempt, "issues": row.issues or [], "accepted_best": accepted_best}
        clips.append(value)
    job.clips = clips
    flag_modified(job, "clips")


def clip_qc_history(db, job) -> dict:
    from app.models.ai_video import VideoClipQc

    rows = (
        db.query(VideoClipQc)
        .filter(VideoClipQc.video_job_id == job.id)
        .order_by(
            VideoClipQc.generation.desc(),
            VideoClipQc.clip_index.asc(),
            VideoClipQc.attempt.asc(),
        )
        .all()
    )

    base = (get_settings().storage_base_url or "http://localhost/media").rstrip("/")
    grouped: dict[tuple[int, int], list[VideoClipQc]] = {}
    for row in rows:
        grouped.setdefault((row.generation, row.clip_index), []).append(row)

    clips = []
    for (gen, c_idx), attempts in grouped.items():
        current_gen = (gen == job.generation)

        pass_row = next((r for r in attempts if r.verdict == "pass"), None)
        if pass_row is not None:
            used_attempt = pass_row.attempt
        else:
            use_attempt_row = next((r for r in attempts if r.verdict == "use_attempt"), None)
            used_attempt = use_attempt_row.use_attempt if use_attempt_row is not None else None

        if not current_gen:
            outcome = "discarded"
        elif any(r.verdict == "pass" for r in attempts):
            outcome = "passed"
        elif any(r.verdict == "use_attempt" for r in attempts):
            outcome = "kept_best"
        elif any(r.verdict == "failed" for r in attempts):
            outcome = "failed"
        else:
            outcome = "pending"

        serialized_attempts = []
        for row in attempts:
            clip_p = Path(row.clip_path) if row.clip_path else None
            clip_url = f"{base}/ai_videos/clip_qc/{clip_p.name}" if clip_p and clip_p.exists() else None

            sheet_p = clip_qc_sheet_path(row.clip_path) if row.clip_path else None
            sheet_url = f"{base}/ai_videos/clip_qc/{sheet_p.name}" if sheet_p and sheet_p.exists() else None

            serialized_attempts.append({
                "id": row.id,
                "attempt": row.attempt,
                "status": row.status,
                "verdict": row.verdict,
                "major_count": row.major_count,
                "minor_count": row.minor_count,
                "issues": row.issues or [],
                "prompt": row.prompt,
                "next_prompt": row.next_prompt,
                "waiting_reason": row.waiting_reason,
                "clip_url": clip_url,
                "sheet_url": sheet_url,
                "used_in_video": bool(current_gen and used_attempt is not None and row.attempt == used_attempt),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            })

        clips.append({
            "generation": gen,
            "clip_index": c_idx,
            "current_generation": current_gen,
            "outcome": outcome,
            "used_attempt": used_attempt,
            "attempts": serialized_attempts,
        })

    return {
        "job_id": job.id,
        "generation": job.generation,
        "max_attempts": max_attempts_for_job(job),
        "clips": clips,
    }

