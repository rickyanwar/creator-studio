from sqlalchemy import Column, Integer, String, Boolean, DateTime, func
from app.database import Base


class GalleryKeyword(Base):
    """Download settings for one gallery keyword (e.g. "marc marquez")."""

    __tablename__ = "gallery_keywords"

    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String(128), unique=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")
    max_images = Column(Integer, nullable=False, server_default="50")
    min_width = Column(Integer, nullable=False, server_default="500")
    min_height = Column(Integer, nullable=False, server_default="500")
    source_engine = Column(String(16), nullable=False, server_default="bing")  # bing | google
    license_filter = Column(String(64), nullable=False, server_default="commercial,modify")
    last_downloaded_at = Column(DateTime, nullable=True)
    last_download_error = Column(String(512), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class GalleryImage(Base):
    """One downloaded gallery image. source_image_url is the dedup key."""

    __tablename__ = "gallery_images"

    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String(128), nullable=False, index=True)
    source_image_url = Column(String(1024), unique=True, nullable=False)
    local_path = Column(String(512), nullable=False)
    public_url = Column(String(512), nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    source_engine = Column(String(16), nullable=False)  # bing | google
    license_info = Column(String(64), nullable=True)
    is_used = Column(Boolean, default=False, nullable=False, server_default="false", index=True)
    downloaded_at = Column(DateTime, server_default=func.now(), nullable=False)
