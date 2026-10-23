"""Tests for app.services.prompt_polish — no network, fake http_post."""

import json
from dataclasses import dataclass
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.settings import Settings
from app.services.encryption import encrypt
from app.services.prompt_polish import (
    BRAND_NAMES,
    PROTECTED_PHRASES,
    PolishConfig,
    PolishInterrupted,
    PolishResult,
    PolishUnavailable,
    build_instruction,
    check_polished,
    get_polish_config,
    parse_answer,
    polish_clips,
)

# ── Tiny template stand-in ────────────────────────────────────────────────────

KNEE_TEMPLATE = SimpleNamespace(
    key="health-knee", name="Knee Recovery", clip_seconds=8, raw={}
)

FAN_CAR_TEMPLATE = SimpleNamespace(
    key="fan-car", name="Formula Weekend", clip_seconds=8,
    raw={"real_names_allowed": False},
)

FAN_CAR_NAMES_ALLOWED_TEMPLATE = SimpleNamespace(
    key="fan-car", name="Formula Weekend", clip_seconds=8,
    raw={"real_names_allowed": True},
)

# ── Shared voice / ambience (≥40 chars) ───────────────────────────────────────

VOICE = "A calm, warm male narrator with a steady low-pitched baritone voice, close-mic dry recording."
AMBIENCE = "Soft ambient room tone at a fixed low level with no music and no sound effects underneath."

# ── KNEE fixtures (3 clips, all t2v, health style) ────────────────────────────

KNEE_CLIPS = [
    {
        "index": 1, "mode": "t2v",
        "prompt": (
            'A close-up of a human knee joint bending slowly in a clinical white room. '
            'No text, letters, numbers, labels, logos or watermarks anywhere in the image. No gore. '
            'no sound effects. '
            'The narrator says: "Your knee is a masterpiece of engineering, built to carry you through decades of motion."'
        ),
    },
    {
        "index": 2, "mode": "t2v",
        "prompt": (
            'Side view of the same knee mid-stride on a treadmill, muscles visible under translucent skin. '
            'No text, letters, numbers, labels, logos or watermarks anywhere in the image. No gore. '
            'no sound effects. '
            'The narrator says: "Every step sends forces through cartilage, ligaments and bone in perfect harmony."'
        ),
    },
    {
        "index": 3, "mode": "t2v",
        "prompt": (
            'Wide shot of a person standing up from a chair, knee fully extending. '
            'No text, letters, numbers, labels, logos or watermarks anywhere in the image. No gore. '
            'no sound effects. '
            'The narrator says: "Recovery begins when you understand what your body already knows how to do."'
        ),
    },
]

# ── FAN_CAR fixtures (3 clips: t2v, i2v, i2v, motorsport) ────────────────────

FAN_CAR_CLIPS = [
    {
        "index": 1, "mode": "t2v",
        "prompt": (
            'A vintage open-wheel race car with "FORMULA WEEKEND" painted on the side hurtles down '
            'a sunlit straight. (no F1, FIA, MotoGP, FIM or Dorna logos) no music. no cuts. '
            'The narrator says: "In nineteen seventy-eight, ground effect changed everything."'
        ),
    },
    {
        "index": 2, "mode": "i2v",
        "prompt": (
            'Continue seamlessly from this exact frame. The same car drifts into a sweeping right-hand '
            'corner, "FORMULA WEEKEND" clearly visible on the bodywork. (no F1, FIA, MotoGP, FIM or Dorna logos) '
            'no music. no cuts. '
            'The narrator says: "The car stuck to the tarmac as if glued by invisible hands."'
        ),
    },
    {
        "index": 3, "mode": "i2v",
        "prompt": (
            'Continue seamlessly from this exact frame. The car crosses the finish line trailing a thin ribbon of '
            'tyre smoke, "FORMULA WEEKEND" shining under floodlights. (no F1, FIA, MotoGP, FIM or Dorna logos) '
            'no music. no cuts. '
            'The narrator says: "Victory belonged to the engineers who dared to look beneath the floor."'
        ),
    },
]


def _good_polished(original_clips, voice=VOICE, ambience=AMBIENCE, motion=""):
    """Build a 'good' polished version: originals + shared voice/ambience (+ motorsport motion rules), under 2900 chars."""
    polished = []
    for c in original_clips:
        prefix = ""
        if c["mode"] == "i2v":
            prefix = "Continue seamlessly from this exact frame. "
            # Strip original prefix to avoid duplication
            p = c["prompt"]
            if p.startswith("Continue seamlessly from this exact frame"):
                p = p[len("Continue seamlessly from this exact frame"):].lstrip(". ")
        else:
            p = c["prompt"]
        prompt = f"{prefix}{voice} {ambience} {motion + ' ' if motion else ''}{p}"
        assert len(prompt) <= 2900, f"good polished clip {c['index']} is {len(prompt)} chars"
        polished.append({"index": c["index"], "mode": c["mode"], "prompt": prompt})
    return polished


