import base64
import colorsys
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.services.pro_poster.typo_background import (
    _load_display_font,
    _resolve_display_word,
    background_datauri,
    make_typographic_background,
)
from app.services.pro_poster.types import EventContext, PosterBrief, Subject


@pytest.fixture
def motorsport_brief() -> PosterBrief:
    return PosterBrief(
        job_id=201,
        subjects=(Subject(name="Max Verstappen", role="hero", team="Red Bull Racing"),),
        event=EventContext(
            series="Formula 1",
            vehicle="Formula 1 car",
            circuit="Monza",
            session="race",
        ),
        story_type="breaking",
        mood="dramatic",
        badge_label="F1 NEWS",
        accent_colors=("#E8362B",),
    )


@pytest.fixture
def ufc_brief() -> PosterBrief:
    return PosterBrief(
        job_id=202,
        subjects=(Subject(name="Mike Malott", role="hero"),),
        event=EventContext(
            series="UFC",
            vehicle=None,
            circuit="T-Mobile Arena",
            session="fight night",
        ),
        story_type="preview",
        mood="tense",
        badge_label="UFC NEWS",
        accent_colors=("#D20A0A",),
    )


def test_make_typographic_background_size_and_format(motorsport_brief: PosterBrief):
    png = make_typographic_background(motorsport_brief)
    assert isinstance(png, bytes)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")

    img = Image.open(io.BytesIO(png))
    assert img.size == (1080, 1350)
    assert img.format == "PNG"


def test_make_typographic_background_custom_size(motorsport_brief: PosterBrief):
    png = make_typographic_background(motorsport_brief, size=(720, 900))
    img = Image.open(io.BytesIO(png))
    assert img.size == (720, 900)


def test_make_typographic_background_deterministic(motorsport_brief: PosterBrief):
    # Same brief and explicit seed produces identical bytes
    png1 = make_typographic_background(motorsport_brief, seed=42)
    png2 = make_typographic_background(motorsport_brief, seed=42)
    assert png1 == png2

    # Same brief without seed (uses job_id) produces identical bytes
    png_def1 = make_typographic_background(motorsport_brief)
    png_def2 = make_typographic_background(motorsport_brief)
    assert png_def1 == png_def2

    # Different seeds produce different bytes
    png3 = make_typographic_background(motorsport_brief, seed=99)
    assert png1 != png3


def test_make_typographic_background_bottom_darker_than_top(
    motorsport_brief: PosterBrief, ufc_brief: PosterBrief
):
    for brief in (motorsport_brief, ufc_brief):
        png = make_typographic_background(brief, seed=10)
        img = Image.open(io.BytesIO(png))
        arr = np.array(img, dtype=np.float32)

        # Standard ITU-R BT.709 relative luminance
        lum = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
        h = arr.shape[0]
        split_y = int(0.63 * h)

        top_lum = float(np.mean(lum[:split_y, :]))
        bottom_lum = float(np.mean(lum[split_y:, :]))

        assert bottom_lum < top_lum, (
            f"Expected bottom ({bottom_lum:.2f}) < top ({top_lum:.2f})"
        )


@pytest.mark.parametrize(
    "accent_colors,brand_color",
    [
        (("#FF0000",), None),
        (("#0000FF",), None),
        (("#FFE600",), None),
        (("#111111",), None),
        ((), "#0055FF"),
        ((), None),
    ],
)
def test_make_typographic_background_bottom_darker_various_accents(
    motorsport_brief: PosterBrief, accent_colors: tuple[str, ...], brand_color: str | None
):
    brief = PosterBrief(
        job_id=301,
        subjects=motorsport_brief.subjects,
        event=motorsport_brief.event,
        story_type="breaking",
        mood="dramatic",
        badge_label="F1 NEWS",
        accent_colors=accent_colors,
    )
    png = make_typographic_background(brief, brand_color=brand_color, seed=7)
    img = Image.open(io.BytesIO(png))
    arr = np.array(img, dtype=np.float32)

    lum = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
    split_y = int(0.63 * arr.shape[0])
    top_lum = float(np.mean(lum[:split_y, :]))
    bottom_lum = float(np.mean(lum[split_y:, :]))

    assert bottom_lum < top_lum


