from sqlalchemy import Column, Integer, String, Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import ARRAY
from app.database import Base


class GalleryKeyword(Base):
    """Download settings for one gallery keyword (e.g. "marc marquez")."""

    __tablename__ = "gallery_keywords"

    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String(128), unique=True, nullable=False)
    niche = Column(String(64), nullable=True, index=True)  # e.g. "F1", "MotoGP", "UFC" — free text
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")
    max_images = Column(Integer, nullable=False, server_default="50")
    max_pages = Column(Integer, nullable=False, server_default="10")  # search-result pages to fetch per run
    min_width = Column(Integer, nullable=False, server_default="200")
    min_height = Column(Integer, nullable=False, server_default="200")
    source_engine = Column(String(16), nullable=False, server_default="bing")  # bing | google | 9router
    license_filter = Column(String(64), nullable=False, server_default="commercial,modify")
    last_downloaded_at = Column(DateTime, nullable=True)
    last_download_error = Column(String(512), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class GalleryImage(Base):
    """One downloaded gallery image. source_image_url is the dedup key."""

    __tablename__ = "gallery_images"

    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String(128), nullable=False, index=True)
    # A photo can feature more than one person (e.g. two riders on a podium) —
    # extra_keywords lets the same image match under other names too, beyond
    # the single primary `keyword` it was downloaded under.
    extra_keywords = Column(ARRAY(String(128)), nullable=False, server_default="{}")
    source_image_url = Column(String(1024), unique=True, nullable=False)
    local_path = Column(String(512), nullable=False)
    public_url = Column(String(512), nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    source_engine = Column(String(16), nullable=False)  # bing | google | 9router
    license_info = Column(String(64), nullable=True)
    # Vision label at download time: "face" | "action" | "other" — lets the
    # designer pick the right kind of photo per news context.
    label = Column(String(16), nullable=True, index=True)
    is_used = Column(Boolean, default=False, nullable=False, server_default="false", index=True)
    downloaded_at = Column(DateTime, server_default=func.now(), nullable=False)
    # Soft delete: the row (and its source_image_url) stays so the same URL is
    # never re-downloaded. local_path/public_url are left as historical
    # references even though the file on disk is removed.
    is_deleted = Column(Boolean, default=False, nullable=False, server_default="false", index=True)
    deleted_at = Column(DateTime, nullable=True)
