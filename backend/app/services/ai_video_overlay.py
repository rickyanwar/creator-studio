"""Logo and word-highlight caption rendering for Mode 8 videos."""

import base64
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.services.design_images import watermark_datauri
from app.services.video_captions import FONTS_DIR, Word, write_ass


def _fallback_window(start: float, end: float) -> tuple[float, float]:
    return (start + min(0.4, max(0.0, (end - start) / 3)),
            end - min(0.4, max(0.0, (end - start) / 3)))


def speech_windows(video_path: str, clip_seconds: float, n_clips: int) -> list[tuple[float, float]]:
    """Return speech-like audio bounds per clip, falling back for missing/short audio."""
    total = clip_seconds * n_clips
    cmd = ["ffmpeg", "-v", "info", "-i", video_path, "-af", "silencedetect=noise=-30dB:d=0.25",
           "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        log = proc.stderr
    except (OSError, subprocess.SubprocessError):
        log = ""
    silences = [(float(a), float(b)) for a, b in re.findall(
        r"silence_start:\s*([\d.]+).*?silence_end:\s*([\d.]+)", log, re.S)]
    # A missing final silence means the audio continues to the clip/video end.
    windows = []
    for index in range(n_clips):
        start, end = index * clip_seconds, min(total, (index + 1) * clip_seconds)
        speech_start, speech_end = start, end
        detected = False
        if silences:
            intervals = []
            cursor = start
            for silence_start, silence_end in silences:
                if silence_end <= start or silence_start >= end:
                    continue
                if silence_start > cursor:
                    intervals.append((cursor, min(silence_start, end)))
                cursor = max(cursor, silence_end)
            if cursor < end:
                intervals.append((cursor, end))
            if intervals:
                speech_start, speech_end = intervals[0][0], intervals[-1][1]
                detected = True
        if not detected or speech_end - speech_start < 3.0:
            speech_start, speech_end = _fallback_window(start, end)
        windows.append((speech_start, speech_end))
    return windows


def estimate_words(texts_by_clip: list[str], windows: list[tuple[float, float]]) -> list[Word]:
    result: list[Word] = []
    for text, (start, end) in zip(texts_by_clip, windows):
        tokens = re.findall(r"\S+", text or "")
        if not tokens or end <= start:
            continue
        word_weights = [max(1, len(re.sub(r"[^\w]", "", token))) for token in tokens]
        pause_weights = [1.5 if token.rstrip().endswith((",", ".", "?", "!")) else 0 for token in tokens[:-1]]
        total = sum(word_weights) + sum(pause_weights)
        cursor = start
        for index, (token, weight) in enumerate(zip(tokens, word_weights)):
            duration = (end - start) * weight / total
            word_end = min(end, cursor + duration)
            result.append(Word(cursor, word_end, token))
            cursor = word_end + (end - start) * pause_weights[index] / total if index < len(pause_weights) else word_end
    return result


def _filter_path(path: str | Path) -> str:
    return (str(path).replace("\\", "/").replace(" ", "\\ ")
            .replace(":", "\\:").replace("'", ""))


def _position(position: str, margin_x: str = "m", margin_y: str | None = None) -> tuple[str, str]:
    margin_y = margin_y or margin_x
    x = "W-w-{m}" if position.endswith("right") else "{m}"
    y = "H-h-{m}" if position.startswith("bottom") else "{m}"
    return x.format(m=margin_x), y.format(m=margin_y)


def _build_ffmpeg_cmd(*, src: str, out: str, ass_path: str | None, fonts_dir: str | None,
                      logo_path: str | None, logo_size_pct: int, logo_position: str,
                      logo_margin_pct: int, text_fallback: str | None,
                      caption_size: int = 76, caption_margin_v: int = 560,
                      highlight_hex: str = "#FFE500") -> list[str]:
    margin_x = f"W*{logo_margin_pct}/100"
    margin_y = f"H*{logo_margin_pct}/100"
    chain: list[str] = []
    label = "0:v"
    if ass_path:
        fonts = f":fontsdir={_filter_path(fonts_dir)}" if fonts_dir else ""
        chain.append(f"[{label}]ass=filename={_filter_path(ass_path)}{fonts}[cap]")
        label = "cap"
    inputs = ["ffmpeg", "-v", "error", "-y", "-i", src]
    if logo_path:
        inputs += ["-i", logo_path]
        x, y = _position(logo_position, margin_x, margin_y)
        chain += [f"[1:v]scale=iw*{logo_size_pct}/100:-1,format=rgba[logo]",
                  f"[{label}][logo]overlay={x}:{y}[branded]"]
        label = "branded"
    elif text_fallback:
        text = text_fallback.replace("\\", "").replace("'", "")[:80]
        x, y = _position(logo_position, "W*0/100", margin_y)
        x = "w-tw-(w*%d/100)" % logo_margin_pct if logo_position.endswith("right") else "w*%d/100" % logo_margin_pct
        y = "h-th-(h*%d/100)" % logo_margin_pct if logo_position.startswith("bottom") else "h*%d/100" % logo_margin_pct
        font = f":fontfile={_filter_path(Path(fonts_dir) / 'Poppins-Black.ttf')}" if fonts_dir else ""
        chain.append(f"[{label}]drawtext=text='{text}'{font}:fontsize=34:fontcolor=white@0.85:shadowcolor=black@0.6:shadowx=2:shadowy=2:x={x}:y={y}[branded]")
        label = "branded"
    if chain:
        inputs += ["-filter_complex", ";".join(chain), "-map", f"[{label}]"]
    else:
        inputs += ["-map", "0:v"]
    return inputs + ["-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                     "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", out]


def _resolve_logo(fanpage, workdir: Path) -> tuple[str | None, str | None]:
    uri = watermark_datauri(fanpage)
    if uri:
        path = workdir / "ai-video-logo.png"
        path.write_bytes(base64.b64decode(uri.split(",", 1)[1]))
        return str(path), None
    return None, getattr(fanpage, "watermark_text", None)


def _fallback_render(src: str, out: str, fanpage, words: list[Word], logo_path: str | None) -> None:
    """Burn overlays through a raw-frame pipeline for minimal ffmpeg builds."""
    import io
    import json
    from PIL import Image, ImageDraw, ImageFont

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", src],
        check=True, capture_output=True, text=True, timeout=60,
    )
    data = json.loads(probe.stdout)
    video = next(stream for stream in data["streams"] if stream.get("codec_type") == "video")
    width, height = int(video["width"]), int(video["height"])
    rate_num, rate_den = (video.get("r_frame_rate") or "24/1").split("/", 1)
    fps = float(rate_num) / float(rate_den)
    duration = float(data.get("format", {}).get("duration") or 0)
    frame_count = max(1, round(duration * fps))
    font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Black.ttf"),
                              max(20, round(fanpage.ai_video_caption_size * width / 1080)))
    logo = Image.open(logo_path).convert("RGBA") if logo_path else None
    if logo:
        logo.thumbnail((round(width * fanpage.ai_video_logo_size_pct / 100), height))
    decoder = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-f", "rawvideo",
                                "-pix_fmt", "rgb24", "-",], stdout=subprocess.PIPE)
    encoder = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{width}x{height}", "-r", f"{fps:.6f}", "-i", "-", "-i", src,
         "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-preset", "medium",
         "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", out],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        frame_bytes = width * height * 3
        for index in range(frame_count):
            raw = decoder.stdout.read(frame_bytes)
            if len(raw) != frame_bytes:
                break
            image = Image.frombytes("RGB", (width, height), raw).convert("RGBA")
            if logo:
                margin = round(width * fanpage.ai_video_logo_margin_pct / 100)
                x = width - logo.width - margin if fanpage.ai_video_logo_position.endswith("right") else margin
                y = height - logo.height - margin if fanpage.ai_video_logo_position.startswith("bottom") else margin
                image.alpha_composite(logo, (x, y))
            if fanpage.ai_video_captions_enabled:
                timestamp = index / fps
                active = [word for word in words if word.start <= timestamp <= word.end]
                if active:
                    caption = " ".join(word.text.upper() for word in active[-3:])
                    draw = ImageDraw.Draw(image)
                    bbox = draw.textbbox((0, 0), caption, font=font, stroke_width=3)
                    x = (width - (bbox[2] - bbox[0])) // 2
                    y = height - round(height * fanpage.ai_video_caption_bottom_pct / 100) - (bbox[3] - bbox[1])
                    draw.text((x, y), caption, font=font, fill="white", stroke_width=3, stroke_fill="black")
            encoder.stdin.write(image.convert("RGB").tobytes())
    finally:
        decoder.stdout.close()
        decoder.wait()
        encoder.stdin.close()
        error = encoder.stderr.read().decode(errors="replace")
        encoder.wait()
    if encoder.returncode:
        raise RuntimeError(f"fallback ffmpeg encode failed: {error[-800:]}")