def test_make_typographic_background_motorsport_vs_ufc_differ(
    motorsport_brief: PosterBrief,
):
    ufc_same_job = PosterBrief(
        job_id=motorsport_brief.job_id,
        subjects=motorsport_brief.subjects,
        event=EventContext(
            series="UFC",
            vehicle=None,
        ),
        story_type="breaking",
        mood="dramatic",
        badge_label="UFC NEWS",
        accent_colors=motorsport_brief.accent_colors,
    )

    png_motorsport = make_typographic_background(motorsport_brief, seed=123)
    png_ufc = make_typographic_background(ufc_same_job, seed=123)

    assert png_motorsport != png_ufc

    # Check that pixels differ between motorsport and UFC
    arr_ms = np.array(Image.open(io.BytesIO(png_motorsport)))
    arr_ufc = np.array(Image.open(io.BytesIO(png_ufc)))
    assert not np.array_equal(arr_ms, arr_ufc)


@pytest.mark.parametrize(
    "color_hex,channel_idx",
    [
        ("#FF0000", 0),  # Red
        ("#00FF00", 1),  # Green
        ("#0000FF", 2),  # Blue
    ],
)
def test_make_typographic_background_accent_color_visible_channels(
    color_hex: str, channel_idx: int
):
    brief = PosterBrief(
        job_id=401,
        subjects=(),
        event=EventContext(series="Formula 1", vehicle="Formula 1 car"),
        story_type="breaking",
        mood="dramatic",
        badge_label="F1 NEWS",
        accent_colors=(color_hex,),
    )
    png = make_typographic_background(brief, seed=5)
    img = Image.open(io.BytesIO(png))
    arr = np.array(img, dtype=np.float32)

    # Inspect gradient area (upper-middle region where gradient is strongest)
    h, w = arr.shape[:2]
    y0, y1 = int(0.10 * h), int(0.40 * h)
    x0, x1 = int(0.20 * w), int(0.80 * w)
    region = arr[y0:y1, x0:x1]

    mean_channels = np.mean(region, axis=(0, 1))
    target_val = mean_channels[channel_idx]
    other_vals = [mean_channels[i] for i in range(3) if i != channel_idx]

    # Target channel must be prominently higher than the other channels
    assert target_val > max(other_vals) + 15.0


def test_make_typographic_background_accent_color_visible_hue():
    # Orange McLaren accent (#FF8000 -> hue ~ 30 degrees)
    brief = PosterBrief(
        job_id=402,
        subjects=(),
        event=EventContext(series="Formula 1", vehicle="Formula 1 car"),
        story_type="breaking",
        mood="dramatic",
        badge_label="F1 NEWS",
        accent_colors=("#FF8000",),
    )
    png = make_typographic_background(brief, seed=5)
    img = Image.open(io.BytesIO(png))
    arr = np.array(img, dtype=np.float32)

    h, w = arr.shape[:2]
    y0, y1 = int(0.10 * h), int(0.35 * h)
    x0, x1 = int(0.25 * w), int(0.75 * w)
    region = arr[y0:y1, x0:x1]

    mean_rgb = np.mean(region, axis=(0, 1))
    hue, _, _ = colorsys.rgb_to_hsv(
        mean_rgb[0] / 255.0, mean_rgb[1] / 255.0, mean_rgb[2] / 255.0
    )
    target_hue, _, _ = colorsys.rgb_to_hsv(1.0, 128.0 / 255.0, 0.0)

    hue_diff = abs(hue - target_hue) * 360.0
    if hue_diff > 180.0:
        hue_diff = 360.0 - hue_diff

    assert hue_diff < 20.0, f"Expected hue close to target; got diff {hue_diff:.1f}°"


def test_make_typographic_background_empty_accent_list():
    # Empty accent list with brand_color fallback
    brief_brand = PosterBrief(
        job_id=501,
        subjects=(),
        event=EventContext(series="Formula 1", vehicle="Formula 1 car"),
        story_type="breaking",
        mood="dramatic",
        badge_label="F1 NEWS",
        accent_colors=(),
    )
    png_brand = make_typographic_background(brief_brand, brand_color="#0055FF")
    img_brand = Image.open(io.BytesIO(png_brand))
    arr_brand = np.array(img_brand, dtype=np.float32)
    # Blue channel should dominate due to brand_color
    assert np.mean(arr_brand[:400, :, 2]) > np.mean(arr_brand[:400, :, 0])

    # Empty accent list with no brand_color (default #E8362B red)
    png_default = make_typographic_background(brief_brand, brand_color=None)
    img_default = Image.open(io.BytesIO(png_default))
    arr_default = np.array(img_default, dtype=np.float32)
    # Red channel should dominate
    assert np.mean(arr_default[:400, :, 0]) > np.mean(arr_default[:400, :, 2])


