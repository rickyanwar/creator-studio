"""AI image editing: Gemini primary + Qwen (Alibaba DashScope) fallback.

Same failover pattern as ai_caption.py — if Gemini fails N times in a row
(ai_fallback_after_failures), switch to Qwen for ai_fallback_reset_after_minutes,
then retry Gemini. Failure counter stored in Redis with TTL.

Qwen is called via DashScope's plain REST API (httpx, already a dependency)
instead of Alibaba's `dashscope` SDK, to avoid pulling in another package with
its own pydantic/dependency pins — same reasoning that keeps this app off the
unofficial gemini_webapi client. Uses the "multimodal-generation/generation"
endpoint (chat-style messages with an image + text content part), model
"qwen-image-edit" — confirmed working live against a real DashScope account:
it accepts the source image either as a public URL or as an inline base64
data URI (used here, so no publicly reachable URL is required) and returns a
temporary OSS URL for the edited image, no async polling needed.

Note: the earlier "image2image/image-synthesis" (Wanxiang-style) endpoint
does NOT work for this model — it 400s with a misleading "url error" no
matter what URL is passed. Verified by live testing 2026-07-05.

Also confirmed by that same testing: no amount of prompting gets
qwen-image-edit / qwen-image-edit-plus to reliably *redraw* translated text
in a single generative call — it either leaves the text untouched or
garbles it into gibberish, even when handed the exact literal target string
from a separate translation call. Redrawing precise typography is just not
something these diffusion image editors do reliably.

What IS reliable: asking a vision-language model (qwen3-vl-plus) for each
text block's content, English translation, pixel bounding box, background
color, and text color as structured JSON — it nails this in one call — and
then painting the translation onto the image ourselves with PIL instead of
asking a generative model to do it. See clean_and_translate_image(): the
Qwen path does one generative call for watermark/logo removal (a task that
model handles fine on its own) and one VL call for text regions, then PIL
does the actual text rendering, deterministically, every time.
"""

import base64
import io
import json
import logging
import re
from typing import Literal

import httpx
import redis as _redis
from PIL import Image, ImageDraw, ImageFont

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

AIProviderName = Literal["gemini", "qwen"]

# Tried in order — if a request fails on one model (e.g. quota/availability
# differs per model), the next one is attempted.
_GEMINI_MODEL_FALLBACK_ORDER = ["gemini-2.5-flash-image", "gemini-3.1-flash-image"]

_QWEN_GENERATION_URL = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

_REDIS_FAILURE_KEY = "ai_image_edit:gemini_consecutive_failures"
_REDIS_SWITCHED_KEY = "ai_image_edit:switched_to_qwen_until"


def _get_redis() -> _redis.Redis:
    return _redis.from_url(settings.redis_url, decode_responses=True)


def _gemini_failures() -> int:
    val = _get_redis().get(_REDIS_FAILURE_KEY)
    return int(val) if val else 0


def _increment_gemini_failure():
    r = _get_redis()
    r.incr(_REDIS_FAILURE_KEY)
    r.expire(_REDIS_FAILURE_KEY, settings.ai_fallback_reset_after_minutes * 60)


def _reset_gemini_failures():
    _get_redis().delete(_REDIS_FAILURE_KEY)


def _is_switched_to_qwen() -> bool:
    return bool(_get_redis().get(_REDIS_SWITCHED_KEY))


def _switch_to_qwen():
    _get_redis().set(_REDIS_SWITCHED_KEY, "1", ex=settings.ai_fallback_reset_after_minutes * 60)
    logger.warning("Switched image-edit provider to Qwen for %d minutes", settings.ai_fallback_reset_after_minutes)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builders
# ─────────────────────────────────────────────────────────────────────────────

def build_cleanup_prompt(custom_prompt: str | None = None) -> str:
    """Prompt to translate any burned-in text overlay to English and strip
    the source's original watermark/logo."""
    base = (
        "Edit this image: translate any text overlay or caption burned into "
        "the image into English, keeping the same position, font style, and "
        "formatting. Also remove any watermark or logo that was burned into "
        "the image by its original source. Keep the rest of the image "
        "composition, subjects, and quality unchanged."
    )
    if custom_prompt:
        base += f"\nAdditional instructions: {custom_prompt}"
    return base


