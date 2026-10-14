"""LLM-backed variable filling for approved video ideas."""
from functools import lru_cache
import json
import random
import re

from app.models.ai_video import VideoIdea
from app.services import news_copywriter
from app.services.fanpage_caption import caption_settings
from app.services.fanpage_language import fanpage_language, language_name
from app.services.video_templates import (
    _TEMPLATE_DIR,
    get_narration_budget,
    get_template,
    liveries,
    narrator_presets,
    render_clips,
    required_variables,
    resolve_narrator,
    sanitize_channel_name,
    vehicle_presets,
)


@lru_cache(maxsize=1)
def _mechanism_refs() -> dict:
    path = _TEMPLATE_DIR / "_mechanisms.json"
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def matching_mechanisms(text: str) -> list[dict]:
    t = (text or "").lower()
    refs = _mechanism_refs()
    matches = []
    for entry in refs.values():
        if any(kw.lower() in t for kw in entry.get("keywords", [])):
            matches.append(entry)
    return matches


def build_fill_prompt(t, idea, fanpage, clips) -> str:
    cs = caption_settings(fanpage)
    lang = cs.language
    lang_display = language_name(lang)
    example = t.raw.get("example", {}).get("variables", {})
    budgets = {f"narration_{i + 1}": get_narration_budget(c, lang) for i, c in enumerate(t.raw["clip_templates"][:clips])}
    page_niches = getattr(fanpage, "mode2_gallery_niches", None) or []
    niche_text = " ".join(str(value) for value in page_niches) + " " + getattr(fanpage, "name", "")
    niche = "motogp" if "motogp" in niche_text.lower() else "f1"
    available_presets = vehicle_presets(t)
    if niche == "motogp":
        presets = [p for p in available_presets if "motogp" in p] or available_presets
    else:
        # F1/car niche: prefer classic_gp or f1
        presets = [p for p in available_presets if "motogp" not in p] or available_presets
    title_max = getattr(fanpage, "ai_video_title_max_chars", None) or 100
    desc_max = getattr(fanpage, "ai_video_description_max_chars", None) or 600
    matches = matching_mechanisms((idea.topic or "") + " " + (idea.angle or ""))
    ref_line = f"Reference for this topic (follow it exactly): {json.dumps(matches)}\n" if matches else ""
    prompt = f"""Fill this video template as strict JSON with keys variables, title, caption.
Template rules: {json.dumps(t.raw.get('rules', t.raw.get('description', '')))}
Format example variables: {json.dumps(example)}
Required variables: {json.dumps(required_variables(t, clips))}; narration word budgets: {json.dumps(budgets)}
Vehicle preset choices for this page: {json.dumps(presets)}. Narration must be in {lang_display}.
Title and caption must be in {lang}; title <= {title_max} chars, caption <= {desc_max} chars and <= {cs.hashtag_count} hashtags.
The FIRST sentence is the hook a reader sees before 'See more' on Facebook mobile: at most 125 characters, concrete and specific (who + what), no generic opener.
Do not output livery or channel_name; the system sets them.
Mechanical accuracy requirements:
1. Physical placement: every visible component must be placed where it really is on the real vehicle (e.g. in F1, MGU-K is on the engine power unit driving the crankshaft, NOT inside wheel hubs; brake-by-wire hydraulic block is in the chassis/sidepod, brake calipers/discs are on wheel uprights).
2. Mechanism action: mechanism_action must state exactly what moves, around which pivot/axis, in which direction, and what stays fixed (e.g. DRS: only the upper flap pivots up around its forward hinge; the main plane and vertical endplates stay completely rigid, horizontal, and level).
3. Parts simplicity: mechanism_details and parts_list must contain only 3 to 4 major, large, clearly distinct components that can visibly separate in an exploded view (e.g. for rear wing: "fixed lower main plane, movable upper flap, central actuator cylinder, side endplates"; for brake-by-wire: "cockpit brake master cylinder, chassis electronic control unit, rear carbon brake disc, rear brake caliper"). Do NOT list tiny internal linkages or screws that an AI video model cannot separate cleanly.
4. Grounded anatomy: prefer well-known, simple, mechanically correct anatomy over invented detail.
5. moving_part names the ONE part that moves; fixed_parts lists the nearby parts that must stay completely still. Both are written into the video prompt.
{ref_line}Use research_notes as the ONLY source of specific facts and numbers. If absent, explain the mechanism without specific numbers or season claims.
Never name real teams, sponsors, or brands. Research notes: {idea.research_notes or '(none)'}
    Topic: {idea.topic}; angle: {idea.angle or '(none)'}
    """
    if idea.variables:
        full_presets = set(required_variables(t, clips))
        if full_presets.issubset(set(idea.variables.keys())):
            prompt += "Use these variables exactly: " + json.dumps(idea.variables)
    return prompt


