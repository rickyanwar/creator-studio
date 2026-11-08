"""Batch caption-only classification for Facebook group routing."""

import json
import logging
import re

from app.models.fb_group_share import FbShareItem
from .brand_settings import get_brand_share


logger = logging.getLogger(__name__)
CLASSIFY_PROMPT = """You label social media posts for routing into Facebook fan groups. Use ONLY the caption text given. Do not guess facts that are not in the caption.
For each post return:
- subjects: the people or teams the post is about, written exactly as in the caption (max 4). Empty list if none.
- sentiment: for each subject, how the post treats that subject: "positive" (win, record, praise, good news), "negative" (crash, loss, criticism, bad news), "neutral" (plain fact), or "unsure" when you cannot tell.
- content_type: one of "news", "photo", "stat", "meme", "video".
- niche: one of these exact values: {niches}. Use "" when none fits.
Answer with JSON only, no prose: {"items":[{"id":<id>,"subjects":[...],"sentiment":{"<subject>":"positive|negative|neutral|unsure"},"content_type":"...","niche":"..."}]}

Posts:
{posts}"""


def classify_pending_items(db, fanpage, *, max_items: int = 10, generate=None) -> int:
    if max_items <= 0:
        return 0
    items = db.query(FbShareItem).filter(
        FbShareItem.fanpage_id == fanpage.id,
        FbShareItem.status == "new", FbShareItem.sentiment.is_(None),
    ).order_by(FbShareItem.created_at, FbShareItem.id).limit(max_items).all()
    if not items:
        return 0
    _, settings = get_brand_share(db, fanpage.id)
    niches = settings["niches"]
    posts = "\n".join(
        f"id={item.id} media={item.media_kind} caption={' '.join((item.caption or '')[:600].splitlines())}"
        for item in items
    )
    prompt = CLASSIFY_PROMPT.replace("{niches}", json.dumps(niches)).replace("{posts}", posts)
    try:
        if generate is None:
            from app.services.gptcli_client import generate_text, get_gptcli_config
            text = generate_text(get_gptcli_config(db), prompt).text
        else:
            text = generate(prompt)
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
        payload = json.loads(text)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("Missing items list")
    except Exception:
        logger.warning("Facebook share classification failed; leaving items untouched")
        return 0

    batch = {item.id: item for item in items}
    updated = set()
    for result in payload["items"]:
        if not isinstance(result, dict) or type(result.get("id")) is not int:
            continue
        item = batch.get(result["id"])
        if item is None or item.id in updated:
            continue
        subjects = result.get("subjects", [])
        subjects = [s.strip() for s in subjects if isinstance(s, str) and s.strip()][:4] if isinstance(subjects, list) else []
        sentiment = result.get("sentiment", {})
        sentiment = sentiment if isinstance(sentiment, dict) else {}
        item.subjects = subjects
        item.sentiment = {
            subject: value if isinstance(value, str) and value in ("positive", "negative", "neutral", "unsure") else "unsure"
            for subject in subjects for value in [sentiment.get(subject)]
        }
        content_type = result.get("content_type")
        item.content_type = content_type if isinstance(content_type, str) and content_type in ("news", "photo", "stat", "meme", "video") else (
            "video" if item.media_kind == "video" else "photo"
        )
        niche = result.get("niche")
        if isinstance(niche, str) and niche in niches:
            item.niche = niche
        elif not item.niche:
            item.status = "skipped"
        updated.add(item.id)
    db.commit()
    return len(updated)