def _make_answer(polished_clips, voice=VOICE, ambience=AMBIENCE, changes=None):
    """Build a JSON answer dict."""
    return {
        "clips": [{"index": c["index"], "prompt": c["prompt"]} for c in polished_clips],
        "voice_sentence": voice,
        "ambience_sentence": ambience,
        "changes": changes or ["improved continuity"],
    }


def _fake_200(answer_dict, account="acct-1", seconds=12.5):
    """Return a fake http_post that returns 200 with the given answer."""
    def _post(url, headers=None, json=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"text": json_module.dumps(answer_dict), "account": account,
                          "thoughts_chars": 100, "seconds": seconds},
        )
    import json as json_module
    return _post


def _fake_200_text(text, account="acct-1", seconds=12.5):
    """Return a fake http_post that returns 200 with raw text."""
    def _post(url, headers=None, json=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"text": text, "account": account, "thoughts_chars": 100, "seconds": seconds},
        )
    return _post


def _fake_error(status_code, body):
    """Return a fake http_post returning an error."""
    def _post(url, headers=None, json=None, timeout=None):
        return SimpleNamespace(status_code=status_code, json=lambda: body)
    return _post


CFG = PolishConfig(base_url="http://localhost:8098", token="test-token", model="gemini-argon")


# ═══════════════════════════════════════════════════════════════════════════════
# Test cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestCase1GoodAnswer:
    def test_good_answer_ok(self):
        polished = _good_polished(KNEE_CLIPS)
        answer = _make_answer(polished)
        post = _fake_200(answer)
        result = polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG, http_post=post)
        assert result.ok is True
        assert result.attempts == 1
        assert result.problems == []
        # modes copied from original
        for orig, pol in zip(KNEE_CLIPS, result.clips):
            assert pol["mode"] == orig["mode"]

    def test_good_answer_fan_car(self):
        from app.services.prompt_polish import MOTION_SENTENCES
        polished = _good_polished(FAN_CAR_CLIPS, motion=MOTION_SENTENCES["f1"])
        answer = _make_answer(polished)
        post = _fake_200(answer)
        result = polish_clips(FAN_CAR_CLIPS, template=FAN_CAR_TEMPLATE, niche="f1", config=CFG, http_post=post)
        assert result.ok is True
        assert result.attempts == 1
        assert result.problems == []


class TestCase2RetryWithFeedback:
    def test_quote_changed_then_fixed(self):
        """Voice-over quote changed by one word → problem; second good → ok, attempts 2."""
        polished_bad = _good_polished(KNEE_CLIPS)
        # Change one quote word in clip 1
        polished_bad[0] = {
            **polished_bad[0],
            "prompt": polished_bad[0]["prompt"].replace("masterpiece of engineering", "masterpiece of design"),
        }
        polished_good = _good_polished(KNEE_CLIPS)
        answer_bad = _make_answer(polished_bad)
        answer_good = _make_answer(polished_good)

        call_count = [0]

        def _post(url, headers=None, json=None, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"text": json_mod.dumps(answer_bad), "account": "a1", "thoughts_chars": 10, "seconds": 5.0},
                )
            else:
                # Second call should contain feedback
                body_prompt = json["prompt"]
                assert "Your previous answer failed these automatic checks" in body_prompt
                assert "original quoted text missing" in body_prompt
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"text": json_mod.dumps(answer_good), "account": "a2", "thoughts_chars": 10, "seconds": 5.0},
                )

        import json as json_mod
        result = polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG, http_post=_post)
        assert result.ok is True
        assert result.attempts == 2


class TestCase3ProtectedPhraseDropped:
    def test_knee_drops_no_sound_effects(self):
        polished = _good_polished(KNEE_CLIPS)
        # Remove "no sound effects" from clip 2
        polished[1] = {**polished[1], "prompt": polished[1]["prompt"].replace("no sound effects", "")}
        problems = check_polished(KNEE_CLIPS, polished, VOICE, AMBIENCE)
        assert any("no sound effects" in p for p in problems)


class TestCase4ExtraQuotedText:
    def test_extra_quote_added(self):
        polished = _good_polished(KNEE_CLIPS)
        polished[0] = {**polished[0], "prompt": polished[0]["prompt"] + ' "Extra brand text" appears.'}
        problems = check_polished(KNEE_CLIPS, polished, VOICE, AMBIENCE)
        assert any("extra quoted text" in p.lower() for p in problems)


class TestCase5VoiceMissing:
    def test_voice_missing_clip2(self):
        polished = _good_polished(KNEE_CLIPS)
        # Remove voice sentence from clip 2
        polished[1] = {**polished[1], "prompt": polished[1]["prompt"].replace(VOICE, "")}
        problems = check_polished(KNEE_CLIPS, polished, VOICE, AMBIENCE)
        assert any("clip 2" in p and "voice_sentence" in p for p in problems)


