"""FlashAPI (fast.igapi.ru) — alternative Instagram scraper (no burner account needed).

Base URL:  https://fast.igapi.ru/api
Endpoint:  GET /posts_username?user={username}
Auth:      Authorization: Bearer {token}

Returns max 10 posts per call (API limit — no count/limit param accepted).

The client normalises the response into duck-typed objects matching the
instagrapi Media/Resource attributes the crawler already consumes:
  .pk, .code, .caption_text, .taken_at, .media_type, .resources,
  resource.media_type, resource.thumbnail_url
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# media_type constants — mirror instagrapi convention
MEDIA_IMAGE = 1
MEDIA_VIDEO = 2
MEDIA_ALBUM = 8


@dataclass
class FlashAPIResource:
    """One image slot inside an album post."""
    thumbnail_url: str
    url: str = ""
    media_type: int = MEDIA_IMAGE


@dataclass
class FlashAPIMedia:
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


class FlashAPIClient:
    """HTTP client for fast.igapi.ru."""

    def __init__(self, api_key: str, base_url: str = "https://fast.igapi.ru/api"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        })

    def fetch_recent_posts(self, ig_username: str, amount: int = 12) -> list[FlashAPIMedia]:
        """Return up to *amount* recent posts for *ig_username*.

        The API returns at most 10 per call; amount is used only to cap the
        result list locally (pagination is not implemented here).
        """
        url = f"{self.base_url}/posts_username"
        params = {"user": ig_username}

        try:
            resp = self.session.get(url, params=params, timeout=30)
            if not resp.ok:
                body = ""
                try:
                    body = resp.text[:400]
                except Exception:
                    pass
                logger.error(
                    "FlashAPI HTTP %s for @%s — %s", resp.status_code, ig_username, body
                )
                resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("FlashAPI request failed for @%s: %s", ig_username, exc)
            raise

        raw = resp.json()
        posts = _extract_posts_list(raw)
        medias = []
        for item in posts[:amount]:
            media = _normalise(item)
            if media is not None:
                medias.append(media)
        logger.info("FlashAPI: fetched %d posts for @%s", len(medias), ig_username)
        return medias


# ── Response normalisation ────────────────────────────────────────────────────

def _extract_posts_list(raw) -> list:
    """Handle the various envelope shapes the API might return.

    Supports:
    - Plain list
    - {"data": [...]} or {"posts": [...]} or {"items": [...]}
    - GraphQL: {"data": {"user": {"edge_owner_to_timeline_media": {"edges": [{"node": ...}]}}}}
    """
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        return []

    # Simple flat envelope
    for key in ("posts", "items", "results", "media"):
        if isinstance(raw.get(key), list):
            return raw[key]

    # data key — may be a list or a nested GraphQL dict
    data = raw.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # GraphQL shape: data.user.edge_owner_to_timeline_media.edges
        user = data.get("user") or {}
        timeline = user.get("edge_owner_to_timeline_media") or {}
        edges = timeline.get("edges") or []
        if edges:
            return [e["node"] for e in edges if isinstance(e.get("node"), dict)]
        # Flat under data
        for key in ("posts", "items", "results", "media"):
            if isinstance(data.get(key), list):
                return data[key]

    return []


def _get_image_url(item: dict) -> str:
    """Extract the best image URL from a private-API item (image_versions2) or GraphQL (display_url)."""
    candidates = (item.get("image_versions2") or {}).get("candidates") or []
    if candidates:
        return candidates[0].get("url", "")
    return (
        item.get("display_url")
        or item.get("thumbnail_url")
        or item.get("image_url")
        or item.get("url")
        or ""
    )


def _normalise(item: dict) -> Optional[FlashAPIMedia]:
    """Convert a raw post dict into FlashAPIMedia. Returns None for video posts."""

    # Determine media type — GraphQL uses __typename; private API uses media_type int
    typename = item.get("__typename", "")
    is_video = item.get("is_video") or item.get("is_reel")

    if typename == "GraphVideo" or is_video:
        return None

    raw_type = str(item.get("media_type") or "").lower()
    if raw_type in ("2", "video", "reel"):
        return None

    pk = str(item.get("id") or item.get("pk") or "")
    code = item.get("shortcode") or item.get("code") or ""

    # Caption — GraphQL wraps in edges; private API returns a dict with "text"
    caption_edges = (item.get("edge_media_to_caption") or {}).get("edges") or []
    if caption_edges:
        caption = caption_edges[0].get("node", {}).get("text", "")
    else:
        raw_caption = item.get("caption") or item.get("caption_text") or ""
        caption = raw_caption.get("text", "") if isinstance(raw_caption, dict) else str(raw_caption)

    # Timestamp — GraphQL: taken_at_timestamp; private API: taken_at (unix int)
    ts_raw = (
        item.get("taken_at_timestamp")
        or item.get("timestamp")
        or item.get("taken_at")
    )
    taken_at = _parse_timestamp(ts_raw)

    # Album children — GraphQL: edge_sidecar_to_children; private API: carousel_media
    sidecar = (item.get("edge_sidecar_to_children") or {}).get("edges") or []
    resources: list[FlashAPIResource] = []

    if sidecar:
        for edge in sidecar:
            node = edge.get("node") or {}
            if node.get("is_video"):
                continue
            thumb = _get_image_url(node)
            if thumb:
                resources.append(FlashAPIResource(thumbnail_url=thumb, url=thumb))
    else:
        for r in (item.get("carousel_media") or item.get("resources") or []):
            r_type = str(r.get("media_type") or "").lower()
            if r_type in ("2", "video") or r.get("is_video"):
                continue
            thumb = _get_image_url(r)
            if thumb:
                resources.append(FlashAPIResource(thumbnail_url=thumb, url=thumb))

    # Determine final media type
    if typename == "GraphSidecar" or raw_type in ("8", "album", "carousel") or len(resources) > 1:
        media_type = MEDIA_ALBUM
    else:
        media_type = MEDIA_IMAGE

    thumb = _get_image_url(item)

    return FlashAPIMedia(
        pk=pk,
        code=code,
        caption_text=caption,
        taken_at=taken_at,
        media_type=media_type,
        resources=resources,
        thumbnail_url=thumb,
        url=thumb,
    )


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
