"""Designed background generator for typographic fallback cards.

Renders high-resolution PNG background textures for card templates when no
verified photo exists or AI generation fails QA.
Uses Pillow and numpy only (no network or AI dependencies).
"""
from __future__ import annotations

import base64
import io
import math
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from app.services.pro_poster.types import PosterBrief

# Deep near-black base (#0B0F14 family)
BASE_COLOR_RGB = (11.0, 15.0, 20.0)
DEFAULT_ACCENT_HEX = "#E8362B"


def _parse_hex_color(
    hex_str: str | None, default: tuple[int, int, int] = (232, 54, 43)
) -> tuple[int, int, int]:
    """Parse a hex color string into an (R, G, B) integer tuple."""
    if not hex_str or not isinstance(hex_str, str):
        return default
    s = hex_str.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6 or not all(c in "0123456789abcdefABCDEF" for c in s):
        return default
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return default


def _resolve_accent_colors(
    brief: PosterBrief, brand_color: str | None
) -> tuple[tuple[int, int, int], tuple[int, int, int] | None]:
    """Determine primary and optional secondary accent colors."""
    accents = getattr(brief, "accent_colors", ()) or ()
    if accents:
        primary_hex = accents[0]
        second_hex = (
            accents[1]
            if len(accents) > 1
            else (
                brand_color
                if brand_color
                and brand_color.strip().lower() != primary_hex.strip().lower()
                else None
            )
        )
    elif brand_color:
        primary_hex = brand_color
        second_hex = None
    else:
        primary_hex = DEFAULT_ACCENT_HEX
        second_hex = None

    c1 = _parse_hex_color(primary_hex, default=(232, 54, 43))
    c2 = _parse_hex_color(second_hex, default=(232, 54, 43)) if second_hex else None
    return c1, c2


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """Calculate relative luminance using ITU-R BT.709 coefficients."""
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255.0


def _contrast_aware_color(
    rgb: tuple[int, int, int], min_lum: float = 0.35
) -> tuple[int, int, int]:
    """Brighten a dark accent until luminance >= min_lum, keeping its hue and saturation.

    Mixing with white turns a dark navy into a dull grey-blue; raising the HSV value
    (and keeping saturation high) gives a bright royal blue instead, which reads as a
    deliberate poster colour on the near-black background."""
    import colorsys

    if _relative_luminance(rgb) >= min_lum:
        return rgb
    h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
    s = max(s, 0.7)
    candidate = rgb
    for step in range(1, 41):
        v2 = min(1.0, v + step * 0.025)
        candidate = tuple(int(round(c * 255)) for c in colorsys.hsv_to_rgb(h, s, v2))
        if _relative_luminance(candidate) >= min_lum:
            return candidate
        if v2 >= 1.0:
            s = max(0.0, s - 0.05)  # pure blues cannot reach the floor at full value; desaturate slightly
    return candidate


def _resolve_display_word(brief: PosterBrief, display_word: str | None) -> str:
    """Resolve the display word for the background outline text.

    Defaults from the brief:
    1. Hero subject's surname in capitals (if brief.subjects has a hero).
    2. Event circuit/venue in capitals.
    3. Event series in capitals.
    4. Fallback 'STUDIO'.
    """
    if display_word and display_word.strip():
        return display_word.strip().upper()

    subjects = getattr(brief, "subjects", ()) or ()
    hero = next((s for s in subjects if getattr(s, "role", None) == "hero"), None)
    if hero and getattr(hero, "name", None):
        parts = hero.name.strip().split()
        if parts:
            return parts[-1].upper()

    event = getattr(brief, "event", None)
    if event:
        circuit = getattr(event, "circuit", None)
        if circuit and circuit.strip():
            return circuit.strip().upper()
        series = getattr(event, "series", None)
        if series and series.strip():
            return series.strip().upper()

    return "STUDIO"


def _format_event_strip(brief: PosterBrief) -> str:
    """Format the small event metadata strip: SERIES · CIRCUIT · SESSION · YEAR."""
    event = getattr(brief, "event", None)
    if not event:
        return ""
    items: list[str] = []
    series = getattr(event, "series", None)
    if series and series.strip():
        items.append(series.strip().upper())
    circuit = getattr(event, "circuit", None)
    if circuit and circuit.strip():
        items.append(circuit.strip().upper())
    session = getattr(event, "session", None)
    if session and session.strip():
        items.append(session.strip().upper())
    year = getattr(event, "year", None)
    if year:
        items.append(str(year))
    if not items:
        return ""
    raw = " · ".join(items)
    return " ".join(raw)


