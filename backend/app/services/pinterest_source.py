"""Mode 5 (Pinterest content source) — turns Pinterest pin candidates into
staged PinterestContentIdea rows (a title + description + a bound
GalleryImage), consumed FIFO by app/tasks/pinterest.py on the fanpage's own
pacing. See image_downloader.py's Mode 5 section for what each Pinterest URL
shape (profile/board/pin) actually returns.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

# A pin's downloaded resolution is whatever the CDN happens to serve at
# "originals" — smaller than a Getty editorial photo, so the usual 300x300
# gallery floor would reject too many genuinely usable pins.
_MIN_SIZE = (400, 400)
_CANDIDATES_PER_TICK = 3


def _existing_pinterest_urls(db) -> set[str]:
    from app.models.gallery import GalleryImage

    return {
        u for (u,) in
        db.query(GalleryImage.source_image_url).filter(GalleryImage.source_engine == "pinterest").all()
    }


def collect_new_candidates(db, fanpage, mode: str, limit: int = _CANDIDATES_PER_TICK):
    """Returns list[(PinCandidate, source_type, source_ref)] not already in
    gallery_images. "both" tries ai_keyword first, falling back to the
    curated PinterestSource rotation only when that yields nothing new —
    mirrors Mode 4's own "both" ordering (discussion.py's _create_one)."""
    from app.services.image_downloader import (
        classify_pinterest_url, fetch_profile_boards, fetch_board_pins, fetch_pin,
        search_pinterest_candidates, _dedup_key,
    )
    from app.services.news_copywriter import generate_pinterest_search_keyword
    from app.config import get_settings

    def _dedup(cands):
        seen = _existing_pinterest_urls(db)
        out = []
        for c in cands:
            key = _dedup_key(c.image_url)
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
            if len(out) >= limit:
                break
        return out

    def _try_ai_keyword():
        try:
            keyword = generate_pinterest_search_keyword(fanpage)
        except Exception as exc:
            logger.warning("Pinterest: AI-keyword generation failed for fanpage %d: %s", fanpage.id, exc)
            return []
        s = get_settings()
        search_url = s.pinterest_search_url_template.format(query=quote(keyword))
        try:
            cands = search_pinterest_candidates(search_url)
        except Exception as exc:
            logger.warning("Pinterest: AI-keyword search failed for %r (fanpage %d): %s", keyword, fanpage.id, exc)
            return []
        return [(c, "ai_keyword", keyword) for c in _dedup(cands)]

    def _try_curated():
        from app.models.pinterest_sources import PinterestSource

        source = (
            db.query(PinterestSource)
            .filter_by(fanpage_id=fanpage.id, is_active=True)
            .order_by(PinterestSource.last_used_at.asc().nullsfirst(), PinterestSource.id.asc())
            .first()
        )
        if not source:
            return []
        try:
            kind = classify_pinterest_url(source.source_url)
            if kind == "pin":
                pin = fetch_pin(source.source_url)
                cands = [pin] if pin else []
            elif kind == "profile":
                cands = []
                for board_url in fetch_profile_boards(source.source_url)[:3]:
                    cands.extend(fetch_board_pins(board_url))
                    if len(cands) >= limit * 2:
                        break
            else:
                cands = fetch_board_pins(source.source_url)
        except Exception as exc:
            logger.warning("Pinterest: curated fetch failed for source %d (%s): %s", source.id, source.source_url, exc)
            return []

        source.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
        source.times_used = (source.times_used or 0) + 1
        db.commit()
        return [(c, "curated", source.source_url) for c in _dedup(cands)]

    if mode == "ai_keyword":
        return _try_ai_keyword()
    if mode == "curated":
        return _try_curated()
    out = _try_ai_keyword()
    if not out:
        out = _try_curated()
    return out


def build_idea_from_candidate(db, fanpage, candidate, source_type: str):
    """Download one candidate pin, verify/identify it via vision, and store
    it as a pending PinterestContentIdea. Returns None (and leaves no trace)
    on any rejection — download failure, usability-gate rejection
    (_fetch_and_store's classify_and_gate_image), a failed description
    fact-check, or an unidentifiable photo."""
    from app.config import get_settings
    from app.models.gallery import GalleryImage
    from app.models.pinterest_content_ideas import PinterestContentIdea
    from app.services.design_images import (
        vision_check_pin_description, vision_identify_pin_subject, vision_has_watermark,
    )
    from app.services.image_downloader import _fetch_and_store

    s = get_settings()
    niche = (fanpage.mode2_gallery_niches or [None])[0] or fanpage.name
    dest_dir = Path(s.storage_base_path) / "gallery" / "pinterest"

    saved = _fetch_and_store(
        [candidate.image_url], dest_dir, 1, _MIN_SIZE, _existing_pinterest_urls(db), "pinterest",
    )
    if not saved:
        return None
    item = saved[0]
    image_bytes = Path(item.local_path).read_bytes()

    # Checked before the description/identify vision call so a watermarked
    # pin is rejected without spending a second vision call on it (see
    # vision_has_watermark's docstring — real batch testing this session
    # found agency watermarks surviving straight through to the published
    # post on the no-crop direct-post fallback).
    if vision_has_watermark(image_bytes):
        Path(item.local_path).unlink(missing_ok=True)
        return None

    custom_prompt = fanpage.pinterest_custom_prompt or ""
    if candidate.description:
        result = vision_check_pin_description(image_bytes, candidate.description, niche, custom_prompt)
        ok, title, description = result["valid"], result["title"], result["description"]
    else:
        result = vision_identify_pin_subject(image_bytes, niche, custom_prompt)
        ok, title, description = result["identified"], result["title"], result["description"]

    if not ok or not title or not description:
        Path(item.local_path).unlink(missing_ok=True)
        return None

    gi = GalleryImage(
        keyword=title.lower()[:128],
        source_image_url=item.source_url,
        local_path=item.local_path,
        public_url=f"{s.storage_base_url.rstrip('/')}/gallery/pinterest/{item.filename}",
        width=item.width,
        height=item.height,
        source_engine="pinterest",
        label=item.label,
    )
    db.add(gi)
    try:
        db.commit()
    except IntegrityError:
        # a concurrent tick claimed this exact pin between our dedup read
        # and this insert — same race handled the same way as
        # design_images.fetch_subject_datauri.
        db.rollback()
        Path(item.local_path).unlink(missing_ok=True)
        return None

    idea = PinterestContentIdea(
        fanpage_id=fanpage.id,
        gallery_image_id=gi.id,
        title=title,
        description=description,
        source_type=source_type,
        status="pending",
    )
    db.add(idea)
    db.commit()
    logger.info(
        "Pinterest: fanpage %d — new idea %d (%s) title=%r",
        fanpage.id, idea.id, source_type, title,
    )
    return idea
