"""Argon prompt polish: rewrite Mode 8 clip prompts via gemini-web-serve, with code guards and retry."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from app.models.settings import Settings
from app.services.ai_video_cover import (
    build_cover_copy_prompt,
    build_cover_image_prompt,
    parse_cover_copy,
)
from app.services.encryption import decrypt

logger = logging.getLogger(__name__)

GENERATE_TIMEOUT_S = 900

PROTECTED_PHRASES = (
    "no text",
    "watermark",
    "(no F1, FIA, MotoGP, FIM or Dorna logos)",
    "no gore",
    "no music",
    "no sound effects",
    "no cuts",
)

BRAND_NAMES = (
    "Ferrari", "Brabham", "McLaren", "Mercedes", "Red Bull", "Williams",
    "Lotus", "Renault", "Alpine", "Aston Martin", "Haas", "Sauber",
    "Alfa Romeo", "Tyrrell", "Ligier", "Ducati", "Yamaha", "Honda",
    "Suzuki", "KTM", "Aprilia", "Kawasaki", "BMW", "Porsche",
    "Lamborghini", "Bugatti",
)

_DOUBLED_SMALL = re.compile(r"(?i)\b(the|a|an|of|to|and|in|on)\s+\1\b")
_QUOTE_RE = re.compile(r'"([^"]+)"')
_I2V_PREFIX = "Continue seamlessly from this exact frame"


class PolishUnavailable(Exception):
    """gemini-web-serve could not run Argon now (no account ready, proxy down, unreachable, auth, 5xx)."""

    def __init__(self, kind: str, detail: str = "", retry_after_s: int | None = None):
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class PolishConfig:
    base_url: str
    token: str
    model: str


@dataclass
class PolishResult:
    ok: bool
    clips: list[dict] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    voice_sentence: str = ""
    ambience_sentence: str = ""
    attempts: int = 0
    problems: list[str] = field(default_factory=list)
    account: str | None = None
    seconds: float = 0.0


def get_polish_config(db) -> PolishConfig | None:
    """Return PolishConfig from the Settings row, or None when no token is set."""
    row = db.query(Settings).first() if db is not None else None
    if row is None:
        return None
    token_enc = getattr(row, "prompt_polish_token_encrypted", None)
    if not token_enc:
        return None
    try:
        token = decrypt(token_enc)
    except Exception:
        return None
    if not token:
        return None
    base_url = str(getattr(row, "prompt_polish_base_url", "http://172.17.0.1:8098") or "http://172.17.0.1:8098").rstrip("/")
    model = str(getattr(row, "prompt_polish_model", "gemini-argon") or "gemini-argon")
    return PolishConfig(base_url=base_url, token=token, model=model)


def build_instruction(clips: list[dict], *, template, niche: str | None) -> str:
    """Build the Argon instruction prompt from clips and template."""
    n = len(clips)
    clip_seconds = template.clip_seconds
    template_name = template.name
    niche_str = niche if niche else "general"
    parts = []
    for c in clips:
        parts.append(f"===== CLIP {c['index']} — mode {c['mode']} =====\n{c['prompt']}")
    clips_block = "\n\n".join(parts) + "\n"
    return (
        f'You are a senior prompt engineer for Google Flow (Veo 3). Below are {n} clip prompts ({clip_seconds} s each, vertical 9:16) for ONE short video made from template "{template_name}" (niche: {niche_str}). Each clip is generated separately and the clips are joined in order. A clip with mode t2v is text-to-video. A clip with mode i2v is image-to-video: it starts from the LAST FRAME of the previous clip, so it must begin by continuing that exact frame.\n'
        "\n"
        "Rewrite every clip prompt so the final video is realistic, physically and historically logical, and feels like ONE continuous video:\n"
        "1. Fix every logic problem: duplicated words or sentences, awkward machine-filled phrases, physically impossible actions, camera moves that cannot happen in one shot, anything that contradicts the story, the period or real-world engineering/anatomy. Everything real in the scene obeys real physics: gravity and momentum, a moving vehicle keeps moving the same way at a believable speed, wheels turn with the ground, parts move only the way the real mechanism lets them, and nothing appears, vanishes or changes shape without a cause. Never invent facts, mechanisms or events that are not in the original. Keep the same story, the same facts and the same order of events.\n"
        "   Deliberate visual styles of the template are NOT logic errors: keep them exactly as the original describes them - X-ray or semi-transparent cutaways, exploded technical views where parts separate, float and then reassemble into the same object, glowing airflow, force or signal lines, slow motion. Around such a visualisation everything else still obeys real physics, and afterwards the object is back in exactly the same state.\n"
        "2. Visual continuity: describe the main subject (vehicle, rider, body part, object) once in concrete detail (shape, colours, materials, the exact lettering already in the prompt) and repeat that exact description in every clip. Each clip must end on a frame the next clip can continue from naturally.\n"
        f"3. Audio continuity: write ONE narrator voice sentence in rich concrete detail (keep the narrator the original prompt describes; add pitch, timbre, pace that fits the narration inside {clip_seconds} seconds, and a close-mic dry recording) and put that exact same sentence, word for word, in every clip right before the voice-over line. Write ONE background sound sentence (keep what the original allows: if it says no music, keep no music; if it has a music bed, keep one calm continuous bed with a fixed tempo and level) at a fixed low level under the voice, and put that exact same sentence, word for word, in every clip.\n"
        "Hard rules:\n"
        "- Keep every text that is inside double quotes in the original EXACTLY as written (every voice-over line and any lettering). Do not add any new double-quoted text.\n"
        "- Keep every restriction of the original (no text, no logos or the exact logo rules, no watermark, no gore, no music, no sound effects, no cuts — whichever the original has).\n"
        "- Do not name or imply any real team, maker, sponsor or brand that the original does not already name.\n"
        "- Every i2v clip must start with: Continue seamlessly from this exact frame\n"
        "- Each clip prompt must stay under 2,700 characters.\n"
        "- Do not change the number of clips or their order.\n"
        'Return ONLY JSON, no markdown: {"clips":[{"index":1,"prompt":"..."}],"voice_sentence":"...","ambience_sentence":"...","changes":["one short reason per change"]}\n'
        "\n"
        f"{clips_block}"
    )


def parse_answer(text: str) -> dict:
    """Parse Argon's answer. Strip markdown fences, extract outermost JSON object. Raises ValueError."""
    cleaned = text.strip()
    # Strip markdown fences
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line (```json or ```)
        lines = lines[1:]
        # Remove trailing ``` if present
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    # Find outermost { ... }
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("answer is not valid JSON in the required shape")
    depth = 0
    end = -1
    for i in range(start, len(cleaned)):
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        raise ValueError("answer is not valid JSON in the required shape")
    json_str = cleaned[start:end + 1]
    try:
        obj = json.loads(json_str)
    except json.JSONDecodeError:
        raise ValueError("answer is not valid JSON in the required shape")
    if not isinstance(obj, dict):
        raise ValueError("answer is not valid JSON in the required shape")
    # Validate required keys
    clips = obj.get("clips")
    voice = obj.get("voice_sentence")
    ambience = obj.get("ambience_sentence")
    changes = obj.get("changes")
    if not isinstance(clips, list) or not isinstance(voice, str) or not isinstance(ambience, str) or not isinstance(changes, list):
        raise ValueError("answer is not valid JSON in the required shape")
    for c in clips:
        if not isinstance(c, dict) or not isinstance(c.get("index"), int) or not isinstance(c.get("prompt"), str):
            raise ValueError("answer is not valid JSON in the required shape")
    return obj


