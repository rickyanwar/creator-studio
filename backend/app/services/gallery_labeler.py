import base64
import io
import json
import logging
import re
from PIL import Image
import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)

SHOT_TYPES = {
    "face_closeup", "portrait", "half_body", "full_body",
    "car_on_track", "car_static", "pitlane", "podium", "crowd",
    "athlete_in_action", "team_group", "vehicle_static", "other",
}

ACTIVITIES = {
    "driving", "celebrating", "interview", "walking", "talking", "posing",
    "working_on_car", "waiting", "spraying_champagne", "other",
    "playing", "shooting", "dunking", "fighting", "striking", "grappling",
    "training", "warming_up", "weigh_in", "face_off", "press_conference",
    "walkout", "signing_autographs",
}

EXPRESSIONS = {
    "happy", "smiling", "serious", "focused", "celebrating", "disappointed",
    "neutral", "not_visible",
    "angry", "shouting", "emotional",
}

SPORTS = {"motorsport", "basketball", "mma", "boxing", "football", "tennis", "other", "unknown"}
VENUE_TYPES = {
    "circuit", "court", "octagon", "ring", "pitch", "stadium", "arena", "gym",
    "studio", "paddock", "garage", "pitlane", "street", "other", "unknown",
}

CONTEXT_SESSIONS = {
    "race", "qualifying", "practice", "pitlane", "podium", "paddock", "garage",
    "event", "unknown",
    "game", "fight", "weigh_in", "training", "warm_up", "press_conference",
    "media_day", "ceremony",
}

EVENT_SESSIONS = CONTEXT_SESSIONS

LABEL_PROMPT = """Analyze this image and return a JSON object with the following fields strictly:
{
  "sport": "motorsport" | "basketball" | "mma" | "boxing" | "football" | "tennis" | "other" | "unknown",
  "venue": {
    "type": "circuit" | "court" | "octagon" | "ring" | "pitch" | "stadium" | "arena" | "gym" | "studio" | "paddock" | "garage" | "pitlane" | "street" | "other" | "unknown",
    "visible": boolean
  },
  "shot_type": "face_closeup" | "portrait" | "half_body" | "full_body" | "car_on_track" | "car_static" | "pitlane" | "podium" | "crowd" | "athlete_in_action" | "team_group" | "vehicle_static" | "other",
  "people_count": integer from 0 to 20,
  "activity": "driving" | "celebrating" | "interview" | "walking" | "talking" | "posing" | "working_on_car" | "waiting" | "spraying_champagne" | "playing" | "shooting" | "dunking" | "fighting" | "striking" | "grappling" | "training" | "warming_up" | "weigh_in" | "face_off" | "press_conference" | "walkout" | "signing_autographs" | "other",
  "expression": "happy" | "smiling" | "serious" | "focused" | "celebrating" | "disappointed" | "neutral" | "not_visible" | "angry" | "shouting" | "emotional",
  "context": {
    "session": "race" | "qualifying" | "practice" | "pitlane" | "podium" | "paddock" | "garage" | "event" | "game" | "fight" | "weigh_in" | "training" | "warm_up" | "press_conference" | "media_day" | "ceremony" | "unknown",
    "rain": boolean,
    "night": boolean,
    "circuit_visible": boolean
  },
  "livery_colors": ["color1", "color2", "color3"],
  "kit_colors": ["color1", "color2", "color3"],
  "event": {"series": "up to 40 chars", "event_name": "up to 80 chars", "year": 1950-2100, "season": "YYYY or YYYY-YY", "session": "context session enum"},
  "quality": {
    "blurry": boolean,
    "obstructed": boolean
  },
  "caption": "Short caption up to 160 characters"
}
Use livery_colors for motorsport and kit_colors for other sports, each with at most 3 colors.
Use venue.type and venue.visible; keep context.circuit_visible true only when a circuit is visible.
When evidence supports it, include event with series, event_name, year, optional season (YYYY or YYYY-YY), and session.
Output only valid JSON with no extra commentary or Markdown formatting.
"""


class LabelerError(Exception):
    """Client error or 4xx from labeler provider."""
    pass


class LabelerUnavailable(Exception):
    """Temporary failure, quota, 429, 5xx, or network timeout."""
    pass


def _image_to_datauri(path: str, max_dim: int = 512, quality: int = 80) -> str:
    """Open image, convert to RGB JPEG with max 512px long side, and encode to data URI."""
    with Image.open(path) as im:
        img = im.convert("RGB")
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"


def _parse_json(raw: str) -> dict:
    """Parse JSON string, stripping code fences and tolerating extra wrappers."""
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        val = json.loads(cleaned)
    except (TypeError, json.JSONDecodeError):
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise ValueError("no JSON object found in response")
        try:
            val = json.loads(match.group(0))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc

    if not isinstance(val, dict):
        raise ValueError("output must be a JSON dictionary")
    return val


