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
from datetime import date
from pathlib import Path
from urllib.parse import quote

import httpx
from PIL import Image

from app.config import get_settings

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_FETCH_TIMEOUT = 20.0

# Hard wall-clock budget for one _fetch_and_store call (2026-09-02). Found
# via a real production keyword ("franco colapinto") whose available photo
# pool happened to skew almost entirely toward crowd/press-conference/parade
# shots — every one correctly rejected by classify_and_gate_image, but at
# ~5-10s per candidate (download + vision gate) that meant grinding through
# up to `max_num * 2` URLs (up to a few hundred) with a near-0% hit rate,
# tying up a worker slot for 45+ minutes on a single keyword. Nothing here
# was actually broken — just unbounded — so this caps it: past this budget,
# stop searching and return whatever was found so far (same code path as
# normally running out of URLs early), rather than exhausting the full list.
# The keyword's next scheduled run picks up wherever dedup (skip_urls)
# leaves off, so a capped run never loses images, only defers them.
_FETCH_LOOP_TIME_BUDGET_SECONDS = 900  # 15 minutes


@dataclass
class DownloadedImage:
    source_url: str
    local_path: str
    filename: str
    width: int
    height: int
    engine: str  # "9router"
    label: str | None = None  # vision label: "face" | "action" | "other"
    captured_at: date | None = None  # shot date parsed from the Getty caption, if found


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
    allow_topup: bool = True,
) -> list[DownloadedImage]:
    """Collect image URLs for a keyword via 9Router web-fetch and download the
    ones that pass dedup (skip_urls) and min-size validation.

    `allow_topup=False` skips the "{keyword} press" top-up phrase even if the
    bare search underdelivers — a second paid web/fetch call that only pays
    off for a keyword worth building a deep archive for. The scheduled
    downloader passes False for quiet-tier keywords (see
    gallery_downloader.download_keyword); an explicit "Download Now" leaves
    the default True."""
    urls, captured_dates = _collect_urls_9router(keyword, max_num * 2, skip_urls, max_pages, allow_topup)

    if not urls:
        logger.info("Gallery: no new image URLs for keyword %r (all already downloaded)", keyword)
        return []

    return _fetch_and_store(
        urls, dest_dir, max_num, min_size, skip_urls, "9router", subject=keyword, captured_dates=captured_dates,
    )


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

# Same shape as _IMG_URL_RE but also captures the alt-text caption, e.g.
# ![Marc Marquez ... at Silverstone Circuit on August 09, 2026 in
# Northampton, England.](https://media.gettyimages.com/id/.../photo/foo.jpg?...)
# — Getty editorial captions consistently dateline the shot this way, so the
# capture date can be read straight out of text we already paid to fetch, at
# zero extra web/fetch cost. Only used by the gallery-keyword download path
# (_collect_urls_for_phrase); the shared _IMG_URL_RE stays untouched for
# design_images.py's single-image fetch, which doesn't need the date.
_CAPTION_IMG_RE = re.compile(
    r"!\[([^\]]*)\]\((https?://[^\s)\"'<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^\s)\"'<>]*)?)\)",
    re.IGNORECASE,
)

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_CAPTION_DATE_RE = re.compile(
    r"\bon\s+(" + "|".join(_MONTH_NAMES) + r")\s+(\d{1,2}),?\s+(\d{4})\b",
    re.IGNORECASE,
)


def _parse_caption_date(caption: str) -> date | None:
    """Best-effort shot date from a Getty editorial caption. Returns None for
    captions that don't dateline this way (e.g. non-Getty sources) — callers
    treat a missing date as "unknown", never as an error."""
    m = _CAPTION_DATE_RE.search(caption)
    if not m:
        return None
    month = _MONTH_NAMES[m.group(1).lower()]
    try:
        return date(int(m.group(3)), month, int(m.group(2)))
    except ValueError:
        return None


def _dedup_key(url: str) -> str:
    """Stable per-image dedup key. Getty signs each media URL with a `c=`
    signature that changes every fetch, so dedup on the path without its query
    string — the same photo always maps to the same key across runs."""
    return url.split("?", 1)[0]


