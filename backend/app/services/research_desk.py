"""Research desk: the API contract Muse works against for Mode 8 research (docs/research-desk-api.md).

Muse researches a topic once (a "pack") and writes one variant per channel. A channel is a
fanpage, but Muse must never learn that: everything it sees is a channel id, a niche and an
audience, never page names, the platform or publishing data. Tests assert this.

Only the pilot mode exists so far: accepted variants are stored as ideas with status "pilot",
which the planner never picks up, until the owner approves them on the AI Video page.
"""
import difflib
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ai_video import VideoIdea
from app.models.research import ResearchCall, ResearchFeedback
from app.models.settings import Settings
from app.models.target_fanpages import TargetFanpage
from app.services.ai_video_idea_writer import fanpage_niche
from app.services.ai_video_ideas import _normalized_topic
from app.services.fanpage_caption import caption_settings
from app.services.fanpage_language import fanpage_language
from app.services.real_names import find_real_names
from app.services.video_templates import (
    allowed_video_templates,
    clip_choices,
    get_narration_budget,
    get_template,
    list_templates,
    liveries,
    render_clips,
    required_variables,
    vehicle_presets,
)

SCHEMA_VERSION = 1
DOCS_PATH = Path(__file__).resolve().parent.parent / "assets" / "research-desk-api.md"
NICHES = ("f1", "motogp")
MAX_CHANNELS_PER_PACK = 10
MAX_PACKS_PER_REQUEST = 3
PILOT_CHANNELS_PER_NICHE = 10
MIN_SCORE = 70
HOOK_MAX_CHARS = 125
TOPIC_DAYS = 30
SIMILARITY_LIMIT = 0.85
EVIDENCE_LIMIT = 16384
CALL_LOG_DAYS = 30
STUDIO_SLOTS = {"livery", "channel_name", "narrator_preset"}
# Template notes written for the old manual workflow, not for a research desk.
DROP_RULE_PREFIXES = ("Ask the owner before generating",)
SOURCE_ID = re.compile(r"^S[0-9]+$")
CITE = re.compile(r"\[(S[0-9]+)\]")
PLATFORMS = ("instagram", "facebook", "youtube", "tiktok", "other")
REFERENCE_KINDS = ("video_style", "topic_signal")
STATUS_FOR_MUSE = {
    "pilot": "in_review", "proposed": "in_review", "approved": "approved", "queued": "in_production",
    "done": "produced", "failed": "failed", "rejected": "rejected",
}

DEFAULT_REFERENCES = {
    "f1": {
        "video_style": [
            {"url": "https://www.instagram.com/formula.addict/", "note": "Reels with the visual style the explainer format is modelled on"},
        ],
        "topic_signals": [
            {"url": "https://www.instagram.com/lapfuel/", "note": "Image posts: which topics draw unusual engagement"},
            {"url": "https://www.instagram.com/sportskeedapitstop/", "note": "Image posts: which topics draw unusual engagement"},
            {"url": "https://www.instagram.com/formula.planet/", "note": "Image posts: which topics draw unusual engagement"},
            {"url": "https://www.instagram.com/f1feverr/", "note": "Image posts: which topics draw unusual engagement"},
        ],
    },
    "motogp": {"video_style": [], "topic_signals": []},
}

STUDIO = ("Short vertical (9:16) motorsport explainer videos, 20-30 seconds, photorealistic 3D CGI, "
          "voice-over spoken inside the clips.")
QUEUE_POLICY = ("The studio caps its queue: ideas_needed and packs_needed already subtract ideas still waiting. "
                "When nothing is needed the brief says action 'idle': stop at once and do no research.")
