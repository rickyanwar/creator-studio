"""Pro poster photo gathering, vision verification, and selection.

Gathers candidate photos from the story photo and the gallery, verifies each candidate
against the brief using a strict JSON Gemini Flash vision check (fail-closed on person,
event match, obstruction, or watermark), and selects the verified hero and context photos.
"""
from __future__ import annotations

import json
import logging
import string
import unicodedata
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import String, cast, func, or_

from app.models.gallery import GalleryImage
from app.services.gallery_filters import not_keyword_mismatch
from app.services import design_images, speaker_design
from app.services.pro_poster.brief import _SERIES_ALIASES, _normalize_series
from app.services.pro_poster.types import (
    PhotoCandidate,
    PhotoCheck,
    PhotoSelection,
    PosterBrief,
)
from app.services.visual_sourcing import get_name_variants

logger = logging.getLogger(__name__)

PREFERRED_SHOT_TYPES = frozenset(
    {"car_on_track", "athlete_in_action", "podium", "pitlane"}
)


def gallery_caption(row: Any) -> str | None:
    """Extract a clean lowercase caption from row.source_image_url slug.

    Takes the last path segment, drops extension and query string,
    replaces '-' and '_' with spaces, collapses spaces, and lowercases.
    Returns None if URL is empty or the cleaned slug is shorter than 12 characters.
    """
    url = getattr(row, "source_image_url", None) if not isinstance(row, dict) else row.get("source_image_url")
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    path_part = url.split("?", 1)[0].split("#", 1)[0]
    segment = path_part.rstrip("/").split("/")[-1]
    if "." in segment:
        segment = segment.rsplit(".", 1)[0]
    cleaned = " ".join(segment.replace("-", " ").replace("_", " ").split()).lower()
    if len(cleaned) < 12:
        return None
    return cleaned


def _as_naive_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    return datetime.min


def _is_usable_row(row: GalleryImage) -> bool:
    if getattr(row, "is_deleted", False):
        return False
    attrs = getattr(row, "attributes", None) or {}
    if not isinstance(attrs, dict):
        return True
    if attrs.get("archival_photo") is True:
        return False
    quality = attrs.get("quality")
    if isinstance(quality, dict):
        if quality.get("blurry") is True or quality.get("obstructed") is True:
            return False
    return True


def _normalize_name(s: str) -> str:
    if not s:
        return ""
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


def _strip_accents(s: str) -> str:
    if not s:
        return ""
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


def _get_name_targets(names: list[str]) -> set[str]:
    targets: set[str] = set()
    for n in names:
        norm = _normalize_name(n)
        if norm:
            targets.add(norm)
        parts = n.strip().split()
        if len(parts) >= 2:
            surname_norm = _normalize_name(parts[-1])
            if len(surname_norm) >= 5:
                targets.add(surname_norm)
    return targets


def _matches_person(row: GalleryImage, names: list[str]) -> bool:
    """Check if row matches person using ONLY people field, keyword, or extra_keywords.

    NEVER matches on location, slug, caption, or getty_hints.
    Normalises with unicodedata (NFKD, drop combining marks, casefold) on BOTH sides.
    Also matches distinctive surname alone (>= 5 letters).
    """
    attrs = getattr(row, "attributes", None) or {}
    if not isinstance(attrs, dict):
        attrs = {}

    targets = _get_name_targets(names)
    if not targets:
        return False

    kw = _normalize_name(getattr(row, "keyword", "") or "")
    if kw:
        for t in targets:
            if t in kw:
                return True

    extra_kw = getattr(row, "extra_keywords", []) or []
    if isinstance(extra_kw, (list, tuple, set)):
        for ek in extra_kw:
            ek_norm = _normalize_name(str(ek))
            if ek_norm:
                for t in targets:
                    if t in ek_norm:
                        return True
    elif isinstance(extra_kw, str):
        ek_norm = _normalize_name(extra_kw)
        if ek_norm:
            for t in targets:
                if t in ek_norm:
                    return True

    people = attrs.get("people", [])
    if isinstance(people, list):
        for p in people:
            if isinstance(p, dict):
                p_norm = _normalize_name(str(p.get("name", "")))
            elif isinstance(p, str):
                p_norm = _normalize_name(p)
            else:
                continue
            if p_norm:
                for t in targets:
                    if t in p_norm:
                        return True

    return False


