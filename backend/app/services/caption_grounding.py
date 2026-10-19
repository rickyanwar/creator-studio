"""Caption grounding service to prevent hallucinated social media captions."""

import io
import json
import logging
import re
from pathlib import Path
from PIL import Image
import base64

logger = logging.getLogger(__name__)


def has_usable_caption(text: str | None) -> bool:
    """Return True when text, after stripping hashtags, @mentions, URLs,
    emojis, and whitespace, contains at least 20 letters."""
    if not text or not isinstance(text, str):
        return False
    cleaned = re.sub(r"https?://\S+|www\.\S+", "", text)
    cleaned = re.sub(r"#\S+", "", cleaned)
    cleaned = re.sub(r"@\S+", "", cleaned)
    letters = [c for c in cleaned if c.isalpha()]
    return len(letters) >= 20


def _to_datauri(image_input: str | bytes | Path, max_dim: int = 768, quality: int = 80) -> str:
    """Load image from path or bytes, resize long edge to max_dim, return JPEG data URI."""
    if isinstance(image_input, (str, Path)):
        im = Image.open(image_input)
    elif isinstance(image_input, bytes):
        im = Image.open(io.BytesIO(image_input))
    else:
        raise ValueError(f"Unsupported image input type: {type(image_input)}")

    with im:
        img = im.convert("RGB")
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"


def _parse_json(raw: str) -> dict | None:
    """Parse JSON dictionary from raw vision output."""
    if not raw or not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE).strip()
    try:
        val = json.loads(cleaned)
        if isinstance(val, dict):
            return val
    except Exception:
        pass
    try:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            val = json.loads(match.group(0))
            if isinstance(val, dict):
                return val
    except Exception:
        pass
    return None


def describe_image_for_caption(image_path_or_bytes, niche_hint: str | None = None) -> dict | None:
    """Call Gemini Flash vision chat to describe an image for grounding caption generation.
    Returns parsed dict or None on any failure. Never raises."""
    try:
        if not image_path_or_bytes:
            return None
        datauri = _to_datauri(image_path_or_bytes, max_dim=768)

        hint_text = f"Niche context: {niche_hint}.\n" if niche_hint else ""
        prompt = (
            f"Analyze this image for social media caption writing.\n"
            f"{hint_text}"
            f"Return a JSON object with EXACTLY this structure:\n"
            f"{{\n"
            f'  "visible_text": "...exact text printed in the image...",\n'
            f'  "description": "1-2 factual sentences of what is visible",\n'
            f'  "people": [{{"name": "...", "confidence": 0.0}}],\n'
            f'  "is_meme": true|false\n'
            f"}}\n\n"
            f"Rules:\n"
            f"- Name a person only if clearly recognizable or named by visible text, otherwise describe them generically.\n"
            f"- Never guess places.\n"
            f"- {niche_hint or 'niche_hint'} is context only.\n"
            f"- Output ONLY valid JSON, no markdown formatting, no code fences."
        )

        from app.services.design_images import _vision_chat

        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": datauri}},
        ]
        raw = _vision_chat(content, max_tokens=800, context="caption_grounding")
        return _parse_json(raw)
    except Exception as exc:
        logger.warning("describe_image_for_caption failed: %s", exc)
        return None
