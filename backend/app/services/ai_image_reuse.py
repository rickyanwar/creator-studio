"""Edit once: reuse one AI-made image across a brand, its group lane and same-profile fanpages.

When sibling fanpages (see fb_share.lanes.sibling_fanpage_ids) take the same source item
with the same format and the same edit prompt, the AI image is made once. A finished render
records what it was made from under ``design_analysis_json["ai_image_reuse"]``; a later
sibling job with an identical key copies that file to its own path (cleanup deletes files per
job, so a path is never shared) and skips the engine call. Only the caption differs.

A sibling that is still rendering is never waited for: the job renders normally.

Reuse only happens inside Group Share: one side of the pair must be a group lane whose brand
has Group Share enabled (a Page never reuses another Page's image). Two different brands on
the same Facebook profile must also share the same look (accent colours, template style,
caption language). Every record carries its provenance (``origin_job_id`` / ``origin_path``):
a copy is only valid while the job that made the image still holds that same image and is not
deleted, skipped, failed or back in the render queue (re-edit, render-now, retry).
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.models.publish_jobs import PublishJob, PublishJobStatus

logger = logging.getLogger(__name__)

RECORD_KEY = "ai_image_reuse"
PRO_POSTER_REUSED_FROM_KEY = "pro_poster_reused_from"
# Origin images the owner rejected on this job (re-edit); never reused for it again.
REJECTED_KEY = "ai_image_reuse_rejected"
REJECTED_MAX = 20
NOTE_MAX_LEN = 200  # PublishJob.design_engine_note is String(200)
CANDIDATE_LIMIT = 200
# A sibling in these states has no finished image to give (still rendering, waiting for a
# render, or failed/skipped/deleted — accuracy over availability).
_UNFINISHED_STATUSES = (
    PublishJobStatus.pending_design,
    PublishJobStatus.rendering,
    PublishJobStatus.failed,
    PublishJobStatus.skipped,
)
# Brand look that must match before two different brands share one AI image.
LOOK_FIELDS = ("accent_color", "accent_secondary_color", "template_style", "caption_language")
# Raw files this module writes; only these are ever unlinked when a record is replaced.
_OWN_FILE_MARKERS = ("_ai_source_", "_reused_")


@dataclass(frozen=True)
class ReusableImage:
    job_id: int          # the sibling job whose file is copied
    path: str            # the sibling's AI image file
    record: dict         # the sibling's reuse record (engine, layout, ...)
    origin_job_id: int   # the job that really made the image (job_id, or the job it was copied from)
    origin_path: str     # that job's own file of the image


def prompt_digest(prompt_text: str | None) -> str:
    return hashlib.sha256((prompt_text or "").strip().encode("utf-8")).hexdigest()


def _record(job) -> dict | None:
    data = getattr(job, "design_analysis_json", None) or {}
    record = data.get(RECORD_KEY) if isinstance(data, dict) else None
    return record if isinstance(record, dict) else None


def _candidate_filter(job, source_key: str):
    """Narrow the sibling query by the source column when the source has one."""
    kind = source_key.split(":", 1)[0]
    if kind == "ig" and getattr(job, "post_id", None) is not None:
        return PublishJob.post_id == job.post_id
    if kind == "news" and getattr(job, "source_article_id", None) is not None:
        return PublishJob.source_article_id == job.source_article_id
    return PublishJob.content_type == job.content_type


def _matches(candidate, *, source_key: str, format_key: str, digest: str, qa_required: bool) -> str | None:
    record = _record(candidate)
    if not record:
        return None
    if (record.get("source_key"), record.get("format_key"), record.get("prompt_sha")) != (source_key, format_key, digest):
        return None
    if qa_required and not record.get("qa_checked"):
        return None
    path = record.get("path")
    if not (isinstance(path, str) and path and Path(path).is_file()):
        return None
    return path


def _origin(candidate, record: dict) -> tuple[int, str]:
    origin_id = record.get("origin_job_id")
    origin_path = record.get("origin_path")
    if not isinstance(origin_id, int):
        origin_id = candidate.id
    if not (isinstance(origin_path, str) and origin_path):
        origin_path = record.get("path")
    return origin_id, origin_path


def _origin_still_valid(origin_job, origin_path: str, keys: tuple) -> bool:
    """The job that made the image still stands behind it: not removed or reset, same image."""
    if origin_job is None or origin_job.is_deleted or origin_job.status in _UNFINISHED_STATUSES:
        return False
    record = _record(origin_job)
    if not record:
        return False
    if (record.get("source_key"), record.get("format_key"), record.get("prompt_sha")) != keys:
        return False
    return _origin(origin_job, record) == (origin_job.id, origin_path)


def find_reusable(db, job, *, source_key: str, format_key: str, prompt_text: str | None,
                  qa_required: bool = False) -> ReusableImage | None:
    """A finished AI image of a sibling job with the same source, format and prompt, or None.

    ``qa_required``: the job's own checks are on, so only an image that passed them is reused.
    Never reuses an image this job made itself (a reset job must get a new image), nor a copy
    whose origin job was removed, reset or re-rendered.
    Never raises; any lookup error means "render normally".
    """
    if not source_key:
        return None
    try:
        siblings = reuse_sibling_ids(db, job.fanpage_id)
        if not siblings:
            return None
        digest = prompt_digest(prompt_text)
        keys = (source_key, format_key, digest)
        data = getattr(job, "design_analysis_json", None)
        rejected = set(data.get(REJECTED_KEY) or ()) if isinstance(data, dict) else set()
        candidates = (
            db.query(PublishJob)
            .filter(
                PublishJob.fanpage_id.in_(siblings),
                PublishJob.id != job.id,
                PublishJob.status.notin_(_UNFINISHED_STATUSES),
                PublishJob.is_deleted.is_(False),
                PublishJob.design_analysis_json.isnot(None),
                _candidate_filter(job, source_key),
            )
            .order_by(PublishJob.id.desc())
            .limit(CANDIDATE_LIMIT)
            .all()
        )
        matches = []
        for candidate in candidates:
            path = _matches(candidate, source_key=source_key, format_key=format_key,
                            digest=digest, qa_required=qa_required)
            if not path:
                continue
            origin_id, origin_path = _origin(candidate, _record(candidate))
            if origin_id == job.id or origin_path in rejected:
                continue
            matches.append((candidate, path, origin_id, origin_path))
        foreign = {origin_id for candidate, _, origin_id, _ in matches if origin_id != candidate.id}
        origins = {row.id: row for row in db.query(PublishJob).filter(PublishJob.id.in_(foreign)).all()} if foreign else {}
        for candidate, path, origin_id, origin_path in matches:
            origin_job = candidate if origin_id == candidate.id else origins.get(origin_id)
            if not _origin_still_valid(origin_job, origin_path, keys):
                continue
            return ReusableImage(job_id=candidate.id, path=path, record=dict(_record(candidate)),
                                 origin_job_id=origin_id, origin_path=origin_path)
    except Exception as exc:
        logger.warning("AI image reuse lookup failed for job %s: %s", getattr(job, "id", None), exc)
    return None


def _designs_dir() -> Path:
    designs = Path(get_settings().storage_base_path) / "designs"
    designs.mkdir(parents=True, exist_ok=True)
    return designs


def copy_reused(path: str, job) -> str:
    """Copy a sibling's AI image to a new file owned by ``job`` and return its path."""
    suffix = Path(path).suffix or ".png"
    target = _designs_dir() / f"job_{job.id}_reused_{uuid.uuid4().hex[:8]}{suffix}"
    shutil.copyfile(path, target)
    return str(target)


