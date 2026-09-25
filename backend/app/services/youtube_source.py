"""YouTube discovery for Mode 7 — no yt-dlp, no API key, no player requests.

Everything here is a plain GET that YouTube serves to anyone (verified from
the VPS IP, 2026-09-25): a channel's @handle page (to resolve its UC… id
once) and the public RSS feeds (`/feeds/videos.xml?channel_id=` /
`?playlist_id=`, ~15 newest entries each). The feed's per-entry link
distinguishes `/shorts/` from normal uploads, so Shorts are skipped without
another request. Downloading (the part YouTube actually bot-walls) lives in
services.yt_downloader.
"""

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,64}$")
_CANONICAL_RE = re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]{22})"')
_META_CHANNEL_RE = re.compile(r'"(?:channelId|externalId)":"(UC[A-Za-z0-9_-]{22})"')
_NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "media": "http://search.yahoo.com/mrss/"}


@dataclass(frozen=True)
class ParsedSource:
    kind: str                    # channel | playlist | video
    video_id: str | None = None
    playlist_id: str | None = None
    channel_id: str | None = None
    channel_path: str | None = None   # "@handle" / "c/name" / "user/name" — needs resolving


@dataclass(frozen=True)
class FeedEntry:
    video_id: str
    title: str
    published_at: datetime | None
    is_short: bool


def classify_url(url: str) -> ParsedSource:
    """Any pasted YouTube URL (or a bare @handle / video id) → what it is.
    Raises ValueError with a user-facing message for anything unusable."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("Paste a YouTube channel, playlist or video link.")
    if raw.startswith("@"):
        return ParsedSource(kind="channel", channel_path=raw)
    if _VIDEO_ID_RE.match(raw):
        return ParsedSource(kind="video", video_id=raw)
    if not re.match(r"^https?://", raw):
        raw = "https://" + raw

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.").removeprefix("music.")
    path = parsed.path.rstrip("/")
    query = parse_qs(parsed.query)

    if host == "youtu.be":
        vid = path.lstrip("/").split("/")[0]
        if _VIDEO_ID_RE.match(vid):
            return ParsedSource(kind="video", video_id=vid)
        raise ValueError("That youtu.be link has no valid video id.")
    if host != "youtube.com":
        raise ValueError("That isn't a youtube.com link.")

    if path.startswith("/shorts/"):
        raise ValueError("Shorts are already vertical — paste a normal video, playlist or channel instead.")
    if path == "/playlist":
        pl = (query.get("list") or [""])[0]
        if _PLAYLIST_ID_RE.match(pl):
            return ParsedSource(kind="playlist", playlist_id=pl)
        raise ValueError("That playlist link has no valid list id.")
    if path == "/watch":
        vid = (query.get("v") or [""])[0]
        if _VIDEO_ID_RE.match(vid):
            return ParsedSource(kind="video", video_id=vid)
        raise ValueError("That watch link has no valid video id.")
    for prefix in ("/live/", "/embed/", "/v/"):
        if path.startswith(prefix):
            vid = path[len(prefix):].split("/")[0]
            if _VIDEO_ID_RE.match(vid):
                return ParsedSource(kind="video", video_id=vid)
    if path.startswith("/channel/"):
        cid = path.split("/")[2]
        if _CHANNEL_ID_RE.match(cid):
            return ParsedSource(kind="channel", channel_id=cid)
        raise ValueError("That channel link has no valid channel id.")
    first = path.lstrip("/").split("/")[0]
    if first.startswith("@"):
        return ParsedSource(kind="channel", channel_path=first)
    if first in ("c", "user") and len(path.split("/")) >= 3:
        return ParsedSource(kind="channel", channel_path=f"{first}/{path.split('/')[2]}")
    raise ValueError("Couldn't tell whether that's a channel, playlist or video link.")


def resolve_channel_id(channel_path: str) -> str:
    """"@handle" / "c/name" / "user/name" → UC… id, from the page's canonical
    link. Done once when the source is added, then cached on the row."""
    url = f"https://www.youtube.com/{channel_path.lstrip('/')}"
    resp = httpx.get(url, headers={"User-Agent": _UA, "Accept-Language": "en"}, timeout=20.0, follow_redirects=True)
    if resp.status_code == 404:
        raise ValueError(f"YouTube has no channel at {url}.")
    resp.raise_for_status()
    match = _CANONICAL_RE.search(resp.text) or _META_CHANNEL_RE.search(resp.text)
    if not match:
        raise ValueError(f"Couldn't find the channel id on {url} (YouTube may have served a consent page).")
    return match.group(1)


def fetch_feed(*, channel_id: str | None = None, playlist_id: str | None = None) -> list[FeedEntry]:
    """The ~15 newest entries of a channel's or playlist's public RSS feed,
    newest first."""
    if channel_id:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    elif playlist_id:
        url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
    else:
        raise ValueError("fetch_feed needs a channel_id or playlist_id")
    resp = httpx.get(url, headers={"User-Agent": _UA}, timeout=20.0, follow_redirects=True)
    resp.raise_for_status()
    return parse_feed(resp.text)


def parse_feed(xml_text: str) -> list[FeedEntry]:
    root = ET.fromstring(xml_text)
    entries = []
    for e in root.findall("a:entry", _NS):
        vid = (e.findtext("yt:videoId", default="", namespaces=_NS) or "").strip()
        if not _VIDEO_ID_RE.match(vid):
            continue
        link = e.find("a:link[@rel='alternate']", _NS)
        href = link.get("href", "") if link is not None else ""
        entries.append(FeedEntry(
            video_id=vid,
            title=(e.findtext("a:title", default="", namespaces=_NS) or "").strip(),
            published_at=_parse_iso(e.findtext("a:published", default="", namespaces=_NS)),
            is_short="/shorts/" in href,
        ))
    entries.sort(key=lambda x: x.published_at or datetime.min, reverse=True)
    return entries


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
