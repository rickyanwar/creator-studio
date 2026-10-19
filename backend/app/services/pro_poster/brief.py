"""Poster brief generation from article text (Mode 2 Pro poster pipeline).

Decides what the poster must show and avoid from the article text only.
A wrong person or a wrong event is worse than no post (fail closed).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from app.services.news_copywriter import _generate_with_fallback
from app.services.pro_poster.types import EventContext, PosterBrief, StoryType, Subject

logger = logging.getLogger(__name__)


class BriefError(ValueError):
    """Raised when brief generation or validation fails."""


SERIES_BADGES: dict[str, str] = {
    "Formula 1": "F1 NEWS",
    "MotoGP": "MOTOGP NEWS",
    "Moto2": "MOTO2 NEWS",
    "Moto3": "MOTO3 NEWS",
    "WorldSBK": "WORLDSBK NEWS",
    "WorldSSP": "WORLDSSP NEWS",
    "WorldWCR": "WORLDWCR NEWS",
    "GT World Challenge Europe": "GT WORLD CHALLENGE",
    "WEC": "WEC NEWS",
    "IMSA": "IMSA NEWS",
    "Formula E": "FORMULA E NEWS",
    "IndyCar": "INDYCAR NEWS",
    "NASCAR": "NASCAR NEWS",
    "F2": "F2 NEWS",
    "F3": "F3 NEWS",
    "DTM": "DTM NEWS",
    "Rally / WRC": "WRC NEWS",
    "UFC": "UFC NEWS",
    "Boxing": "BOXING NEWS",
}

_SERIES_ALIASES: dict[str, str] = {
    "f1": "Formula 1",
    "formula one": "Formula 1",
    "formula 1 world championship": "Formula 1",
    "world superbike": "WorldSBK",
    "wsbk": "WorldSBK",
    "superbike world championship": "WorldSBK",
    "world supersport": "WorldSSP",
    "wssp": "WorldSSP",
    "worldwcr": "WorldWCR",
    "women's circuit racing": "WorldWCR",
    "gtwc": "GT World Challenge Europe",
    "gt world challenge": "GT World Challenge Europe",
    "gtwc europe": "GT World Challenge Europe",
    "fanatec gt world challenge": "GT World Challenge Europe",
    "world endurance": "WEC",
    "fia wec": "WEC",
    "le mans": "WEC",
    "formula e": "Formula E",
    "abb fia formula e": "Formula E",
    "indycar series": "IndyCar",
}

CANONICAL_SERIES: tuple[str, ...] = tuple(SERIES_BADGES.keys())

_VALID_STORY_TYPES = {
    "result",
    "quote",
    "transfer",
    "crash",
    "controversy",
    "preview",
    "feature",
    "breaking",
    "other",
}

_VALID_MOODS = {"serious", "happy", "tense", "dramatic", "neutral"}

BRIEF_PROMPT_TEMPLATE = """You are an expert editorial art director and motorsport/combat-sports journalist creating a poster brief for a social media news card.
A wrong person or wrong event is completely unacceptable (e.g. never place an F1 car or F1 logo on a GT3 race card; never confuse a circuit/city with a person).

INPUT ARTICLE & BRAND:
- Design Title: {design_title}
- Design Subtitle: {design_subtitle}
- Scraped Title: {scraped_title}
- Brand Niche: {brand_niche}
- Article Content (first 2000 chars):
{scraped_content}

TASK:
Analyze the article text and output a STRICT JSON object with this exact structure:
{{
  "subjects": [
    {{"name": "Full Name", "role": "hero|secondary|mentioned", "team": "Team Name or null"}}
  ],
  "series": "Exact series name from canonical list or 'Other: <name>'",
  "vehicle": "Exact vehicle type (e.g. 'GT3 car', 'Formula 1 car', 'MotoGP bike') or null for combat sports",
  "circuit": "Circuit or venue name or null",
  "country": "Country name or null",
  "session": "Session name (e.g. race, qualifying, sprint, practice, test, fight night) or null",
  "year": null,
  "night": true|false|null,
  "rain": true|false|null,
  "story_type": "result|quote|transfer|crash|controversy|preview|feature|breaking|other",
  "mood": "serious|happy|tense|dramatic|neutral",
  "accent_colors": ["#RRGGBB"],
  "visual_ideas": ["short visual idea"],
  "must_avoid": ["visual element to avoid"],
  "language": "en"
}}

REQUIREMENTS:
1. subjects: Only REAL PEOPLE named in the text. NEVER include circuits, cities, teams, or cars in subjects.
   - "hero" = the single main person the story is about.
   - "secondary" = key opponent or teammate directly involved.
   - "mentioned" = other real people named.
2. series: Must be EXACTLY ONE of the canonical series below, or 'Other: <name>' when none fits:
   Formula 1, MotoGP, Moto2, Moto3, WorldSBK, WorldSSP, WorldWCR, GT World Challenge Europe, WEC, IMSA, Formula E, IndyCar, NASCAR, F2, F3, DTM, Rally / WRC, UFC, Boxing.
   E.g. if Max Verstappen is testing/racing a GT3 car at Portimao, the series is "GT World Challenge Europe", NOT Formula 1.
