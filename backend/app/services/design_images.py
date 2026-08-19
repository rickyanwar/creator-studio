"""Source the images for a design job's slots.

Single-slot templates just get the main photo. Two-slot templates
(placeholderRole "image" + "image_2", like the manual "GP Today" graphics) also
get a secondary/related photo: 9Router extracts the second subject from the
headline (the rival, other rider/driver, bike/car/brand…) and we pull a matching photo from
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


def _vision_datauri(image_bytes: bytes, max_dim: int = 768, quality: int = 78) -> str:
    """Downscaled JPEG data-URI for a 9Router vision call — the gallery/upscaled
    source photos this app renders with can be multi-megapixel, and bundling
    several of them in one request (vision_pick_best compares up to 8) blows
    past the router's request-size limit (413). Vision only needs to compare
    compositions, not full render resolution, so this is used for the AI call
    only — callers keep the original bytes for the actual design."""
    try:
        import io
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        # Fall back to the original bytes — worst case the call 413s and the
        # caller's own except-and-fall-back-to-first-candidate logic kicks in.
        return "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()


# Vision-capable 9Router models, in fallback order. The configured
# nine_router_vision_model is tried first; these are the backups if it errors
# or comes back empty. Deliberately long (user preference, 2026-08-16: use as
# many 9Router fallbacks as possible — these are background Celery tasks, so
# trading worst-case latency for reliability is the right call) — but NOT a
# free-for-all: see config.py's nine_router_vision_model comment for models
# confirmed broken for vision on this router and deliberately excluded here —
# "ag/claude-*" silently ignores the image ("no photo attached"), plain
# "ag/gemini-3-flash" (not the "-agent" variant) replies empty,
# "ag/gemini-3.1-flash-image" hallucinates a description instead of reading
# the photo. Everything below is either verified (the first four) or the same
# Gemini-flash family/architecture as the verified ones.
_VISION_MODEL_FALLBACKS = [
    "ag/gemini-3.5-flash-low",
    "ag/gemini-pro-agent",
    "ag/gemini-3.1-pro-low",
    "ag/gemini-3-flash-agent",
    "ag/gemini-3.6-flash-medium",
    "ag/gemini-3.6-flash-low",
    "ag/gemini-3.5-flash-extra-low",
]


def _vision_models() -> list[str]:
    from app.config import get_settings

    primary = get_settings().nine_router_vision_model
    ordered = [primary] + [m for m in _VISION_MODEL_FALLBACKS if m != primary]
    seen = set()
    return [m for m in ordered if m and not (m in seen or seen.add(m))]


def _vision_chat(content: list, max_tokens: int = 1500, temperature: float = 0, context: str = "vision") -> str:
    """Chat-completion call against 9Router, trying each vision model in
    `_vision_models()` in turn until one returns a non-empty response — a
    single flaky/overloaded/image-blind model shouldn't take down image
    selection. Raises the last error if every model fails.

    Some fallback models (e.g. ag/gemini-3.5-flash-low) always run hidden
    reasoning that counts against max_tokens — the same failure mode that
    silently truncated Mode 2's news copy (see ai_caption.py's
    ROUTER_MODEL_FALLBACKS). Callers should budget generously for it.
    Every outcome is logged to ai_copy_events so vision's health shows up on
    the dashboard alongside the text-copy success rate.
    """
    import time
    from openai import OpenAI  # type: ignore
    from app.services.ai_caption import log_ai_copy_event
    from app.services.nine_router import get_nine_router_config

    cfg = get_nine_router_config()
    if not cfg.base_url:
        raise RuntimeError("9Router base URL not configured")
    # Explicit timeout — without it a hung (not erroring) model blocks on the
    # SDK's default for way too long, and that cost repeats per fallback model.
    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key or "sk-9router", timeout=30.0)

    t0 = time.monotonic()
    models_tried: list[str] = []
    last_exc: Exception | None = None
    for model in _vision_models():
        models_tried.append(model)
        try:
            c = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=30.0,
            )
            text = (c.choices[0].message.content or "").strip()
            if text:
                log_ai_copy_event(
                    context=context, fanpage_id=None, article_id=None,
                    outcome="success" if len(models_tried) == 1 else "recovered",
                    models_tried=models_tried, final_provider=model,
                    error_message=str(last_exc) if last_exc else None,
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return text
            last_exc = RuntimeError(f"{model}: empty response")
        except Exception as exc:
            last_exc = exc
            logger.debug("vision model %s failed: %s", model, exc)

    log_ai_copy_event(
        context=context, fanpage_id=None, article_id=None, outcome="failed",
        models_tried=models_tried, final_provider=None,
        error_message=str(last_exc) if last_exc else "no vision models configured",
        latency_ms=int((time.monotonic() - t0) * 1000),
    )
    raise last_exc or RuntimeError("no vision models configured")


def niche_keywords(db, niches: list[str]) -> list[str]:
    """Resolve a fanpage's gallery niches (e.g. ["MotoGP"]) to the lowercased
    keyword strings of every active GalleryKeyword under those niches — the
    candidate pool for image matching. Replaces manually curating individual
    keywords per fanpage: add a keyword under a niche once, every fanpage
    subscribed to that niche picks it up automatically."""
    if not niches:
        return []
    from app.models.gallery import GalleryKeyword

    rows = (
        db.query(GalleryKeyword.keyword)
        .filter(GalleryKeyword.niche.in_(niches), GalleryKeyword.is_active == True)
        .all()
    )
    return [k.lower() for (k,) in rows]


def resolve_template(db, category: str, fanpage=None, job_template_id: int | None = None):
    """Cascade shared by every content pipeline that renders a design:
    job override → fanpage's default_{category}_template_id → shared
    (is_default) template tagged with this category, fanpage-specific
    override preferred over global.

    category: "quote" | "news" | "discussion". A "news"-category template serves
    both Mode 2 news_content jobs and Mode 3 ig_recreate posts classified "news";
    "quote" serves ig_recreate posts classified "quote"; "discussion" serves
    Mode 4 debate cards — one global pool per category instead of separate
    per-mode template fields."""
    from app.models.design_templates import DesignTemplate

    if job_template_id:
        t = db.query(DesignTemplate).filter_by(id=job_template_id).first()
        if t:
            return t

    if fanpage is not None:
        fp_field = {
            "quote": "default_quote_template_id",
            "discussion": "default_discussion_template_id",
        }.get(category, "default_news_template_id")
        fp_template_id = getattr(fanpage, fp_field, None)
        if fp_template_id:
            t = db.query(DesignTemplate).filter_by(id=fp_template_id).first()
            if t:
                return t

    q = db.query(DesignTemplate).filter(DesignTemplate.is_default == True, DesignTemplate.category == category)
    q = q.filter((DesignTemplate.fanpage_id == fanpage.id) | (DesignTemplate.fanpage_id.is_(None))) if fanpage is not None else q.filter(DesignTemplate.fanpage_id.is_(None))
    return q.order_by(DesignTemplate.fanpage_id.desc().nullslast()).first()


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
                     blur: int = 40, darken: float = 0.78, feather: int = 220,
                     face_bbox: tuple[float, float, float, float] | None = None,
                     face_zoom: float = 5.5) -> bytes | None:
    """Canva-style "fit": show the subject (contain, no hard crop) over a
    blurred, darkened cover of the same photo so the frame stays full-bleed.
    The subject is nudged up (top_bias) to stay clear of the bottom text overlay.
    The sharp foreground's bottom edge is feathered into the blurred/darkened
    background beneath it (rather than pasted as a hard rectangle) so it reads
    as one continuous photo behind the text overlay, not a visible seam.

    face_bbox (fx, fy, fw, fh — normalized fractions, from _dominant_face_bbox):
    when given, the foreground is first cropped to a window centered on the
    face (sized so the face reads at a natural ~1/face_zoom of the crop's
    height) instead of containing the WHOLE original photo. Without this, a
    photo where the subject is already a small part of the frame ends up
    looking small and distant even after "fit" — and centering the whole
    photo (rather than the face) leaves an off-center subject off-center.
    Omit face_bbox to keep the old whole-photo behavior (still used for the
    extreme-close-up case, which needs headroom, not a further zoom-in).

    Returns JPEG bytes sized target_w×target_h, or None if PIL/decoding fails."""
    try:
        import io
        import numpy as np
        from PIL import Image, ImageFilter, ImageEnhance

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        iw, ih = img.size
        # Background: cover-crop → blur → darken (always the full original photo).
        sbg = max(target_w / iw, target_h / ih)
        bg = img.resize((max(1, int(iw * sbg)), max(1, int(ih * sbg))))
        l = (bg.width - target_w) // 2
        t = (bg.height - target_h) // 2
        bg = bg.crop((l, t, l + target_w, t + target_h))
        bg = bg.filter(ImageFilter.GaussianBlur(blur))
        bg = ImageEnhance.Brightness(bg).enhance(darken)

        # Foreground source: the whole photo, or a tighter window zoomed/
        # centered on the face so the subject reads at a natural size.
        subject = img
        if face_bbox:
            fx, fy, ffw, ffh = face_bbox
            cx, cy = (fx + ffw / 2) * iw, (fy + ffh / 2) * ih
            crop_h = min(ih, max(1, (ffh * ih) * face_zoom))
            crop_w = min(iw, crop_h * (target_w / target_h))
            crop_h = crop_w * (target_h / target_w)  # re-lock aspect after width clamp
            left = min(max(0, cx - crop_w / 2), iw - crop_w)
            top_ = min(max(0, cy - crop_h / 2), ih - crop_h)
            subject = img.crop((int(left), int(top_), int(left + crop_w), int(top_ + crop_h)))

        siw, sih = subject.size
        sfg = min(target_w * pad / siw, target_h * pad / sih)
        fw, fh = max(1, int(siw * sfg)), max(1, int(sih * sfg))
        fg = subject.resize((fw, fh))
        x = (target_w - fw) // 2
        y = int((target_h - fh) * top_bias)

        bg_arr = np.asarray(bg).astype(np.float32)
        fg_arr = np.asarray(fg).astype(np.float32)
        f = min(feather, fh)
        alpha = np.ones(fh, dtype=np.float32)
        if f > 0:
            alpha[-f:] = np.linspace(1, 0, f)
        alpha3 = alpha[:, None, None]
        region = bg_arr[y:y + fh, x:x + fw]
        bg_arr[y:y + fh, x:x + fw] = fg_arr * alpha3 + region * (1 - alpha3)
        out_img = Image.fromarray(np.clip(bg_arr, 0, 255).astype(np.uint8))
        out = io.BytesIO()
        out_img.save(out, "JPEG", quality=90)
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


def _dominant_face_bbox(image_bytes: bytes) -> tuple[float, float, float, float] | None:
    """Largest detected face as normalized (fx, fy, fw, fh) fractions of the
    image, or None if no face found. Used both to decide "does a face dominate
    the frame" and, via fh/face-bottom, "does it reach too far down for a
    bottom text overlay" (an extreme close-up)."""
    try:
        import cv2
        import numpy as np

        arr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return None
        gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, 1.1, 5, minSize=(int(w * 0.06), int(h * 0.06)))
        if len(faces) == 0:
            return None
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        return (fx / w, fy / h, fw / w, fh / h)
    except Exception:
        return None


