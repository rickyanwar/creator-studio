import json
import logging
import re
from typing import Literal
from app.services.ai_caption import _call_router

logger = logging.getLogger(__name__)

def classify_pinterest_content(title: str, description: str) -> Literal["quote", "news"]:
    prompt = (
        "You are classifying a Pinterest pin's text into 'quote' or 'news' for social media.\n"
        "- 'quote': The text is a spoken quote from a specific person, or heavily features a direct quotation.\n"
        "- 'news': The text is an informational headline, article summary, meme, or general statement.\n\n"
        f"Title: {title or 'N/A'}\n"
        f"Description: {description or 'N/A'}\n\n"
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
    except Exception as e:
        logger.warning(f"Pinterest AI classification failed, falling back to heuristic: {e}")
    
    # Fallback heuristic
    combined = (title or "") + " " + (description or "")
    has_quote = any(q in combined for q in ['"', '“', '”'])
    return "quote" if has_quote else "news"
