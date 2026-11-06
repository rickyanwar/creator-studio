from sqlalchemy import func
from app.models.gallery import GalleryImage


def not_label_pending():
    return GalleryImage.label_pending.is_(False)


def not_keyword_mismatch():
    """SQL condition: keep rows unless Gemini marked the keyword person as not in the photo."""
    return func.coalesce(GalleryImage.attributes["keyword_mismatch"].as_boolean(), False).is_(False)