def smart_expand(image_bytes: bytes, target_w: int, target_h: int) -> bytes | None:
    """Decide per-image whether the frame needs filling and how. Returns the
    composite bytes, or None to leave the photo untouched (plain cover + focus).

    - Extreme close-up (face/chin already reaches deep into the lower frame) →
      fit+blur regardless of aspect ratio: a big bottom text overlay (e.g. a
      Quote Card) would land on the face otherwise, and a plain cover-crop
      can only pan, not shrink the subject to make room. face_zoom is large
      here so the crop clamps to the whole photo (we need HEADROOM, not a
      further zoom-in) — just centered on the face, not the photo's geometry.
    - Source close to (or taller than) the slot aspect → None (cover crops little).
    - Wide/landscape source with a detected face → fit+blur, zoomed/centered
      on the face (rather than reflect_extend's plain whole-photo centre-crop,
      which has no notion of where the face actually is and can leave it
      small and off-centre).
    - Wide/landscape, no face at all → reflect-extend (continuous-background
      action shots — track, grass, crowd; nothing that needs face-centring).
    """
    try:
        import io
        from PIL import Image

        iw, ih = Image.open(io.BytesIO(image_bytes)).size
    except Exception:
        return None

    slot_ar = target_w / target_h
    img_ar = iw / ih
    face = _dominant_face_bbox(image_bytes)

    if face and (face[3] >= 0.32 or face[1] + face[3] >= 0.78):
        # pad=0.98 (near-full-bleed) leaves almost no vertical slack for
        # top_bias to redistribute, so the subject lands wherever it falls —
        # often right in the bottom text zone. A smaller pad deliberately
        # shrinks the fitted photo so there's real slack, and a low top_bias
        # pushes nearly all of it to the BOTTOM (under the text), clearing
        # the face/chin.
        return fit_with_blur_bg(
            image_bytes, target_w, target_h, face_bbox=face, face_zoom=50,
            pad=0.78, top_bias=0.04,
        )

    if img_ar < slot_ar * 1.15:  # portrait / near-square → cover is fine
        return None
    if face:
        return fit_with_blur_bg(image_bytes, target_w, target_h, face_bbox=face)
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
        "rival, the other rider/driver, or the bike/car/brand mentioned — NOT "
        "the main speaker.\n"
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
        # x/y are fractions of the frame, so a downscaled copy is fine here —
        # and keeps the request under the router's size limit (see _vision_datauri).
        datauri = _vision_datauri(image_bytes)
        content = [
            {"type": "text", "text": (
                "This photo is the full-bleed background of a news graphic whose "
                "headline sits along the BOTTOM. Find the MAIN subject (the "
                "person's/vehicle's key point — a face/head, or a car/bike). "
                'Reply with ONLY a JSON object {"x":0.00-1.00,"y":0.00-1.00} — the '
                "point the crop should centre on so the subject stays fully in "
                "frame and clear of the bottom text. x is fraction from left, y "
                "from top (the subject is usually in the upper half)."
            )},
            {"type": "image_url", "image_url": {"url": datauri}},
        ]
        raw = _vision_chat(content, max_tokens=1500, context="vision_focus_point").strip()
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
    face = clear head/upper-body portrait; action = riding/driving/on track;
    other = everything else. Runs on 9Router vision (no VPS cost)."""
    try:
        content = [
            {"type": "text", "text": (
                "Label this photo for a sports graphic. Reply ONE word: "
                "FACE (clear head / upper-body portrait of a person), "
                "ACTION (a rider/driver on a moving bike/car / on track), or "
                "OTHER (anything else)."
            )},
            {"type": "image_url", "image_url": {"url": _vision_datauri(image_bytes)}},
        ]
        raw = _vision_chat(content, max_tokens=1500, context="vision_classify_type").upper()
        for w in ("FACE", "ACTION", "OTHER"):
            if w in raw:
                return w.lower()
    except Exception as exc:
        logger.warning("classify_image_type failed: %s", exc)
    return "other"


def classify_and_gate_image(image_bytes: bytes, subject: str | None = None) -> tuple[str, bool]:
    """Download-time vision call: label the photo (face/action/other) AND
    judge whether it's actually usable as a design background — one vision
    call doing both jobs (replaces classify_image_type on the download path)
    so the quality gate doesn't cost a second call per image.

    "Usable" rejects the obvious junk that slips through a keyword search —
    generic crowd/stage/logo shots, screenshots, graphics/text overlays, or
    photos where the subject is tiny/blurry/mostly obstructed — not a strict
    editorial judgment call. Fails OPEN (label="other", usable=True) on any
    error: a flaky vision call should reduce to today's behavior (unlabeled,
    unfiltered), never block a download outright."""
    try:
        subject_line = f" The subject should be {subject}." if subject else ""
        content = [
            {"type": "text", "text": (
                "This photo is a candidate for a sports/news graphic background."
                f"{subject_line}\n"
                '1) "label": ONE word — FACE (clear head/upper-body portrait), '
                "ACTION (riding/driving/on track), or OTHER (anything else).\n"
                '2) "usable": true/false — is this a real, reasonably sharp, '
                "usable photo where the subject is clearly visible and not "
                "mostly obstructed, cropped out? Reply false for a generic "
                "crowd/stage/logo-only shot, a screenshot, a graphic with "
                "text overlays, or a photo where the subject is tiny/blurry.\n"
                'Reply with ONLY a JSON object {"label": "FACE|ACTION|OTHER", "usable": true|false}.'
            )},
            {"type": "image_url", "image_url": {"url": _vision_datauri(image_bytes)}},
        ]
        raw = _vision_chat(content, max_tokens=1500, context="vision_download_gate")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return "other", True
        import json as _json
        d = _json.loads(m.group(0))
        label = str(d.get("label") or "OTHER").strip().upper()
        if label not in ("FACE", "ACTION", "OTHER"):
            label = "OTHER"
        usable = bool(d.get("usable", True))
        return label.lower(), usable
    except Exception as exc:
        logger.warning("classify_and_gate_image failed: %s", exc)
        return "other", True


def classify_closeup_match(image_bytes: bytes, criteria: str) -> dict:
    """Ask 9Router vision whether a gallery photo matches an admin-typed
    filter criteria (e.g. "close-up headshot of a person's face, not
    full-body/action/crowd shots") — used by the manual, on-demand "Run AI
    Filter" tool on the Gallery page (see
    tasks/gallery_downloader.scan_gallery_closeup_filter), which scans the
    whole gallery or one keyword and returns non-matches for the admin to
    review before anything is deleted.

    Returns {"match": bool, "confidence": 0.0-1.0}. Fails OPEN (match=True) on
    any parse/API error, same as vision_verify_match — a flaky vision call
    should never get an image flagged for deletion."""
    try:
        content = [
            {"type": "text", "text": (
                f"Filter criteria for this photo gallery: {criteria.strip()}\n"
                "Does this photo satisfy that criteria?\n"
                'Reply with ONLY a JSON object {"match": true|false, "confidence": 0.00-1.00}.'
            )},
            {"type": "image_url", "image_url": {"url": _vision_datauri(image_bytes)}},
        ]
        raw = _vision_chat(content, max_tokens=1500, context="vision_classify_closeup")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {"match": True, "confidence": 0.0}
        import json as _json
        d = _json.loads(m.group(0))
        return {"match": bool(d.get("match")), "confidence": float(d.get("confidence") or 0)}
    except Exception as exc:
        logger.warning("classify_closeup_match failed: %s", exc)
        return {"match": True, "confidence": 0.0}


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
        want = f"the best ACTION shot of {subject} riding/driving — on the bike/car or on track"
    else:
        want = f"the best, clearest, sharpest photo of {subject} (ideally face/upper body)"
    try:
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
            try:
                raw = base64.b64decode(uri.split(",", 1)[1])
                small_uri = _vision_datauri(raw)
            except Exception:
                small_uri = uri
            content.append({"type": "image_url", "image_url": {"url": small_uri}})
        raw = _vision_chat(content, max_tokens=1500, context="vision_pick_best")
        m = re.search(r"\d+", raw)
        idx = (int(m.group(0)) - 1) if m else 0
        return idx if 0 <= idx < len(candidates) else 0
    except Exception as exc:
        logger.warning("vision_pick_best failed (%s) — using first", exc)
        return 0


def vision_verify_match(image_bytes: bytes, title: str, excerpt: str = "") -> dict:
    """Ask 9Router vision whether a candidate photo actually depicts this news
    story (same person(s)/team/vehicle/event) rather than a generic or
    unrelated stock photo. Used to gate the article's scraped hero image and
    fresh topic-search results before trusting them — see
    design_renderer.select_image_for_job and fetch_topic_datauri.

    Returns {"match": bool, "confidence": 0.0-1.0}. Fails OPEN (match=True) on
    any parse/API error — a flaky vision call shouldn't block the whole
    pipeline, only an explicit "no" from the model should veto the image."""
    try:
        content = [
            {"type": "text", "text": (
                f'News headline: "{title[:200]}"\n'
                + (f'Article excerpt: "{excerpt[:400]}"\n' if excerpt else "")
                + "Does this photo actually depict THIS story's subject — the "
                "same named person(s), team, vehicle, or event/scene the "
                "headline is about? A generic/unrelated stock photo, or a "
                "photo of a clearly different person or team, is NOT a match.\n"
                'Reply with ONLY a JSON object {"match": true|false, "confidence": 0.00-1.00}.'
            )},
            {"type": "image_url", "image_url": {"url": _vision_datauri(image_bytes)}},
        ]
        raw = _vision_chat(content, max_tokens=1500, context="vision_verify_match")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {"match": True, "confidence": 0.0}
        import json as _json
        d = _json.loads(m.group(0))
        return {"match": bool(d.get("match")), "confidence": float(d.get("confidence") or 0)}
    except Exception as exc:
        logger.warning("vision_verify_match failed: %s", exc)
        return {"match": True, "confidence": 0.0}


# A photo shouldn't reappear across a fanpage's feed too often (user
# feedback, 2026-08-16: same image was showing up far too frequently at the
# old 2-day cooldown). Randomized per pick within [MIN, MAX] rather than a
# fixed constant, so reuse timing doesn't fall into a predictable cadence.
GALLERY_REUSE_COOLDOWN_MIN_DAYS = 14
GALLERY_REUSE_COOLDOWN_MAX_DAYS = 28


def _eligible_rows(base_query, limit: int = 8, allow_stale_reuse: bool = True):
    """Candidate pool respecting the reuse cooldown: images never used, or not
    used within a randomized 14-28 day window, are eligible. Ordered by
    captured_at (the real-world shot date parsed from the Getty caption —
    see image_downloader._parse_caption_date) descending first, so the
    freshest-dated photos fill the pool find_gallery_datauri's vision pick
    chooses from — a photo from this week's race beats one from last month
    whenever both are eligible. Images with no parsed date (older rows from
    before captured_at existed, or non-Getty sources) sort last, not
    excluded. Random within same-date/no-date ties, not always the newest
    *download* — the same photo still shouldn't reappear every run.

    `allow_stale_reuse=True` (default) falls back to the least-recently-used
    images when the cooldown pool is empty, so a small keyword/niche pool
    doesn't just stop producing images — this is the right call for a caller
    with no fresher source of its own. Callers that DO have a fresher
    fallback (a live Getty/Google search) should pass
    `allow_stale_reuse=False` so an empty cooldown pool comes back empty here
    too instead of silently handing back a stale-but-technically-available
    photo — that emptiness is what makes the caller actually try the fresh
    search instead of skipping straight to reuse. Call again with
    `allow_stale_reuse=True` afterward as the true last resort if the fresh
    search also comes up empty."""
    import random
    from datetime import datetime, timedelta

    from sqlalchemy import func
    from app.models.gallery import GalleryImage

    cooldown_days = random.randint(GALLERY_REUSE_COOLDOWN_MIN_DAYS, GALLERY_REUSE_COOLDOWN_MAX_DAYS)
    cutoff = datetime.utcnow() - timedelta(days=cooldown_days)
    rows = (
        base_query.filter(
            (GalleryImage.last_used_at.is_(None)) | (GalleryImage.last_used_at < cutoff)
        )
        .order_by(GalleryImage.captured_at.desc().nullslast(), func.random())
        .limit(limit)
        .all()
    )
    if rows or not allow_stale_reuse:
        return rows
    # Cooldown pool exhausted (e.g. a niche with very few photos) — reuse the
    # one used longest ago rather than refuse to produce an image at all.
    return (
        base_query.order_by(GalleryImage.last_used_at.asc().nullsfirst())
        .limit(limit)
        .all()
    )


def _mark_gallery_image_used(db, gi) -> None:
    from datetime import datetime

    gi.is_used = True
    gi.last_used_at = datetime.utcnow()
    db.commit()


def find_gallery_datauri(
    db, subject: str, exclude_path: str | None = None, use_vision: bool = True,
    image_type: str | None = None, allow_stale_reuse: bool = True,
):
    """Find the best gallery image whose keyword matches the subject, honoring
    the reuse cooldown and favoring the freshest-shot (captured_at) eligible
    candidates — see _eligible_rows. When several match, 9Router vision picks
    the best among that freshest-first pool — constrained to `image_type`
    ("face"/"action") so split layouts stay consistent. Marks the picked
    image used.

    `allow_stale_reuse=False` makes this return (None, None) when the cooldown
    pool is empty instead of reusing a stale photo — pass this when the
    caller has a fresher fallback (a live Getty/Google search) it should try
    first, then call this again with the default True as the true last
    resort if that fresh search also finds nothing."""
    from sqlalchemy import func, or_
    from app.models.gallery import GalleryImage

    needle = f"%{subject.lower()}%"
    base = db.query(GalleryImage).filter(
        # A photo can feature more than one person — match either the primary
        # keyword it was downloaded under or any additional tag on the image.
        or_(
            GalleryImage.keyword.ilike(needle),
            func.array_to_string(GalleryImage.extra_keywords, ",").ilike(needle),
        ),
        GalleryImage.is_deleted == False,
    )

    rows = []
    # Prefer pre-labelled images matching the wanted type (from download-time vision)
    if image_type in ("face", "action"):
        rows = _eligible_rows(base.filter(GalleryImage.label == image_type), allow_stale_reuse=allow_stale_reuse)
    if not rows:
        rows = _eligible_rows(base, allow_stale_reuse=allow_stale_reuse)

    usable = [gi for gi in rows if gi.local_path and gi.local_path != exclude_path and os.path.exists(gi.local_path)]
    if not usable:
        return None, None

    uris = [file_to_datauri(gi.local_path) for gi in usable]
    best = vision_pick_best(uris, subject, image_type=image_type) if use_vision else 0
    _mark_gallery_image_used(db, usable[best])
    return uris[best], usable[best]


def pick_split_image_type(title: str, niche: str) -> str:
    """Decide whether a 2-person split should use FACE portraits or ACTION shots.
    Both sides then use the same type for a consistent look."""
    from app.services.ai_caption import generate_caption

    prompt = (
        f'A {niche} news graphic features two riders/drivers. Headline: "{title}".\n'
        "Choose the photo style:\n"
        "- FACE: a quote/statement, opinion, contract, rivalry off-track, or "
        "anything personal.\n"
        "- ACTION: on-track performance, a race/qualifying/result, who is fastest "
        "or favorite to win, riding/driving-focused news.\n"
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
        "named people (e.g. a rider/driver and a rival/teammate), reply exactly as "
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


def fetch_subject_datauri(db, subject: str, image_type: str = "face", niche: str = "MotoGP", max_candidates: int = 5):
    """Fetch fresh, context-appropriate candidate photos of `subject` straight
    from the Getty search (via 9Router/jina), store the ones that pass the
    same quality gate the scheduled downloader uses (dest keyword = the
    subject, lowercased), and let vision pick the best of what's usable.
    `image_type` shapes the query ("face" → portrait, "action" → riding).
    Returns a data-URI or None.

    Storing every candidate (not just the winner) — rather than the old
    behavior of returning a bare in-memory data-URI — fixes a real bug found
    2026-08-17: two different fanpages independently searching the identical
    subject name minutes apart (same source article, different fanpage
    caption) both landed on Getty's identical newest-first top result, so the
    same photo went out on both pages. Persisting to gallery_images means the
    second search's skip_urls (same as the scheduled downloader's dedup)
    naturally excludes whatever the first one already claimed. The unused
    candidates become free bonus stock for later picks instead of being
    discarded, too."""
    from urllib.parse import quote
    from pathlib import Path

    from sqlalchemy.exc import IntegrityError
    from app.config import get_settings
    from app.models.gallery import GalleryImage
    from app.services.image_downloader import (
        _9router_fetch_markdown, _IMG_URL_RE, _fetch_and_store, keyword_slug,
    )

    s = get_settings()
    keyword = subject.strip().lower()
    # Always keep the niche in the query, even for portraits — a bare surname
    # (e.g. "Guevara") can collide with a far more famous unrelated person
    # (Che Guevara) in stock-photo search results without it. "press" on a
    # face query steers Getty toward press-conference/paddock closeups
    # (clean, forward-facing) rather than wide/candid shots.
    query = f"{subject} {niche} portrait press" if image_type == "face" else f"{subject} {niche}"
    url = s.gallery_search_url_template.format(query=quote(query), page=1)
    try:
        md = _9router_fetch_markdown(url, context="subject_datauri", keyword=keyword, niche=niche)
    except Exception as exc:
        logger.warning("fetch_subject_datauri: search failed for %r: %s", subject, exc)
        return None

    candidate_urls: list[str] = []
    seen: set = set()
    for m in _IMG_URL_RE.finditer(md):
        u = m.group(0)
        if u in seen:
            continue
        seen.add(u)
        candidate_urls.append(u)
    if not candidate_urls:
        return None

    skip_urls = {
        u for (u,) in
        db.query(GalleryImage.source_image_url).filter(GalleryImage.keyword == keyword).all()
    }
    dest_dir = Path(s.storage_base_path) / "gallery" / keyword_slug(keyword)
    saved = _fetch_and_store(candidate_urls, dest_dir, max_candidates, (300, 300), skip_urls, "9router-live", subject=subject)
    if not saved:
        return None

    survivors: list[tuple] = []  # (GalleryImage, datauri)
    for item in saved:
        gi = GalleryImage(
            keyword=keyword,
            source_image_url=item.source_url,
            local_path=item.local_path,
            public_url=f"{s.storage_base_url.rstrip('/')}/gallery/{keyword_slug(keyword)}/{item.filename}",
            width=item.width,
            height=item.height,
            source_engine=item.engine,
            label=item.label,
        )
        db.add(gi)
        try:
            db.commit()
        except IntegrityError:
            # a concurrent job (this subject, another fanpage) claimed this
            # exact photo between our skip_urls read and this insert.
            db.rollback()
            Path(item.local_path).unlink(missing_ok=True)
            continue
        survivors.append((gi, file_to_datauri(item.local_path)))

    if not survivors:
        return None

    uris = [uri for _, uri in survivors]
    best = vision_pick_best(uris, subject, image_type=image_type)
    picked_gi, picked_uri = survivors[best]
    _mark_gallery_image_used(db, picked_gi)
    logger.info(
        "fetch_subject_datauri: %r (%s) → %d candidate(s) stored, picked %d",
        subject, image_type, len(survivors), best,
    )
    return picked_uri


def fetch_topic_datauri(title: str, niche: str, excerpt: str = "", max_candidates: int = 6):
    """Last-resort fresh search when neither a subject-specific photo nor a
    gallery keyword match was found: search stock photos for the article's
    TOPIC itself (the whole headline, not a named person) — Getty first, a
    Google Images search as a second source if Getty comes back empty — and
    keep only the candidates 9Router vision confirms actually match this
    story (see vision_verify_match). An unrelated stock photo is worse than
    falling through to the article's own (also vision-checked) hero image, so
    this returns None rather than guess if nothing verifies.
    """
    from urllib.parse import quote

    import httpx

    from app.config import get_settings
    from app.services.image_downloader import (
        _9router_fetch_markdown, _IMG_URL_RE, _MD_IMG_ANY_RE, _dedup_key, _UA,
    )
    from app.services.upscaler import upscale_image_bytes

    s = get_settings()
    query = f"{title} {niche}".strip()[:150]

    def _collect(url: str, pattern, group: int) -> list[str]:
        try:
            md = _9router_fetch_markdown(url, context="topic_datauri", keyword=query[:128], niche=niche)
        except Exception as exc:
            logger.warning("fetch_topic_datauri: fetch failed for %r: %s", query, exc)
            return []
        seen: set = set()
        urls: list[str] = []
        for m in pattern.finditer(md):
            u = m.group(group)
            k = _dedup_key(u)
            if k in seen:
                continue
            seen.add(k)
            urls.append(u)
        return urls

    candidate_urls = _collect(s.gallery_search_url_template.format(query=quote(query), page=1), _IMG_URL_RE, 0)
    if not candidate_urls:
        candidate_urls = _collect(s.gallery_search_url_template_google.format(query=quote(query)), _MD_IMG_ANY_RE, 1)

    verified: list[str] = []
    checked = 0
    for u in candidate_urls:
        if checked >= max_candidates * 2 or len(verified) >= max_candidates:
            break
        try:
            r = httpx.get(u, headers={"User-Agent": _UA}, timeout=20, follow_redirects=True)
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
                continue
            data = upscale_image_bytes(r.content)
        except Exception:
            continue
        checked += 1
        check = vision_verify_match(data, title, excerpt)
        if check["match"]:
            verified.append("data:image/jpeg;base64," + base64.b64encode(data).decode())

    if not verified:
        logger.info("fetch_topic_datauri: no verified match for %r among %d checked", query, checked)
        return None
    best = vision_pick_best(verified, title[:60])
    logger.info("fetch_topic_datauri: %r → %d verified candidates, picked %d", query, len(verified), best)
    return verified[best]


def source_news_main(db, title: str, niche: str, use_vision: bool = True, exclude_path: str | None = None):
    """Source the MAIN photo for a recreated news card by its subject — so the
    card shows a clean, relevant photo instead of the original IG screenshot
    (which often carries the source page's own text/branding).

    Order: a cooldown-fresh gallery image we already downloaded (vision-
    picked) → a fresh Getty search → a stale (cooldown-expired) gallery photo
    as the true last resort, rather than reusing one within its cooldown just
    because a fresh search also came up empty. Returns (datauri, path) or
    (None, None) so the caller can fall back to the IG image.

    `exclude_path` (a GalleryImage.local_path) is forwarded to
    find_gallery_datauri so a History "Re-edit with new image" retry can rule
    out the exact photo it picked last time.
    """
    primary, _ = extract_two_subjects(title, niche)
    if not primary:
        return None, None
    image_type = pick_split_image_type(title, niche)  # "face" or "action"
    uri, gi = find_gallery_datauri(
        db, primary, exclude_path=exclude_path, use_vision=use_vision, image_type=image_type,
        allow_stale_reuse=False,
    )
    if uri:
        logger.info("Recreate main: fresh gallery photo for %r (%s)", primary, image_type)
        return uri, (gi.local_path if gi else None)
    uri = fetch_subject_datauri(db, primary, image_type, niche)
    if uri:
        logger.info("Recreate main: fresh Getty photo for %r (%s)", primary, image_type)
        return uri, None
    uri, gi = find_gallery_datauri(
        db, primary, exclude_path=exclude_path, use_vision=use_vision, image_type=image_type,
    )
    if uri:
        logger.info("Recreate main: stale gallery photo for %r (%s) — nothing fresher available", primary, image_type)
        return uri, (gi.local_path if gi else None)
    logger.info("Recreate main: no photo for %r — falling back to IG image", primary)
    return None, None


def build_split_srcs(db, primary: str, secondary: str, image_type: str = "face", niche: str = "MotoGP"):
    """Two context-appropriate, vision-picked photos (same style) for a left/right
    split. Fetches fresh from Getty first, falls back to the gallery."""
    left = fetch_subject_datauri(db, primary, image_type, niche)
    if not left:
        left, lg = find_gallery_datauri(db, primary, use_vision=True, image_type=image_type)
    if not left:
        return None
    right = fetch_subject_datauri(db, secondary, image_type, niche)
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


def _with_inset(template_json, srcs: list) -> list:
    """Fill an optional circular image_3 inset (e.g. the quote-card name badge's
    portrait) by reusing the last already-resolved photo — keeps the inset
    always filled without firing a third AI/gallery lookup. No-op if the
    template has no image_3 slot.

    image_3 is the THIRD element positionally (srcs[0]→image, srcs[1]→image_2,
    srcs[2]→image_3) — if image_2 wasn't filled (srcs has only 1 entry) we pad
    index 1 with None (falsy → renderer leaves that slot empty) rather than
    accidentally shifting the inset photo into the image_2 slot."""
    slot3 = find_role_object(template_json, "image_3")
    if slot3 is not None and slot3.get("type") in ("circle", "ellipse") and srcs:
        padded = (list(srcs) + [None, None])[:2]
        return padded + [srcs[-1]]
    return srcs


def _widen_to_full(template_json, canvas_width: int):
    """When a rectangular image_2 split never got filled (only one subject to
    show), widen the main "image" slot to cover the full canvas instead of
    leaving it a half-width rect next to an empty grey panel. image_2 itself
    is hidden too — left in place (unfilled) it would still paint its flat
    placeholder colour over the right half, on top of the now-widened photo."""
    import copy

    tj = copy.deepcopy(template_json)
    img = find_role_object(tj, "image")
    if img is not None:
        img["left"] = 0
        img["width"] = canvas_width
    img2 = find_role_object(tj, "image_2")
    if img2 is not None:
        img2["visible"] = False
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
        return template_json, _with_inset(template_json, [main_datauri])

    slot = find_role_object(template_json, "image_2")
    if slot is None:
        return template_json, _with_inset(template_json, [main_datauri])

    if slot.get("type") in ("circle", "ellipse"):
        # ── Inset flow ──
        subject = extract_secondary_subject(title, niche)
        if not subject:
            logger.info("Design: no secondary subject for %r — image_2 left empty", title[:50])
            return template_json, _with_inset(template_json, [main_datauri])
        uri = fetch_subject_datauri(db, subject, "face", niche)
        if not uri:
            uri, _ = find_gallery_datauri(db, subject, exclude_path=main_path, image_type="face")
        if not uri:
            logger.info("Design: no photo found for secondary subject %r", subject)
            return template_json, _with_inset(template_json, [main_datauri])
        side = analyze_subject_side(main_datauri)
        tj = position_secondary_slot(template_json, side, canvas_width)
        logger.info("Design: inset image_2 subject=%r (main subject %s)", subject, side)
        return tj, _with_inset(tj, [main_datauri, uri])

    # ── Split flow (rectangular image_2) ──
    primary, secondary = extract_two_subjects(title, niche)
    if primary and secondary:
        image_type = pick_split_image_type(title, niche)
        srcs = build_split_srcs(db, primary, secondary, image_type, niche)
        if srcs:
            logger.info("Design: split layout %r | %r (%s)", primary, secondary, image_type)
            return template_json, _with_inset(template_json, srcs)
        logger.info("Design: split sourcing failed for %r | %r — falling back", primary, secondary)

    subject = secondary if (primary and secondary) else extract_secondary_subject(title, niche)
    if not subject:
        # Only one subject in the whole piece — no split to show, so the main
        # photo fills the full canvas instead of sitting half-width next to
        # an empty grey panel.
        logger.info("Design: no secondary subject for %r — image widened to full width", title[:50])
        tj = _widen_to_full(template_json, canvas_width)
        return tj, _with_inset(tj, [main_datauri])
    uri, gi = find_gallery_datauri(db, subject, exclude_path=main_path)
    if not uri:
        logger.info("Design: no gallery image for secondary subject %r — image widened to full width", subject)
        tj = _widen_to_full(template_json, canvas_width)
        return tj, _with_inset(tj, [main_datauri])
    logger.info("Design: image_2 subject=%r → gallery image %d", subject, gi.id)
    return template_json, _with_inset(template_json, [main_datauri, uri])