def check_polished(original: list[dict], polished: list[dict], voice: str, ambience: str,
                   *, names_allowed: bool = False) -> list[str]:
    """Run guards. Returns [] if all pass, else list of problem strings."""
    problems = []

    # 1. Same number and set of indexes
    orig_indexes = sorted(c["index"] for c in original)
    pol_indexes = sorted(c["index"] for c in polished)
    if orig_indexes != pol_indexes:
        problems.append(f"clip indexes mismatch: expected {orig_indexes}, got {pol_indexes}")
        return problems  # Can't check per-clip if indexes don't match

    orig_by_idx = {c["index"]: c for c in original}
    pol_by_idx = {c["index"]: c for c in polished}

    # 3. voice and ambience length
    if len(voice) < 40:
        problems.append("voice_sentence is shorter than 40 characters")
    if len(ambience) < 40:
        problems.append("ambience_sentence is shorter than 40 characters")

    for idx in orig_indexes:
        orig_clip = orig_by_idx[idx]
        pol_clip = pol_by_idx[idx]
        orig_prompt = orig_clip["prompt"]
        pol_prompt = pol_clip["prompt"]

        # 2. Quotes
        orig_quotes = set(_QUOTE_RE.findall(orig_prompt))
        pol_quotes = set(_QUOTE_RE.findall(pol_prompt))
        missing = orig_quotes - pol_quotes
        if missing:
            problems.append(f"clip {idx}: original quoted text missing: {missing}")
        extra = pol_quotes - orig_quotes
        if extra:
            problems.append(f"clip {idx}: extra quoted text added: {extra}")

        # 3 cont. voice and ambience in every clip
        if voice and len(voice) >= 40 and voice not in pol_prompt:
            problems.append(f"clip {idx}: voice_sentence missing")
        if ambience and len(ambience) >= 40 and ambience not in pol_prompt:
            problems.append(f"clip {idx}: ambience_sentence missing")

        # 4. Length
        if len(pol_prompt) > 2900:
            problems.append(f"clip {idx}: prompt is {len(pol_prompt)} characters, exceeds 2900")

        # 5. Protected phrases
        for phrase in PROTECTED_PHRASES:
            if phrase.lower() in orig_prompt.lower() and phrase.lower() not in pol_prompt.lower():
                problems.append(f"clip {idx}: protected phrase missing: {phrase}")

        # 6. i2v prefix
        if orig_clip["mode"] == "i2v" and not pol_prompt.startswith(_I2V_PREFIX):
            problems.append(f"clip {idx}: i2v clip must start with \"{_I2V_PREFIX}\"")

        # 7. Doubled small words
        if _DOUBLED_SMALL.search(pol_prompt):
            match = _DOUBLED_SMALL.search(pol_prompt)
            problems.append(f"clip {idx}: doubled word \"{match.group()}\"")

        # 8. Brand names
        if not names_allowed:
            orig_lower = orig_prompt.lower()
            pol_lower = pol_prompt.lower()
            for brand in BRAND_NAMES:
                pattern = re.compile(r"\b" + re.escape(brand) + r"\b", re.IGNORECASE)
                if pattern.search(pol_prompt) and not pattern.search(orig_prompt):
                    problems.append(f"clip {idx}: brand name \"{brand}\" added")

    return problems