def test_make_typographic_background_dual_accents():
    brief_single = PosterBrief(
        job_id=601,
        subjects=(),
        event=EventContext(series="Formula 1", vehicle="Formula 1 car"),
        story_type="breaking",
        mood="dramatic",
        badge_label="F1 NEWS",
        accent_colors=("#FF8000",),
    )
    brief_dual = PosterBrief(
        job_id=601,
        subjects=(),
        event=EventContext(series="Formula 1", vehicle="Formula 1 car"),
        story_type="breaking",
        mood="dramatic",
        badge_label="F1 NEWS",
        accent_colors=("#FF8000", "#002244"),
    )
    png_single = make_typographic_background(brief_single, seed=1)
    png_dual = make_typographic_background(brief_dual, seed=1)

    assert png_single != png_dual


def test_make_typographic_background_invalid_hex():
    brief = PosterBrief(
        job_id=701,
        subjects=(),
        event=EventContext(series="Formula 1", vehicle="Formula 1 car"),
        story_type="breaking",
        mood="dramatic",
        badge_label="F1 NEWS",
        accent_colors=("not-a-hex",),
    )
    png = make_typographic_background(brief)
    assert isinstance(png, bytes)
    assert png.startswith(b"\x89PNG")


def test_resolve_display_word_defaults():
    # Hero subject -> surname in capitals
    brief_hero = PosterBrief(
        job_id=801,
        subjects=(
            Subject(name="Lando Norris", role="hero"),
            Subject(name="Oscar Piastri", role="secondary"),
        ),
        event=EventContext(series="Formula 1", circuit="Monza"),
        story_type="breaking",
        mood="dramatic",
        badge_label="F1",
    )
    assert _resolve_display_word(brief_hero, None) == "NORRIS"

    # Multi-word surname
    brief_multi = PosterBrief(
        job_id=802,
        subjects=(Subject(name="Nyck de Vries", role="hero"),),
        event=EventContext(series="Formula 1", circuit="Zandvoort"),
        story_type="breaking",
        mood="dramatic",
        badge_label="F1",
    )
    assert _resolve_display_word(brief_multi, None) == "VRIES"

    # No hero -> circuit/venue in capitals
    brief_venue = PosterBrief(
        job_id=803,
        subjects=(),
        event=EventContext(series="Formula 1", circuit="Silverstone"),
        story_type="breaking",
        mood="dramatic",
        badge_label="F1",
    )
    assert _resolve_display_word(brief_venue, None) == "SILVERSTONE"

    # No hero, no circuit -> series in capitals
    brief_series = PosterBrief(
        job_id=804,
        subjects=(),
        event=EventContext(series="MotoGP", circuit=None),
        story_type="breaking",
        mood="dramatic",
        badge_label="MOTOGP",
    )
    assert _resolve_display_word(brief_series, None) == "MOTOGP"

    # Explicit display_word overrides brief
    assert _resolve_display_word(brief_hero, "CHAMPION") == "CHAMPION"


def test_format_event_strip():
    from app.services.pro_poster.typo_background import _format_event_strip

    # All fields present
    b_full = PosterBrief(
        job_id=810,
        subjects=(),
        event=EventContext(
            series="Formula 1",
            circuit="Singapore",
            session="race",
            year=2026,
        ),
        story_type="breaking",
        mood="dramatic",
        badge_label="F1",
    )
    strip_text = _format_event_strip(b_full)
    assert "F O R M U L A   1" in strip_text
    assert "S I N G A P O R E" in strip_text
    assert "R A C E" in strip_text
    assert "2 0 2 6" in strip_text

    # Partial fields
    b_part = PosterBrief(
        job_id=811,
        subjects=(),
        event=EventContext(series="MotoGP", circuit="Mugello"),
        story_type="breaking",
        mood="dramatic",
        badge_label="MOTOGP",
    )
    strip_part = _format_event_strip(b_part)
    assert "M O T O G P" in strip_part
    assert "M U G E L L O" in strip_part
    assert "2 0 2 6" not in strip_part