def render_branded(src: str, out: str, fanpage, words: list[Word], workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    logo_path, text = _resolve_logo(fanpage, workdir) if fanpage.ai_video_logo_enabled else (None, None)
    ass_path = None
    if fanpage.ai_video_captions_enabled and words:
        ass = workdir / "ai-video-captions.ass"
        write_ass(words, 0, max(w.end for w in words) + 1, ass,
                  font_size=fanpage.ai_video_caption_size,
                  margin_v=round(1920 * fanpage.ai_video_caption_bottom_pct / 100),
                  highlight_hex=fanpage.ai_video_caption_highlight)
        ass_path = str(ass)
    cmd = _build_ffmpeg_cmd(src=src, out=out, ass_path=ass_path, fonts_dir=str(FONTS_DIR),
                            logo_path=logo_path, logo_size_pct=fanpage.ai_video_logo_size_pct,
                            logo_position=fanpage.ai_video_logo_position,
                            logo_margin_pct=fanpage.ai_video_logo_margin_pct,
                            text_fallback=text, caption_size=fanpage.ai_video_caption_size,
                            caption_margin_v=0, highlight_hex=fanpage.ai_video_caption_highlight)
    def _runtime_expr(arg: str) -> str:
        return (arg.replace("w-tw-W*", "w-tw-w*")
                .replace("W-w-W*", "main_w-overlay_w-main_w*")
                .replace("H-h-H*", "main_h-overlay_h-main_h*")
                .replace("W*", "main_w*")
                .replace("H*", "main_h*") )
    ffmpeg_cmd = [_runtime_expr(arg) for arg in cmd]
    assets = Path(tempfile.mkdtemp(prefix="ai-video-assets-"))
    try:
        staged_ass = None
        if ass_path:
            staged_ass = assets / "captions.ass"
            shutil.copy2(ass_path, staged_ass)
        staged_fonts = assets / "fonts"
        shutil.copytree(FONTS_DIR, staged_fonts)
        cmd = _build_ffmpeg_cmd(src=src, out=out, ass_path=str(staged_ass) if staged_ass else None,
                                fonts_dir=str(staged_fonts), logo_path=logo_path,
                                logo_size_pct=fanpage.ai_video_logo_size_pct,
                                logo_position=fanpage.ai_video_logo_position,
                                logo_margin_pct=fanpage.ai_video_logo_margin_pct,
                                text_fallback=text, caption_size=fanpage.ai_video_caption_size,
                                caption_margin_v=0, highlight_hex=fanpage.ai_video_caption_highlight)
        ffmpeg_cmd = [_runtime_expr(arg) for arg in cmd]
        try:
            subprocess.run(ffmpeg_cmd, check=True, capture_output=True, text=True, timeout=600)
        except subprocess.CalledProcessError as exc:
            if ass_path and "No such filter: 'ass'" in (exc.stderr or ""):
                _fallback_render(src, out, fanpage, words, logo_path)
            else:
                raise
    finally:
        shutil.rmtree(assets, ignore_errors=True)


def preview_frame(settings: dict, source_video: str | None, out_png: str | Path) -> None:
    out_png = Path(out_png)
    workdir = out_png.parent
    frame = workdir / "preview-source.png"
    if source_video:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "3", "-i", source_video,
                        "-frames:v", "1", str(frame)], check=True, timeout=60)
    else:
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (720, 1280), (17, 24, 39))
        draw = ImageDraw.Draw(image)
        for y in range(1280):
            c = int(17 + 30 * y / 1280)
            draw.line((0, y, 720, y), fill=(c, c + 4, c + 18))
        image.save(frame)
    from PIL import Image, ImageDraw, ImageFont
    image = Image.open(frame).convert("RGBA").resize((720, 1280))
    if settings.get("ai_video_logo_enabled") and settings.get("watermark_image_path"):
        logo = Image.open(settings["watermark_image_path"]).convert("RGBA")
        logo.thumbnail((int(720 * settings["ai_video_logo_size_pct"] / 100), 1280))
        m = int(720 * settings["ai_video_logo_margin_pct"] / 100)
        x = 720 - logo.width - m if "right" in settings["ai_video_logo_position"] else m
        y = 1280 - logo.height - m if "bottom" in settings["ai_video_logo_position"] else m
        image.alpha_composite(logo, (x, y))
    if settings.get("ai_video_captions_enabled"):
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Black.ttf"), max(20, round(settings["ai_video_caption_size"] * 720 / 1080)))
        text = "THIS IS THE DRS"
        text_height = draw.textbbox((0, 0), text, font=font)[3]
        y = 1280 - int(1280 * settings["ai_video_caption_bottom_pct"] / 100) - text_height
        x = (720 - draw.textbbox((0, 0), text, font=font)[2]) // 2
        draw.text((x + 3, y + 3), text, font=font, fill="black", stroke_width=3, stroke_fill="black")
        parts = text.split()
        before = "THIS "
        draw.text((x, y), text, font=font, fill="white", stroke_width=2, stroke_fill="black")
        draw.text((x + draw.textlength(before, font=font), y), "IS", font=font, fill=settings["ai_video_caption_highlight"], stroke_width=2, stroke_fill="black")
    image.convert("RGB").save(out_png, "PNG")