class TestCase6I2vPrefix:
    def test_i2v_no_prefix(self):
        polished = _good_polished(FAN_CAR_CLIPS)
        # Remove i2v prefix from clip 2
        polished[1] = {**polished[1], "prompt": "The car drifts into a corner. " + VOICE + " " + AMBIENCE + ' "FORMULA WEEKEND" (no F1, FIA, MotoGP, FIM or Dorna logos) no music. no cuts. The narrator says: "The car stuck to the tarmac as if glued by invisible hands."'}
        problems = check_polished(FAN_CAR_CLIPS, polished, VOICE, AMBIENCE)
        assert any("i2v" in p and "Continue seamlessly" in p for p in problems)


class TestCase7Length:
    def test_2901_chars_problem(self):
        polished = _good_polished(KNEE_CLIPS)
        # Pad clip 1 to 2901 chars
        current = len(polished[0]["prompt"])
        pad = 2901 - current
        if pad > 0:
            polished[0] = {**polished[0], "prompt": polished[0]["prompt"] + " " + "x" * (pad - 1)}
        assert len(polished[0]["prompt"]) == 2901
        problems = check_polished(KNEE_CLIPS, polished, VOICE, AMBIENCE)
        assert any("2900" in p or "2901" in p for p in problems)

    def test_2900_chars_fine(self):
        polished = _good_polished(KNEE_CLIPS)
        current = len(polished[0]["prompt"])
        pad = 2900 - current
        if pad > 0:
            polished[0] = {**polished[0], "prompt": polished[0]["prompt"] + " " + "x" * (pad - 1)}
        elif pad < 0:
            polished[0] = {**polished[0], "prompt": polished[0]["prompt"][:2900]}
        assert len(polished[0]["prompt"]) == 2900
        # Only check length-related problems
        problems = check_polished(KNEE_CLIPS, polished, VOICE, AMBIENCE)
        length_problems = [p for p in problems if "2900" in p or "exceeds" in p]
        assert length_problems == []


class TestCase8DoubledWords:
    def test_the_the(self):
        polished = _good_polished(KNEE_CLIPS)
        polished[0] = {**polished[0], "prompt": "the the " + polished[0]["prompt"]}
        problems = check_polished(KNEE_CLIPS, polished, VOICE, AMBIENCE)
        assert any("doubled" in p.lower() for p in problems)


class TestCase9BrandNames:
    def test_ferrari_added_to_fan_car(self):
        polished = _good_polished(FAN_CAR_CLIPS)
        polished[0] = {**polished[0], "prompt": polished[0]["prompt"] + " A Ferrari leads the pack."}
        problems = check_polished(FAN_CAR_CLIPS, polished, VOICE, AMBIENCE, names_allowed=False)
        assert any("Ferrari" in p for p in problems)

    def test_ferrari_with_names_allowed(self):
        polished = _good_polished(FAN_CAR_CLIPS)
        polished[0] = {**polished[0], "prompt": polished[0]["prompt"] + " A Ferrari leads the pack."}
        problems = check_polished(FAN_CAR_CLIPS, polished, VOICE, AMBIENCE, names_allowed=True)
        brand_problems = [p for p in problems if "brand" in p.lower() or "Ferrari" in p]
        assert brand_problems == []

    def test_ferrari_already_in_original(self):
        """Original already containing Ferrari → fine."""
        orig = [
            {**FAN_CAR_CLIPS[0], "prompt": FAN_CAR_CLIPS[0]["prompt"] + " A Ferrari engine roars."},
            FAN_CAR_CLIPS[1],
            FAN_CAR_CLIPS[2],
        ]
        polished = _good_polished(orig)
        polished[0] = {**polished[0], "prompt": polished[0]["prompt"] + " A Ferrari engine roars."}
        problems = check_polished(orig, polished, VOICE, AMBIENCE, names_allowed=False)
        brand_problems = [p for p in problems if "Ferrari" in p]
        assert brand_problems == []


class TestCase10Parsing:
    def test_json_fences_parsed(self):
        polished = _good_polished(KNEE_CLIPS)
        answer = _make_answer(polished)
        fenced = "```json\n" + json.dumps(answer) + "\n```"
        parsed = parse_answer(fenced)
        assert parsed["voice_sentence"] == VOICE

    def test_non_json_three_times_fail(self):
        post = _fake_200_text("This is not JSON at all.")
        result = polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG,
                              http_post=post, max_attempts=3)
        assert result.ok is False
        assert result.attempts == 3

    def test_200_json_raises_value_error(self):
        def _post(url, headers=None, json=None, timeout=None):
            def _raise():
                raise ValueError("invalid json in body")
            return SimpleNamespace(status_code=200, json=_raise, text="<html>502 Bad Gateway</html>")
        result = polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG,
                              http_post=_post, max_attempts=2)
        assert result.ok is False
        assert result.attempts == 2
        assert "answer is not valid JSON in the required shape" in result.problems

    def test_seconds_none_defaults_to_zero(self):
        polished = _good_polished(KNEE_CLIPS)
        answer = _make_answer(polished)
        post = _fake_200(answer, seconds=None)
        result = polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG, http_post=post)
        assert result.ok is True
        assert result.seconds == 0.0


