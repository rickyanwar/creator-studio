"""Source the images for a design job's slots.

Single-slot templates just get the main photo. Two-slot templates
(placeholderRole "image" + "image_2", like the manual "GP Today" graphics) also
get a secondary/related photo: 9Router extracts the second subject from the
headline (the rival, other rider, bike/brand…) and we pull a matching photo from
the gallery. If nothing fits, the second slot is left empty (graceful).
"""

import base64
import logging
import os
import re

logger = logging.getLogger(__name__)

# The renderer outputs at DESIGN_SCALE× the design size (crisper, ~2K). Composited
# photos (reflect-extend / fit+blur) are built at the same scale so their detail
# survives the high-res render. Keep in sync with the renderer's `scale` default.
DESIGN_SCALE = int(os.getenv("DESIGN_RENDER_SCALE", "2"))


def template_has_role(template_json, role: str) -> bool:
    return find_role_object(template_json, role) is not None


def find_role_object(template_json, role: str):
    for o in (template_json or {}).get("objects", []):
        if o.get("placeholderRole") == role:
            return o
    return None


def file_to_datauri(path: str) -> str:
    with open(path, "rb") as f:
        return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()


def watermark_datauri(fanpage) -> str | None:
    """A fanpage's watermark LOGO (if set) as a data-URI for the renderer, else
    None so the renderer falls back to the text watermark."""
    url = getattr(fanpage, "watermark_image_url", None)
    if not url:
        return None
    try:
        from app.config import get_settings
        s = get_settings()
        base = (s.storage_base_url or "").rstrip("/")
        path = os.path.join(s.storage_base_path, url[len(base):].lstrip("/")) if base and url.startswith(base) else url
        if os.path.exists(path):
            mime = "png" if path.lower().endswith("png") else "jpeg"
            with open(path, "rb") as f:
                return f"data:image/{mime};base64," + base64.b64encode(f.read()).decode()
    except Exception as exc:
        logger.warning("watermark_datauri failed: %s", exc)
    return None


def fit_with_blur_bg(image_bytes: bytes, target_w: int, target_h: int,
                     top_bias: float = 0.24, pad: float = 0.98,
                     blur: int = 40, darken: float = 0.78) -> bytes | None:
    """Canva-style "fit": show the WHOLE subject (contain, no hard crop) over a
    blurred, darkened cover of the same photo so the frame stays full-bleed.
    The subject is nudged up (top_bias) to stay clear of the bottom text overlay.
    Returns JPEG bytes sized target_w×target_h, or None if PIL/decoding fails."""
    try:
        import io
        from PIL import Image, ImageFilter, ImageEnhance

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        iw, ih = img.size
        # Background: cover-crop → blur → darken.
        sbg = max(target_w / iw, target_h / ih)
        bg = img.resize((max(1, int(iw * sbg)), max(1, int(ih * sbg))))
        l = (bg.width - target_w) // 2
        t = (bg.height - target_h) // 2
        bg = bg.crop((l, t, l + target_w, t + target_h))
        bg = bg.filter(ImageFilter.GaussianBlur(blur))
        bg = ImageEnhance.Brightness(bg).enhance(darken)
        # Foreground: contain the whole subject, nudged up.
        sfg = min(target_w * pad / iw, target_h * pad / ih)
        fw, fh = max(1, int(iw * sfg)), max(1, int(ih * sfg))
        fg = img.resize((fw, fh))
        x = (target_w - fw) // 2
        y = int((target_h - fh) * top_bias)
        bg.paste(fg, (x, y))
        out = io.BytesIO()
        bg.save(out, "JPEG", quality=90)
        return out.getvalue()
    except Exception as exc:
        logger.warning("fit_with_blur_bg failed: %s", exc)
        return None


