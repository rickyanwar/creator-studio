"""Mode 6 (Facebook photo source) — turns new photos from a curated Facebook
page's public `/photos` grid into staged FacebookPhotoIdea rows, consumed
FIFO by app/tasks/facebook_photo.py on the fanpage's own pacing.

Facebook blocks like-counts/timestamps/captions for anyone not logged in
(tested live, see memory feature-facebook-trending-source) — the ONLY route
that works without an authenticated session is the `/photos` grid, which
returns just the photo + Facebook's own AI-generated alt-text + a permalink
containing the photo's fbid. No growth/like gating in this version (v1) —
"new to us" (fbid not already downloaded) is the only filter.
"""

import io
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

# A Facebook CDN photo can be well under a typical gallery-keyword floor —
# these are page-authored graphics, not high-res editorial photography, so
# the floor is deliberately low (mirrors Pinterest's _MIN_SIZE reasoning).
_MIN_SIZE = (300, 300)
_CANDIDATES_PER_TICK = 5

# Matches a nested photo-grid entry: [![Image N[: alt text]](cdn_image_url)](permalink)
# — the alt text group is optional (the grid's cover-photo entries sometimes
# have no alt text at all, just "![Image N](url)"). Verified against a real
# `/photos` grid fetch this session; re-verify against a live fetch before
# trusting in production (see the feature's plan doc verification section).
_PHOTO_ENTRY_RE = re.compile(
    r"\[!\[Image\s*\d+(?::\s*([^\]]*))?\]\((https?://[^\s)\"'<>]+)\)\]\((https?://[^\s)\"'<>]+)\)",
    re.IGNORECASE,
)
_FBID_RE = re.compile(r"[?&]fbid=(\d+)")

# The `/photos` grid embeds each photo at thumbnail size (`ctp=s206x206` —
# ~206px, well below any usable design floor) even though the SAME CDN path
# has a much larger rendition available at `cstp=mx<W>x<H>` (e.g. 1080x1080).
# Stripping the `ctp=` size override makes Facebook serve that larger size
# instead — confirmed live (2026-09-24): with `ctp` present the same URL
# returned a 206x206/13KB image, stripped it returned the full 1080x1080/
# 167KB image. Without this, nearly every grid candidate fails the min-size
# floor even though a genuinely larger photo exists at the same URL.
_CTP_SIZE_RE = re.compile(r"[?&]ctp=s\d+x\d+")


def _upgrade_image_url(url: str) -> str:
    return _CTP_SIZE_RE.sub("", url)


@dataclass(frozen=True)
class FacebookPhotoCandidate:
    fbid: str
    image_url: str
    alt_text: str


@dataclass(frozen=True)
class _DownloadedPhoto:
    source_url: str
    local_path: str
    filename: str
    width: int
    height: int


_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def _download_photo(url: str, dest_dir: Path, min_size: tuple[int, int]) -> "_DownloadedPhoto | None":
    """Download+validate one photo, deliberately WITHOUT
    image_downloader._fetch_and_store's shared classify_and_gate_image call
    — that gate explicitly rejects "graphics/text overlays" (see its
    docstring), which is exactly what Mode 6's source photos ARE (meme/
    debate-bait graphics, not clean editorial photography). Real incident,
    2026-09-24: reusing _fetch_and_store as-is silently rejected every
    single real candidate from gpfansglobal for this reason — confirmed via
    a direct throwaway test, not assumed. services.facebook_content_classifier
    is this pipeline's own equivalent usability gate (content-appropriate
    for graphics, rejects "other"), so no second AI gate is needed here."""
    key = url.split("?", 1)[0]
    try:
        resp = httpx.get(url, headers={"User-Agent": _UA}, timeout=20.0, follow_redirects=True)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        img.load()
    except Exception as exc:
        logger.debug("Facebook photo: download failed %s (%s)", url, exc)
        return None

    min_w, min_h = min_size
    if img.width < min_w or img.height < min_h:
        logger.debug("Facebook photo: skipping %s — %dx%d below min %dx%d", url, img.width, img.height, min_w, min_h)
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    path = dest_dir / filename
    try:
        img.convert("RGB").save(path, format="JPEG", quality=90)
    except Exception as exc:
        logger.warning("Facebook photo: failed to save %s: %s", url, exc)
        return None

    return _DownloadedPhoto(source_url=key, local_path=str(path), filename=filename, width=img.width, height=img.height)


def _photos_url(page_url_or_username: str) -> str:
    raw = (page_url_or_username or "").strip()
    if not raw:
        raise ValueError("empty Facebook page reference")
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        username = parsed.path.strip("/").split("/")[0]
    else:
        username = raw.strip("/").split("/")[0]
    if not username:
        raise ValueError(f"could not extract a page username from {page_url_or_username!r}")
    return f"https://www.facebook.com/{username}/photos"


