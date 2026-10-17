from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from typing import Any
import unicodedata
import urllib.parse
import urllib.request

from phone_runner.config import (
    get_adb_path,
    get_mobilerun_gateway_url,
    is_serial_valid_format,
)

# One hung model must not eat the whole gate (a real run lost its 40 s budget to one stuck call).
VISION_MODEL_TIMEOUT_SECONDS = 25.0
VISION_GATE_BUDGET_SECONDS = 90.0

# Owner rule 2026-10-17 (FINAL): vision uses ONLY the FlashFast combo (= gemini-3.8-flash
# medium/low/high) and those three ids. Never My-Combo (it can route to Claude, which ignores images).
VISION_COMBOS = ("FlashFast",)

DEFAULT_VISION_MODELS = [
    "FlashFast",
    "ag/gemini-3.8-flash-medium",
    "ag/gemini-3.8-flash-low",
    "ag/gemini-3.8-flash-high",
]

_MODELS_CACHE: dict[str, Any] = {"timestamp": 0.0, "ids": set()}

PROMPT_COMPOSER = (
    'Analyze the phone screenshot (image 1) and reference photo (image 2, if provided). '
    'Respond ONLY with a JSON object: {"screen": "composer|post_settings|profile|other", '
    '"author_name": string, "has_attached_photo": boolean, "caption_starts_with": string, '
    '"same_photo_as_reference": boolean}. '
    'has_attached_photo is true when a photo or a video is attached. '
    'Image 1 is the phone screenshot; image 2 is the photo we intend to post. '
    'caption_starts_with must be the first 40 characters of the text typed in the composer post box, or "" if empty.'
)

PROMPT_POST_SETTINGS = (
    'Analyze the phone screenshot. Respond ONLY with a JSON object: '
    '{"screen": "composer|post_settings|profile|other", "share_button_visible": boolean, "audience": string}.'
)

PROMPT_REEL_SETTINGS = (
    'Analyze the phone screenshot. Respond ONLY with a JSON object: '
    '{"screen": "reel_settings|post_settings|composer|profile|other", '
    '"share_now_visible": boolean, "has_video_thumbnail": boolean}. '
    'reel_settings is the "Reel Settings" screen with a "Share now" button.'
)

PROMPT_POSTED = (
    'Analyze the phone screenshot. Respond ONLY with a JSON object: '
    '{"screen": "composer|post_settings|profile|other", '
    '"posts": [{"author_name": string, "caption_starts_with": string, "has_photo": boolean}], '
    '"still_uploading": boolean}. '
    'has_photo is true when the post shows a photo or a video. '
    'Include every post visible on screen, top to bottom. '
    'caption_starts_with must be the first 40 characters of that post\'s text.'
)


def get_vision_models() -> list[str]:
    raw_env = os.environ.get("PHONE_RUNNER_VISION_MODELS")
    if raw_env is not None and raw_env.strip():
        raw_models = [m.strip() for m in raw_env.split(",") if m.strip()]
    else:
        raw_models = list(DEFAULT_VISION_MODELS)

    candidates = [
        m for m in raw_models
        if (m in VISION_COMBOS or "gemini" in m.lower())
        and not m.startswith("cx/")
        and "claude" not in m.lower()
    ]

    now = time.time()
    cached_ids = _MODELS_CACHE.get("ids", set())
    cache_time = _MODELS_CACHE.get("timestamp", 0.0)

    if cached_ids and (now - cache_time < 600.0):
        return [m for m in candidates if m in cached_ids]

    gateway = get_mobilerun_gateway_url().rstrip("/")
    url = f"{gateway}/models"
    api_key = os.environ.get("OPENAI_API_KEY", "")

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "PhoneRunner/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = []
            if isinstance(data, dict):
                items = data.get("data", []) or data.get("models", [])
            elif isinstance(data, list):
                items = data
            live_ids = set()
            for it in items:
                if isinstance(it, dict) and "id" in it:
                    live_ids.add(str(it["id"]))
                elif isinstance(it, str):
                    live_ids.add(it)
            if live_ids:
                _MODELS_CACHE["timestamp"] = now
                _MODELS_CACHE["ids"] = live_ids
                return [m for m in candidates if m in live_ids]
    except Exception:
        pass

    return candidates


