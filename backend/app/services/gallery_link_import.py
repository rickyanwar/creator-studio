"""Import gallery images from Getty pages and direct image URLs."""

import ipaddress
import re
import socket
from io import BytesIO
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image

from app.config import get_settings
from app.services.gallery_labeler import keyword_name_from_gallery_keyword
from app.services.design_images import vision_has_watermark
from app.services.getty_dates import anchors_from_db, build_hints, estimate_date_from_anchors, parse_asset_id
from app.services.image_downloader import (
    _9router_fetch_markdown,
    _CAPTION_IMG_RE,
    _dedup_key,
    _parse_caption_date,
    _fetch_and_store,
    keyword_slug,
)


class LinkError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedLink:
    kind: str
    download_url: str
    dedup_key: str
    asset_id: int | None = None
    caption: str | None = None
    captured_date: date | None = None
    suggested_keyword: str | None = None
    original_url: str | None = None


@dataclass(frozen=True)
class LinkResult:
    url: str
    status: str
    image_id: int | None
    reason: str | None


_ASSET_ANYWHERE_RE = re.compile(r"(?<!\d)(\d{6,})(?!\d)")
_NAME_OF_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s+of\b")
_IMAGE_SUFFIX_RE = re.compile(r"\.(?:jpg|jpeg|png|webp)(?:$|[?#])", re.IGNORECASE)
_MAX_BYTES = 15 * 1024 * 1024


