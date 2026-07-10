"""Gallery image downloader — collects candidate image URLs via 9Router.

Backend: 9Router's `/v1/web/fetch` (jina-reader provider). For each keyword we
build a search-results URL (default: Getty editorial search), have jina-reader
fetch it as markdown — which gets past bot-walls that block plain HTTP — and
extract the image URLs from that markdown.

The collected URLs are then downloaded, deduped-by-source-URL, and Pillow
min-size validated in one shared code path (`_fetch_and_store`).

Note: search-result previews are often watermarked/licensed thumbnails capped at
~612px on the long side. Ensure the caller's min-size and licensing fit the use.
"""

import io
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
from PIL import Image

from app.config import get_settings

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
    engine: str  # "9router"


def keyword_slug(keyword: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", keyword.lower()).strip("_") or "keyword"


def download_images(
    keyword: str,
    dest_dir: str | Path,
    max_num: int = 50,
    min_size: tuple[int, int] = (500, 500),
    license_filter: str = "commercial,modify",  # kept for API compat; unused by the 9Router backend
    skip_urls: set[str] | frozenset[str] = frozenset(),
    max_pages: int | None = None,
) -> list[DownloadedImage]:
    """Collect image URLs for a keyword via 9Router web-fetch and download the
    ones that pass dedup (skip_urls) and min-size validation."""
    urls = _collect_urls_9router(keyword, max_num * 2, skip_urls, max_pages)

    if not urls:
        logger.info("Gallery: no new image URLs for keyword %r (all already downloaded)", keyword)
        return []

    return _fetch_and_store(urls, dest_dir, max_num, min_size, skip_urls, "9router")


# ─────────────────────────────────────────────────────────────────────────────
# URL collector — 9Router /v1/web/fetch (jina-reader)
# ─────────────────────────────────────────────────────────────────────────────

# Matches image URLs inside the markdown jina-reader returns, e.g.
# ![Image 1: ...](https://media.gettyimages.com/id/123/photo/foo.jpg?s=612x612&...)
_IMG_URL_RE = re.compile(
    r"https?://[^\s)\"'<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^\s)\"'<>]*)?",
    re.IGNORECASE,
)


def _dedup_key(url: str) -> str:
    """Stable per-image dedup key. Getty signs each media URL with a `c=`
    signature that changes every fetch, so dedup on the path without its query
    string — the same photo always maps to the same key across runs."""
    return url.split("?", 1)[0]


def _9router_fetch_markdown(search_url: str) -> str:
    """Fetch a URL through 9Router's jina-reader web-fetch and return its
    markdown text. Raises on transport/HTTP/API error."""
    from app.services.nine_router import get_nine_router_config

    settings = get_settings()
    cfg = get_nine_router_config()
    base = cfg.base_url
    if not base:
        raise RuntimeError("9Router base URL is not configured — cannot fetch gallery images")

    resp = httpx.post(
        f"{base}/web/fetch",
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        },
        json={"url": search_url, "provider": settings.gallery_fetch_provider},
        timeout=60.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"9Router web-fetch error: {data['error']}")
    return (data.get("content") or {}).get("text", "") if isinstance(data, dict) else ""


def _collect_urls_9router(
    keyword: str,
    max_num: int,
    skip_urls: set[str] | frozenset[str] = frozenset(),
    max_pages: int | None = None,
) -> list[str]:
    """Collect NEW candidate image URLs for a keyword by fetching search-result
    pages (newest-first) through 9Router (jina-reader) and extracting image URLs
    from the returned markdown.

    Skips images already downloaded before (`skip_urls`, matched on the stable
    dedup key). Walks pages until enough new URLs are found, a page yields no
    new images (caught up — since results are sorted newest-first), or the page
    cap is reached. Returns full signed URLs (needed to actually download)."""
    settings = get_settings()
    template = settings.gallery_search_url_template
    encoded = quote(keyword)
    pages = max_pages if max_pages and max_pages > 0 else settings.gallery_max_pages
    stop_after = settings.gallery_stop_after_consecutive_dupes

    collected: list[str] = []
    seen_keys: set[str] = set()  # processed this run (guards against a page repeating)
    consecutive_dupes = 0        # run of already-downloaded images (newest-first → older ahead)

    for page in range(1, pages + 1):
        search_url = template.format(query=encoded, page=page)
        try:
            markdown = _9router_fetch_markdown(search_url)
        except Exception as exc:
            logger.warning("Gallery: 9Router fetch failed for %r page %d (%s)", keyword, page, exc)
            break

        page_urls = [m.group(0) for m in _IMG_URL_RE.finditer(markdown)]
        if not page_urls:
            break  # end of results

        page_new = 0
        early_stop = False
        for u in page_urls:
            key = _dedup_key(u)
            if key in seen_keys:
                continue  # same image seen earlier this run (pagination overlap)
            seen_keys.add(key)

            if key in skip_urls:
                consecutive_dupes += 1
                if stop_after and consecutive_dupes >= stop_after:
                    early_stop = True
                    break
                continue

            # genuinely new image
            consecutive_dupes = 0
            collected.append(u)
            page_new += 1

        logger.info(
            "Gallery: keyword %r page %d — %d urls, %d new (consecutive already-have=%d)",
            keyword, page, len(page_urls), page_new, consecutive_dupes,
        )

        if early_stop:
            logger.info(
                "Gallery: keyword %r — stopped after %d consecutive already-downloaded images "
                "(newest-first, so older pages are already downloaded)",
                keyword, consecutive_dupes,
            )
            break
        if len(collected) >= max_num:
            break
        if page_new == 0:
            break  # whole page already downloaded → older pages too

    return collected


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
    seen_keys: set[str] = set()
    for url in urls:
        if len(saved) >= max_num:
            break
        # Dedup on the stable key (query stripped), but download via the full
        # signed URL — Getty rejects the bare URL with HTTP 400.
        key = _dedup_key(url)
        if key in skip_urls or key in seen_keys:
            continue
        seen_keys.add(key)

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
            source_url=key,  # stable key stored for dedup across future runs
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