class TestCase11Errors:
    def test_503_no_account_ready(self):
        post = _fake_error(503, {"error": "no_account_ready", "retry_after_s": 60, "reasons": {}})
        with pytest.raises(PolishUnavailable) as exc_info:
            polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG, http_post=post)
        assert exc_info.value.kind == "no_account_ready"
        assert exc_info.value.retry_after_s == 60

    def test_503_proxy_down(self):
        post = _fake_error(503, {"error": "proxy_down"})
        with pytest.raises(PolishUnavailable) as exc_info:
            polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG, http_post=post)
        assert exc_info.value.kind == "proxy_down"

    def test_401_unauthorized(self):
        post = _fake_error(401, {"error": "unauthorized"})
        with pytest.raises(PolishUnavailable) as exc_info:
            polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG, http_post=post)
        assert exc_info.value.kind == "unauthorized"

    def test_connection_error_unreachable(self):
        def _post(url, headers=None, json=None, timeout=None):
            raise httpx.ConnectError("Connection refused")
        with pytest.raises(PolishUnavailable) as exc_info:
            polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG, http_post=_post)
        assert exc_info.value.kind == "unreachable"

    def test_non_200_non_json_raises_upstream_http_status(self):
        def _post(url, headers=None, json=None, timeout=None):
            def _raise():
                raise ValueError("non-json html")
            return SimpleNamespace(status_code=502, json=_raise)
        with pytest.raises(PolishUnavailable) as exc_info:
            polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG, http_post=_post)
        assert exc_info.value.kind == "upstream"
        assert "HTTP 502" in exc_info.value.detail