def reflect_extend(image_bytes: bytes, target_w: int, target_h: int,
                   top_bias: float = 0.16, zoom: float = 1.08, feather: int = 70,
                   blur: int = 26, edge_dark: float = 0.55) -> bytes | None:
    """Fill the frame by fitting the photo to the slot WIDTH (with a slight zoom
    so the subject is more prominent and less filling is needed) and extending the
    empty area with a mirrored continuation of the scene — natural for racing
    shots (track, grass, crowd, barriers) without blur bars.

    Refinements: the photo is raised (top_bias small) so the extension piles up at
    the BOTTOM, under the text overlay where it barely shows; the seam is feathered
    so there's no visible edge; the extension is darkened toward the far edges for
    depth. Returns JPEG bytes, or None on failure."""
    try:
        import io
        import numpy as np
        from PIL import Image, ImageFilter

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        iw, ih = img.size
        scale = (target_w / iw) * zoom
        rw, rh = max(1, round(iw * scale)), max(1, round(ih * scale))
        img = img.resize((rw, rh))
        if rw > target_w:  # zoom overshoots width → centre-crop the sides
            l = (rw - target_w) // 2
            img = img.crop((l, 0, l + target_w, rh))
        arr = np.asarray(img)
        h = arr.shape[0]
        if h >= target_h:  # tall enough → just crop, biased up
            t = int((h - target_h) * top_bias)
            out_img = Image.fromarray(arr[t:t + target_h])
        else:
            pt = int((target_h - h) * top_bias)
            pb = target_h - h - pt
            padded = np.pad(arr, ((pt, pb), (0, 0), (0, 0)), mode="reflect").astype(np.float32)
            blurred = np.asarray(
                Image.fromarray(padded.astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur))
            ).astype(np.float32)
            ys = np.arange(target_h)
            # darken the mirrored extension toward the far top/bottom edges
            d_top = np.clip((pt - ys) / max(pt, 1), 0, 1)
            d_bot = np.clip((ys - (pt + h)) / max(pb, 1), 0, 1)
            blurred *= (1 - np.maximum(d_top, d_bot) * (1 - edge_dark))[:, None, None]
            # feathered seam: sharp photo fades into the extension over `feather` px
            rt = np.clip((ys - pt) / feather, 0, 1)
            rb = np.clip((pt + h - 1 - ys) / feather, 0, 1)
            alpha = np.minimum(rt, rb)[:, None, None]
            sharp = np.zeros((target_h, target_w, 3), np.float32)
            sharp[pt:pt + h] = arr
            res = blurred * (1 - alpha) + sharp * alpha
            out_img = Image.fromarray(np.clip(res, 0, 255).astype(np.uint8))
        out = io.BytesIO()
        out_img.save(out, "JPEG", quality=92)
        return out.getvalue()
    except Exception as exc:
        logger.warning("reflect_extend failed: %s", exc)
        return None


def _has_dominant_face(image_bytes: bytes) -> bool:
    """True when a large face fills the frame (close-up portrait) — mirroring it
    would look wrong, so prefer fit+blur over reflect-extend."""
    try:
        import cv2
        import numpy as np

        arr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return False
        gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, 1.1, 5, minSize=(int(w * 0.06), int(h * 0.06)))
        return any(fw >= 0.22 * w for (_x, _y, fw, _fh) in faces)
    except Exception:
        return False


def smart_expand(image_bytes: bytes, target_w: int, target_h: int) -> bytes | None:
    """Decide per-image whether the frame needs filling and how. Returns the
    composite bytes, or None to leave the photo untouched (plain cover + focus).

    - Source close to (or taller than) the slot aspect → None (cover crops little).
    - Wide/landscape source (would crop heavily) → EXTEND: reflect-extend for
      continuous-background action shots, fit+blur when a face dominates.
    """
    try:
        import io
        from PIL import Image

        iw, ih = Image.open(io.BytesIO(image_bytes)).size
    except Exception:
        return None
    slot_ar = target_w / target_h
    img_ar = iw / ih
    if img_ar < slot_ar * 1.15:  # portrait / near-square → cover is fine
        return None
    if _has_dominant_face(image_bytes):
        return fit_with_blur_bg(image_bytes, target_w, target_h)
    return reflect_extend(image_bytes, target_w, target_h)


def _expand_datauri(datauri: str, target_w: int, target_h: int) -> str:
    """Apply smart_expand to a data-URI image; return the original untouched when
    no expansion is needed or on any failure (graceful)."""
    try:
        raw = base64.b64decode(datauri.split(",", 1)[1])
    except Exception:
        return datauri
    composite = smart_expand(raw, target_w, target_h)
    if not composite:
        return datauri
    return "data:image/jpeg;base64," + base64.b64encode(composite).decode()


