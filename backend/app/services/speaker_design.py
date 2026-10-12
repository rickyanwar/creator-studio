"""Speaker-led image generation for the news and Facebook recreate designs."""

from __future__ import annotations

import io
import logging
import os
import threading
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PIL import Image
from sqlalchemy import String, cast, func, or_

from app.models.gallery import GalleryImage
from app.services import design_images, visual_engine
from app.services.visual_engine import GenerationRequest, VisualEngineError
from app.services.visual_sourcing import get_name_variants

logger = logging.getLogger(__name__)

IDENTITY_THRESHOLD = 0.40
_SFACE_URL = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
_sface_lock = threading.Lock()
_sface_state: dict[str, Any] = {"model": None, "failed": False}

_face_cache: OrderedDict[int, list[dict]] = OrderedDict()
_FACE_CACHE_MAX = 32


class SpeakerDesignResult(bytes):
    used_gallery_images: list[GalleryImage]

    def __new__(cls, image_bytes: bytes, used_gallery_images: list[GalleryImage] | None = None):
        obj = super().__new__(cls, image_bytes)
        obj.used_gallery_images = list(used_gallery_images or [])
        return obj

    @property
    def image_bytes(self) -> bytes:
        return bytes(self)


def _decode(image_bytes: bytes):
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("invalid image")
    return image


def _detect_faces_raw(image_bytes: bytes) -> list[dict]:
    """Detect all faces and return original-pixel boxes and landmarks."""
    try:
        import cv2

        image = _decode(image_bytes)
        height, width = image.shape[:2]
        detector = design_images._get_yunet_detector()
        if detector is None:
            return []
        scale = min(1.0, 640 / max(width, height))
        det_w, det_h = max(1, round(width * scale)), max(1, round(height * scale))
        detected = image if scale == 1 else cv2.resize(image, (det_w, det_h))
        detector.setInputSize((det_w, det_h))
        _, faces = detector.detect(detected)
        result = []
        for face in faces if faces is not None else []:
            values = [float(value) / scale for value in face[:14]]
            x, y, w, h = values[:4]
            landmarks = [(values[i], values[i + 1]) for i in range(4, 14, 2)]
            eye_mid_x = (landmarks[0][0] + landmarks[1][0]) / 2
            yaw = (landmarks[2][0] - eye_mid_x) / max(w, 1.0)
            raw = [float(face[i]) / scale for i in range(14)] + [float(face[14])]
            result.append({"box": (x, y, w, h), "landmarks": landmarks, "yaw": yaw, "raw": raw})
        return result
    except Exception as exc:
        logger.debug("speaker face detection failed: %s", exc)
        return []


def _detect_faces(image_bytes: bytes) -> list[dict]:
    # ponytail: cache by hash of bytes to avoid repeated cv2 decoding & face detection
    h = hash(image_bytes)
    if h in _face_cache:
        _face_cache.move_to_end(h)
        return _face_cache[h]
    res = _detect_faces_raw(image_bytes)
    _face_cache[h] = res
    if len(_face_cache) > _FACE_CACHE_MAX:
        _face_cache.popitem(last=False)
    return res


def face_info(image_bytes: bytes) -> dict | None:
    faces = _detect_faces(image_bytes)
    return max(faces, key=lambda face: face["box"][2] * face["box"][3]) if faces else None


def speaker_side(image_bytes: bytes) -> str:
    info = face_info(image_bytes)
    yaw = info.get("yaw", 0) if info else 0
    return "right" if yaw < -0.03 else "left"


def _matches(row: GalleryImage, names: list[str]) -> bool:
    haystack = " ".join([row.keyword or "", *(row.extra_keywords or [])]).lower()
    attrs = row.attributes or {}
    people = attrs.get("people", []) if isinstance(attrs, dict) else []
    attr_names = [p.get("name", "") for p in people if isinstance(p, dict)]
    return any(variant.lower() in haystack or any(variant.lower() in n.lower() for n in attr_names) for variant in names)