3. vehicle: The exact vehicle type driven or ridden in this event. Must be null for combat sports (UFC, Boxing).
4. must_avoid: Visual elements that would contradict the event (e.g. "Formula 1 cars", "night race", "Singapore", or rival series' logos).
5. accent_colors: Team or series colours as hex codes (#RRGGBB).
6. Output ONLY strict JSON. No markdown backticks, no comments, no extra text.
"""


def resolve_badge(series: str | None, fanpage: Any = None) -> str:
    """Resolve the pill badge text for a series, falling back to brand badge or uppercase name."""
    series_clean = (series or "").strip()
    if series_clean in SERIES_BADGES:
        return SERIES_BADGES[series_clean]
    if series_clean:
        return f"{series_clean.upper()} NEWS"[:24]
    brand_badge = getattr(fanpage, "mode2_badge_text", None) if fanpage else None
    if brand_badge and str(brand_badge).strip():
        return str(brand_badge).strip()[:24]
    return ""


def _to_bool_or_none(val: Any) -> bool | None:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        v = val.strip().lower()
        if v == "true":
            return True
        if v == "false":
            return False
    return None


def _normalize_series(series_raw: str) -> str:
    """Normalize series aliases and 'Other: <name>' to canonical series or clean name."""
    s = series_raw.strip()
    if s.lower().startswith("other:"):
        s = s[6:].strip()
        if not s:
            raise BriefError("Missing series in brief output")

    s_lower = s.lower()
    if s_lower in _SERIES_ALIASES:
        return _SERIES_ALIASES[s_lower]

    if re.match(r"^ufc(?:\s+(?:fight\s+night|\d+)|$)", s_lower):
        return "UFC"

    for k in SERIES_BADGES:
        if s_lower == k.lower():
            return k

    return s


def parse_brief(raw: str, job_id: int = 0, fanpage: Any = None) -> PosterBrief:
    """Parse raw AI output into a validated PosterBrief (fails closed)."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not m:
        raise BriefError(f"No JSON object found in output: {raw[:200]!r}")
    try:
        data = json.loads(m.group(0))
    except Exception as exc:
        raise BriefError(f"Invalid JSON in AI output: {exc}") from exc

    if not isinstance(data, dict):
        raise BriefError(f"Expected JSON object, got {type(data).__name__}")

    series_raw = data.get("series")
    if not series_raw or not isinstance(series_raw, str) or not series_raw.strip():
        raise BriefError("Missing series in brief output")

    series = _normalize_series(series_raw)

    vehicle = str(data["vehicle"]).strip() if data.get("vehicle") else None
    circuit = str(data["circuit"]).strip() if data.get("circuit") else None
    country = str(data["country"]).strip() if data.get("country") else None
    session = str(data["session"]).strip() if data.get("session") else None

    year: int | None = None
    if data.get("year") is not None:
        try:
            y = int(data["year"])
            if 1950 <= y <= 2100:
                year = y
        except (ValueError, TypeError):
            year = None

    night = _to_bool_or_none(data.get("night"))
    rain = _to_bool_or_none(data.get("rain"))

    event = EventContext(
        series=series,
        vehicle=vehicle,
        circuit=circuit,
        country=country,
        session=session,
        year=year,
        night=night,
        rain=rain,
    )

    raw_subjects = data.get("subjects")
    if not isinstance(raw_subjects, list):
        raw_subjects = []

    all_teams = {
        str(s.get("team")).strip().lower()
        for s in raw_subjects
        if isinstance(s, dict) and s.get("team")
    }

    subjects: list[Subject] = []
    hero_seen = False

    for s in raw_subjects:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        if not name:
            continue
        name_lower = name.lower()

        # Drop subject if name equals circuit, country, team, or is a single word not capitalised
        if circuit and name_lower == circuit.lower():
            continue
        if country and name_lower == country.lower():
            continue
        subj_team = str(s["team"]).strip() if s.get("team") else None
        if subj_team and name_lower == subj_team.lower():
            continue
        if name_lower in all_teams:
            continue
        words = name.split()
        if len(words) == 1 and not words[0][0].isupper():
            continue

        raw_role = str(s.get("role") or "").strip().lower()
        if raw_role == "hero":
            if not hero_seen:
                role: Literal["hero", "secondary", "mentioned"] = "hero"
                hero_seen = True
            else:
                role = "secondary"
        elif raw_role in ("secondary", "mentioned"):
            role = raw_role  # type: ignore[assignment]
        else:
            role = "hero" if not hero_seen else "secondary"
            if role == "hero":
                hero_seen = True

        subjects.append(Subject(name=name, role=role, team=subj_team))

    raw_story_type = str(data.get("story_type") or "").strip().lower()
    story_type: StoryType = (
        raw_story_type if raw_story_type in _VALID_STORY_TYPES else "other"  # type: ignore[assignment]
    )

    raw_mood = str(data.get("mood") or "").strip().lower()
    mood: Literal["serious", "happy", "tense", "dramatic", "neutral"] = (
        raw_mood if raw_mood in _VALID_MOODS else "neutral"  # type: ignore[assignment]
    )

    raw_colors = data.get("accent_colors") or []
    accent_colors: list[str] = []
    if isinstance(raw_colors, list):
        for c in raw_colors:
            if isinstance(c, str):
                c_clean = c.strip().upper()
                if re.match(r"^#[0-9A-F]{6}$", c_clean):
                    accent_colors.append(c_clean)

    raw_ideas = data.get("visual_ideas") or []
    visual_ideas = tuple(
        str(x).strip() for x in raw_ideas if isinstance(x, (str, int, float)) and str(x).strip()
    ) if isinstance(raw_ideas, list) else ()

    raw_avoid = data.get("must_avoid") or []
    must_avoid = tuple(
        str(x).strip() for x in raw_avoid if isinstance(x, (str, int, float)) and str(x).strip()
    ) if isinstance(raw_avoid, list) else ()

    language = str(data.get("language") or "en").strip() or "en"
    badge_label = resolve_badge(series, fanpage)

    return PosterBrief(
        job_id=job_id,
        subjects=tuple(subjects),
        event=event,
        story_type=story_type,
        mood=mood,
        badge_label=badge_label,
        accent_colors=tuple(accent_colors),
        visual_ideas=visual_ideas,
        must_avoid=must_avoid,
        language=language,
    )


def build_brief(job: Any, article: Any, fanpage: Any) -> PosterBrief:
    """Build a PosterBrief from the article text via text-AI fallback chain."""
    design_title = (getattr(job, "design_title", None) or "").strip()
    design_subtitle = (getattr(job, "design_subtitle", None) or "").strip()
    scraped_title = (getattr(article, "scraped_title", None) or "").strip()
    scraped_content = (getattr(article, "scraped_content", None) or "").strip()[:2000]

    niches = getattr(fanpage, "mode2_gallery_niches", None)
    if niches:
        brand_niche = ", ".join(niches) if isinstance(niches, (list, tuple)) else str(niches)
    else:
        brand_niche = getattr(fanpage, "name", "") or ""

    prompt = BRIEF_PROMPT_TEMPLATE.format(
        design_title=design_title,
        design_subtitle=design_subtitle,
        scraped_title=scraped_title,
        brand_niche=brand_niche,
        scraped_content=scraped_content,
    )

    fanpage_id = getattr(fanpage, "id", None)
    article_id = getattr(article, "id", None)
    job_id = getattr(job, "id", 0)

    def _parse_fn(raw: str) -> PosterBrief:
        return parse_brief(raw, job_id=job_id, fanpage=fanpage)

    try:
        brief, _provider = _generate_with_fallback(
            prompt,
            _parse_fn,
            context="pro_poster_brief",
            fanpage_id=fanpage_id,
            article_id=article_id,
        )
        return brief
    except BriefError:
        raise
    except Exception as exc:
        raise BriefError(f"Brief generation failed: {exc}") from exc


def brief_to_dict(brief: PosterBrief) -> dict[str, Any]:
    """Serialize PosterBrief to a dict suitable for publish_jobs.design_analysis_json."""
    return {
        "job_id": brief.job_id,
        "subjects": [
            {"name": s.name, "role": s.role, "team": s.team}
            for s in brief.subjects
        ],
        "event": {
            "series": brief.event.series,
            "vehicle": brief.event.vehicle,
            "circuit": brief.event.circuit,
            "country": brief.event.country,
            "session": brief.event.session,
            "year": brief.event.year,
            "night": brief.event.night,
            "rain": brief.event.rain,
        },
        "story_type": brief.story_type,
        "mood": brief.mood,
        "badge_label": brief.badge_label,
        "accent_colors": list(brief.accent_colors),
        "visual_ideas": list(brief.visual_ideas),
        "must_avoid": list(brief.must_avoid),
        "language": brief.language,
    }


def brief_from_dict(d: dict[str, Any], job_id: int | None = None) -> PosterBrief:
    """Deserialize a PosterBrief from dictionary."""
    jid = job_id if job_id is not None else int(d.get("job_id", 0))
    subjects = tuple(
        Subject(
            name=str(s["name"]),
            role=s.get("role", "hero"),
            team=s.get("team"),
        )
        for s in d.get("subjects", [])
        if isinstance(s, dict) and s.get("name")
    )
    ev_d = d.get("event") or {}
    event = EventContext(
        series=str(ev_d.get("series") or ""),
        vehicle=ev_d.get("vehicle"),
        circuit=ev_d.get("circuit"),
        country=ev_d.get("country"),
        session=ev_d.get("session"),
        year=ev_d.get("year"),
        night=ev_d.get("night"),
        rain=ev_d.get("rain"),
    )
    return PosterBrief(
        job_id=jid,
        subjects=subjects,
        event=event,
        story_type=d.get("story_type", "other"),
        mood=d.get("mood", "neutral"),
        badge_label=str(d.get("badge_label") or ""),
        accent_colors=tuple(d.get("accent_colors") or ()),
        visual_ideas=tuple(d.get("visual_ideas") or ()),
        must_avoid=tuple(d.get("must_avoid") or ()),
        language=str(d.get("language") or "en"),
    )
