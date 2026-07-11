"""Classify a scraped Instagram post image via 9Router vision.

The IG-recreate feature reads each post's image and decides what it is:
  - "news"  → a news/announcement graphic; we extract the headline
  - "quote" → a person's quote (e.g. 'Marc Marquez: "saya akan lawan"'); we
              extract it as `Speaker: "quote"`
  - "other" → anything else → the caller skips it

Vision runs through 9Router (a multimodal model, default ag/gemini-3-flash)
because direct Gemini API keys don't work in this environment.
"""

import base64
import json
import logging
import re
from typing import Literal, TypedDict

from app.config import get_settings

logger = logging.getLogger(__name__)

ContentType = Literal["news", "quote", "other"]


class Classification(TypedDict):
    type: ContentType
    text: str


_PROMPT = (
    "You are classifying an Instagram post image for a {niche} page.\n"
    "Decide which ONE category it is and extract its main text:\n"
    '- "news": a news headline / announcement graphic. Extract the single main headline.\n'
    '- "quote": a person\'s spoken quote. Extract it as `Speaker: "the quote"` '
    "(keep the speaker name if shown).\n"
    '- "other": memes, plain photos, promos, schedules, or anything without a clear '
    "headline or quote. Text can be empty.\n\n"
    'Respond with ONLY a JSON object: {{"type": "news|quote|other", "text": "..."}} '
    "— no markdown, no explanation. Keep the extracted text in its original language."
)


def _parse(raw: str) -> Classification:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError(f"no JSON in vision output: {raw[:200]!r}")
    data = json.loads(match.group(0))
    t = str(data.get("type", "")).lower().strip()
    if t not in ("news", "quote", "other"):
        t = "other"
    return {"type": t, "text": str(data.get("text") or "").strip()}


def classify_ig_image(image_bytes: bytes, niche: str = "general") -> Classification:
    """Classify a post image into news/quote/other and extract its text.

    Raises on transport/API/parse error — the caller owns retry/skip policy.
    """
    from openai import OpenAI  # type: ignore

    from app.services.nine_router import get_nine_router_config

    settings = get_settings()
    cfg = get_nine_router_config()
    if not cfg.base_url:
        raise RuntimeError("9Router base URL not configured — cannot classify IG images")

    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key or "sk-9router")
    b64 = base64.b64encode(image_bytes).decode()
    completion = client.chat.completions.create(
        model=settings.nine_router_vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT.format(niche=niche or "general")},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
        max_tokens=1500,
        temperature=0,
    )
    raw = completion.choices[0].message.content or ""
    result = _parse(raw)
    logger.info("IG classify → %s (%d chars)", result["type"], len(result["text"]))
    return result