def _rows(db, names: list[str]) -> list[GalleryImage]:
    conditions = []
    for variant in names:
        pattern = f"%{variant}%"
        conditions.extend([
            GalleryImage.keyword.ilike(pattern),
            GalleryImage.extra_keywords.any(variant),
            cast(GalleryImage.attributes, String).ilike(pattern),
        ])
    return (db.query(GalleryImage)
            .filter(GalleryImage.is_deleted.is_(False), or_(*conditions))
            .order_by(func.coalesce(GalleryImage.captured_at, GalleryImage.downloaded_at).desc())
            .limit(300).all())


def _read_photo(row: GalleryImage) -> bytes:
    return Path(row.local_path).read_bytes()


HEAD_AND_SHOULDERS_FRAMING = "Tight head-and-shoulders, top of the head about 8-12 percent from the top edge, face about 40-45 percent of the card width, eyes in the upper third; shoulders and chest continue down to the bottom edge, filling the lower area behind where the text will go; no empty floor or background strip at the bottom"
HALF_BODY_FRAMING = "HALF-BODY portrait from the top of the head down to the waist, the top of the head about 4-6 percent from the top edge, head and shoulders high in the frame, face about 34-38 percent of the card width; the body continues down to the bottom edge, filling the lower area behind where the text will go; no empty floor or background strip at the bottom"

_CRASH_WORDS = {"crash", "crashed", "crashes", "fall", "fell", "fallen", "clash", "collision", "contact", "incident", "highside", "lowside", "crashing"}
_WIN_WORDS = {"win", "won", "victory", "winner", "podium", "trophy", "celebrate", "celebrates", "celebration", "champion"}
_BATTLE_WORDS = {"battle", "overtake", "overtook", "fight", "duel", "pass", "passed", "attack", "attacked"}

_SERIOUS_WORDS = {"injury", "injured", "pain", "disappointed", "disappointing", "penalty", "retired", "retire", "problem", "problems", "difficult", "suffered", "mistake", "lost", "lose", "brake", "crash", "fall", "fell", "worst", "sorry", "frustrated", "frustrating", "angry", "struggle", "struggled"}
_HAPPY_WORDS = {"happy", "great", "fantastic", "amazing", "proud", "delighted", "best"}

_SERIOUS_GOOD_EXPRESSIONS = {"serious", "focused", "neutral", "disappointed", "emotional", "angry"}
_SERIOUS_BAD_EXPRESSIONS = {"smiling", "happy", "celebrating", "shouting"}

_HAPPY_GOOD_EXPRESSIONS = {"smiling", "happy", "celebrating"}
_HAPPY_BAD_EXPRESSIONS = {"angry", "disappointed"}

_ADDENDUM_SENTENCE = "This expression change is the only allowed exception to the rule 'do not change any face'."
_EXPRESSION_EDIT_SERIOUS = f"Change ONLY the speaker's facial expression to a calm, serious, focused look (mouth closed, no smile, no teeth showing). Keep exactly his identity: face shape, eyes, nose, beard, skin texture, hair, cap and clothing. Nothing else changes. {_ADDENDUM_SENTENCE}"
_EXPRESSION_EDIT_HAPPY = f"Change ONLY the speaker's facial expression to a natural, subtle smile. Keep exactly his identity: face shape, eyes, nose, beard, skin texture, hair, cap and clothing. Nothing else changes. {_ADDENDUM_SENTENCE}"


def story_mood(article_text: str) -> str:
    s_event = story_event(article_text)
    words = set(re.findall(r"[a-z0-9]+", (article_text or "").lower()))

    is_serious = (s_event == "crash") or any(w in words for w in _SERIOUS_WORDS)
    is_happy = (s_event == "win") or any(w in words for w in _HAPPY_WORDS)

    if is_serious:
        return "serious"
    if is_happy:
        return "happy"
    return "any"


_CELEBRATION_WORDS = {"podium", "celebrate", "celebrates", "celebrating", "celebration", "trophy", "champagne", "winner", "wins", "victory", "parc", "ferme"}


