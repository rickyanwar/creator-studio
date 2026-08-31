import json
import logging
import re
from typing import Literal
from app.services.ai_caption import _call_router

logger = logging.getLogger(__name__)


def _has_real_quote(text: str) -> bool:
    """True only for a quoted span that reads as an actual quotation (a
    clause/sentence), not a short nickname-in-quotes embedded in a name —
    e.g. 'Anderson "The Spider" Silva' must NOT count, but a standalone
    '"I'm the best there has ever been"' should. Nickname-in-quotes is an
    extremely common convention in combat sports/motorsport naming and
    reliably misfires a naive any-quote-character check. Requires the
    quoted span to be at least 4 words — a nickname is 1-3 words, a real
    quotation is a clause or longer."""
    for m in re.finditer(r'["“”]([^"“”]{6,})["“”]', text or ""):
        if len(m.group(1).strip().split()) >= 4:
            return True
    return False


def classify_pinterest_content(title: str, description: str) -> Literal["quote", "news"]:
    """Mode 5: decide which template pool (quote vs news) an idea's title
    should render on. The TITLE is the only field that ever gets printed
    large on the card — description is a Facebook-caption-only field (see
    app/tasks/pinterest.py's _consume_one) and must never drive this
    decision on its own.

    Real incident, 2026-08-31: 3 Fight Today posts (Anderson Silva, Jon
    Jones vs Dominick Reyes, Conor McGregor) shipped on the Quote Card
    template even though their titles were plain names/headlines with no
    spoken quote at all — the card rendered the title as a giant "quote"
    with decorative quote marks, and the REAL content (the description)
    got squeezed into the template's small attribution-line slot, illegible.
    Root cause was two-fold, both fixed here:
    1. A control-flow bug: when the AI correctly answered "news", the old
       code only ever checked `if t == "quote": return "quote"` — a "news"
       answer fell through with no explicit return and was silently
       DISCARDED, always re-decided by the fallback heuristic below
       instead of trusting the AI's own correct classification.
    2. The fallback heuristic scanned title+description COMBINED for any
       quote-mark character at all — and combat-sports/motorsport nickname
       convention ("Jon \"Bones\" Jones", "Anderson \"The Spider\" Silva")
       almost always contains one in the description, even when the title
       itself has no quote in it (job 5737's title didn't). See
       _has_real_quote for the fix, and memory
       bug-design-expand-oversized-face's sibling note on this incident."""
    prompt = (
        "You are deciding which graphic template to use for a social media post: 'quote' or 'news'.\n"
        "The Title below is the ONLY text that gets printed large on the image. The Description is "
        "background context only (used elsewhere as a caption) — it is NEVER printed on the image and "
        "must NOT by itself trigger a 'quote' decision.\n\n"
        "- 'quote' template: the Title IS a direct quotation/statement actually spoken by a person — a "
        "standalone sentence or clause meant to be read as their own words.\n"
        "- 'news' template: the Title is a headline, name, matchup, or general statement — this INCLUDES "
        "a person's name that contains a NICKNAME in quotation marks, e.g. Anderson \"The Spider\" Silva, "
        "Jon \"Bones\" Jones, Conor \"The Notorious\" McGregor. A nickname-in-quotes is part of a NAME, "
        "not a spoken quote — always classify these as 'news', even if the Description also contains "
        "quoted nicknames.\n\n"
        f"Title: {title or 'N/A'}\n"
        f"Description (context only — ignore any quote marks in here): {description or 'N/A'}\n\n"
        "Respond with ONLY a JSON object containing the type. Example: {\"type\": \"quote\"} or {\"type\": \"news\"}\n"
        "Do not include markdown blocks or any other explanation."
    )

    try:
        raw = _call_router(prompt)
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            t = str(data.get("type", "")).lower().strip()
            if t == "quote":
                return "quote"
            if t == "news":
                return "news"
    except Exception as e:
        logger.warning(f"Pinterest AI classification failed, falling back to heuristic: {e}")

    # Reached only when the AI call failed or returned something unparseable
    # — a real fallback, not a second-guess of a valid AI answer. Checks the
    # TITLE only (the field that actually gets printed), for a quoted span
    # long enough to read as a real quotation, not a short nickname.
    return "quote" if _has_real_quote(title) else "news"