class TestCase12GetPolishConfig:
    @pytest.fixture
    def _stable_fernet(self):
        """Pin a stable Fernet key so encrypt/decrypt use the same key."""
        from cryptography.fernet import Fernet
        from app.config import get_settings
        cfg = get_settings()
        old_key = cfg.fernet_key
        cfg.fernet_key = Fernet.generate_key().decode()
        yield
        cfg.fernet_key = old_key

    @pytest.fixture
    def test_db(self, _stable_fernet):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Settings.__table__.create(bind=engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        row = Settings(
            id=1,
            crawl_interval_minutes=30,
            max_post_age_days=2,
            ai_provider_primary="gemini",
            ai_provider_fallback="groq",
            ai_fallback_after_failures=3,
            ai_fallback_reset_after_minutes=15,
        )
        session.add(row)
        session.commit()
        yield session
        session.close()

    def test_no_token_returns_none(self, test_db):
        cfg = get_polish_config(test_db)
        assert cfg is None

    def test_token_set_returns_config(self, test_db):
        row = test_db.query(Settings).first()
        token = "my-secret-token"
        row.prompt_polish_token_encrypted = encrypt(token)
        test_db.commit()
        cfg = get_polish_config(test_db)
        assert cfg is not None
        assert cfg.token == token
        assert cfg.base_url == "http://172.17.0.1:8098"
        assert cfg.model == "gemini-argon"


class TestCase13BuildInstruction:
    def test_contains_template_info(self):
        instr = build_instruction(FAN_CAR_CLIPS, template=FAN_CAR_TEMPLATE, niche="f1")
        assert "Formula Weekend" in instr
        assert "8 s each" in instr
        # All clip prompts present
        for c in FAN_CAR_CLIPS:
            assert c["prompt"] in instr
        # i2v instruction
        assert "Continue seamlessly from this exact frame" in instr
        # Clip seconds
        assert str(FAN_CAR_TEMPLATE.clip_seconds) in instr

    def test_niche_none_becomes_general(self):
        instr = build_instruction(KNEE_CLIPS, template=KNEE_TEMPLATE, niche=None)
        assert "general" in instr

    def test_physics_and_visual_style_rules(self):
        instr = build_instruction(FAN_CAR_CLIPS, template=FAN_CAR_TEMPLATE, niche="f1")
        assert "Everything real in the scene obeys real physics" in instr
        assert "Deliberate visual styles of the template are NOT logic errors" in instr


def test_generate_timeout_is_900_s():
    from app.services.prompt_polish import GENERATE_TIMEOUT_S, _post_generate
    assert GENERATE_TIMEOUT_S == 900

    captured = {}
    def mock_post(url, headers, json, timeout):
        captured["timeout"] = timeout
        return SimpleNamespace(status_code=200, json=lambda: {"text": "{}"})

    cfg = PolishConfig(base_url="http://172.17.0.1:8098", token="tok", model="gemini-argon")
    _post_generate(cfg, "prompt", http_post=mock_post)
    assert captured["timeout"] == 900



# ── Motorsport motion rules (owner 2026-10-23) ──────────────────────────────────────────────────────────────
from app.services.prompt_polish import MOTION_SENTENCES, motion_sentence_for


def _moto_clips(prompt_text: str) -> list[dict]:
    return [{"index": 1, "mode": "t2v", "prompt": prompt_text}]


def test_motion_sentence_per_niche():
    assert motion_sentence_for("motogp") == MOTION_SENTENCES["motogp"]
    assert motion_sentence_for("F1") == MOTION_SENTENCES["f1"]
    assert motion_sentence_for("health") is None
    assert motion_sentence_for(None) is None


def test_build_instruction_adds_motorsport_rules_only_for_motorsport():
    class T:
        name = "motorcycle commercial"
        clip_seconds = 10
        raw = {}
    clips = _moto_clips("A motorcycle on a circuit.")
    moto = build_instruction(clips, template=T(), niche="motogp")
    assert MOTION_SENTENCES["motogp"] in moto
    assert "livery panel by panel" in moto
    health = build_instruction(clips, template=T(), niche="health")
    assert "Motion:" not in health
    assert "livery panel by panel" not in health


def test_check_polished_requires_motion_sentence_and_exact_colours():
    voice = "Voice-over: a calm narrator with a warm low voice, close-mic dry recording, says:"
    amb = "Background: a steady low engine note and distant crowd, no music and no other sound."
    motion = MOTION_SENTENCES["motogp"]
    orig = _moto_clips("A motorcycle.")
    good = _moto_clips(f"A motorcycle in a white and navy livery. {motion} {amb} {voice}")
    assert check_polished(orig, good, voice, amb, motion=motion) == []
    missing = _moto_clips(f"A motorcycle in a white livery. {amb} {voice}")
    assert any("motion rules sentence missing" in p for p in check_polished(orig, missing, voice, amb, motion=motion))
    vague = _moto_clips(f"Plain solid-colour number boards. {motion} {amb} {voice}")
    assert any("vague colour word" in p for p in check_polished(orig, vague, voice, amb, motion=motion))
    # Non-motorsport niches are not affected by the vague-colour rule.
    assert check_polished(orig, vague, voice, amb, motion=None) == []


def test_check_polished_requires_the_engine_type_in_the_ambience():
    voice = "Voice-over: a calm narrator with a warm low voice, close-mic dry recording, says:"
    motion = MOTION_SENTENCES["motogp"]
    orig = _moto_clips("A 1993 two-stroke V4 Grand Prix motorcycle.")
    amb_ok = "Background: the crisp ringing two-stroke V4 engine at a fixed low level, no music."
    amb_bad = "Background: a deep four-stroke roar at a fixed low level under the voice, no music."
    good = _moto_clips(f"A bike in a white livery. {motion} {amb_ok} {voice}")
    bad = _moto_clips(f"A bike in a white livery. {motion} {amb_bad} {voice}")
    assert check_polished(orig, good, voice, amb_ok, motion=motion) == []
    assert any("two-stroke engine sound" in p for p in check_polished(orig, bad, voice, amb_bad, motion=motion))
    # No engine type named in the original -> no engine check.
    plain = _moto_clips("A Grand Prix motorcycle.")
    assert check_polished(plain, bad, voice, amb_bad, motion=motion) == []


def test_motorsport_instruction_mentions_racing_speed_and_engine_sound():
    class T:
        name = "motorcycle commercial"
        clip_seconds = 10
        raw = {}
    instr = build_instruction(_moto_clips("A motorcycle."), template=T(), niche="motogp")
    assert "real racing speed" in instr and "two-stroke" in instr and "same person" in instr
    assert "full racing speed" in MOTION_SENTENCES["motogp"]


# ── PolishInterrupted and resume tests (TASK-R) ────────────────────────────────

class TestCase14PolishInterruptedAfterOneAttempt:
    """(a) attempt 1 answers with a problem, attempt 2 raises PolishUnavailable -> PolishInterrupted."""

    def test_interrupted_carries_answer_and_problems(self):
        polished_bad = _good_polished(KNEE_CLIPS)
        # Break a quote so check_polished reports a problem
        polished_bad[0] = {
            **polished_bad[0],
            "prompt": polished_bad[0]["prompt"].replace("masterpiece of engineering", "masterpiece of design"),
        }
        answer_bad = _make_answer(polished_bad)

        call_count = [0]

        def _post(url, headers=None, json=None, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"text": json_mod.dumps(answer_bad), "account": "a1",
                                  "thoughts_chars": 10, "seconds": 5.0},
                )
            # Attempt 2: account unavailable
            return SimpleNamespace(
                status_code=503,
                json=lambda: {"error": "no_account_ready", "retry_after_s": 300, "reasons": {}},
            )

        import json as json_mod
        with pytest.raises(PolishInterrupted) as exc_info:
            polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG, http_post=_post)
        exc = exc_info.value
        assert isinstance(exc, PolishUnavailable)
        assert exc.attempts_used == 1
        assert exc.answer == json_mod.dumps(answer_bad)
        assert len(exc.problems) > 0
        assert exc.account == "a1"
        assert exc.seconds > 0
        assert exc.kind == "no_account_ready"


