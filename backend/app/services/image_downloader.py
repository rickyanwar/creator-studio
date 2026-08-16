"""Gallery image downloader — collects candidate image URLs via 9Router.

Backend: 9Router's `/v1/web/fetch` (jina-reader provider). For each keyword we
build a search-results URL (default: Getty editorial search), have jina-reader
fetch it as markdown — which gets past bot-walls that block plain HTTP — and
extract the image URLs from that markdown.

The collected URLs are then downloaded, deduped-by-source-URL, Pillow
min-size validated, and vision-gated for design usability (rejects
crowd/stage/logo-only shots, screenshots, unusably blurry/tiny/obstructed
subjects) in one shared code path (`_fetch_and_store`).

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
    label: str | None = None  # vision label: "face" | "action" | "other"


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

    return _fetch_and_store(urls, dest_dir, max_num, min_size, skip_urls, "9router", subject=keyword)


# ─────────────────────────────────────────────────────────────────────────────
# URL collector — 9Router /v1/web/fetch (jina-reader)
# ─────────────────────────────────────────────────────────────────────────────

# Matches image URLs inside the markdown jina-reader returns, e.g.
# ![Image 1: ...](https://media.gettyimages.com/id/123/photo/foo.jpg?s=612x612&...)
_IMG_URL_RE = re.compile(
    r"https?://[^\s)\"'<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^\s)\"'<>]*)?",
    re.IGNORECASE,
)

# Broader fallback for markdown image links whose URL has no recognizable file
# extension — e.g. Google Images' thumbnail proxy
# (`https://encrypted-tbn0.gstatic.com/images?q=tbn:...`) has no .jpg/.png
# suffix for _IMG_URL_RE to match. Captures the URL inside markdown image
# syntax ![...](url) regardless of extension. Only used for sources where
# _IMG_URL_RE comes back empty (see design_images.fetch_topic_datauri).
_MD_IMG_ANY_RE = re.compile(r"!\[[^\]]*\]\((https?://[^\s)\"'<>]+)\)")


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


# Extra search phrase tried only as a top-up when the base keyword search
# doesn't yield enough new images on its own (e.g. "marc marquez" running dry
# on fresh race-action shots) — surfaces different Getty editorial coverage
# (press conferences, paddock, interviews) than the bare name search. Only
# spent when needed, not on every run, since each page is a paid web/fetch call.
_TOPUP_QUERY_SUFFIX = "press"


def _collect_urls_for_phrase(
    phrase: str,
    max_num: int,
    skip_urls: set[str] | frozenset[str],
    seen_keys: set[str],
    pages: int,
) -> list[str]:
    """Collect NEW candidate image URLs for one search phrase by fetching
    search-result pages (newest-first) through 9Router (jina-reader) and
    extracting image URLs from the returned markdown.

    Skips images already downloaded before (`skip_urls`, matched on the stable
    dedup key) and images already collected this run for ANY phrase
    (`seen_keys`, shared/mutated across phrases by the caller). Walks pages
    until enough new URLs are found, a page yields no new images (caught up —
    since results are sorted newest-first), or the page cap is reached.
    Returns full signed URLs (needed to actually download)."""
    settings = get_settings()
    template = settings.gallery_search_url_template
    encoded = quote(phrase)
    stop_after = settings.gallery_stop_after_consecutive_dupes

    collected: list[str] = []
    consecutive_dupes = 0  # run of already-downloaded images (newest-first → older ahead)

    for page in range(1, pages + 1):
        search_url = template.format(query=encoded, page=page)
        try:
            markdown = _9router_fetch_markdown(search_url)
        except Exception as exc:
            logger.warning("Gallery: 9Router fetch failed for %r page %d (%s)", phrase, page, exc)
            break

        page_urls = [m.group(0) for m in _IMG_URL_RE.finditer(markdown)]
        if not page_urls:
            break  # end of results

        page_new = 0
        early_stop = False
        for u in page_urls:
            key = _dedup_key(u)
            if key in seen_keys:
                continue  # same image seen earlier this run (pagination overlap or other phrase)
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
            "Gallery: phrase %r page %d — %d urls, %d new (consecutive already-have=%d)",
            phrase, page, len(page_urls), page_new, consecutive_dupes,
        )

        if early_stop:
            logger.info(
                "Gallery: phrase %r — stopped after %d consecutive already-downloaded images "
                "(newest-first, so older pages are already downloaded)",
                phrase, consecutive_dupes,
            )
            break
        if len(collected) >= max_num:
            break
        if page_new == 0:
            break  # whole page already downloaded → older pages too

    return collected


def _collect_urls_9router(
    keyword: str,
    max_num: int,
    skip_urls: set[str] | frozenset[str] = frozenset(),
    max_pages: int | None = None,
) -> list[str]:
    """Collect NEW candidate image URLs for a keyword, trying the bare keyword
    first and only spending extra web/fetch calls on the "{keyword} press"
    variant if the bare search didn't find enough — see _TOPUP_QUERY_SUFFIX."""
    settings = get_settings()
    pages = max_pages if max_pages and max_pages > 0 else settings.gallery_max_pages
    seen_keys: set[str] = set()  # shared across phrases this run

    collected = _collect_urls_for_phrase(keyword, max_num, skip_urls, seen_keys, pages)

    if len(collected) < max_num and _TOPUP_QUERY_SUFFIX:
        topup_phrase = f"{keyword} {_TOPUP_QUERY_SUFFIX}"
        remaining = max_num - len(collected)
        topup = _collect_urls_for_phrase(topup_phrase, remaining, skip_urls, seen_keys, pages)
        if topup:
            logger.info("Gallery: keyword %r topped up with %d image(s) via %r", keyword, len(topup), topup_phrase)
        collected.extend(topup)

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
    subject: str | None = None,
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

        # Min-size filter applies to the ORIGINAL (pre-upscale) resolution
        if img.width < min_w or img.height < min_h:
            logger.debug("Gallery: skipping %s — %dx%d below min %dx%d", url, img.width, img.height, min_w, min_h)
            continue

        # Upscale small images (e.g. Getty 612px comps). HQ mode (opt-in) uses
        # UltraSharpV2 + GFPGAN for detailed bikes/gear/faces (slow, CPU); else
        # the fast FSRCNN x2 + sharpen.
        final_bytes = resp.content
        _s = get_settings()
        if _s.hq_upscale_enabled:
            from app.services.hq_upscale import enhance_image_bytes
            final_bytes = enhance_image_bytes(resp.content)
        elif _s.gallery_upscale_enabled:
            from app.services.upscaler import upscale_image_bytes
            final_bytes = upscale_image_bytes(resp.content)

        filename = f"{uuid.uuid4().hex}.jpg"
        path = dest / filename
        try:
            final_img = Image.open(io.BytesIO(final_bytes))
            final_img.load()
            final_img.convert("RGB").save(path, format="JPEG", quality=90)
        except Exception as exc:
            logger.warning("Gallery: failed to save %s: %s", url, exc)
            continue

        # Label the photo (face/action/other) AND gate it for actual design
        # usability — one vision call doing both jobs (see
        # design_images.classify_and_gate_image). Rejects the obvious junk a
        # keyword search drags in: crowd/stage/logo-only shots, screenshots,
        # graphics with text overlays, tiny/blurry/mostly-obstructed subjects.
        # Fails open (usable=True) on any vision error — never let a flaky
        # call block a download outright.
        try:
            from app.services.design_images import classify_and_gate_image
            label, usable = classify_and_gate_image(final_bytes, subject=subject)
        except Exception:
            label, usable = None, True

        if not usable:
            logger.info("Gallery: rejecting %s — vision quality gate says not usable for design", url)
            path.unlink(missing_ok=True)
            continue

        saved.append(DownloadedImage(
            source_url=key,  # stable key stored for dedup across future runs
            local_path=str(path),
            filename=filename,
            width=final_img.width,
            height=final_img.height,
            engine=engine,
            label=label,
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