def _has_celebration_context(row: GalleryImage) -> bool:
    attrs = getattr(row, "attributes", None) or {}
    caption = str(attrs.get("caption", "") if isinstance(attrs, dict) else "")
    getty_slug = str(attrs.get("getty_slug", "") if isinstance(attrs, dict) else "")
    url = getattr(row, "source_image_url", "") or ""
    url_slug_match = re.search(r"([^/]+?)(?:\.jpe?g|\.png)(?:\?.*)?$", url, re.I)
    url_slug = url_slug_match.group(1).replace("-", " ").replace("_", " ") if url_slug_match else ""

    text = f"{caption} {getty_slug} {url_slug}".lower()
    words = set(re.findall(r"[a-z0-9]+", text))
    if any(w in words for w in {"podium", "celebrate", "celebrates", "celebrating", "celebration", "trophy", "champagne", "winner", "wins", "victory"}):
        return True
    if "parc ferme" in text or "parc-ferme" in text or "parc_ferme" in text:
        return True
    return False


def _expression_rank(row: GalleryImage, mood: str) -> int:
    attrs = getattr(row, "attributes", None) or {}
    expr = attrs.get("expression") if isinstance(attrs, dict) else None
    if mood == "serious":
        if _has_celebration_context(row):
            return 0
        if expr in _SERIOUS_GOOD_EXPRESSIONS:
            return 2
        if expr in _SERIOUS_BAD_EXPRESSIONS:
            return 0
        return 1
    if mood == "happy":
        if expr in _HAPPY_GOOD_EXPRESSIONS:
            return 2
        if expr in _HAPPY_BAD_EXPRESSIONS:
            return 0
        return 1
    return 1


def is_bad_expression(row: GalleryImage, mood: str) -> bool:
    attrs = getattr(row, "attributes", None) or {}
    expr = attrs.get("expression") if isinstance(attrs, dict) else None
    if mood == "serious":
        if _has_celebration_context(row):
            return True
        if expr in _SERIOUS_BAD_EXPRESSIONS:
            return True
    if mood == "happy" and expr in _HAPPY_BAD_EXPRESSIONS:
        return True
    return False


def story_event(text: str) -> str:
    words = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    if any(w in words for w in _CRASH_WORDS):
        return "crash"
    if any(w in words for w in _WIN_WORDS):
        return "win"
    if any(w in words for w in _BATTLE_WORDS):
        return "battle"
    return "general"


def photo_event(row: GalleryImage) -> str:
    attrs = row.attributes or {} if hasattr(row, "attributes") else {}
    shot_type = attrs.get("shot_type") if isinstance(attrs, dict) else None
    if shot_type == "podium":
        return "win"
    caption = str(attrs.get("caption", "") if isinstance(attrs, dict) else "")
    getty_slug = str(attrs.get("getty_slug", "") if isinstance(attrs, dict) else "")
    url = getattr(row, "source_image_url", "") or ""
    url_slug_match = re.search(r"([^/]+?)(?:\.jpe?g|\.png)(?:\?.*)?$", url, re.I)
    url_slug = url_slug_match.group(1).replace("-", " ").replace("_", " ") if url_slug_match else ""

    text = f"{caption} {getty_slug} {url_slug}".lower()
    words = set(re.findall(r"[a-z0-9]+", text))
    if any(w in words for w in _CRASH_WORDS):
        return "crash"
    if any(w in words for w in _WIN_WORDS):
        return "win"
    if any(w in words for w in _BATTLE_WORDS):
        return "battle"
    return "general"


def _is_half_body_source(row: GalleryImage) -> bool:
    attrs = row.attributes or {} if hasattr(row, "attributes") else {}
    shot = attrs.get("shot_type") if isinstance(attrs, dict) else None
    if shot == "half_body":
        return True
    try:
        data = _read_photo(row)
        info = face_info(data)
        if not info or "box" not in info:
            return False
        with Image.open(io.BytesIO(data)) as img:
            img_w, img_h = img.size
        x, y, w, h = info["box"]
        face_center_y = y + h / 2
        return (h / img_h <= 0.22) and (face_center_y / img_h <= 0.45)
    except Exception:
        return False