def save_ai_image(job, image: bytes) -> str:
    """Keep the raw AI image of a job whose final card is drawn over it (Pro poster)."""
    ext = ".png" if image.startswith(b"\x89PNG") else ".jpg"
    target = _designs_dir() / f"job_{job.id}_ai_source_{uuid.uuid4().hex[:8]}{ext}"
    target.write_bytes(image)
    return str(target)


def _look(fanpage) -> tuple | None:
    return tuple(getattr(fanpage, field, None) for field in LOOK_FIELDS) if fanpage is not None else None


def reuse_sibling_ids(db, fanpage_id: int) -> set[int]:
    """Sibling fanpages that may share an AI image with ``fanpage_id`` (Group Share only).

    A pair qualifies when one side is a group lane whose brand has Group Share enabled; two
    Pages never share. A pair of different brands (same Facebook profile) also needs the same
    brand look; a brand and its own lane need no look check (the lane look is synced).
    """
    from app.models.fb_group_share import FbShareLane
    from app.models.target_fanpages import TargetFanpage
    from app.services.fb_share.brand_settings import get_brand_share
    from app.services.fb_share.lanes import sibling_fanpage_ids

    siblings = sibling_fanpage_ids(db, fanpage_id) - {fanpage_id}
    if not siblings:
        return set()
    lane_brand = dict(db.query(FbShareLane.lane_fanpage_id, FbShareLane.brand_fanpage_id).filter(
        FbShareLane.lane_fanpage_id.in_(siblings | {fanpage_id}),
    ).all())
    enabled: dict[int, bool] = {}
    looks: dict[int, tuple | None] = {}

    def enabled_lane(fid: int) -> bool:
        brand_id = lane_brand.get(fid)
        if brand_id is None:
            return False
        if brand_id not in enabled:
            enabled[brand_id] = bool(get_brand_share(db, brand_id)[0])
        return enabled[brand_id]

    def look(brand_id: int) -> tuple | None:
        if brand_id not in looks:
            looks[brand_id] = _look(db.get(TargetFanpage, brand_id))
        return looks[brand_id]

    own_brand = lane_brand.get(fanpage_id, fanpage_id)
    allowed = set()
    for sibling in siblings:
        if not (enabled_lane(fanpage_id) or enabled_lane(sibling)):
            continue
        sibling_brand = lane_brand.get(sibling, sibling)
        if sibling_brand != own_brand and (look(own_brand) is None or look(own_brand) != look(sibling_brand)):
            continue
        allowed.add(sibling)
    return allowed


