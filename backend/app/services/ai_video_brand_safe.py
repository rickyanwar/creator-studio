"""Remove named brands from refused motorcycle prompts for one retry."""

import json
import logging
import re

from app.services import news_copywriter, video_templates

logger = logging.getLogger(__name__)

BRAND_SAFE_TEMPLATES = frozenset({"motorcycle_commercial"})
BRAND_SAFE_PROMPT = '''You edit Google Flow video prompts. Google refused to generate this video, most likely because the prompts name
real brands. Rewrite each prompt so that, outside double quotes, it names NO real brand, company, team, sponsor or
maker: replace each such name with a plain description of how it looks and where it sits, for example
"white REPSOL lettering on the orange side fairing" becomes "white sponsor lettering on the orange side fairing" and
"a red HONDA wing logo on the tank" becomes "a red wing emblem on the tank". Keep everything else exactly as written:
every other word, every colour, every race number and its digits, every camera move, timing mark and restriction,
and every text inside double quotes character for character. Do not add or remove anything else.
Return ONLY JSON, no markdown: {"prompts": ["<clip 1 prompt>", "<clip 2 prompt>", ...]} with the same number of
prompts in the same order.

PROMPTS:
'''


def rewrite_brand_safe(clips: list[dict]) -> list[dict] | None:
    def parse(raw):
        data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip()))
        prompts = data.get("prompts") if isinstance(data, dict) else None
        if not isinstance(prompts, list) or len(prompts) != len(clips):
            raise ValueError("Brand-safe rewrite must preserve the prompt count")
        for clip, rewritten in zip(clips, prompts):
            original = clip["prompt"]
            if not isinstance(rewritten, str):
                raise ValueError("Brand-safe prompts must be strings")
            if re.findall(r'"[^"]*"', rewritten) != re.findall(r'"[^"]*"', original):
                raise ValueError("Brand-safe rewrite changed quoted text")
            if not set(re.findall(r'\d+', original)).issubset(re.findall(r'\d+', rewritten)):
                raise ValueError("Brand-safe rewrite lost digits")
            if not 0.6 * len(original) <= len(rewritten) <= video_templates.MAX_PROMPT_CHARS:
                raise ValueError("Brand-safe rewrite has invalid prompt length")
        return [{**clip, "prompt": prompt, "brand_safe": True} for clip, prompt in zip(clips, prompts)]

    try:
        prompt = BRAND_SAFE_PROMPT + json.dumps([c["prompt"] for c in clips], ensure_ascii=False, indent=1)
        rewritten, _provider = news_copywriter._generate_with_fallback(
            prompt, parse, context="ai_video_brand_safe",
        )
        return rewritten
    except Exception as exc:
        logger.warning("Brand-safe prompt rewrite failed: %s", exc)
        return None