def capture_screenshot_png(serial: str, timeout: float = 20.0) -> bytes | None:
    if not is_serial_valid_format(serial):
        return None
    cmd = [get_adb_path(), "-s", serial, "exec-out", "screencap", "-p"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if proc.returncode == 0 and proc.stdout and proc.stdout.startswith(b"\x89PNG"):
            return proc.stdout
        return None
    except Exception:
        return None


def strip_json_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


def ask_vision(
    png: bytes,
    prompt: str,
    reference_jpeg: bytes | None = None,
    timeout: float = VISION_MODEL_TIMEOUT_SECONDS,
    total_budget: float = VISION_GATE_BUDGET_SECONDS,
) -> tuple[dict | None, str, str]:
    models = get_vision_models()
    if not models:
        return None, "", "no vision models available"

    b64_png = base64.b64encode(png).decode("ascii")
    content_parts: list[dict[str, Any]] = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_png}"}},
    ]
    if reference_jpeg:
        b64_ref = base64.b64encode(reference_jpeg).decode("ascii")
        content_parts.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_ref}"}}
        )

    gateway = get_mobilerun_gateway_url().rstrip("/")
    url = f"{gateway}/chat/completions"
    api_key = os.environ.get("OPENAI_API_KEY", "")

    started = time.monotonic()
    last_detail = "no models attempted"
    for model in models:
        elapsed = time.monotonic() - started
        remaining_budget = total_budget - elapsed
        if remaining_budget <= 0:
            return None, "", "vision budget exhausted"

        per_model_timeout = min(timeout, remaining_budget)
        payload = {
            "model": model,
            "stream": False,
            "temperature": 0,
            "max_tokens": 400,
            "messages": [
                {
                    "role": "user",
                    "content": content_parts,
                }
            ],
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "PhoneRunner/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=per_model_timeout) as resp:
                raw_bytes = resp.read()
                resp_json = json.loads(raw_bytes.decode("utf-8"))
                choices = resp_json.get("choices")
                if not choices or not isinstance(choices, list):
                    last_detail = f"model {model} returned no choices"
                    continue
                content = choices[0].get("message", {}).get("content", "")
                if not content or not isinstance(content, str):
                    last_detail = f"model {model} returned empty content"
                    continue
                cleaned = strip_json_fences(content)
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict):
                    return parsed, model, "ok"
                last_detail = f"model {model} returned non-dict JSON"
        except Exception as exc:
            last_detail = f"model {model} error: {type(exc).__name__}"
            continue

    if time.monotonic() - started >= total_budget:
        return None, "", "vision budget exhausted"

    return None, "", f"all vision models failed; last detail: {last_detail}"


def _normalize_vision_text(t: str | None) -> str:
    if not t or not isinstance(t, str):
        return ""
    return " ".join(t.split()).casefold()


def loose_text_key(s: str | None, n: int = 15) -> str:
    if not s or not isinstance(s, str):
        return ""
    normalized = unicodedata.normalize("NFKD", s)
    no_combining = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    folded = no_combining.casefold()
    alnum_only = "".join(ch for ch in folded if ch.isalnum())
    return alnum_only[:n]


def _match_loose_caption(observed: str | None, expected: str | None) -> bool:
    exp_key = loose_text_key(expected, 15)
    n = len(exp_key)
    if n == 0:
        return loose_text_key(observed, 15) == ""
    obs_key = loose_text_key(observed, n)
    return obs_key == exp_key


CAPTION_FRAGMENT_MIN_CHARS = 10


def _caption_fragment_in(observed: str | None, expected: str | None) -> bool:
    """True when the visible caption text is part of the expected caption. A long caption scrolls the
    post box to its END after pasting, so only a later fragment is on screen (exact text is checked
    from the accessibility tree)."""
    obs_key = loose_text_key(observed, 10_000)
    if len(obs_key) < CAPTION_FRAGMENT_MIN_CHARS:
        return False
    return obs_key[:40] in loose_text_key(expected, 100_000)


def check_composer(
    ans: dict | None,
    expected_author: str,
    expected_caption: str = "",
    require_caption: bool = False,
    require_photo: bool = True,
    require_same_reference: bool = True,
) -> tuple[bool, str]:
    if not ans or not isinstance(ans, dict):
        return False, "invalid or empty vision response"

    screen = ans.get("screen")
    if screen != "composer":
        return False, f"screen is not composer (got '{screen}')"

    author = (ans.get("author_name") or "").strip()
    if author != expected_author.strip():
        return False, f"author mismatch: expected '{expected_author.strip()}', got '{author}'"

    if require_photo:
        if ans.get("has_attached_photo") is not True:
            return False, "has_attached_photo is not True"

        if require_same_reference and ans.get("same_photo_as_reference") is not True:
            return False, "same_photo_as_reference is not True"

    cap_ans = ans.get("caption_starts_with") or ""
    if require_caption:
        if not (_match_loose_caption(cap_ans, expected_caption) or _caption_fragment_in(cap_ans, expected_caption)):
            return False, f"caption mismatch: observed '{cap_ans}' does not loosely match expected '{expected_caption}'"
    else:
        if cap_ans.strip() != "":
            return False, f"expected empty caption, got '{cap_ans}'"

    return True, "composer verified"