def build_watermark_prompt(watermark_text: str) -> str:
    """Prompt to stamp a small brand watermark onto an already-cleaned image."""
    return (
        f'Edit this image: add a small, subtle text watermark reading "{watermark_text}" '
        "in a corner of the image. Do not cover the main subject, and keep the rest "
        "of the image unchanged."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Provider calls
# ─────────────────────────────────────────────────────────────────────────────

def _call_gemini(image_bytes: bytes, prompt: str) -> bytes:
    import google.generativeai as genai

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    genai.configure(api_key=settings.gemini_api_key)
    image = Image.open(io.BytesIO(image_bytes))

    last_exc: Exception | None = None
    for model_name in _GEMINI_MODEL_FALLBACK_ORDER:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content([prompt, image])
        except Exception as exc:
            last_exc = exc
            continue

        for part in response.parts:
            if part.inline_data is not None:
                return part.inline_data.data
        last_exc = RuntimeError(f"Gemini returned no image (model={model_name})")

    raise last_exc or RuntimeError("Gemini image edit failed: no model returned an image")


def _qwen_data_uri(image_bytes: bytes) -> str:
    mime = Image.open(io.BytesIO(image_bytes)).get_format_mimetype() or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


def _qwen_generation_call(model: str, image_bytes: bytes, text: str) -> list[dict]:
    if not settings.qwen_api_key:
        raise RuntimeError("QWEN_API_KEY is not set")

    resp = httpx.post(
        _QWEN_GENERATION_URL,
        headers={
            "Authorization": f"Bearer {settings.qwen_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "input": {"messages": [{"role": "user", "content": [{"image": _qwen_data_uri(image_bytes)}, {"text": text}]}]},
            "parameters": {},
        },
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()["output"]["choices"][0]["message"]["content"]


def _call_qwen(image_bytes: bytes, prompt: str) -> bytes:
    content = _qwen_generation_call(settings.qwen_image_edit_model, image_bytes, prompt)
    image_url = next((part["image"] for part in content if "image" in part), None)
    if not image_url:
        raise RuntimeError(f"Qwen returned no image: {content}")

    img_resp = httpx.get(image_url, timeout=60)
    img_resp.raise_for_status()
    return img_resp.content


def _extract_text_regions_qwen(image_bytes: bytes) -> list[dict]:
    """Ask a vision-language model for each burned-in text block's content,
    English translation, pixel bounding box, background color, and text
    color, as structured JSON. Returns [] on no text or unparseable output."""
    w, h = Image.open(io.BytesIO(image_bytes)).size
    content = _qwen_generation_call(
        settings.qwen_vl_model,
        image_bytes,
        "Find each block of text overlay/caption that was burned into this image by an "
        "editor (not text that is part of photographed objects, clothing, or signage). "
        "For each block, output a JSON object with: text (original), translation_en "
        f"(English translation), bbox (pixel coordinates [x1,y1,x2,y2] for this {w}x{h} "
        "image), bg_color (hex color of the background behind the text), text_color "
        "(hex color of the text itself). Output ONLY a raw JSON array, no markdown "
        "fences, no explanation. If no overlay text exists, output []",
    )
    raw = next((part["text"] for part in content if "text" in part), "[]").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        regions = json.loads(raw)
    except ValueError:
        logger.warning("Qwen VL returned unparseable text-region JSON: %s", raw[:300])
        return []
    return regions if isinstance(regions, list) else []


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if not current or draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _render_translated_regions(image_bytes: bytes, regions: list[dict]) -> bytes:
    """Deterministically paint over each detected text region and redraw its
    English translation with PIL — avoids relying on a generative model to
    render text, which testing showed is unreliable (see module docstring)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)

    # VL-model bounding boxes are approximate — often underestimate box
    # height for multi-line text — and can leave the original text peeking
    # out at the edges. Pad generously, relative to each region's own size
    # (an image-wide padding floor would over-inflate small regions like a
    # one-line credit caption). Process top-to-bottom so a later region's
    # fill/redraw always wins over any bleed from an earlier one's padding.
    regions = sorted(
        (r for r in regions if isinstance(r.get("bbox"), (list, tuple)) and len(r["bbox"]) == 4),
        key=lambda r: r["bbox"][1],
    )

    for region in regions:
        try:
            x1, y1, x2, y2 = (int(v) for v in region["bbox"])
            translation = str(region.get("translation_en") or region.get("text") or "").strip()
            bg_color = region.get("bg_color") or "#000000"
            text_color = region.get("text_color") or "#FFFFFF"
        except (KeyError, TypeError, ValueError):
            continue

        pad_x, pad_y = max(6, int((x2 - x1) * 0.08)), max(6, int((y2 - y1) * 0.5))
        x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
        x2, y2 = min(img.width, x2 + pad_x), min(img.height, y2 + pad_y)

        box_w, box_h = x2 - x1, y2 - y1
        if not translation or box_w <= 0 or box_h <= 0:
            continue

        draw.rectangle([x1, y1, x2, y2], fill=bg_color)

        font_size = box_h
        lines, line_height, total_h, max_line_w = [translation], box_h, box_h, box_w
        while font_size >= 8:
            font = ImageFont.truetype(_FONT_PATH, font_size)
            lines = _wrap_text(draw, translation, font, box_w)
            line_height = (font.getbbox("Ag")[3] - font.getbbox("Ag")[1]) + 6
            total_h = line_height * len(lines)
            max_line_w = max((draw.textlength(line, font=font) for line in lines), default=0)
            if total_h <= box_h and max_line_w <= box_w:
                break
            font_size -= 2

        y = y1 + max(0, (box_h - total_h) // 2)
        for line in lines:
            line_w = draw.textlength(line, font=font)
            x = x1 + max(0, (box_w - line_w) // 2)
            draw.text((x, y), line, font=font, fill=text_color)
            y += line_height

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()


def _call_qwen_cleanup(image_bytes: bytes, custom_prompt: str | None) -> bytes:
    """Cleanup via Qwen: a VL call extracts text regions + translations from
    the original, rendered deterministically with PIL (see module docstring
    for why translation is done this way, not generatively).

    Deliberately does NOT also run a generative watermark/logo-removal pass
    here. Tried every ordering (before text, after text) and every prompt
    phrasing during testing on 2026-07-06 — the generative edit is not just
    imprecise, it actively re-hallucinated the original (untranslated) text
    back into the image even when explicitly told to leave text unchanged,
    corrupting an otherwise-correct result. Not worth the risk until a more
    reliable model is available (e.g. Gemini, once its quota works — see
    build_cleanup_prompt/_call_gemini, untested at scale for the same
    reason). custom_prompt is accepted but currently unused on this path;
    kept for signature parity with the Gemini path."""
    regions = _extract_text_regions_qwen(image_bytes)
    return _render_translated_regions(image_bytes, regions) if regions else image_bytes


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────

def edit_image(image_bytes: bytes, prompt: str, force_provider: AIProviderName | None = None) -> bytes:
    """Send an image + edit instruction to an AI provider, return edited image bytes.

    For simple single-instruction edits (e.g. stamping a fixed watermark
    text — see build_watermark_prompt). For source-cleanup + text
    translation, use clean_and_translate_image() instead, which handles
    Qwen's translation limitation.

    Raises on any failure (quota exceeded, no image returned, API error) —
    the caller is responsible for retry/backoff.
    """
    threshold = settings.ai_fallback_after_failures

    if force_provider == "qwen":
        return _call_qwen(image_bytes, prompt)
    if force_provider == "gemini":
        return _call_gemini(image_bytes, prompt)

    if _is_switched_to_qwen() or _gemini_failures() >= threshold:
        _switch_to_qwen()
        return _call_qwen(image_bytes, prompt)

    try:
        result = _call_gemini(image_bytes, prompt)
        _reset_gemini_failures()
        return result
    except Exception as gemini_exc:
        logger.warning("Gemini image edit failed: %s — falling back to Qwen", gemini_exc)
        _increment_gemini_failure()
        if _gemini_failures() >= threshold:
            _switch_to_qwen()

        try:
            return _call_qwen(image_bytes, prompt)
        except Exception as qwen_exc:
            raise RuntimeError(
                f"Both image-edit providers failed. Gemini: {gemini_exc}. Qwen: {qwen_exc}"
            ) from qwen_exc


def clean_and_translate_image(
    image_bytes: bytes, custom_prompt: str | None = None, force_provider: AIProviderName | None = None
) -> bytes:
    """Remove the source's burned-in watermark/logo and translate any
    embedded text to English. Same Gemini-primary/Qwen-fallback failover as
    edit_image(), but the Qwen path uses a 2-call VL-extract-then-replace
    chain instead of a single translate-and-edit call (see module docstring
    for why that matters). Gemini gets the plain single-call prompt since it
    has not shown the same translation limitation in testing (untested at
    scale due to quota — verify quality once Gemini image quota is active).
    """
    threshold = settings.ai_fallback_after_failures
    gemini_prompt = build_cleanup_prompt(custom_prompt)

    if force_provider == "qwen":
        return _call_qwen_cleanup(image_bytes, custom_prompt)
    if force_provider == "gemini":
        return _call_gemini(image_bytes, gemini_prompt)

    if _is_switched_to_qwen() or _gemini_failures() >= threshold:
        _switch_to_qwen()
        return _call_qwen_cleanup(image_bytes, custom_prompt)

    try:
        result = _call_gemini(image_bytes, gemini_prompt)
        _reset_gemini_failures()
        return result
    except Exception as gemini_exc:
        logger.warning("Gemini image edit failed: %s — falling back to Qwen", gemini_exc)
        _increment_gemini_failure()
        if _gemini_failures() >= threshold:
            _switch_to_qwen()

        try:
            return _call_qwen_cleanup(image_bytes, custom_prompt)
        except Exception as qwen_exc:
            raise RuntimeError(
                f"Both image-edit providers failed. Gemini: {gemini_exc}. Qwen: {qwen_exc}"
            ) from qwen_exc
