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
    largest detected face, else slightly above centre. OpenCV Haar cascade runs
    locally in ~ms (no API)."""
    default = [0.5, 0.42]
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
                return [round(float(fx + fw / 2) / w, 4), round(float(fy + fh / 2) / h, 4)]
    except Exception as exc:
        logger.warning("detect_focus_point failed: %s", exc)
    return default


def focus_points_for(image_srcs: list) -> list:
    """Compute a focus point per data-URI image (for the renderer)."""
    out = []
    for uri in image_srcs:
        try:
            b = base64.b64decode(uri.split(",", 1)[1]) if "," in uri else b""
            out.append(detect_focus_point(b) if b else [0.5, 0.42])
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
                          main_datauri: str, main_path: str | None = None, smart: bool = True):
    """Source every image the template needs. Returns (template_json, image_srcs)
    — the template may be modified (inset slot repositioned).

    `smart=False` (the default OFF toggle) → main photo only, no AI
    secondary/split sourcing. `smart=True`:
    - no image_2 slot → main photo only
    - circular image_2 (inset) → portrait of the secondary subject, and the
      inset is moved to the side opposite the main subject's face
    - rectangular image_2 (split) → two fresh, style-consistent photos of the
      two subjects (both sides FACE or both ACTION)
    Every lookup failure degrades gracefully to fewer images.
    """
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