def _post_generate(config: PolishConfig, prompt: str, http_post=None):
    if not config or not config.token:
        raise PolishUnavailable("not_configured", "no token")
    post_fn = http_post or httpx.post
    url = f"{config.base_url.rstrip('/')}/v1/generate"
    headers = {"Authorization": f"Bearer {config.token}"}
    body = {"prompt": prompt, "model": config.model, "extended_thinking": True}

    try:
        resp = post_fn(url, headers=headers, json=body, timeout=GENERATE_TIMEOUT_S)
    except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as e:
        raise PolishUnavailable("unreachable", str(e))
    except httpx.TimeoutException as e:
        raise PolishUnavailable("unreachable", str(e))

    sc = resp.status_code
    if sc != 200:
        try:
            rj = resp.json()
        except Exception:
            raise PolishUnavailable("upstream", f"HTTP {sc}")
        if sc == 401:
            raise PolishUnavailable("unauthorized", rj.get("error", "unauthorized"))
        if sc == 400:
            raise PolishUnavailable("bad_request", rj.get("error", "bad_request"))
        if sc == 503:
            err = rj.get("error", "")
            if err == "no_account_ready":
                raise PolishUnavailable("no_account_ready", err, retry_after_s=rj.get("retry_after_s"))
            if err == "proxy_down":
                raise PolishUnavailable("proxy_down", err)
            raise PolishUnavailable("upstream", err)
        raise PolishUnavailable("upstream", str(rj.get("error", f"HTTP {sc}")))

    return resp