IDEA_SOURCES = [
    "New or changed motorsport regulations: FIA (Formula 1 and other FIA championships) and FIM / MotoGP technical "
    "and sporting regulations, technical directives, clarifications and bans for the current and next season - "
    "what the new rules change on the car or bike, and why.",
    "Recent technical news and controversies across motorsport: protests, upgrades, legality disputes, rule interpretations.",
    "Topics drawing unusually high engagement on the reference accounts (compared with each account's usual level).",
    "Evergreen engineering and history topics with strong curiosity, only when nothing timely fits.",
]
GLOBAL_RULES = [
    "Never name real teams, constructors, manufacturers, riders, drivers, sponsors, brands or circuits in any slot, "
    "narration, title or caption; describe them instead (colour, era, corner type).",
    "Every specific fact or number in narration, title or caption must come from research_notes.",
    "Numbers need two independent sources; note the season or year of every rule-dependent claim; leave unverified claims out.",
    "Regulations change by season: check that the technology is still current, or frame it as history or as what replaced it.",
    "Write narration, title and caption in the channel's audience language.",
    "Slot values: plain text, at most 600 characters, no curly braces; narration lines contain no double quotes.",
    "Copy the visual style of reference videos, never their footage, logos, watermarks or wording.",
]
RESEARCH_DEFAULTS = {
    "source_priority": [
        "official: FIA and FIM regulations and technical directives, formula1.com, motogp.com, team and manufacturer technical releases",
        "technical journalism: The Race, Motorsport.com, Autosport, Racecar Engineering, F1Technical, Crash.net, Paddock-GP",
        "YouTube, forums and Wikipedia only as leads; re-verify elsewhere",
    ],
    "fact_rules": ["numbers need 2 independent sources", "note the season/year", "unverified -> leave out of narration"],
}
PACK_RULES = {
    "max_channels_per_pack": MAX_CHANNELS_PER_PACK,
    "max_packs_per_request": MAX_PACKS_PER_REQUEST,
    "min_score": MIN_SCORE,
    "variants": "Variants of one pack share the facts only: each needs its own angle, hook, narration wording and "
                "title. A channel never gets two variants of the same pack.",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_settings_row(db: Session) -> Settings:
    row = db.query(Settings).filter_by(id=1).first()
    if not row:
        row = Settings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


# ── References ────────────────────────────────────────────────────────────────

def normalize_references(value) -> dict:
    """Validate an owner-edited reference list; raises ValueError with a readable message."""
    if not isinstance(value, dict):
        raise ValueError("research_references must be an object keyed by niche")
    out = {}
    for niche in NICHES:
        lists = value.get(niche) or {}
        if not isinstance(lists, dict):
            raise ValueError(f"research_references.{niche} must be an object")
        out[niche] = {}
        for kind in ("video_style", "topic_signals"):
            items = lists.get(kind) or []
            if not isinstance(items, list) or len(items) > 20:
                raise ValueError(f"research_references.{niche}.{kind} must be a list of at most 20 links")
            clean = []
            for item in items:
                if isinstance(item, str):
                    item = {"url": item}
                url = (item.get("url") or "").strip() if isinstance(item, dict) else ""
                note = (item.get("note") or "").strip() if isinstance(item, dict) else ""
                if not url.startswith("https://") or len(url) > 300:
                    raise ValueError(f"research_references.{niche}.{kind}: every link must start with https:// and be at most 300 characters")
                if len(note) > 200:
                    raise ValueError(f"research_references.{niche}.{kind}: notes are at most 200 characters")
                clean.append({"url": url, "note": note} if note else {"url": url})
            out[niche][kind] = clean
    return out


def effective_references(row: Settings) -> dict:
    value = getattr(row, "research_references", None)
    return value if isinstance(value, dict) else DEFAULT_REFERENCES


# ── Templates and channels as Muse sees them ─────────────────────────────────

def niche_presets(t, niche: str) -> list[str]:
    """The vehicle presets the studio's own fill step would allow for this niche."""
    presets = vehicle_presets(t)
    if niche == "motogp":
        return [p for p in presets if "motogp" in p] or presets
    return [p for p in presets if "motogp" not in p] or presets


def content_slots(t, clips: int) -> list[str]:
    return [s for s in required_variables(t, clips) if s not in STUDIO_SLOTS]


def _neutral_vehicle_text(text: str) -> str:
    text = text.replace("{lettering}", "").replace("{lettering_short}", "")
    return text.replace("{livery}", "[livery chosen by the studio]").replace("{livery_short}", "[livery chosen by the studio]")


def template_item(t, niches) -> dict:
    raw = t.raw
    research = dict(raw.get("research") or {})
    for key in ("output", "owner_report_before_generate", "tools"):
        research.pop(key, None)
    example = dict(raw.get("example") or {})
    if isinstance(example.get("variables"), dict):
        example["variables"] = {k: v for k, v in example["variables"].items() if k not in STUDIO_SLOTS}
    example = {"note": "Format example only: this topic is already produced and its facts may be out of date. "
                       "Do not reuse its topic or facts.", **example}
    topics = raw.get("topic_ideas") or []
    if isinstance(topics, dict):
        topics = {n: topics.get(n, []) for n in niches}
    blocks = raw["blocks"]
    presets = sorted({p for n in niches for p in niche_presets(t, n)})
    return {
        "key": t.key,
        "name": t.name,
        "niches": [n for n in t.niches if n in niches],
        "usage": raw.get("usage") or raw.get("purpose") or raw.get("description") or "",
        "inspiration": raw.get("inspiration"),
        "clip_seconds": t.clip_seconds,
        "clip_options": list(t.clip_options),
        "slots_by_clips": {str(n): content_slots(t, n) for n in t.clip_options},
        "clip_prompts": [
            {"index": i, "role": c.get("role"), "continues_previous_clip": c["mode"] != "t2v", "prompt": c["prompt"]}
            for i, c in enumerate(raw["clip_templates"], start=1)
        ],
        "studio_filled_placeholders": {
            "style": blocks.get("style"),
            "audio": blocks.get("audio"),
            "narrator / narrator_followup": "voice-over line chosen by the studio; one consistent voice per video",
            "vehicle / vehicle_short": "built from your vehicle_preset choice (see vehicle_presets)",
        },
        "vehicle_presets": {
            p: {"full": _neutral_vehicle_text(blocks["vehicle_presets"][p]),
                "short": _neutral_vehicle_text(blocks["vehicle_presets"][p + "_short"])}
            for p in presets
        },
        "rules": [r for r in raw.get("rules", []) if not r.startswith(DROP_RULE_PREFIXES)],
        "research": research,
        "keywords_that_worked": raw.get("keywords_that_worked", []),
        "starter_topics": topics,
        "example": example,
    }


def channel_templates(fanpage, niche: str) -> list:
    allowed = allowed_video_templates(fanpage)
    return [t for t in list_templates() if niche in t.niches and (not allowed or t.key in allowed)]


def copy_rules(fanpage) -> dict:
    return {
        "title_max_chars": getattr(fanpage, "ai_video_title_max_chars", None) or 100,
        "caption_max_chars": getattr(fanpage, "ai_video_description_max_chars", None) or 600,
        "hashtags_max": caption_settings(fanpage).hashtag_count,
        "hook_max_chars": HOOK_MAX_CHARS,
    }


def _narration_words(t, lang: str) -> dict:
    out = {}
    for i, clip in enumerate(t.raw["clip_templates"], start=1):
        lo, hi = map(int, get_narration_budget(clip, lang).split("-"))
        out[f"narration_{i}"] = {"target": f"{lo}-{hi}", "accepted": f"{lo - 6}-{hi}"}
    return out


def recent_topics(db: Session, fanpage_id: int) -> list[str]:
    rows = db.query(VideoIdea.topic).filter(
        VideoIdea.fanpage_id == fanpage_id,
        VideoIdea.created_at >= _now() - timedelta(days=TOPIC_DAYS),
        VideoIdea.status != "rejected",
    ).order_by(VideoIdea.created_at.desc()).limit(200).all()
    return [topic for (topic,) in rows]


def topic_channel_counts(db: Session) -> dict[str, tuple[str, set]]:
    """normalized topic -> (a topic text, channels that had it in the last 30 days)."""
    rows = db.query(VideoIdea.topic, VideoIdea.fanpage_id).filter(
        VideoIdea.created_at >= _now() - timedelta(days=TOPIC_DAYS),
        VideoIdea.status != "rejected",
    ).all()
    out: dict[str, tuple[str, set]] = {}
    for topic, fanpage_id in rows:
        key = _normalized_topic(topic)
        text, channels = out.get(key, (topic, set()))
        channels.add(fanpage_id)
        out[key] = (text, channels)
    return out


def research_channels(db: Session) -> dict[str, list]:
    """Mode 8 fanpages that take part in research, by niche (the pilot takes the first 10 per niche)."""
    by_niche: dict[str, list] = {n: [] for n in NICHES}
    fanpages = db.query(TargetFanpage).filter(
        TargetFanpage.is_active == True, TargetFanpage.ai_video_enabled == True,  # noqa: E712
    ).order_by(TargetFanpage.id).all()
    for fp in fanpages:
        niche = fanpage_niche(fp)
        if len(by_niche[niche]) < PILOT_CHANNELS_PER_NICHE and channel_templates(fp, niche):
            by_niche[niche].append(fp)
    return by_niche


def waiting_muse_ideas(db: Session, fanpage_id: int) -> int:
    return db.query(VideoIdea).filter(
        VideoIdea.fanpage_id == fanpage_id,
        VideoIdea.source == "muse",
        VideoIdea.status.in_(["pilot", "approved"]),
    ).count()


def channel_need(db: Session, fanpage, row: Settings) -> int:
    return max(0, (row.muse_pilot_packs or 0) - waiting_muse_ideas(db, fanpage.id))


def packs_needed(needs: list[int]) -> int:
    needs = [n for n in needs if n > 0]
    if not needs:
        return 0
    return max(max(needs), math.ceil(sum(needs) / MAX_CHANNELS_PER_PACK))


def latest_feedback_at(db: Session):
    value = db.query(func.max(ResearchFeedback.created_at)).filter(ResearchFeedback.author == "studio").scalar()
    return value.isoformat() if value else None


@lru_cache(maxsize=1)
def docs_text() -> str:
    return DOCS_PATH.read_text(encoding="utf-8")


def docs_version() -> str:
    return hashlib.sha1(docs_text().encode()).hexdigest()[:8]


def _brief_id(body: dict) -> str:
    digest = hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:8]
    return f"{_now():%Y%m%d%H%M}-{digest}"


