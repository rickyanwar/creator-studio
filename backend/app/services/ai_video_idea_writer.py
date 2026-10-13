"""In-app writer that tops up the Mode 8 video idea queue via 9Router."""
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from app.models.ai_video import VideoIdea
from app.services import news_copywriter
from app.services.ai_video_ideas import ConflictError, LimitExceededError, create_idea
from app.services.video_templates import allowed_video_templates, clip_choices, get_template, list_templates

logger = logging.getLogger(__name__)

IDEA_SOURCE = "ai"
MAX_IDEAS_PER_RUN = 4
RECENT_TOPIC_DAYS = 30
RECENT_TOPIC_LIMIT = 60


def fanpage_niche(fanpage) -> str:
    page_niches = getattr(fanpage, "mode2_gallery_niches", None) or []
    niche_text = " ".join(str(value) for value in page_niches) + " " + getattr(fanpage, "name", "")
    return "motogp" if "motogp" in niche_text.lower() else "f1"


def writer_templates(fanpage) -> list:
    keys = allowed_video_templates(fanpage)
    if keys:
        templates = []
        for k in keys:
            try:
                templates.append(get_template(k))
            except ValueError:
                continue
        return templates
    niche = fanpage_niche(fanpage)
    return [t for t in list_templates() if niche in t.niches]


def _topic_ideas(t, niche) -> list[str]:
    raw_ideas = t.raw.get("topic_ideas")
    if isinstance(raw_ideas, dict):
        return raw_ideas.get(niche) or []
    if isinstance(raw_ideas, list):
        return raw_ideas
    return []


def recent_topics(db) -> list[str]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=RECENT_TOPIC_DAYS)
    ideas = (
        db.query(VideoIdea)
        .filter(VideoIdea.created_at >= cutoff, VideoIdea.status != "rejected")
        .order_by(VideoIdea.created_at.desc())
        .limit(RECENT_TOPIC_LIMIT)
        .all()
    )
    return [idea.topic for idea in ideas]


def build_idea_prompt(fanpage, templates, avoid_topics, count) -> str:
    niche = fanpage_niche(fanpage)
    templates_data = [
        {
            "template_key": t.key,
            "name": t.name,
            "allowed_clips": clip_choices(fanpage, t),
            "clip_seconds": t.clip_seconds,
            "rules": t.raw.get("rules", []),
            "example_topics": _topic_ideas(t, niche),
        }
        for t in templates
    ]
    return f"""Write exactly {count} short-video ideas for the Facebook page {getattr(fanpage, 'name', '')}, niche {niche}.

Available templates:
{json.dumps(templates_data, indent=2)}

Guidelines:
- Pick the template that fits each idea best; spread ideas across templates when more than one template is listed.
- Pick clips for each idea from that template's allowed_clips: the smallest number for a simple single-idea topic, the largest when the topic needs more explanation (more parts or steps).
- Topics must be evergreen, technical and visual (things a 3D x-ray / exploded-view or telemetry animation can show); never name real teams, sponsors or brands; do not invent numbers, dates or race results (there is no research step, so keep claims general).
- Never repeat or paraphrase any of these topics: {json.dumps(avoid_topics)}.
- Write topic and angle in English (the page language is applied later, when the script is written).
- Score each idea honestly 0-100 for how well it fits the template and this page's audience; rationale = one or two sentences why.
- Return ONLY strict JSON, no markdown: {{"ideas": [{{"template_key": "...", "topic": "<= 120 chars", "angle": "<= 400 chars", "clips": <int>, "score": 0-100, "rationale": "<= 400 chars"}}]}}
"""


def parse_ideas(raw: str, allowed_clips: dict[str, list[int]], count: int) -> list[dict]:
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        data = json.loads(cleaned)
    except (TypeError, json.JSONDecodeError):
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise ValueError("invalid JSON")
        try:
            data = json.loads(match.group(0))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON") from exc

    if not isinstance(data, dict) or not isinstance(data.get("ideas"), list):
        raise ValueError("missing ideas list")

    kept = []
    for item in data["ideas"]:
        if not isinstance(item, dict):
            continue
        t_key = item.get("template_key")
        if t_key not in allowed_clips:
            continue
        topic = item.get("topic")
        if not isinstance(topic, str):
            continue
        topic = topic.strip()
        if not (3 <= len(topic) <= 200):
            continue
        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, int) or not (0 <= score <= 100):
            continue
        rationale = item.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            continue
        rationale = rationale[:4000]
        angle = item.get("angle")
        if angle is not None:
            if not isinstance(angle, str):
                continue
            angle = angle[:2000]

        entry = {
            "template_key": t_key,
            "topic": topic,
            "angle": angle,
            "score": score,
            "rationale": rationale,
        }
        clips = item.get("clips")
        if not isinstance(clips, bool) and isinstance(clips, int) and clips in allowed_clips[t_key]:
            entry["clips"] = clips

        kept.append(entry)
        if len(kept) == count:
            break

    if not kept:
        raise ValueError("no valid ideas")

    return kept


def write_ideas(db, fanpage, count: int) -> int:
    count = min(count, MAX_IDEAS_PER_RUN)
    if count <= 0:
        return 0
    templates = writer_templates(fanpage)
    if not templates:
        logger.info("No writer templates found for fanpage %s", getattr(fanpage, "id", None))
        return 0
    avoid = recent_topics(db)
    prompt = build_idea_prompt(fanpage, templates, avoid, count)
    allowed = {t.key: clip_choices(fanpage, t) for t in templates}
    ideas, _provider = news_copywriter._generate_with_fallback(
        prompt,
        lambda raw: parse_ideas(raw, allowed, count),
        context="ai_video_idea_writer",
        fanpage_id=fanpage.id,
    )
    created = 0
    for idea in ideas:
        try:
            create_idea(db, fanpage.id, idea, source=IDEA_SOURCE)
            created += 1
        except (ConflictError, ValueError) as exc:
            db.rollback()
            logger.info("Mode 8 idea writer skipped topic '%s': %s", idea.get("topic"), exc)
            continue
        except LimitExceededError as exc:
            db.rollback()
            logger.info("Mode 8 idea writer hit limit for fanpage %s: %s", fanpage.id, exc)
            break
    return created
