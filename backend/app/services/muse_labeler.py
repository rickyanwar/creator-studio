"""Temporary, token-protected labeling workflow for the external Muse agent."""

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import redis
from sqlalchemy import func

from app.config import get_settings
from app.models.gallery import GalleryImage
from app.services.gallery_labeler import (
    ACTIVITIES,
    CONTEXT_SESSIONS,
    EXPRESSIONS,
    SHOT_TYPES,
    SPORTS,
    VENUE_TYPES,
    normalise_label_output,
    keyword_name_from_gallery_keyword,
)
from app.services.getty_dates import anchors_from_db, build_hints, estimate_date_from_anchors, parse_asset_id

LEASE_PREFIX = "labeler:lease:"
LEASE_SECONDS = 30 * 60
CLAIM_LABELS = ("face", "action", "other")
CONFIDENCE_RANK = {"none": 0, "estimated": 1, "day": 2, "exact": 3}


def redis_client():
    return redis.from_url(get_settings().redis_url, decode_responses=True)


def acquire_lease(client, image_id: int, worker: str) -> bool:
    return bool(client.set(f"{LEASE_PREFIX}{image_id}", worker, nx=True, ex=LEASE_SECONDS))


def release_lease(client, image_id: int, worker: str) -> bool:
    key = f"{LEASE_PREFIX}{image_id}"
    if client.get(key) != worker:
        return False
    client.delete(key)
    return True


def label_schema() -> dict:
    return {
        "version": 1,
        "fields": {
            "sport": {"type": "string", "allowed": sorted(SPORTS)},
            "venue": {"type": "object", "fields": {
                "type": {"type": "string", "allowed": sorted(VENUE_TYPES)},
                "visible": {"type": "boolean"},
            }},
            "shot_type": {"type": "string", "allowed": sorted(SHOT_TYPES)},
            "people_count": {"type": "integer", "min": 0, "max": 20},
            "activity": {"type": "string", "allowed": sorted(ACTIVITIES)},
            "expression": {"type": "string", "allowed": sorted(EXPRESSIONS)},
            "context.session": {"type": "string", "allowed": sorted(CONTEXT_SESSIONS)},
            "context.rain": {"type": "boolean"},
            "context.night": {"type": "boolean"},
            "context.circuit_visible": {"type": "boolean", "compatibility": "true only for visible circuits"},
            "livery_colors": {"type": "array", "max": 3, "use_for": "motorsport"},
            "kit_colors": {"type": "array", "max": 3, "use_for": "non-motorsport sports"},
            "quality.blurry": {"type": "boolean"},
            "quality.obstructed": {"type": "boolean"},
            "caption": {"type": "string", "max": 160},
            "people": {"type": "array", "max": 10, "item": {"name": "string <= 80", "confidence": "number 0..1"}},
            "keyword_mismatch": {"type": "boolean", "optional": True},
            "event": {"type": "object", "fields": {
                "series": "string <= 40", "event_name": "string <= 80",
                "year": "integer 1950..2100", "season": "YYYY or YYYY-YY",
                "session": {"type": "string", "allowed": sorted(CONTEXT_SESSIONS)},
            }},
            "evidence": {"type": "string", "max": 300},
        },
        "limits": {"claim": 50, "results": 50, "label": list(CLAIM_LABELS)},
        "guidelines": "One label set per photo; use the gallery keyword as a name hint; name a matching person at confidence >= 0.8 with gallery keyword + visual cue evidence; leave people empty and set keyword_mismatch when the photo contradicts the keyword; use evidence for dates; never guess names.",
    }


def _bounded_string(value: Any, max_length: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"{field} exceeds {max_length} characters")
    return value


def normalise_result(data: dict, keyword: str | None = None) -> dict:
    return _normalise_result(data, keyword=keyword)


def apply_archival_photo(row, attributes: dict) -> None:
    if attributes.get("archival_photo") is True:
        row.attributes = dict(row.attributes or {})
        row.attributes["archival_photo"] = True
        row.attributes["captured_at_confidence"] = "estimated"


def _normalise_result(data: dict, keyword: str | None = None) -> dict:
    attributes = normalise_label_output(data.get("attributes") or data, keyword=keyword)
    people = data.get("people", [])
    if not isinstance(people, list) or len(people) > 10:
        raise ValueError("people must be a list of at most 10 items")
    normalized_people = []
    for person in people:
        if not isinstance(person, dict):
            raise ValueError("people items must be objects")
        name = _bounded_string(person.get("name", ""), 80, "people.name")
        confidence = person.get("confidence")
        if not name or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("people requires name and confidence between 0 and 1")
        normalized_people.append({"name": name, "confidence": confidence})
    if normalized_people:
        attributes["people"] = normalized_people
    elif not attributes.get("keyword_mismatch"):
        name = keyword_name_from_gallery_keyword(keyword)
        attributes["people"] = ([{"name": name, "confidence": 0.8, "source": "keyword"}] if name else [])
    keyword_mismatch = data.get("keyword_mismatch", False)
    if not isinstance(keyword_mismatch, bool):
        raise ValueError("keyword_mismatch must be a boolean")
    attributes["keyword_mismatch"] = keyword_mismatch
    archival_photo = data.get("archival_photo", False)
    if not isinstance(archival_photo, bool):
        raise ValueError("archival_photo must be a boolean")
    attributes["archival_photo"] = archival_photo

    raw_event = data.get("event")
    if raw_event is not None:
        if not isinstance(raw_event, dict):
            raise ValueError("event must be an object")
        event = {
            "series": _bounded_string(raw_event.get("series", ""), 40, "event.series"),
            "event_name": _bounded_string(raw_event.get("event_name", ""), 80, "event.event_name"),
            "year": raw_event.get("year"),
            "session": str(raw_event.get("session") or "unknown").lower(),
        }
        if event["year"] is not None and not isinstance(event["year"], int):
            raise ValueError("event.year must be an integer")
        if event["year"] is not None and not 1950 <= event["year"] <= 2100:
            raise ValueError("event.year must be between 1950 and 2100")
        if event["session"] not in CONTEXT_SESSIONS:
            event["session"] = "unknown"
        season = raw_event.get("season")
        if season is not None:
            if not isinstance(season, str) or not re.fullmatch(r"[0-9]{4}(-[0-9]{2})?", season):
                raise ValueError("event.season must match YYYY or YYYY-YY")
            event["season"] = season
        attributes["event"] = event

    evidence = data.get("evidence")
    if evidence is not None:
        attributes["evidence"] = _bounded_string(evidence, 300, "evidence")
    attributes["source"] = "muse"
    attributes["schema_version"] = 1
    return attributes