def has_siblings(db, fanpage_id: int) -> bool:
    """True when some sibling may reuse this fanpage's AI images (so the raw file is worth keeping)."""
    try:
        return bool(reuse_sibling_ids(db, fanpage_id))
    except Exception as exc:
        logger.warning("AI image reuse sibling check failed for fanpage %s: %s", fanpage_id, exc)
        return False


def _unlink_own_file(job, keep: str | None = None) -> None:
    """Delete the job's previous raw reuse file (save_ai_image / copy_reused), never the design."""
    path = owned_reuse_file(job)
    if not path or path == keep or not any(marker in Path(path).name for marker in _OWN_FILE_MARKERS):
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("AI image reuse: could not delete old file %s of job %s: %s", path, getattr(job, "id", None), exc)


def remember(job, path: str, *, source_key: str, format_key: str, prompt_text: str | None,
             engine: str | None, qa_checked: bool, reused_from: ReusableImage | None = None, **extra) -> None:
    """Record what ``job``'s AI image at ``path`` was made from (no commit).

    ``reused_from``: the sibling image this one was copied from; the record then keeps that
    image's origin, else the job itself is the origin.
    """
    if not source_key:
        return
    _unlink_own_file(job, keep=path)
    record = {
        "source_key": source_key,
        "format_key": format_key,
        "prompt_sha": prompt_digest(prompt_text),
        "path": path,
        "engine": engine,
        "qa_checked": bool(qa_checked),
        "origin_job_id": reused_from.origin_job_id if reused_from else job.id,
        "origin_path": reused_from.origin_path if reused_from else path,
        **extra,
    }
    job.design_analysis_json = {**(getattr(job, "design_analysis_json", None) or {}), RECORD_KEY: record}


def forget(job, *, reject: bool = False) -> None:
    """Drop the job's reuse record (re-edit, render-now): its image may no longer be given to siblings.

    ``reject``: the owner rejected the image (re-edit), so its origin image is also never
    reused for this job again (a copy of it may still sit on a sibling). Also deletes the job's
    own raw reuse file (never the design file). No commit.
    """
    data = getattr(job, "design_analysis_json", None)
    if not isinstance(data, dict) or not ({RECORD_KEY, PRO_POSTER_REUSED_FROM_KEY} & data.keys()):
        return
    record = _record(job)
    _unlink_own_file(job)
    cleaned = {key: value for key, value in data.items() if key not in (RECORD_KEY, PRO_POSTER_REUSED_FROM_KEY)}
    if reject and record:
        _, origin_path = _origin(job, record)
        if origin_path:
            rejected = [p for p in (data.get(REJECTED_KEY) or []) if p != origin_path] + [origin_path]
            cleaned[REJECTED_KEY] = rejected[-REJECTED_MAX:]
    job.design_analysis_json = cleaned


def reused_note(note: str | None, source_job_id: int) -> str:
    text = f"reused AI image from job {source_job_id}"
    return (f"{note}; {text}" if note else text)[:NOTE_MAX_LEN]


def ig_source_key(post) -> str | None:
    post_id = getattr(post, "id", None)
    return f"ig:{post_id}" if post_id is not None else None


def news_source_key(article_id: int) -> str:
    return f"news:{article_id}"


def facebook_source_key(db, job) -> str | None:
    """The Facebook photo's fbid, read off the job's seen-marker gallery row.

    The marker keyword is ``facebook_photo:<fanpage_id>:<fbid>``; the fbid is the same for
    every fanpage that took the photo, the gallery row is not. Never raises; no key means no reuse.
    """
    if getattr(job, "source_gallery_image_id", None) is None:
        return None
    from app.models.gallery import GalleryImage

    try:
        marker = db.query(GalleryImage).filter_by(id=job.source_gallery_image_id).first()
    except Exception as exc:
        logger.warning("AI image reuse: no Facebook photo key for job %s: %s", getattr(job, "id", None), exc)
        return None
    keyword = getattr(marker, "keyword", None)
    parts = (keyword if isinstance(keyword, str) else "").split(":")
    if len(parts) != 3 or parts[0] != "facebook_photo" or not parts[2]:
        return None
    return f"fb:{parts[2]}"


def owned_reuse_file(job) -> str | None:
    """The job's own raw reuse file that cleanup must delete with its design, or None.

    Only a file this job made (``job_<id>_...`` from save_ai_image / copy_reused) that is not the
    design file itself (photo edits record the design path). Another job's file is never
    returned: copies are per job, so a sibling's record never points here.
    """
    record = _record(job)
    path = record.get("path") if record else None
    job_id = getattr(job, "id", None)
    if not (isinstance(path, str) and path and isinstance(job_id, int)):
        return None
    if path == getattr(job, "design_image_path", None):
        return None
    if not Path(path).name.startswith(f"job_{job_id}_"):
        return None
    return path
