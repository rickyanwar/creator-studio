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
    title: str      # short headline placed on the image design (may carry **red** markers)
    caption: str    # FB post text
    provider: AIProviderName
    subtitle: str = ""  # short sub-headline for the design (may carry **red** markers)


def _effective_title_max_chars(fanpage, article) -> int:
    """Title budget: never force the AI to compress below the scraped title.

    mode2_title_max_chars acts as a floor for the budget, and the scraped
    title's own length (plus slack for an engagement hook like "BREAKING:")
    raises it — the design auto-shrinks its font to fit, so a longer
    headline is safe.
    """
    scraped_len = len(article.scraped_title or "")
    return max(fanpage.mode2_title_max_chars, scraped_len + 40)


def _eff_news(source, fanpage, src_field: str, fp_field: str):
    """Effective news-caption value: the news source's per-source override when
    set (non-empty), else the fanpage's Mode-2 criteria."""
    if source is not None:
        v = getattr(source, src_field, None)
        if v is not None and v != "":
            return v
    return getattr(fanpage, fp_field)


def build_news_copy_prompt(fanpage, article) -> str:
    news_source = article.news_source if article else None
    source_name = news_source.name if news_source else "the original source"
    attribution_line = (
        f"- End the caption with a source attribution line: \"Source: {source_name}\""
        if fanpage.mode2_source_attribution else ""
    )
    content = (article.scraped_content or "")[:_MAX_CONTENT_CHARS]

    language = _eff_news(news_source, fanpage, "caption_language", "mode2_caption_language")
    tone = _eff_news(news_source, fanpage, "caption_tone", "mode2_caption_tone")
    max_length = _eff_news(news_source, fanpage, "caption_max_length", "mode2_caption_max_length")
    hashtag_count = _eff_news(news_source, fanpage, "caption_hashtag_count", "mode2_caption_hashtag_count")
    cta_text = _eff_news(news_source, fanpage, "caption_cta_text", "mode2_caption_cta_text")
    custom_prompt = _eff_news(news_source, fanpage, "caption_custom_prompt", "mode2_caption_custom_prompt")

    return f"""You are a social media copywriter for the Facebook Fanpage "{fanpage.name}".

SOURCE NEWS ARTICLE (from {source_name}):
TITLE: {article.scraped_title}
CONTENT:
{content}

TASK: Write copy for a news image post, substantially rewritten in your own words (do not copy sentences from the source):

1. "title" — the headline that will be printed ON the image design.
   - Stay close to the source TITLE above: keep all its facts and names, and rewrite it only to make it more engaging (stronger verbs, urgency, hook like "BREAKING:" / "OFFICIAL:" when it fits). Translate to {language} if needed.
   - Keep roughly the SAME LENGTH as the source TITLE (or slightly longer with the hook) — do NOT shorten it or compress it into a vague topic label.
   - GOOD example: source "Di Giannantonio to join Red Bull KTM Factory Racing" → "BREAKING: Fabio Di Giannantonio is officially joining Red Bull KTM Factory Racing!"
   - BAD example: "MotoGP Shake-Up" (dropped the facts, too short)
   - HIGHLIGHT: wrap the SINGLE most important phrase (the key claim/result — 2 to 5 words) in double asterisks so it renders in red, e.g. "Marc Marquez takes **his first pole** at Sachsenring". Mark exactly ONE phrase; leave the rest unmarked.
   - Maximum {_effective_title_max_chars(fanpage, article)} characters (the ** markers do not count)
   - No hashtags, no emoji, no quote marks

2. "subtitle" — one short supporting sentence printed under the headline on the image.
   - Max 120 characters, same language as the title, plain factual detail that supports the headline.
   - HIGHLIGHT the key phrase (2 to 6 words) in double asterisks, same as the title.

3. "caption" — the Facebook post text that accompanies the image (NO asterisk markers here).
   - Language: {language}
   - Tone: {tone}
   - Maximum length: {max_length} characters
   - Include {hashtag_count} relevant hashtags at the end
   - End with call-to-action: {cta_text if cta_text else "none"}
{attribution_line}
   - Additional notes: {custom_prompt if custom_prompt else "none"}

OUTPUT: only a raw JSON object {{"title": "...", "subtitle": "...", "caption": "..."}} — no markdown fences, no explanation."""


def _parse_news_copy(raw: str) -> tuple[str, str, str]:
    """Parse the model's JSON output, tolerating markdown fences and stray text.
    Returns (title, subtitle, caption); subtitle may be empty."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    # models occasionally prepend/append prose — grab the outermost JSON object
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in AI output: {raw[:200]!r}")
    data = json.loads(match.group(0))
    title = str(data.get("title") or "").strip()
    subtitle = str(data.get("subtitle") or "").strip()
    caption = str(data.get("caption") or "").strip()
    if not title or not caption:
        raise ValueError(f"AI output missing title/caption: {raw[:200]!r}")
    return title, subtitle, caption


def generate_news_copy(fanpage, article, force_provider: AIProviderName | None = None) -> NewsCopy:
    """Generate headline + caption for one (fanpage, article) pair.

    Raises on AI failure (both providers down) or unparseable output —
    the calling task owns retry/backoff.
    """
    prompt = build_news_copy_prompt(fanpage, article)
    raw, provider = generate_caption(prompt, force_provider=force_provider)
    title, subtitle, caption = _parse_news_copy(raw)

    # Length check ignores the ** highlight markers; if we must truncate we drop
    # the markers (rare) rather than risk splitting a pair.
    title_max = _effective_title_max_chars(fanpage, article)
    if len(title.replace("**", "")) > title_max:
        title = title.replace("**", "")[: title_max - 1].rstrip() + "…"

    return NewsCopy(title=title, caption=caption, provider=provider, subtitle=subtitle)
