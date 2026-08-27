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
import threading

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

    category: "quote" | "news" | "discussion". A "news"-category template
    serves Mode 2 news_content jobs, Mode 3 ig_recreate posts classified
    "news", AND Mode 5 Pinterest ideas whose bound photo has no detected
    face; "quote" serves ig_recreate posts classified "quote" and Mode 5
    ideas whose photo DOES have a face (see render_pinterest); "discussion"
    serves Mode 4 debate cards — one global pool per category instead of
    separate per-mode template fields."""
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


# YuNet (opencv_zoo, WIDER FACE-trained DNN) vs. the Haar cascade this file
# used to call at 3 separate sites: Haar's bounding box shifts unpredictably
# with pose/angle/occlusion (a cap covering the forehead, a slight head
# turn), which is exactly what made two split-photo halves' face-WIDTH
# measurements land inconsistently even when both photos showed a
# similarly-sized head to a human eye — real user feedback, 2026-08-25
# ("gambar yang dihasilkan masih kurang baik... cara crop dan pemilihan
# zoom"), after a numeric zoom-matching fix had already made the two
# TARGET fractions equal (0.61 vs 0.57) without the RESULT looking equal —
# the input measurement itself was the imprecise part, not the matching
# arithmetic. Downloaded lazily (same pattern as hq_upscale.py's ONNX
# models) to storage_base_path/sr_models/, cached across the process
# lifetime; every caller below still falls back to the old Haar cascade if
# the model can't be fetched/loaded, so this can only improve on the
# previous behavior, never regress it to "no detection at all".
# media.githubusercontent.com, NOT raw.githubusercontent.com — the model is
# stored via git-lfs in opencv_zoo, and raw.* only serves the LFS pointer
# text file (131 bytes) for an LFS path, not the actual binary.
_YUNET_MODEL_URL = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
_YUNET_WORKING_SIZE = 640  # see _yunet_dominant_face's docstring
_yunet_lock = threading.Lock()
_yunet_state = {"detector": None, "failed": False}


def _get_yunet_detector():
    if _yunet_state["detector"] is not None or _yunet_state["failed"]:
        return _yunet_state["detector"]
    with _yunet_lock:
        if _yunet_state["detector"] is not None or _yunet_state["failed"]:
            return _yunet_state["detector"]
        try:
            import urllib.request

            import cv2
            from app.config import get_settings

            s = get_settings()
            model_path = os.path.join(s.storage_base_path, "sr_models", "face_detection_yunet_2023mar.onnx")
            if not (os.path.exists(model_path) and os.path.getsize(model_path) > 100_000):
                os.makedirs(os.path.dirname(model_path), exist_ok=True)
                tmp = model_path + ".part"
                req = urllib.request.Request(_YUNET_MODEL_URL, headers={"User-Agent": "ig-fb-reposter"})
                with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                    f.write(r.read())
                os.replace(tmp, model_path)
            _yunet_state["detector"] = cv2.FaceDetectorYN_create(model_path, "", (320, 320), score_threshold=0.6)
            logger.info("YuNet face detector loaded")
        except Exception as exc:
            logger.warning("YuNet face detector unavailable, falling back to Haar cascade: %s", exc)
            _yunet_state["failed"] = True
        return _yunet_state["detector"]


def _yunet_dominant_face(image_bytes: bytes) -> tuple[float, float, float, float] | None:
    """YuNet equivalent of the Haar-based measurement below — (fx, fy, fw, fh)
    fractions of the image for the highest-confidence detected face, or None
    if the detector is unavailable or finds nothing. Downscales to a max
    dimension of _YUNET_WORKING_SIZE first — found via real testing,
    2026-08-27: run directly on a full-resolution source photo (e.g.
    2448x1632), YuNet's own confidence for an obvious, close-up face topped
    out around 0.58 (below the 0.6 threshold, so it silently found nothing);
    the exact same photo resized to 640px wide detected the same face at
    0.94 confidence. The returned bbox is normalized fractions either way,
    so detecting on the smaller image loses no precision that matters here."""
    detector = _get_yunet_detector()
    if detector is None:
        return None
    try:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        h, w = img.shape[:2]
        scale = min(1.0, _YUNET_WORKING_SIZE / max(w, h))
        dw, dh = max(1, round(w * scale)), max(1, round(h * scale))
        det_img = cv2.resize(img, (dw, dh)) if scale < 1.0 else img
        detector.setInputSize((dw, dh))
        _, faces = detector.detect(det_img)
        if faces is None or len(faces) == 0:
            return None
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])[:4]
        fx, fy = max(0.0, float(fx)), max(0.0, float(fy))
        fw, fh = min(float(fw), dw - fx), min(float(fh), dh - fy)
        return (fx / dw, fy / dh, fw / dw, fh / dh)
    except Exception as exc:
        logger.debug("_yunet_dominant_face failed: %s", exc)
        return None


def _dominant_face_bbox(image_bytes: bytes) -> tuple[float, float, float, float] | None:
    """Largest detected face as normalized (fx, fy, fw, fh) fractions of the
    image, or None if no face found. Used both to decide "does a face dominate
    the frame" and, via fh/face-bottom, "does it reach too far down for a
    bottom text overlay" (an extreme close-up). Tries YuNet first (see above
    for why), falls back to the original Haar cascade."""
    face = _yunet_dominant_face(image_bytes)
    if face is not None:
        return face
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


# Target: both split halves' faces end up at the SAME fraction of the slot's
# width (see content_aware_split_pair). Tuned 2026-08-24 through many rounds
# of real user feedback on an actual split render (Marquez/Bagnaia, then
# validated across 5 more real pairs) — cover-fit's forced scale on this
# template's narrow 0.4-aspect split slot was putting a landscape 612x408
# photo's face at 85-93% of the slot's width (an uncomfortably tight,
# "too zoomed" crop the ordinary zoom_l/zoom_r correction can't fix, since
# that only ever zooms IN further — see vision_pick_split_pair). 0.62 was
# the level the user settled on as looking right, not a theoretical ideal —
# revisit if a differently-shaped template is added.
_SPLIT_TARGET_FACE_FRAC = 0.62
# Above this, content_aware_split_pair backs OFF and leaves that pair on the
# existing cover-fit + zoom_l/zoom_r path instead of forcing it. A photo
# whose face is already large relative to its own frame needs LESS zoom to
# reach _SPLIT_TARGET_FACE_FRAC, which means MORE of the final canvas has to
# be invented — real-world validation (2026-08-24, 5 test pairs) found the
# two photos users flagged as looking wrong were the two with the least
# zoom-in headroom (57%/54% synthetic fill) — but two OTHER photos at
# similarly high ratios (55%/52%) were NOT flagged, so this is a risk
# signal, not a precise predictor; 0.45 is a deliberately conservative
# reading of that data, not a proven exact cutoff.
_SPLIT_MAX_SYNTHETIC_FILL = 0.45

# Where each half's face TOP (hairline, not centre — see
# content_aware_split_extend's `anchor="top"`) lands, as a fraction of the
# slot height. Matching WIDTH via _SPLIT_TARGET_FACE_FRAC alone still lets
# two photos land with visibly different headroom (a cap covering the
# forehead, a tilted head, differing amounts of hair above the detected
# face box all shrink/grow the box without changing its width) — real user
# feedback, 2026-08-25 ("masih tidak bagus... cara cut splitnya", pointing
# at a Newey/Alonso pair whose heads sat at very different heights even
# after the face-width fix). 0.12 leaves clear headroom above either head
# without pushing shoulders/chest into the template's bottom scrim band.
_SPLIT_HEADROOM_TARGET_CY = 0.12

# How much of each half's WIDTH gets the inner-edge darkening vignette
# (see _seam_vignette) and how dark it gets at the seam itself — two flat
# photos butted at a hard vertical line read as "taped together", not one
# graphic (same 2026-08-25 feedback). Kept subtle: this is meant to read as
# depth/mood, not as an obvious dark bar.
_SEAM_VIGNETTE_FRAC = 0.08
_SEAM_VIGNETTE_STRENGTH = 0.35

# Per-channel brightness gain is clamped to this range when nudging both
# split halves toward their shared average (see _match_split_exposure) — a
# photo that's genuinely under/overexposed should still look off rather
# than being forced to match; this is a cosmetic blend for two normally-lit
# photos with merely different colour temperature/harshness, not a fix for
# a broken source photo.
_SEAM_EXPOSURE_GAIN_RANGE = (0.85, 1.18)


def _match_split_exposure(left_bytes: bytes, right_bytes: bytes) -> tuple[bytes, bytes]:
    """Split photos usually come from two different shoots/lighting setups —
    even once both crop cleanly, a warm/harshly-lit photo next to a cool/
    evenly-lit one still reads as two unrelated stock photos side by side
    rather than one graphic (real user feedback, 2026-08-25). Nudges both
    halves' per-channel brightness toward their shared average, gain capped
    to _SEAM_EXPOSURE_GAIN_RANGE. Returns the inputs unchanged on any
    failure — a missed blend is a minor cosmetic loss, not worth failing
    the whole split over."""
    try:
        import io

        import numpy as np
        from PIL import Image

        left_arr = np.asarray(Image.open(io.BytesIO(left_bytes)).convert("RGB")).astype(np.float32)
        right_arr = np.asarray(Image.open(io.BytesIO(right_bytes)).convert("RGB")).astype(np.float32)

        left_mean = left_arr.reshape(-1, 3).mean(axis=0)
        right_mean = right_arr.reshape(-1, 3).mean(axis=0)
        target = (left_mean + right_mean) / 2
        lo_gain, hi_gain = _SEAM_EXPOSURE_GAIN_RANGE

        def _apply(arr: "np.ndarray", mean: "np.ndarray") -> bytes:
            gain = np.clip(target / np.maximum(mean, 1e-3), lo_gain, hi_gain)
            out = np.clip(arr * gain, 0, 255).astype(np.uint8)
            buf = io.BytesIO()
            Image.fromarray(out).save(buf, "JPEG", quality=92)
            return buf.getvalue()

        return _apply(left_arr, left_mean), _apply(right_arr, right_mean)
    except Exception as exc:
        logger.warning("_match_split_exposure failed (using unmatched): %s", exc)
        return left_bytes, right_bytes


def _seam_vignette(left_bytes: bytes, right_bytes: bytes) -> tuple[bytes, bytes]:
    """Darkens each half's INNER edge (the side touching the shared seam) in
    a soft linear ramp, so the join reads as depth/vignette instead of a
    flat hard cut — the same trick real editorial "head to head" graphics
    use. Purely multiplicative darkening (never lightens, never touches
    hue), applied AFTER _match_split_exposure so it can't fight that pass.
    Returns the inputs unchanged on failure."""
    try:
        import io

        import numpy as np
        from PIL import Image

        left_arr = np.asarray(Image.open(io.BytesIO(left_bytes)).convert("RGB")).astype(np.float32)
        right_arr = np.asarray(Image.open(io.BytesIO(right_bytes)).convert("RGB")).astype(np.float32)

        w = left_arr.shape[1]
        band = max(1, int(w * _SEAM_VIGNETTE_FRAC))
        ramp = np.linspace(0, _SEAM_VIGNETTE_STRENGTH, band, dtype=np.float32)

        left_gain = np.ones(w, dtype=np.float32)
        left_gain[w - band:] = 1 - ramp
        right_gain = np.ones(w, dtype=np.float32)
        right_gain[:band] = 1 - ramp[::-1]

        left_out = np.clip(left_arr * left_gain[None, :, None], 0, 255).astype(np.uint8)
        right_out = np.clip(right_arr * right_gain[None, :, None], 0, 255).astype(np.uint8)

        lo = io.BytesIO()
        Image.fromarray(left_out).save(lo, "JPEG", quality=92)
        ro = io.BytesIO()
        Image.fromarray(right_out).save(ro, "JPEG", quality=92)
        return lo.getvalue(), ro.getvalue()
    except Exception as exc:
        logger.warning("_seam_vignette failed (using unblended): %s", exc)
        return left_bytes, right_bytes


def content_aware_split_extend(image_bytes: bytes, target_w: int, target_h: int,
                                zoom: float = 1.0, top_bias: float = 0.16,
                                feather: int = 24, downscale: int = 5,
                                face_bbox: tuple[float, float, float, float] | None = None,
                                target_cy: float = 0.34,
                                anchor: str = "center") -> bytes | None:
    """Fit `image_bytes` to `target_w` (scaled further by `zoom`), then fill
    the remaining canvas with content-aware synthesis (cv2.xphoto
    INPAINT_FSR_BEST) instead of reflect_extend's mirror-flip — built
    specifically for split-photo halves (see content_aware_split_pair), not
    a general reflect_extend replacement (smart_expand's face path already
    has fit_with_blur_bg; this is for when a split slot's forced cover-fit
    scale would otherwise crop far too tight — see _SPLIT_TARGET_FACE_FRAC).

    `face_bbox` (fx, fy, fw, fh — normalized fractions of the ORIGINAL
    image, from `_dominant_face_bbox`) drives BOTH crop axes when given:
    - vertical: the real photo is positioned so the face lands at `target_cy`
      (default 0.34, matching detect_focus_point/vision_focus_point's own
      default for the ordinary non-split case) — NOT a blind top_bias split.
      `anchor` picks WHICH point of the face that is: "center" (default) is
      the face's vertical midpoint; "top" is the face's top edge (hairline),
      used by content_aware_split_pair so two different photos' HEADROOM
      matches, not just their face centers — two faces of different detected
      height (a cap covering the forehead, a tilted head, a closer/farther
      shot) can share a center point while still landing with visibly
      different space above the head, which reads as sloppy/unbalanced side
      by side (real user feedback, 2026-08-25). Callers with a specific
      template's own safe ceiling in hand
      (see `_safe_face_cy_ceiling`, used by `fix_unsafe_single_photo_face`)
      should pass THAT instead of relying on the flat default — a template
      whose title starts higher or lower than "typical" needs a different
      target, the same way the split path's own target was tuned by hand
      for its own template's geometry rather than assumed. This matters more
      here than elsewhere: the
      caller pre-fits the composite to the EXACT slot size, so the renderer's
      own cover-fit ends up at ~1.0 scale with no slack left to reposition
      afterward — this function's placement is the ONLY thing standing
      between the face and the template's bottom text/scrim band.
    - horizontal: at high zoom the width crop is narrow enough that a blind
      centre-crop can slice through an off-centre face — found via a real
      bug (zoom=3.45, face centred at x=0.403, blind crop window
      [0.372,0.628] clipped the face's left edge at 0.279). Centres on the
      face's own x instead.
    Falls back to a blind top_bias/centre split when no face is given —
    same convention reflect_extend already uses.

    The inpaint runs on a `downscale`-times-smaller canvas — FSR_BEST's
    runtime blows up non-linearly with fill area (195s at full res for a
    73%-empty 480x1200 canvas vs ~12s at 1/5 scale) — then the result is
    upscaled with LANCZOS and blended against the sharp source at the seam.
    Returns None on failure (caller should fall back to the raw photo)."""
    try:
        import io

        import cv2
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        iw, ih = img.size
        scale = (target_w / iw) * zoom
        rw, rh = max(1, round(iw * scale)), max(1, round(ih * scale))
        img_full = img.resize((rw, rh))
        if rw > target_w:
            if face_bbox:
                face_cx_px = (face_bbox[0] + face_bbox[2] / 2) * rw
                l = int(round(face_cx_px - target_w / 2))
                l = max(0, min(rw - target_w, l))
            else:
                l = (rw - target_w) // 2
            img_full = img_full.crop((l, 0, l + target_w, rh))
        arr_full = np.asarray(img_full)
        h_full = arr_full.shape[0]

        def _anchor_px(h: int) -> float:
            return face_bbox[1] * h if anchor == "top" else (face_bbox[1] + face_bbox[3] / 2) * h

        def _placement(gap: int) -> int:
            if face_bbox:
                anchor_px = _anchor_px(h_full)
                pt = int(round(target_h * target_cy - anchor_px))
                return max(0, min(gap, pt))
            return int(gap * top_bias)

        if h_full >= target_h:
            over = h_full - target_h
            if face_bbox:
                anchor_px = _anchor_px(h_full)
                t = max(0, min(over, int(round(anchor_px - target_h * target_cy))))
            else:
                t = int(over * top_bias)
            out = io.BytesIO()
            Image.fromarray(arr_full[t:t + target_h]).save(out, "JPEG", quality=92)
            return out.getvalue()

        pt = _placement(target_h - h_full)

        dw, dh = max(8, target_w // downscale), max(8, target_h // downscale)
        d_arr = np.asarray(
            Image.fromarray(arr_full).resize((dw, max(1, round(h_full * dw / target_w))))
        )
        dh_src = min(d_arr.shape[0], dh)
        pt_d = min(int(round(pt / target_h * dh)), dh - dh_src)
        canvas = np.zeros((dh, dw, 3), np.uint8)
        canvas[pt_d:pt_d + dh_src] = d_arr[:dh_src]
        mask = np.zeros((dh, dw), np.uint8)
        mask[pt_d:pt_d + dh_src] = 255
        bgr = cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR)
        filled = np.zeros_like(bgr)
        cv2.xphoto.inpaint(bgr, mask, filled, cv2.xphoto.INPAINT_FSR_BEST)
        filled_rgb = cv2.cvtColor(filled, cv2.COLOR_BGR2RGB)

        filled_full = np.asarray(
            Image.fromarray(filled_rgb).resize((target_w, target_h), Image.LANCZOS)
        ).astype(np.float32)

        ys = np.arange(target_h)
        rt = np.clip((ys - pt) / feather, 0, 1)
        rb = np.clip((pt + h_full - 1 - ys) / feather, 0, 1)
        alpha = np.minimum(rt, rb)[:, None, None]
        sharp = np.zeros((target_h, target_w, 3), np.float32)
        sharp[pt:pt + h_full] = arr_full
        res = filled_full * (1 - alpha) + sharp * alpha
        out = io.BytesIO()
        Image.fromarray(np.clip(res, 0, 255).astype(np.uint8)).save(out, "JPEG", quality=92)
        return out.getvalue()
    except Exception as exc:
        logger.warning("content_aware_split_extend failed: %s", exc)
        return None


def content_aware_split_pair(left_bytes: bytes, right_bytes: bytes, slot_w: int, slot_h: int):
    """Try content-aware fill on BOTH split halves at once, targeting the
    SAME face-width-of-slot fraction (_SPLIT_TARGET_FACE_FRAC) for both —
    "equal by construction" rather than measuring one side and chasing the
    other to match it (an earlier version of this did that; the user's own
    verdict comparing them side by side was that even a supposedly-exact
    numeric match didn't always look equal, so aiming both at the same
    independently-computed target is the more robust rule to generalize).

    Returns (left_datauri, right_datauri) on success, or None if either
    side has no detected face or either fill call fails outright — that
    still tells the caller (build_split_srcs) to keep the existing
    cover-fit + zoom_l/zoom_r behavior for this pair, but is now rare (only
    a face-detection or encode failure, not a fill-budget decision). Each
    side's zoom is resolved against three independent lower bounds (ideal
    0.62 target, a hard >=1.0 floor, and whatever keeps synthetic fill at/
    under _SPLIT_MAX_SYNTHETIC_FILL) rather than aborting the whole pair
    whenever one side is hard — see _resolve_zoom_and_fill below for why
    each bound exists."""
    left_face = _dominant_face_bbox(left_bytes)
    right_face = _dominant_face_bbox(right_bytes)
    if not left_face or not right_face:
        return None

    # zoom must satisfy THREE independent lower bounds at once, so compute
    # each as its own floor on zoom and take the max rather than deriving
    # one from the other (fill is a plain decreasing function of zoom, so
    # "raise zoom" is always the right direction for all three — no
    # candidate bound is ever individually sufficient on its own):
    #   1. ideal_zoom — reach _SPLIT_TARGET_FACE_FRAC exactly.
    #   2. 1.0 — content_aware_split_extend's width-crop step only handles
    #      zoom >= 1.0 (it crops DOWN to target_w when the resize comes out
    #      wider — see its `if rw > target_w` branch; there's no path for
    #      rw < target_w). A face already large relative to its own source
    #      frame (a tight close-up candidate) can compute an ideal_zoom
    #      BELOW 1.0 — found via real testing 2026-08-25 on an Alonso
    #      close-up (face 47% of its own frame width): the resized image
    #      came out narrower than the slot and the composite was badly
    #      misaligned (ear/cap visible, no face). A face already this size
    #      needs no zoom-in to read clearly, so 1.0 is always a safe floor.
    #   3. zoom_for_cap — whatever zoom keeps synthetic fill at/under
    #      _SPLIT_MAX_SYNTHETIC_FILL for THIS photo's own height. Needed
    #      because bound 2 can itself push fill back up past the cap (zoom
    #      1.0 covers only the image's OWN aspect ratio, which can leave a
    #      lot of vertical gap on a narrow split slot) — checking fill at
    #      the ideal_zoom alone and never re-checking it after flooring to
    #      1.0 is exactly the bug bound 2's docstring above describes.
    # Whichever bound ends up binding, fill is then computed FRESH from the
    # zoom actually used — never carried over from an earlier candidate
    # zoom — so it always reflects what will actually be rendered. Real
    # incident, 2026-08-25: job 4893 Newey/Alonso, one side an extreme
    # near-unrecognizable close crop next to a normally framed other side —
    # this whole function exists to keep both sides on the SAME equalized
    # pipeline instead of the old independent cover-fit + zoom_l/zoom_r
    # path falling back for the whole pair whenever either side was hard.
    def _resolve_zoom_and_fill(face_w_frac: float, img_w: int, img_h: int) -> tuple[float, float]:
        fit_scale = slot_w / img_w
        ideal_target_scale = _SPLIT_TARGET_FACE_FRAC * slot_w / (face_w_frac * img_w)
        ideal_zoom = ideal_target_scale / fit_scale
        cap_target_scale = slot_h * (1 - _SPLIT_MAX_SYNTHETIC_FILL) / img_h
        zoom_for_cap = cap_target_scale / fit_scale
        zoom = max(ideal_zoom, zoom_for_cap, 1.0)
        target_scale = zoom * fit_scale
        fill = max(0.0, 1 - (img_h * target_scale) / slot_h)
        return zoom, fill

    try:
        import io
        from PIL import Image

        left_iw, left_ih = Image.open(io.BytesIO(left_bytes)).size
        right_iw, right_ih = Image.open(io.BytesIO(right_bytes)).size
    except Exception:
        return None

    zoom_l, fill_l = _resolve_zoom_and_fill(left_face[2], left_iw, left_ih)
    zoom_r, fill_r = _resolve_zoom_and_fill(right_face[2], right_iw, right_ih)

    # anchor="top" (aligning both faces' hairlines to the same height) was
    # tried here and REVERTED — 2026-08-25, real user comparison of both
    # renders: top-anchoring made the two subjects look MORE mismatched in
    # SIZE, not less (a cap/hair obscuring one photo's detected face box
    # shifts where "top" lands, so equalizing headroom pushed that side's
    # zoom up further, making that face read as bigger/more dominant than
    # the other). The user's own verdict was they preferred the plain
    # centre-anchored default they'd already seen — back to that; SIZE
    # parity is what _SPLIT_TARGET_FACE_FRAC's shared width target above is
    # for, not vertical anchoring.
    left_filled = content_aware_split_extend(left_bytes, slot_w, slot_h, zoom=zoom_l, face_bbox=left_face)
    right_filled = content_aware_split_extend(right_bytes, slot_w, slot_h, zoom=zoom_r, face_bbox=right_face)
    if not left_filled or not right_filled:
        return None

    # Cosmetic-only passes — a photo pair that's already correctly framed
    # can still read as two unrelated stock photos taped together (real
    # user feedback, 2026-08-25) without these: bring both halves' exposure
    # toward their shared average, then darken each half's inner edge in a
    # soft ramp so the seam reads as depth rather than a flat hard cut.
    left_filled, right_filled = _match_split_exposure(left_filled, right_filled)
    left_filled, right_filled = _seam_vignette(left_filled, right_filled)

    logger.info(
        "content_aware_split_pair: applied (zoom_l=%.2f fill=%.0f%%, zoom_r=%.2f fill=%.0f%%)",
        zoom_l, fill_l * 100, zoom_r, fill_r * 100,
    )
    return (
        "data:image/jpeg;base64," + base64.b64encode(left_filled).decode(),
        "data:image/jpeg;base64," + base64.b64encode(right_filled).decode(),
    )


def fix_unsafe_single_photo_face(image_bytes: bytes, canvas_width: int, canvas_height: int,
                                  safe_cy_ceiling: float) -> str | None:
    """Safety net for the SINGLE full-width photo case (not a split half —
    see content_aware_split_pair for that): when a landscape source photo's
    face would land past the template's safe ceiling AND ordinary cover-fit
    has NO vertical slack to reposition it, content-aware-fill the photo at
    a wider, less-zoomed framing instead — same technique as the split fix,
    generalized here.

    Found 2026-08-24 investigating 5 real user-flagged renders: adding
    corrective ZOOM (the split fix's approach) does NOT work for this case
    and was tried and rejected — zooming in on the template's fixed target
    point moves every OTHER point (including the face, which is naturally
    BELOW that point) proportionally further from centre, eventually
    cropping it out entirely, confirmed both by hand-derived geometry and
    by testing it against real face-position numbers. This is a fundamentally
    different situation from the split fix's zoom_l/zoom_r, which zooms IN
    ON THE FACE ITSELF (a pure size adjustment, anchor = face) rather than
    trying to reposition a face relative to a DIFFERENT fixed anchor point —
    only content-aware fill (choosing what to show and where, not
    constrained by "must crop to exactly fill") can actually move a face
    that has zero natural repositioning room.

    Why img_ar > slot_ar is the exact zero-slack condition: cover-fit picks
    scale = max(slot_w/img_w, slot_h/img_h); when img_w/img_h (img_ar) >
    slot_w/slot_h (slot_ar), the HEIGHT ratio wins the max(), which makes
    img_h * scale equal slot_h EXACTLY (by construction) — i.e. the whole
    image height is always shown, zero pixels of vertical cover-fit slack,
    regardless of what focus point is computed. A near-square or portrait
    source (img_ar <= slot_ar) already has real vertical slack and doesn't
    need this — the normal focus-point ceiling clamp (see
    _safe_face_cy_ceiling) is sufficient there on its own.

    Only fires when genuinely needed (the face's natural position already
    clears the ceiling, or the aspect ratio already has slack → returns
    None, caller keeps the original photo + normal cover-fit). zoom=1.0
    (show the whole photo width, no extra tightening) is deliberately the
    safest possible choice — this is a correctness safety net, not a
    framing/aesthetic enhancement, so it errs toward "definitely keeps the
    face fully visible" over "looks tightly cropped".

    2026-08-25: two no-face fallbacks were tried and REJECTED here for the
    helmeted-rider/car-action case (same zero-slack condition, no face to
    anchor on) — real-render testing on a Mode 5 Senna/McLaren photo found:
    (1) a vision-point-anchored content-aware fill stayed visibly soft/
    discolored regardless of how tightly the invented-area fraction was
    capped (40%, 30%, 12% all looked bad — the defect is the inpaint
    algorithm's output quality on this content, not the amount invented);
    (2) reflect_extend (mirror+blur+darken, no hallucinated detail) looked
    clean but was still ultimately rejected per the user's own call: for
    Mode 5 specifically, a photo that doesn't already crop well should be
    excluded at candidate-selection time rather than patched at render
    time — see photo_crops_well, used by
    pinterest_source.build_idea_from_candidate. This function stays
    face-only; a no-face landscape photo just returns None here (normal
    cover-fit), same as before either fallback existed."""
    try:
        import io
        from PIL import Image

        iw, ih = Image.open(io.BytesIO(image_bytes)).size
    except Exception:
        return None
    if not iw or not ih or not canvas_height:
        return None
    img_ar = iw / ih
    slot_ar = canvas_width / canvas_height
    if img_ar <= slot_ar:
        return None  # real cover-fit slack already exists — normal path handles it

    face = _dominant_face_bbox(image_bytes)
    if not face:
        # No face — see docstring: both a content-aware fallback and a
        # reflect_extend fallback were tried here (2026-08-25) and reverted.
        # Per the user's final call for Mode 5 specifically: don't try to
        # salvage a bad-fitting photo with a crop/extend trick at render
        # time — see photo_crops_well, which render_pinterest checks BEFORE
        # calling this at all, to decide whether to use the template or post
        # the (upscaled) photo directly with no template/crop.
        return None

    natural_cy = face[1] + face[3] / 2 - 0.08  # same lift bias detect_focus_point uses
    if natural_cy <= safe_cy_ceiling:
        return None  # already safe at zoom=1, no fix needed

    filled = content_aware_split_extend(
        image_bytes, canvas_width, canvas_height, zoom=1.0, face_bbox=face, target_cy=safe_cy_ceiling,
    )
    if not filled:
        return None
    logger.info(
        "fix_unsafe_single_photo_face: applied (natural_cy=%.2f > ceiling=%.2f, img_ar=%.2f > slot_ar=%.2f)",
        natural_cy, safe_cy_ceiling, img_ar, slot_ar,
    )
    return "data:image/jpeg;base64," + base64.b64encode(filled).decode()


def single_photo_face_fits(image_bytes: bytes, canvas_width: int, canvas_height: int,
                            safe_cy_ceiling: float) -> bool:
    """Mode 2/3 single-photo selection gate: can THIS candidate's face ever
    be made to clear the template's text zone, even with
    fix_unsafe_single_photo_face's best effort — or is the face simply too
    LARGE relative to the frame for any repositioning to save it?

    Real incident, 2026-08-27: production jobs (e.g. a Sean Strickland quote
    card, a Ralf Schumacher quote card) shipped with the face's mouth/chin
    cropped clean off by the caption text. Root cause was in
    fix_unsafe_single_photo_face's own trigger condition — it compared the
    face's CENTRE (`natural_cy`) against the ceiling, with no term for the
    face's HEIGHT at all. An extreme close-up (a paparazzi lens filling the
    frame edge-to-edge with just a face) can have a centre that reads as
    "safe" while the face is simply too TALL for its bottom half to fit
    above the ceiling — the fix's own gate silently skipped a photo that
    badly needed it, and normal cover-fit shipped it uncorrected.

    Rather than extend fix_unsafe_single_photo_face to try even harder on a
    photo like this (more content-aware fill invented across a bigger area,
    the same class of soft/discoloured artifact already found and rejected
    for Mode 5's no-face landscape case — see that function's own docstring),
    this gate runs BEFORE selection commits to a candidate: reject outright
    and let the caller try a different photo. A face that's already too
    zoomed-in to read as a normal editorial portrait doesn't make a good
    hero image regardless of whether the crop can technically be forced to
    fit, and this codebase already prefers "no candidate" over "a knowingly
    bad one" elsewhere (see feedback-image-accuracy-over-availability).

    True: normal cover-fit already has vertical slack (img_ar <= slot_ar),
    or no face was detected (a separate concern — unchanged, still handled
    by fix_unsafe_single_photo_face/cover-fit as before), or the face WOULD
    fit even at fix_unsafe_single_photo_face's best achievable placement.
    False: the face is too large for that placement to clear the text zone
    even in the best case — reject this candidate."""
    try:
        import io
        from PIL import Image

        iw, ih = Image.open(io.BytesIO(image_bytes)).size
    except Exception:
        return True  # can't tell — don't block a photo over a decode hiccup
    if not iw or not ih or not canvas_height or not canvas_width:
        return True
    img_ar = iw / ih
    slot_ar = canvas_width / canvas_height
    if img_ar <= slot_ar:
        return True  # real cover-fit slack already exists — not this gate's concern

    face = _dominant_face_bbox(image_bytes)
    if not face:
        return True  # no-face landscape case — unchanged, separate concern

    # fix_unsafe_single_photo_face rescales at zoom=1.0 → scale = target_w/iw
    # (a WIDTH-fit, not cover-fit's height-fit) before repositioning, which is
    # exactly what creates room to move the face at all — the same face
    # height that was `fh` of the original image becomes `fh * slot_ar /
    # img_ar` of the final canvas under that rescale (derivable from
    # scale_width_fit / scale_height_fit = slot_ar / img_ar). Centring the
    # face at the ceiling (the best that function's positioning can do)
    # puts the face's bottom edge at ceiling + half that scaled height.
    _, _, _fw, fh = face
    scaled_fh = fh * slot_ar / img_ar
    best_case_bottom = safe_cy_ceiling + scaled_fh / 2

    # _safe_face_cy_ceiling subtracts a flat 0.10 from the template's real
    # title/scrim top to get `safe_cy_ceiling` — undo that to recover the
    # actual boundary the face's bottom edge must clear, then allow a small
    # buffer (0.03) since the ceiling's own margin already has some slack
    # built in for typical (non-extreme) face heights.
    title_top = min(0.95, safe_cy_ceiling + 0.10)
    return best_case_bottom <= title_top + 0.03


def photo_crops_well(image_bytes: bytes, canvas_width: int, canvas_height: int) -> bool:
    """Mode 5 (Pinterest) gate: can this photo go on the design template at
    all, or should it post directly with no template/crop?

    Same zero-vertical-slack condition as fix_unsafe_single_photo_face: a
    landscape source (img_ar > slot_ar) forced onto this portrait canvas
    always shows its FULL height compressed to fit, with zero room to
    reposition — fine when a detected face lets fix_unsafe_single_photo_face
    correct it, but for a no-face action/vehicle shot every attempted fix
    (content-aware fill, reflect_extend) looked worse than not cropping at
    all (real render testing, 2026-08-25). Rather than force that photo
    through the template with a bad crop, render_pinterest skips the
    template entirely for it and posts the (already-upscaled)
    photo as-is — this function is what tells it which path to take.

    True: portrait/near-square source (real cover-fit slack exists), or a
    landscape source WITH a detected face (fix_unsafe_single_photo_face
    handles it). False: landscape, no face — nothing left to safely fix it."""
    try:
        import io
        from PIL import Image

        iw, ih = Image.open(io.BytesIO(image_bytes)).size
    except Exception:
        return True  # can't tell — don't block the photo over a decode hiccup
    if not iw or not ih or not canvas_height:
        return True
    img_ar = iw / ih
    slot_ar = canvas_width / canvas_height
    if img_ar <= slot_ar:
        return True
    return _dominant_face_bbox(image_bytes) is not None


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


def _safe_face_cy_ceiling(template_json: dict, canvas_height: float) -> float:
    """How far down (0..1 fraction of canvas height) a subject's face CENTRE
    can safely sit before the CHOSEN template's own title text risks covering
    it — derived from that template's actual `title` (or `scrim`, if no
    title role exists) object position, not a flat guess.

    Found 2026-08-24 via 5 real user-flagged renders where the face landed
    under the title text: `detect_focus_point`/`vision_focus_point` clamped
    the target to a flat ceiling (0.44 for the OpenCV path, 0.7 for the
    vision path) regardless of which template ended up being used — but a
    template's own title box can start anywhere from ~44% down (e.g. "News
    Highlight — Green · Center (Quote overlay)", "News Highlight — Green ·
    Left") to ~75-78% down (the plainer Quote Card templates). A flat 0.44
    ceiling leaves ~0 margin against a title starting at 44%, and the
    vision path's 0.7 ceiling doesn't even try — it was never checked
    against any specific template's real geometry at all.

    -0.10 margin below the title's own top accounts for the face's own
    height extending below its centre point (a real face box, not just its
    centre, must clear the title) — the same kind of fixed lift already
    used elsewhere in this file (e.g. detect_focus_point's own "-0.08").
    Floored at 0.20 so an unusually high title box doesn't push the target
    absurdly close to the very top of the frame."""
    if not canvas_height:
        return 0.44
    title = find_role_object(template_json, "title") or find_role_object(template_json, "scrim")
    if not title:
        return 0.44
    top_frac = float(title.get("top", canvas_height * 0.44)) / canvas_height
    return round(max(0.20, min(0.7, top_frac - 0.10)), 4)


def detect_focus_point(image_bytes: bytes, for_split: bool = False, safe_cy_ceiling: float | None = None) -> list:
    """Return [fx, fy] (0..1) of the main subject to focus the crop on — the
    largest detected face, else the salient object, else slightly above centre.
    Vertical is biased UP (smaller fy raises the subject in the crop) because the
    bottom of the card carries the text overlay — we keep the subject clear of it.
    OpenCV runs locally in ~ms (no API).

    `for_split=True` (a side-by-side split half, see build_split_srcs) skips
    that upward bias — the split photo area has no text overlaid on it (the
    headline sits in its own black band below), so lifting the face away from
    its true centre only pushes the crop off-target. Found 2026-08-21: a
    split half is already a much tighter horizontal crop than a full-width
    template (half the canvas width, same landscape source photos), so this
    bias — harmless on a full-bleed single photo — was enough there to clip
    straight through the chin/mouth or land on an ear instead of the face.

    `safe_cy_ceiling` (non-split only — see `_safe_face_cy_ceiling`): caps
    how far down the target can sit, computed from the ACTUAL chosen
    template's title position. Defaults to the old flat 0.44 when omitted
    (callers that don't have a template in hand yet)."""
    ceiling = 0.44 if safe_cy_ceiling is None else safe_cy_ceiling
    default = [0.5, min(0.34, ceiling)] if not for_split else [0.5, 0.42]
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
                if for_split:
                    # True face centre, no upward lift — a little below centre
                    # so chin/shoulders stay in frame on a tight vertical crop.
                    cy = min(0.62, max(0.3, (fy + fh / 2) / h + 0.04))
                else:
                    # Lift the face toward the upper third so it sits above the text
                    # overlay (only bites when the source is tall enough to move).
                    cy = min(ceiling, max(0.24, (fy + fh / 2) / h - 0.08))
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
                    if for_split:
                        cy = 0.55 * (M["m01"] / M["m00"] / h) + 0.45 * 0.42
                        cy = min(0.62, max(0.3, cy))
                    else:
                        cy = 0.55 * (M["m01"] / M["m00"] / h) + 0.45 * 0.34
                        cy = min(min(0.46, ceiling), max(0.26, cy))
                    return [round(float(cx), 4), round(float(cy), 4)]
        except Exception as exc:
            logger.debug("saliency focus failed: %s", exc)
    except Exception as exc:
        logger.warning("detect_focus_point failed: %s", exc)
    return default


def vision_focus_point(image_bytes: bytes, for_split: bool = False, safe_cy_ceiling: float | None = None) -> list | None:
    """Ask 9Router vision for the MAIN subject's focal point as [x, y] fractions
    (where the crop should centre — usually the face/head). Returns None on any
    problem so the caller can fall back to OpenCV. Runs on 9Router (no VPS cost).

    `for_split=True` — see detect_focus_point's docstring: a split half has no
    text overlaid on the photo itself, so the prompt and clamp range drop the
    "clear the bottom text" framing and just centre on the actual face.

    `safe_cy_ceiling` (non-split only — see `_safe_face_cy_ceiling`): the
    model is only ASKED to stay clear of the bottom text (the prompt has no
    idea how much of the canvas that actually is) — this is the hard
    enforcement, same role as detect_focus_point's ceiling. Defaults to the
    old flat 0.7 when omitted, which is permissive enough to have let a
    real face land under a title box that started as high as 44% down."""
    try:
        # x/y are fractions of the frame, so a downscaled copy is fine here —
        # and keeps the request under the router's size limit (see _vision_datauri).
        datauri = _vision_datauri(image_bytes)
        if for_split:
            prompt = (
                "This photo is one half of a side-by-side split news graphic "
                "(cropped to a narrow vertical strip — expect a tight crop). "
                "Find the MAIN subject's face/head (or the key point of a "
                "vehicle if there's no clear face). "
                'Reply with ONLY a JSON object {"x":0.00-1.00,"y":0.00-1.00} — '
                "the point the crop should centre on so the FULL face (chin to "
                "forehead, both eyes) stays in frame. x is fraction from left, "
                "y from top."
            )
        else:
            prompt = (
                "This photo is the full-bleed background of a news graphic whose "
                "headline sits along the BOTTOM. Find the MAIN subject (the "
                "person's/vehicle's key point — a face/head, or a car/bike). "
                'Reply with ONLY a JSON object {"x":0.00-1.00,"y":0.00-1.00} — the '
                "point the crop should centre on so the subject stays fully in "
                "frame and clear of the bottom text. x is fraction from left, y "
                "from top (the subject is usually in the upper half)."
            )
        content = [
            {"type": "text", "text": prompt},
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
            if for_split:
                return [round(min(0.85, max(0.15, x)), 4), round(min(0.75, max(0.2, y)), 4)]
            # keep the subject slightly above centre so it clears the bottom text
            ceiling = 0.7 if safe_cy_ceiling is None else safe_cy_ceiling
            return [round(min(0.85, max(0.15, x)), 4), round(min(ceiling, max(0.15, y)), 4)]
    except Exception as exc:
        logger.warning("vision_focus_point failed: %s", exc)
    return None


def focus_points_for(image_srcs: list, for_split: bool = False, safe_cy_ceiling: float | None = None) -> list:
    """Compute a focus point per data-URI image (for the renderer). When vision
    focus is enabled, the 9Router vision model chooses the point (best for the
    full-bleed portrait templates); otherwise OpenCV face/saliency is used.

    `for_split` is NOT "is this a split image" — it's "does nothing get
    drawn over the BOTTOM of this specific photo after compositing." Pass
    `for_split=True` only for a layout where the split half has no overlay
    on it at all (an old flat-band-below-photo design where the photo area
    and the text band are two separate, non-overlapping regions) — it drops
    the upward face-lift bias since there's no text/scrim to clear.
    `for_split=False` (the default) is correct whenever a scrim/gradient or
    any other overlay is drawn over part of the photo itself — including a
    split half rendered on a template whose scrim is baked over the photo's
    bottom portion (the current real-template split layout, see
    build_split_srcs) — because that's exactly the "lift the face clear of
    the overlay" situation this bias exists for. Found the hard way
    2026-08-21: using `for_split=True` on the real-template split layout let
    faces land under the scrim with no correction at all.

    For a 2-entry split pair specifically, also see `align_split_focus_points`
    — this function computes each entry's focus point independently, so the
    two heads can land at different heights even when both individually
    clear the overlay.

    `safe_cy_ceiling` (non-split only — see `_safe_face_cy_ceiling`): pass
    the ACTUAL chosen template's computed ceiling so the target adapts to
    that template's own title position instead of a flat guess. Omit to
    keep the old flat-ceiling behavior (callers without a template in hand,
    or the split path, which has its own separate ceiling logic already)."""
    from app.config import get_settings
    use_vision = get_settings().vision_focus_enabled
    default = [0.5, 0.42]
    out = []
    for uri in image_srcs:
        try:
            b = base64.b64decode(uri.split(",", 1)[1]) if "," in uri else b""
            if not b:
                out.append(default); continue
            fp = vision_focus_point(b, for_split=for_split, safe_cy_ceiling=safe_cy_ceiling) if use_vision else None
            out.append(fp or detect_focus_point(b, for_split=for_split, safe_cy_ceiling=safe_cy_ceiling))
        except Exception:
            out.append(default)
    return out


def align_split_focus_points(focus_points: list) -> list:
    """Given exactly 2 focus points (a left/right split pair, see
    focus_points_for), level their vertical (y) component to the SAFER
    (smaller — higher up in frame) of the two — so both heads land at the
    same height in the final composite instead of wherever each photo's own
    face happened to sit, AND neither ends up lower than it would have been
    on its own. Each side's focus point is otherwise computed independently
    (its own face position within ITS OWN photo), which can leave the two
    heads at visibly different heights even when both individually clear
    the overlay — user feedback on a real render: "kepala Norris seharusnya
    sejajar dengan kepala Max".

    min() rather than a plain average: averaging can still push a tightly-
    cropped face DOWN toward risk — found on a real render (Marquez, a
    much tighter close-up than Bagnaia in that pair) where the average
    landed low enough to clip his mouth/chin under the scrim, i.e. the
    average was "between the two" but not "safe for both". A tighter crop
    has less vertical margin for the same fy than a looser one, so meeting
    in the middle isn't actually a safe middle for the tighter photo — only
    using the more conservative (smaller/higher) of the two guarantees
    neither photo ends up worse off than its own independent bias already
    was. No-op (returns input unchanged) unless given exactly 2 points."""
    if len(focus_points) != 2:
        return focus_points
    safe_fy = min(focus_points[0][1], focus_points[1][1])
    return [[focus_points[0][0], safe_fy], [focus_points[1][0], safe_fy]]


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


_FRAME_RANK = {"HEAD": 0, "HALF": 1, "FULL": 2, "VEHICLE": 10}


def _classify_split_frames(labeled: list[tuple[str, str]]) -> dict[str, str]:
    """One vision call: classify EVERY candidate's shot type up front, as its
    own separate step. `labeled` is a list of (label, datauri) — labels are
    the caller's own (e.g. "L1", "R2"). Returns {label: "HEAD"|"HALF"|"FULL"
    |"VEHICLE"}; a label missing from the result (unparseable reply) is left
    out, not guessed.

    Split out from the pairing decision itself (2026-08-21) because asking
    one call to both classify AND pick let the model's final answer quietly
    violate its own same-category rule — visibly, on real test renders
    (a tight HEAD shot paired against a HALF-body shot despite an explicit
    "this is the single most important rule" instruction). Classification
    is an easy, low-ambiguity sub-task; enforcing the match is far more
    reliable done in plain Python (see vision_pick_split_pair) than hoped
    for from a multi-step instruction the model has to hold across a whole
    reasoning chain.

    `VEHICLE` added 2026-08-23 after a real user report, backed by 4/4
    checked production renders: an "action" search for a driver/rider
    sometimes returns a photo where the CAR/BIKE dominates the frame and the
    person is tiny/unclear (e.g. a wide on-track shot), while the OTHER side
    of the same split returned a tight face portrait — HEAD/HALF/FULL all
    describe how much of a PERSON's body shows, so a vehicle-dominant photo
    doesn't cleanly fit any of them and was landing in whatever bucket the
    model guessed, with nothing stopping it from pairing against a
    close-up face. `_FRAME_RANK["VEHICLE"]=10` deliberately isolates it from
    the HEAD/HALF/FULL 0-2 spectrum — its distance to every person-framing
    category is then always far larger than any person-to-person distance,
    so vision_pick_split_pair's tier system only ever treats two VEHICLE
    shots as a top-tier match against each other, never against a person
    shot. The user's own framing: "face dengan face, mobil dengan mobil,
    motor dengan motor."

    Face-SIZE matching (as opposed to this framing bucket) is handled
    separately by `_face_width_fraction` — an OpenCV pixel measurement on the
    two already-chosen photos, not a vision guess. A first version asked
    this same call to also eyeball a face-width fraction per candidate, but
    that number was too imprecise to catch a real mismatch on a live test
    render (two HALF-body photos the model itself called "similar enough"
    that were visibly not) — a deterministic measurement on the final pair
    beats an LLM's guess across a dozen candidates at once."""
    content = [{
        "type": "text",
        "text": (
            "Classify EACH numbered photo below into ONE of these categories. "
            "HEAD (tight face / head-and-shoulders only), HALF (waist-up — "
            "chest and arms visible, e.g. a standing/podium/pit-lane shot), "
            "FULL (most of the person, head to at least mid-thigh) — these "
            "three are about how much of the PERSON's body shows. VEHICLE is "
            "different: use it when the car/motorcycle itself is the main "
            "subject of the frame and the rider/driver is small, unclear, "
            "turned away, or otherwise not the clear focus (e.g. a wide "
            "on-track action shot) — even if a person is technically visible "
            "in it. Reply with exactly ONE line per photo, in the form "
            "`LABEL:CATEGORY` (e.g. `L1:HALF`) — nothing else, no explanation."
        ),
    }]
    for label, uri in labeled:
        content.append({"type": "text", "text": f"{label}:"})
        content.append({"type": "image_url", "image_url": {"url": _vision_datauri(base64.b64decode(uri.split(",", 1)[1]))}})
    raw = _vision_chat(content, max_tokens=1500, context="vision_classify_split_frame")
    return {
        m.group(1).upper(): m.group(2).upper()
        for m in re.finditer(r"\b([LR]\d+)\s*:\s*(HEAD|HALF|FULL|VEHICLE)\b", raw, re.IGNORECASE)
    }


def _face_width_fraction(image_bytes: bytes) -> float | None:
    """Largest detected face's width as a fraction of the photo's total
    width — a real pixel measurement, not a vision model's guess (see
    _classify_split_frames docstring for why that guess wasn't precise
    enough). Tries YuNet first (see _dominant_face_bbox's docstring for
    why), falls back to the original Haar cascade. Returns None if no
    confident face is found (the caller then skips zoom correction for that
    photo rather than act on a guess)."""
    face = _yunet_dominant_face(image_bytes)
    if face is not None:
        fw_frac = face[2]
        return fw_frac if fw_frac >= 0.14 else None  # same trust floor as detect_focus_point
    try:
        import cv2
        import numpy as np

        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                         minSize=(int(w * 0.06), int(h * 0.06)))
        if not len(faces):
            return None
        _, _, fw, _ = max(faces, key=lambda f: f[2] * f[3])
        if fw < 0.14 * w:  # same "trust only clearly-sized faces" floor as detect_focus_point
            return None
        return float(fw) / float(w)
    except Exception as exc:
        logger.debug("_face_width_fraction failed: %s", exc)
        return None


def _vision_face_width_fraction(image_bytes: bytes) -> float | None:
    """Vision fallback for _face_width_fraction when OpenCV's Haar cascade
    finds no confident face — a known Haar weakness on a tilted/angled/
    chin-down/chin-on-fist pose (found on a real render: a Verstappen
    candidate in exactly that pose came back with no OpenCV detection, so
    the size-mismatch correction never even had a measurement to act on and
    silently no-opped — the underlying bug wasn't the correction logic,
    it was having no signal at all for that photo). Better an imprecise
    estimate than none. Asks about ONE image in isolation — not a batch of
    many candidates at once, which is what made the earlier all-in-one
    classify+fraction attempt imprecise (see _classify_split_frames'
    docstring) — a single focused image is a much easier estimate."""
    try:
        content = [
            {"type": "text", "text": (
                "What fraction of this photo's WIDTH does the person's face "
                "(just the face, not hair/head) take up? Reply with ONLY a "
                "number from 0.05 (tiny, far away) to 0.60 (extreme close-up "
                "filling the frame) — nothing else, no explanation."
            )},
            {"type": "image_url", "image_url": {"url": _vision_datauri(image_bytes)}},
        ]
        raw = _vision_chat(content, max_tokens=1500, context="vision_face_width_fraction")
        m = re.search(r"(0?\.\d+|1(?:\.0+)?)", raw)
        return float(m.group(1)) if m else None
    except Exception as exc:
        logger.debug("_vision_face_width_fraction failed: %s", exc)
        return None


def _pick_best_aesthetic_pair(left_candidates: list, right_candidates: list, primary: str,
                               secondary: str, image_type: str | None = None) -> tuple[int, int, bool]:
    """Given candidates ALREADY narrowed to the same framing category (see
    vision_pick_split_pair), pick the best-looking LEFT/RIGHT pair AND judge
    whether it's actually good enough to publish. Always makes this call —
    even with exactly one candidate per side — so a genuinely bad SINGLE
    option gets REJECTED instead of blindly waved through just because
    there was nothing to "choose" between; the caller then tries the next
    framing-distance tier instead of forcing a bad pair to render. Found via
    a real user-rejected render (2026-08-21): two same-category HALF-body
    photos, one badly underexposed/moody next to one bright/sunlit, plus
    stray background ad-board text awkwardly cropped into both strips —
    passed the OLD version of this function because there was only one
    same-category pair, so nothing was ever judged, just accepted.

    Returns (left_index, right_index, accepted)."""
    style = (
        "close-up FACE/head-and-shoulders portraits" if image_type == "face"
        else "ACTION shots (riding/driving/on track)" if image_type == "action"
        else "photos"
    )
    content = [{
        "type": "text",
        "text": (
            f"These are candidate {style} for a side-by-side split graphic: "
            f"{primary} on the LEFT half, {secondary} on the RIGHT half — all "
            "already matched for similar body-framing. Pick the best-LOOKING "
            "LEFT+RIGHT pair, judging:\n"
            "- HARD REJECT if one side is a vehicle-dominant shot (the car/bike "
            "is the clear subject, the rider/driver small or unclear) while the "
            "other is a person-forward shot (a clear face/body) — face-with-face, "
            "vehicle-with-vehicle only, never mixed, no exceptions.\n"
            "- Full face visible with margin — each photo is CROPPED HARD "
            "into a narrow vertical strip, so reject anything too tight to "
            "survive that.\n"
            "- Similar EXPOSURE/BRIGHTNESS between the two photos — one dark/"
            "moody photo next to one bright/sunlit one reads as broken, even "
            "if each looks fine alone.\n"
            "- No distracting background element that would get awkwardly "
            "cropped into the narrow strip — stray text/logo fragments, "
            "another person's head, a sign cut in half. Prefer a cleaner "
            "background over a busier one.\n"
            "- Similar facing direction and background tone, so the two "
            "halves read as one cohesive graphic.\n\n"
            f"LEFT candidates are numbered L1..L{len(left_candidates)}, then "
            f"RIGHT candidates are numbered R1..R{len(right_candidates)}.\n"
            "Reply on exactly TWO lines: line 1 is your best pick as `L# R#`; "
            "line 2 is `OK` if that pair is genuinely good enough to publish "
            "as-is, or `REJECT` if even your best pick has a real problem "
            "from the list above. Be honest — reply REJECT rather than pick "
            "something you wouldn't be happy to see published."
        ),
    }]
    for i, uri in enumerate(left_candidates):
        content.append({"type": "text", "text": f"L{i + 1}:"})
        content.append({"type": "image_url", "image_url": {"url": _vision_datauri(base64.b64decode(uri.split(",", 1)[1]))}})
    for j, uri in enumerate(right_candidates):
        content.append({"type": "text", "text": f"R{j + 1}:"})
        content.append({"type": "image_url", "image_url": {"url": _vision_datauri(base64.b64decode(uri.split(",", 1)[1]))}})
    raw = _vision_chat(content, max_tokens=1500, context="vision_pick_split_pair")
    lm = re.search(r"L\s*(\d+)", raw, re.IGNORECASE)
    rm = re.search(r"R\s*(\d+)", raw, re.IGNORECASE)
    li = (int(lm.group(1)) - 1) if lm else 0
    ri = (int(rm.group(1)) - 1) if rm else 0
    li = li if 0 <= li < len(left_candidates) else 0
    ri = ri if 0 <= ri < len(right_candidates) else 0
    accepted = not re.search(r"\bREJECT\b", raw, re.IGNORECASE)
    return li, ri, accepted


# Assumed split-slot geometry (see build_split_srcs / load_real_split_template
# in the test harness). vision_pick_split_pair runs before any template is
# chosen, so this can't be read from the actual render target — kept in
# sync by hand with whatever the test harness's real-template mutation
# currently uses. Height is 880, NOT the full 1350 canvas height: at full
# height a 540-wide slot's aspect ratio (1:2.5) forces such a severe
# horizontal crop that the zoom-correction tuning in this file couldn't
# actually fix it — found 2026-08-22 via user feedback on the full-height
# version ("terlalu di zoom, bibirnya sampai tidak kelihatan" — BOTH sides
# too tight, not a left/right mismatch this file's corrections address).
# 880 overlaps the real template's scrim (fixed at top=800) by 80px so the
# photo/text seam still blends for free. If the seeded split template's
# geometry ever changes, this needs to move with it.
_SPLIT_SLOT_W = 540
_SPLIT_SLOT_H = 880
# Fraction of the zoomed visible window a face is allowed to fill at most —
# the remaining fraction is safety margin so a real face (which is wider
# than the tight "face" landmark box detection measures) doesn't touch the
# edge.
_SPLIT_ZOOM_SAFETY_MARGIN = 0.15
# Absolute ceiling regardless of how much margin the geometry says is
# available. A photo where the face starts out tiny (a wide far-away shot)
# can compute a huge geometrically-"safe" z_max — nothing would crop — but
# blowing a small face up 2x+ still looks visibly soft/upscaled and, more
# fundamentally, means that photo was a poor candidate for THIS pairing to
# begin with (its native framing is too far from its partner's for a zoom
# fix to paper over). Found 2026-08-22: z_max alone produced 1.63x/2.13x
# corrections that were technically crop-safe but still looked wrong —
# adaptive-per-photo fixes the CROP-SAFETY failure mode, this ceiling
# guards the separate "just don't upscale that much" one.
_MAX_SPLIT_ZOOM_ABSOLUTE = 1.3
# Below this face-size ratio (smaller/larger), don't bother correcting —
# the difference isn't visually significant and the fraction estimate
# itself is a rough vision guess, not a measurement.
_SPLIT_ZOOM_RATIO_THRESHOLD = 1.08


def _max_safe_zoom(image_bytes: bytes, face_width_frac: float) -> float:
    """How much EXTRA zoom (beyond the base cover-fit crop) THIS SPECIFIC
    photo can take before its face would spill outside the split slot's
    visible window — computed per photo from its own real dimensions and
    face size, not a single flat number applied to every image.

    Replaced a flat `_MAX_SPLIT_ZOOM` constant (2026-08-21) after user
    feedback made the tradeoff explicit either way: tuned loose enough
    (1.5) to close a real size gap, it cropped through a face on a
    different, tighter-cropped photo; tuned conservative enough (1.15) to
    be safe there, it under-corrected a pair with real spare margin (still
    visibly mismatched). Both failures trace to the same root cause: a
    fixed cap can't be simultaneously right for a photo with lots of margin
    and one with almost none — "every image is different" (user, verbatim)
    — so the cap has to be computed FROM each photo's own geometry instead
    of guessed as one constant for all of them.

    Math: the base cover-fit crop (zoom=1.0) already shows
    `_SPLIT_SLOT_W / (scale * img_w)` of the source photo's width, where
    `scale = max(_SPLIT_SLOT_W/img_w, _SPLIT_SLOT_H/img_h)`. Extra zoom `z`
    shrinks that visible fraction to `.../z`. Solving for the largest `z`
    that still keeps the face within `(1 - _SPLIT_ZOOM_SAFETY_MARGIN)` of
    that shrinking window gives a real per-photo ceiling instead of a
    guess. Returns 1.0 (no safe headroom to zoom at all) on any failure to
    read the image or a degenerate face fraction."""
    if face_width_frac <= 0:
        return 1.0
    try:
        import cv2
        import numpy as np

        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return 1.0
        img_h, img_w = img.shape[:2]
        if not img_w or not img_h:
            return 1.0
    except Exception as exc:
        logger.debug("_max_safe_zoom failed: %s", exc)
        return 1.0

    scale = max(_SPLIT_SLOT_W / img_w, _SPLIT_SLOT_H / img_h)
    visible_width_frac_at_1x = (_SPLIT_SLOT_W / scale) / img_w
    z_max = (visible_width_frac_at_1x * (1 - _SPLIT_ZOOM_SAFETY_MARGIN)) / face_width_frac
    return max(1.0, min(z_max, _MAX_SPLIT_ZOOM_ABSOLUTE))


def vision_pick_split_pair(left_candidates: list, right_candidates: list, primary: str, secondary: str,
                            image_type: str | None = None) -> tuple[int, int, float, float, bool]:
    """For a side-by-side split (two subjects, one photo each), pick the
    LEFT/RIGHT candidate pair whose pose and framing match best — same rough
    zoom level (both head-and-shoulders or both waist-up, not one tight face
    crop next to one wide action shot), similar facing direction, and a
    similar background tone — so the two halves read as one cohesive graphic
    instead of two unrelated stock photos stitched together.

    Three-phase: (1) `_classify_split_frames` labels every candidate's
    body-framing in one call, then this function groups every (left, right)
    index pair by framing-category distance in plain Python (a hard
    constraint, not a hoped-for instruction) — distance 0 = exact
    same-category match; (2) starting from the SMALLEST distance tier,
    `_pick_best_aesthetic_pair` both picks the best-looking pair in that tier
    AND judges whether it's actually good enough (full face w/ margin,
    matched exposure/lighting, no distracting cropped background element) —
    if rejected, move to the next tier instead of forcing a bad pair through
    (found via a real user-rejected render: same-category but one photo
    underexposed next to one bright, plus stray background text cropped
    into both strips — the old version only ran this check when MULTIPLE
    pairs tied on distance, so a lone same-category pair got waved through
    unjudged); every tier exhausted with no accept falls back to the closest
    tier's top pick rather than produce nothing; (3) even within a matched
    pair, one subject's face can still render visibly smaller than the
    other's — the chosen pair's face fractions are measured (OpenCV, see
    `_face_width_fraction`) and a corrective zoom computed for the
    smaller-faced side, capped per-photo by `_max_safe_zoom` (not a flat
    constant — see its docstring for why), for the renderer to apply on top
    of its normal cover-fit crop.

    `left_candidates`/`right_candidates` are lists of data-URIs (already
    identity-verified — this call only judges composition, not who's in the
    photo). Returns (left_index, right_index, left_zoom, right_zoom,
    accepted). `accepted=False` means every framing tier's best pick still
    failed the aesthetic/quality gate (see _pick_best_aesthetic_pair) — e.g.
    the only available photo of one subject has their face turned away or
    buried in a crowd. The caller (build_split_srcs) must treat that as "no
    acceptable split pair" and let prepare_design_images fall back to a
    single photo, rather than ship the rejected pick — found 2026-08-22 via
    two real user-rejected renders (Verstappen buried in crowd/smoke;
    Makhachev showing the back of his head) that BOTH hit this exact
    rejected-but-shipped-anyway path. This is the same "a bad photo is worse
    than no photo" principle already applied elsewhere in this file (see
    vision_verify_match/vision_verify_subject's fail-closed behavior) —
    previously this was the one place in the split pipeline that failed
    OPEN instead. Falls back to (0, 0, 1.0, 1.0, True) if vision is
    unavailable or either list is empty — a hard error, not a quality
    judgment, so there's no "rejection" signal to honor; best-effort still
    beats crashing the whole render."""
    if not left_candidates or not right_candidates:
        return 0, 0, 1.0, 1.0, True
    if len(left_candidates) == 1 and len(right_candidates) == 1:
        return 0, 0, 1.0, 1.0, True
    try:
        left_labels = [f"L{i + 1}" for i in range(len(left_candidates))]
        right_labels = [f"R{j + 1}" for j in range(len(right_candidates))]
        frames = _classify_split_frames(
            list(zip(left_labels, left_candidates)) + list(zip(right_labels, right_candidates))
        )
        # A label the model's reply didn't cover (parse miss) defaults to
        # HALF — the middle rank, so it's never automatically the "best"
        # match nor the "worst" mismatch against an unknown partner.
        def rank(label: str) -> int:
            return _FRAME_RANK.get(frames.get(label, ""), 1)

        by_dist: dict[int, list[tuple[int, int]]] = {}
        for li in range(len(left_candidates)):
            for ri in range(len(right_candidates)):
                d = abs(rank(left_labels[li]) - rank(right_labels[ri]))
                by_dist.setdefault(d, []).append((li, ri))

        li = ri = None
        fallback: tuple[int, int] | None = None
        for d in sorted(by_dist):
            pairs = by_dist[d]
            left_idx = sorted({p[0] for p in pairs})
            right_idx = sorted({p[1] for p in pairs})
            sub_li, sub_ri, accepted = _pick_best_aesthetic_pair(
                [left_candidates[i] for i in left_idx],
                [right_candidates[j] for j in right_idx],
                primary, secondary, image_type,
            )
            cand = (left_idx[sub_li], right_idx[sub_ri])
            if fallback is None:
                fallback = cand  # closest-framing tier's top pick, kept as a last resort
            if accepted:
                li, ri = cand
                break

        if li is None:
            # Every tier's best pick was rejected on quality (mismatched
            # exposure, distracting crop, a subject whose face isn't even
            # visible, etc.) — do NOT ship the closest-tier pick anyway
            # (2026-08-22: that's exactly what produced two real
            # user-rejected renders). No acceptable pair exists among these
            # candidates; tell the caller so it can fall back to a single
            # photo instead.
            logger.warning(
                "vision_pick_split_pair: every framing tier rejected on quality for %r|%r — no acceptable split pair",
                primary, secondary,
            )
            return fallback[0], fallback[1], 1.0, 1.0, False

        zoom_l, zoom_r = 1.0, 1.0
        left_bytes = base64.b64decode(left_candidates[li].split(",", 1)[1])
        right_bytes = base64.b64decode(right_candidates[ri].split(",", 1)[1])
        # Vision fallback ONLY when OpenCV finds nothing (angled/tilted pose
        # it can't detect) — never overrides a real OpenCV measurement, so
        # the common case stays free and precise.
        frac_l = _face_width_fraction(left_bytes) or _vision_face_width_fraction(left_bytes)
        frac_r = _face_width_fraction(right_bytes) or _vision_face_width_fraction(right_bytes)
        if frac_l and frac_r and frac_l > 0 and frac_r > 0:
            ratio = max(frac_l, frac_r) / min(frac_l, frac_r)
            if ratio >= _SPLIT_ZOOM_RATIO_THRESHOLD:
                # Cap computed from THIS photo's own geometry (see
                # _max_safe_zoom), not a flat constant — every photo has a
                # different amount of safe margin.
                if frac_l < frac_r:
                    zoom_l = min(ratio, _max_safe_zoom(left_bytes, frac_l))
                else:
                    zoom_r = min(ratio, _max_safe_zoom(right_bytes, frac_r))
        return li, ri, zoom_l, zoom_r, True
    except Exception as exc:
        logger.warning("vision_pick_split_pair failed (%s) — using first of each", exc)
        return 0, 0, 1.0, 1.0, True


def vision_verify_match(image_bytes: bytes, title: str, excerpt: str = "", niche: str = "") -> dict:
    """Ask 9Router vision whether a candidate photo actually depicts this news
    story (same person(s)/team/vehicle/event) rather than a generic or
    unrelated stock photo. Used to gate the article's scraped hero image and
    fresh topic-search results before trusting them — see
    design_renderer.select_image_for_job and fetch_topic_datauri.

    `niche` (e.g. "MotoGP", "UFC") disambiguates name collisions across
    fields — a bare-name Google/Getty search for someone like a team
    principal or a common name can just as easily surface an unrelated
    person who happens to share it in a totally different sport/industry.
    Passed straight through to the prompt so the model checks identity
    within the right context, not just visual plausibility.

    Returns {"match": bool, "confidence": 0.0-1.0}. Fails CLOSED (match=False)
    on any parse/API error (changed 2026-08-20 — real posts had nearly gone
    out with a mismatched photo under the old fail-open behavior): this is
    the only gate fetch_topic_datauri has on an UNVERIFIED fresh Google/Getty
    topic search, its riskiest photo source since it's not searching for a
    named subject. An unverifiable candidate is treated the same as a "no" —
    consistent with fetch_topic_datauri's own stated principle that a wrong
    photo is worse than no photo, so failing this check just means that one
    candidate is skipped, not that the whole pipeline blocks."""
    try:
        content = [
            {"type": "text", "text": (
                f'News headline: "{title[:200]}"\n'
                + (f'Niche/field: "{niche}"\n' if niche else "")
                + (f'Article excerpt: "{excerpt[:400]}"\n' if excerpt else "")
                + "Does this photo actually depict THIS story's subject — the "
                "same named person(s), team, vehicle, or event/scene the "
                "headline is about? A generic/unrelated stock photo, or a "
                "photo of a clearly different person or team, is NOT a match — "
                "including someone who merely SHARES A NAME with the subject "
                "but belongs to a different field than the niche above (e.g. "
                "a same-named person from another sport, industry, or era).\n"
                'Reply with ONLY a JSON object {"match": true|false, "confidence": 0.00-1.00}.'
            )},
            {"type": "image_url", "image_url": {"url": _vision_datauri(image_bytes)}},
        ]
        raw = _vision_chat(content, max_tokens=1500, context="vision_verify_match")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {"match": False, "confidence": 0.0}
        import json as _json
        d = _json.loads(m.group(0))
        return {"match": bool(d.get("match")), "confidence": float(d.get("confidence") or 0)}
    except Exception as exc:
        logger.warning("vision_verify_match failed (treating as no-match): %s", exc)
        return {"match": False, "confidence": 0.0}


def vision_verify_subject(image_bytes: bytes, subject: str, niche: str = "") -> dict:
    """Ask 9Router vision whether a candidate photo is actually a picture of
    `subject` (as opposed to a same-named but different person, or a
    generic/wrong photo). Gates the two subject-photo sources that had NO
    identity check at all before 2026-08-20 — find_gallery_datauri (a bare
    keyword/name substring match; the photo could have been mistagged, or a
    different niche's identically-named subject) and fetch_subject_datauri
    (a fresh Getty/Google search keyed on name+niche text, which steers
    results but never confirms them). Both previously handed vision_pick_best
    a candidate pool it only ranks by clarity/quality, never by identity —
    found after real posts nearly went out with the wrong same-named person's
    photo (e.g. a UFC fighter's search returning an unrelated person who
    happens to share the name).

    Returns {"match": bool, "confidence": 0.0-1.0}. Fails CLOSED (match=False)
    on any parse/API error, same reasoning as vision_verify_match — treating
    an unverifiable candidate as a non-match just means it's skipped, since
    every caller already has a further fallback (another candidate, a fresh
    search, or a "needs manual image" state) rather than nothing at all."""
    try:
        content = [
            {"type": "text", "text": (
                f'Subject: "{subject}"\n'
                + (f'Niche/field: "{niche}"\n' if niche else "")
                + "Is this photo actually of THIS specific person — the named "
                "subject above, in the stated niche/field? Reply NOT a match "
                "if this is clearly a different person, even one who shares "
                "the same (or a similar) name but belongs to a different "
                "sport, industry, or era, or if the photo doesn't clearly "
                "show an identifiable person at all.\n"
                'Reply with ONLY a JSON object {"match": true|false, "confidence": 0.00-1.00}.'
            )},
            {"type": "image_url", "image_url": {"url": _vision_datauri(image_bytes)}},
        ]
        raw = _vision_chat(content, max_tokens=1500, context="vision_verify_subject")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {"match": False, "confidence": 0.0}
        import json as _json
        d = _json.loads(m.group(0))
        return {"match": bool(d.get("match")), "confidence": float(d.get("confidence") or 0)}
    except Exception as exc:
        logger.warning("vision_verify_subject failed (treating as no-match): %s", exc)
        return {"match": False, "confidence": 0.0}


def vision_check_pin_description(image_bytes: bytes, description: str, niche: str = "", custom_prompt: str = "") -> dict:
    """Mode 5 (Pinterest): a curated pin's own page sometimes carries a real
    caption (see image_downloader.fetch_pin) — before trusting it as the
    idea's title/description, ask vision whether the photo actually matches
    what it claims. Same fail-closed convention as vision_verify_match: an
    unverifiable description is treated as wrong, not trusted by default
    (see feedback-image-accuracy-over-availability — a wrong caption on a
    real post is worse than skipping this one candidate).

    `custom_prompt` (fanpage.pinterest_custom_prompt — e.g. "You are a
    passionate Formula 1 historian...") steers the WRITING STYLE of the
    cleaned description that comes back, so every idea for this page reads
    in the page's own persona/voice, not a generic factual caption.

    Returns {"valid": bool, "title": str, "description": str} — on
    valid=True the model hands back both a short title (the queue needs one
    and Pinterest's own description text is prose, not a title) and a
    tightened/cleaned version of the description rather than the raw
    scraped text verbatim."""
    try:
        content = [
            {"type": "text", "text": (
                (f"{custom_prompt.strip()}\n" if custom_prompt else "")
                + f'Claimed description of this photo: "{description[:400]}"\n'
                + (f'Niche/field: "{niche}"\n' if niche else "")
                + "Does this photo actually match that description — same "
                "named person(s)/team/vehicle/event, no factual mismatch? "
                "Reply invalid if the description is wrong, generic, or "
                "doesn't match what's actually shown.\n"
                'Reply with ONLY a JSON object {"valid": true|false, '
                '"title": "short name/subject, max 60 chars, empty string '
                'if invalid", "description": "a clean 1-2 sentence version '
                'of the description if valid, else empty string"}.'
            )},
            {"type": "image_url", "image_url": {"url": _vision_datauri(image_bytes)}},
        ]
        raw = _vision_chat(content, max_tokens=1500, context="vision_check_pin_description")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {"valid": False, "title": "", "description": ""}
        import json as _json
        d = _json.loads(m.group(0))
        return {
            "valid": bool(d.get("valid")),
            "title": str(d.get("title") or "").strip()[:60],
            "description": str(d.get("description") or "").strip(),
        }
    except Exception as exc:
        logger.warning("vision_check_pin_description failed (treating as invalid): %s", exc)
        return {"valid": False, "title": "", "description": ""}


def vision_identify_pin_subject(image_bytes: bytes, niche: str = "", custom_prompt: str = "") -> dict:
    """Mode 5 (Pinterest): a board/search-grid pin has no caption at all
    (confirmed — Pinterest's grid markdown never carries one) — ask vision
    to identify what's actually in the photo from scratch. Fails CLOSED
    (identified=False) whenever the model isn't confident of a SPECIFIC,
    named subject — never fabricate a caption for a photo nobody can
    actually place; the candidate is simply skipped by the caller, same
    principle as vision_verify_subject.

    `custom_prompt` (fanpage.pinterest_custom_prompt) steers the generated
    description's voice — see vision_check_pin_description's docstring.

    Returns {"identified": bool, "title": str, "description": str}."""
    try:
        content = [
            {"type": "text", "text": (
                (f"{custom_prompt.strip()}\n" if custom_prompt else "")
                + (f'Niche/field: "{niche}"\n' if niche else "")
                + "Identify the specific, named person, team, vehicle, or "
                "event/moment shown in this photo, within the niche above. "
                "Only answer if you're genuinely confident — a generic or "
                "unrecognizable photo (crowd, unnamed car, unclear scene) "
                "should NOT be identified.\n"
                'Reply with ONLY a JSON object {"identified": true|false, '
                '"title": "short name/subject, max 60 chars", "description": '
                '"1-2 sentence caption suitable for a social media post"}.'
            )},
            {"type": "image_url", "image_url": {"url": _vision_datauri(image_bytes)}},
        ]
        raw = _vision_chat(content, max_tokens=1500, context="vision_identify_pin_subject")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {"identified": False, "title": "", "description": ""}
        import json as _json
        d = _json.loads(m.group(0))
        return {
            "identified": bool(d.get("identified")),
            "title": str(d.get("title") or "").strip()[:60],
            "description": str(d.get("description") or "").strip(),
        }
    except Exception as exc:
        logger.warning("vision_identify_pin_subject failed (treating as unidentified): %s", exc)
        return {"identified": False, "title": "", "description": ""}


def vision_has_watermark(image_bytes: bytes) -> bool:
    """Mode 5 (Pinterest): stock/editorial photo agencies (LAT Images, XPB,
    RaceFans, etc.) commonly stamp a visible logo/URL/copyright mark on their
    images. The Quote/News templates usually crop tight enough to cut it out
    by luck, but the no-crop direct-post fallback (photo_crops_well=False,
    design_renderer.render_pinterest) posts the frame untouched — so an
    agency mark there stays fully visible on the published post. Real batch
    testing this session (3/3 direct-post samples checked) surfaced this.
    Fails CLOSED (assume a watermark is present) on any error — same
    convention as vision_check_pin_description/vision_identify_pin_subject;
    an unverifiable photo is skipped, not risked."""
    try:
        content = [
            {"type": "text", "text": (
                "Does this photo have a visible watermark, logo, website URL, "
                "or copyright/credit text stamped on it by a photo agency or "
                "media outlet (e.g. a corner logo, a semi-transparent URL "
                "overlay, a \"© ...\" credit line)? Ignore text/logos that are "
                "part of the actual photographed scene itself (sponsor "
                "decals on a car or racesuit, signage in the background).\n"
                'Reply with ONLY a JSON object {"has_watermark": true|false}.'
            )},
            {"type": "image_url", "image_url": {"url": _vision_datauri(image_bytes)}},
        ]
        raw = _vision_chat(content, max_tokens=200, context="vision_has_watermark")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return True
        import json as _json
        d = _json.loads(m.group(0))
        return bool(d.get("has_watermark", True))
    except Exception as exc:
        logger.warning("vision_has_watermark failed (treating as watermarked): %s", exc)
        return True


def _filter_verified_subject(uris: list[str], subject: str, niche: str = "") -> list[str]:
    """Run vision_verify_subject over each candidate data-URI, keeping only
    the ones confirmed to actually be `subject`. Shared by
    find_gallery_datauri and fetch_subject_datauri so both get the same
    identity gate before vision_pick_best ranks whatever survives."""
    verified = []
    for uri in uris:
        try:
            raw_bytes = base64.b64decode(uri.split(",", 1)[1])
        except Exception:
            continue
        if vision_verify_subject(raw_bytes, subject, niche)["match"]:
            verified.append(uri)
    return verified


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


def _gallery_verified_candidates(
    db, subject: str, exclude_path: str | None = None, use_vision: bool = True,
    image_type: str | None = None, allow_stale_reuse: bool = True, niche: str = "",
    pool_limit: int = 8,
):
    """Shared lookup behind find_gallery_datauri and find_gallery_datauris:
    matching gallery rows for `subject`, honoring the reuse cooldown and
    favoring freshest-shot eligible candidates (see _eligible_rows), then
    identity-verified (vision_verify_subject) when use_vision. Returns a
    list of (datauri, GalleryImage) tuples, NOT yet reduced to a winner.

    `pool_limit` is how many raw DB rows _eligible_rows samples BEFORE
    identity verification (default 8, matching the single-photo callers).
    A caller doing its own joint selection over a shrunk post-verification
    list (e.g. build_split_srcs picking a pose-matched pair) should pass a
    higher value — the single-photo pick only ever needs its eventual
    winner to survive verification, but a joint pick needs enough SURVIVORS
    left after verification to have real pairing choices, and identity
    verification alone already discards a chunk of any raw sample."""
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
        rows = _eligible_rows(base.filter(GalleryImage.label == image_type), limit=pool_limit, allow_stale_reuse=allow_stale_reuse)
    if not rows:
        rows = _eligible_rows(base, limit=pool_limit, allow_stale_reuse=allow_stale_reuse)

    usable = [gi for gi in rows if gi.local_path and gi.local_path != exclude_path and os.path.exists(gi.local_path)]
    if not usable:
        return []

    uris = [file_to_datauri(gi.local_path) for gi in usable]
    if use_vision:
        by_uri = {uri: gi for uri, gi in zip(uris, usable)}
        uris = _filter_verified_subject(uris, subject, niche)
        if not uris:
            return []
        usable = [by_uri[uri] for uri in uris]
    return list(zip(uris, usable))


def find_gallery_datauri(
    db, subject: str, exclude_path: str | None = None, use_vision: bool = True,
    image_type: str | None = None, allow_stale_reuse: bool = True, niche: str = "",
):
    """Find the best gallery image whose keyword matches the subject — see
    _gallery_verified_candidates for the matching/verification rules. 9Router
    vision picks the best of what's left, constrained to `image_type`
    ("face"/"action") so split layouts stay consistent. Marks the picked
    image used.

    The identity check matters here specifically because the initial match
    is a bare keyword/extra_keywords substring — a photo tagged
    "anthony smith" could be a different Anthony Smith than the one this
    call means, or simply mistagged; nothing before this confirmed WHO is in
    the photo, only that it downloaded under a matching label. Found
    2026-08-20 after real posts nearly went out with a same-named but wrong
    person's photo.

    `allow_stale_reuse=False` makes this return (None, None) when the cooldown
    pool is empty instead of reusing a stale photo — pass this when the
    caller has a fresher fallback (a live Getty/Google search) it should try
    first, then call this again with the default True as the true last
    resort if that fresh search also finds nothing."""
    candidates = _gallery_verified_candidates(
        db, subject, exclude_path, use_vision, image_type, allow_stale_reuse, niche,
    )
    if not candidates:
        return None, None
    uris = [uri for uri, _ in candidates]
    best = vision_pick_best(uris, subject, image_type=image_type) if use_vision else 0
    picked_uri, picked_gi = candidates[best]
    _mark_gallery_image_used(db, picked_gi)
    return picked_uri, picked_gi


def find_gallery_datauris(
    db, subject: str, exclude_path: str | None = None, image_type: str | None = None,
    allow_stale_reuse: bool = True, niche: str = "", top_n: int = 3, pool_limit: int = 8,
):
    """Like find_gallery_datauri but returns up to `top_n` identity-verified
    candidate (datauri, GalleryImage) tuples instead of picking a winner —
    for callers doing their own joint selection (e.g. a pose-matched split
    pair). Always identity-verifies (there's no single-winner vision_pick_best
    step here to skip). See _gallery_verified_candidates for `pool_limit`."""
    candidates = _gallery_verified_candidates(
        db, subject, exclude_path, True, image_type, allow_stale_reuse, niche, pool_limit,
    )
    return candidates[:top_n]


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
    """Return (primary, secondary|None). Only returns two names when a
    side-by-side split illustration is actually the right editorial call —
    NOT just whenever two people happen to be named. See build_prompt below
    for the distinction (a name mentioned in passing vs. two people the
    graphic should actually put face-to-face).

    Real production incidents, 2026-08-27 (8 examples, one Repliz ID each:
    6415b97c, 278841cd, 277e89c3, 643e2381, 643e25a3, 279605f4, 6445414c,
    27980e3e): every one was a genuinely single-subject story — a farewell/
    retirement, a signing, a team-boss's commentary ABOUT a driver, a quote
    comparing several legends — that still got split. Sharpening the prompt
    with concrete negative examples (see _extract_two_subjects_once) measured
    10/10 correct across all 8 real headlines, 2 runs each — but a SEPARATE
    re-render of the same headline the same day still reproduced a wrong
    split (Bastianini paired with Raul Fernandez, the passing "incoming
    teammate" mention the prompt explicitly warns against). One call to a
    non-deterministic model, however well-prompted, cannot be trusted as
    the sole gate for a decision this consequential — this function now
    calls it TWICE and only accepts a two-person split when both calls
    independently agree on the exact same pairing; any disagreement (the
    far more common outcome for a genuinely borderline/wrong case — a
    headline that actually deserves a duel reads as unambiguous to the
    model both times, while an incidental-mention case tends to waffle)
    falls back to the single-subject result instead of risking a second
    wrong split."""
    primary1, secondary1 = _extract_two_subjects_once(title, niche)
    if not secondary1:
        return primary1, None
    primary2, secondary2 = _extract_two_subjects_once(title, niche)
    if (
        secondary2
        and primary1.strip().lower() == primary2.strip().lower()
        and secondary1.strip().lower() == secondary2.strip().lower()
    ):
        return primary1, secondary1
    logger.info(
        "extract_two_subjects: disagreement across 2 calls (%r|%r vs %r|%r) — falling back to single-subject",
        primary1, secondary1, primary2, secondary2,
    )
    return primary1, None


def _extract_two_subjects_once(title: str, niche: str):
    """Single raw call — see extract_two_subjects (the public entry point)
    for why this is never trusted alone."""
    from app.services.ai_caption import generate_caption

    prompt = (
        f'Act as a professional sports graphic designer laying out a social '
        f'media card for this {niche} headline: "{title}".\n\n'
        "Decide how many people this card should feature, as a real editorial "
        "call — not just a name count:\n"
        '- TWO people, side-by-side (`Name One | Name Two`) — ONLY when the '
        "headline is genuinely about a head-to-head: a duel, rivalry, "
        "comparison ('X vs Y', 'who's better'), a direct exchange between "
        "them (X said something ABOUT or TO Y, a clash/incident between "
        "them), or they share the moment equally (both won, both were "
        "involved in the same incident). Ask yourself: would a reader "
        "instinctively expect to see BOTH faces side by side for this story? "
        "If the honest answer is anything less than a clear yes, don't force "
        "a two-name reply just because a second name appears in the text.\n"
        "- ONE person — the default whenever a single individual is clearly "
        "the story's main subject, even if one or more OTHER named people are "
        "mentioned as context, a source, an opponent they merely faced, or a "
        "minor detail. A single strong hero photo beats a forced pairing.\n"
        "- `NONE` — no individual person is the subject at all (an "
        "organization, event, vehicle, or decision instead).\n\n"
        "Real examples that are ONE person, not two, even though a second "
        "name appears — study the pattern, don't just pattern-match on "
        "'two names present':\n"
        '- "Team bids farewell to Rider A" + a mention that Rider A joins '
        '"Team B alongside Rider C" next season → ONE (Rider A). Rider C is '
        "a future teammate mentioned in passing, not part of this story's "
        "moment.\n"
        '- "Ducati bosses confirm Rider A\'s deal with Team B" (owned/run by '
        'Legend C) → ONE (Rider A). Legend C is named only because they own '
        "the team, not because the story is about the two of them together.\n"
        '- "Team boss X raises a theory about Driver Y\'s new role" → ONE '
        "(Driver Y, the actual subject of the theory) — X is the source "
        "making a claim, not a second face this graphic should show; if the "
        "claim is specifically about Y being ordered to help teammate Z, "
        "consider Y|Z instead of X, but never X paired with anyone.\n"
        '- A quote comparing several legends (\"A was spiritual, B was '
        'Germanic, C keeps you at a distance\") credited to speaker D → ONE '
        "(D, the speaker) — A/B/C are a passing comparison, not this card's "
        "subject, and are never a valid pairing with D or each other here.\n\n"
        "Reply with ONLY the result in the exact format above — the one/two "
        "name(s) or NONE. People only — no teams/brands. No explanation."
    )
    try:
        out, _ = generate_caption(prompt)
        s = (out or "").strip().strip('".')
        if not s or s.upper() == "NONE":
            return None, None
        parts = [p.strip() for p in s.split("|") if p.strip() and p.strip().upper() != "NONE"]
        if len(parts) >= 2:
            primary, secondary = parts[0][:40], parts[1][:40]
            # Real incident, 2026-08-27: the model returned the SAME person
            # as both primary and secondary for a single-subject story (see
            # extract_two_subjects's docstring) — both split halves rendered
            # as two different photos of one person. A cheap, deterministic
            # guard: normalized-identical names can never be a valid pairing.
            if primary.strip().lower() == secondary.strip().lower():
                return primary, None
            return primary, secondary
        return (parts[0][:40] if parts else None), None
    except Exception as exc:
        logger.warning("_extract_two_subjects_once failed: %s", exc)
        return None, None


def _fetch_verified_subject_candidates(db, subject: str, image_type: str = "face", niche: str = "MotoGP", max_candidates: int = 5):
    """Fetch fresh, context-appropriate candidate photos of `subject` straight
    from the Getty search (via 9Router/jina), store the ones that pass the
    same quality gate the scheduled downloader uses (dest keyword = the
    subject, lowercased), and identity-verify each before returning.
    `image_type` shapes the query ("face" → portrait, "action" → riding).
    Returns a list of (GalleryImage, datauri) tuples, ranked as Getty
    returned them (newest/most-relevant first) — NOT yet reduced to a single
    winner, so callers can do their own joint selection (e.g. picking a
    pose-matched pair for a split layout).

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
        return []

    candidate_urls: list[str] = []
    seen: set = set()
    for m in _IMG_URL_RE.finditer(md):
        u = m.group(0)
        if u in seen:
            continue
        seen.add(u)
        candidate_urls.append(u)
    if not candidate_urls:
        return []

    skip_urls = {
        u for (u,) in
        db.query(GalleryImage.source_image_url).filter(GalleryImage.keyword == keyword).all()
    }
    dest_dir = Path(s.storage_base_path) / "gallery" / keyword_slug(keyword)
    saved = _fetch_and_store(candidate_urls, dest_dir, max_candidates, (300, 300), skip_urls, "9router-live", subject=subject)
    if not saved:
        return []

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
        return []

    # Identity-verify each candidate before ranking — this query's own text
    # match (subject+niche keywords) steers Getty but never confirms the
    # result; without this, a wrong same-named person (or, per the
    # "None"-subject bug fixed 2026-08-20, a nonsense query landing on an
    # unrelated stock photo) would go straight to vision_pick_best, which
    # only ranks clarity/quality, never identity.
    verified = [
        (gi, uri) for gi, uri in survivors
        if vision_verify_subject(base64.b64decode(uri.split(",", 1)[1]), subject, niche)["match"]
    ]
    if not verified:
        logger.info("fetch_subject_datauri: no candidate verified as %r among %d stored", subject, len(survivors))
    return verified


def fetch_subject_datauri(db, subject: str, image_type: str = "face", niche: str = "MotoGP", max_candidates: int = 5):
    """Single-best convenience wrapper around _fetch_verified_subject_candidates
    for callers that just want one photo (discussion cards, the inset flow).
    Returns a data-URI or None."""
    verified = _fetch_verified_subject_candidates(db, subject, image_type, niche, max_candidates)
    if not verified:
        return None
    uris = [uri for _, uri in verified]
    best = vision_pick_best(uris, subject, image_type=image_type)
    picked_gi, picked_uri = verified[best]
    _mark_gallery_image_used(db, picked_gi)
    logger.info(
        "fetch_subject_datauri: %r (%s) → %d candidate(s) verified, picked %d",
        subject, image_type, len(verified), best,
    )
    return picked_uri


def fetch_subject_datauris(db, subject: str, image_type: str = "face", niche: str = "MotoGP",
                            max_candidates: int = 5, top_n: int = 3):
    """Like fetch_subject_datauri but returns up to `top_n` identity-verified
    candidate (GalleryImage, datauri) tuples instead of picking a winner —
    for callers doing their own joint selection (e.g. a pose-matched split
    pair)."""
    verified = _fetch_verified_subject_candidates(db, subject, image_type, niche, max_candidates)
    return verified[:top_n]


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
        check = vision_verify_match(data, title, excerpt, niche)
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
        allow_stale_reuse=False, niche=niche,
    )
    if uri:
        logger.info("Recreate main: fresh gallery photo for %r (%s)", primary, image_type)
        return uri, (gi.local_path if gi else None)
    uri = fetch_subject_datauri(db, primary, image_type, niche)
    if uri:
        logger.info("Recreate main: fresh Getty photo for %r (%s)", primary, image_type)
        return uri, None
    uri, gi = find_gallery_datauri(
        db, primary, exclude_path=exclude_path, use_vision=use_vision, image_type=image_type, niche=niche,
    )
    if uri:
        logger.info("Recreate main: stale gallery photo for %r (%s) — nothing fresher available", primary, image_type)
        return uri, (gi.local_path if gi else None)
    logger.info("Recreate main: no photo for %r — falling back to IG image", primary)
    return None, None


def build_split_srcs(db, primary: str, secondary: str, image_type: str = "face", niche: str = "MotoGP",
                      slot_w: int | None = None, slot_h: int | None = None):
    """Two context-appropriate photos (same style) for a left/right split.
    Fetches several fresh candidates from Getty per side, and ALWAYS also
    pulls from the existing gallery (an already-used photo is fair game —
    see the note below), then asks vision to pick the LEFT/RIGHT pair whose
    pose/framing match best (see vision_pick_split_pair) — a wide action
    shot next to a tight face crop reads as sloppy even when both photos are
    individually fine, so the pairing is judged jointly rather than picking
    each side's "best" photo in isolation.

    `slot_w`/`slot_h` (pixel dims of the split slot at render scale — the
    caller already computes this for `_expand_datauri`'s target, e.g.
    `slot.get("width") * DESIGN_SCALE`) are optional but enable a second
    pass: for `image_type == "face"`, once the pair is picked,
    `content_aware_split_pair` is tried on the chosen photos — cover-fit's
    forced scale on this template's narrow split-slot aspect crops a normal
    landscape stock photo's face uncomfortably tight (85-93% of the slot's
    width — the ordinary zoom_l/zoom_r correction can only zoom IN further,
    never loosen that), so both halves are re-composited to a wider, less-
    zoomed framing with the vacated canvas filled in (not left blank) —
    see content_aware_split_pair's docstring for the "equal by construction"
    target and its conditional bail-out (a photo whose face is already
    large relative to its frame needs too little zoom, meaning too much of
    the canvas would need to be invented — that pair just keeps the
    ordinary cover-fit + zoom_l/zoom_r result instead). Omit slot_w/slot_h
    (or pass image_type="action") to skip this pass entirely and get the
    original cover-fit-only behavior.

    Returns `([left_uri, right_uri], [zoom_left, zoom_right])`, or `None` if
    either side has no usable candidate at all. The zoom pair is a
    corrective per-photo zoom (see vision_pick_split_pair) the renderer
    should apply on top of its normal cover-fit crop so both faces render
    at a similar apparent size — the caller (prepare_design_images) stashes
    it onto the returned template_json for design_renderer to pick up.
    When the content-aware pass above is used, both photos are already
    pre-fit to the exact slot size, so zoom is [1.0, 1.0] (the renderer's
    own cover-fit ends up a no-op)."""
    def _candidates(subject: str) -> list:
        # Normalize to (datauri, GalleryImage) regardless of source: fresh
        # Getty (fetch_subject_datauris) yields (GalleryImage, uri); gallery
        # reuse (find_gallery_datauris) yields (uri, GalleryImage). A wider
        # pool gives vision_pick_split_pair (and its framing-category +
        # quality-gate logic) real odds of finding a genuinely matched pair
        # instead of settling for whatever's available — pool_limit=30
        # validated 2026-08-21 against a 30-pair test batch as a solid
        # quality/cost middle ground (the default single-photo-pick pool of
        # 8 was found to quietly starve this to 1-2 verified survivors even
        # for well-photographed subjects; 50 gave the best results in
        # testing but is expensive to run on every job).
        #
        # Gallery reuse used to only fire when the fresh count was thin
        # (<2) — changed 2026-08-23 after a real user report: a fresh
        # "action" search can come back with several candidates that are
        # ALL poorly matched for pairing (e.g. every fresh result is a wide
        # vehicle-dominant shot, see the VEHICLE framing category), while
        # the gallery already holds a better-framed photo of the same
        # subject from an earlier save — one that never got a chance to
        # compete because the top-up only kicked in on COUNT, not quality.
        # User's own framing: reusing an already-used photo for ONE side of
        # a genuine 2-subject split is fine; that's different from (and not
        # to be confused with) forcing the exact same single photo onto
        # BOTH sides when there's only one subject at all — that idea was
        # considered and explicitly rejected for the single-subject case,
        # which still widens to full width instead (see prepare_design_images).
        fresh = fetch_subject_datauris(db, subject, image_type, niche, top_n=8)
        pairs = [(uri, gi) for gi, uri in fresh]
        pairs += find_gallery_datauris(
            db, subject, image_type=image_type, niche=niche, top_n=8, pool_limit=30,
        )
        return pairs

    left_pairs = _candidates(primary)
    if not left_pairs:
        return None
    right_pairs = _candidates(secondary)
    if not right_pairs:
        return None

    left_uris = [uri for uri, _ in left_pairs]
    right_uris = [uri for uri, _ in right_pairs]
    li, ri, zoom_l, zoom_r, accepted = vision_pick_split_pair(left_uris, right_uris, primary, secondary, image_type)
    if not accepted:
        # No candidate pair cleared the quality gate (see
        # vision_pick_split_pair's docstring) — a bad split is worse than no
        # split, so don't mark anything used and let the caller
        # (prepare_design_images) fall back to a single photo instead.
        logger.info(
            "build_split_srcs: %r | %r (%s) — no pair cleared the quality gate, skipping split",
            primary, secondary, image_type,
        )
        return None

    left_uri, left_gi = left_pairs[li]
    right_uri, right_gi = right_pairs[ri]
    if left_gi is not None:
        _mark_gallery_image_used(db, left_gi)
    if right_gi is not None:
        _mark_gallery_image_used(db, right_gi)
    logger.info(
        "build_split_srcs: %r | %r (%s) — chose pair L%d/R%d from %d/%d candidates (zoom_l=%.2f zoom_r=%.2f)",
        primary, secondary, image_type, li + 1, ri + 1, len(left_pairs), len(right_pairs), zoom_l, zoom_r,
    )

    if image_type == "face" and slot_w and slot_h:
        try:
            left_bytes = base64.b64decode(left_uri.split(",", 1)[1])
            right_bytes = base64.b64decode(right_uri.split(",", 1)[1])
            filled = content_aware_split_pair(left_bytes, right_bytes, slot_w, slot_h)
            if filled:
                return list(filled), [1.0, 1.0]
        except Exception as exc:
            logger.warning("build_split_srcs: content_aware_split_pair errored (%s) — using cover-fit", exc)

    return [left_uri, right_uri], [zoom_l, zoom_r]


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
        # A rectangular image_2 (split) slot left in place with only one
        # photo would render as a half-canvas photo next to an empty grey
        # panel — widen "image" to the full canvas and hide image_2, same
        # treatment the smart=True "only one subject" branch below already
        # applies. A circular inset is left alone (an unfilled small inset
        # doesn't read as visually broken the way a blank half-canvas panel
        # does), matching smart=True's own no-subject-found behavior for it.
        slot = find_role_object(template_json, "image_2")
        if slot is not None and slot.get("type") == "rect":
            tj = _widen_to_full(template_json, canvas_width)
            return tj, _with_inset(tj, [main_datauri])
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
            uri, _ = find_gallery_datauri(db, subject, exclude_path=main_path, image_type="face", niche=niche)
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
        split_slot_w = int(slot.get("width", 0) * slot.get("scaleX", 1)) * DESIGN_SCALE
        split_slot_h = int(slot.get("height", 0) * slot.get("scaleY", 1)) * DESIGN_SCALE
        built = build_split_srcs(db, primary, secondary, image_type, niche,
                                  slot_w=split_slot_w, slot_h=split_slot_h)
        if built:
            srcs, zooms = built
            logger.info("Design: split layout %r | %r (%s)", primary, secondary, image_type)
            out_srcs = _with_inset(template_json, srcs)
            # Stashed on the template JSON rather than widening this
            # function's return signature (every OTHER branch here returns
            # a plain (template_json, image_srcs) 2-tuple) — an extra root
            # key Fabric.js's loadFromJSON simply ignores. The renderer call
            # site (design_renderer.render_design) pops it back off before
            # building the /render request body.
            tj = dict(template_json)
            tj["_splitImageZooms"] = zooms
            return tj, out_srcs
        logger.info("Design: split sourcing failed for %r | %r — falling back", primary, secondary)
        # extract_two_subjects already made the careful "this IS a genuine
        # duel" call (including its own 2-call self-consistency check) —
        # only the ORIGINAL secondary's photo wasn't sourceable. Retry with
        # a fresh secondary name rather than abandoning the duel outright.
        subject = extract_secondary_subject(title, niche)
    else:
        # extract_two_subjects already decided this is NOT a genuine duel.
        # Real incident, 2026-08-27: falling through to
        # extract_secondary_subject here re-litigated that call with a much
        # looser prompt (no concept of "should a split even happen" — just
        # "name a second subject") and reliably found the exact same
        # passing-mention name (a farewell announcement's incoming
        # teammate) that extract_two_subjects had correctly rejected,
        # silently undoing the fix. A careful "no" from the stricter check
        # is never re-litigated by the looser one — single-subject stays
        # single-subject.
        subject = None

    if not subject:
        # Only one subject in the whole piece — no split to show, so the main
        # photo fills the full canvas instead of sitting half-width next to
        # an empty grey panel.
        logger.info("Design: no secondary subject for %r — image widened to full width", title[:50])
        tj = _widen_to_full(template_json, canvas_width)
        return tj, _with_inset(tj, [main_datauri])
    uri, gi = find_gallery_datauri(db, subject, exclude_path=main_path, niche=niche)
    if not uri:
        logger.info("Design: no gallery image for secondary subject %r — image widened to full width", subject)
        tj = _widen_to_full(template_json, canvas_width)
        return tj, _with_inset(tj, [main_datauri])
    logger.info("Design: image_2 subject=%r → gallery image %d", subject, gi.id)
    return template_json, _with_inset(template_json, [main_datauri, uri])
