from __future__ import annotations

import base64
import json
import logging
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from app.config import get_settings
from app.models.ai_video import VideoJob
from app.services.ai_video_cover import (
    COVER_FADE_SECONDS,
    COVER_HOLD_SECONDS,
    build_cover_copy_prompt,
    build_cover_html,
    build_cover_image_prompt,
    fallback_cover_copy,
    parse_cover_copy,
)
from app.services.design_images import watermark_datauri
from app.services.video_captions import FONTS_DIR
from app.services.video_templates import fanpage_niche

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models.target_fanpages import TargetFanpage

logger = logging.getLogger(__name__)


def recent_cover_texts(db: Session, fanpage_id: int) -> list[str]:
    recent_rows = (
        db.query(VideoJob)
        .filter(VideoJob.fanpage_id == fanpage_id, VideoJob.cover_lines.isnot(None))
        .order_by(VideoJob.id.desc())
        .limit(8)
        .all()
    )
    return [" ".join(j.cover_lines) for j in recent_rows if j.cover_lines]


def apply_prepared_cover(job: VideoJob, cover: dict) -> None:
    job.cover_lines = cover["lines"]
    job.cover_highlight = cover["highlight"]
    job.cover_prompt = cover["image_prompt"]


def prepare_cover(db: Session, fanpage: TargetFanpage, job: VideoJob) -> None:
    try:
        if not getattr(fanpage, "ai_video_cover_enabled", True):
            return

        recent = recent_cover_texts(db, fanpage.id)
        niche = fanpage_niche(fanpage)
        prompt = build_cover_copy_prompt(job.title, job.caption, niche, recent)

        try:
            from app.services import news_copywriter
            copy, _provider = news_copywriter._generate_with_fallback(
                prompt,
                lambda raw: parse_cover_copy(raw, job.title or "", strict=True),
                context="ai_video_cover",
                fanpage_id=fanpage.id,
            )
        except Exception as exc:
            logger.warning("prepare_cover copywriter failed, using fallback: %s", exc)
            copy = fallback_cover_copy(job.title or "")

        job.cover_lines = copy["lines"]
        job.cover_highlight = copy["highlight"]
        job.cover_prompt = build_cover_image_prompt(
            job.title or "",
            copy.get("scene", ""),
            niche,
            getattr(job, "aspect", None) or "9:16",
        )
    except Exception as exc:
        logger.warning("prepare_cover failed for job %s: %s", getattr(job, "id", None), exc)


