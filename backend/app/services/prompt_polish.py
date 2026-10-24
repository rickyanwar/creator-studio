"""Argon prompt polish: rewrite Mode 8 clip prompts via gemini-web-serve, with code guards and retry."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from app.models.settings import Settings
from app.services.encryption import decrypt
from app.services.gptcli_client import (
    GptcliConfig,
    generate_text,
    get_gptcli_config,
    gptcli_unavailable_until,
)

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

# Motorsport motion rules (owner 2026-10-23, after a NSR500 video "turned" upright and sideways in a corner).
# Argon must put the sentence for the niche, word for word, in every clip; check_polished enforces it.
MOTION_SENTENCES = {
    "motogp": (
        "Motion: one manoeuvre per shot; on track it runs at full racing speed; it turns only while bike and "
        "rider lean together about 45 degrees, rides upright only on straights, never slides sideways."
    ),
    "f1": (
        "Motion: one manoeuvre per shot; the car turns only by steering its front wheels, keeps its direction "
        "across cuts, never slides sideways."
    ),
}
VAGUE_COLOUR_WORDS = ("solid-colour", "solid colour", "solid-color", "solid color")


def motion_sentence_for(niche: str | None) -> str | None:
    """The fixed motion-rules sentence for a motorsport niche, None for other niches."""
    return MOTION_SENTENCES.get((niche or "").strip().lower())


def _motorsport_rules(motion: str | None) -> str:
    """Extra instruction lines for motorsport templates (empty for other niches)."""
    if not motion:
        return ""
    return (
        "4. Motorsport: describe the livery panel by panel with exact colours (for a car: nose, bodywork and sidepods, "
        "engine cover, wings, wheels, helmet; for a motorcycle: upper fairing, lower fairing, tank, tail, number boards, "
        "wheels, rider suit, helmet). Never use vague colour words such as \"solid-colour\" or \"matching\"; repeat that "
        "exact livery sentence in every clip. If a clip asks for several manoeuvres (for example a straight, a corner "
        "and an exit), keep the one that matches its voice-over and simplify the rest. A corner always names its "
        "direction (left or right) and where the camera stands. On a circuit the vehicle runs at real racing speed: "
        "background streaking with motion blur, the camera tracking hard; never a slow cruise unless the original "
        "asks for slow motion.\n"
        "   Engine sound: the background sound sentence names the engine exactly as the subject is described - a "
        "two-stroke sounds like a crisp, ringing, crackling two-stroke; a four-stroke like a deep, smooth four-stroke "
        "roar - loud at speed, quiet only when parked, the same character in every clip, never switching type. The "
        "narrator voice sentence keeps the same person (gender, age, accent) in every clip.\n"
        f"5. Put this sentence word for word in every clip: {motion}\n"
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


class PolishInterrupted(PolishUnavailable):
    """Argon became unavailable after at least one answered attempt; carries what is needed to resume."""

    def __init__(self, kind: str, detail: str = "", retry_after_s: int | None = None, *,
                 answer: str, problems: list[str], attempts_used: int, account: str | None, seconds: float):
        super().__init__(kind, detail, retry_after_s)
        self.answer = answer
        self.problems = list(problems)
        self.attempts_used = attempts_used
        self.account = account
        self.seconds = seconds


@dataclass(frozen=True)
class PolishConfig:
    base_url: str
    token: str
    model: str
    chatgpt: GptcliConfig | None = None


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

    chatgpt_cfg = None
    try:
        gcfg = get_gptcli_config(db)
        if gcfg and gcfg.token and gcfg.base_url:
            chatgpt_cfg = gcfg
    except Exception as e:
        logger.warning("Failed to load gptcli config for prompt polish: %s", e)
        chatgpt_cfg = None

    return PolishConfig(base_url=base_url, token=token, model=model, chatgpt=chatgpt_cfg)


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
        f"{_motorsport_rules(motion_sentence_for(niche))}"
        "Hard rules:\n"
        "- Keep every text that is inside double quotes in the original EXACTLY as written (every voice-over line and any lettering). Do not add any new double-quoted text.\n"
        "- Keep every restriction of the original (no text, no logos or the exact logo rules, no watermark, no gore, no music, no sound effects, no cuts — whichever the original has).\n"
        "- Do not name or imply any real team, maker, sponsor or brand that the original does not already name.\n"
        "- Every i2v clip must start with: Continue seamlessly from this exact frame\n"
        "- Each clip prompt must stay under 2,600 characters: cut decorative or repeated words first, never the rules, quotes or livery.\n"
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


def _motion_problems(polished: list[dict], motion: str | None) -> list[str]:
    """Motorsport guards: the motion-rules sentence in every clip, no vague colour words."""
    problems = []
    for clip in polished:
        text = clip["prompt"]
        if motion and motion not in text:
            problems.append(f"clip {clip['index']}: motion rules sentence missing (copy it word for word)")
        vague = [w for w in VAGUE_COLOUR_WORDS if w in text.lower()]
        if motion and vague:
            problems.append(f"clip {clip['index']}: vague colour word {vague[0]!r}; name the exact colour")
    return problems


def _engine_type(original: list[dict]) -> str | None:
    """'two-stroke' / 'four-stroke' when the original subject names its engine type, else None."""
    text = " ".join(c["prompt"].lower() for c in original)
    for kind, words in (("two-stroke", ("two-stroke", "2-stroke")), ("four-stroke", ("four-stroke", "4-stroke"))):
        if any(w in text for w in words):
            return kind
    return None


def check_polished(original: list[dict], polished: list[dict], voice: str, ambience: str,
                   *, names_allowed: bool = False, motion: str | None = None) -> list[str]:
    """Run guards. Returns [] if all pass, else list of problem strings."""
    problems = _motion_problems(polished, motion) if motion else []
    engine = _engine_type(original) if motion else None
    if engine and engine not in ambience.lower():
        problems.append(f"ambience_sentence must name the {engine} engine sound of this subject")

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
    except httpx.TransportError as e:
        raise PolishUnavailable("unreachable", str(e))
    except OSError as e:
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


def _post_generate_chatgpt(gcfg: GptcliConfig, prompt: str, http_post=None):
    req_id = "polish-" + hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:16]
    return generate_text(gcfg, prompt, request_id=req_id, http_post=http_post)


def _ask(config: PolishConfig, prompt: str, http_post=None, attempt: int = 1):
    try:
        return _post_generate(config, prompt, http_post=http_post)
    except PolishUnavailable as exc:
        if config.chatgpt and gptcli_unavailable_until() is None:
            logger.info("prompt_polish attempt %d: Argon unavailable (%s); asking ChatGPT", attempt, exc.kind)
            try:
                return _post_generate_chatgpt(config.chatgpt, prompt, http_post=http_post)
            except PolishUnavailable as chatgpt_exc:
                detail_base = exc.detail or exc.kind
                new_detail = f"{detail_base} | chatgpt: {chatgpt_exc.kind}"
                raise PolishUnavailable(exc.kind, new_detail, exc.retry_after_s) from chatgpt_exc
        raise


def parse_clip_answer(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    depth = 0
    end = -1
    for index in range(start, len(cleaned)) if start >= 0 else ():
        if cleaned[index] == "{":
            depth += 1
        elif cleaned[index] == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end < 0:
        raise ValueError("answer is not valid JSON in the required shape")
    try:
        value = json.loads(cleaned[start:end])
    except json.JSONDecodeError as exc:
        raise ValueError("answer is not valid JSON in the required shape") from exc
    if not isinstance(value, dict):
        raise ValueError("answer is not valid JSON in the required shape")
    if not isinstance(value.get("prompt"), str) or not isinstance(value.get("changes"), list):
        raise ValueError("answer is not valid JSON in the required shape")
    return value


def repolish_clip(prompt: str, *, mode: str, clip_index: int, clips_total: int, issues: list[dict],
                  template, niche: str | None, config: PolishConfig, http_post=None) -> str:
    i2v = (' It is image-to-video: it starts from the last frame of the previous clip and must begin with "Continue seamlessly from this exact frame".'
           if mode != "t2v" else "")
    issue_lines = "\n".join(f"- [{item['severity']}] {item['problem']}" for item in issues)
    instruction = f'''You are a senior prompt engineer for Google Flow (Veo 3). The prompt below made clip {clip_index} of {clips_total} ({template.clip_seconds} s, vertical 9:16) of one short video from template "{template.name}" (niche: {niche or "general"}).{i2v}
A reviewer watched the generated clip and found these logic errors:
{issue_lines}
Rewrite the prompt so Flow does not make these errors again:
- Keep the same story, facts, subject, livery, camera plan and order of events. Change only what prevents the listed errors: state the correct behaviour plainly (for example the direction of travel, which parts stay still, that both tyres stay on the ground), and simplify or remove the action that caused an error. Fewer, clearer actions beat more words.
- Keep every text inside double quotes EXACTLY as written, in the same order. Do not add new double-quoted text.
- Keep the narrator voice sentence and the background sound sentence exactly as they are.
- Keep every restriction of the original (no text, the logo rules, no watermark, no cuts - whichever it has) and its intended visual styles (X-ray cutaway, exploded view, glowing lines).
- Do not invent facts, mechanisms, brands or events.
- Stay under 2,600 characters.
Return ONLY JSON, no markdown: {{"prompt":"...","changes":["one short reason per change"]}}

PROMPT:
{prompt}'''
    original_quotes = _QUOTE_RE.findall(prompt)
    for attempt in (1, 2):
        try:
            resp = _ask(config, instruction, http_post=http_post, attempt=attempt)
            try:
                response_json = resp.json()
            except Exception as exc:
                raise ValueError("repolish answer invalid") from exc
            answer = parse_clip_answer(response_json.get("text", ""))
            rewritten = answer["prompt"]
            if (_QUOTE_RE.findall(rewritten) != original_quotes or len(rewritten) > 2600
                    or (mode != "t2v" and not rewritten.startswith(_I2V_PREFIX))):
                raise ValueError("repolish answer invalid")
            return rewritten
        except ValueError:
            if attempt == 2:
                raise ValueError("repolish answer invalid")
    raise ValueError("repolish answer invalid")


def polish_clips(clips: list[dict], *, template, niche: str | None, config: PolishConfig,
                 max_attempts: int = 3, resume: dict | None = None, http_post=None) -> PolishResult:
    """Rewrite clip prompts via Argon. Raises PolishUnavailable on infra errors."""
    if not config or not config.token:
        raise PolishUnavailable("not_configured", "no token")
    names_allowed = bool(template.raw.get("real_names_allowed"))

    total_seconds = 0.0
    last_account = None
    last_problems: list[str] = []
    prev_answer_text: str | None = None

    start_attempt = 1
    if resume is not None:
        prev_answer_text = resume.get("answer")
        last_problems = list(resume.get("problems", []))
        attempts_used = int(resume.get("attempts_used", 0))
        if attempts_used >= max_attempts:
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
        start_attempt = attempts_used + 1

    for attempt in range(start_attempt, max_attempts + 1):
        prompt = build_instruction(clips, template=template, niche=niche)
        if attempt >= 2 and prev_answer_text is not None:
            feedback_lines = ["Your previous answer failed these automatic checks:"]
            for p in last_problems:
                feedback_lines.append(f"- {p}")
            feedback_lines.append("Fix every problem and return the complete JSON again (all clips), following every rule above.")
            feedback_lines.append("Your previous answer was:")
            feedback_lines.append(prev_answer_text[:20000])
            prompt = prompt + "\n" + "\n".join(feedback_lines)

        try:
            resp = _ask(config, prompt, http_post=http_post, attempt=attempt)
        except PolishUnavailable as exc:
            if prev_answer_text is not None:
                logger.info(
                    "prompt_polish interrupted after attempt %d (%s); round saved for resume",
                    attempt - 1,
                    exc.kind,
                )
                raise PolishInterrupted(
                    exc.kind,
                    exc.detail,
                    exc.retry_after_s,
                    answer=prev_answer_text,
                    problems=last_problems,
                    attempts_used=attempt - 1,
                    account=last_account,
                    seconds=total_seconds,
                ) from exc
            raise

        try:
            rj = resp.json()
        except Exception:
            last_problems = ["answer is not valid JSON in the required shape"]
            prev_answer_text = getattr(resp, "text", "")
            summary = (" | ".join(last_problems))[:600]
            logger.info("prompt_polish attempt %d non-json 200 response problems=1: %s", attempt, summary)
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
            summary = (" | ".join(last_problems))[:600]
            logger.info("prompt_polish attempt %d account=%s seconds=%.1f problems=1: %s", attempt, acct, secs, summary)
            continue

        # Build polished clips
        polished_by_idx = {c["index"]: c for c in parsed["clips"]}
        polished_clips = []
        for orig in clips:
            idx = orig["index"]
            if idx not in polished_by_idx:
                last_problems = [f"clip indexes mismatch: polished missing index {idx}"]
                prev_answer_text = answer_text
                summary = (" | ".join(last_problems))[:600]
                logger.info("prompt_polish attempt %d account=%s seconds=%.1f problems=1: %s", attempt, acct, secs, summary)
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
        problems = check_polished(clips, polished_clips, voice, ambience, names_allowed=names_allowed,
                                  motion=motion_sentence_for(niche))
        if problems:
            summary = (" | ".join(problems))[:600]
            logger.info("prompt_polish attempt %d account=%s seconds=%.1f problems=%d: %s", attempt, acct, secs, len(problems), summary)
        else:
            logger.info("prompt_polish attempt %d account=%s seconds=%.1f problems=0", attempt, acct, secs)

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