def _query_person_rows(db, names: list[str]) -> list[GalleryImage]:
    if db is None or not names:
        return []
    is_sqlite = bool(
        hasattr(db, "bind") and db.bind and getattr(db.bind, "dialect", None) and db.bind.dialect.name == "sqlite"
    )
    expanded: list[str] = []
    seen = set()
    for n in names:
        if not n:
            continue
        for v in [n, _strip_accents(n)]:
            v_clean = v.strip()
            if v_clean and v_clean.lower() not in seen:
                seen.add(v_clean.lower())
                expanded.append(v_clean)
        parts = n.strip().split()
        if len(parts) >= 2:
            surname = parts[-1].strip()
            unacc_surname = _strip_accents(surname)
            if len(unacc_surname) >= 5:
                for s in [surname, unacc_surname]:
                    if s and s.lower() not in seen:
                        seen.add(s.lower())
                        expanded.append(s)

    conditions = []
    for variant in expanded:
        pattern = f"%{variant}%"
        conds = [
            GalleryImage.keyword.ilike(pattern),
            cast(GalleryImage.attributes["people"], String).ilike(pattern),
        ]
        if is_sqlite:
            conds.append(cast(GalleryImage.extra_keywords, String).ilike(pattern))
        else:
            conds.append(GalleryImage.extra_keywords.any(variant))
        conditions.extend(conds)
    try:
        return (
            db.query(GalleryImage)
            .filter(GalleryImage.is_deleted.is_(False), or_(*conditions), not_keyword_mismatch())
            .order_by(func.coalesce(GalleryImage.captured_at, GalleryImage.downloaded_at).desc())
            .limit(100)
            .all()
        )
    except Exception as exc:
        logger.debug("_query_person_rows failed: %s", exc)
        try:
            return db.query(GalleryImage).filter(GalleryImage.is_deleted.is_(False), not_keyword_mismatch()).all()
        except Exception:
            return []


def _get_series_aliases(series: str) -> list[str]:
    if not series:
        return []
    try:
        norm = _normalize_series(series)
    except Exception:
        norm = series.strip()
    aliases = {norm, series.strip()}
    norm_lower = norm.lower()
    for alias, canonical in _SERIES_ALIASES.items():
        if canonical.lower() == norm_lower:
            aliases.add(alias)
            aliases.add(canonical)
    return [a for a in aliases if a]


def _query_context_rows(db, series: str) -> list[GalleryImage]:
    if db is None:
        return []
    is_sqlite = bool(
        hasattr(db, "bind") and db.bind and getattr(db.bind, "dialect", None) and db.bind.dialect.name == "sqlite"
    )
    try:
        query = db.query(GalleryImage).filter(GalleryImage.is_deleted.is_(False), not_keyword_mismatch())
        if series:
            series_list = _get_series_aliases(series)
            if is_sqlite:
                conditions = [cast(GalleryImage.attributes, String).ilike(f"%{s}%") for s in series_list]
                query = query.filter(or_(*conditions))
            else:
                conditions = [cast(GalleryImage.attributes["event"]["series"], String).ilike(s) for s in series_list]
                query = query.filter(or_(*conditions))
        return (
            query.order_by(func.coalesce(GalleryImage.captured_at, GalleryImage.downloaded_at).desc())
            .limit(100)
            .all()
        )
    except Exception as exc:
        logger.debug("_query_context_rows failed: %s", exc)
        try:
            return db.query(GalleryImage).filter(GalleryImage.is_deleted.is_(False), not_keyword_mismatch()).all()
        except Exception:
            return []