def speaker_framing(image_bytes: bytes, row: GalleryImage | None = None) -> str:
    # ponytail: row shot_type shortcut skips face detection when metadata already marks half_body
    if row and getattr(row, "attributes", None):
        attrs = row.attributes
        if isinstance(attrs, dict) and attrs.get("shot_type") == "half_body":
            return HALF_BODY_FRAMING
    try:
        info = face_info(image_bytes)
        if not info or "box" not in info:
            return HEAD_AND_SHOULDERS_FRAMING
        with Image.open(io.BytesIO(image_bytes)) as img:
            img_w, img_h = img.size
        x, y, w, h = info["box"]
        face_center_y = y + h / 2
        if (h / img_h <= 0.22) and (face_center_y / img_h <= 0.45):
            return HALF_BODY_FRAMING
    except Exception:
        pass
    return HEAD_AND_SHOULDERS_FRAMING


def _usable_face(row: GalleryImage, names: list[str]) -> bool:
    attrs = row.attributes or {}
    people = attrs.get("people", []) if isinstance(attrs, dict) else []
    count = attrs.get("people_count") if isinstance(attrs, dict) else None
    shot_type = attrs.get("shot_type") if isinstance(attrs, dict) else None
    caption = str(attrs.get("caption", "") if isinstance(attrs, dict) else "").lower()
    if "sunglasses" in caption or "helmet" in caption or row.label not in ("face", None) and shot_type not in ("face_closeup", "portrait", "half_body"):
        return False
    if count not in (None, 1) or attrs.get("archival_photo") is True:
        return False
    quality = attrs.get("quality") if isinstance(attrs.get("quality"), dict) else {}
    if quality.get("blurry") or quality.get("obstructed"):
        return False
    info = face_info(_read_photo(row))
    return bool(info and info["box"][2] >= 120)


def pick_speaker_photos(db, name: str, mood: str = "any") -> list[GalleryImage]:
    names = get_name_variants(name)
    candidates = []
    for row in _rows(db, names)[:15]:
        attrs = row.attributes or {}
        shot = attrs.get("shot_type") if isinstance(attrs, dict) else None
        if (row.label == "face" or shot in ("face_closeup", "portrait", "half_body")) and _usable_face(row, names):
            candidates.append(row)
    # Sort: (expression rank, half-body first, newest)
    def _sort_key(r):
        expr_rank = _expression_rank(r, mood)
        half_body = 1 if _is_half_body_source(r) else 0
        when = getattr(r, "downloaded_at", None) or getattr(r, "captured_at", None) or datetime.min.replace(tzinfo=timezone.utc)
        return (expr_rank, half_body, when)

    return sorted(candidates, key=_sort_key, reverse=True)[:2]


def pick_moment_photos(db, names: list[str], around: datetime, *, article_text: str = "", speaker: str | None = None) -> list[GalleryImage]:
    speaker_variants = set(get_name_variants(speaker)) if speaker else set()
    all_names = list(dict.fromkeys([*names, *speaker_variants]))
    cutoff = around.replace(tzinfo=None) - timedelta(hours=72)
    s_event = story_event(article_text)
    words = set(re.findall(r"[a-z0-9]+", (article_text or "").lower()))

    candidates = []
    for row in _rows(db, all_names)[:15]:
        when = row.captured_at or row.downloaded_at
        if when and getattr(when, "replace", lambda **_: when)(tzinfo=None) < cutoff:
            continue
        attrs = row.attributes or {}
        shot = attrs.get("shot_type") if isinstance(attrs, dict) else None
        if getattr(row, "label", None) != "action" and shot not in ("action", "athlete_in_action", "podium", "car_on_track", "pitlane"):
            continue
        p_event = photo_event(row)
        # CONTRADICTION: story is crash/battle and photo is win
        if s_event in ("crash", "battle") and p_event == "win":
            continue
        caption = str(attrs.get("caption", "") if isinstance(attrs, dict) else "") + " " + str(attrs.get("getty_slug", "") if isinstance(attrs, dict) else "")
        overlap = len(words.intersection(set(re.findall(r"[a-z0-9]+", caption.lower()))))
        when_val = getattr(row, "downloaded_at", None) or getattr(row, "captured_at", None) or datetime.min.replace(tzinfo=timezone.utc)
        candidates.append((p_event, overlap, when_val, row))

    if s_event == "crash":
        crash_matches = [item for item in candidates if item[0] == "crash"]
        if not crash_matches:
            # Fallback 1: newest action photo of rival
            # The rival is the first name in names that is not a variant of the speaker
            rival = next((n for n in names if n not in speaker_variants and not any(v.lower() == n.lower() for v in speaker_variants)), None)
            if rival:
                rival_variants = get_name_variants(rival)
                rival_candidates = [item for item in candidates if _matches(item[3], rival_variants)]
                if rival_candidates:
                    best_rival = max(rival_candidates, key=lambda item: item[2])
                    return [best_rival[3]]
            # Fallback 2: newest non-win action photo of speaker
            speaker_candidates = [item for item in candidates if _matches(item[3], list(speaker_variants))]
            if speaker_candidates:
                best_speaker = max(speaker_candidates, key=lambda item: item[2])
                return [best_speaker[3]]
            return []

    # Rank: photos whose event equals story_event come first, then word overlap, then newest
    ranked = sorted(candidates, key=lambda item: (item[0] == s_event, item[1], item[2]), reverse=True)
    if not ranked:
        return []

    if len(ranked) >= 2:
        top1, top2 = ranked[0], ranked[1]
        same_url = getattr(top1[3], "source_image_url", id(top1[3])) == getattr(top2[3], "source_image_url", id(top2[3]))
        both_match_event = (top1[0] == s_event and top2[0] == s_event and s_event != "general")
        both_overlap = (top1[1] > 0 and top2[1] > 0)
        if (both_match_event or both_overlap) and not same_url:
            return [top1[3], top2[3]]

    return [ranked[0][3]]


