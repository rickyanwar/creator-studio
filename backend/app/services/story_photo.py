import io
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image
from scrapling.fetchers import Fetcher

from app.models.scraped_articles import ScrapedArticle
from app.services.design_images import vision_has_watermark
from app.services.news_scraper import (
    USER_AGENT,
    _BLOCK_STATUSES,
    _POOL_TRIES,
    _proxy_host,
    _robots_allowed,
)
from app.services.proxy_pool import pick_proxy
from app.services.relay_pool import pick_relay

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = 15.0
_MAX_BYTES = 15 * 1024 * 1024  # 15 MB cap
_MIN_SIDE = 500
_MAX_REDIRECTS = 3


def _is_safe_url(url: str) -> bool:
    """SSRF guard: http/https only; reject private/loopback/link-local/metadata IPs."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        if hostname.lower() == "localhost":
            return False

        addr_info = socket.getaddrinfo(hostname, None)
        for entry in addr_info:
            ip_str = entry[4][0]
            ip = ipaddress.ip_address(ip_str)
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
                return False
        return True
    except Exception as exc:
        logger.info("SSRF validation failed for %s: %s", url, exc)
        return False


def _is_html_or_challenge(content_type: str, body: bytes) -> bool:
    ct = content_type.lower()
    if "text/html" in ct or "application/xhtml" in ct:
        return True
    head = body[:1024].lower()
    if b"<html" in head or b"<!doctype html" in head or b"<head" in head:
        return True
    return False


def _fetch_plain(url: str, proxy: str | None = None) -> tuple[bytes | None, str]:
    """Plain download using httpx streaming. Follows up to 3 redirects with SSRF check per hop."""
    headers = {"User-Agent": USER_AGENT}
    current_url = url
    redirect_count = 0

    try:
        kwargs = {
            "headers": headers,
            "timeout": _FETCH_TIMEOUT,
            "follow_redirects": False,
        }
        if proxy:
            kwargs["proxy"] = proxy

        with httpx.Client(**kwargs) as client:
            while True:
                with client.stream("GET", current_url) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        redirect_count += 1
                        if redirect_count > _MAX_REDIRECTS:
                            logger.info("Plain image fetch rejected: exceeded max %d redirects (%s)", _MAX_REDIRECTS, url)
                            return None, ""

                        loc = resp.headers.get("location")
                        if not loc:
                            return None, ""

                        next_url = urljoin(current_url, loc)
                        if not _is_safe_url(next_url):
                            logger.info("Plain image fetch rejected: unsafe redirect hop to %s", next_url)
                            return None, ""

                        current_url = next_url
                        continue

                    if resp.status_code in _BLOCK_STATUSES:
                        return None, ""
                    resp.raise_for_status()

                    ctype = resp.headers.get("content-type", "")
                    data = bytearray()
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        data.extend(chunk)
                        if len(data) > _MAX_BYTES:
                            logger.info("Story photo rejected: size exceeds 15 MB cap while streaming (%s)", current_url)
                            return None, ""

                    return bytes(data), ctype
    except Exception as exc:
        logger.info("Plain image fetch failed for %s (proxy=%s): %s", url, _proxy_host(proxy), exc)
        return None, ""


def _fetch_scrapling_bytes(url: str, proxy: str | None = None) -> tuple[bytes | None, str]:
    """Fallback fetch via Scrapling Fetcher."""
    try:
        page = Fetcher.get(url, timeout=_FETCH_TIMEOUT, proxy=proxy)
        if page.status in _BLOCK_STATUSES:
            return None, ""

        final_url = getattr(page, "url", None)
        if final_url and final_url != url and not _is_safe_url(final_url):
            logger.info("Scrapling image fetch rejected: unsafe final url %s", final_url)
            return None, ""

        raw_body = getattr(page, "body", None)
        if raw_body is None:
            raw_body = getattr(page, "html_content", b"")
        if isinstance(raw_body, str):
            raw_body = raw_body.encode("utf-8", errors="replace")

        if len(raw_body) > _MAX_BYTES:
            return None, ""

        headers = getattr(page, "headers", {}) or {}
        ctype = headers.get("content-type", "") or ""
        return raw_body, ctype
    except Exception as exc:
        logger.info("Scrapling image fetch failed for %s (proxy=%s): %s", url, _proxy_host(proxy), exc)
        return None, ""


def _fetch_bytes_with_fallbacks(url: str) -> tuple[bytes | None, str]:
    """Fetch image bytes through the news scraper tier order."""
    # Tier 1: Plain direct httpx
    body, ctype = _fetch_plain(url, proxy=None)
    if body and not _is_html_or_challenge(ctype, body):
        return body, ctype

    # Tier 1 fallback: Scrapling direct
    body, ctype = _fetch_scrapling_bytes(url, proxy=None)
    if body and not _is_html_or_challenge(ctype, body):
        return body, ctype

    # Tier 2: Through proxy pool (httpx plain then Scrapling)
    for _ in range(_POOL_TRIES):
        proxy = pick_proxy()
        if not proxy:
            break
        body, ctype = _fetch_plain(url, proxy=proxy)
        if body and not _is_html_or_challenge(ctype, body):
            return body, ctype

        body, ctype = _fetch_scrapling_bytes(url, proxy=proxy)
        if body and not _is_html_or_challenge(ctype, body):
            return body, ctype

    # Tier 3: Relay pool is text-only (fetch_via_relay returns resp.text string),
    # so we skip it for raw binary images.
    return None, ""


def fetch_story_photo(article: ScrapedArticle) -> bytes | None:
    """Fetch and validate an article's own photo for story inset."""
    url = getattr(article, "scraped_image_url", None)
    if not url:
        return None

    if not _is_safe_url(url):
        logger.info("Story photo rejected: URL unsafe / SSRF guard (%s)", url)
        return None

    if not _robots_allowed(url):
        logger.info("Story photo rejected: robots.txt disallowed for %s", url)
        return None

    content, ctype = _fetch_bytes_with_fallbacks(url)
    if not content:
        logger.info("Story photo rejected: empty response or blocked across tiers for %s", url)
        return None

    if _is_html_or_challenge(ctype, content):
        logger.info("Story photo rejected: HTML / challenge body for %s", url)
        return None

    if ctype and not ctype.lower().startswith("image/"):
        logger.info("Story photo rejected: non-image content-type (%s) for %s", ctype, url)
        return None

    try:
        img = Image.open(io.BytesIO(content))
        img.load()
    except Exception as exc:
        logger.info("Story photo rejected: PIL decode failed for %s: %s", url, exc)
        return None

    if min(img.width, img.height) < _MIN_SIDE:
        logger.info("Story photo rejected: resolution %dx%d under %d px min for %s", img.width, img.height, _MIN_SIDE, url)
        return None

    try:
        if vision_has_watermark(content):
            logger.info("Story photo rejected: watermark detected for %s", url)
            return None
    except Exception as exc:
        logger.info("Story photo rejected: watermark check failed (fails closed): %s", exc)
        return None

    # ponytail: return original bytes, no re-encoding. Ceiling: caller stores or passes to inset.
    return content


def describe_story_photo(image_bytes: bytes, article_title: str) -> str:
    """Return a short prompt description for the photo."""
    trimmed = (article_title or "").strip()[:90]
    return f"the real news photo from the article: {trimmed}"