def _log_fetch_event(
    context: str, keyword: str | None, niche: str | None, url: str,
    error_message: str | None, latency_ms: int,
) -> None:
    """Fire-and-forget log of one paid web/fetch call — see
    app.models.gallery_fetch_events. Swallows its own failures (a logging
    hiccup must never break the actual fetch it's observing) and opens its
    own short-lived session rather than threading `db` through every caller
    of _9router_fetch_markdown, several of which don't have one in scope."""
    try:
        from app.database import SessionLocal
        from app.models.gallery_fetch_events import GalleryFetchEvent

        db = SessionLocal()
        try:
            db.add(GalleryFetchEvent(
                context=context, keyword=keyword, niche=niche, url=url[:1024],
                success=error_message is None, error_message=error_message, latency_ms=latency_ms,
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.debug("Gallery: fetch-event logging failed (non-fatal)", exc_info=True)


def _9router_fetch_markdown(
    search_url: str, context: str = "unknown", keyword: str | None = None, niche: str | None = None,
) -> str:
    """Fetch a URL through 9Router's jina-reader web-fetch and return its
    markdown text. Raises on transport/HTTP/API error.

    `context`/`keyword`/`niche` are purely for gallery_fetch_events logging
    (see _log_fetch_event) — they don't affect the fetch itself. Every
    caller should pass a `context` identifying what spent this call
    (gallery search, editorial fact-check, event date/time detection, ...)."""
    import time

    from app.services.nine_router import get_nine_router_config

    settings = get_settings()
    cfg = get_nine_router_config()
    base = cfg.base_url
    if not base:
        raise RuntimeError("9Router base URL is not configured — cannot fetch gallery images")

    started = time.monotonic()
    error_message: str | None = None
    try:
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
    except Exception as exc:
        error_message = str(exc)[:512]
        raise
    finally:
        latency_ms = int((time.monotonic() - started) * 1000)
        _log_fetch_event(context, keyword, niche, search_url, error_message, latency_ms)


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
    captured_dates: dict[str, date],
) -> list[str]:
    """Collect NEW candidate image URLs for one search phrase by fetching
    search-result pages (newest-first) through 9Router (jina-reader) and
    extracting image URLs from the returned markdown.

    Skips images already downloaded before (`skip_urls`, matched on the stable
    dedup key) and images already collected this run for ANY phrase
    (`seen_keys`, shared/mutated across phrases by the caller). Walks pages
    until enough new URLs are found, a page yields no new images (caught up —
    since results are sorted newest-first), or the page cap is reached.
    Returns full signed URLs (needed to actually download).

    `captured_dates` is shared/mutated across phrases like `seen_keys`: each
    newly collected URL's dedup key is mapped to its shot date, parsed for
    free from the Getty caption already sitting in this same markdown (see
    _parse_caption_date) — no extra web/fetch spent to learn it."""
    settings = get_settings()
    template = settings.gallery_search_url_template
    encoded = quote(phrase)
    stop_after = settings.gallery_stop_after_consecutive_dupes

    collected: list[str] = []
    consecutive_dupes = 0  # run of already-downloaded images (newest-first → older ahead)

    for page in range(1, pages + 1):
        search_url = template.format(query=encoded, page=page)
        try:
            markdown = _9router_fetch_markdown(search_url, context="gallery", keyword=phrase)
        except Exception as exc:
            logger.warning("Gallery: 9Router fetch failed for %r page %d (%s)", phrase, page, exc)
            break

        page_matches = list(_CAPTION_IMG_RE.finditer(markdown))
        if not page_matches:
            break  # end of results

        page_new = 0
        early_stop = False
        for m in page_matches:
            caption, u = m.group(1), m.group(2)
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
            found_date = _parse_caption_date(caption)
            if found_date:
                captured_dates[key] = found_date

        logger.info(
            "Gallery: phrase %r page %d — %d urls, %d new (consecutive already-have=%d)",
            phrase, page, len(page_matches), page_new, consecutive_dupes,
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
    allow_topup: bool = True,
) -> tuple[list[str], dict[str, date]]:
    """Collect NEW candidate image URLs for a keyword, trying the bare keyword
    first and only spending extra web/fetch calls on the "{keyword} press"
    variant if the bare search didn't find enough — see _TOPUP_QUERY_SUFFIX.
    `allow_topup=False` skips that second call outright.

    Returns (urls, captured_dates) — captured_dates maps each URL's dedup key
    to its parsed Getty shot date where one was found (see
    _collect_urls_for_phrase); a key absent from the dict just means no date
    could be parsed, not that lookup failed."""
    settings = get_settings()
    pages = max_pages if max_pages and max_pages > 0 else settings.gallery_max_pages
    seen_keys: set[str] = set()  # shared across phrases this run
    captured_dates: dict[str, date] = {}

    collected = _collect_urls_for_phrase(keyword, max_num, skip_urls, seen_keys, pages, captured_dates)

    if allow_topup and len(collected) < max_num and _TOPUP_QUERY_SUFFIX:
        topup_phrase = f"{keyword} {_TOPUP_QUERY_SUFFIX}"
        remaining = max_num - len(collected)
        topup = _collect_urls_for_phrase(topup_phrase, remaining, skip_urls, seen_keys, pages, captured_dates)
        if topup:
            logger.info("Gallery: keyword %r topped up with %d image(s) via %r", keyword, len(topup), topup_phrase)
        collected.extend(topup)

    return collected, captured_dates


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
    captured_dates: dict[str, date] | None = None,
) -> list[DownloadedImage]:
    import time

    captured_dates = captured_dates or {}
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    min_w, min_h = min_size

    saved: list[DownloadedImage] = []
    seen_keys: set[str] = set()
    loop_start = time.monotonic()
    for url in urls:
        if len(saved) >= max_num:
            break
        if time.monotonic() - loop_start > _FETCH_LOOP_TIME_BUDGET_SECONDS:
            logger.warning(
                "Gallery: hit %ds time budget for %r after %d/%d candidates (%d saved) — "
                "stopping early, remaining URLs deferred to the next scheduled run",
                _FETCH_LOOP_TIME_BUDGET_SECONDS, subject, len(seen_keys), len(urls), len(saved),
            )
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
            captured_at=captured_dates.get(key),
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