def parse_links(text: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for value in re.split(r"\s+", text or ""):
        if not value.startswith(("http://", "https://")) or value in seen:
            continue
        seen.add(value)
        links.append(value)
    if len(links) > 20:
        raise ValueError("max 20 links")
    return links


def _is_public_host(host: str) -> None:
    normalized_host = host.lower().rstrip(".")
    if not normalized_host or normalized_host == "localhost" or normalized_host.endswith(".localhost"):
        raise LinkError("ssrf_private_host")
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise LinkError("ssrf_host_unresolvable") from exc
    for _, _, _, _, sockaddr in addresses:
        address = ipaddress.ip_address(sockaddr[0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified:
            raise LinkError("ssrf_private_host")
    storage_host = (urlparse(get_settings().storage_base_url or "").hostname or "").lower()
    if host.lower() == storage_host:
        raise LinkError("ssrf_storage_host")


def _check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise LinkError("invalid_scheme")
    _is_public_host(parsed.hostname or "")


def _safe_fetch(url: str):
    current = url
    for _ in range(4):
        _check_url(current)
        with httpx.Client(follow_redirects=False, timeout=20.0) as client:
            with client.stream("GET", current, headers={"User-Agent": "Mozilla/5.0"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise LinkError("redirect_missing_location")
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise LinkError("direct_fetch_failed")
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > _MAX_BYTES:
                        raise LinkError("body exceeds 15 MB")
                return type("Fetched", (), {"content": bytes(content), "headers": response.headers})()
    raise LinkError("too many redirects")


_GENERIC_NAME_WORDS = {"motogp", "f1", "formula", "nba", "ufc", "race", "sprint", "qualifying", "practice", "grand", "prix", "news", "photo", "event"}


def _clean_name(value: str | None) -> str | None:
    words = [word for word in re.split(r"[-_\s]+", value or "") if word and word.lower() not in _GENERIC_NAME_WORDS]
    return " ".join(words).strip().lower() or None


def _original_slug_name(url: str) -> str | None:
    parts = [part for part in urlparse(url).path.split("/") if part]
    slug = parts[-2] if parts and parts[-1].isdigit() and len(parts[-1]) >= 6 and len(parts) > 1 else parts[-1] if parts else ""
    slug = re.sub(r"-(?:news-)?photo$", "", slug, flags=re.IGNORECASE)
    slug = re.sub(r"-?\d{6,}$", "", slug)
    candidate = _clean_name(slug)
    return candidate if candidate and len(candidate.split()) >= 2 else None


def _exif_metadata(content: bytes) -> tuple[str | None, date | None]:
    try:
        with Image.open(BytesIO(content)) as image:
            exif = image.getexif()
            description = str(exif.get(270) or "")
            match = _NAME_OF_RE.search(description)
            keyword = _clean_name(match.group(1) if match else None)
            original = str(exif.get(36867) or exif.get(306) or exif.get(33432) or "")
            year_match = re.search(r"(19\d{2}|20\d{2})", original)
            captured = date(int(year_match.group(1)), 1, 1) if year_match else None
            date_match = re.search(r"\b(" + "|".join(("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")) + r")\s+(\d{1,2})\b", description, re.IGNORECASE)
            if year_match and date_match:
                month = {name: index for index, name in enumerate(("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"), 1)}[date_match.group(1).lower()]
                captured = date(int(year_match.group(1)), month, int(date_match.group(2)))
            return keyword, captured
    except Exception:
        return None, None


def _suggested_keyword(caption: str | None, url: str, original_url: str | None = None) -> str | None:
    original = _original_slug_name(original_url or "")
    if original:
        return original
    match = _NAME_OF_RE.search(caption or "")
    if match:
        return _clean_name(match.group(1))
    slug = build_hints(url).get("slug") or ""
    words = re.split(r"[-_]+", slug)
    before_of = []
    for word in words:
        if word.lower() == "of":
            break
        before_of.append(word)
    return _clean_name(" ".join(before_of))


def _resolve_getty(url: str, asset_id: int) -> ResolvedLink:
    search_url = f"https://www.gettyimages.com/search/2/image?family=editorial&phrase={asset_id}"
    markdown = _9router_fetch_markdown(search_url, context="gallery_link_import", keyword=str(asset_id))
    matches = _CAPTION_IMG_RE.findall(markdown)
    for caption, image_url in matches:
        if f"/id/{asset_id}/" not in image_url or not re.search(r"(?:^|&)w=0(?:&|$)", urlparse(image_url).query):
            continue
        return ResolvedLink(
            kind="getty", download_url=image_url, dedup_key=_dedup_key(image_url), asset_id=asset_id,
            caption=caption, captured_date=_parse_caption_date(caption),
            suggested_keyword=_suggested_keyword(caption, image_url, url), original_url=url,
        )
    raise LinkError("getty_not_found")


def resolve_link(url: str) -> ResolvedLink:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if "gettyimages." in host:
        match = _ASSET_ANYWHERE_RE.search(url)
        if not match:
            raise LinkError("getty_asset_id_missing")
        return _resolve_getty(url, int(match.group(1)))

    _check_url(url)
    if not _IMAGE_SUFFIX_RE.search(parsed.path):
        try:
            response = _safe_fetch(url)
            if not response.headers.get("content-type", "").lower().startswith("image/"):
                raise LinkError("not_an_image")
        except LinkError:
            raise
        except Exception as exc:
            raise LinkError("direct_fetch_failed") from exc
    else:
        _safe_fetch(url)
    return ResolvedLink(kind="direct", download_url=url, dedup_key=_dedup_key(url))


def import_links(db, links: list[str], keyword: str | None = None, hints: dict | None = None) -> list[LinkResult]:
    """Resolve and store links. Labeling is queued after a successful insert."""
    from app.models.gallery import GalleryImage, GalleryKeyword
    from app.tasks.gallery_labels import label_gallery_image

    s = get_settings()
    output: list[LinkResult] = []
    for source in links:
        try:
            resolved = resolve_link(source)
            if db.query(GalleryImage).filter_by(source_image_url=resolved.dedup_key).first():
                output.append(LinkResult(source, "duplicate", None, "already exists"))
                continue
            fetched = _safe_fetch(resolved.download_url)
            is_clean_getty = resolved.kind == "getty" and "media.gettyimages.com" in resolved.download_url and f"/id/{resolved.asset_id}/" in resolved.download_url and re.search(r"(?:^|&)w=0(?:&|$)", urlparse(resolved.download_url).query)
            exif_keyword, exif_date = _exif_metadata(fetched.content) if resolved.kind == "getty" else (None, None)
            if not is_clean_getty and vision_has_watermark(fetched.content):
                output.append(LinkResult(source, "watermark", None, "watermark detected"))
                continue
            suggested_keyword = resolved.suggested_keyword or exif_keyword
            final_keyword = keyword or suggested_keyword or "manual"
            keyword_row = db.query(GalleryKeyword).filter_by(keyword=final_keyword).first()
            min_size = (keyword_row.min_width, keyword_row.min_height) if keyword_row else (300, 300)
            with Image.open(BytesIO(fetched.content)) as source_image:
                source_width, source_height = source_image.size
            if source_width < min_size[0] or source_height < min_size[1]:
                output.append(LinkResult(source, "rejected", None, f"too_small {source_width}x{source_height} vs min {min_size[0]}x{min_size[1]}"))
                continue
            slug = keyword_slug(final_keyword)
            dest = Path(s.storage_base_path) / "gallery" / slug
            items = _fetch_and_store(
                [resolved.download_url], dest, 1, min_size, set(), "manual-link",
                subject=keyword or suggested_keyword,
                fetcher=lambda _: fetched.content,
            )
            if not items:
                output.append(LinkResult(source, "rejected", None, "vision_gate_rejected"))
                continue
            item = items[0]
            captured = resolved.captured_date or exif_date
            confidence = "day" if captured else None
            if not captured and resolved.asset_id is not None:
                estimate = estimate_date_from_anchors(resolved.asset_id, anchors_from_db(db))
                if estimate:
                    captured, confidence = estimate
            attributes = {"getty_hints": build_hints(resolved.download_url)} if resolved.kind == "getty" else {}
            if hints:
                attributes["owner_hints"] = hints
                manual_fields = ["people"] if hints.get("people") else []
                manual_fields.extend(f"event.{field}" for field in ("session", "event_name", "year") if hints.get(field) not in (None, ""))
                if hints.get("note"):
                    manual_fields.append("note")
                attributes["manual_fields"] = sorted(manual_fields)
                if hints.get("people"):
                    attributes["people"] = [{"name": name, "confidence": 1.0, "source": "manual"} for name in hints["people"]]
                event = {field: hints[field] for field in ("session", "event_name", "year") if hints.get(field) not in (None, "")}
                if event:
                    attributes["event"] = event
            if confidence:
                attributes.update({"captured_at_source": "caption" if resolved.captured_date else "getty_id", "captured_at_confidence": confidence})
            image = GalleryImage(
                keyword=final_keyword[:128], source_image_url=item.source_url,
                local_path=item.local_path, public_url=f"{s.storage_base_url.rstrip('/')}/gallery/{slug}/{item.filename}",
                width=item.width, height=item.height, source_engine="manual-link", license_info="manual-link",
                label=item.label, captured_at=captured, attributes=attributes or None,
            )
            db.add(image)
            db.commit()
            label_gallery_image.delay(image.id, force=True)
            output.append(LinkResult(source, "saved", image.id, None))
        except LinkError as exc:
            output.append(LinkResult(source, "rejected", None, str(exc)))
        except Exception as exc:
            db.rollback()
            output.append(LinkResult(source, "error", None, str(exc)[:300]))
    return output
