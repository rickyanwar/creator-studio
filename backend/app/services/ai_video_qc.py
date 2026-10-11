"""Technical and optional vision quality checks for Mode 8 videos."""

import io
import json
import logging
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


def _probe(path: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return json.loads(result.stdout)


def _duration(path: str) -> float:
    probe = _probe(path)
    return float(probe.get("format", {}).get("duration") or 0)


def _filter_duration(path: str, filter_name: str, options: str) -> float:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-vf", f"{filter_name}={options}", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300,
    )
    pattern = r"(?:black_duration|freeze_duration):([0-9.]+)"
    return sum(float(match) for match in re.findall(pattern, result.stderr))


def technical_check(path: str, clips: int, clip_seconds: int, aspect: str) -> dict:
    result = {
        "passed": False, "reasons": [], "duration_s": 0.0, "width": 0, "height": 0,
        "has_audio": False, "black_s": 0.0, "frozen_s": 0.0, "size_bytes": 0,
    }
    try:
        result["size_bytes"] = Path(path).stat().st_size
        probe = _probe(path)
        streams = probe.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        result["has_audio"] = any(s.get("codec_type") == "audio" for s in streams)
        if not video:
            result["reasons"].append("no video stream")
            return result
        result["width"], result["height"] = int(video.get("width") or 0), int(video.get("height") or 0)
        result["duration_s"] = float(probe.get("format", {}).get("duration") or video.get("duration") or 0)
        result["black_s"] = _filter_duration(path, "blackdetect", "d=0.5:pix_th=0.10")
        result["frozen_s"] = _filter_duration(path, "freezedetect", "n=0.003:d=1.5")
    except Exception as exc:
        result["reasons"].append(f"probe/ffmpeg error: {exc}")
        return result

    expected = clips * clip_seconds
    if not expected - 3 <= result["duration_s"] <= expected + 2:
        result["reasons"].append("duration outside expected range")
    if not result["has_audio"]:
        result["reasons"].append("no audio stream")
    if aspect == "9:16" and not (
        result["height"] > result["width"]
        and abs(result["width"] / result["height"] - 0.5625) <= 0.02
    ):
        result["reasons"].append("wrong aspect ratio")
    if result["size_bytes"] < 200 * 1024:
        result["reasons"].append("file too small")
    if result["black_s"] > 2.0:
        result["reasons"].append("too much black video")
    if result["frozen_s"] > 3.0:
        result["reasons"].append("too much frozen video")
    result["passed"] = not result["reasons"]
    return result


def contact_sheet(path: str, n: int = 8) -> bytes:
    duration = _duration(path)
    timestamps = [0.5] if duration <= 1 else [0.5 + i * (duration - 1) / max(n - 1, 1) for i in range(n)]
    frames = []
    for timestamp in timestamps:
        raw = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(timestamp), "-i", path,
             "-frames:v", "1", "-vf", "scale=320:-1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            capture_output=True, check=True, timeout=60,
        ).stdout
        with Image.open(io.BytesIO(raw)) as image:
            frames.append(image.convert("RGB"))
    tile_w, tile_h = 320, max(image.height for image in frames)
    sheet = Image.new("RGB", (tile_w * 4, tile_h * 2), "black")
    for index, image in enumerate(frames[:8]):
        sheet.paste(ImageOps.contain(image, (tile_w, tile_h)), ((index % 4) * tile_w, (index // 4) * tile_h))
    output = io.BytesIO()
    sheet.save(output, format="JPEG", quality=85)
    return output.getvalue()


def vision_check(sheet_bytes: bytes, title: str, expectations: dict | None = None) -> dict:
    try:
        from app.services.design_images import _vision_chat, _vision_datauri

        prompt = f"Review this contact sheet for the AI video titled {title!r}. "
        if expectations:
            def clean(value):
                return re.sub(r"\s+", " ", str(value).strip())[:300]

            lines = []
            has_mechanism = all(expectations.get(key) is not None for key in ("part", "mechanism_action", "final_state"))
            if has_mechanism:
                lines.append(
                    "Topic part: " + clean(expectations["part"])
                    + ". Expected mechanism: " + clean(expectations["mechanism_action"])
                    + ". Final state: " + clean(expectations["final_state"]) + "."
                )
            lines.append("Frames are in time order, left to right, top row then bottom row.")
            has_exploded = expectations.get("parts_list") is not None
            if has_exploded:
                lines.append(
                    "At some point the video must show a technical exploded view: "
                    + clean(expectations["parts_list"])
                    + " visibly separated and floating apart, then reassembled."
                )
                lines.append(
                    "During the exploded view, separated parts floating in mid-air and visible screws/bolts "
                    "are intended and are not artifacts."
                )
            if expectations.get("channel_name") is not None:
                lines.append(
                    'If the car shows lettering, it must read exactly "'
                    + clean(expectations["channel_name"])
                    + '"; any misspelled, garbled or extra text fails.'
                )
            prompt += " ".join(lines) + " "
            fail_clauses = [
                "rigid parts (wings, bodywork, wheels, suspension) bend, twist, melt or tilt unnaturally"
            ]
            if has_mechanism:
                fail_clauses.append("the shown part or motion contradicts the expected mechanism")
            if has_exploded:
                fail_clauses.append("the exploded view is missing")
            prompt += "Fail if: " + "; ".join(fail_clauses) + ". Also fail on any of the following. "
        prompt += (
            "Fail if any Formula 1 / F1 / FIA logo or wordmark is visible anywhere, including a small "
            "watermark in a corner or on the nose. Check watermark/logo/URL overlays, garbled text, "
            "severe AI artifacts, vehicle design or colour changes between frames, and NSFW. "
            'Return JSON only: {"pass": bool, "issues": [string]} with short issue strings.'
        )
        raw = _vision_chat([
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": _vision_datauri(sheet_bytes, max_dim=1024)}},
        ], max_tokens=1500, context="ai_video_qc")
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0) if match else raw)
        issues = data.get("issues", [])
        return {"passed": bool(data.get("pass")), "issues": issues, "raw": raw}
    except Exception as exc:
        return {"passed": False, "issues": [f"vision QC unavailable: {exc}"], "raw": None}
