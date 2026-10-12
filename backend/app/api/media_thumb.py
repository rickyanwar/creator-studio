import os
import hashlib
import tempfile
import logging
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse, unquote

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import FileResponse
from PIL import Image

from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/media", tags=["media"])  # nginx strips /api before proxying

ALLOWED_WIDTHS = {240, 360, 480}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _resolve_source_path(u: str, storage_root: Path, base_url: str) -> Path | None:
    # 1. Strip query string or fragments if any
    parsed = urlparse(u)
    # If scheme/netloc exists, check against base_url
    if parsed.scheme and parsed.netloc:
        parsed_base = urlparse(base_url)
        # If hostname does not match base_url hostname, not allowed
        if parsed.netloc != parsed_base.netloc:
            return None
        # Extract path relative to base_url path
        base_path = parsed_base.path.rstrip("/")
        req_path = unquote(parsed.path)
        if base_path and req_path.startswith(base_path):
            rel_str = req_path[len(base_path):].lstrip("/")
        else:
            rel_str = req_path.lstrip("/")
    else:
        req_path = unquote(parsed.path or u)
        # If relative or absolute starts with /media or media
        if req_path.startswith("/media/"):
            rel_str = req_path[len("/media/"):].lstrip("/")
        elif req_path.startswith("media/"):
            rel_str = req_path[len("media/"):].lstrip("/")
        elif req_path.startswith("/"):
            # Check if it starts with storage_root path string
            storage_str = str(storage_root)
            if req_path.startswith(storage_str):
                rel_str = req_path[len(storage_str):].lstrip("/")
            else:
                rel_str = req_path.lstrip("/")
        else:
            rel_str = req_path

    # Construct target path inside storage root
    target_path = (storage_root / rel_str).resolve()
    storage_root_resolved = storage_root.resolve()

    # Strict path-traversal guard
    try:
        target_path.relative_to(storage_root_resolved)
    except ValueError:
        return None

    # Check symlinks or realpath
    real_path = target_path.resolve()
    try:
        real_path.relative_to(storage_root_resolved)
    except ValueError:
        return None

    # Verify extension
    if target_path.suffix.lower() not in ALLOWED_EXTENSIONS:
        return None

    # Verify file exists and is regular file
    if not target_path.is_file():
        return None

    return target_path


@router.get("/thumb")
def get_media_thumb(
    u: str = Query(..., description="Media URL or path"),
    w: int = Query(360, description="Thumbnail width"),
):
    if w not in ALLOWED_WIDTHS:
        raise HTTPException(status_code=422, detail="w must be 240, 360, or 480")
    settings = get_settings()
    storage_root = Path(settings.storage_base_path)
    base_url = settings.storage_base_url or "http://localhost/media"

    source_path = _resolve_source_path(u, storage_root, base_url)
    if not source_path:
        raise HTTPException(status_code=404, detail="Media file not found")

    storage_root_resolved = storage_root.resolve()
    rel_path = str(source_path.relative_to(storage_root_resolved))
    try:
        mtime = str(os.path.getmtime(source_path))
    except OSError:
        raise HTTPException(status_code=404, detail="Media file not found")

    # Cache key: sha1 of relative path + source mtime
    cache_key = hashlib.sha1(f"{rel_path}:{mtime}".encode("utf-8")).hexdigest()
    thumb_dir = storage_root / "thumbs" / str(w)
    cached_thumb_path = thumb_dir / f"{cache_key}.webp"

    if cached_thumb_path.is_file():
        return FileResponse(
            str(cached_thumb_path),
            media_type="image/webp",
            headers={"Cache-Control": "public, max-age=604800"},
        )

    thumb_dir.mkdir(parents=True, exist_ok=True)

    try:
        with Image.open(source_path) as im:
            # Convert color mode if needed (e.g. RGBA / P / CMYK)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.mode else "RGB")

            # Calculate height preserving aspect ratio
            src_w, src_h = im.size
            if src_w <= 0 or src_h <= 0:
                raise HTTPException(status_code=404, detail="Invalid image dimensions")

            target_w = w
            target_h = max(1, round(src_h * (target_w / src_w)))

            im_resized = im.resize((target_w, target_h), Image.Resampling.LANCZOS)

            # Write to temp file then atomic os.replace
            fd, tmp_file = tempfile.mkstemp(dir=str(thumb_dir), suffix=".tmp")
            os.close(fd)
            try:
                im_resized.save(tmp_file, format="WEBP", quality=70)
                os.replace(tmp_file, str(cached_thumb_path))
            finally:
                if os.path.exists(tmp_file):
                    try:
                        os.unlink(tmp_file)
                    except OSError:
                        pass
    except Image.DecompressionBombError as e:
        logger.error("Decompression bomb detected in image %s: %s", source_path, e)
        raise HTTPException(status_code=404, detail="Image too large to decode")
    except Exception as e:
        logger.error("Failed to generate thumbnail for %s: %s", source_path, e)
        raise HTTPException(status_code=404, detail="Failed to process image")

    return FileResponse(
        str(cached_thumb_path),
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=604800"},
    )