def extract_secondary_subject(title: str, niche: str) -> str | None:
    """Ask 9Router for the second subject a side-photo should show."""
    from app.services.ai_caption import generate_caption

    prompt = (
        f'This is a {niche} news headline: "{title}".\n'
        "Name the SECOND subject a related side-photo should show — e.g. the "
        "rival, the other rider, or the bike/brand mentioned — NOT the main "
        "speaker.\n"
        'Reply with just the name/subject (2-4 words), or "NONE" if there is no '
        "clear second subject."
    )
    try:
        out, _ = generate_caption(prompt)
        s = (out or "").strip().strip('".')
        if not s or s.upper() == "NONE" or len(s) > 40:
            return None
        return s
    except Exception as exc:
        logger.warning("Secondary subject extraction failed: %s", exc)
        return None


def detect_focus_point(image_bytes: bytes) -> list:
    """Return [fx, fy] (0..1) of the main subject to focus the crop on — the
    largest detected face, else the salient object, else slightly above centre.
    Vertical is biased UP (smaller fy raises the subject in the crop) because the
    bottom of the card carries the text overlay — we keep the subject clear of it.
    OpenCV runs locally in ~ms (no API)."""
    default = [0.5, 0.34]
    try:
        import cv2
        import numpy as np

        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return default
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                         minSize=(int(w * 0.06), int(h * 0.06)))
        if len(faces):
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            # Trust only clearly-sized faces: gallery measurements show real
            # portraits at ≥0.15 of image width, Haar false positives ≤0.13 —
            # a wrong focus drags the crop into the background, worse than the
            # default. float() — np.float64 is not JSON-serializable.
            if fw >= 0.14 * w:
                cx = (fx + fw / 2) / w
                # Lift the face toward the upper third so it sits above the text
                # overlay (only bites when the source is tall enough to move).
                cy = min(0.44, max(0.24, (fy + fh / 2) / h - 0.08))
                return [round(float(cx), 4), round(float(cy), 4)]

        # No usable face → saliency: focus on the main object/subject (bike,
        # action shot, object). Centre of mass of the salient region, clamped so
        # a noisy map can't push the crop way off.
        try:
            sal = cv2.saliency.StaticSaliencySpectralResidual_create()
            ok, smap = sal.computeSaliency(img)
            if ok:
                smap = (smap * 255).astype("uint8")
                _, th = cv2.threshold(smap, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
                M = cv2.moments(th)
                if M["m00"] > 0:
                    # Horizontal centre of the object is safe to trust. Vertical
                    # is risky — saliency skews low on racing shots (colourful
                    # leathers/bike) and would push the subject into the text
                    # overlay, so blend toward the upper default and keep cy high.
                    cx = min(0.8, max(0.2, M["m10"] / M["m00"] / w))
                    cy = 0.55 * (M["m01"] / M["m00"] / h) + 0.45 * 0.34
                    cy = min(0.46, max(0.26, cy))
                    return [round(float(cx), 4), round(float(cy), 4)]
        except Exception as exc:
            logger.debug("saliency focus failed: %s", exc)
    except Exception as exc:
        logger.warning("detect_focus_point failed: %s", exc)
    return default


def vision_focus_point(image_bytes: bytes) -> list | None:
    """Ask 9Router vision for the MAIN subject's focal point as [x, y] fractions
    (where the crop should centre — usually the face/head). Returns None on any
    problem so the caller can fall back to OpenCV. Runs on 9Router (no VPS cost)."""
    try:
        from openai import OpenAI  # type: ignore
        from app.config import get_settings
        from app.services.nine_router import get_nine_router_config

        cfg = get_nine_router_config()
        if not cfg.base_url:
            return None
        client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key or "sk-9router")
        b64 = base64.b64encode(image_bytes).decode()
        c = client.chat.completions.create(
            model=get_settings().nine_router_vision_model,
            max_tokens=300,
            temperature=0,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": (
                    "This photo is the full-bleed background of a news graphic whose "
                    "headline sits along the BOTTOM. Find the MAIN subject (the "
                    "person's face/head, or the key object). Reply with ONLY a JSON "
                    'object {"x":0.00-1.00,"y":0.00-1.00} — the point the crop should '
                    "centre on so the subject stays fully in frame and clear of the "
                    "bottom text. x is fraction from left, y from top (the face is "
                    "usually in the upper half)."
                )},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
        )
        raw = (c.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        import json as _json
        d = _json.loads(m.group(0))
        x = float(d.get("x")); y = float(d.get("y"))
        if 0 <= x <= 1 and 0 <= y <= 1:
            # keep the subject slightly above centre so it clears the bottom text
            return [round(min(0.85, max(0.15, x)), 4), round(min(0.7, max(0.15, y)), 4)]
    except Exception as exc:
        logger.warning("vision_focus_point failed: %s", exc)
    return None


def focus_points_for(image_srcs: list) -> list:
    """Compute a focus point per data-URI image (for the renderer). When vision
    focus is enabled, the 9Router vision model chooses the point (best for the
    full-bleed portrait templates); otherwise OpenCV face/saliency is used."""
    from app.config import get_settings
    use_vision = get_settings().vision_focus_enabled
    out = []
    for uri in image_srcs:
        try:
            b = base64.b64decode(uri.split(",", 1)[1]) if "," in uri else b""
            if not b:
                out.append([0.5, 0.42]); continue
            fp = vision_focus_point(b) if use_vision else None
            out.append(fp or detect_focus_point(b))
        except Exception:
            out.append([0.5, 0.42])
    return out


def classify_image_type(image_bytes: bytes) -> str:
    """Label a photo at download time: 'face' | 'action' | 'other'.
    face = clear head/upper-body portrait; action = riding/on the bike; other =
    everything else. Runs on 9Router vision (no VPS cost)."""
    try:
        from openai import OpenAI  # type: ignore
        from app.config import get_settings
        from app.services.nine_router import get_nine_router_config

        cfg = get_nine_router_config()
        if not cfg.base_url:
            return "other"
        client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key or "sk-9router")
        b64 = base64.b64encode(image_bytes).decode()
        c = client.chat.completions.create(
            model=get_settings().nine_router_vision_model,
            max_tokens=1500,
            temperature=0,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": (
                    "Label this photo for a sports graphic. Reply ONE word: "
                    "FACE (clear head / upper-body portrait of a person), "
                    "ACTION (a rider on a moving bike / on track), or "
                    "OTHER (anything else)."
                )},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
        )
        raw = (c.choices[0].message.content or "").upper()
        for w in ("FACE", "ACTION", "OTHER"):
            if w in raw:
                return w.lower()
    except Exception as exc:
        logger.warning("classify_image_type failed: %s", exc)
    return "other"