def polish_cover(
    *,
    title: str,
    caption: str | None,
    niche: str | None,
    recent: list[str],
    subject_hint: str,
    aspect: str,
    config: PolishConfig,
    max_attempts: int = 2,
    http_post=None,
) -> dict | None:
    cover_prompt = build_cover_copy_prompt(title, caption, niche, recent)
    extra = (
        "\n\nThe video's main subject, exactly as it appears in the video (keep the cover picture consistent with it):\n"
        f"{subject_hint}\n"
        'Describe the cover background picture ("scene") in rich concrete detail (2-3 sentences) for the SAME subject, '
        'same colours and same look as above, following every picture rule above.\n'
    )
    prompt = cover_prompt + extra

    for _ in range(max_attempts):
        resp = _post_generate(config, prompt, http_post=http_post)
        try:
            rj = resp.json()
        except Exception:
            continue
        text = rj.get("text", "")
        try:
            parsed = parse_cover_copy(text, title, strict=True)
        except ValueError:
            continue
        scene = parsed.get("scene", "")
        image_prompt = build_cover_image_prompt(title, scene, niche, aspect)
        return {
            "lines": parsed["lines"],
            "highlight": parsed["highlight"],
            "scene": scene,
            "image_prompt": image_prompt,
            "by": "argon",
        }
    return None


def polish_clips(clips: list[dict], *, template, niche: str | None, config: PolishConfig,
                 max_attempts: int = 3, http_post=None) -> PolishResult:
    """Rewrite clip prompts via Argon. Raises PolishUnavailable on infra errors."""
    if not config or not config.token:
        raise PolishUnavailable("not_configured", "no token")
    names_allowed = bool(template.raw.get("real_names_allowed"))

    total_seconds = 0.0
    last_account = None
    last_problems: list[str] = []
    prev_answer_text: str | None = None

    for attempt in range(1, max_attempts + 1):
        prompt = build_instruction(clips, template=template, niche=niche)
        if attempt >= 2 and prev_answer_text is not None:
            feedback_lines = ["Your previous answer failed these automatic checks:"]
            for p in last_problems:
                feedback_lines.append(f"- {p}")
            feedback_lines.append("Fix every problem and return the complete JSON again (all clips), following every rule above.")
            feedback_lines.append("Your previous answer was:")
            feedback_lines.append(prev_answer_text[:20000])
            prompt = prompt + "\n" + "\n".join(feedback_lines)

        resp = _post_generate(config, prompt, http_post=http_post)

        try:
            rj = resp.json()
        except Exception:
            last_problems = ["answer is not valid JSON in the required shape"]
            prev_answer_text = getattr(resp, "text", "")
            logger.info("prompt_polish attempt %d non-json 200 response problems=1", attempt)
            continue

        answer_text = rj.get("text", "")
        acct = rj.get("account")
        secs = float(rj.get("seconds") or 0.0)
        total_seconds += secs
        last_account = acct

        # Parse
        try:
            parsed = parse_answer(answer_text)
        except ValueError:
            last_problems = ["answer is not valid JSON in the required shape"]
            prev_answer_text = answer_text
            logger.info("prompt_polish attempt %d account=%s seconds=%.1f problems=1", attempt, acct, secs)
            continue

        # Build polished clips
        polished_by_idx = {c["index"]: c for c in parsed["clips"]}
        polished_clips = []
        for orig in clips:
            idx = orig["index"]
            if idx not in polished_by_idx:
                last_problems = [f"clip indexes mismatch: polished missing index {idx}"]
                prev_answer_text = answer_text
                logger.info("prompt_polish attempt %d account=%s seconds=%.1f problems=1", attempt, acct, secs)
                continue
            polished_clips.append({
                "index": idx,
                "mode": orig["mode"],  # always from original
                "prompt": polished_by_idx[idx]["prompt"],
            })

        if len(polished_clips) != len(clips):
            # index mismatch already logged above
            continue

        voice = parsed["voice_sentence"]
        ambience = parsed["ambience_sentence"]
        changes = parsed["changes"]
        problems = check_polished(clips, polished_clips, voice, ambience, names_allowed=names_allowed)
        logger.info("prompt_polish attempt %d account=%s seconds=%.1f problems=%d", attempt, acct, secs, len(problems))

        if not problems:
            return PolishResult(
                ok=True,
                clips=polished_clips,
                changes=changes,
                voice_sentence=voice,
                ambience_sentence=ambience,
                attempts=attempt,
                problems=[],
                account=last_account,
                seconds=total_seconds,
            )
        last_problems = problems
        prev_answer_text = answer_text

    return PolishResult(
        ok=False,
        clips=[],
        changes=[],
        voice_sentence="",
        ambience_sentence="",
        attempts=max_attempts,
        problems=last_problems,
        account=last_account,
        seconds=total_seconds,
    )
