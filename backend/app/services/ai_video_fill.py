"""LLM-backed variable filling for approved video ideas."""
import json
import random
import re

from app.models.ai_video import VideoIdea
from app.services import news_copywriter
from app.services.video_templates import get_template, liveries, narrator_presets, render_clips, required_variables, resolve_narrator, sanitize_channel_name, vehicle_presets


def build_fill_prompt(t, idea, fanpage, clips) -> str:
    example = t.raw.get("example", {}).get("variables", {})
    budgets = {f"narration_{i + 1}": c["narration_words"]["en"] for i, c in enumerate(t.raw["clip_templates"][:clips])}
    page_niches = getattr(fanpage, "mode2_gallery_niches", None) or []
    niche_text = " ".join(str(value) for value in page_niches) + " " + getattr(fanpage, "name", "")
    niche = "motogp" if "motogp" in niche_text.lower() else "f1"
    available_presets = vehicle_presets(t)
    if niche == "motogp":
        presets = [p for p in available_presets if "motogp" in p] or available_presets
    else:
        # F1/car niche: prefer classic_gp or f1
        presets = [p for p in available_presets if "motogp" not in p] or available_presets
    return f"""Fill this video template as strict JSON with keys variables, title, caption.
Template rules: {json.dumps(t.raw.get('rules', t.raw.get('description', '')))}
Format example variables: {json.dumps(example)}
Required variables: {json.dumps(required_variables(t, clips))}; narration word budgets: {json.dumps(budgets)}
Vehicle preset choices for this page: {json.dumps(presets)}. Narration must be English.
Title and caption must be in {getattr(fanpage, 'caption_language', 'en')}; title <= 100 chars, caption <= 600 chars and <= 5 hashtags.
Do not output livery or channel_name; the system sets them.
Mechanical accuracy requirements:
1. Physical placement: every visible component must be placed where it really is on the real vehicle (e.g. in F1, MGU-K is on the engine power unit driving the crankshaft, NOT inside wheel hubs; brake-by-wire hydraulic block is in the chassis/sidepod, brake calipers/discs are on wheel uprights).
2. Mechanism action: mechanism_action must state exactly what moves, around which pivot/axis, in which direction, and what stays fixed (e.g. DRS: only the upper flap pivots up around its forward hinge; the main plane and vertical endplates stay completely rigid, horizontal, and level).
3. Parts simplicity: mechanism_details and parts_list must contain only 3 to 4 major, large, clearly distinct components that can visibly separate in an exploded view (e.g. for rear wing: "fixed lower main plane, movable upper flap, central actuator cylinder, side endplates"; for brake-by-wire: "cockpit brake master cylinder, chassis electronic control unit, rear carbon brake disc, rear brake caliper"). Do NOT list tiny internal linkages or screws that an AI video model cannot separate cleanly.
4. Grounded anatomy: prefer well-known, simple, mechanically correct anatomy over invented detail.
Use research_notes as the ONLY source of specific facts and numbers. If absent, explain the mechanism without specific numbers or season claims.
Never name real teams, sponsors, or brands. Research notes: {idea.research_notes or '(none)'}
    Topic: {idea.topic}; angle: {idea.angle or '(none)'}
    """
    if idea.variables:
        full_presets = set(required_variables(t, clips))
        if full_presets.issubset(set(idea.variables.keys())):
            prompt += "Use these variables exactly: " + json.dumps(idea.variables)
    return prompt


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


def pick_livery(db, fanpage_id, t):
    recent = (
        db.query(VideoIdea)
        .filter(VideoIdea.fanpage_id == fanpage_id, VideoIdea.variables.isnot(None))
        .order_by(VideoIdea.id.desc())
        .limit(3)
        .all()
    )
    used = {
        idea.variables.get("livery")
        for idea in recent
        if isinstance(idea.variables, dict) and idea.variables.get("livery")
    }
    available = [value for value in liveries(t) if value not in used]
    return random.choice(available or liveries(t))


def fill_idea(db, idea, fanpage, clips):
    template = get_template(idea.template_key)
    prompt = build_fill_prompt(template, idea, fanpage, clips)

    def apply_system_look(variables):
        variables = dict(variables)
        if variables.get("narrator_preset") not in narrator_presets(template):
            variables["narrator_preset"] = resolve_narrator(fanpage, template)
        preset = variables.get("vehicle_preset")
        if preset in {"f1", "classic_gp", "motogp_livery"}:
            if variables.get("livery") not in liveries(template):
                variables["livery"] = pick_livery(db, fanpage.id, template)
            channel_name = sanitize_channel_name(fanpage.name)
            if channel_name is None:
                variables.pop("channel_name", None)
            else:
                variables["channel_name"] = channel_name
        else:
            variables.pop("livery", None)
            variables.pop("channel_name", None)
        return variables

    def parse(raw):
        result = _parse(raw)
        merged_vars = dict(result["variables"])
        if isinstance(idea.variables, dict):
            merged_vars.update(idea.variables)
        variables = apply_system_look(merged_vars)
        render_clips(template, variables, clips)
        result["variables"] = variables
        return result

    result, provider = news_copywriter._generate_with_fallback(
        prompt, parse, context="ai_video_fill", fanpage_id=fanpage.id
    )
    merged_vars = dict(result["variables"])
    if isinstance(idea.variables, dict):
        merged_vars.update(idea.variables)
    result["variables"] = apply_system_look(merged_vars)
    rendered = render_clips(template, result["variables"], clips)
    return result["variables"], result["title"], result["caption"], rendered, provider