def _idle(db: Session, reason: str) -> dict:
    body = {"schema_version": SCHEMA_VERSION, "docs_version": docs_version(), "mode": "pilot", "action": "idle", "reason": reason,
            "latest_feedback_at": latest_feedback_at(db), "niches": []}
    return {"brief_id": _brief_id(body), **body}


def build_brief(db: Session) -> dict:
    row = get_settings_row(db)
    if row.ai_video_researcher != "muse":
        return _idle(db, "research is handled elsewhere for now")
    if row.muse_research_mode != "pilot":
        return _idle(db, "this research mode is not available yet")

    references = effective_references(row)
    counts = topic_channel_counts(db)
    niches = []
    used_templates = {}
    for niche, fanpages in research_channels(db).items():
        channels = []
        for fp in fanpages:
            need = channel_need(db, fp, row)
            if need <= 0:
                continue
            lang = fanpage_language(fp)
            templates = channel_templates(fp, niche)
            for t in templates:
                used_templates.setdefault(t.key, (t, set()))[1].add(niche)
            channels.append({
                "channel_id": fp.id,
                "audience": {"language": lang, "country": fp.target_country},
                "ideas_needed": need,
                "templates": {
                    t.key: {"clip_options": clip_choices(fp, t), "vehicle_presets": niche_presets(t, niche),
                            "narration_words": _narration_words(t, lang)}
                    for t in templates
                },
                "copy_rules": copy_rules(fp),
                "recent_topics": recent_topics(db, fp.id),
            })
        if channels:
            niches.append({
                "niche": niche,
                "packs_needed": packs_needed([c["ideas_needed"] for c in channels]),
                "references": references.get(niche, {"video_style": [], "topic_signals": []}),
                "channels": channels,
            })
    if not niches:
        return _idle(db, "no channel needs ideas")

    body = {
        "schema_version": SCHEMA_VERSION,
        "docs_version": docs_version(),
        "mode": "pilot",
        "action": "research",
        "latest_feedback_at": latest_feedback_at(db),
        "queue_policy": QUEUE_POLICY,
        "studio": STUDIO,
        "idea_sources": IDEA_SOURCES,
        "global_rules": GLOBAL_RULES,
        "research_defaults": RESEARCH_DEFAULTS,
        "pack_rules": PACK_RULES,
        "niches": niches,
        "saturated_topics": sorted(text for text, channels in counts.values() if len(channels) >= MAX_CHANNELS_PER_PACK),
        "templates": [template_item(t, sorted(ns)) for t, ns in used_templates.values()],
    }
    return {"brief_id": _brief_id(body), **body}