def apply_cover(
    job: VideoJob,
    fanpage: TargetFanpage,
    video_path: Path,
    frame_source: Path,
    workdir: Path,
) -> tuple[Path, Path] | None:
    try:
        video_path = Path(video_path)
        frame_source = Path(frame_source)
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)

        probe_cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json",
            str(video_path),
        ]
        probe_res = subprocess.run(
            probe_cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        probe_data = json.loads(probe_res.stdout)
        v_stream = probe_data["streams"][0]
        width = int(v_stream["width"])
        height = int(v_stream["height"])

        cover_bg = getattr(job, "cover_bg_path", None)
        if cover_bg and Path(cover_bg).is_file():
            bg_path = Path(cover_bg)
        else:
            frame_file = workdir / "cover_frame.jpg"
            extract_cmd = [
                "ffmpeg", "-v", "error", "-y",
                "-ss", "3",
                "-i", str(frame_source),
                "-frames:v", "1",
                "-q:v", "3",
                str(frame_file),
            ]
            res = subprocess.run(extract_cmd, timeout=60)
            if res.returncode != 0 or not frame_file.is_file() or frame_file.stat().st_size == 0:
                subprocess.run(
                    [
                        "ffmpeg", "-v", "error", "-y",
                        "-ss", "0",
                        "-i", str(frame_source),
                        "-frames:v", "1",
                        "-q:v", "3",
                        str(frame_file),
                    ],
                    check=True,
                    timeout=60,
                )
            bg_path = frame_file

        if getattr(job, "cover_lines", None) and getattr(job, "cover_highlight", None):
            lines = list(job.cover_lines)
            highlight = list(job.cover_highlight)
        else:
            fb = fallback_cover_copy(getattr(job, "title", "") or "")
            lines = fb["lines"]
            highlight = fb["highlight"]

        logo_src: str | None = None
        logo_text: str | None = None
        logo_width_px: int | None = None
        if fanpage and getattr(fanpage, "ai_video_logo_enabled", False):
            uri = watermark_datauri(fanpage)
            if uri:
                logo_src = uri
                # Same size rule as ai_video_overlay: scale=iw*logo_size_pct/100 of the logo file itself.
                from io import BytesIO
                from PIL import Image
                with Image.open(BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as logo_img:
                    logo_width_px = round(logo_img.width * (getattr(fanpage, "ai_video_logo_size_pct", 14) or 14) / 100)
            else:
                logo_text = getattr(fanpage, "watermark_text", None)

        logo_size_pct = getattr(fanpage, "ai_video_logo_size_pct", 14) if fanpage else 14
        logo_position = getattr(fanpage, "ai_video_logo_position", "top_right") if fanpage else "top_right"
        logo_margin_pct = getattr(fanpage, "ai_video_logo_margin_pct", 3) if fanpage else 3
        highlight_hex = getattr(fanpage, "ai_video_caption_highlight", "#FFE500") if fanpage else "#FFE500"

        font_file = FONTS_DIR / "Poppins-Black.ttf"
        font_b64 = base64.b64encode(font_file.read_bytes()).decode("ascii")
        font_src = f"data:font/ttf;base64,{font_b64}"

        bg_b64 = base64.b64encode(bg_path.read_bytes()).decode("ascii")
        background_src = f"data:image/jpeg;base64,{bg_b64}"

        html = build_cover_html(
            width=width,
            height=height,
            background_src=background_src,
            lines=lines,
            highlight=highlight,
            highlight_hex=highlight_hex,
            font_src=font_src,
            logo_src=logo_src,
            logo_text=logo_text,
            logo_size_pct=logo_size_pct,
            logo_position=logo_position,
            logo_margin_pct=logo_margin_pct,
            logo_width_px=logo_width_px,
        )

        renderer_url = get_settings().renderer_url.rstrip("/")
        resp = httpx.post(
            f"{renderer_url}/render-html",
            json={
                "html": html,
                "width": width,
                "height": height,
                "ready_flag": "__coverReady",
            },
            timeout=90,
        )
        resp.raise_for_status()
        cover_png = workdir / "cover.png"
        cover_png.write_bytes(resp.content)

        cover_jpg = video_path.with_name(f"{video_path.stem}_cover.jpg")
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-i", str(cover_png),
                "-q:v", "3",
                str(cover_jpg),
            ],
            check=True,
            timeout=60,
        )

        out = video_path.with_name(f"{video_path.stem}_c{uuid.uuid4().hex[:6]}{video_path.suffix}")
        filter_complex = (
            f"[1:v]scale={width}:{height},format=rgba,fade=t=out:st={COVER_HOLD_SECONDS}:d={COVER_FADE_SECONDS}:alpha=1[c];"
            f"[0:v][c]overlay=0:0:eof_action=pass[v]"
        )
        total_cover_time = COVER_HOLD_SECONDS + COVER_FADE_SECONDS
        cmd = [
            "ffmpeg", "-v", "error", "-y",
            "-i", str(video_path),
            "-loop", "1",
            "-t", str(total_cover_time),
            "-i", str(cover_png),
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(out),
        ]
        subprocess.run(cmd, check=True, timeout=300)
        return out, cover_jpg
    except Exception as exc:
        logger.warning("apply_cover failed for job %s: %s", getattr(job, "id", None), exc)
        return None