def pick_bike_photos(db, rider: str) -> list[GalleryImage]:
    rows = []
    for row in _rows(db, get_name_variants(rider))[:15]:
        attrs = row.attributes or {}
        shot = attrs.get("shot_type") if isinstance(attrs, dict) else None
        if getattr(row, "label", None) == "action" or shot in ("car_on_track", "athlete_in_action"):
            rows.append(row)
    # Put photo with visible race number first
    return sorted(rows, key=lambda r: _race_number(r) != "unknown", reverse=True)[:2]


def plan_layout(moment_photos: list, bike_photos: list) -> str | None:
    return "inset" if moment_photos else "side" if bike_photos else None


ASIS = "Real photos used inside insets or behind the speaker must be used AS IS: only crop, scale, place and softly blur them. Never redraw, re-render, sharpen, enhance or add detail to any motorcycle, rider or helmet; keep their original photographic softness, grain and motion blur, exactly like a real press photo."
COMMON = ASIS + '''
Keep every real sponsor logo, patch, number and printed text on the clothing, caps, suits, helmets and motorcycles exactly as in the photos; never remove, blur or simplify them; do not add any new text or logo. If an official MotoGP logo appears in a background, leave it out.
Lighting and colour: natural neutral light with true-to-life colours like a real Getty press photo composited by a professional designer. NO golden hour, NO yellow or orange tint, NO haze, NO glow, NO lens flare, NO dramatic grading. The light on the speaker's face matches Image 1.
Keep every face, the inset circle and any important detail ABOVE the lower 37 percent band of the card; the bottom 37 percent may hold only the speaker's body/torso and background, because the headline text is placed there.
The bottom 30 percent fades to deep navy almost black and stays empty for text.
Do not invent or change any person, face, clothing, bike or pose; no extra people beyond those in the photos; natural skin texture with visible pores and subtle film grain; no HDR look, no over-sharpening, no beauty filter. NO text at all.'''
def _photo_description(row, fallback: str) -> str:
    attrs = getattr(row, "attributes", None) or {}
    caption = attrs.get("caption") if isinstance(attrs, dict) else None
    if caption:
        return str(caption)
    slug = re.search(r"([^/]+?)(?:\.jpe?g|\.png)(?:\?.*)?$", getattr(row, "source_image_url", "") or "", re.I)
    if slug:
        return slug.group(1).replace("-", " ").replace("_", " ")
    return fallback


def _race_number(row) -> str:
    text = _photo_description(row, "")
    match = re.search(r"#\s*(\d{1,3})\b|\bnumber\s+(\d{1,3})\b", text, re.I)
    return (match.group(1) or match.group(2)) if match else "unknown"