# ── JSON Schema of POST /research/packs ───────────────────────────────────────

def pack_schema() -> dict:
    templates = list_templates()
    conditions = []
    for t in templates:
        conditions.append({
            "if": {"properties": {"template_key": {"const": t.key}}, "required": ["template_key"]},
            "then": {"properties": {
                "niche": {"enum": [n for n in NICHES if n in t.niches]},
                "clips": {"enum": list(t.clip_options)},
                "variants": {"items": {"properties": {"variables": {"properties": {"vehicle_preset": {"enum": vehicle_presets(t)}}}}}},
            }},
        })
        for n in t.clip_options:
            conditions.append({
                "if": {"properties": {"template_key": {"const": t.key}, "clips": {"const": n}}, "required": ["template_key", "clips"]},
                "then": {"properties": {"variants": {"items": {"properties": {"variables": {"required": content_slots(t, n)}}}}}},
            })
    count = {"type": ["integer", "null"], "minimum": 0}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "POST /research/packs body",
        "schema_version": SCHEMA_VERSION,
        "type": "object",
        "required": ["brief_id", "packs"],
        "properties": {
            "brief_id": {"type": "string", "maxLength": 64},
            "packs": {"type": "array", "minItems": 1, "maxItems": MAX_PACKS_PER_REQUEST, "items": {"$ref": "#/$defs/pack"}},
            "run_report": {
                "type": "object",
                "properties": {
                    "started_at": {"type": "string"}, "finished_at": {"type": "string"},
                    "sources_checked": {"type": "integer", "minimum": 0}, "videos_checked": {"type": "integer", "minimum": 0},
                    "usage": {"type": "string", "maxLength": 500},
                    "problems": {"type": "array", "items": {"type": "string", "maxLength": 500}},
                },
            },
        },
        "$defs": {
            "pack": {
                "type": "object",
                "required": ["client_ref", "niche", "template_key", "clips", "topic", "research_notes", "sources",
                             "references", "score", "rationale", "qc", "variants"],
                "properties": {
                    "client_ref": {"type": "string", "format": "uuid"},
                    "niche": {"enum": list(NICHES)},
                    "template_key": {"enum": [t.key for t in templates]},
                    "clips": {"type": "integer"},
                    "topic": {"type": "string", "minLength": 3, "maxLength": 200},
                    "research_notes": {"type": "string", "minLength": 1, "maxLength": 8000,
                                       "description": "Numbered facts, each ending with its source ids, e.g. '1. ... [S1][S3]'."},
                    "sources": {"type": "array", "minItems": 2, "maxItems": 8, "items": {
                        "type": "object", "required": ["id", "url", "publisher", "title", "published_at"],
                        "properties": {
                            "id": {"type": "string", "pattern": "^S[0-9]+$"},
                            "url": {"type": "string", "pattern": "^https?://", "maxLength": 300},
                            "publisher": {"type": "string", "minLength": 1, "maxLength": 100},
                            "title": {"type": "string", "minLength": 1, "maxLength": 200},
                            "published_at": {"type": ["string", "null"], "maxLength": 32},
                        }}},
                    "references": {"type": "array", "minItems": 1, "maxItems": 5, "items": {
                        "type": "object", "required": ["url", "platform", "kind"],
                        "properties": {
                            "url": {"type": "string", "pattern": "^https?://", "maxLength": 300},
                            "platform": {"enum": list(PLATFORMS)}, "kind": {"enum": list(REFERENCE_KINDS)},
                            "views": count, "views_approx": {"type": ["boolean", "null"]},
                            "likes": count, "comments": count, "shares": count,
                            "posted_at": {"type": ["string", "null"], "maxLength": 32},
                            "duration_s": {"type": ["number", "null"], "minimum": 0},
                            "why": {"type": ["string", "null"], "maxLength": 300},
                        }}},
                    "score": {"type": "integer", "minimum": MIN_SCORE, "maximum": 100},
                    "rationale": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "qc": {"type": "object", "required": ["passed"], "properties": {"passed": {"const": True}}},
                    "variants": {"type": "array", "minItems": 1, "maxItems": MAX_CHANNELS_PER_PACK, "items": {
                        "type": "object", "required": ["channel_id", "angle", "variables", "title", "caption"],
                        "properties": {
                            "channel_id": {"type": "integer"},
                            "angle": {"type": "string", "minLength": 1, "maxLength": 2000},
                            "variables": {
                                "type": "object", "required": ["vehicle_preset"],
                                "propertyNames": {"not": {"enum": sorted(STUDIO_SLOTS)}},
                                "additionalProperties": {"type": "string", "minLength": 1, "maxLength": 600, "pattern": "^[^{}]*$"},
                            },
                            "title": {"type": "string", "minLength": 1, "maxLength": 300},
                            "caption": {"type": "string", "minLength": 1, "maxLength": 2000},
                        }}},
                },
                "allOf": conditions,
            }
        },
    }