class TestCase15PlainUnavailableOnFirstAttempt:
    """(b) attempt 1 itself unavailable -> plain PolishUnavailable, NOT PolishInterrupted."""

    def test_first_attempt_unavailable(self):
        post = _fake_error(503, {"error": "no_account_ready", "retry_after_s": 60, "reasons": {}})
        with pytest.raises(PolishUnavailable) as exc_info:
            polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG, http_post=post)
        assert not isinstance(exc_info.value, PolishInterrupted)
        assert exc_info.value.kind == "no_account_ready"


class TestCase16ResumeDict:
    """(c) resume={attempts_used:1} -> first call contains feedback block, at most 2 calls made."""

    def test_resume_starts_with_feedback(self):
        polished_good = _good_polished(KNEE_CLIPS)
        answer_good = _make_answer(polished_good)

        call_count = [0]
        captured_prompts = []

        def _post(url, headers=None, json=None, timeout=None):
            call_count[0] += 1
            captured_prompts.append(json["prompt"])
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"text": json_mod.dumps(answer_good), "account": "a2",
                              "thoughts_chars": 10, "seconds": 3.0},
            )

        import json as json_mod
        result = polish_clips(
            KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG, http_post=_post,
            max_attempts=3,
            resume={"answer": "prev answer text", "problems": ["clip 1: motion missing"], "attempts_used": 1},
        )
        assert result.ok is True
        # First call should already be attempt 2 with feedback
        assert "Your previous answer failed these automatic checks" in captured_prompts[0]
        assert "clip 1: motion missing" in captured_prompts[0]
        # At most 2 calls (attempts 2 and 3, since 1 was already used)
        assert call_count[0] <= 2


class TestCase17ResumeExhausted:
    """(d) attempts_used=3 (>= max_attempts) -> no call, failed result."""

    def test_resume_exhausted(self):
        call_count = [0]

        def _post(url, headers=None, json=None, timeout=None):
            call_count[0] += 1
            return SimpleNamespace(status_code=200, json=lambda: {"text": "{}"})

        result = polish_clips(
            KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG, http_post=_post,
            max_attempts=3,
            resume={"answer": "prev", "problems": ["bad"], "attempts_used": 3},
        )
        assert result.ok is False
        assert call_count[0] == 0
        assert result.problems == ["bad"]


class TestCase18AttemptLogContainsProblemText:
    """(e) the attempt log line contains the problem text."""

    def test_log_contains_problems(self, caplog):
        polished_bad = _good_polished(KNEE_CLIPS)
        polished_bad[0] = {
            **polished_bad[0],
            "prompt": polished_bad[0]["prompt"].replace("masterpiece of engineering", "masterpiece of design"),
        }
        answer_bad = _make_answer(polished_bad)
        # Return bad answer all 2 attempts
        post = _fake_200(answer_bad)

        import logging
        with caplog.at_level(logging.INFO, logger="app.services.prompt_polish"):
            result = polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG,
                                  http_post=post, max_attempts=2)

        assert result.ok is False
        # Check that at least one log line contains the problem text
        log_lines = [r.message for r in caplog.records if "prompt_polish attempt" in r.message]
        assert len(log_lines) >= 1
        assert any("original quoted text missing" in line for line in log_lines)


class TestCase19TransportErrorHandling:
    """Change 3: httpx.ReadError and other TransportErrors -> PolishUnavailable / PolishInterrupted."""

    def test_read_error_raises_unreachable(self):
        def _post(url, headers=None, json=None, timeout=None):
            raise httpx.ReadError("reset")

        with pytest.raises(PolishUnavailable) as exc_info:
            polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG, http_post=_post)
        assert exc_info.value.kind == "unreachable"
        assert "reset" in exc_info.value.detail

    def test_read_error_on_attempt_2_raises_interrupted(self):
        polished_bad = _good_polished(KNEE_CLIPS)
        polished_bad[0] = {
            **polished_bad[0],
            "prompt": polished_bad[0]["prompt"].replace("masterpiece of engineering", "masterpiece of design"),
        }
        answer_bad = _make_answer(polished_bad)

        import json as json_mod
        call_count = [0]

        def _post(url, headers=None, json=None, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"text": json_mod.dumps(answer_bad), "account": "a1",
                                  "thoughts_chars": 10, "seconds": 5.0},
                )
            raise httpx.ReadError("[Errno 104] Connection reset by peer")

        with pytest.raises(PolishInterrupted) as exc_info:
            polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health", config=CFG, http_post=_post)
        exc = exc_info.value
        assert exc.kind == "unreachable"
        assert exc.attempts_used == 1
        assert exc.answer == json_mod.dumps(answer_bad)
        assert len(exc.problems) > 0
        assert exc.account == "a1"


# ═══════════════════════════════════════════════════════════════════════════════
# ChatGPT fallback tests (TASK-P)
# ═══════════════════════════════════════════════════════════════════════════════

from app.services.gptcli_client import GptcliConfig, generate_text