def vision_pick_best(candidates: list, subject: str, image_type: str | None = None) -> int:
    """Ask 9Router vision which candidate photo is best for a news graphic.

    `candidates` is a list of data-URIs. `image_type` ("face" | "action")
    constrains the pick so a split layout gets a consistent style for both
    subjects. Returns the 0-based index, or 0 if vision is unavailable.
    Runs remotely on 9Router — no local GPU/CPU cost on the VPS.
    """
    if len(candidates) <= 1:
        return 0
    if image_type == "face":
        want = f"the clearest close-up FACE / head-and-shoulders portrait of {subject} (calm, looking at or near the camera)"
    elif image_type == "action":
        want = f"the best ACTION shot of {subject} riding/on the bike"
    else:
        want = f"the best, clearest, sharpest photo of {subject} (ideally face/upper body)"
    try:
        from openai import OpenAI  # type: ignore
        from app.config import get_settings
        from app.services.nine_router import get_nine_router_config

        cfg = get_nine_router_config()
        if not cfg.base_url:
            return 0
        client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key or "sk-9router")
        content = [
            {
                "type": "text",
                "text": (
                    f"For a sports news graphic, pick {want}. There are "
                    f"{len(candidates)} images numbered from 1. Reply with ONLY "
                    "the number of the best one."
                ),
            }
        ]
        for uri in candidates:
            content.append({"type": "image_url", "image_url": {"url": uri}})
        completion = client.chat.completions.create(
            model=get_settings().nine_router_vision_model,
            messages=[{"role": "user", "content": content}],
            max_tokens=1500,
            temperature=0,
        )
        raw = (completion.choices[0].message.content or "")
        m = re.search(r"\d+", raw)
        idx = (int(m.group(0)) - 1) if m else 0
        return idx if 0 <= idx < len(candidates) else 0
    except Exception as exc:
        logger.warning("vision_pick_best failed (%s) — using first", exc)
        return 0