# ── Mode 5: Pinterest content source ──────────────────────────────────────
#
# Three live probes (a board fetch, a search-results fetch, a profile fetch)
# confirmed: neither a board grid nor a search-results grid ever exposes a
# per-pin permalink or description through _9router_fetch_markdown —
# Pinterest's grid is JS-routed, so jina-reader's markdown has no anchor
# around each thumbnail, just `![Image N: Pin](https://i.pinimg.com/...)`
# with placeholder alt text. A profile URL is different: it lists that
# user's boards as real anchor-wrapped links with real titles/pin-counts,
# e.g. `[![img] ![img] ## Ferrari - Mistrzowskie zespoły F1 , 550 Pins ,
# 7y](https://.../adamgawliczek3/ferrari-mistrzowskie-zespoly-f1/)`. A single
# pin permalink page's own markdown was never reached by crawling (nothing
# links to one) — its description shape below is a best-effort heuristic,
# unconfirmed against a live fetch; if it never captures anything in
# practice, callers already treat a missing description the same as an
# absent one (fall through to vision-identify).

_PIN_IMG_RE = re.compile(r"!\[[^\]]*\]\((https://i\.pinimg\.com/[^)\s]+)\)")
_PROFILE_BOARD_RE = re.compile(r"##[^\]]*\bPins\b[^\]]*\]\((https://[^)\s]+pinterest\.com/[^)\s]+)\)")
_PIN_URL_RE = re.compile(r"pinterest\.com/pin/\d+/?")
_PROFILE_URL_RE = re.compile(r"pinterest\.com/[^/]+/?$")


@dataclass
class PinCandidate:
    image_url: str
    description: str | None = None


def _upgrade_pin_image_url(url: str) -> str:
    """Pinterest's CDN serves the same image at multiple sizes under
    different path segments (e.g. `236x`, `736x`, `originals`) — swap the
    thumbnail segment for the largest available so downstream min-size
    filtering isn't fighting a deliberately small preview."""
    return re.sub(r"/\d+x(?:_[A-Z0-9]+)?/", "/originals/", url, count=1)


def classify_pinterest_url(url: str) -> str:
    """"pin" | "profile" | "board", by URL shape alone (no fetch needed) —
    a pin permalink (`/pin/<id>/`), a bare profile (`/<username>/`, no
    further path segment), or anything else (a board, `/<username>/<slug>/`)."""
    if _PIN_URL_RE.search(url):
        return "pin"
    if _PROFILE_URL_RE.search(url):
        return "profile"
    return "board"


def fetch_profile_boards(url: str) -> list[str]:
    """Fetch a Pinterest profile page and return every board URL it lists."""
    markdown = _9router_fetch_markdown(url, context="pinterest_profile")
    return list(dict.fromkeys(m.group(1) for m in _PROFILE_BOARD_RE.finditer(markdown)))


def fetch_board_pins(url: str, limit: int = 10) -> list[PinCandidate]:
    """Fetch a Pinterest board (or search-results) page and return its pin
    thumbnails. Grid pages never carry real per-pin descriptions (confirmed
    live) — every candidate comes back with description=None."""
    markdown = _9router_fetch_markdown(url, context="pinterest_board")
    seen: set[str] = set()
    out: list[PinCandidate] = []
    for m in _PIN_IMG_RE.finditer(markdown):
        img_url = _upgrade_pin_image_url(m.group(1))
        key = _dedup_key(img_url)
        if key in seen:
            continue
        seen.add(key)
        out.append(PinCandidate(image_url=img_url))
        if len(out) >= limit:
            break
    return out


def search_pinterest_candidates(search_url: str, limit: int = 10) -> list[PinCandidate]:
    """AI-keyword path: same grid shape as a board, just a search-results URL."""
    return fetch_board_pins(search_url, limit=limit)


def fetch_pin(url: str) -> PinCandidate | None:
    """Fetch a single Pinterest pin permalink page. Returns its main photo,
    plus a description IF the page markdown carries a plausible plain-text
    caption line (not a nav/boilerplate string, not a link/image/heading) —
    best-effort, see module note above. None if no image was found at all."""
    markdown = _9router_fetch_markdown(url, context="pinterest_pin")
    m = _PIN_IMG_RE.search(markdown)
    if not m:
        return None
    image_url = _upgrade_pin_image_url(m.group(1))

    description = None
    _BOILERPLATE = {"log in", "sign up", "explore", "search"}
    for line in markdown.splitlines():
        text = line.strip()
        if not (15 <= len(text) <= 300):
            continue
        if text.startswith(("#", "[", "!", "http")) or "](" in text:
            continue
        if text.lower() in _BOILERPLATE:
            continue
        description = text
        break

    return PinCandidate(image_url=image_url, description=description)