# ── Checking and storing packs ────────────────────────────────────────────────

def _first_sentence(text: str) -> str:
    match = re.match(r"(.+?[.!?])(\s|$)", text.strip(), re.S)
    return (match.group(1) if match else text.strip().splitlines()[0]).strip()


def _is_count(value) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def _check_pack_shape(pack) -> list[str]:
    if not isinstance(pack, dict):
        return ["pack must be an object"]
    errors = []
    ref = pack.get("client_ref")
    try:
        if not isinstance(ref, str) or len(ref) > 64:
            raise ValueError
        uuid.UUID(ref)
    except ValueError:
        errors.append("client_ref must be a UUID")
    if pack.get("niche") not in NICHES:
        errors.append(f"niche must be one of {list(NICHES)}")
    template = None
    try:
        template = get_template(pack.get("template_key") or "")
    except ValueError:
        errors.append("unknown template_key")
    if template and pack.get("niche") in NICHES and pack["niche"] not in template.niches:
        errors.append(f"template {template.key} is not available for niche {pack['niche']}")
    clips = pack.get("clips")
    if template and (isinstance(clips, bool) or clips not in template.clip_options):
        errors.append(f"clips must be one of {list(template.clip_options)}")
    topic = pack.get("topic")
    if not isinstance(topic, str) or not 3 <= len(topic.strip()) <= 200:
        errors.append("topic must be 3-200 characters")
    notes = pack.get("research_notes")
    if not isinstance(notes, str) or not notes.strip() or len(notes) > 8000:
        errors.append("research_notes must be 1-8000 characters")

    sources = pack.get("sources")
    source_ids = []
    if not isinstance(sources, list) or not 2 <= len(sources) <= 8:
        errors.append("sources must list 2-8 sources")
    else:
        for i, src in enumerate(sources, start=1):
            if not isinstance(src, dict):
                errors.append(f"sources[{i}] must be an object")
                continue
            sid = src.get("id")
            if not isinstance(sid, str) or not SOURCE_ID.match(sid):
                errors.append(f"sources[{i}].id must look like S1")
            elif sid in source_ids:
                errors.append(f"source id {sid} is repeated")
            else:
                source_ids.append(sid)
            url = src.get("url")
            if not isinstance(url, str) or not url.startswith(("https://", "http://")) or len(url) > 300:
                errors.append(f"sources[{i}].url must be an http(s) link of at most 300 characters")
            for key, limit in (("publisher", 100), ("title", 200)):
                value = src.get(key)
                if not isinstance(value, str) or not value.strip() or len(value) > limit:
                    errors.append(f"sources[{i}].{key} must be 1-{limit} characters")
            published = src.get("published_at")
            if published is not None and (not isinstance(published, str) or len(published) > 32):
                errors.append(f"sources[{i}].published_at must be a short date string or null")
    if isinstance(notes, str) and source_ids:
        cited = set(CITE.findall(notes))
        if not cited:
            errors.append("research_notes must cite its sources as [S1], [S2] ...")
        for missing in sorted(cited - set(source_ids)):
            errors.append(f"research_notes cites {missing}, which is not in sources")

    references = pack.get("references")
    if not isinstance(references, list) or not 1 <= len(references) <= 5:
        errors.append("references must list 1-5 videos or posts")
    else:
        for i, ref_item in enumerate(references, start=1):
            if not isinstance(ref_item, dict):
                errors.append(f"references[{i}] must be an object")
                continue
            url = ref_item.get("url")
            if not isinstance(url, str) or not url.startswith(("https://", "http://")) or len(url) > 300:
                errors.append(f"references[{i}].url must be an http(s) link of at most 300 characters")
            if ref_item.get("platform") not in PLATFORMS:
                errors.append(f"references[{i}].platform must be one of {list(PLATFORMS)}")
            if ref_item.get("kind") not in REFERENCE_KINDS:
                errors.append(f"references[{i}].kind must be one of {list(REFERENCE_KINDS)}")
            for key in ("views", "likes", "comments", "shares"):
                if not _is_count(ref_item.get(key)):
                    errors.append(f"references[{i}].{key} must be a whole number or null")
            duration = ref_item.get("duration_s")
            if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0):
                errors.append(f"references[{i}].duration_s must be a number or null")

    score = pack.get("score")
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
        errors.append("score must be a whole number 0-100")
    elif score < MIN_SCORE:
        errors.append(f"score must be at least {MIN_SCORE}; send only ideas you are confident in")
    rationale = pack.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 4000:
        errors.append("rationale must be 1-4000 characters")
    qc = pack.get("qc")
    if not isinstance(qc, dict) or qc.get("passed") is not True:
        errors.append("qc.passed must be true: run your quality check before sending")
    variants = pack.get("variants")
    if not isinstance(variants, list) or not 1 <= len(variants) <= MAX_CHANNELS_PER_PACK:
        errors.append(f"variants must list 1-{MAX_CHANNELS_PER_PACK} channels")
    else:
        ids = [v.get("channel_id") for v in variants if isinstance(v, dict)]
        if len(ids) != len(variants):
            errors.append("every variant must be an object")
        elif len(set(map(str, ids))) != len(ids):
            errors.append("a channel appears twice in variants")
    if not errors:
        evidence = {"sources": sources, "references": references, "qc": qc}
        if len(json.dumps(evidence, ensure_ascii=False).encode()) > EVIDENCE_LIMIT:
            errors.append(f"sources, references and qc together must stay under {EVIDENCE_LIMIT} bytes")
    return errors


