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
from app.services.ai_video_qc import contact_sheet
from app.services.vision_retry import run_vision_check

logger = logging.getLogger(__name__)

CLIP_QC_MAX_ATTEMPTS = 4
CLIP_QC_WAIT_RETRY_S = 120
CLIP_QC_WAIT_LIMIT_S = 6 * 3600


def clip_qc_dir() -> Path:
    path = Path(get_settings().storage_base_path) / "ai_videos" / "clip_qc"
    path.mkdir(parents=True, exist_ok=True)
    return path


def clip_qc_path(job_id: int, generation: int, clip_index: int, attempt: int) -> Path:
    return clip_qc_dir() / f"job{job_id}_g{generation}_c{clip_index}_a{attempt}.mp4"


def last_frame_jpeg(path: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".jpg") as output:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.1", "-i", path,
                        "-frames:v", "1", "-q:v", "3", output.name], check=True, timeout=60)
        return Path(output.name).read_bytes()


def build_logic_prompt(clip_prompt: str, *, has_previous_frame: bool) -> str:
    previous = " The last image is the last frame of the previous clip; this clip continues from it." if has_previous_frame else ""
    previous_livery = " or from the previous clip's last frame" if has_previous_frame else ""
    return f'''You check ONE 10-second clip of an AI-generated vertical video for LOGIC errors only. The image grid shows frames of the clip in time order, left to right, top row then bottom row.{previous}
The clip was generated from this prompt:
<<<
{clip_prompt}
>>>
Report only clear, visible logic errors of these kinds:
1. A vehicle drives backwards, slides sideways, or reverses its direction of travel between frames.
2. A vehicle, rider, person or object changes shape or size, gains or loses wheels or parts, or merges with another object.
3. Something floats, passes through a solid object, or appears or vanishes without a cause.
4. A tyre leaves the ground while the vehicle drives normally, or a motorcycle leans to the wrong side for its turn.
5. The livery, colours or lettering differ from the prompt{previous_livery}.
6. A mechanism moves in a way the prompt does not describe, or a part the prompt says stays still moves.
7. An official series or governing-body logo appears (F1, FIA, MotoGP, FIM, Dorna, UFC). Team and sponsor logos are allowed.
Never report these, they are intended or not logic: X-ray or semi-transparent cutaways, exploded views where parts separate, float and then reassemble, glowing lines, slow motion, motion blur, depth of field, film grain, camera moves, hard cuts when the prompt asks for cuts, small soft unreadable lettering, lighting, colour grading, beauty or style, and anything you cannot see clearly.
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
    content = [{"type": "text", "text": build_logic_prompt(clip_prompt, has_previous_frame=bool(previous_clip_path))},
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