def _build_prompt(layout: str, speaker: str, side: str, sport: str, moment_photos: list, bike_photos: list, framing: str = HEAD_AND_SHOULDERS_FRAMING, speaker_photo: GalleryImage | None = None, mood: str = "any") -> str:
    other = "left" if side == "right" else "right"
    edit_line = None
    if speaker_photo and is_bad_expression(speaker_photo, mood):
        if mood == "serious":
            edit_line = _EXPRESSION_EDIT_SERIOUS
        elif mood == "happy":
            edit_line = _EXPRESSION_EDIT_HAPPY

    if layout == "inset":
        speaker_line = f"Image 1 = the SPEAKER (hero), {speaker}. Professional cut-out with clean hair edges. {framing}, placed on the {side} side facing into the frame. Keep his exact face, expression, skin texture, hair, cap, clothing and every logo exactly as in Image 1."
        if edit_line:
            speaker_line += f" {edit_line}"
        lines = [f"Design a premium vertical 4:5 {sport} social media graphic, the quality a senior graphic designer produces in Photoshop. Use only the REAL people and objects from the reference photos.", speaker_line]
        for index, row in enumerate(moment_photos[:2]):
            lines.append(f"Image {index + 2} = INSET {index + 1}: REAL PHOTO of {_photo_description(row, speaker)}. Crop it tight on its subject; keep it exactly as photographed.")
        if len(moment_photos) == 1:
            lines.append(f"Place the inset inside ONE large circle (about 45 percent of the card width) with a clean solid white ring (thin but strong) and a soft drop shadow, upper-{other}. The speaker OVERLAPS the edge of the circle (speaker in front of the circle) with a soft contact shadow on the circle, real layered depth like Photoshop layers.")
        else:
            lines.append(f"Place the insets inside TWO circles of different sizes on the {other} side (a large circle upper-{other} and a smaller one overlapping it lower/mid-{other}), both with the same clean white ring (thin but strong) and a soft drop shadow. The speaker OVERLAPS at least one circle (speaker in front) with a soft contact shadow, real layered depth. Everything inside the top 65 percent.")
        lines.append("Background: the real background from Image 1, extended to fill the card, softly out of focus, slightly darkened and desaturated.")
    else:
        bike = bike_photos[0]
        speaker_line = f"Image 1 = the SPEAKER (hero), {speaker}. Professional cut-out. {framing}, on the {side} side facing into the frame, in FRONT of everything. Keep his exact face, expression, skin texture, hair, cap, clothing and every logo exactly as in Image 1."
        if edit_line:
            speaker_line += f" {edit_line}"
        lines = [f"Design a premium vertical 4:5 {sport} social media graphic, the quality a senior graphic designer produces in Photoshop. Use only the REAL people and objects from the reference photos.", speaker_line, f"Image 2 = the real photo of {_photo_description(bike, 'the rider')} riding (race number {_race_number(bike)}). Use this photo AS IS as the back layer on the {other} side behind the speaker: crop and scale it so the rider and motorcycle are large in the upper {other} area, the rest of that photo's own track scene softly blurred to fill the background. The speaker's shoulder partly covers the near edge of the bike; the bike never covers the speaker.", "Image 3 = a second real photo of the speaker, only to confirm his identity."]
    lines.append(COMMON)
    return "\n".join(lines)


def _prompt(layout: str, speaker: str, subjects: list[str], side: str, sport: str, moment_photos=None, bike_photos=None, framing: str = HEAD_AND_SHOULDERS_FRAMING, speaker_photo: GalleryImage | None = None, mood: str = "any") -> str:
    # Kept as a small compatibility wrapper for callers that only need a prompt.
    return _build_prompt(layout, speaker, side, sport, moment_photos or [], bike_photos or [], framing=framing, speaker_photo=speaker_photo, mood=mood)


