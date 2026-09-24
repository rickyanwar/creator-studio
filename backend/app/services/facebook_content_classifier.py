"""Classify a scraped Facebook page photo via 9Router vision (Mode 6).

Mirrors services/ig_content_classifier.py (Mode 3), swapping "quote" for
"discussion" — competitor/inspiration Facebook pages (e.g. sports fan pages)
lean heavily on debate-bait graphics ("WHO WOULD WIN...", "MOST X OF ALL
TIME") rather than spoken quotes, so this categorizes into:
  - "news"       → a news/announcement graphic; extract the headline
  - "discussion" → a debate/hot-take graphic; extract a question, a label
                    ("DISCUSSION"/"HOT TAKE"), and the subject's name — this
                    matches the Mode 4 discussion template's real render
                    contract (design_title=question, design_subtitle=label,
                    design_caption=subject_name), not a flat text field.
  - "other"      → anything else (memes, plain photos, promos, schedules) →
                    the caller rejects the candidate entirely, no idea row.

Vision runs through 9Router (see design_images._vision_chat/_vision_datauri)
same as ig_content_classifier.
"""

import json
import logging
import re
from typing import Literal, TypedDict

logger = logging.getLogger(__name__)

ContentType = Literal["news", "discussion", "other"]


class Classification(TypedDict):
    type: ContentType
    headline: str          # "news" only, else ""
    question: str          # "discussion" only, else ""
    label: str              # "discussion" only: "DISCUSSION" | "HOT TAKE", else ""
    subject_name: str       # "discussion" only, else ""


_PROMPT = (
    "You are classifying a photo from a {niche} Facebook page's public photo gallery.\n"
    "These pages post a mix of news graphics and debate/hot-take graphics (often with text baked "
    "directly into the image, e.g. \"WHO WOULD WIN A KART RACE OF ALL 22 F1 DRIVERS?\" or "
    "\"MOST F1 SPRINT WINS OF ALL TIME\"). Decide which ONE category this photo is:\n\n"
    '- "news": a news headline / announcement graphic. Extract the single main headline into `headline`.\n'
    '- "discussion": a debate/hot-take/ranking graphic meant to spark disagreement or opinions — '
    "usually phrased as a question or a bold ranking claim. Extract:\n"
    '  - `question`: the debate framed as a punchy question (rewrite a bold claim into a question '
    'if the image itself isn\'t phrased as one, e.g. "MOST F1 SPRINT WINS" -> "WHO HAS THE MOST F1 '
    'SPRINT WINS OF ALL TIME?").\n'
    '  - `label`: either "DISCUSSION" (an open question with no implied answer) or "HOT TAKE" (a '
    "bold, arguable claim/ranking).\n"
    '  - `subject_name`: the driver/team/person/entity this is actually about, if identifiable from '
    "the image, else empty string.\n"
    '- "other": memes, plain photos, promos, schedules, merchandise, or anything without a clear '
    "headline or debate angle. All extracted fields empty.\n\n"
    'Respond with ONLY a JSON object: {{"type": "news|discussion|other", "headline": "...", '
    '"question": "...", "label": "...", "subject_name": "..."}} — no markdown, no explanation. '
    "Keep extracted text in its original language."
)


def _parse(raw: str) -> Classification:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError(f"no JSON in vision output: {raw[:200]!r}")
    data = json.loads(match.group(0))
    t = str(data.get("type", "")).lower().strip()
    if t not in ("news", "discussion", "other"):
        t = "other"
    label = str(data.get("label") or "").strip().upper()
    if label not in ("DISCUSSION", "HOT TAKE"):
        label = "DISCUSSION" if t == "discussion" else ""
    return {
        "type": t,
        "headline": str(data.get("headline") or "").strip(),
        "question": str(data.get("question") or "").strip(),
        "label": label,
        "subject_name": str(data.get("subject_name") or "").strip(),
    }


def classify_facebook_photo(image_bytes: bytes, niche: str = "general") -> Classification:
    """Classify a Facebook page photo into news/discussion/other and extract
    its rendering fields.

    Raises on transport/API/parse error — the caller owns retry/skip policy.
    """
    from app.services.nine_router import get_nine_router_config

    cfg = get_nine_router_config()
    if not cfg.base_url:
        raise RuntimeError("9Router base URL not configured — cannot classify Facebook photos")

    from app.services.design_images import _vision_datauri, _vision_chat

    datauri = _vision_datauri(image_bytes, max_dim=1024)
    content = [
        {"type": "text", "text": _PROMPT.format(niche=niche or "general")},
        {"type": "image_url", "image_url": {"url": datauri}},
    ]
    raw = _vision_chat(content, max_tokens=1500)
    result = _parse(raw)
    logger.info("Facebook photo classify → %s (niche=%r)", result["type"], niche)
    return result