def find_gallery_datauri(db, subject: str, exclude_path: str | None = None, use_vision: bool = True, image_type: str | None = None):
    """Find the best (ideally unused) gallery image whose keyword matches the
    subject. When several match, 9Router vision picks the best — constrained to
    `image_type` ("face"/"action") so split layouts stay consistent."""
    from app.models.gallery import GalleryImage

    base = db.query(GalleryImage).filter(GalleryImage.keyword.ilike(f"%{subject.lower()}%"))
    order = (GalleryImage.is_used.asc(), GalleryImage.downloaded_at.desc())

    rows = []
    # Prefer pre-labelled images matching the wanted type (from download-time vision)
    if image_type in ("face", "action"):
        rows = base.filter(GalleryImage.label == image_type).order_by(*order).limit(8).all()
    if not rows:
        rows = base.order_by(*order).limit(8).all()

    usable = [gi for gi in rows if gi.local_path and gi.local_path != exclude_path and os.path.exists(gi.local_path)]
    if not usable:
        return None, None

    uris = [file_to_datauri(gi.local_path) for gi in usable]
    best = vision_pick_best(uris, subject, image_type=image_type) if use_vision else 0
    return uris[best], usable[best]


def pick_split_image_type(title: str, niche: str) -> str:
    """Decide whether a 2-person split should use FACE portraits or ACTION shots.
    Both sides then use the same type for a consistent look."""
    from app.services.ai_caption import generate_caption

    prompt = (
        f'A {niche} news graphic features two riders. Headline: "{title}".\n'
        "Choose the photo style:\n"
        "- FACE: a quote/statement, opinion, contract, rivalry off-track, or "
        "anything personal.\n"
        "- ACTION: on-track performance, a race/qualifying/result, who is fastest "
        "or favorite to win, riding/bike-focused news.\n"
        "Reply with ONLY one word: FACE or ACTION."
    )
    try:
        out, _ = generate_caption(prompt)
        return "action" if "ACTION" in (out or "").upper() else "face"
    except Exception:
        return "face"


def extract_two_subjects(title: str, niche: str):
    """Return (primary, secondary|None). If the headline is clearly about TWO
    distinct people, both are returned → the caller uses a split layout."""
    from app.services.ai_caption import generate_caption

    prompt = (
        f'This is a {niche} news headline: "{title}".\n'
        "Which specific PEOPLE is it about? If it clearly features TWO distinct "
        "named people (e.g. a rider and a rival/other rider), reply exactly as "
        '`Name One | Name Two`. If it is about only ONE main person, reply with '
        "just that one name. People only — no teams/brands. No extra words."
    )
    try:
        out, _ = generate_caption(prompt)
        s = (out or "").strip().strip('".')
        parts = [p.strip() for p in s.split("|") if p.strip()]
        if len(parts) >= 2:
            return parts[0][:40], parts[1][:40]
        return (parts[0][:40] if parts else None), None
    except Exception as exc:
        logger.warning("extract_two_subjects failed: %s", exc)
        return None, None


def fetch_subject_datauri(subject: str, image_type: str = "face", niche: str = "MotoGP", max_candidates: int = 5):
    """Fetch fresh, context-appropriate candidate photos of `subject` straight
    from the Getty search (via 9Router/jina), upscale them, and let vision pick
    the best. `image_type` shapes the query ("face" → portrait, "action" →
    riding). Returns a data-URI or None. Better than the pre-downloaded gallery
    because the query is tailored to the subject + context."""
    from urllib.parse import quote

    import httpx

    from app.config import get_settings
    from app.services.image_downloader import _9router_fetch_markdown, _IMG_URL_RE, _dedup_key, _UA
    from app.services.upscaler import upscale_image_bytes

    s = get_settings()
    query = f"{subject} portrait" if image_type == "face" else f"{subject} {niche}"
    url = s.gallery_search_url_template.format(query=quote(query), page=1)
    try:
        md = _9router_fetch_markdown(url)
    except Exception as exc:
        logger.warning("fetch_subject_datauri: search failed for %r: %s", subject, exc)
        return None

    seen: set = set()
    uris: list[str] = []
    for m in _IMG_URL_RE.finditer(md):
        u = m.group(0)
        k = _dedup_key(u)
        if k in seen:
            continue
        seen.add(k)
        try:
            r = httpx.get(u, headers={"User-Agent": _UA}, timeout=20, follow_redirects=True)
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
                continue
            data = upscale_image_bytes(r.content)
            uris.append("data:image/jpeg;base64," + base64.b64encode(data).decode())
        except Exception:
            continue
        if len(uris) >= max_candidates:
            break

    if not uris:
        return None
    best = vision_pick_best(uris, subject, image_type=image_type)
    logger.info("fetch_subject_datauri: %r (%s) → %d candidates, picked %d", subject, image_type, len(uris), best)
    return uris[best]