def _narration_text(variables: dict) -> str:
    return " ".join(v for k, v in sorted(variables.items()) if k.startswith("narration_") and isinstance(v, str))


def _check_variant(db, variant, pack, template, row, need_left, accepted, channel_topic_counts) -> tuple[list[str], object]:
    errors = []
    channel_id = variant.get("channel_id")
    fp = None
    if isinstance(channel_id, int) and not isinstance(channel_id, bool):
        fp = db.query(TargetFanpage).filter_by(id=channel_id).first()
    if fp is None or not fp.is_active or not fp.ai_video_enabled:
        return ["unknown channel_id, or the channel does not take ideas now"], None
    niche = fanpage_niche(fp)
    if niche != pack["niche"]:
        return [f"channel {channel_id} is a {niche} channel, not {pack['niche']}"], None
    if need_left.get(fp.id, 0) <= 0:
        return [f"channel {channel_id} needs no more ideas now"], None
    allowed = [t.key for t in channel_templates(fp, niche)]
    if template.key not in allowed:
        errors.append(f"template {template.key} is not allowed for channel {channel_id}")
    elif pack["clips"] not in clip_choices(fp, template):
        errors.append(f"clips must be one of {clip_choices(fp, template)} for channel {channel_id}")
    topic_key = _normalized_topic(pack["topic"])
    if topic_key in {_normalized_topic(t) for t in recent_topics(db, fp.id)}:
        errors.append(f"channel {channel_id} already had this topic in the last {TOPIC_DAYS} days")
    channels_with_topic = channel_topic_counts.get(topic_key, set())
    if fp.id not in channels_with_topic and len(channels_with_topic) >= MAX_CHANNELS_PER_PACK:
        errors.append(f"this topic is already used on {MAX_CHANNELS_PER_PACK} channels")
    min_score = max(MIN_SCORE, fp.ai_video_min_score or 0)
    if pack["score"] < min_score:
        errors.append(f"score must be at least {min_score} for channel {channel_id}")

    angle = variant.get("angle")
    if not isinstance(angle, str) or not angle.strip() or len(angle) > 2000:
        errors.append("angle must be 1-2000 characters")
    variables = variant.get("variables")
    if not isinstance(variables, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in variables.items()):
        errors.append("variables must be an object of text values")
        variables = None
    else:
        sent_studio_slots = sorted(STUDIO_SLOTS & set(variables))
        if sent_studio_slots:
            errors.append(f"do not send {', '.join(sent_studio_slots)}: the studio sets them")
        missing = [s for s in content_slots(template, pack["clips"]) if not (variables.get(s) or "").strip()]
        if missing:
            errors.append(f"missing slots: {', '.join(missing)}")
        preset = variables.get("vehicle_preset")
        if preset not in niche_presets(template, niche):
            errors.append(f"vehicle_preset must be one of {niche_presets(template, niche)}")
        elif not missing and not sent_studio_slots:
            render_vars = dict(variables)
            if any("{livery" in template.raw["blocks"]["vehicle_presets"].get(p, "") for p in (preset, preset + "_short")):
                render_vars["livery"] = liveries(template)[0]
            try:
                render_clips(template, render_vars, pack["clips"], lang_code=fanpage_language(fp))
            except ValueError as exc:
                errors.append(str(exc))
        for key, value in variables.items():
            names = find_real_names(value)
            if names:
                errors.append(f"real names are not allowed in {key}: {', '.join(names)}")

    rules = copy_rules(fp)
    title, caption = variant.get("title"), variant.get("caption")
    if not isinstance(title, str) or not title.strip() or len(title) > rules["title_max_chars"]:
        errors.append(f"title must be 1-{rules['title_max_chars']} characters")
    elif find_real_names(title):
        errors.append(f"real names are not allowed in title: {', '.join(find_real_names(title))}")
    if not isinstance(caption, str) or not caption.strip() or len(caption) > rules["caption_max_chars"]:
        errors.append(f"caption must be 1-{rules['caption_max_chars']} characters")
    else:
        hook = _first_sentence(caption)
        if len(hook) > HOOK_MAX_CHARS:
            errors.append(f"the caption's first sentence (the hook) is {len(hook)} characters; max {HOOK_MAX_CHARS}")
        tags = [w for w in caption.split() if w.startswith("#")]
        if len(tags) > rules["hashtags_max"]:
            errors.append(f"caption has {len(tags)} hashtags; max {rules['hashtags_max']}")
        names = find_real_names(caption)
        if names:
            errors.append(f"real names are not allowed in caption: {', '.join(names)}")

    if variables and isinstance(title, str):
        narration = _narration_text(variables)
        for other_channel, other_narration, other_title in accepted:
            if other_title.strip().casefold() == title.strip().casefold():
                errors.append(f"title is the same as the variant for channel {other_channel}")
            elif narration and difflib.SequenceMatcher(None, narration, other_narration).ratio() >= SIMILARITY_LIMIT:
                errors.append(f"narration is a near-copy of the variant for channel {other_channel}; write it differently")
    return errors, fp


