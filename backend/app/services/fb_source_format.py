from typing import Any

FB_FORMATS = ("follow", "repost", "template", "chatgpt")


def format_of(source: Any) -> str:
    """Return the source format, treating legacy or unknown values as follow."""
    value = getattr(source, "format", None)
    return value if value in FB_FORMATS else "follow"


def format_for_job(db, job) -> str:
    """Resolve the newest matching idea's source format; missing provenance follows."""
    from app.models.facebook_photo_ideas import FacebookPhotoIdea
    from app.models.facebook_photo_sources import FacebookPhotoSource

    if job is None or job.source_gallery_image_id is None:
        return "follow"
    idea = (
        db.query(FacebookPhotoIdea)
        .filter_by(gallery_image_id=job.source_gallery_image_id, fanpage_id=job.fanpage_id)
        .order_by(FacebookPhotoIdea.created_at.desc(), FacebookPhotoIdea.id.desc())
        .first()
    )
    source = db.query(FacebookPhotoSource).filter_by(id=idea.source_id).first() if idea and idea.source_id else None
    return format_of(source)


def queue_override(fmt: str, source: Any) -> str | None:
    """Both ChatGPT designs and custom photo edits require the visual queue."""
    return "chatgpt" if fmt == "chatgpt" else None


def normalize_format(value: str | None) -> str | None:
    """Validate a format and store the inherited default as NULL."""
    if value is None or value == "follow":
        return None
    if value not in FB_FORMATS:
        raise ValueError(f"Invalid format: {value}. Allowed: {', '.join(FB_FORMATS)}")
    return value
