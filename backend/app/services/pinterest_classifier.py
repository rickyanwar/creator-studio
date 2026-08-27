import json
import logging
import re
from typing import Literal
from app.services.ai_caption import _call_router

logger = logging.getLogger(__name__)

def classify_pinterest_content(title: str, description: str) -> Literal["quote", "news"]:
    prompt = (
        "You are deciding which graphic template to use for a social media post: 'quote' or 'news'.\n"
        "IMPORTANT: Your decision MUST be based ONLY on the text that will be printed/rendered on the image.\n"
        "- 'quote' template: Use this if the text printed on the image is a direct quote/statement from a person.\n"
        "- 'news' template: Use this if the text printed on the image is a general headline, statement, or news.\n\n"
        f"Text to be printed on the image (Title): {title or 'N/A'}\n"
        f"Additional context (Description): {description or 'N/A'}\n\n"
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
