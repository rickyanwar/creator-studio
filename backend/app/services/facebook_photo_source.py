"""Mode 6 (Facebook photo source) — turns new photos from a curated Facebook
page's public `/photos` grid into staged FacebookPhotoIdea rows, consumed
FIFO by app/tasks/facebook_photo.py on the fanpage's own pacing.

Each photo is only READ: vision extracts its text and decides the card type
(news/quote/discussion). The photo is deleted right after and never reaches
a design — every card is recreated around a clean photo of the subject (see
design_renderer.render_facebook_photo).

Facebook blocks like-counts/timestamps/captions for anyone not logged in
(tested live, see memory feature-facebook-trending-source) — the ONLY route
that works without an authenticated session is the `/photos` grid, which
returns just the photo + Facebook's own AI-generated alt-text + a permalink
containing the photo's fbid. No growth/like gating in this version (v1) —
"new to us" (fbid not already evaluated) is the only filter.
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


_MARKER_PREFIX = "facebook_photo:"


def _seen_fbids(db) -> set[str]:
    """fbids already evaluated (accepted or rejected). Keyed on the fbid —
    parsed from each grid entry's permalink — NOT the image URL: Facebook
    serves the same photo from a different CDN host depending on where the
    fetch lands (scontent.fcrk1-5 / .fbog11-1 / .fcai19-2 …). Found
    2026-09-26: URL-keyed dedup re-imported the same Sport Opus photos up to
    3× (30 markers for 19 distinct photos), producing duplicate posts."""
    from app.models.gallery import GalleryImage

    rows = db.query(GalleryImage.keyword).filter(GalleryImage.source_engine == "facebook_photo").all()
    return {k[len(_MARKER_PREFIX):] for (k,) in rows if k and k.startswith(_MARKER_PREFIX)}


_FETCH_ATTEMPTS = 3


def _fetch_grid_markdown(page_url: str) -> str:
    """The `/photos` grid as markdown, via 9Router jina-reader. Each attempt
    carries a throwaway cache-busting query param: jina otherwise keeps
    serving a stale ~200-char "cached snapshot" with no grid in it — found
    2026-09-25, 0/4 plain fetches of gpfansglobal returned any entries,
    3/4 cache-busted ones returned the full grid (Facebook ignores the
    param). Retried while the result parses to no entries at all."""
    from app.services.image_downloader import _9router_fetch_markdown

    base = _photos_url(page_url)
    text = ""
    for attempt in range(1, _FETCH_ATTEMPTS + 1):
        text = _9router_fetch_markdown(f"{base}?_cb={uuid.uuid4().hex[:10]}", context="facebook_photo_source")
        if _PHOTO_ENTRY_RE.search(text):
            return text
        logger.info("Facebook photo: %s attempt %d had no grid entries (%d chars)", base, attempt, len(text))
    logger.warning("Facebook photo: %s returned no grid entries after %d attempts", base, _FETCH_ATTEMPTS)
    return text


def fetch_photo_candidates(
    page_url: str, limit: int = _CANDIDATES_PER_TICK, skip_fbids: set[str] | None = None,
) -> list[FacebookPhotoCandidate]:
    """Fetch the page's `/photos` grid via 9Router jina-reader and parse out
    up to `limit` photo entries (fbid + CDN image URL + alt text) whose fbid
    isn't already in `skip_fbids` (see _seen_fbids). Filtering BEFORE the
    limit matters: otherwise the first `limit` grid entries, once all seen,
    would hide every newer-to-us photo further down the grid forever."""
    skip_fbids = skip_fbids or set()
    text = _fetch_grid_markdown(page_url)

    seen_fbids: set[str] = set()
    out: list[FacebookPhotoCandidate] = []
    for match in _PHOTO_ENTRY_RE.finditer(text):
        alt_text, image_url, permalink = match.group(1) or "", match.group(2), match.group(3)
        fbid_match = _FBID_RE.search(permalink)
        if not fbid_match:
            continue
        fbid = fbid_match.group(1)
        if fbid in seen_fbids or fbid in skip_fbids:
            continue
        seen_fbids.add(fbid)
        image_url = _upgrade_image_url(image_url)
        out.append(FacebookPhotoCandidate(fbid=fbid, image_url=image_url, alt_text=alt_text.strip()))
        if len(out) >= limit:
            break
    return out


def _idea_fields(ctype: str, cls: dict) -> dict | None:
    """Map a classification onto the idea's design fields, per each template
    pool's render contract. None when the category isn't usable (other, or a
    required field came back empty)."""
    if ctype == "news" and cls["headline"]:
        return {"design_title": cls["headline"], "design_subtitle": None, "design_caption": None}
    if ctype == "quote" and cls["quote"] and cls["speaker"]:
        # Mode 2 quote-card convention (see news_copywriter's quote prompt):
        # the bare quote is the title, the speaker is the name-badge subtitle.
        return {"design_title": cls["quote"], "design_subtitle": cls["speaker"], "design_caption": None}
    if ctype == "discussion" and cls["question"]:
        return {
            "design_title": cls["question"],
            "design_subtitle": cls["label"],
            "design_caption": cls["subject_name"] or None,
        }
    return None


def _clean_line(text: str) -> str:
    return (text or "").strip().strip('"“”').strip()


def _localize_discussion(text: str, fanpage) -> str:
    """Discussion counterpart of ig_recreate's _rewrite_news_title /
    _translate_quote: keep the SAME debate (names, facts, framing) and only
    localize/tighten it. Mode 4's own copywriter (generate_discussion_copy's
    evergreen prompt) is deliberately not reused — it forces a pure-opinion
    rewrite with no facts, which would drift away from what the source
    page actually asked."""
    from app.services.ai_caption import generate_caption
    from app.services.news_copywriter import _DISCUSSION_MAX_QUESTION_CHARS

    prompt = (
        f'Rewrite this fan-debate line for the Facebook page "{fanpage.name}".\n'
        f'LINE: "{text}"\n'
        f"- Language: {fanpage.caption_language}\n"
        f"- Keep the same debate, names and facts — do not add anything new.\n"
        f"- Keep its form (a question stays a question) and make it punchy.\n"
        f"- Normal sentence case, NOT all caps.\n"
        f"- Max {_DISCUSSION_MAX_QUESTION_CHARS} characters. No hashtags, no emoji.\n"
        f"Output ONLY the rewritten line — no quotes, no explanation."
    )
    line, _ = generate_caption(prompt)
    return line


# Same cap Mode 2 puts on quote titles (news_copywriter.generate_news_copy) —
# a quote card is one punchy line, not a paragraph.
_QUOTE_MAX_CHARS = 140


def _localize_quote(text: str, fanpage) -> str:
    """ig_recreate._translate_quote, plus two things Facebook quote graphics
    need that IG screenshots didn't (found 2026-09-25 on real gpfansglobal
    posts): their text is usually set in ALL CAPS as styling, and a quote
    can run ~300 characters. Excerpts word-for-word to fit — a quote must
    stay accurate, so it's never summarized."""
    from app.services.ai_caption import generate_caption

    prompt = (
        f'Translate this quote to {fanpage.caption_language} for the Facebook page "{fanpage.name}".\n'
        f"QUOTE: {text}\n"
        f"- Preserve the exact meaning — do not paraphrase or embellish. If it's already in "
        f"{fanpage.caption_language}, keep the wording.\n"
        f"- Write it in normal sentence case, NOT all caps — the source graphic's capitals are only styling.\n"
        f"- If it's longer than {_QUOTE_MAX_CHARS} characters, keep only its most striking complete "
        f"sentence(s), word-for-word, so it fits. Never summarize.\n"
        f"- No speaker name, no quotation marks.\n"
        f"Output ONLY the quote — no explanation."
    )
    quote, _ = generate_caption(prompt)
    return _fit_quote(_clean_line(quote))