def source_news_main(db, title: str, niche: str, use_vision: bool = True):
    """Source the MAIN photo for a recreated news card by its subject — so the
    card shows a clean, relevant photo instead of the original IG screenshot
    (which often carries the source page's own text/branding).

    Order: a matching gallery image we already downloaded (vision-picked) →
    a fresh Getty search if the gallery has nothing good. Returns (datauri, path)
    or (None, None) so the caller can fall back to the IG image.
    """
    primary, _ = extract_two_subjects(title, niche)
    if not primary:
        return None, None
    image_type = pick_split_image_type(title, niche)  # "face" or "action"
    uri, gi = find_gallery_datauri(db, primary, use_vision=use_vision, image_type=image_type)
    if uri:
        logger.info("Recreate main: gallery photo for %r (%s)", primary, image_type)
        return uri, (gi.local_path if gi else None)
    uri = fetch_subject_datauri(primary, image_type, niche)
    if uri:
        logger.info("Recreate main: fresh Getty photo for %r (%s)", primary, image_type)
        return uri, None
    logger.info("Recreate main: no photo for %r — falling back to IG image", primary)
    return None, None


def build_split_srcs(db, primary: str, secondary: str, image_type: str = "face", niche: str = "MotoGP"):
    """Two context-appropriate, vision-picked photos (same style) for a left/right
    split. Fetches fresh from Getty first, falls back to the gallery."""
    left = fetch_subject_datauri(primary, image_type, niche)
    if not left:
        left, lg = find_gallery_datauri(db, primary, use_vision=True, image_type=image_type)
    if not left:
        return None
    right = fetch_subject_datauri(secondary, image_type, niche)
    if not right:
        right, rg = find_gallery_datauri(db, secondary, use_vision=True, image_type=image_type)
    if not right:
        return None
    return [left, right]


def analyze_subject_side(main_datauri: str) -> str:
    """Vision: which side is the main subject on → 'left' | 'right' | 'center'.
    Used to place the secondary inset in the opposite (emptier) area."""
    try:
        from openai import OpenAI  # type: ignore
        from app.config import get_settings
        from app.services.nine_router import get_nine_router_config

        cfg = get_nine_router_config()
        if not cfg.base_url:
            return "center"
        client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key or "sk-9router")
        c = client.chat.completions.create(
            model=get_settings().nine_router_vision_model,
            max_tokens=1500,
            temperature=0,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": (
                    "Where is the main person/subject's head positioned in this "
                    "image? Answer with ONLY one word: LEFT, RIGHT, or CENTER."
                )},
                {"type": "image_url", "image_url": {"url": main_datauri}},
            ]}],
        )
        raw = (c.choices[0].message.content or "").upper()
        for w in ("LEFT", "RIGHT", "CENTER"):
            if w in raw:
                return w.lower()
    except Exception as exc:
        logger.warning("analyze_subject_side failed: %s", exc)
    return "center"


def position_secondary_slot(template_json, side: str, canvas_width: int = 1080):
    """Move the circular image_2 slot (and its ring) to the side opposite the
    main subject, so the inset sits over empty background — not over the face."""
    import copy

    tj = copy.deepcopy(template_json)
    objs = tj.get("objects", [])
    i2 = next((i for i, o in enumerate(objs) if o.get("placeholderRole") == "image_2"), None)
    if i2 is None:
        return tj
    slot = objs[i2]
    if slot.get("type") != "circle":
        return tj  # only auto-move circular insets

    W = canvas_width
    r = slot.get("radius", 180)
    margin = 44
    if side == "right":
        cx = margin + r            # subject right → inset left
    elif side == "left":
        cx = W - margin - r        # subject left → inset right
    else:
        cx = margin + r            # center → default left
    cy = 130 + r

    ocx = slot["left"] + r
    ocy = slot["top"] + r
    dx, dy = cx - ocx, cy - ocy
    slot["left"] += dx
    slot["top"] += dy
    ring = objs[i2 - 1] if i2 - 1 >= 0 and objs[i2 - 1].get("type") == "circle" else None
    if ring is not None:
        ring["left"] += dx
        ring["top"] += dy
    return tj