def test_make_typographic_background_word_drawn_only_in_top_area(motorsport_brief: PosterBrief):
    # Render with two different words but same seed
    png_norris = make_typographic_background(
        motorsport_brief, display_word="NORRIS", seed=50
    )
    png_verstappen = make_typographic_background(
        motorsport_brief, display_word="VERSTAPPEN", seed=50
    )

    arr_norris = np.array(Image.open(io.BytesIO(png_norris)))
    arr_verstappen = np.array(Image.open(io.BytesIO(png_verstappen)))

    h = arr_norris.shape[0]
    split_y = int(0.63 * h)

    # Top area has different words -> pixels must differ
    top_diff = np.any(arr_norris[:split_y, :, :] != arr_verstappen[:split_y, :, :])
    assert top_diff, "Top area should differ with different words"

    # Bottom 37% area has NO word -> bottom pixels must be identical
    bottom_identical = np.array_equal(
        arr_norris[split_y:, :, :], arr_verstappen[split_y:, :, :]
    )
    assert bottom_identical, "Bottom 37% area must contain no word and be identical"

    # Bottom band must remain darker than top band
    lum = 0.2126 * arr_norris[:, :, 0] + 0.7152 * arr_norris[:, :, 1] + 0.0722 * arr_norris[:, :, 2]
    top_lum = float(np.mean(lum[:split_y, :]))
    bottom_lum = float(np.mean(lum[split_y:, :]))
    assert bottom_lum < top_lum


def test_make_typographic_background_font_fallback_path(motorsport_brief: PosterBrief):
    # Explicitly pass nonexistent font path to test graceful fallback
    png = make_typographic_background(
        motorsport_brief,
        display_word="NORRIS",
        _font_path="nonexistent_font_file.ttf",
    )
    assert isinstance(png, bytes)
    img = Image.open(io.BytesIO(png))
    assert img.size == (1080, 1350)
    assert img.format == "PNG"

    # Directly verify _load_display_font fallback
    fallback_font = _load_display_font(120, font_path="nonexistent_file.otf")
    assert fallback_font is not None


def test_make_typographic_background_word_span_and_visibility(motorsport_brief: PosterBrief):
    # Test that word font sizing targets 120% - 140% of width
    png = make_typographic_background(motorsport_brief, display_word="NORRIS", seed=10)
    img = Image.open(io.BytesIO(png))
    assert img.size == (1080, 1350)

    # Directly check font sizing helper for various words
    font = _load_display_font(100)
    for word in ("NORRIS", "VERSTAPPEN", "MALOTT"):
        bbox = font.getbbox(word)
        w100 = max(1, bbox[2] - bbox[0])
        ideal_size = int(round(100.0 * (1.30 * 1080) / w100))
        scaled_font = _load_display_font(ideal_size)
        scaled_bbox = scaled_font.getbbox(word)
        word_w = scaled_bbox[2] - scaled_bbox[0]
        ratio = word_w / 1080.0
        assert 1.20 <= ratio <= 1.40, f"Expected 1.20-1.40 span, got {ratio:.2f}"


def test_contrast_aware_word_color_dark_accent():
    from app.services.pro_poster.typo_background import (
        _contrast_aware_color,
        _relative_luminance,
    )

    # Dark blue accent (#1E3A8A) has lum ~0.227 < 0.25
    c_dark = (30, 58, 138)
    orig_lum = _relative_luminance(c_dark)
    assert orig_lum < 0.25

    c_boosted = _contrast_aware_color(c_dark, min_lum=0.35)
    boosted_lum = _relative_luminance(c_boosted)
    assert boosted_lum >= 0.35

    # Pure black (#000000)
    c_black = (0, 0, 0)
    c_black_boosted = _contrast_aware_color(c_black, min_lum=0.35)
    assert _relative_luminance(c_black_boosted) >= 0.35

    # Light accent (#FF8000) remains untouched
    c_orange = (255, 128, 0)
    assert _contrast_aware_color(c_orange, min_lum=0.35) == c_orange


def test_background_datauri():
    sample_bytes = b"fake-png-bytes-for-test"
    uri = background_datauri(sample_bytes)
    assert uri.startswith("data:image/png;base64,")
    b64_part = uri.split(",", 1)[1]
    decoded = base64.b64decode(b64_part)
    assert decoded == sample_bytes
