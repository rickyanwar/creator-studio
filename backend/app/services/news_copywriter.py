"""News copywriter — rewrite a scraped article into a design headline + FB caption.

One AI call per (article, fanpage) returning structured JSON:
  {"type": "news|quote", "title": "...", "subtitle": "...", "caption": "..."}

The same call also classifies the article: a "news" design (headline +
supporting line) or a "quote" design (a standalone quotable statement from a
named person in the article + their name for the template's name badge) —
same two categories, and the same title/subtitle placeholder pair, that
Mode 3 IG-recreate already classifies into (see ig_content_classifier.py).
The caller resolves the fanpage's matching default_{quote,news}_template_id
via design_images.resolve_template.

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

# Role/descriptor words the AI occasionally tacks onto a quote's name-badge
# subtitle despite instructions not to (e.g. "Marc Marquez World Champion").
# Stripped as a deterministic safety net since the badge should show only the
# speaker's name.
_NAME_ROLE_SUFFIX_RE = re.compile(
    r"\s*[,\-–—(]\s*.*$|"
    r"\s+\b(world\s+champion(s)?|champion(s)?|winner|title\s*holder|"
    r"ceo|president|founder|coach|manager|captain|coordinator|"
    r"team\s+principal|spokesperson|director|chairman|"
    r"motogp\s+rider|rider|driver|player|athlete|star|legend|icon)\b.*$",
    flags=re.IGNORECASE,
)


def _clean_quote_subtitle(subtitle: str) -> str:
    """Strip a trailing role/title descriptor from a quote card's name-badge
    text, keeping only the speaker's name (see _NAME_ROLE_SUFFIX_RE)."""
    # A comma almost always separates "Name, Role" — cut there first.
    name = subtitle.split(",")[0].strip()
    name = _NAME_ROLE_SUFFIX_RE.sub("", name).strip()
    return name or subtitle.strip()


@dataclass
class NewsCopy:
    title: str      # short headline placed on the image design (may carry **red** markers)
    caption: str    # FB post text
    provider: AIProviderName
    subtitle: str = ""  # short sub-headline for the design (may carry **red** markers)
    category: str = "news"  # "news" | "quote" — which template pool to render on


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

TASK 1 — CLASSIFY. Decide which design fits this article best:
   - "quote": the article contains an actual quoted statement, in quotation marks or clearly attributed dialogue, from a named person — a reaction, promise, criticism, or bold claim that stands strongly on its own as a standalone quote card. Only choose this when a real quote is present in the source; never invent one.
   - "news": everything else — a headline/announcement design.

TASK 2 — WRITE THE COPY, substantially rewritten in your own words (do not copy sentences from the source). The fields depend on the type you chose:

IF "type" is "news":
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

IF "type" is "quote":
1. "title" — the quote itself, printed on the image next to a large quote-mark icon.
   - Translate to {language} if needed, tightened for punch but keep the original meaning and claim — do not soften or invent words.
   - Do NOT include the speaker's name here (that goes in "subtitle") and do NOT wrap it in quotation marks (the design already implies it's a quote).
   - HIGHLIGHT the single most powerful phrase (2 to 5 words) in double asterisks, same rule as the news title.
   - Maximum 140 characters.
2. "subtitle" — ONLY the speaker's full name, exactly as it appears in the article, and NOTHING else — no role, title, team, or descriptor of any kind, and no comma.
   - GOOD: "Marc Marquez"
   - BAD: "Marc Marquez World Champion", "Marc Marquez, MotoGP Champion", "Marc Marquez (Ducati)"
   - Max 40 characters. No asterisks.

3. "caption" — the Facebook post text that accompanies the image (NO asterisk markers here), same for either type.
   - Language: {language}
   - Tone: {tone}
   - Maximum length: {max_length} characters
   - Include {hashtag_count} relevant hashtags at the end
   - End with call-to-action: {cta_text if cta_text else "none"}
{attribution_line}
   - Additional notes: {custom_prompt if custom_prompt else "none"}

OUTPUT: only a raw JSON object {{"type": "news|quote", "title": "...", "subtitle": "...", "caption": "..."}} — no markdown fences, no explanation."""


def _parse_news_copy(raw: str) -> tuple[str, str, str, str]:
    """Parse the model's JSON output, tolerating markdown fences and stray text.
    Returns (category, title, subtitle, caption); subtitle may be empty."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    # models occasionally prepend/append prose — grab the outermost JSON object
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in AI output: {raw[:200]!r}")
    data = json.loads(match.group(0))
    category = str(data.get("type") or "news").strip().lower()
    if category not in ("news", "quote"):
        category = "news"
    title = str(data.get("title") or "").strip()
    subtitle = str(data.get("subtitle") or "").strip()
    caption = str(data.get("caption") or "").strip()
    if not title or not caption:
        raise ValueError(f"AI output missing title/caption: {raw[:200]!r}")
    return category, title, subtitle, caption


def generate_news_copy(fanpage, article, force_provider: AIProviderName | None = None) -> NewsCopy:
    """Generate headline + caption for one (fanpage, article) pair, classifying
    it as a "news" or "quote" design in the same call (see build_news_copy_prompt).

    Raises on AI failure (both providers down) or unparseable output —
    the calling task owns retry/backoff.
    """
    prompt = build_news_copy_prompt(fanpage, article)
    raw, provider = generate_caption(prompt, force_provider=force_provider)
    category, title, subtitle, caption = _parse_news_copy(raw)

    if category == "quote" and subtitle:
        subtitle = _clean_quote_subtitle(subtitle)

    # Length check ignores the ** highlight markers; if we must truncate we drop
    # the markers (rare) rather than risk splitting a pair. Quote titles use a
    # fixed short cap (they're a punchy standalone line, not a headline).
    title_max = 140 if category == "quote" else _effective_title_max_chars(fanpage, article)
    if len(title.replace("**", "")) > title_max:
        title = title.replace("**", "")[: title_max - 1].rstrip() + "…"

    return NewsCopy(title=title, caption=caption, provider=provider, subtitle=subtitle, category=category)