def check_post_settings(ans: dict | None) -> tuple[bool, str]:
    if not ans or not isinstance(ans, dict):
        return False, "invalid or empty vision response"
    if ans.get("screen") != "post_settings":
        return False, f"screen is not post_settings (got '{ans.get('screen')}')"
    if ans.get("share_button_visible") is not True:
        return False, "share_button_visible is not True"
    return True, "post_settings verified"


def check_reel_settings(ans: dict | None) -> tuple[bool, str]:
    if not ans or not isinstance(ans, dict):
        return False, "invalid or empty vision response"
    if ans.get("screen") != "reel_settings":
        return False, f"screen is not reel_settings (got '{ans.get('screen')}')"
    if ans.get("share_now_visible") is not True:
        return False, "share_now_visible is not True"
    return True, "reel_settings verified"


def check_posted(
    ans: dict | None,
    expected_author: str,
    expected_caption: str = "",
) -> tuple[bool, str]:
    if not ans or not isinstance(ans, dict):
        return False, "invalid or empty vision response"

    posts = ans.get("posts")
    if not isinstance(posts, list):
        if "top_post_caption_starts_with" in ans or "top_post_has_photo" in ans or "author_name" in ans:
            posts = [
                {
                    "author_name": ans.get("author_name", ""),
                    "caption_starts_with": ans.get("top_post_caption_starts_with", ""),
                    "has_photo": ans.get("top_post_has_photo", False),
                }
            ]
        else:
            posts = []

    exp_auth = expected_author.strip()
    for post in posts:
        if not isinstance(post, dict):
            continue
        p_auth = str(post.get("author_name") or "").strip()
        if p_auth != exp_auth:
            continue
        if post.get("has_photo") is not True:
            continue
        p_cap = post.get("caption_starts_with") or ""
        if _match_loose_caption(p_cap, expected_caption):
            return True, "posted verified"

    if not posts:
        return False, "no posts found in vision response"
    return False, f"no matching post found for author '{exp_auth}'"


def verify_gate(
    serial: str,
    gate: str,
    expected_author: str = "",
    expected_caption: str = "",
    require_caption: bool = False,
    reference_jpeg: bytes | None = None,
    timeout: float = VISION_MODEL_TIMEOUT_SECONDS,
    total_budget: float = VISION_GATE_BUDGET_SECONDS,
    require_same_reference: bool = True,
    **kwargs: Any,
) -> tuple[bool, dict[str, Any]]:
    png = capture_screenshot_png(serial)
    if not png:
        evidence = {
            "gate": gate,
            "model": "",
            "ok": False,
            "detail": "screenshot capture failed",
            "answer": None,
        }
        return False, evidence

    if gate in ("composer_before_typing", "composer_after_typing"):
        prompt = PROMPT_COMPOSER
    elif gate == "post_settings":
        prompt = PROMPT_POST_SETTINGS
    elif gate == "reel_settings":
        prompt = PROMPT_REEL_SETTINGS
    elif gate == "posted":
        prompt = PROMPT_POSTED
    else:
        evidence = {
            "gate": gate,
            "model": "",
            "ok": False,
            "detail": f"unknown gate: {gate}",
            "answer": None,
        }
        return False, evidence

    ans, model, detail = ask_vision(
        png,
        prompt=prompt,
        reference_jpeg=reference_jpeg,
        timeout=timeout,
        total_budget=total_budget,
    )
    if ans is None:
        evidence = {
            "gate": gate,
            "model": model,
            "ok": False,
            "detail": detail,
            "answer": None,
        }
        return False, evidence

    if gate == "composer_before_typing":
        ok, chk_detail = check_composer(
            ans,
            expected_author=expected_author,
            expected_caption=expected_caption,
            require_caption=False,
            require_photo=True,
            require_same_reference=require_same_reference,
        )
    elif gate == "composer_after_typing":
        ok, chk_detail = check_composer(
            ans,
            expected_author=expected_author,
            expected_caption=expected_caption,
            require_caption=True,
            require_photo=False,
            require_same_reference=require_same_reference,
        )
    elif gate == "post_settings":
        ok, chk_detail = check_post_settings(ans)
    elif gate == "reel_settings":
        ok, chk_detail = check_reel_settings(ans)
    elif gate == "posted":
        ok, chk_detail = check_posted(
            ans,
            expected_author=expected_author,
            expected_caption=expected_caption,
        )
    else:
        ok, chk_detail = False, f"unhandled gate check: {gate}"

    evidence = {
        "gate": gate,
        "model": model,
        "ok": ok,
        "detail": chk_detail,
        "answer": ans,
    }
    return ok, evidence