def _fit_quote(quote: str, limit: int = _QUOTE_MAX_CHARS) -> str:
    """Hard cap for when the model ignores the excerpt instruction (seen
    2026-09-25: a ~250-char Briatore quote came back whole). Ends on the
    last full sentence if that keeps most of it, else on a word boundary +
    "…" — never mid-word ("media sos…" on the rendered card)."""
    if len(quote) <= limit:
        return quote
    head = quote[:limit]
    sentence_end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if sentence_end >= limit * 0.6:
        return head[: sentence_end + 1]
    word_end = head[: limit - 1].rfind(" ")
    cut = head[:word_end] if word_end > 0 else head[: limit - 1]
    return cut.rstrip(",;:—- ") + "…"


def _localize_title(category: str, text: str, fanpage) -> str:
    """The on-card text in the fanpage's language — mirrors ig_recreate
    (news headline rewritten for punch, quote translated faithfully).
    Falls back to the extracted text if the AI returns nothing usable;
    raises on AI failure (the caller leaves the photo unseen)."""
    from app.tasks.ig_recreate import _rewrite_news_title

    if category == "quote":
        out = _localize_quote(text, fanpage)
    elif category == "discussion":
        out = _localize_discussion(text, fanpage)
    else:
        out = _rewrite_news_title(text, fanpage)
    return _clean_line(out) or text


