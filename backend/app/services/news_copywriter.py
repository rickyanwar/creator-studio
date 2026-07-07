"""News copywriter — rewrite a scraped article into a design headline + FB caption.

One AI call per (article, fanpage) returning structured JSON:
  {"title": "<short headline for the image design>", "caption": "<FB post text>"}

Reuses the Gemini-primary/Groq-fallback failover from ai_caption.generate_caption
(spec Phase 2C: "Reuse Gemini+Groq failover"). Uses the fanpage's Mode 2 caption
criteria, which are a separate set from the Mode 1 IG-repost criteria.
"""

import json
import logging
import re
from dataclasses import dataclass

from app.services.ai_caption import AIProviderName, generate_caption

logger = logging.getLogger(__name__)

# Keep prompts bounded — long articles don't improve copy quality
_MAX_CONTENT_CHARS = 4000


@dataclass
class NewsCopy:
    title: str      # short headline placed on the image design
    caption: str    # FB post text
    provider: AIProviderName


def _effective_title_max_chars(fanpage, article) -> int:
    """Title budget: never force the AI to compress below the scraped title.

    mode2_title_max_chars acts as a floor for the budget, and the scraped
    title's own length (plus slack for an engagement hook like "BREAKING:")
    raises it — the design auto-shrinks its font to fit, so a longer
    headline is safe.
    """
    scraped_len = len(article.scraped_title or "")
    return max(fanpage.mode2_title_max_chars, scraped_len + 40)


def build_news_copy_prompt(fanpage, article) -> str:
    source_name = article.news_source.name if article.news_source else "the original source"
    attribution_line = (
        f"- End the caption with a source attribution line: \"Source: {source_name}\""
        if fanpage.mode2_source_attribution else ""
    )
    content = (article.scraped_content or "")[:_MAX_CONTENT_CHARS]

    return f"""You are a social media copywriter for the Facebook Fanpage "{fanpage.name}".

SOURCE NEWS ARTICLE (from {source_name}):
TITLE: {article.scraped_title}
CONTENT:
{content}

TASK: Write two pieces of copy for a news image post, substantially rewritten in your own words (do not copy sentences from the source):

1. "title" — the headline that will be printed ON the image design.
   - Stay close to the source TITLE above: keep all its facts and names, and rewrite it only to make it more engaging (stronger verbs, urgency, hook like "BREAKING:" / "OFFICIAL:" when it fits). Translate to {fanpage.mode2_caption_language} if needed.
   - Keep roughly the SAME LENGTH as the source TITLE (or slightly longer with the hook) — do NOT shorten it or compress it into a vague topic label.
   - GOOD example: source "Di Giannantonio to join Red Bull KTM Factory Racing" → "BREAKING: Fabio Di Giannantonio is officially joining Red Bull KTM Factory Racing!"
   - BAD example: "MotoGP Shake-Up" (dropped the facts, too short)
   - Maximum {_effective_title_max_chars(fanpage, article)} characters
   - No hashtags, no emoji, no quote marks

2. "caption" — the Facebook post text that accompanies the image.
   - Language: {fanpage.mode2_caption_language}
   - Tone: {fanpage.mode2_caption_tone}
   - Maximum length: {fanpage.mode2_caption_max_length} characters
   - Include {fanpage.mode2_caption_hashtag_count} relevant hashtags at the end
   - End with call-to-action: {fanpage.mode2_caption_cta_text if fanpage.mode2_caption_cta_text else "none"}
{attribution_line}
   - Additional notes: {fanpage.mode2_caption_custom_prompt if fanpage.mode2_caption_custom_prompt else "none"}

OUTPUT: only a raw JSON object {{"title": "...", "caption": "..."}} — no markdown fences, no explanation."""


def _parse_news_copy(raw: str) -> tuple[str, str]:
    """Parse the model's JSON output, tolerating markdown fences and stray text."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    # models occasionally prepend/append prose — grab the outermost JSON object
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in AI output: {raw[:200]!r}")
    data = json.loads(match.group(0))
    title = str(data.get("title") or "").strip()
    caption = str(data.get("caption") or "").strip()
    if not title or not caption:
        raise ValueError(f"AI output missing title/caption: {raw[:200]!r}")
    return title, caption


def generate_news_copy(fanpage, article, force_provider: AIProviderName | None = None) -> NewsCopy:
    """Generate headline + caption for one (fanpage, article) pair.

    Raises on AI failure (both providers down) or unparseable output —
    the calling task owns retry/backoff.
    """
    prompt = build_news_copy_prompt(fanpage, article)
    raw, provider = generate_caption(prompt, force_provider=force_provider)
    title, caption = _parse_news_copy(raw)

    title_max = _effective_title_max_chars(fanpage, article)
    if len(title) > title_max:
        title = title[: title_max - 1].rstrip() + "…"

    return NewsCopy(title=title, caption=caption, provider=provider)