def _stored_variants(db: Session, client_ref: str) -> list[dict]:
    ideas = db.query(VideoIdea).filter(VideoIdea.client_ref == client_ref).order_by(VideoIdea.id).all()
    return [{"channel_id": i.fanpage_id, "status": "exists", "idea_id": i.id, "errors": []} for i in ideas]


def submit_pack(db: Session, pack, brief_id, row: Settings, token_id=None) -> dict:
    ref = pack.get("client_ref") if isinstance(pack, dict) else None
    errors = _check_pack_shape(pack)
    if errors:
        return {"client_ref": ref, "status": "rejected", "errors": errors, "variants": []}
    stored = _stored_variants(db, ref)
    if stored:
        return {"client_ref": ref, "status": "exists", "errors": [], "variants": stored}
    if row.ai_video_researcher != "muse" or row.muse_research_mode != "pilot":
        return {"client_ref": ref, "status": "rejected", "errors": ["the studio is not taking research packs now"], "variants": []}

    template = get_template(pack["template_key"])
    counts = {key: channels for key, (_text, channels) in topic_channel_counts(db).items()}
    topic_key = _normalized_topic(pack["topic"])
    need_left = {}
    results, accepted, to_create = [], [], []
    for variant in pack["variants"]:
        channel_id = variant.get("channel_id")
        fp = db.query(TargetFanpage).filter_by(id=channel_id).first() if isinstance(channel_id, int) and not isinstance(channel_id, bool) else None
        if fp is not None and fp.id not in need_left:
            need_left[fp.id] = channel_need(db, fp, row)
        v_errors, fp = _check_variant(db, variant, pack, template, row, need_left, accepted, counts)
        if v_errors:
            results.append({"channel_id": channel_id, "status": "rejected", "errors": v_errors})
            continue
        need_left[fp.id] -= 1
        counts.setdefault(topic_key, set()).add(fp.id)
        accepted.append((fp.id, _narration_text(variant["variables"]), variant["title"]))
        to_create.append((fp, variant))
        results.append({"channel_id": channel_id, "status": "created", "errors": []})

    if not to_create:
        return {"client_ref": ref, "status": "rejected", "errors": ["no variant passed the checks"], "variants": results}

    evidence = {"pack_ref": ref, "brief_id": brief_id, "niche": pack["niche"], "sources": pack["sources"],
                "references": pack["references"], "qc": pack["qc"]}
    ideas = []
    for fp, variant in to_create:
        idea = VideoIdea(
            fanpage_id=fp.id, template_key=template.key, topic=pack["topic"].strip(), angle=variant["angle"].strip(),
            research_notes=pack["research_notes"], variables=dict(variant["variables"]), clips=pack["clips"],
            score=pack["score"], rationale=pack["rationale"], evidence=evidence, source="muse", token_id=token_id,
            status="pilot", client_ref=ref, title=variant["title"].strip(), caption=variant["caption"].strip(),
        )
        db.add(idea)
        ideas.append((fp.id, idea))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"client_ref": ref, "status": "exists", "errors": [], "variants": _stored_variants(db, ref)}
    ids = {fp_id: idea.id for fp_id, idea in ideas}
    for result in results:
        if result["status"] == "created":
            result["idea_id"] = ids[result["channel_id"]]
    status = "created" if len(to_create) == len(pack["variants"]) else "partial"
    return {"client_ref": ref, "status": status, "errors": [], "variants": results}


def save_run_report(db: Session, row: Settings, report) -> None:
    if not isinstance(report, dict):
        return
    text = json.dumps(report, ensure_ascii=False, default=str)
    row.research_last_report = {"received_at": _now().isoformat(), **report} if len(text) <= 4000 else {
        "received_at": _now().isoformat(), "note": "run report too large to keep"}
    db.commit()


# ── Status, feedback and the call log ─────────────────────────────────────────

def list_packs(db: Session, client_ref: str | None = None, since: datetime | None = None, limit: int = 100) -> list[dict]:
    q = db.query(VideoIdea).filter(VideoIdea.source == "muse", VideoIdea.client_ref.isnot(None))
    if client_ref:
        q = q.filter(VideoIdea.client_ref == client_ref)
    if since:
        q = q.filter(VideoIdea.created_at >= since)
    packs: dict[str, dict] = {}
    for idea in q.order_by(VideoIdea.created_at.desc(), VideoIdea.id).limit(limit * MAX_CHANNELS_PER_PACK).all():
        pack = packs.get(idea.client_ref)
        if pack is None:
            if len(packs) >= limit:
                continue
            pack = packs[idea.client_ref] = {
                "client_ref": idea.client_ref, "niche": (idea.evidence or {}).get("niche"), "topic": idea.topic,
                "template_key": idea.template_key, "created_at": idea.created_at.isoformat(), "variants": [],
            }
        pack["variants"].append({"channel_id": idea.fanpage_id, "idea_id": idea.id,
                                 "status": STATUS_FOR_MUSE.get(idea.status, idea.status), "error": idea.error})
    return list(packs.values())