def gather_candidates(
    db,
    brief: PosterBrief,
    *,
    story_photo_path: str | None = None,
) -> list[PhotoCandidate]:
    """Gather candidate photos for a pro poster from story photo and gallery.

    Story photo comes first (role 'hero' if brief has a hero subject, else 'context').
    Hero candidates reuse speaker_design.pick_speaker_photos, then recent
    GalleryImage rows matching the hero name on people/keyword/extra_keywords (gallery hero up to 12).
    Context candidates (max 10) match the brief event series (and circuit if known) via series aliases,
    preferring action shot types and current/recent year.
    All candidates exclude deleted, blurry, obstructed, or archival photos.
    No duplicates by gallery ID.
    """
    candidates: list[PhotoCandidate] = []
    seen_ids: set[int] = set()

    hero_subject = next((s for s in brief.subjects if s.role == "hero"), None)

    # 1. Story photo first
    if story_photo_path:
        candidates.append(
            PhotoCandidate(
                source="story",
                gallery_image_id=None,
                path_or_url=story_photo_path,
                role="hero" if hero_subject else "context",
                caption=None,
            )
        )

    # 2. Gallery hero candidates (max 12)
    if hero_subject:
        hero_name = hero_subject.name

        # 2a. Reuse speaker_design.pick_speaker_photos (try exact and accent-stripped)
        speaker_rows: list[GalleryImage] = []
        if db is not None:
            names_to_try = [hero_name]
            unacc = _strip_accents(hero_name)
            if unacc and unacc != hero_name:
                names_to_try.append(unacc)
            for n in names_to_try:
                try:
                    try:
                        speaker_rows = speaker_design.pick_speaker_photos(db, n, mood=brief.mood)
                    except TypeError:
                        speaker_rows = speaker_design.pick_speaker_photos(db, n)
                except Exception as exc:
                    logger.debug("pick_speaker_photos error: %s", exc)
                    speaker_rows = []
                if speaker_rows:
                    break

        for row in speaker_rows:
            if len([c for c in candidates if c.role == "hero" and c.source == "gallery"]) >= 12:
                break
            gid = getattr(row, "id", None)
            if gid is not None and gid in seen_ids:
                continue
            if not _is_usable_row(row):
                continue
            if gid is not None:
                seen_ids.add(gid)
            candidates.append(
                PhotoCandidate(
                    source="gallery",
                    gallery_image_id=gid,
                    path_or_url=getattr(row, "local_path", ""),
                    role="hero",
                    caption=gallery_caption(row),
                )
            )

        # 2b. Recent GalleryImage rows matching hero name (up to 12 gallery hero total)
        names = get_name_variants(hero_name) if hero_name else []
        if hero_name and hero_name not in names:
            names.append(hero_name)

        more_hero_rows = _query_person_rows(db, names)
        # Sort by most recent
        more_hero_rows = sorted(
            more_hero_rows,
            key=lambda r: _as_naive_utc(getattr(r, "captured_at", None) or getattr(r, "downloaded_at", None)),
            reverse=True,
        )

        for row in more_hero_rows:
            if len([c for c in candidates if c.role == "hero" and c.source == "gallery"]) >= 12:
                break
            gid = getattr(row, "id", None)
            if gid is not None and gid in seen_ids:
                continue
            if not _is_usable_row(row):
                continue
            if not _matches_person(row, names):
                continue
            if gid is not None:
                seen_ids.add(gid)
            candidates.append(
                PhotoCandidate(
                    source="gallery",
                    gallery_image_id=gid,
                    path_or_url=getattr(row, "local_path", ""),
                    role="hero",
                    caption=gallery_caption(row),
                )
            )

    # 3. Gallery context candidates (max 5)
    context_rows = _query_context_rows(db, brief.event.series)
    matching_context: list[GalleryImage] = []
    brief_series_raw = brief.event.series or ""
    try:
        brief_series_norm = _normalize_series(brief_series_raw).strip().lower() if brief_series_raw else ""
    except Exception:
        brief_series_norm = brief_series_raw.strip().lower()
    brief_circuit = (brief.event.circuit or "").strip().lower() if brief.event.circuit else None

    for row in context_rows:
        gid = getattr(row, "id", None)
        if gid is not None and gid in seen_ids:
            continue
        if not _is_usable_row(row):
            continue
        attrs = getattr(row, "attributes", None) or {}
        if not isinstance(attrs, dict):
            continue
        event_data = attrs.get("event")
        if not isinstance(event_data, dict):
            continue
        row_series_raw = str(event_data.get("series") or "").strip()
        if not row_series_raw:
            continue
        try:
            row_series_norm = _normalize_series(row_series_raw).strip().lower()
        except Exception:
            row_series_norm = row_series_raw.lower()

        if brief_series_norm and row_series_norm != brief_series_norm:
            continue
        if brief_circuit:
            event_name = str(event_data.get("event_name") or "").lower()
            caption = str(attrs.get("caption") or "").lower()
            if brief_circuit not in event_name and brief_circuit not in caption:
                continue
        matching_context.append(row)

    def _context_sort_key(r: GalleryImage):
        attrs = getattr(r, "attributes", None) or {}
        shot = attrs.get("shot_type", "") if isinstance(attrs, dict) else ""
        shot_score = 1 if shot in PREFERRED_SHOT_TYPES else 0

        event_info = attrs.get("event") if isinstance(attrs, dict) and isinstance(attrs.get("event"), dict) else {}
        year = event_info.get("year")
        if not year:
            cap = getattr(r, "captured_at", None)
            if cap and hasattr(cap, "year"):
                year = cap.year
        if not year:
            dl = getattr(r, "downloaded_at", None)
            if dl and hasattr(dl, "year"):
                year = dl.year
        year_val = int(year) if year else 0
        is_brief_year = 1 if (brief.event.year and year_val == brief.event.year) else 0

        when = _as_naive_utc(getattr(r, "captured_at", None) or getattr(r, "downloaded_at", None))
        return (shot_score, is_brief_year, year_val, when)

    sorted_context = sorted(matching_context, key=_context_sort_key, reverse=True)
    for row in sorted_context[:10]:
        gid = getattr(row, "id", None)
        if gid is not None:
            seen_ids.add(gid)
        candidates.append(
            PhotoCandidate(
                source="gallery",
                gallery_image_id=gid,
                path_or_url=getattr(row, "local_path", ""),
                role="context",
                caption=gallery_caption(row),
            )
        )

    return candidates


