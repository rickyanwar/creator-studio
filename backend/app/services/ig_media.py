"""Normalised Instagram media types shared by all scraper backends.

Moved from flashapi_client.py and extended:
- IGMedia / IGResource (renamed from FlashAPI*)
- normalise_post: GraphQL nodes (display_resources), private-API shape
  (image_versions2.candidates), sidecar/carousel children, proxy unwrap.
- unwrap_proxy_url: strips viewer-site proxy wrappers.
- Videos → None (unchanged behaviour).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# media_type constants — mirror instagrapi convention
MEDIA_IMAGE = 1
MEDIA_VIDEO = 2
MEDIA_ALBUM = 8

# Proxy hosts whose `uri`/`url` query param wraps a raw CDN URL
_PROXY_HOSTS = {"media.gramsnap.com", "media.anonyig.com", "media.igstoryviewer.to"}


@dataclass
class IGResource:
    """One image slot inside an album post."""
    thumbnail_url: str
    url: str = ""
    media_type: int = MEDIA_IMAGE


@dataclass
class IGMedia:
    """Duck-typed equivalent of instagrapi's Media object."""
    pk: str
    code: str
    caption_text: str
    taken_at: datetime
    media_type: int          # 1=IMAGE, 8=ALBUM (VIDEO posts are skipped)
    resources: list = field(default_factory=list)
    thumbnail_url: str = ""
    url: str = ""

    @property
    def id(self):
        return self.pk


# ── Proxy URL unwrapping ──────────────────────────────────────────────────────

def unwrap_proxy_url(url: str) -> str:
    """Strip known viewer-site proxy wrappers, returning their inner URL."""
    if not url:
        return url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if host in _PROXY_HOSTS:
        qs = parse_qs(parsed.query)
        for param in ("uri", "url"):
            vals = qs.get(param)
            if vals:
                return vals[0]
    return url


def is_ig_cdn_url(url: str) -> bool:
    """Return whether URL is HTTPS and hosted by an Instagram image CDN."""
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        host in {"cdninstagram.com", "fbcdn.net"}
        or host.endswith(".cdninstagram.com")
        or host.endswith(".fbcdn.net")
    )


def _validated_image_url(item: dict) -> str:
    url = unwrap_proxy_url(_get_image_url(item))
    if url and not is_ig_cdn_url(url):
        logger.debug("Dropping non-Instagram CDN media URL: %s", url)
        return ""
    return url


# ── Timestamp parsing ─────────────────────────────────────────────────────────

def _parse_timestamp(value) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(value, fmt)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return datetime.now(timezone.utc)


# ── Image URL extraction ─────────────────────────────────────────────────────

def _best_display_resource(item: dict) -> str:
    """Prefer largest display_resources[].src (raw IG CDN)."""
    resources = item.get("display_resources") or []
    if resources:
        best = max(resources, key=lambda x: (x.get("config_width", 0) * x.get("config_height", 0)))
        return best.get("src") or best.get("url_downloadable") or ""
    return ""


def _best_image_versions2(item: dict) -> str:
    """Pick largest width*height from image_versions2.candidates (private-API shape)."""
    candidates = (item.get("image_versions2") or {}).get("candidates") or []
    if candidates:
        best = max(candidates, key=lambda c: (c.get("width", 0) * c.get("height", 0)))
        return best.get("url", "")
    return ""


def _get_image_url(item: dict) -> str:
    """Extract the best image URL from a node, preferring display_resources > image_versions2 > display_url."""
    url = _best_display_resource(item)
    if url:
        return url
    url = _best_image_versions2(item)
    if url:
        return url
    return (
        item.get("display_url")
        or item.get("thumbnail_url")
        or item.get("image_url")
        or item.get("url")
        or ""
    )


# ── Video detection ──────────────────────────────────────────────────────────

def is_video_node(node: dict) -> bool:
    """Return True when node is explicitly a video based on raw flags.

    Checks is_video, __typename GraphVideo, media_type 2/"video"/"reel", is_reel.
    Also looks inside item.get("node", item) shape (IGStoryViewer wraps in node key).
    A node that merely fails to parse is NOT considered a video.
    """
    # Support both bare node and {"node": {...}} wrapper shapes
    item = node.get("node", node) if isinstance(node, dict) else node
    if not isinstance(item, dict):
        return False
    if item.get("__typename") == "GraphVideo":
        return True
    if item.get("is_video") or item.get("is_reel"):
        return True
    raw_type = str(item.get("media_type") or "").lower()
    if raw_type in ("2", "video", "reel"):
        return True
    return False


_is_video_node = is_video_node


# ── Post normalisation ───────────────────────────────────────────────────────

def normalise_post(item: dict) -> Optional[IGMedia]:
    """Convert a raw post dict into IGMedia. Returns None for video posts.

    Handles both GraphQL shape (edge_sidecar_to_children, display_resources,
    edge_media_to_caption, taken_at_timestamp) and private-API shape
    (carousel_media, image_versions2, caption dict, taken_at unix int).

    Every URL is passed through unwrap_proxy_url.
    """

    # ── Detect video → skip ───────────────────────────────────────────────
    typename = item.get("__typename", "")
    raw_type = str(item.get("media_type") or "").lower()
    if is_video_node(item):
        return None

    # ── Identity ──────────────────────────────────────────────────────────
    pk = str(item.get("id") or item.get("pk") or "")
    code = item.get("shortcode") or item.get("code") or ""

    # ── Caption ───────────────────────────────────────────────────────────
    caption_edges = (item.get("edge_media_to_caption") or {}).get("edges") or []
    if caption_edges:
        caption = caption_edges[0].get("node", {}).get("text", "")
    else:
        raw_caption = item.get("caption") or item.get("caption_text") or ""
        caption = raw_caption.get("text", "") if isinstance(raw_caption, dict) else str(raw_caption)

    # ── Timestamp (always tz-aware UTC) ───────────────────────────────────
    ts_raw = (
        item.get("taken_at_timestamp")
        or item.get("timestamp")
        or item.get("taken_at")
    )
    taken_at = _parse_timestamp(ts_raw)

    # ── Album children ────────────────────────────────────────────────────
    # GraphQL: edge_sidecar_to_children.edges[].node
    sidecar = (item.get("edge_sidecar_to_children") or {}).get("edges") or []
    resources: list[IGResource] = []

    if sidecar:
        for edge in sidecar:
            node = edge.get("node") or {}
            if is_video_node(node):
                continue
            thumb = _validated_image_url(node)
            if thumb:
                resources.append(IGResource(thumbnail_url=thumb, url=thumb))
    else:
        # Private-API: carousel_media or generic resources list
        for r in (item.get("carousel_media") or item.get("resources") or []):
            if is_video_node(r):
                continue
            thumb = _validated_image_url(r)
            if thumb:
                resources.append(IGResource(thumbnail_url=thumb, url=thumb))

    # ── Final media type ──────────────────────────────────────────────────
    if typename == "GraphSidecar" or raw_type in ("8", "album", "carousel") or len(resources) > 1:
        media_type = MEDIA_ALBUM
    else:
        media_type = MEDIA_IMAGE

    thumb = _validated_image_url(item)
    if not thumb and resources:
        thumb = resources[0].thumbnail_url
    if not thumb:
        logger.debug("Dropping non-video post %s with no valid IG CDN image", code or pk)
        return None

    return IGMedia(
        pk=pk,
        code=code,
        caption_text=caption,
        taken_at=taken_at,
        media_type=media_type,
        resources=resources,
        thumbnail_url=thumb,
        url=thumb,
    )