def apply_captured_at(row, value: str, confidence: str) -> None:
    if confidence not in CONFIDENCE_RANK or confidence == "none":
        raise ValueError("captured_at_confidence must be exact, day, or estimated")
    current_confidence = (row.attributes or {}).get("captured_at_confidence")
    if not current_confidence:
        current_confidence = "day" if row.captured_at else "none"
    if CONFIDENCE_RANK[confidence] <= CONFIDENCE_RANK.get(current_confidence, 0):
        return
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    row.captured_at = parsed.date()
    row.attributes = dict(row.attributes or {})
    row.attributes["captured_at_confidence"] = confidence


def claim_images(db, limit: int, worker: str, client=None, label: str | None = None) -> list[dict]:
    client = client or redis_client()
    if label is not None and label not in CLAIM_LABELS:
        raise ValueError(f"label must be one of {CLAIM_LABELS}")
    leased_ids = {
        int(key[len(LEASE_PREFIX):])
        for key in client.scan_iter(match=f"{LEASE_PREFIX}*")
        if key[len(LEASE_PREFIX):].isdigit()
    }
    usage = (
        db.query(GalleryImage.keyword, func.count(GalleryImage.id).label("usage_count"))
        .filter(GalleryImage.is_used.is_(True), GalleryImage.last_used_at >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=90))
        .group_by(GalleryImage.keyword)
        .subquery()
    )
    candidates = (db.query(GalleryImage)
        .outerjoin(usage, GalleryImage.keyword == usage.c.keyword)
        .filter(GalleryImage.is_deleted.is_(False), GalleryImage.attributes.is_(None), GalleryImage.label_attempts < 3)
        .order_by(func.coalesce(usage.c.usage_count, 0).desc(), GalleryImage.downloaded_at.desc())
    )
    if label is not None:
        candidates = candidates.filter(GalleryImage.label == label)
    if leased_ids:
        candidates = candidates.filter(GalleryImage.id.notin_(leased_ids))
    candidates = candidates.limit(max(limit * 3, limit)).all()
    selected = []
    for image in candidates:
        if len(selected) >= limit:
            break
        if acquire_lease(client, image.id, worker):
            selected.append({"id": image.id, "image_url": image.public_url, "keyword": image.keyword,
                             "extra_keywords": image.extra_keywords or [], "current_label": image.label,
                             "source_engine": image.source_engine, "downloaded_at": image.downloaded_at,
                             "captured_at": image.captured_at,
                             "captured_at_confidence": (image.attributes or {}).get("captured_at_confidence"),
                             "captured_at_source": (image.attributes or {}).get("captured_at_source"),
                             "getty_hints": build_hints(image.source_image_url),
                             "width": image.width, "height": image.height})
    return selected


def process_results(db, payload: dict, worker: str, client=None) -> dict:
    client = client or redis_client()
    results = payload.get("results", [])
    if len(results) > 50:
        raise ValueError("results cannot contain more than 50 items")
    saved = failed = 0
    skipped, errors = [], []
    for item in results:
        image_id = item.get("id")
        row = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
        if not row or row.attributes is not None:
            skipped.append(image_id)
            continue
        try:
            if item.get("error") is not None:
                row.label_attempts = (row.label_attempts or 0) + 1
                row.label_error = str(item["error"])[:300]
                failed += 1
            else:
                attrs = _normalise_result(item, keyword=row.keyword)
                row.attributes = attrs
                row.labeled_at = datetime.now(timezone.utc).replace(tzinfo=None)
                row.label_error = None
                confidence = item.get("captured_at_confidence")
                if item.get("captured_at") and confidence:
                    apply_captured_at(row, item["captured_at"], confidence)
                apply_archival_photo(row, attrs)
                saved += 1
        except ValueError as exc:
            row.label_attempts = (row.label_attempts or 0) + 1
            row.label_error = str(exc)[:300]
            errors.append({"id": image_id, "error": str(exc)})
            failed += 1
        finally:
            release_lease(client, image_id, worker)
    db.commit()
    return {"saved": saved, "failed": failed, "skipped": skipped, "errors": errors}


def fill_people_from_keyword(db, dry_run: bool = False) -> int:
    """Fill empty people arrays for labeled, non-mismatched gallery rows."""
    changed = 0
    rows = db.query(GalleryImage).filter(GalleryImage.attributes.is_not(None)).yield_per(500)
    for row in rows:
        attributes = dict(row.attributes or {})
        if attributes.get("keyword_mismatch") is True or attributes.get("people"):
            continue
        name = keyword_name_from_gallery_keyword(row.keyword)
        if not name:
            continue
        attributes["people"] = [{"name": name, "confidence": 0.8, "source": "keyword"}]
        changed += 1
        if not dry_run:
            row.attributes = attributes
    if not dry_run:
        db.commit()
    return changed