def _parse_vision_response(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty response")
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            data = json.loads(text[start : end + 1])
        else:
            raise
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    return data


def _normalize_shows(s: str) -> str:
    if not s:
        return ""
    decomposed = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in decomposed if not unicodedata.combining(c))
    table = str.maketrans("", "", string.punctuation)
    return " ".join(s.translate(table).lower().split())


def _similar(a: str, b: str) -> bool:
    na = _normalize_shows(a)
    nb = _normalize_shows(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    wa = set(na.split())
    wb = set(nb.split())
    if not wa or not wb:
        return False
    overlap = len(wa & wb)
    return (overlap / len(wa | wb) > 0.8) or (overlap / max(len(wa), len(wb)) > 0.8)


def _shows_has_surname(shows: str, surname: str | None) -> bool:
    if not shows or not surname:
        return False
    s_norm = _normalize_name(surname)
    if not s_norm:
        return False
    shows_norm = _normalize_shows(shows)
    words = [_normalize_name(w) for w in shows_norm.split()]
    return any(w == s_norm or (len(s_norm) >= 4 and w.startswith(s_norm)) for w in words)


def check_photo(
    candidate: PhotoCandidate,
    brief: PosterBrief,
    *,
    story: str | None = None,
) -> PhotoCheck:
    """Run ONE Gemini Flash vision call on candidate photo to verify person and event.

    Fails closed: any error or invalid response returns person='error', event_match='error', quality=1.
    """
    try:
        path = candidate.path_or_url
        if not path:
            raise ValueError("empty candidate path")
        image_bytes = Path(path).read_bytes()
        if not image_bytes:
            raise ValueError("empty photo bytes")

        hero_subject = next((s for s in brief.subjects if s.role == "hero"), None)
        hero_name = hero_subject.name if hero_subject else "no specific person"

        event = brief.event
        series = event.series or "unknown"
        vehicle = event.vehicle or "unknown"
        circuit = event.circuit or "unknown"
        session = event.session or "unknown"
        year_str = str(event.year) if event.year else "unknown"
        night_str = "yes" if event.night is True else ("no" if event.night is False else "unknown")
        rain_str = "yes" if event.rain is True else ("no" if event.rain is False else "unknown")
        must_avoid = ", ".join(brief.must_avoid) if brief.must_avoid else "none"

        context_lines = [
            f"- Hero subject name: {hero_name}",
            f"- Series: {series}",
            f"- Vehicle: {vehicle}",
            f"- Circuit / Venue: {circuit}",
            f"- Session: {session}",
            f"- Year: {year_str}",
            f"- Night: {night_str}",
            f"- Rain: {rain_str}",
            f"- Must avoid: {must_avoid}",
        ]
        if story:
            context_lines.append(f"- Story: {story[:600]}")
        if getattr(candidate, "caption", None):
            context_lines.append(f"- Photo agency caption (trusted): {candidate.caption}")
        context_block = "\n".join(context_lines)

        prompt = (
            f"Analyze this sports news photo for an editorial poster.\n\n"
            f"Context from brief:\n"
            f"{context_block}\n\n"
            f"Questions to answer:\n"
            f"1. Is the main person in this photo exactly '{hero_name}'? Judge by the face. When the face is hidden by a helmet or visor, answer 'yes' only if the agency caption names {hero_name} AND the helmet, race number and team colours are consistent with him; otherwise 'unsure'. (If the hero subject is 'no specific person', answer 'no' if a person dominates, else 'unsure' or 'yes'.)\n"
            f"2. Does the photo fit THIS event? Required: the same series and the same type of vehicle (and the same team when the hero is a driver or rider). A different circuit, session or round of the same series is fine ('yes') unless the brief says night or rain and the photo clearly contradicts it. A photo from another series or another vehicle type, or from a clearly different era, is 'no'.\n"
            f"3. Is the subject covered by a foreground object or another person? (obstructed: true/false)\n"
            f"4. Is there any watermark or agency logo overlay? (watermark: true/false)\n"
            f"5. Overall photo quality for a poster: 1 to 5 integer\n"
            f"6. Is the hero's face clearly visible and turned toward the camera (front or three-quarter view, eyes visible, not from behind, not hidden by a helmet or visor)? (face_frontal: true/false)\n"
            f"7. In one short line, what does the photo show (who, doing what, where)? (shows: string, max 120 characters)\n"
            f"8. How well does this photo support THIS story as a poster reference, 1 to 5? (5 = the exact moment, place or emotion of the story; 1 = unrelated) (story_fit: integer)\n\n"
            f"Reply with ONLY STRICT JSON in this exact structure, no markdown, no explanation:\n"
            f"{{\n"
            f'  "person": "yes|no|unsure",\n'
            f'  "event_match": "yes|no|unsure",\n'
            f'  "obstructed": bool,\n'
            f'  "watermark": bool,\n'
            f'  "quality": 1-5,\n'
            f'  "face_frontal": bool,\n'
            f'  "shows": "string (max 120 chars)",\n'
            f'  "story_fit": 1-5,\n'
            f'  "notes": "..."\n'
            f"}}"
        )

        data_uri = design_images._vision_datauri(image_bytes)
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]
        raw = design_images._vision_chat(
            content,
            max_tokens=600,
            temperature=0,
            context="pro_poster_photo_check",
        )

        data = _parse_vision_response(raw)

        person = str(data.get("person", "")).strip().lower()
        if person not in ("yes", "no", "unsure"):
            raise ValueError(f"invalid person verdict: {person}")

        event_match = str(data.get("event_match", "")).strip().lower()
        if event_match not in ("yes", "no", "unsure"):
            raise ValueError(f"invalid event_match verdict: {event_match}")

        obstructed_val = data.get("obstructed")
        if isinstance(obstructed_val, bool):
            obstructed = obstructed_val
        elif isinstance(obstructed_val, str) and obstructed_val.lower() in ("true", "false"):
            obstructed = obstructed_val.lower() == "true"
        else:
            raise ValueError("invalid obstructed boolean")

        watermark_val = data.get("watermark")
        if isinstance(watermark_val, bool):
            watermark = watermark_val
        elif isinstance(watermark_val, str) and watermark_val.lower() in ("true", "false"):
            watermark = watermark_val.lower() == "true"
        else:
            raise ValueError("invalid watermark boolean")

        quality_val = data.get("quality")
        try:
            quality = int(quality_val)
            if not (1 <= quality <= 5):
                raise ValueError(f"quality out of range 1-5: {quality}")
        except (TypeError, ValueError):
            raise ValueError(f"invalid quality value: {quality_val}")

        face_frontal_val = data.get("face_frontal")
        face_frontal = (face_frontal_val is True)

        shows_val = data.get("shows")
        if isinstance(shows_val, str):
            shows = shows_val.strip()[:120]
        elif shows_val is not None:
            shows = str(shows_val).strip()[:120]
        else:
            shows = ""

        story_fit_val = data.get("story_fit")
        story_fit = 0
        if story_fit_val is not None and not isinstance(story_fit_val, bool):
            try:
                sf_int = int(story_fit_val)
                story_fit = max(1, min(5, sf_int))
            except (TypeError, ValueError):
                story_fit = 0

        notes = str(data.get("notes", ""))
        return PhotoCheck(
            candidate=candidate,
            person=person,
            event_match=event_match,
            obstructed=obstructed,
            watermark=watermark,
            quality=quality,
            face_frontal=face_frontal,
            shows=shows,
            story_fit=story_fit,
            notes=notes,
        )

    except Exception as exc:
        logger.warning("check_photo failed for candidate %s: %s", getattr(candidate, "path_or_url", ""), exc)
        return PhotoCheck(
            candidate=candidate,
            person="error",
            event_match="error",
            obstructed=False,
            watermark=False,
            quality=1,
            face_frontal=False,
            shows="",
            story_fit=0,
            notes=f"check_error: {exc}",
        )