def _finish(image_bytes: bytes) -> bytes:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = image.size
    crop_width = min(width, int(height * 0.8))
    crop_height = int(crop_width / 0.8)
    crop_height = min(height, crop_height)
    crop_width = int(crop_height * 0.8)
    left = (width - crop_width) // 2
    image = image.crop((left, 0, left + crop_width, crop_height)).resize((1080, 1350), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def compose_speaker_design(
    db, fanpage, *, speaker: str, subjects: list[str], article_text: str,
    sport: str, around: datetime, story_photo_path: str | None = None,
    story_photo_desc: str | None = None,
) -> bytes | None:
    try:
        mood = story_mood(article_text)
        speakers = pick_speaker_photos(db, speaker, mood=mood)
        moments = pick_moment_photos(db, subjects, around, article_text=article_text, speaker=speaker)
        bikes = pick_bike_photos(db, subjects[0] if subjects else speaker)

        if story_photo_path:
            story_photo_row = SimpleNamespace(
                local_path=story_photo_path,
                attributes={"caption": story_photo_desc},
            )
            s_event = story_event(article_text)
            chosen_moments = [story_photo_row]
            for m in moments:
                if getattr(m, "local_path", None) == story_photo_path:
                    continue
                m_event = photo_event(m)
                # Matches story event and no contradiction
                if s_event in ("crash", "battle") and m_event == "win":
                    continue
                if m_event == s_event or (s_event == "general" and m_event != "win"):
                    chosen_moments.append(m)
                    break
            moments = chosen_moments
            layout = "inset"
        else:
            layout = plan_layout(moments, bikes)

        if not speakers or not layout:
            logger.info("speaker design skipped: missing speaker or layout")
            return None
        refs = [speakers[0]] + (moments[:2] if layout == "inset" else bikes[:1] + speakers[1:2])
        paths = [row.local_path for row in refs]
        speaker_bytes = _read_photo(speakers[0])
        framing = speaker_framing(speaker_bytes, row=speakers[0])
        request = GenerationRequest(layout="inset" if layout == "inset" else "action", refs=tuple(paths), prompt=_build_prompt(layout, speaker, speaker_side(speaker_bytes), sport, moments, bikes, framing=framing, speaker_photo=speakers[0], mood=mood), size="1024x1536", quality="high")
        generated = visual_engine.generate(db, request, fanpage_id=getattr(fanpage, "id", None))
        if identity_score(speaker_bytes, generated) < IDENTITY_THRESHOLD:
            logger.warning("speaker design rejected: identity score below threshold")
            return None
        finished_bytes = _finish(generated)
        used_gallery = [r for r in refs if isinstance(r, GalleryImage)]
        return SpeakerDesignResult(image_bytes=finished_bytes, used_gallery_images=used_gallery)
    except Exception as exc:
        logger.warning("speaker design failed: %s", exc)
        return None


def _get_sface_model():
    if _sface_state["model"] is not None or _sface_state["failed"]:
        return _sface_state["model"]
    with _sface_lock:
        try:
            import cv2
            import urllib.request
            from app.config import get_settings
            path = os.path.join(get_settings().storage_base_path, "sr_models", "face_recognition_sface_2021dec.onnx")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path) or os.path.getsize(path) < 100_000:
                urllib.request.urlretrieve(_SFACE_URL, path)
            _sface_state["model"] = cv2.FaceRecognizerSF_create(path, "")
        except Exception as exc:
            logger.warning("SFace unavailable: %s", exc)
            _sface_state["failed"] = True
    return _sface_state["model"]


def identity_score(ref_bytes: bytes, out_bytes: bytes) -> float:
    model = _get_sface_model()
    ref = face_info(ref_bytes)
    if model is None or not ref:
        return 0.0
    try:
        import cv2
        ref_image, out_image = _decode(ref_bytes), _decode(out_bytes)
        ref_feature = _face_feature(model, ref_image, ref)
        return max((_face_feature_score(model, ref_feature, out_image, face) for face in _detect_faces(out_bytes)), default=0.0)
    except Exception as exc:
        logger.warning("identity score calculation failed: %s", exc)
        return 0.0


def _face_feature(model, image, face: dict):
    import numpy as np
    raw = face.get("raw")
    if raw is not None:
        face_box = np.asarray(raw, dtype=np.float32)
    else:
        face_box = np.asarray([*face["box"], *sum(face["landmarks"], ()), 1.0], dtype=np.float32)
    return model.feature(model.alignCrop(image, face_box))


def _face_feature_score(model, reference_feature, image, face: dict) -> float:
    import cv2
    return float(model.match(reference_feature, _face_feature(model, image, face), cv2.FaceRecognizerSF_FR_COSINE))