def prepare_design_images(db, template_json, canvas_width: int, title: str, niche: str,
                          main_datauri: str, main_path: str | None = None, smart: bool = True,
                          expand: bool = False):
    """Source every image the template needs. Returns (template_json, image_srcs)
    — the template may be modified (inset slot repositioned).

    `expand=True` (opt-in toggle) → when the MAIN photo would be hard-cropped to
    fill the slot, fill the frame instead (reflect-extend for action shots,
    fit+blur for close-up faces). Photos that already fit are left untouched.

    `smart=False` (the default OFF toggle) → main photo only, no AI
    secondary/split sourcing. `smart=True`:
    - no image_2 slot → main photo only
    - circular image_2 (inset) → portrait of the secondary subject, and the
      inset is moved to the side opposite the main subject's face
    - rectangular image_2 (split) → two fresh, style-consistent photos of the
      two subjects (both sides FACE or both ACTION)
    Every lookup failure degrades gracefully to fewer images.
    """
    # Reframe the MAIN photo to fill the image slot when it would otherwise be
    # heavily cropped. Done up-front so every path below uses the filled photo.
    if expand:
        img_slot = find_role_object(template_json, "image")
        tw = int((img_slot or {}).get("width", canvas_width) * (img_slot or {}).get("scaleX", 1)) if img_slot else canvas_width
        th = int((img_slot or {}).get("height", 0) * (img_slot or {}).get("scaleY", 1)) if img_slot else 0
        if tw and th:
            # Build the composite at the render scale so photo detail survives.
            main_datauri = _expand_datauri(main_datauri, tw * DESIGN_SCALE, th * DESIGN_SCALE)

    if not smart:
        return template_json, [main_datauri]

    slot = find_role_object(template_json, "image_2")
    if slot is None:
        return template_json, [main_datauri]

    if slot.get("type") in ("circle", "ellipse"):
        # ── Inset flow ──
        subject = extract_secondary_subject(title, niche)
        if not subject:
            logger.info("Design: no secondary subject for %r — image_2 left empty", title[:50])
            return template_json, [main_datauri]
        uri = fetch_subject_datauri(subject, "face", niche)
        if not uri:
            uri, _ = find_gallery_datauri(db, subject, exclude_path=main_path, image_type="face")
        if not uri:
            logger.info("Design: no photo found for secondary subject %r", subject)
            return template_json, [main_datauri]
        side = analyze_subject_side(main_datauri)
        tj = position_secondary_slot(template_json, side, canvas_width)
        logger.info("Design: inset image_2 subject=%r (main subject %s)", subject, side)
        return tj, [main_datauri, uri]

    # ── Split flow (rectangular image_2) ──
    primary, secondary = extract_two_subjects(title, niche)
    if primary and secondary:
        image_type = pick_split_image_type(title, niche)
        srcs = build_split_srcs(db, primary, secondary, image_type, niche)
        if srcs:
            logger.info("Design: split layout %r | %r (%s)", primary, secondary, image_type)
            return template_json, srcs
        logger.info("Design: split sourcing failed for %r | %r — falling back", primary, secondary)

    subject = secondary if (primary and secondary) else extract_secondary_subject(title, niche)
    if not subject:
        logger.info("Design: no secondary subject for %r — image_2 left empty", title[:50])
        return template_json, [main_datauri]
    uri, gi = find_gallery_datauri(db, subject, exclude_path=main_path)
    if not uri:
        logger.info("Design: no gallery image for secondary subject %r", subject)
        return template_json, [main_datauri]
    logger.info("Design: image_2 subject=%r → gallery image %d", subject, gi.id)
    return template_json, [main_datauri, uri]
