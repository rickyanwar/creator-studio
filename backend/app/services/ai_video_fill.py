"""LLM-backed variable filling for approved video ideas."""
import json
import re

from app.services import news_copywriter
from app.services.video_templates import get_template, render_clips, required_variables, vehicle_presets


def build_fill_prompt(t, idea, fanpage, clips) -> str:
    example = t.raw.get("example", {}).get("variables", {})
    budgets = {f"narration_{i + 1}": c["narration_words"]["en"] for i, c in enumerate(t.raw["clip_templates"][:clips])}
    page_niches = getattr(fanpage, "mode2_gallery_niches", None) or []
    niche_text = " ".join(str(value) for value in page_niches) + " " + getattr(fanpage, "name", "")
    niche = "motogp" if "motogp" in niche_text.lower() else "f1"
    presets = ["motogp"] if niche == "motogp" else ["f1_red"]
    return f"""Fill this video template as strict JSON with keys variables, title, caption.
Template rules: {json.dumps(t.raw.get('rules', t.raw.get('description', '')))}
Format example variables: {json.dumps(example)}
Required variables: {json.dumps(required_variables(t, clips))}; narration word budgets: {json.dumps(budgets)}
Vehicle preset choices for this page: {json.dumps(presets)}. Narration must be English.
Title and caption must be in {getattr(fanpage, 'caption_language', 'en')}; title <= 100 chars, caption <= 600 chars and <= 5 hashtags.
Use research_notes as the ONLY source of specific facts and numbers. If absent, explain the mechanism without specific numbers or season claims.
Never name real teams, sponsors, or brands. Research notes: {idea.research_notes or '(none)'}
Topic: {idea.topic}; angle: {idea.angle or '(none)'}
""" + ("Use these variables exactly: " + json.dumps(idea.variables) if idea.variables else "")


def _parse(raw: str) -> dict:
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except (TypeError, json.JSONDecodeError):
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise ValueError("invalid JSON: no JSON object found")
        try:
            value = json.loads(match.group(0))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("variables"), dict):
        raise ValueError("output must contain variables object")
    title, caption = value.get("title"), value.get("caption")
    if not isinstance(title, str) or not title.strip() or len(title) > 100:
        raise ValueError("title must be a non-empty string of at most 100 characters")
    if not isinstance(caption, str) or len(caption) > 600 or sum(1 for word in caption.split() if word.startswith("#")) > 5:
        raise ValueError("caption must be at most 600 characters and 5 hashtags")
    return value


def fill_idea(db, idea, fanpage, clips):
    template = get_template(idea.template_key)
    prompt = build_fill_prompt(template, idea, fanpage, clips)

    def parse(raw):
        result = _parse(raw)
        variables = idea.variables if idea.variables is not None else result["variables"]
        render_clips(template, variables, clips)
        result["variables"] = variables
        return result

    result, provider = news_copywriter._generate_with_fallback(
        prompt, parse, context="ai_video_fill", fanpage_id=fanpage.id
    )
    if idea.variables is not None:
        result["variables"] = idea.variables
        render_clips(template, result["variables"], clips)
    rendered = render_clips(template, result["variables"], clips)
    return result["variables"], result["title"], result["caption"], rendered, provider