def _load_display_font(
    size: int, font_path: str | Path | None = None
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load condensed bold display font, falling back gracefully to PIL default."""
    if font_path:
        p = Path(font_path)
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size=size)
            except Exception:
                pass

    candidate_paths = [
        Path(__file__).resolve().parents[2]
        / "assets"
        / "fonts"
        / "tusker-grotesk-6600-semibold.otf",
        Path(__file__).resolve().parents[2] / "assets" / "fonts" / "Anton-Regular.ttf",
        Path(__file__).resolve().parents[2] / "assets" / "fonts" / "Poppins-Black.ttf",
        Path(__file__).resolve().parents[4]
        / "renderer"
        / "fonts"
        / "tusker-grotesk-6600-semibold.otf",
        Path("/app/assets/fonts/tusker-grotesk-6600-semibold.otf"),
    ]
    for p in candidate_paths:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size=size)
            except Exception:
                continue

    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
    except Exception:
        pass

    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def background_datauri(png: bytes) -> str:
    """Format PNG bytes as a base64 data URI string."""
    encoded = base64.b64encode(png).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def make_typographic_background(
    brief: PosterBrief,
    *,
    size: tuple[int, int] = (1080, 1350),
    brand_color: str | None = None,
    seed: int | None = None,
    display_word: str | None = None,
    _font_path: str | Path | None = None,
) -> bytes:
    """Generate a designed typographic background texture as PNG bytes (v3 bold editorial).

    Layout & Aesthetics:
    - Base: deep near-black (#0B0F14 family).
    - Soft diagonal gradient in primary (and optional secondary) accent colour.
    - Solid diagonal colour block from top-right corner (accent darkened 25%) at ~30°.
    - Halftone dot pattern in middle band (fading 40% to 0%).
    - Huge SOLID display word (accent colour, 85% opacity, tight spacing, -6° rotation,
      cropped at left edge, upper third).
    - Echo duplicate: white OUTLINE word (5 px, 35% opacity) offset 18 px down-right.
    - Small event data strip near top-left: SERIES · CIRCUIT · SESSION · YEAR (white 70%).
    - Series motif: motorsport gets speed lines + chequered strip; combat sports gets octagon.
    - Soft vignette and film grain noise (sigma ≈ 6).
    - Bottom 37% kept darker and calm for headline overlay text (no word in bottom band).
    - Deterministic output for identical (brief.job_id, seed).
    """
    width, height = size

    # Deterministic RNG initialization
    job_id = getattr(brief, "job_id", 0)
    if seed is not None:
        combined_seed = (int(job_id) * 1000003 + int(seed)) & 0xFFFFFFFF
    else:
        combined_seed = int(job_id) & 0xFFFFFFFF
    rng = np.random.default_rng(combined_seed)

    # Base color field
    base_arr = np.array(BASE_COLOR_RGB, dtype=np.float32)
    img = np.tile(base_arr, (height, width, 1))

    # Coordinate grids normalized to [0, 1]
    y_norm = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    x_norm = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]

    # Accent color resolution
    c1, c2 = _resolve_accent_colors(brief, brand_color)
    c1_arr = np.array(c1, dtype=np.float32)
    if c2 is not None:
        c2_arr = np.array(c2, dtype=np.float32)
        t_color = np.clip(x_norm * 0.7 + (1.0 - y_norm) * 0.3, 0.0, 1.0)
        color_field = (
            1.0 - t_color[:, :, None]
        ) * c1_arr[None, None, :] + t_color[:, :, None] * c2_arr[None, None, :]
    else:
        color_field = c1_arr[None, None, :]

    # 1. Soft diagonal gradient field in upper 63%
    diag = x_norm * 0.65 + (1.0 - y_norm) * 0.55
    glow = np.clip(1.0 - np.abs(diag - 0.6) * 1.3, 0.0, 1.0)
    vert_env = np.clip((0.63 - y_norm) / 0.28, 0.0, 1.0) ** 1.8
    alpha_grad = glow * vert_env * 0.35
    img = img * (1.0 - alpha_grad[:, :, None]) + color_field * alpha_grad[:, :, None]

    # 2. Solid diagonal colour block from top-right corner (accent colour darkened 25%) at ~30 deg
    c_block = (
        int(round(0.75 * c1[0])),
        int(round(0.75 * c1[1])),
        int(round(0.75 * c1[2])),
    )
    block_img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    block_draw = ImageDraw.Draw(block_img)
    # Wedge from top-right corner: slope tan(30°) ≈ 0.577
    # (0.20 * width, 0) to (width, 0) to (width, 0.38 * height) -> angle approx 30.7°
    block_pts = [(int(width * 0.20), 0), (width, 0), (width, int(height * 0.38))]
    block_draw.polygon(block_pts, fill=(c_block[0], c_block[1], c_block[2], 255))
    block_arr = np.array(block_img, dtype=np.float32)
    block_alpha = (block_arr[:, :, 3] / 255.0)[:, :, None]
    img = img * (1.0 - block_alpha) + block_arr[:, :, :3] * block_alpha

    # 3. Halftone dot pattern in middle band (dots fading from 40% to 0%)
    dots_img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    dots_draw = ImageDraw.Draw(dots_img)
    dots_y0, dots_y1 = int(0.30 * height), int(0.58 * height)
    step = 28
    for gy in range(dots_y0, dots_y1, step):
        t_dot = (gy - dots_y0) / max(1, dots_y1 - dots_y0)
        alpha_dot = int(round(0.40 * (1.0 - t_dot) * 255))
        r_dot = max(1, int(round(1.5 + 4.5 * (1.0 - t_dot))))
        for gx in range(step // 2, width, step):
            dots_draw.ellipse(
                [(gx - r_dot, gy - r_dot), (gx + r_dot, gy + r_dot)],
                fill=(255, 255, 255, alpha_dot),
            )
    dots_arr = np.array(dots_img, dtype=np.float32)
    dots_alpha = (dots_arr[:, :, 3] / 255.0)[:, :, None]
    img = img * (1.0 - dots_alpha) + dots_arr[:, :, :3] * dots_alpha

    # 4. Series motif on transparent overlay
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    event = getattr(brief, "event", None)
    vehicle = getattr(event, "vehicle", None) if event else None
    is_motorsport = bool(vehicle)

    if is_motorsport:
        # Subtle chequered strip near top edge (low opacity ~10%)
        sq_size = max(8, int(round(height * 0.016)))
        strip_rows = 2
        cols = width // sq_size + 1
        for r in range(strip_rows):
            for c in range(cols):
                if (r + c) % 2 == 0:
                    x0 = c * sq_size
                    y0 = r * sq_size
                    draw.rectangle(
                        [x0, y0, x0 + sq_size, y0 + sq_size],
                        fill=(255, 255, 255, 26),
                    )

        # 6-12 thin diagonal motion/speed lines in the top/middle area
        num_lines = int(rng.integers(6, 13))
        for _ in range(num_lines):
            y_start = float(rng.uniform(0.06 * height, 0.48 * height))
            x_start = float(rng.uniform(-0.1 * width, 0.6 * width))
            length = float(rng.uniform(0.3 * width, 0.8 * width))
            angle = math.radians(float(rng.uniform(25.0, 40.0)))
            x_end = x_start + length * math.cos(angle)
            y_end = y_start + length * math.sin(angle)
            max_y = 0.58 * height
            if y_end > max_y:
                scale = (max_y - y_start) / max(y_end - y_start, 1e-4)
                x_end = x_start + (x_end - x_start) * scale
                y_end = max_y
            line_w = int(rng.integers(1, 3))
            line_alpha = int(rng.integers(40, 85))
            draw.line(
                [(x_start, y_start), (x_end, y_end)],
                fill=(255, 255, 255, line_alpha),
                width=line_w,
            )
    else:
        # Combat sports: large faint octagon outline
        cx = width / 2.0
        cy = height * 0.36
        radius = width * 0.33
        angles = [math.pi / 8.0 + i * math.pi / 4.0 for i in range(8)]
        outer_pts = [
            (cx + radius * math.cos(a), cy + radius * math.sin(a)) for a in angles
        ]
        draw.polygon(outer_pts, outline=(255, 255, 255, 55), width=3)
        inner_pts = [
            (cx + radius * 0.93 * math.cos(a), cy + radius * 0.93 * math.sin(a))
            for a in angles
        ]
        draw.polygon(inner_pts, outline=(255, 255, 255, 30), width=2)

    overlay_arr = np.array(overlay, dtype=np.float32)
    overlay_rgb = overlay_arr[:, :, :3]
    overlay_alpha = (overlay_arr[:, :, 3] / 255.0)[:, :, None]
    img = img * (1.0 - overlay_alpha) + overlay_rgb * overlay_alpha

    # 5. Background words: echo outline + solid word
    resolved_word = _resolve_display_word(brief, display_word)
    if resolved_word:
        # Dynamically size font so the word spans about 120% to 140% of canvas width
        target_w = 1.30 * width
        test_font = _load_display_font(100, _font_path)
        test_bbox = test_font.getbbox(resolved_word)
        w100 = max(1, test_bbox[2] - test_bbox[0])
        ideal_size = int(round(100.0 * target_w / w100))
        font_size = max(160, min(480, ideal_size))
        display_font = _load_display_font(font_size, _font_path)

        bbox = display_font.getbbox(resolved_word)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        pad = 60

        # Duplicate white OUTLINE echo (5 px, 35% opacity)
        echo_canvas = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
        echo_draw = ImageDraw.Draw(echo_canvas)
        echo_draw.text(
            (pad - bbox[0], pad - bbox[1]),
            resolved_word,
            font=display_font,
            fill=(0, 0, 0, 0),
            stroke_width=5,
            stroke_fill=(255, 255, 255, int(round(0.35 * 255))),
        )
        echo_rot = echo_canvas.rotate(-6, resample=Image.Resampling.BILINEAR, expand=True)

        # Huge SOLID word in contrast-aware accent colour at 85% opacity
        c_word = _contrast_aware_color(c1, min_lum=0.35)
        solid_canvas = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
        solid_draw = ImageDraw.Draw(solid_canvas)
        solid_draw.text(
            (pad - bbox[0], pad - bbox[1]),
            resolved_word,
            font=display_font,
            fill=(c_word[0], c_word[1], c_word[2], int(round(0.85 * 255))),
        )
        solid_rot = solid_canvas.rotate(-6, resample=Image.Resampling.BILINEAR, expand=True)

        # Cropped at the left edge, placed in the upper third
        paste_x = -int(0.08 * width)
        paste_y = int(0.18 * height)

        word_overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        # Behind: echo word offset 18 px down-right
        word_overlay.paste(echo_rot, (paste_x + 18, paste_y + 18), echo_rot)
        # Front: solid word
        word_overlay.paste(solid_rot, (paste_x, paste_y), solid_rot)

        # Strictly enforce: no word inside bottom 37% band
        word_arr = np.array(word_overlay, dtype=np.float32)
        word_arr[int(0.63 * height):, :, :] = 0.0

        word_rgb = word_arr[:, :, :3]
        word_alpha = (word_arr[:, :, 3] / 255.0)[:, :, None]
        img = img * (1.0 - word_alpha) + word_rgb * word_alpha

    # 6. Small event data strip near top-left: SERIES · CIRCUIT · SESSION · YEAR
    event_strip_text = _format_event_strip(brief)
    if event_strip_text:
        strip_font = _load_display_font(22, _font_path)
        strip_canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        strip_draw = ImageDraw.Draw(strip_canvas)
        strip_y = 52 if is_motorsport else 36
        strip_draw.text(
            (48, strip_y),
            event_strip_text,
            font=strip_font,
            fill=(255, 255, 255, int(round(0.70 * 255))),
        )
        strip_arr = np.array(strip_canvas, dtype=np.float32)
        strip_alpha = (strip_arr[:, :, 3] / 255.0)[:, :, None]
        img = img * (1.0 - strip_alpha) + strip_arr[:, :, :3] * strip_alpha

    # 7. Soft vignette (darker edges/corners, especially at the bottom)
    cx_norm, cy_norm = 0.5, 0.40
    r2 = ((x_norm - cx_norm) / 0.75) ** 2 + ((y_norm - cy_norm) / 0.85) ** 2
    vignette = 1.0 - 0.30 * np.clip(r2, 0.0, 1.0)
    bottom_dim = 1.0 - 0.35 * np.clip((y_norm - 0.55) / 0.45, 0.0, 1.0)
    vignette = vignette * bottom_dim
    img = img * vignette[:, :, None]

    # Ensure minimum base floor
    img = np.maximum(img, base_arr * 0.7)

    # 8. Fine film grain noise (sigma ≈ 6)
    noise = rng.normal(loc=0.0, scale=6.0, size=(height, width, 3)).astype(np.float32)
    img = np.clip(img + noise, 0.0, 255.0)

    # Encode to PNG
    pil_img = Image.fromarray(img.astype(np.uint8), mode="RGB")
    bio = io.BytesIO()
    # ponytail: compress_level=4 for fast rendering; bump to 6 or 9 if disk/network transfer size becomes critical
    pil_img.save(bio, format="PNG", compress_level=4)
    return bio.getvalue()
