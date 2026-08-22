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
from app.services.ai_caption import log_ai_copy_event as _log_ai_copy_event

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


_QUOTE_LINE_RE = re.compile(r"^\s*🗣️.*$", re.MULTILINE)


def _sync_caption_quote(caption: str, title: str) -> str:
    """Force the caption's 🗣️ line to be exactly the same text as the design's
    quote (title) — the prompt ASKS the model to reuse it verbatim, but a
    single generation call doesn't reliably keep two fields byte-identical
    (it tends to paste the fuller/original-language quote here instead of the
    tightened, translated title), which is exactly the image/caption mismatch
    this exists to prevent. Doing it deterministically in code guarantees it
    regardless of what the model actually wrote."""
    clean_title = title.replace("**", "").strip()
    replacement = f'🗣️ "{clean_title}"'
    if _QUOTE_LINE_RE.search(caption):
        return _QUOTE_LINE_RE.sub(replacement, caption, count=1)
    # Model didn't include a 🗣️ line at all — insert one after the first
    # paragraph so it still reads naturally instead of just tacking it on.
    parts = caption.split("\n\n", 1)
    if len(parts) == 2:
        return f"{parts[0]}\n\n{replacement}\n\n{parts[1]}"
    return f"{caption}\n\n{replacement}"


@dataclass
class NewsCopy:
    title: str      # short headline placed on the image design (may carry **red** markers)
    caption: str    # FB post text
    provider: AIProviderName
    subtitle: str = ""  # short sub-headline for the design (may carry **red** markers)
    category: str = "news"  # "news" | "quote" — which template pool to render on
    is_breaking: bool = False  # significant enough to skip the normal publish pacing queue


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

    return f"""Act as a professional sports journalist and social media editor — the caliber of ESPN, Bleacher Report, or a top motorsport/combat-sports fan page — writing for the Facebook Fanpage "{fanpage.name}". You've covered this sport for years: you know the storylines, the rivalries, and how a real beat reporter phrases things. Sharp, credible, and highly readable. Never generic, never robotic, never clickbait-fake.

SOURCE NEWS ARTICLE (from {source_name}):
TITLE: {article.scraped_title}
CONTENT:
{content}

TASK 1 — CLASSIFY. Decide which design fits this article best:
   - "quote": choose this ONLY when a quoted statement from a named person IS the actual news — the headline-worthy hook is what someone SAID (a bold reaction, callout, promise, controversial claim), not something that happened. Ask: "if I had to headline this in one line, would it be the quote itself?" — only then is it "quote".
   - "news": everything else, INCLUDING articles that happen to contain a quote as supporting color. If the headline-worthy hook is an event — a result, transfer, signing, injury, ranking, schedule change, announcement — classify it "news" even if a person is quoted somewhere in the body reacting to it. A quote can still appear in the caption body either way (see TASK 2, item 3).
   - Default to "news" when unsure — this matters: aim for roughly 9 out of 10 articles landing as "news". "quote" is a rare exception (a person's own words being the whole story, not just color commentary), not a coin flip. A sports article quoting someone reacting to a result is still "news" about that result.

TASK 1B — IS THIS BREAKING? Set "is_breaking" true ONLY for the kind of story fans are actively refreshing feeds for right now — a major contract signing/extension, a driver/rider/fighter transfer, a title-deciding or major race/fight RESULT, a big injury, a retirement or major roster change, an official confirmation of something that's been rumored. Ask: "would a fan who missed this for 3 hours feel like they missed something?" If yes → true. Routine content — previews, analysis, rankings chatter, minor updates, opinion pieces, anything speculative/rumor-stage — is "is_breaking": false. This should be RARE (most articles are false); when true, this post skips the normal publish queue and goes out immediately, so only flag it for stories that actually deserve that.

TASK 2 — WRITE THE COPY, substantially rewritten in your own words (do not copy sentences from the source). The fields depend on the type you chose:

IF "type" is "news":
1. "title" — the headline that will be printed ON the image design.
   - Stay close to the source TITLE above: keep all its facts and names — do NOT invent, exaggerate, or imply something the article doesn't say. Within that constraint, punch it up: stronger verbs, urgency, a hook ("BREAKING:" / "OFFICIAL:" / "CONFIRMED:" when it genuinely fits), a touch of drama in how it's phrased. Slightly clickbait in DELIVERY is good — a flat wire-service rewrite is not the goal — but never clickbait in SUBSTANCE (no bait-and-switch, no withheld info the reader has to click to get; a design card has no "click" to withhold behind anyway). Translate to {language} if needed.
   - Keep roughly the SAME LENGTH as the source TITLE (or slightly longer with the hook) — do NOT shorten it or compress it into a vague topic label.
   - GOOD example: source "Di Giannantonio to join Red Bull KTM Factory Racing" → "BREAKING: Fabio Di Giannantonio is officially joining Red Bull KTM Factory Racing!"
   - BAD example: "MotoGP Shake-Up" (dropped the facts, too short)
   - BAD example: "You Won't Believe What Marquez Just Did" (withholds the actual news — this is the "far from the original title" failure mode to avoid)
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
   - IMPORTANT: this exact text (words, language) is reused verbatim as the caption's 🗣️ line below — write it as a real, accurate quote, not a mashed-up paraphrase that adds claims from elsewhere in the article.
2. "subtitle" — ONLY the speaker's full name, exactly as it appears in the article, and NOTHING else — no role, title, team, or descriptor of any kind, and no comma.
   - GOOD: "Marc Marquez"
   - BAD: "Marc Marquez World Champion", "Marc Marquez, MotoGP Champion", "Marc Marquez (Ducati)"
   - Max 40 characters. No asterisks.

3. "caption" — the Facebook post text that accompanies the image (NO asterisk markers here), same for either type.
   - Language: {language}
   - Tone: {tone}
   - Maximum length: {max_length} characters
   - Formatting: write in short paragraphs (1-3 sentences each) separated by a blank line — never one dense block.
   - If "type" is "quote": the 🗣️ quoted line is REQUIRED, on its own line with a blank line before and after (e.g. a blank line, then the 🗣️ line, then a blank line), and it MUST be word-for-word the same text as the "title" field above (strip the ** markers, same {language} wording — do not re-translate it separately, re-phrase it, or fall back to the source language). The image and the caption must show the reader the exact same quote.
   - If "type" is "news" and you choose to quote the source directly anywhere in the body, that quote is optional, may be any relevant line from the source (not tied to "title"), and follows the same 🗣️ on-its-own-line formatting.
   - Hashtags: put a blank line before the hashtag line, then EXACTLY {hashtag_count} relevant, specific hashtags on that single line — never more, and never generic filler tags (no #love #instagood #viral).
   - End with call-to-action: {cta_text if cta_text else "none"}
{attribution_line}
   - Additional notes: {custom_prompt if custom_prompt else "none"}
   - Write like a human beat reporter who actually follows this sport, not a language model summarizing an article. Concretely avoid: generic openers ("In an exciting turn of events...", "Big news for fans of..."), telling the reader how to feel instead of giving them a reason to feel it ("This is huge!", "Unbelievable!" with nothing concrete backing it up), restating the headline in slightly different words as the first sentence, hedge-everything phrasing ("it seems that", "reportedly" used more than once), and stacking generic adjectives (exciting, amazing, incredible) instead of a specific detail. Instead: lead with the single most interesting concrete fact, use plain confident sentences a real editor would publish, and let specifics (numbers, names, direct stakes) do the work a superlative would otherwise be doing.

OUTPUT: only a raw JSON object {{"type": "news|quote", "is_breaking": true|false, "title": "...", "subtitle": "...", "caption": "..."}} — no markdown fences, no explanation."""