def select_photos(
    db,
    brief: PosterBrief,
    *,
    story_photo_path: str | None = None,
    story: str | None = None,
) -> PhotoSelection:
    """Select verified hero, context, and portrait photos for pro poster.

    Checks candidates in order (at most 24 checks total).
    Hero is chosen among usable_as_hero with sort key (face_frontal, story_fit, quality) descending.
    If the chosen hero is not face_frontal, portrait is set to the best checked candidate with
    person == 'yes', face_frontal True, not watermark, quality >= 3, and not the hero itself,
    ranked by (story_fit, quality).
    Context is up to 2 usable_as_context that are not the hero or portrait image,
    sorted by (story_fit, quality), skipping near-duplicates of the hero's shows and
    preferring photos that do not show the hero person on equal story_fit.
    A story photo with person yes + event yes but quality 2 may still be used as
    context (not hero or portrait) when nothing better exists.
    face_ref selection (when hero is None) prefers face_frontal: sort key (face_frontal, quality).
    Remaining checked candidates are marked rejected.
    """
    candidates = gather_candidates(db, brief, story_photo_path=story_photo_path)
    checked: list[PhotoCheck] = []
    total_checked = 0
    max_checks = 24

    for cand in candidates:
        if total_checked >= max_checks:
            break

        check = check_photo(cand, brief, story=story)
        checked.append(check)
        total_checked += 1

    usable_heroes = [c for c in checked if c.usable_as_hero]
    hero = (
        max(usable_heroes, key=lambda c: (c.face_frontal, c.story_fit, c.quality))
        if usable_heroes
        else None
    )

    portrait: PhotoCheck | None = None
    if hero is not None and not hero.face_frontal:
        portrait_cands = [
            c
            for c in checked
            if c is not hero
            and c.person == "yes"
            and c.face_frontal is True
            and not c.watermark
            and c.quality >= 3
        ]
        if portrait_cands:
            portrait = max(portrait_cands, key=lambda c: (c.story_fit, c.quality))

    hero_surname = None
    hero_subject = next((s for s in brief.subjects if s.role == "hero"), None)
    if hero_subject:
        parts = hero_subject.name.strip().split()
        if parts:
            hero_surname = parts[-1]

    usable_contexts = [
        c
        for c in checked
        if (hero is None or c is not hero)
        and (portrait is None or c is not portrait)
        and c.usable_as_context
        and (c.candidate.role == "context" or c.candidate.source == "story")
        and not (hero is not None and hero.shows and _similar(c.shows, hero.shows))
    ]

    def _context_rank(c: PhotoCheck) -> tuple[int, int, int]:
        has_surname = _shows_has_surname(c.shows, hero_surname)
        no_surname = 1 if not has_surname else 0
        return (c.story_fit, no_surname, c.quality)

    usable_contexts.sort(key=_context_rank, reverse=True)

    if usable_contexts:
        context_picks = tuple(usable_contexts[:2])
    else:
        fallback_story_context = [
            c
            for c in checked
            if (hero is None or c is not hero)
            and (portrait is None or c is not portrait)
            and c.candidate.source == "story"
            and c.person == "yes"
            and c.event_match == "yes"
            and not c.watermark
            and c.quality == 2
            and not (hero is not None and hero.shows and _similar(c.shows, hero.shows))
        ]
        context_picks = tuple(fallback_story_context[:1]) if fallback_story_context else ()

    face_ref: PhotoCheck | None = None
    if hero is None:
        face_ref_cands = [
            c
            for c in checked
            if c.person == "yes"
            and not c.obstructed
            and not c.watermark
            and c.quality >= 3
            and c.event_match == "no"
        ]
        if face_ref_cands:
            face_ref = max(face_ref_cands, key=lambda c: (c.face_frontal, c.quality))

    rejected = tuple(
        c
        for c in checked
        if (hero is None or c is not hero)
        and (portrait is None or c is not portrait)
        and (c not in context_picks)
        and (face_ref is None or c is not face_ref)
    )

    return PhotoSelection(
        hero=hero,
        context=context_picks,
        rejected=rejected,
        face_ref=face_ref,
        portrait=portrait,
    )