def _record_seen(db, candidate: FacebookPhotoCandidate, item: "_DownloadedPhoto"):
    """Persist the dedup marker for an evaluated photo — accepted OR rejected
    — so no later tick downloads and re-classifies it (a rejected "other"
    photo used to be re-evaluated, with a paid vision call, on every tick).
    Returns the GalleryImage, or None if a concurrent tick already recorded
    it.

    Kept ONLY as a marker: _seen_fbids reads the fbid off every
    facebook_photo row regardless of is_deleted, while is_deleted keeps every
    gallery lookup (they all filter is_deleted=False) from ever offering it
    as a design photo, and the keyword can't substring-match a subject."""
    from app.config import get_settings
    from app.models.gallery import GalleryImage

    s = get_settings()
    gi = GalleryImage(
        keyword=f"facebook_photo:{candidate.fbid}",
        source_image_url=item.source_url,
        local_path=item.local_path,
        public_url=f"{s.storage_base_url.rstrip('/')}/gallery/facebook_photo/{item.filename}",
        width=item.width,
        height=item.height,
        source_engine="facebook_photo",
        label=None,
        is_used=True,
        is_deleted=True,
    )
    db.add(gi)
    try:
        db.commit()
    except IntegrityError:
        # a concurrent tick already recorded this exact photo between our
        # dedup read and this insert — same race handled the same way as
        # pinterest_source.build_idea_from_candidate.
        db.rollback()
        return None
    return gi


def build_idea_from_candidate(db, fanpage, candidate: FacebookPhotoCandidate):
    """Download one candidate photo, classify its text via vision, rewrite
    that text into the fanpage's language, and stage a pending
    FacebookPhotoIdea. The photo itself never reaches a design — it's
    deleted right after being read (render_facebook_photo recreates every
    card around a clean photo of the subject). Returns None on any
    rejection; every rejection except a failed download/classify/rewrite
    call (all transient) is still recorded as seen."""
    from app.config import get_settings
    from app.models.facebook_photo_ideas import FacebookPhotoIdea
    from app.services.facebook_content_classifier import classify_facebook_photo

    s = get_settings()
    niche = (fanpage.mode2_gallery_niches or [None])[0] or fanpage.name
    dest_dir = Path(s.storage_base_path) / "gallery" / "facebook_photo"

    if candidate.fbid in _seen_fbids(db):
        return None

    # No photo-quality gate: only the photo's TEXT is used, and a slightly
    # soft graphic can still be perfectly legible.
    item = _download_photo(candidate.image_url, dest_dir, _MIN_SIZE)
    if not item:
        return None
    image_bytes = Path(item.local_path).read_bytes()
    Path(item.local_path).unlink(missing_ok=True)

    try:
        cls = classify_facebook_photo(image_bytes, niche=niche, topic_filter=fanpage.facebook_photo_topic_filter)
    except Exception as exc:
        logger.warning("Facebook photo: classify failed fbid=%s fanpage %d: %s", candidate.fbid, fanpage.id, exc)
        return None

    ctype = cls["type"]
    fields = _idea_fields(ctype, cls) if cls["on_topic"] else None
    if fields:
        try:
            fields = {**fields, "design_title": _localize_title(ctype, fields["design_title"], fanpage)}
        except Exception as exc:
            logger.warning(
                "Facebook photo: rewrite failed fbid=%s fanpage %d: %s — left unseen, retried next tick",
                candidate.fbid, fanpage.id, exc,
            )
            return None

    gi = _record_seen(db, candidate, item)
    if gi is None:
        return None
    if not fields:
        logger.info(
            "Facebook photo: fbid=%s classified %r%s — skipped",
            candidate.fbid, ctype, "" if cls["on_topic"] else " but off-topic for this fanpage",
        )
        return None

    idea = FacebookPhotoIdea(
        fanpage_id=fanpage.id,
        gallery_image_id=gi.id,
        category=ctype,
        status="pending",
        **fields,
    )
    db.add(idea)
    db.commit()
    logger.info(
        "Facebook photo: fanpage %d — new idea %d (%s) fbid=%s title=%r",
        fanpage.id, idea.id, ctype, candidate.fbid, idea.design_title,
    )
    return idea