GPTCLI_CFG = GptcliConfig(
    enabled=True,
    base_url="http://localhost:8099",
    token="test-gptcli-token",
    fanpage_ids=(),
    thread="polish",
    size="1080x1350",
    day_start="05:30",
)
CFG_WITH_CHATGPT = PolishConfig(
    base_url="http://localhost:8098",
    token="test-token",
    model="gemini-argon",
    chatgpt=GPTCLI_CFG,
)


class TestCaseChatGPTFallback:
    def test_argon_503_chatgpt_200_valid_answer(self):
        """Argon 503 no_account_ready + ChatGPT 200 valid answer -> result.ok, result.account == 'chatgpt:utama'."""
        polished = _good_polished(KNEE_CLIPS)
        answer = _make_answer(polished)

        def _post(url, headers=None, json=None, timeout=None):
            if "/v1/text" in url:
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {
                        "text": json_mod.dumps(answer),
                        "gptcli": {"profile": "utama", "duration_s": 4.5, "request_id": "polish-123"},
                    },
                )
            return SimpleNamespace(
                status_code=503,
                json=lambda: {"error": "no_account_ready", "retry_after_s": 300},
            )

        import json as json_mod
        result = polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health",
                              config=CFG_WITH_CHATGPT, http_post=_post)
        assert result.ok is True
        assert result.account == "chatgpt:utama"
        assert result.seconds == 4.5
        assert len(result.clips) == len(KNEE_CLIPS)

    def test_argon_unavailable_chatgpt_429(self):
        """Argon unavailable + ChatGPT 429 -> raises PolishUnavailable with Argon kind and 'chatgpt: chatgpt_busy' in detail."""
        def _post(url, headers=None, json=None, timeout=None):
            if "/v1/text" in url:
                return SimpleNamespace(
                    status_code=429,
                    headers={"Retry-After": "60"},
                    json=lambda: {"error": {"type": "quota", "message": "rate limited"}},
                )
            return SimpleNamespace(
                status_code=503,
                json=lambda: {"error": "no_account_ready", "retry_after_s": 120},
            )

        with pytest.raises(PolishUnavailable) as exc_info:
            polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health",
                         config=CFG_WITH_CHATGPT, http_post=_post)
        exc = exc_info.value
        assert exc.kind == "no_account_ready"
        assert "chatgpt: chatgpt_busy" in exc.detail

    def test_argon_unavailable_after_attempt_1_chatgpt_answers_attempt_2(self):
        """Argon unavailable after one answered attempt + ChatGPT answers attempt 2 validly -> ok (resume path not needed)."""
        polished_bad = _good_polished(KNEE_CLIPS)
        polished_bad[0] = {
            **polished_bad[0],
            "prompt": polished_bad[0]["prompt"].replace("masterpiece of engineering", "masterpiece of design"),
        }
        answer_bad = _make_answer(polished_bad)
        answer_good = _make_answer(_good_polished(KNEE_CLIPS))

        call_count = {"argon": 0, "chatgpt": 0}
        import json as json_mod

        def _post(url, headers=None, json=None, timeout=None):
            if "/v1/text" in url:
                call_count["chatgpt"] += 1
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {
                        "text": json_mod.dumps(answer_good),
                        "gptcli": {"profile": "utama", "duration_s": 3.0},
                    },
                )
            call_count["argon"] += 1
            if call_count["argon"] == 1:
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"text": json_mod.dumps(answer_bad), "account": "a1", "seconds": 2.0},
                )
            return SimpleNamespace(
                status_code=503,
                json=lambda: {"error": "no_account_ready", "retry_after_s": 60},
            )

        result = polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health",
                              config=CFG_WITH_CHATGPT, http_post=_post, max_attempts=3)
        assert result.ok is True
        assert result.attempts == 2
        assert result.account == "chatgpt:utama"
        assert call_count["chatgpt"] == 1

    def test_chatgpt_answer_fails_check_polished_not_accepted(self):
        """ChatGPT answer that fails check_polished is NOT accepted (problems reported; same as Argon)."""
        polished_bad = _good_polished(KNEE_CLIPS)
        polished_bad[0] = {
            **polished_bad[0],
            "prompt": polished_bad[0]["prompt"].replace("masterpiece of engineering", "masterpiece of design"),
        }
        answer_bad = _make_answer(polished_bad)
        import json as json_mod

        def _post(url, headers=None, json=None, timeout=None):
            if "/v1/text" in url:
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {
                        "text": json_mod.dumps(answer_bad),
                        "gptcli": {"profile": "utama", "duration_s": 3.0},
                    },
                )
            return SimpleNamespace(
                status_code=503,
                json=lambda: {"error": "no_account_ready"},
            )

        result = polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health",
                              config=CFG_WITH_CHATGPT, http_post=_post, max_attempts=2)
        assert result.ok is False
        assert result.attempts == 2
        assert len(result.problems) > 0
        assert any("original quoted text missing" in p for p in result.problems)

    def test_config_chatgpt_none_behaviour_as_before(self):
        """config.chatgpt None -> behaviour exactly as before (existing tests keep passing)."""
        called_urls = []

        def _post(url, headers=None, json=None, timeout=None):
            called_urls.append(url)
            return SimpleNamespace(
                status_code=503,
                json=lambda: {"error": "no_account_ready"},
            )

        with pytest.raises(PolishUnavailable) as exc_info:
            polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health",
                         config=CFG, http_post=_post)
        assert exc_info.value.kind == "no_account_ready"
        assert "chatgpt:" not in exc_info.value.detail
        assert not any("/v1/text" in u for u in called_urls)

    def test_gptcli_unavailable_until_set_chatgpt_not_called(self, monkeypatch):
        """gptcli_unavailable_until() set (monkeypatch) -> ChatGPT not called."""
        from datetime import datetime, timedelta, timezone
        monkeypatch.setattr(
            "app.services.prompt_polish.gptcli_unavailable_until",
            lambda: datetime.now(timezone.utc) + timedelta(minutes=15),
        )

        called_urls = []

        def _post(url, headers=None, json=None, timeout=None):
            called_urls.append(url)
            return SimpleNamespace(
                status_code=503,
                json=lambda: {"error": "no_account_ready"},
            )

        with pytest.raises(PolishUnavailable) as exc_info:
            polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health",
                         config=CFG_WITH_CHATGPT, http_post=_post)
        assert exc_info.value.kind == "no_account_ready"
        assert not any("/v1/text" in u for u in called_urls)

    def test_token_never_appears_in_logs_or_exception_text(self, caplog):
        """token never appears in logs/exception text (use a token like 'SECRET123' and assert not in str(exc) / caplog.text)."""
        import logging
        cfg_secret = PolishConfig(
            base_url="http://localhost:8098",
            token="ARGON_SECRET",
            model="gemini-argon",
            chatgpt=GptcliConfig(
                enabled=True,
                base_url="http://localhost:8099",
                token="SECRET123",
                fanpage_ids=(),
                thread="polish",
                size="1080x1350",
                day_start="05:30",
            ),
        )

        def _post(url, headers=None, json=None, timeout=None):
            if "/v1/text" in url:
                return SimpleNamespace(
                    status_code=500,
                    json=lambda: {"error": {"type": "server_error", "message": "Bearer SECRET123 rejected by upstream"}},
                )
            return SimpleNamespace(
                status_code=503,
                json=lambda: {"error": "no_account_ready"},
            )

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(PolishUnavailable) as exc_info:
                polish_clips(KNEE_CLIPS, template=KNEE_TEMPLATE, niche="health",
                             config=cfg_secret, http_post=_post)
        exc = exc_info.value
        assert "SECRET123" not in str(exc)
        assert "SECRET123" not in exc.detail
        assert "SECRET123" not in caplog.text

    def test_get_polish_config_with_chatgpt(self, monkeypatch):
        from cryptography.fernet import Fernet
        from app.config import get_settings
        monkeypatch.setattr(get_settings(), "fernet_key", Fernet.generate_key().decode())
        engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
        Settings.__table__.create(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            row = Settings(
                id=1,
                prompt_polish_token_encrypted=encrypt("argon-tok"),
                prompt_polish_base_url="http://argon:8098",
                prompt_polish_model="gemini-argon",
                gptcli_token_encrypted=encrypt("gpt-tok"),
                gptcli_base_url="http://gpt:8099",
            )
            db.add(row)
            db.commit()

            cfg = get_polish_config(db)
            assert cfg is not None
            assert cfg.token == "argon-tok"
            assert cfg.chatgpt is not None
            assert cfg.chatgpt.token == "gpt-tok"
            assert cfg.chatgpt.base_url == "http://gpt:8099"
        finally:
            db.close()
            Settings.__table__.drop(engine)

    def test_generate_text_status_code_mappings(self):
        """Test status code and error kind mappings in generate_text."""
        for status, err_type, expected_kind in [
            (401, "unauthorized", "chatgpt_unauthorized"),
            (404, "disabled", "chatgpt_disabled"),
            (429, "quota", "chatgpt_busy"),
            (429, "text_cap_reached", "chatgpt_busy"),
            (503, "sleeping", "chatgpt_sleeping"),
            (502, "refused", "chatgpt_refused"),
            (500, "server_error", "chatgpt_failed"),
        ]:
            def _post(url, headers=None, json=None, timeout=None):
                return SimpleNamespace(
                    status_code=status,
                    json=lambda: {"error": {"type": err_type, "message": f"err {err_type}"}},
                )

            with pytest.raises(PolishUnavailable) as exc_info:
                generate_text(GPTCLI_CFG, "test prompt", http_post=_post)
            assert exc_info.value.kind == expected_kind

    def test_generate_text_transport_error_mapped(self):
        def _post(url, headers=None, json=None, timeout=None):
            raise httpx.ConnectError("connection refused")

        with pytest.raises(PolishUnavailable) as exc_info:
            generate_text(GPTCLI_CFG, "test prompt", http_post=_post)
        assert exc_info.value.kind == "chatgpt_unreachable"
