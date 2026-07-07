"""Gallery image downloader — abstraction layer with two switchable backends.

Per the Feature 2 spec (docs/spec-feature2-news-to-image.md):
- Primary: google-images-download (tested working in this environment, but the
  upstream repo is archived since Dec 2025 and may break at any time).
- Fallback: icrawler's BingImageCrawler (supports license filters).

Both backends are used only to *collect candidate image URLs*; the actual
download, dedup-by-source-URL, and Pillow min-size (500x500) validation happen
in one shared code path so the guarantees are identical regardless of engine.
"""

import io
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_FETCH_TIMEOUT = 20.0


@dataclass
class DownloadedImage:
    source_url: str
    local_path: str
    filename: str
    width: int
    height: int
    engine: str  # "google" | "bing"


def keyword_slug(keyword: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", keyword.lower()).strip("_") or "keyword"


def download_images(
    keyword: str,
    dest_dir: str | Path,
    max_num: int = 50,
    min_size: tuple[int, int] = (500, 500),
    license_filter: str = "commercial,modify",
    skip_urls: set[str] | frozenset[str] = frozenset(),
) -> list[DownloadedImage]:
    """Collect image URLs for a keyword and download the ones that pass
    dedup (skip_urls) and min-size validation. Primary engine first; any
    failure or empty result falls through to the fallback engine."""
    urls: list[str] = []
    engine = "google"
    try:
        urls = _collect_urls_gid(keyword, max_num * 2)
        if not urls:
            raise RuntimeError("google-images-download returned no URLs")
    except Exception as exc:
        logger.warning("Gallery: google-images-download failed for %r (%s) — falling back to icrawler/Bing", keyword, exc)
        engine = "bing"
        urls = _collect_urls_icrawler(keyword, max_num * 2, license_filter)

    if not urls:
        raise RuntimeError(f"No image URLs found for keyword {keyword!r} on either engine")

    return _fetch_and_store(urls, dest_dir, max_num, min_size, skip_urls, engine)


# ─────────────────────────────────────────────────────────────────────────────
# URL collectors (one per backend)
# ─────────────────────────────────────────────────────────────────────────────

def _collect_urls_gid(keyword: str, max_num: int) -> list[str]:
    """Primary: google-images-download in no_download mode → list of image URLs."""
    from google_images_download import google_images_download

    gid = google_images_download.googleimagesdownload()
    res = gid.download({
        "keywords": keyword,
        "limit": max_num,
        "no_download": True,
        "silent_mode": True,
    })
    if isinstance(res, tuple):  # newer versions return (paths, error_count)
        res = res[0]
    urls = res.get(keyword) or []
    # in no_download mode the "paths" are the source image URLs
    return [u for u in urls if isinstance(u, str) and u.startswith("http")]


def _collect_urls_icrawler(keyword: str, max_num: int, license_filter: str | None) -> list[str]:
    """Fallback: icrawler BingImageCrawler with a downloader that only records
    each task's file_url instead of downloading — the shared path downloads."""
    import tempfile

    from icrawler.builtin import BingImageCrawler
    from icrawler.downloader import ImageDownloader

    captured: list[str] = []

    class _URLCollector(ImageDownloader):
        def download(self, task, default_ext, timeout=5, max_retry=3, **kwargs):
            url = task.get("file_url")
            if url:
                captured.append(url)
            task["success"] = True

    with tempfile.TemporaryDirectory() as tmp:
        crawler = BingImageCrawler(
            downloader_cls=_URLCollector,
            storage={"root_dir": tmp},
            log_level=logging.WARNING,
        )
        filters = {"license": license_filter} if license_filter else None
        crawler.crawl(keyword=keyword, max_num=max_num, filters=filters)

    # preserve order, drop duplicates
    seen: set[str] = set()
    return [u for u in captured if not (u in seen or seen.add(u))]


# ─────────────────────────────────────────────────────────────────────────────
# Shared download + validate + store path
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_and_store(
    urls: list[str],
    dest_dir: str | Path,
    max_num: int,
    min_size: tuple[int, int],
    skip_urls: set[str] | frozenset[str],
    engine: str,
) -> list[DownloadedImage]:
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    min_w, min_h = min_size

    saved: list[DownloadedImage] = []
    for url in urls:
        if len(saved) >= max_num:
            break
        if url in skip_urls:
            continue

        try:
            resp = httpx.get(url, headers={"User-Agent": _UA}, timeout=_FETCH_TIMEOUT, follow_redirects=True)
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content))
            img.load()
        except Exception as exc:
            logger.debug("Gallery: skipping %s (%s)", url, exc)
            continue

        if img.width < min_w or img.height < min_h:
            logger.debug("Gallery: skipping %s — %dx%d below min %dx%d", url, img.width, img.height, min_w, min_h)
            continue

        filename = f"{uuid.uuid4().hex}.jpg"
        path = dest / filename
        try:
            img.convert("RGB").save(path, format="JPEG", quality=90)
        except Exception as exc:
            logger.warning("Gallery: failed to save %s: %s", url, exc)
            continue

        saved.append(DownloadedImage(
            source_url=url,
            local_path=str(path),
            filename=filename,
            width=img.width,
            height=img.height,
            engine=engine,
        ))

    logger.info("Gallery: stored %d/%d candidate images (engine=%s)", len(saved), len(urls), engine)
    return saved


def validate_and_store_upload(file_bytes: bytes, dest_dir: str | Path, min_size: tuple[int, int] = (500, 500)) -> DownloadedImage:
    """Validate a manually uploaded image (same min-size rule) and store it
    like a downloaded one. source_url gets a unique manual: marker so the
    dedup unique-constraint never collides."""
    img = Image.open(io.BytesIO(file_bytes))
    img.load()
    min_w, min_h = min_size
    if img.width < min_w or img.height < min_h:
        raise ValueError(f"Image is {img.width}x{img.height}, below the minimum {min_w}x{min_h}")

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    path = dest / filename
    img.convert("RGB").save(path, format="JPEG", quality=90)

    return DownloadedImage(
        source_url=f"manual:{uuid.uuid4().hex}",
        local_path=str(path),
        filename=filename,
        width=img.width,
        height=img.height,
        engine="manual",
    )
