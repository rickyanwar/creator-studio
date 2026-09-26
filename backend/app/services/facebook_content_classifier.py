"""Classify a scraped Facebook page photo via 9Router vision (Mode 6).

The photo is only ever READ here — its text decides what kind of card gets
recreated, and nothing from the image itself reaches the final design (see
design_renderer.render_facebook_photo, which sources a clean photo of the
subject instead). Categories, matching the existing template pools:
  - "news"       → a news/announcement graphic; extract the headline
  - "quote"      → a person's spoken quote; extract the bare quote and the
                    speaker (Mode 2 quote-card convention: design_title =
                    quote, design_subtitle = speaker's name badge)
  - "discussion" → a debate/hot-take graphic; extract a question, a label
                    ("DISCUSSION"/"HOT TAKE"), and the subject's name — the
                    Mode 4 discussion template's render contract
                    (design_title=question, design_subtitle=label,
                    design_caption=subject_name)
  - "other"      → anything else (memes, plain photos, promos, schedules) →
                    the caller rejects the candidate entirely, no idea row.
"""

import json
import logging
import re
from typing import Literal, TypedDict

logger = logging.getLogger(__name__)

ContentType = Literal["news", "quote", "discussion", "other"]


class Classification(TypedDict):
    type: ContentType
    headline: str          # "news" only, else ""
    quote: str             # "quote" only: the quote alone, no name, no quote marks
    speaker: str           # "quote" only: the person who said it
    question: str          # "discussion" only, else ""
    label: str              # "discussion" only: "DISCUSSION" | "HOT TAKE", else ""
    subject_name: str       # "discussion" only, else ""
    on_topic: bool          # always True unless a topic filter was given (see _TOPIC_FILTER)


_PROMPT = (
    "You are classifying a photo from a {niche} Facebook page's public photo gallery.\n"
    "These pages post news graphics, quote graphics and debate/hot-take graphics, usually with "
    "text baked directly into the image. Decide which ONE category this photo is:\n\n"
    '- "news": a news headline / announcement graphic. Extract the single main headline into '
    "`headline`.\n"
    '- "quote": the main text is something a specific person SAID (usually shown in quotation '
    "marks next to their name or photo). Extract:\n"
    "  - `quote`: only the spoken words — no speaker name, no surrounding quotation marks.\n"
    "  - `speaker`: the full name of the person who said it.\n"
    "  A nickname in quotes inside a name (e.g. Jon \"Bones\" Jones) is NOT a quote.\n"
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
    "headline, quote or debate angle. All extracted fields empty.\n\n"
    'Respond with ONLY a JSON object: {{"type": "news|quote|discussion|other", "headline": "...", '
    '"quote": "...", "speaker": "...", "question": "...", "label": "...", "subject_name": "..."}} '
    "— no markdown, no explanation. Keep extracted text in its original language."
)


# Appended (not .format()-ed — the admin's text may contain braces) when the
# fanpage has facebook_photo_topic_filter set. A source page can mix niches
# (found 2026-09-26: debate.opus put Trump, a UK prime minister, Musk and
# Arsenal onto an F1 page), so the filter judges what the post is actually
# ABOUT, not whether a name from the niche appears somewhere in it.
_TOPIC_FILTER = (
    "\n\nTOPIC FILTER — this fanpage only posts about: {topic}\n"
    "Also return `on_topic`: true ONLY if what this post is actually about clearly belongs to "
    "that topic. A post about football, politics, business or anything else is NOT on topic "
    "just because it mentions someone from the topic in passing. If unsure, false. "
    'Add it to the JSON object: {{..., "on_topic": true|false}}.'
)


def _parse(raw: str, strict_topic: bool = False) -> Classification:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError(f"no JSON in vision output: {raw[:200]!r}")
    data = json.loads(match.group(0))
    t = str(data.get("type", "")).lower().strip()
    if t not in ("news", "quote", "discussion", "other"):
        t = "other"
    label = str(data.get("label") or "").strip().upper()
    if label not in ("DISCUSSION", "HOT TAKE"):
        label = "DISCUSSION" if t == "discussion" else ""
    quote = str(data.get("quote") or "").strip().strip('"“”').strip()
    return {
        "type": t,
        "headline": str(data.get("headline") or "").strip(),
        "quote": quote,
        "speaker": str(data.get("speaker") or "").strip(),
        "question": str(data.get("question") or "").strip(),
        "label": label,
        "subject_name": str(data.get("subject_name") or "").strip(),
        # With a filter, only an explicit true counts (fail-closed — a
        # missing/garbled answer must not let off-topic content through).
        "on_topic": data.get("on_topic") is True if strict_topic else True,
    }


def classify_facebook_photo(image_bytes: bytes, niche: str = "general", topic_filter: str | None = None) -> Classification:
    """Classify a Facebook page photo into news/quote/discussion/other and
    extract its text fields. With `topic_filter` (the fanpage's allowed
    topic, free text), also judge `on_topic` in the same vision call.

    Raises on transport/API/parse error — the caller owns retry/skip policy.
    """
    from app.services.nine_router import get_nine_router_config

    cfg = get_nine_router_config()
    if not cfg.base_url:
        raise RuntimeError("9Router base URL not configured — cannot classify Facebook photos")

    from app.services.design_images import _vision_datauri, _vision_chat

    datauri = _vision_datauri(image_bytes, max_dim=1024)
    topic = " ".join((topic_filter or "").split())[:300]
    prompt = _PROMPT.format(niche=niche or "general")
    if topic:
        prompt += _TOPIC_FILTER.replace("{topic}", topic).replace("{{", "{").replace("}}", "}")
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": datauri}},
    ]
    raw = _vision_chat(content, max_tokens=1500)
    result = _parse(raw, strict_topic=bool(topic))
    logger.info("Facebook photo classify → %s (niche=%r, on_topic=%s)", result["type"], niche, result["on_topic"])
    return result
