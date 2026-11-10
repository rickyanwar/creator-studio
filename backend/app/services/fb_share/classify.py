"""Batch caption-only classification for Facebook group routing."""

import json
import logging
import re

from app.models.fb_group_share import FbShareItem
from .brand_settings import get_brand_share
from app.services.muse_labeler import redis_client


logger = logging.getLogger(__name__)
GROUP_CAPTION_MAX_CHARS = 280
GROUP_HASHTAG_MAX = 3
HASHTAG_RE = re.compile(r"#\w+")
# Quoted passages: "…", “…”, «…», and single quotes ‘…’ / '…'. A single quote only opens
# after a non-word character and only closes before one, so apostrophes (don't, Márquez's,
# I’m) are never read as quote marks. One capture group per style; findall gives a tuple.
QUOTED_RE = re.compile(
    r'"([^"]+)"'
    r"|\u201c([^\u201d]+)\u201d"
    r"|\u00ab([^\u00bb]+)\u00bb"
    r"|(?<!\w)\u2018([^\n]+?)\u2019(?!\w)"
    r"|(?<!\w)'([^\n]+?)'(?!\w)"
)
CLASSIFY_PROMPT = """You label social media posts for routing into Facebook fan groups. Use ONLY the caption text given. Do not guess facts that are not in the caption.
For each post return:
- subjects: the people or teams the post is about, written exactly as in the caption (max 4). Empty list if none.
- sentiment: for each subject, how the post treats that subject: "positive" (win, record, praise, good news), "negative" (crash, loss, criticism, bad news), "neutral" (plain fact), or "unsure" when you cannot tell.
- content_type: one of "news", "photo", "stat", "meme", "video".
- niche: one of these exact values: {niches}. Use "" when none fits.
- group_caption: a short caption for posting this in Facebook fan groups: 1 or 2 sentences, at most 280 characters, written in the language with code "{language}". Use only facts from the caption. No hashtags inside it. If you include a quote from the source, copy it word for word. Never shorten or paraphrase a quote. If the quote is too long for a short caption, leave it out.
- group_hashtags: 2 or 3 hashtags that fit the post, each starting with "#", no spaces inside a hashtag.
Answer with JSON only, no prose: {"items":[{"id":<id>,"subjects":[...],"sentiment":{"<subject>":"positive|negative|neutral|unsure"},"content_type":"...","niche":"...","group_caption":"...","group_hashtags":["#...","#..."]}]}

Posts:
{posts}"""


def _squash(text: str) -> str:
    return " ".join(text.split())


def _quotes_verbatim(caption: str, source: str) -> bool:
    """Every quoted passage in the group caption must appear word for word in the source caption."""
    quotes = {_squash("".join(groups)) for groups in QUOTED_RE.findall(source)}
    return all(_squash("".join(groups)) in quotes for groups in QUOTED_RE.findall(caption))


def validate_group_caption(caption, hashtags, source: str) -> tuple[str, list[str]]:
    """Return the cleaned short group caption and 2–3 hashtags, or raise ValueError.

    Shared by classification and per-group caption variants: quotes must appear word for word in `source`.
    """
    caption = caption.strip() if isinstance(caption, str) else ""
    if not caption or len(caption) > GROUP_CAPTION_MAX_CHARS or "#" in caption:
        raise ValueError("empty, overlong, or inline-hashtag group caption")
    if not _quotes_verbatim(caption, source):
        raise ValueError("group caption quote is not verbatim")
    tags = []
    for tag in hashtags if isinstance(hashtags, list) else []:
        tag = tag.strip() if isinstance(tag, str) else ""
        if HASHTAG_RE.fullmatch(tag) and tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
    if not 2 <= len(tags) <= GROUP_HASHTAG_MAX:
        raise ValueError("group caption requires 2–3 valid hashtags")
    return caption, tags


def _apply_group_caption(item, caption, hashtags) -> None:
    """Validate the whole result before replacing any item fields."""
    caption, tags = validate_group_caption(caption, hashtags, item.caption or "")
    item.caption = caption
    item.hashtags = " ".join(tags)


def _classification_failed(item, reason: str) -> None:
    try:
        client = redis_client()
        key = f"fb_share:classify_fail:{item.id}"
        attempts = client.incr(key)
        client.expire(key, 7 * 24 * 60 * 60)
        if attempts >= 3:
            item.status = "skipped"
    except Exception:
        logger.warning("Group Share: classification failure counter unavailable", exc_info=True)
    logger.warning("Group Share: item %s classification rejected (%s): %s", item.id, item.status, reason)


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
    language = getattr(fanpage, "caption_language", None) or "en"
    prompt = CLASSIFY_PROMPT.replace("{niches}", json.dumps(niches)).replace(
        "{language}", language).replace("{posts}", posts)
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
        try:
            _apply_group_caption(item, result.get("group_caption"), result.get("group_hashtags"))
        except ValueError as exc:
            _classification_failed(item, str(exc))
            # A duplicate result must not consume another attempt in the same tick.
            batch.pop(item.id)
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