def _existing_facebook_photo_urls(db) -> set[str]:
    from app.models.gallery import GalleryImage

    return {
        u for (u,) in
        db.query(GalleryImage.source_image_url).filter(GalleryImage.source_engine == "facebook_photo").all()
    }


def fetch_photo_candidates(page_url: str, limit: int = _CANDIDATES_PER_TICK) -> list[FacebookPhotoCandidate]:
    """Fetch the page's `/photos` grid via 9Router jina-reader and parse out
    new-looking photo entries (fbid + CDN image URL + alt text). Does NOT
    dedup against the DB itself — callers filter via _existing_facebook_photo_urls
    (mirrors pinterest_source.collect_new_candidates's own dedup split)."""
    from app.services.image_downloader import _9router_fetch_markdown

    url = _photos_url(page_url)
    text = _9router_fetch_markdown(url, context="facebook_photo_source")

    seen_fbids: set[str] = set()
    out: list[FacebookPhotoCandidate] = []
    for match in _PHOTO_ENTRY_RE.finditer(text):
        alt_text, image_url, permalink = match.group(1) or "", match.group(2), match.group(3)
        fbid_match = _FBID_RE.search(permalink)
        if not fbid_match:
            continue
        fbid = fbid_match.group(1)
        if fbid in seen_fbids:
            continue
        seen_fbids.add(fbid)
        out.append(FacebookPhotoCandidate(fbid=fbid, image_url=_upgrade_image_url(image_url), alt_text=alt_text.strip()))
        if len(out) >= limit:
            break
    return out


def build_idea_from_candidate(db, fanpage, candidate: FacebookPhotoCandidate):
    """Download one candidate photo, classify it via vision, and store it as
    a pending FacebookPhotoIdea. Returns None (and leaves no trace) on any
    rejection — download failure, low-quality gate, or a "other" classification."""
    from app.config import get_settings
    from app.models.gallery import GalleryImage
    from app.models.facebook_photo_ideas import FacebookPhotoIdea
    from app.services.design_images import _is_low_quality_photo
    from app.services.facebook_content_classifier import classify_facebook_photo

    s = get_settings()
    niche = fanpage.name
    dest_dir = Path(s.storage_base_path) / "gallery" / "facebook_photo"

    key = candidate.image_url.split("?", 1)[0]
    if key in _existing_facebook_photo_urls(db):
        return None

    item = _download_photo(candidate.image_url, dest_dir, _MIN_SIZE)
    if not item:
        return None
    image_bytes = Path(item.local_path).read_bytes()

    if _is_low_quality_photo(image_bytes):
        Path(item.local_path).unlink(missing_ok=True)
        return None

    try:
        cls = classify_facebook_photo(image_bytes, niche=niche)
    except Exception as exc:
        logger.warning("Facebook photo: classify failed fbid=%s fanpage %d: %s", candidate.fbid, fanpage.id, exc)
        Path(item.local_path).unlink(missing_ok=True)
        return None

    ctype = cls["type"]
    if ctype == "other" or (ctype == "news" and not cls["headline"]) or (ctype == "discussion" and not cls["question"]):
        Path(item.local_path).unlink(missing_ok=True)
        return None

    gi = GalleryImage(
        keyword=(cls["headline"] or cls["question"] or candidate.alt_text or "facebook").lower()[:128],
        source_image_url=item.source_url,
        local_path=item.local_path,
        public_url=f"{s.storage_base_url.rstrip('/')}/gallery/facebook_photo/{item.filename}",
        width=item.width,
        height=item.height,
        source_engine="facebook_photo",
        label=None,
    )
    db.add(gi)
    try:
        db.commit()
    except IntegrityError:
        # a concurrent tick already claimed this exact photo between our
        # dedup read and this insert — same race handled the same way as
        # pinterest_source.build_idea_from_candidate.
        db.rollback()
        Path(item.local_path).unlink(missing_ok=True)
        return None

    idea = FacebookPhotoIdea(
        fanpage_id=fanpage.id,
        gallery_image_id=gi.id,
        category=ctype,
        design_title=cls["headline"] if ctype == "news" else cls["question"],
        design_subtitle=cls["label"] if ctype == "discussion" else None,
        design_caption=cls["subject_name"] if ctype == "discussion" else None,
        status="pending",
    )
    db.add(idea)
    db.commit()
    logger.info(
        "Facebook photo: fanpage %d — new idea %d (%s) fbid=%s title=%r",
        fanpage.id, idea.id, ctype, candidate.fbid, idea.design_title,
    )
    return idea