def _parse(raw: str, title_max: int = 100, desc_max: int = 600, hashtag_max: int = 5) -> dict:
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
    if not isinstance(title, str) or not title.strip() or len(title) > title_max:
        raise ValueError(f"title must be a non-empty string of at most {title_max} characters")
    if not isinstance(caption, str) or len(caption) > desc_max or sum(1 for word in caption.split() if word.startswith("#")) > hashtag_max:
        raise ValueError(f"caption must be at most {desc_max} characters and {hashtag_max} hashtags")
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


def apply_system_look(db, fanpage, template, variables):
    """The studio's own slots: narrator voice, livery and channel lettering."""
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


LOCK_SLOTS = ("moving_part", "fixed_parts")
# Muse ideas written before the motion lock existed: a safe, generic lock instead of a guess.
GENERIC_LOCK = {
    "moving_part": "mechanism described above",
    "fixed_parts": "the car body, the wheels and every part that is not named as moving",
}


def research_ready(idea, template, clips) -> bool:
    """A research-desk idea that already carries every slot plus its own title and caption."""
    return bool(
        idea.source == "muse" and idea.title and idea.caption and isinstance(idea.variables, dict)
        and (set(required_variables(template, clips)) - set(LOCK_SLOTS)) <= set(idea.variables)
    )


def fill_from_research(db, idea, fanpage, clips):
    """Use the slots, title and caption the research desk wrote, with the studio's look; no LLM call."""
    template = get_template(idea.template_key)
    raw = dict(idea.variables or {})
    base = dict(raw)
    for slot in LOCK_SLOTS:
        val = raw.get(slot)
        if not val or not str(val).strip():
            base[slot] = GENERIC_LOCK[slot]
    variables = apply_system_look(db, fanpage, template, base)
    rendered = render_clips(template, variables, clips, lang_code=fanpage_language(fanpage))
    return variables, idea.title, idea.caption, rendered, "research_desk"


def fill_idea(db, idea, fanpage, clips):
    template = get_template(idea.template_key)
    prompt = build_fill_prompt(template, idea, fanpage, clips)

    def apply_look(variables):
        return apply_system_look(db, fanpage, template, variables)

    lang = fanpage_language(fanpage)
    title_max = getattr(fanpage, "ai_video_title_max_chars", None) or 100
    desc_max = getattr(fanpage, "ai_video_description_max_chars", None) or 600
    cs = caption_settings(fanpage)
    def parse(raw):
        result = _parse(raw, title_max=title_max, desc_max=desc_max, hashtag_max=cs.hashtag_count)
        merged_vars = dict(result["variables"])
        if isinstance(idea.variables, dict):
            merged_vars.update(idea.variables)
        variables = apply_look(merged_vars)
        render_clips(template, variables, clips, lang_code=lang)
        result["variables"] = variables
        return result

    result, provider = news_copywriter._generate_with_fallback(
        prompt, parse, context="ai_video_fill", fanpage_id=fanpage.id
    )
    merged_vars = dict(result["variables"])
    if isinstance(idea.variables, dict):
        merged_vars.update(idea.variables)
    result["variables"] = apply_look(merged_vars)
    rendered = render_clips(template, result["variables"], clips, lang_code=lang)
    return result["variables"], result["title"], result["caption"], rendered, provider