def normalise_label_output(data: dict) -> dict:
    """Validate and normalise label output dictionary according to spec."""
    if not isinstance(data, dict):
        raise ValueError("input must be a dictionary")

    sport = str(data.get("sport") or "").strip().lower()
    sport = sport if sport in SPORTS else "unknown"

    raw_venue = data.get("venue")
    if not isinstance(raw_venue, dict):
        raw_venue = {}
    venue_type = str(raw_venue.get("type") or "").strip().lower()
    venue_type = venue_type if venue_type in VENUE_TYPES else "unknown"
    venue_visible = bool(raw_venue.get("visible", False))

    # Legacy clients only know context.circuit_visible. Preserve it and derive
    # the new venue visibility flag for circuit labels.
    legacy_circuit_visible = bool((data.get("context") or {}).get("circuit_visible", False)) if isinstance(data.get("context"), dict) else False
    if venue_type == "circuit" and legacy_circuit_visible:
        venue_visible = True
    if venue_type == "circuit":
        legacy_circuit_visible = venue_visible

    # shot_type
    shot_type = str(data.get("shot_type") or "").strip().lower()
    shot_type = shot_type if shot_type in SHOT_TYPES else "other"

    # people_count
    try:
        people_count = int(data.get("people_count", 0))
    except (ValueError, TypeError):
        raise ValueError("invalid people_count: must be an integer")
    people_count = max(0, min(20, people_count))

    # activity
    activity = str(data.get("activity") or "").strip().lower()
    activity = activity if activity in ACTIVITIES else "other"

    # expression
    expression = str(data.get("expression") or "").strip().lower()
    expression = expression if expression in EXPRESSIONS else "not_visible"

    # context
    ctx = data.get("context")
    if not isinstance(ctx, dict):
        ctx = {}
    session = str(ctx.get("session") or "").strip().lower()
    session = session if session in CONTEXT_SESSIONS else "unknown"
    rain = bool(ctx.get("rain", False))
    night = bool(ctx.get("night", False))
    circuit_visible = bool(ctx.get("circuit_visible", False))

    context_dict = {
        "session": session,
        "rain": rain,
        "night": night,
        "circuit_visible": legacy_circuit_visible if venue_type == "circuit" else circuit_visible,
    }

    # livery_colors
    raw_colors = data.get("livery_colors") or []
    if not isinstance(raw_colors, list):
        raw_colors = []
    livery_colors = [str(c).strip().lower() for c in raw_colors if str(c).strip()][:3]
    raw_kit_colors = data.get("kit_colors") or []
    if not isinstance(raw_kit_colors, list):
        raw_kit_colors = []
    kit_colors = [str(c).strip().lower() for c in raw_kit_colors if str(c).strip()][:3]

    # quality
    q = data.get("quality")
    if not isinstance(q, dict):
        q = {}
    quality_dict = {
        "blurry": bool(q.get("blurry", False)),
        "obstructed": bool(q.get("obstructed", False)),
    }

    raw_event = data.get("event")
    event_dict = None
    if isinstance(raw_event, dict):
        event_dict = {
            "series": str(raw_event.get("series") or "").strip()[:40],
            "event_name": str(raw_event.get("event_name") or "").strip()[:80],
            "year": raw_event.get("year"),
            "session": str(raw_event.get("session") or "").strip().lower(),
        }
        if event_dict["year"] is not None:
            try:
                event_dict["year"] = int(event_dict["year"])
            except (TypeError, ValueError):
                raise ValueError("event.year must be an integer")
            if not 1950 <= event_dict["year"] <= 2100:
                raise ValueError("event.year must be between 1950 and 2100")
        if event_dict["session"] not in EVENT_SESSIONS:
            event_dict["session"] = "unknown"
        season = raw_event.get("season")
        if season is not None and re.fullmatch(r"[0-9]{4}(-[0-9]{2})?", str(season)):
            event_dict["season"] = str(season)
        else:
            event_dict.pop("season", None)

    # caption
    caption = str(data.get("caption") or "").strip()[:160]

    result = {
        "sport": sport,
        "venue": {"type": venue_type, "visible": venue_visible},
        "shot_type": shot_type,
        "people_count": people_count,
        "activity": activity,
        "expression": expression,
        "context": context_dict,
        "livery_colors": livery_colors,
        "kit_colors": kit_colors,
        "quality": quality_dict,
        "caption": caption,
    }
    if event_dict is not None:
        result["event"] = event_dict
    return result


def label_image(path: str, provider: str = "router") -> dict:
    """Label a local image file using provider ('router' or 'external')."""
    datauri = _image_to_datauri(path, max_dim=512)

    if provider == "router":
        from app.services.design_images import _vision_chat

        content = [
            {"type": "text", "text": LABEL_PROMPT},
            {"type": "image_url", "image_url": {"url": datauri}},
        ]
        try:
            raw = _vision_chat(content, max_tokens=1000, temperature=0, context="gallery_label")
        except Exception as exc:
            msg = str(exc).lower()
            if any(term in msg for term in ("429", "rate limit", "quota", "timeout", "unavailable", "503", "502", "500")):
                raise LabelerUnavailable(f"9Router unavailable: {exc}") from exc
            raise LabelerError(f"9Router error: {exc}") from exc

    elif provider == "external":
        cfg = get_settings()
        base_url = (cfg.labeler_base_url or "").rstrip("/")
        api_key = cfg.labeler_api_key or ""
        model = cfg.labeler_model or "default"
        if not base_url:
            raise LabelerError("LABELER_BASE_URL not configured")

        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": LABEL_PROMPT},
                        {"type": "image_url", "image_url": {"url": datauri}},
                    ],
                }
            ],
            "max_tokens": 1000,
            "temperature": 0,
        }
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=60.0)
            resp.raise_for_status()
            res_data = resp.json()
            raw = res_data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (429, 500, 502, 503, 504):
                raise LabelerUnavailable(f"External labeler status {code}: {exc}") from exc
            raise LabelerError(f"External labeler status {code}: {exc}") from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise LabelerUnavailable(f"External labeler network error: {exc}") from exc
        except Exception as exc:
            raise LabelerError(f"External labeler error: {exc}") from exc
    else:
        raise LabelerError(f"Unknown labeler provider: {provider}")

    parsed = _parse_json(raw)
    return normalise_label_output(parsed)