def _parse_news_copy(raw: str) -> tuple[str, bool, str, str, str]:
    """Parse the model's JSON output, tolerating markdown fences and stray text.
    Returns (category, is_breaking, title, subtitle, caption); subtitle may be empty."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    # models occasionally prepend/append prose — grab the outermost JSON object
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in AI output: {raw[:200]!r}")
    data = json.loads(match.group(0))
    category = str(data.get("type") or "news").strip().lower()
    if category not in ("news", "quote"):
        category = "news"
    is_breaking = bool(data.get("is_breaking") is True)
    title = str(data.get("title") or "").strip()
    subtitle = str(data.get("subtitle") or "").strip()
    caption = str(data.get("caption") or "").strip()
    if not title or not caption:
        raise ValueError(f"AI output missing title/caption: {raw[:200]!r}")
    return category, is_breaking, title, subtitle, caption


# ── Mode 4: Discussion / hot-take cards ──────────────────────────────────────

# The big debate line is auto-shrunk to fit by the renderer, but keep it punchy —
# these cards live or die on a single readable line, not a paragraph.
_DISCUSSION_MAX_QUESTION_CHARS = 90
_DISCUSSION_LABELS = ("DISCUSSION", "HOT TAKE")


@dataclass
class DiscussionCopy:
    label: str        # "DISCUSSION" | "HOT TAKE" — badge text (renderer colours it)
    question: str     # the big debate line printed on the image
    subject_name: str # person/subject to source the photo for (gallery→Getty)
    caption: str      # FB post text
    provider: AIProviderName


def _discussion_caption_block(fanpage, source_name: str | None) -> str:
    """Caption-writing instructions for a discussion card, reusing the fanpage's
    Mode-2 caption criteria (language/tone/length/hashtags/CTA/custom)."""
    attribution_line = ""
    if source_name and fanpage.mode2_source_attribution:
        attribution_line = f'\n   - End the caption with a source attribution line: "Source: {source_name}"'
    cta_text = fanpage.mode2_caption_cta_text
    custom_prompt = fanpage.mode2_caption_custom_prompt
    return f"""3. "caption" — the Facebook post text that accompanies the image (no ** markers here).
   - Language: {fanpage.mode2_caption_language}
   - Tone: {fanpage.mode2_caption_tone}
   - Maximum length: {fanpage.mode2_caption_max_length} characters
   - Formatting: short paragraphs (1-3 sentences), separated by a blank line — never one dense block.
   - It MUST invite the reader to take a side and comment their opinion — this is a debate post. End by explicitly asking readers to drop their verdict in the comments (this REPLACES the old yes/no buttons).
   - Hashtags: a blank line, then EXACTLY {fanpage.mode2_caption_hashtag_count} specific, relevant hashtags on one line — never generic filler (#love #viral #instagood).
   - Call-to-action: {cta_text if cta_text else "ask readers to comment their take"}{attribution_line}
   - Additional notes: {custom_prompt if custom_prompt else "none"}"""


def build_discussion_news_prompt(fanpage, article) -> str:
    """Prompt: turn a scraped article into a debate card grounded in its facts."""
    news_source = article.news_source if article else None
    source_name = news_source.name if news_source else "the original source"
    content = (article.scraped_content or "")[:_MAX_CONTENT_CHARS]
    language = fanpage.mode2_caption_language

    return f"""Act as a top-tier sports social media editor for the Facebook Fanpage "{fanpage.name}" — the caliber of Bleacher Report or a big motorsport fan page. Your job: turn one news story into a DEBATE post that makes fans argue in the comments.

SOURCE NEWS ARTICLE (from {source_name}):
TITLE: {article.scraped_title}
CONTENT:
{content}

Produce ONE discussion card grounded in the facts of this article.

1. "label" — either "DISCUSSION" (an open question, e.g. "Is George the most hated driver on the grid?") or "HOT TAKE" (a bold, arguable claim stated as fact, e.g. "Lewis was fully robbed of the 2016 championship"). Pick whichever is more provocative for this story.
2. "question" — the single big line printed on the image.
   - If label is "DISCUSSION": phrase it as a yes/no-style question ending in "?".
   - If label is "HOT TAKE": phrase it as a punchy declarative statement (no question mark), and you MAY wrap it in quotes only if it reads like a spoken take.
   - Language: {language}. Max {_DISCUSSION_MAX_QUESTION_CHARS} characters. No hashtags, no emoji.
   - It must be DEBATABLE (roughly 50/50) — not something everyone already agrees on. Stay honest to the article's facts; do NOT invent results, numbers, or quotes.
3. "subject" — the ONE person the card photo should show (the central figure of the debate), full name exactly as in the article, nothing else. Max 40 chars.
{_discussion_caption_block(fanpage, source_name)}

OUTPUT: only a raw JSON object {{"label": "DISCUSSION|HOT TAKE", "question": "...", "subject": "...", "caption": "..."}} — no markdown fences, no explanation."""


def build_discussion_evergreen_prompt(fanpage, seed_text: str, subject_hint: str | None) -> str:
    """Prompt: turn an evergreen debate seed into a polished discussion card.

    Opinion-only by design — evergreen topics carry no fresh article to fact-check
    against, so the model is told to avoid hard facts (specific stats, dates,
    results) that it might get wrong, and stick to subjective debate.
    """
    language = fanpage.mode2_caption_language
    hint_line = f'\nSUBJECT HINT: {subject_hint}' if subject_hint else ""

    return f"""Act as a top-tier sports social media editor for the Facebook Fanpage "{fanpage.name}". Turn the debate seed below into a polished DISCUSSION post that makes fans argue in the comments.

DEBATE SEED (the user's idea, may be rough): {seed_text}{hint_line}

1. "label" — "DISCUSSION" (an open opinion question) or "HOT TAKE" (a bold, arguable opinion stated as a claim). Prefer "DISCUSSION" for evergreen debates.
2. "question" — the single big line printed on the image.
   - If label is "DISCUSSION": a yes/no-style opinion question ending in "?".
   - If label is "HOT TAKE": a punchy declarative opinion (no question mark).
   - Language: {language}. Max {_DISCUSSION_MAX_QUESTION_CHARS} characters. No hashtags, no emoji.
   - CRITICAL: this is EVERGREEN with no article to fact-check against. Keep it a pure OPINION/subjective debate (greatness, rankings, likability, "overrated?", "GOAT?"). Do NOT state or imply specific facts, statistics, dates, results, or quotes — you may get them wrong. No hard numbers.
3. "subject" — the ONE person the card photo should show, full name. Use the subject hint if given. Max 40 chars.
{_discussion_caption_block(fanpage, None)}

OUTPUT: only a raw JSON object {{"label": "DISCUSSION|HOT TAKE", "question": "...", "subject": "...", "caption": "..."}} — no markdown fences, no explanation."""


def build_discussion_general_prompt(
    fanpage, avoid_subjects: list[str], recent_headlines: list[str],
    recent_questions: list[str] | None = None, todays_questions: list[str] | None = None,
) -> str:
    """Prompt: invent a debate card from the model's own general/current
    knowledge of the fanpage's niche — used when there's no fresh unclaimed
    article and no evergreen seed left. Unlike the evergreen prompt, this one
    is allowed real stats/records/results (it's the only source of the topic
    itself, so banning facts would leave nothing to debate) — the model is
    just told to stay qualitative on any number it isn't confident about
    rather than invent a precise one.

    `recent_headlines` (this fanpage's own news feed, last ~2 months) is
    passed as best-effort grounding so a stale training cutoff doesn't
    produce a claim already contradicted by a known result — e.g. framing a
    now-decided title race as still open. It's a second line of defense on
    top of steering the model away from settled-outcome predictions; the
    caller (discussion.py) still runs a separate fact-check pass on the
    output before accepting it.

    `recent_questions` (this fanpage's own last ~week of discussion cards,
    ANY subject) exists for a different reason than avoid_subjects: found
    2026-08-22 via a real user report — a fanpage's cards had all converged
    on the exact same "is X the most underrated rider" angle across many
    DIFFERENT subjects, because that framing is this prompt's own explicit
    example AND the safest possible answer under the "don't bet on an
    undecided outcome" rule below, so the model kept defaulting to it even
    though avoid_subjects was correctly blocking literal subject repeats.
    Showing the actual recent lines lets the model see the pattern it's
    stuck in and break out of it, which merely varying the subject name
    can't fix on its own.

    `todays_questions` (this fanpage's own cards from TODAY's WIB calendar
    day only) is a HARD constraint layered on top of `recent_questions`'
    softer week-long guidance — user's explicit ask (2026-08-22): a single
    day's whole batch must read as genuinely different card-to-card, which
    is a tighter bar than merely not repeating within the week."""
    language = fanpage.mode2_caption_language
    niche = (fanpage.mode2_gallery_niches or [None])[0] or fanpage.name
    avoid_line = ""
    if avoid_subjects:
        avoid_line = f"\nAVOID these subjects — already covered recently, pick someone/something else: {', '.join(avoid_subjects)}"
    headlines_block = ""
    if recent_headlines:
        headlines_list = "\n".join(f"- {h}" for h in recent_headlines)
        headlines_block = f"\n\nRECENT HEADLINES from this page's own news feed (last ~2 months, for context only — treat any result/outcome they report as already decided, do not contradict them):\n{headlines_list}"
    recent_questions_block = ""
    if recent_questions:
        rq_list = "\n".join(f"- {q}" for q in recent_questions)
        recent_questions_block = (
            f"\n\nTHIS PAGE'S OWN RECENT DISCUSSION CARDS (last ~week — do NOT reuse "
            f"this same angle/framing/sentence-structure again, even on a different "
            f"person; pick a genuinely different TYPE of debate this time):\n{rq_list}"
        )
    todays_block = ""
    if todays_questions:
        tq_list = "\n".join(f"- {q}" for q in todays_questions)
        todays_block = (
            f"\n\nALREADY POSTED TODAY on this exact page (HARD constraint — today's "
            f"whole batch must feel genuinely different card-to-card, this is stricter "
            f"than the week-long list above): do NOT repeat any of these angles, "
            f"subjects, or sentence-structures today, no exceptions:\n{tq_list}"
        )

    return f"""Act as a top-tier sports social media editor for the Facebook Fanpage "{fanpage.name}" (niche: {niche}). There is no fresh news article to work from right now — invent an ORIGINAL debate post from your own knowledge of {niche} that will make fans argue in the comments.{avoid_line}{headlines_block}{recent_questions_block}{todays_block}

Produce ONE discussion card about a genuinely contested, current-or-recent topic in {niche}. Vary the ANGLE each time — don't lean on the same type of take repeatedly. Examples of DIFFERENT angles to rotate across (not an exhaustive list, and not a ranking of which to prefer): a head-to-head rivalry ("who wins between X and Y"), a controversial call/decision, a team/contract/lineup decision, a tactical or technical debate, a legacy/GOAT comparison, a retirement or move question, "was X right to do Y", an underrated/overrated take. An underrated/overrated take is only ONE option among many, not a default — do not reach for it just because it's easy to make fact-check-proof; if this page has used it recently (see above), pick a different angle this time.

It must feel genuinely FRESH, not a reskin of something this page already ran with a different name slotted in. Before finalizing, judge which realistic candidate topic would actually drive the STRONGEST engagement (comments, shares, arguments) from this niche's fans RIGHT NOW — favor something genuinely divisive and current over a generic, safe, low-friction pick.

1. "label" — either "DISCUSSION" (an open question) or "HOT TAKE" (a bold, arguable claim stated as fact). Pick whichever is more provocative.
2. "question" — the single big line printed on the image.
   - If label is "DISCUSSION": phrase as a yes/no-style question ending in "?".
   - If label is "HOT TAKE": phrase as a punchy declarative statement (no question mark).
   - Language: {language}. Max {_DISCUSSION_MAX_QUESTION_CHARS} characters. No hashtags, no emoji.
   - It must be DEBATABLE (roughly 50/50) — not something everyone already agrees on.
   - CRITICAL — do not bet on an undecided outcome: avoid framing like "X will win/beat/achieve Y" about a title, race, or result that may already be decided by the time this posts. Prefer debating something that stays true regardless of results already in: ability, decisions, legacy, comparisons, rivalries, tactics, "was X right to do Y". If you genuinely aren't sure whether an outcome is already decided, don't bet on it — debate the reasoning/opinion around it instead. This constraint is about TIMING, not an instruction to default to any one specific angle.
   - You may cite facts/stats/records you're genuinely confident about, but if you're not sure of an exact number, describe it qualitatively (e.g. "dominated" instead of guessing a score) rather than risk stating something wrong.
3. "subject" — the ONE person the card photo should show (the central figure of the debate), full name. Max 40 chars.
{_discussion_caption_block(fanpage, None)}

OUTPUT: only a raw JSON object {{"label": "DISCUSSION|HOT TAKE", "question": "...", "subject": "...", "caption": "..."}} — no markdown fences, no explanation."""


def build_discussion_factcheck_prompt(
    question: str, subject: str, niche: str, grounding_headlines: list[str]
) -> str:
    """Prompt: a second, independent 9Router call that checks a drafted
    general-knowledge hot-take/discussion line before it's accepted. Checking
    with the model's bare memory alone doesn't catch a claim that's wrong
    because of a stale training cutoff — the same blind spot that produced
    the claim would also wave it through at review time. `grounding_headlines`
    (this page's own recent article titles that mention the names in the
    claim — see discussion.py's _grounding_headlines_for_claim) gives the
    checker concrete, page-specific evidence to weigh against its own memory,
    which is what actually catches that class of mistake."""
    headlines_block = ""
    if grounding_headlines:
        headlines_list = "\n".join(f"- {h}" for h in grounding_headlines)
        headlines_block = f"\n\nRECENT HEADLINES from this page's own news feed mentioning the people in this claim — treat these as ground truth over your own memory if they conflict:\n{headlines_list}"

    return f"""You are a strict fact-checker for a {niche} social media page. A colleague drafted this line to post as a debate/hot-take card:

LINE: "{question}"
SUBJECT: {subject}{headlines_block}

Based on the headlines above (if given) and your own knowledge of {niche}, does this line contradict something that has ALREADY been decided or is already publicly known (e.g. it bets on an outcome that has already happened the other way, states a result that's factually wrong, or references a status that's no longer current)? A genuinely open/debatable opinion is fine and should pass — you are only rejecting lines that assert something you're confident is factually wrong or already settled differently. If the headlines above conflict with what you recall, trust the headlines — they're this page's own recent coverage.

OUTPUT: only a raw JSON object {{"valid": true|false, "reason": "one short sentence"}} — no markdown fences, no explanation outside the JSON."""


def _parse_discussion_factcheck(raw: str) -> tuple[bool, str]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in AI output: {raw[:200]!r}")
    data = json.loads(match.group(0))
    valid = bool(data.get("valid", True))
    reason = str(data.get("reason") or "").strip()
    return valid, reason


def factcheck_discussion_claim(
    question: str,
    subject: str,
    niche: str,
    grounding_headlines: list[str] | None = None,
    force_provider: AIProviderName | None = None,
) -> tuple[bool, str]:
    """Ask 9Router to sanity-check a general-knowledge discussion draft,
    grounded in this page's own recent coverage when available. Raises on AI
    failure — the caller decides whether to retry or skip; a failed
    fact-check call should never silently wave a claim through."""
    prompt = build_discussion_factcheck_prompt(question, subject, niche, grounding_headlines or [])
    (valid, reason), _provider = _generate_with_fallback(
        prompt, _parse_discussion_factcheck, force_provider,
        context="discussion_factcheck",
    )
    return valid, reason


def _parse_discussion_copy(raw: str) -> tuple[str, str, str, str]:
    """Parse the model's JSON, tolerating fences/prose. Returns
    (label, question, subject, caption)."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in AI output: {raw[:200]!r}")
    data = json.loads(match.group(0))
    label = str(data.get("label") or "DISCUSSION").strip().upper()
    if label not in _DISCUSSION_LABELS:
        # tolerate "HOTTAKE"/"HOT-TAKE"/etc.
        label = "HOT TAKE" if "HOT" in label else "DISCUSSION"
    question = str(data.get("question") or "").strip()
    subject = str(data.get("subject") or "").strip()
    caption = str(data.get("caption") or "").strip()
    if not question or not caption:
        raise ValueError(f"AI output missing question/caption: {raw[:200]!r}")
    return label, question, subject, caption


def _generate_with_fallback(
    prompt: str,
    parse_fn,
    force_provider: AIProviderName | None = None,
    *,
    context: str,
    fanpage_id: int | None = None,
    article_id: int | None = None,
):
    """Call generate_caption and parse the result. A 9Router response can come
    back HTTP 200 but truncated (reasoning-heavy routes like My-Combo can burn
    most of the token budget on hidden reasoning before the visible JSON) —
    that's a parse failure, not an API failure, so generate_caption()'s own
    provider fallback never sees it and the caller would otherwise lose the
    whole (article, fanpage) pair.

    On a router parse failure: try each of ROUTER_MODEL_FALLBACKS directly
    (a same-model retry wouldn't help — the truncation is deterministic per
    prompt, not transient) before giving up on 9Router and retrying once via
    Gemini as the last resort. Every outcome (success/recovered/failed) is
    logged to ai_copy_events so degradation is visible on the dashboard
    without grepping worker logs on the VPS.

    Discussion contexts ("discussion_copy"/"discussion_factcheck") get
    NineRouterConfig.discussion_model tried FIRST (see generate_caption's
    preferred_router_model) — a stronger combo route reserved for Mode 4
    hot-take/discussion, editable from Settings without touching news copy.
    """
    import time

    t0 = time.monotonic()
    models_tried: list[str] = []

    def _elapsed_ms() -> int:
        return int((time.monotonic() - t0) * 1000)

    preferred_router_model = None
    if force_provider is None and context.startswith("discussion"):
        from app.services.nine_router import get_nine_router_config
        preferred_router_model = get_nine_router_config().discussion_model

    raw, provider = generate_caption(prompt, force_provider=force_provider, preferred_router_model=preferred_router_model)
    models_tried.append(provider)
    try:
        result = parse_fn(raw)
        _log_ai_copy_event(
            context=context, fanpage_id=fanpage_id, article_id=article_id,
            outcome="success", models_tried=models_tried, final_provider=provider,
            error_message=None, latency_ms=_elapsed_ms(),
        )
        return result, provider
    except ValueError as parse_exc:
        if force_provider is not None:
            _log_ai_copy_event(
                context=context, fanpage_id=fanpage_id, article_id=article_id,
                outcome="failed", models_tried=models_tried, final_provider=None,
                error_message=str(parse_exc), latency_ms=_elapsed_ms(),
            )
            raise

        if provider == "groq":
            # Groq (gpt-oss-120b, a reasoning model) can still come back
            # empty/truncated on some prompts even with a generous max_tokens
            # (see _call_groq) — this branch only gets reached when 9Router
            # itself was already exhausted (generate_caption fell through to
            # Groq internally), so a same-provider retry wouldn't help; one
            # retry via Gemini before giving up, same "don't lose the whole
            # pair over one bad response" philosophy as the router recovery
            # below. Found 2026-08-18: a 9Router outage forced several
            # fanpages onto Groq, and every one of its unparseable responses
            # was a permanently lost post with no recovery attempt at all.
            logger.warning("Groq output failed to parse — retrying via Gemini: %s", parse_exc)
            models_tried.append("gemini")
            try:
                raw, recovered_provider = generate_caption(prompt, force_provider="gemini")
                result = parse_fn(raw)
                _log_ai_copy_event(
                    context=context, fanpage_id=fanpage_id, article_id=article_id,
                    outcome="recovered", models_tried=models_tried, final_provider=recovered_provider,
                    error_message=f"groq output unparseable: {parse_exc}",
                    latency_ms=_elapsed_ms(),
                )
                return result, recovered_provider
            except Exception as final_exc:
                _log_ai_copy_event(
                    context=context, fanpage_id=fanpage_id, article_id=article_id,
                    outcome="failed", models_tried=models_tried, final_provider=None,
                    error_message=str(final_exc), latency_ms=_elapsed_ms(),
                )
                raise

        if provider != "router":
            _log_ai_copy_event(
                context=context, fanpage_id=fanpage_id, article_id=article_id,
                outcome="failed", models_tried=models_tried, final_provider=None,
                error_message=str(parse_exc), latency_ms=_elapsed_ms(),
            )
            raise

        from app.services.ai_caption import ROUTER_MODEL_FALLBACKS, call_router_model

        last_error: Exception = parse_exc
        for model in ROUTER_MODEL_FALLBACKS:
            models_tried.append(model)
            try:
                raw = call_router_model(prompt, model)
                result = parse_fn(raw)
                _log_ai_copy_event(
                    context=context, fanpage_id=fanpage_id, article_id=article_id,
                    outcome="recovered", models_tried=models_tried, final_provider="router",
                    error_message=f"primary router model failed: {parse_exc}",
                    latency_ms=_elapsed_ms(),
                )
                return result, "router"
            except Exception as exc:
                last_error = exc
                logger.warning("9Router fallback model %s also failed: %s", model, exc)

        logger.warning("All 9Router models failed to produce parseable output — retrying via Gemini")
        models_tried.append("gemini")
        try:
            raw, provider = generate_caption(prompt, force_provider="gemini")
            result = parse_fn(raw)
            _log_ai_copy_event(
                context=context, fanpage_id=fanpage_id, article_id=article_id,
                outcome="recovered", models_tried=models_tried, final_provider=provider,
                error_message=f"9Router exhausted, last error: {last_error}",
                latency_ms=_elapsed_ms(),
            )
            return result, provider
        except Exception as final_exc:
            _log_ai_copy_event(
                context=context, fanpage_id=fanpage_id, article_id=article_id,
                outcome="failed", models_tried=models_tried, final_provider=None,
                error_message=str(final_exc), latency_ms=_elapsed_ms(),
            )
            raise


def generate_discussion_copy(
    fanpage,
    *,
    article=None,
    seed_text: str | None = None,
    subject_hint: str | None = None,
    avoid_subjects: list[str] | None = None,
    recent_headlines: list[str] | None = None,
    recent_questions: list[str] | None = None,
    todays_questions: list[str] | None = None,
    force_provider: AIProviderName | None = None,
) -> DiscussionCopy:
    """Generate one Mode 4 discussion card. Pass `article` (news-seeded,
    fact-grounded), `seed_text` (evergreen, opinion-only), or neither — the
    model then invents the whole topic from its own knowledge of the
    fanpage's niche (general-knowledge fallback, used when both other
    sources are exhausted). `recent_headlines`/`recent_questions`/
    `todays_questions` only apply to that last case — see
    build_discussion_general_prompt.

    Raises on AI failure (both providers down) or unparseable output — the
    calling task owns retry/backoff.
    """
    if article is not None:
        prompt = build_discussion_news_prompt(fanpage, article)
    elif seed_text:
        prompt = build_discussion_evergreen_prompt(fanpage, seed_text, subject_hint)
    else:
        prompt = build_discussion_general_prompt(
            fanpage, avoid_subjects or [], recent_headlines or [], recent_questions or [],
            todays_questions or [],
        )

    (label, question, subject, caption), provider = _generate_with_fallback(
        prompt, _parse_discussion_copy, force_provider,
        context="discussion_copy", fanpage_id=fanpage.id,
        article_id=article.id if article is not None else None,
    )

    # Prefer an explicit evergreen subject hint when the model returned nothing.
    if not subject and subject_hint:
        subject = subject_hint.strip()

    if len(question) > _DISCUSSION_MAX_QUESTION_CHARS:
        question = question[: _DISCUSSION_MAX_QUESTION_CHARS - 1].rstrip() + "…"

    return DiscussionCopy(
        label=label, question=question, subject_name=subject, caption=caption, provider=provider
    )


def generate_news_copy(fanpage, article, force_provider: AIProviderName | None = None) -> NewsCopy:
    """Generate headline + caption for one (fanpage, article) pair, classifying
    it as a "news" or "quote" design in the same call (see build_news_copy_prompt).

    Raises on AI failure (both providers down) or unparseable output —
    the calling task owns retry/backoff.
    """
    prompt = build_news_copy_prompt(fanpage, article)
    (category, is_breaking, title, subtitle, caption), provider = _generate_with_fallback(
        prompt, _parse_news_copy, force_provider,
        context="news_copy", fanpage_id=fanpage.id, article_id=article.id,
    )

    if category == "quote" and subtitle:
        subtitle = _clean_quote_subtitle(subtitle)

    # Length check ignores the ** highlight markers; if we must truncate we drop
    # the markers (rare) rather than risk splitting a pair. Quote titles use a
    # fixed short cap (they're a punchy standalone line, not a headline).
    title_max = 140 if category == "quote" else _effective_title_max_chars(fanpage, article)
    if len(title.replace("**", "")) > title_max:
        title = title.replace("**", "")[: title_max - 1].rstrip() + "…"

    if category == "quote":
        caption = _sync_caption_quote(caption, title)

    return NewsCopy(
        title=title, caption=caption, provider=provider, subtitle=subtitle,
        category=category, is_breaking=is_breaking,
    )