def add_feedback(db: Session, *, author: str, kind: str, message: str, client_ref=None, idea_id=None,
                 username=None, token_id=None) -> ResearchFeedback:
    item = ResearchFeedback(author=author, kind=kind, message=message.strip()[:2000], client_ref=client_ref,
                            idea_id=idea_id, username=username, token_id=token_id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def feedback_for_muse(db: Session, since: datetime | None = None, limit: int = 200) -> list[dict]:
    q = db.query(ResearchFeedback, VideoIdea).outerjoin(VideoIdea, VideoIdea.id == ResearchFeedback.idea_id).filter(
        ResearchFeedback.author == "studio")
    if since:
        q = q.filter(ResearchFeedback.created_at > since)
    out = []
    for fb, idea in q.order_by(ResearchFeedback.created_at.desc()).limit(limit).all():
        out.append({
            "id": fb.id, "created_at": fb.created_at.isoformat(), "client_ref": fb.client_ref, "idea_id": fb.idea_id,
            "channel_id": idea.fanpage_id if idea else None, "topic": idea.topic if idea else None,
            "kind": fb.kind, "message": fb.message,
        })
    return out


def feedback_thread(db: Session, idea: VideoIdea) -> list[dict]:
    q = db.query(ResearchFeedback).filter(ResearchFeedback.idea_id == idea.id)
    if idea.client_ref:
        q = db.query(ResearchFeedback).filter(
            (ResearchFeedback.idea_id == idea.id)
            | ((ResearchFeedback.client_ref == idea.client_ref) & ResearchFeedback.idea_id.is_(None)))
    return [{"id": fb.id, "created_at": fb.created_at, "author": fb.author, "kind": fb.kind, "message": fb.message,
             "username": fb.username} for fb in q.order_by(ResearchFeedback.created_at).all()]


def reject_for_review(db: Session, idea: VideoIdea, username: str, reason: str | None) -> VideoIdea:
    idea.status, idea.decided_by, idea.decided_at = "rejected", username, _now()
    db.commit()
    if reason and reason.strip() and idea.source == "muse":
        add_feedback(db, author="studio", kind="rejected", message=reason, client_ref=idea.client_ref,
                     idea_id=idea.id, username=username)
    db.refresh(idea)
    return idea


def log_call(db: Session, endpoint: str, action: str, detail: dict | None = None, token_id=None) -> None:
    db.add(ResearchCall(endpoint=endpoint, action=action, detail=detail, token_id=token_id))
    db.query(ResearchCall).filter(ResearchCall.created_at < _now() - timedelta(days=CALL_LOG_DAYS)).delete(synchronize_session=False)
    db.commit()


def recent_calls(db: Session, limit: int = 50) -> list[dict]:
    rows = db.query(ResearchCall).order_by(ResearchCall.created_at.desc(), ResearchCall.id.desc()).limit(limit).all()
    return [{"id": r.id, "created_at": r.created_at, "endpoint": r.endpoint, "action": r.action, "detail": r.detail} for r in rows]


def last_call_at(db: Session):
    return db.query(func.max(ResearchCall.created_at)).scalar()


def review_packs(db: Session, status: str | None = None, since: datetime | None = None, limit: int = 30) -> list[dict]:
    """Full content of Muse's packs for a reviewer token (no page names either)."""
    q = db.query(VideoIdea).filter(VideoIdea.source == "muse", VideoIdea.client_ref.isnot(None))
    if status:
        q = q.filter(VideoIdea.status == status)
    if since:
        q = q.filter(VideoIdea.created_at >= since)
    packs: dict[str, dict] = {}
    for idea in q.order_by(VideoIdea.created_at.desc(), VideoIdea.id).limit(limit * MAX_CHANNELS_PER_PACK).all():
        pack = packs.get(idea.client_ref)
        if pack is None:
            if len(packs) >= limit:
                continue
            evidence = idea.evidence or {}
            pack = packs[idea.client_ref] = {
                "client_ref": idea.client_ref, "niche": evidence.get("niche"), "template_key": idea.template_key,
                "clips": idea.clips, "topic": idea.topic, "research_notes": idea.research_notes,
                "sources": evidence.get("sources"), "references": evidence.get("references"), "score": idea.score,
                "rationale": idea.rationale, "qc": evidence.get("qc"), "created_at": idea.created_at.isoformat(),
                "variants": [],
            }
        pack["variants"].append({
            "idea_id": idea.id, "channel_id": idea.fanpage_id, "status": STATUS_FOR_MUSE.get(idea.status, idea.status),
            "angle": idea.angle, "variables": idea.variables, "title": idea.title, "caption": idea.caption,
            "error": idea.error,
        })
    return list(packs.values())
