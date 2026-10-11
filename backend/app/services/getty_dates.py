"""Offline Getty asset-id date estimation and editorial slug hints."""

import re
from datetime import date, timedelta
from typing import Iterable

from app.models.gallery import GalleryImage

ASSET_RE = re.compile(r"/id/(\d+)/")
SLUG_RE = re.compile(r"/photo/([^/?#]+)", re.IGNORECASE)
SEPARATORS = {"during", "of", "and", "after", "before", "celebrates", "drives", "looks", "talks"}
SESSION_HINTS = (
    ("press conference", "press_conference"), ("warm-up", "warm_up"), ("warm up", "warm_up"),
    ("practice", "practice"), ("qualifying", "qualifying"), ("sprint", "sprint"),
    ("race", "race"), ("podium", "podium"), ("paddock", "paddock"),
    ("pit lane", "pitlane"), ("pitlane", "pitlane"), ("test", "test"),
)


def parse_asset_id(source_url: str | None) -> int | None:
    match = ASSET_RE.search(source_url or "")
    return int(match.group(1)) if match else None


def _slug(source_url: str | None) -> str:
    match = SLUG_RE.search(source_url or "")
    return match.group(1).rsplit(".", 1)[0] if match else ""


def build_hints(source_url: str | None) -> dict:
    slug = _slug(source_url)
    tokens = re.split(r"[-_]+", slug)
    location_tokens = []
    for token in tokens[:4]:
        if token.lower() in SEPARATORS:
            break
        location_tokens.append(token)
    session_hint = None
    lowered = slug.replace("-", " ").lower()
    for phrase, hint in SESSION_HINTS:
        if phrase in lowered:
            session_hint = hint
            break
    return {"location": " ".join(location_tokens).title() or None, "session_hint": session_hint, "slug": slug}


def _clean_anchors(anchors: Iterable[tuple[int, date]]) -> list[tuple[int, date]]:
    ordered = sorted(set(anchors))
    if len(ordered) < 3:
        return ordered
    kept = [ordered[0]]
    for index in range(1, len(ordered) - 1):
        previous, current, following = ordered[index - 1], ordered[index], ordered[index + 1]
        if current[1] < previous[1] - timedelta(days=3) or current[1] > following[1] + timedelta(days=3):
            continue
        kept.append(current)
    kept.append(ordered[-1])
    return kept


def estimate_date_from_anchors(asset_id: int, anchors: Iterable[tuple[int, date]]) -> tuple[date, str] | None:
    clean = _clean_anchors(anchors)
    if not clean:
        return None
    if asset_id <= clean[0][0]:
        return clean[0][1], "estimated"
    if asset_id >= clean[-1][0]:
        return clean[-1][1], "estimated"
    for lower, upper in zip(clean, clean[1:]):
        if lower[0] <= asset_id <= upper[0]:
            span = upper[0] - lower[0]
            ratio = (asset_id - lower[0]) / span if span else 0
            days = round((upper[1] - lower[1]).days * ratio)
            confidence = "day" if (upper[1] - lower[1]).days <= 7 else "estimated"
            return lower[1] + timedelta(days=days), confidence
    return None


def anchors_from_db(db) -> list[tuple[int, date]]:
    rows = db.query(GalleryImage).filter(GalleryImage.captured_at.is_not(None)).all()
    return [
        (asset_id, row.captured_at)
        for row in rows
        if (asset_id := parse_asset_id(row.source_image_url)) is not None
        and (row.attributes or {}).get("captured_at_source", "caption") == "caption"
    ]
