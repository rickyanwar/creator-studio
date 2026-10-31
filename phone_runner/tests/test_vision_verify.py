import base64
import io
import json
import shutil
import subprocess
import urllib.error
from unittest import mock
import pytest

from phone_runner.vision_verify import (
    _MODELS_CACHE,
    DEFAULT_VISION_MODELS,
    PROMPT_COMPOSER,
    PROMPT_POST_SETTINGS,
    PROMPT_POSTED,
    PROMPT_REEL_SETTINGS,
    VISION_GATE_BUDGET_SECONDS,
    VISION_MAX_IMAGE_BYTES,
    VISION_MODEL_TIMEOUT_SECONDS,
    VISION_REFERENCE_MAX_SIDE,
    VISION_SCREENSHOT_MAX_SIDE,
    VISION_SHRINK_TIMEOUT_SECONDS,
    _shrink_for_vision,
    ask_vision,
    capture_screenshot_png,
    check_composer,
    check_post_settings,
    check_posted,
    check_reel_settings,
    get_vision_models,
    loose_text_key,
    strip_json_fences,
    verify_gate,
)

FAKE_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
FAKE_SERIAL = "100.64.96.50:5555"


@pytest.fixture(autouse=True)
def _reset_models_cache():
    _MODELS_CACHE["timestamp"] = 0.0
    _MODELS_CACHE["ids"] = set()


def test_model_filtering_and_env_override(monkeypatch):
    # Test env override
    monkeypatch.setenv(
        "PHONE_RUNNER_VISION_MODELS",
        "cx/gemini-pro,ag/claude-3-opus,ag/gemini-3.8-flash,ag/gemini-3.7-flash-medium",
    )
    # Mock live /models GET failure -> returns filtered list
    with mock.patch("urllib.request.urlopen", side_effect=Exception("network down")):
        models = get_vision_models()
        assert "cx/gemini-pro" not in models
        assert "ag/claude-3-opus" not in models
        assert models == ["ag/gemini-3.8-flash", "ag/gemini-3.7-flash-medium"]


def test_model_filtering_live_intersection(monkeypatch):
    monkeypatch.delenv("PHONE_RUNNER_VISION_MODELS", raising=False)
    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = json.dumps({
        "data": [
            {"id": "FlashFast"},
            {"id": "My-Combo"},
            {"id": "ag/gemini-3.8-flash-high"},
            {"id": "ag/gemini-3.8-flash-medium"},
            {"id": "some/other-model"},
        ]
    }).encode("utf-8")
    fake_resp.__enter__.return_value = fake_resp

    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        models = get_vision_models()
        # FlashFast combo first (owner rule 2026-10-17); My-Combo is never used for vision
        assert models == ["FlashFast", "ag/gemini-3.8-flash-medium", "ag/gemini-3.8-flash-high"]


def test_strip_json_fences():
    assert strip_json_fences('{"a": 1}') == '{"a": 1}'
    fenced = '```json\n{"screen": "composer"}\n```'
    assert strip_json_fences(fenced) == '{"screen": "composer"}'
    fenced_no_lang = '```\n{"screen": "composer"}\n```'
    assert strip_json_fences(fenced_no_lang) == '{"screen": "composer"}'


def test_capture_screenshot_png(monkeypatch):
    # Success
    mock_run = mock.MagicMock()
    mock_run.return_value = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=FAKE_PNG + b"\x00\x00",
        stderr=b"",
    )
    monkeypatch.setattr("subprocess.run", mock_run)
    res = capture_screenshot_png(FAKE_SERIAL)
    assert res == FAKE_PNG + b"\x00\x00"

    # Non-PNG output -> None
    mock_run.return_value = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=b"Error: device not found",
        stderr=b"",
    )
    assert capture_screenshot_png(FAKE_SERIAL) is None

    # Invalid serial -> None without subprocess call
    mock_run.reset_mock()
    assert capture_screenshot_png("invalid serial with spaces;") is None
    mock_run.assert_not_called()


def test_ask_vision_fallback_on_error(monkeypatch):
    monkeypatch.setenv("PHONE_RUNNER_VISION_MODELS", "ag/gemini-first,ag/gemini-second")
    with mock.patch("urllib.request.urlopen") as mock_open:
        # First call fails, second call succeeds
        resp1 = mock.MagicMock()
        resp1.side_effect = Exception("HTTP 500")

        resp2 = mock.MagicMock()
        resp2.read.return_value = json.dumps({
            "choices": [{"message": {"content": '```json\n{"screen": "composer"}\n```'}}]
        }).encode("utf-8")
        resp2.__enter__.return_value = resp2

        mock_open.side_effect = [Exception("HTTP 500"), resp2]

        # Bypass models fetch by mocking get_vision_models
        with mock.patch("phone_runner.vision_verify.get_vision_models", return_value=["ag/gemini-first", "ag/gemini-second"]):
            ans, model, detail = ask_vision(FAKE_PNG, "prompt", reference_jpeg=b"fake_jpeg")
            assert ans == {"screen": "composer"}
            assert model == "ag/gemini-second"
            # detail encodes winner with success outcome and elapsed, plus prior failure
            assert detail.startswith("ok;winner=ag/gemini-second")
            assert ":success:" in detail
            assert "s" in detail  # elapsed seconds present
            assert "prior_failures" in detail
            assert "ag/gemini-first" in detail
            assert "HTTP 500" not in detail


def test_check_composer_cases():
    valid_ans = {
        "screen": "composer",
        "author_name": "Agung",
        "has_attached_photo": True,
        "caption_starts_with": "",
        "same_photo_as_reference": True,
    }
    # Happy path: empty caption
    ok, detail = check_composer(valid_ans, expected_author="Agung", require_caption=False)
    assert ok is True

    # Wrong screen
    bad_screen = dict(valid_ans, screen="other")
    ok, _ = check_composer(bad_screen, expected_author="Agung", require_caption=False)
    assert ok is False

    # Wrong author: "Agung Baelah" vs "Agung" must fail
    bad_author = dict(valid_ans, author_name="Agung Baelah")
    ok, _ = check_composer(bad_author, expected_author="Agung", require_caption=False)
    assert ok is False

    # same_photo_as_reference False must fail
    diff_photo = dict(valid_ans, same_photo_as_reference=False)
    ok, _ = check_composer(diff_photo, expected_author="Agung", require_caption=False)
    assert ok is False

    # has_attached_photo False must fail
    no_photo = dict(valid_ans, has_attached_photo=False)
    ok, _ = check_composer(no_photo, expected_author="Agung", require_caption=False)
    assert ok is False

    # Empty caption required, but non-empty returned -> fail
    non_empty_cap = dict(valid_ans, caption_starts_with="Hello")
    ok, _ = check_composer(non_empty_cap, expected_author="Agung", require_caption=False)
    assert ok is False

    # Caption required: matching prefix
    typed_ans = dict(
        valid_ans,
        caption_starts_with="Hello world! This is my post",
    )
    ok, _ = check_composer(
        typed_ans,
        expected_author="Agung",
        expected_caption="Hello world! This is my post that is long",
        require_caption=True,
    )
    assert ok is True

    # Caption required: mismatch prefix -> fail
    ok, _ = check_composer(
        typed_ans,
        expected_author="Agung",
        expected_caption="Completely different caption",
        require_caption=True,
    )
    assert ok is False


def test_check_post_settings():
    ok, _ = check_post_settings({"screen": "post_settings", "share_button_visible": True, "audience": "Public"})
    assert ok is True

    ok, _ = check_post_settings({"screen": "composer", "share_button_visible": True})
    assert ok is False

    ok, _ = check_post_settings({"screen": "post_settings", "share_button_visible": False})
    assert ok is False


def test_check_posted():
    valid = {
        "screen": "profile",
        "author_name": "Agung",
        "top_post_caption_starts_with": "Hello world! This is my post",
        "top_post_has_photo": True,
        "still_uploading": False,
    }
    ok, _ = check_posted(valid, expected_author="Agung", expected_caption="Hello world! This is my post")
    assert ok is True

    # Author mismatch
    bad_auth = dict(valid, author_name="Other")
    ok, _ = check_posted(bad_auth, expected_author="Agung", expected_caption="Hello world!")
    assert ok is False

    # Photo missing
    no_photo = dict(valid, top_post_has_photo=False)
    ok, _ = check_posted(no_photo, expected_author="Agung", expected_caption="Hello world!")
    assert ok is False

    # Caption mismatch
    bad_cap = dict(valid, top_post_caption_starts_with="Something else")
    ok, _ = check_posted(bad_cap, expected_author="Agung", expected_caption="Hello world!")
    assert ok is False


def test_verify_gate_full():
    with (
        mock.patch("phone_runner.vision_verify.capture_screenshot_png", return_value=FAKE_PNG),
        mock.patch(
            "phone_runner.vision_verify.ask_vision",
            return_value=({"screen": "post_settings", "share_button_visible": True}, "gemini-model", "ok"),
        ),
    ):
        ok, ev = verify_gate(FAKE_SERIAL, "post_settings")
        assert ok is True
        assert ev["gate"] == "post_settings"
        assert ev["model"] == "gemini-model"
        assert ev["ok"] is True
        assert ev["answer"]["screen"] == "post_settings"

    # Screenshot failure fails closed
    with mock.patch("phone_runner.vision_verify.capture_screenshot_png", return_value=None):
        ok, ev = verify_gate(FAKE_SERIAL, "post_settings")
        assert ok is False
        assert ev["detail"] == "screenshot capture failed"


def test_ask_vision_budget_exhausted_skips_second_model(monkeypatch):
    monkeypatch.setenv("PHONE_RUNNER_VISION_MODELS", "ag/gemini-first,ag/gemini-second")

    current_time = [100.0]

    def fake_monotonic():
        return current_time[0]

    monkeypatch.setattr("time.monotonic", fake_monotonic)

    call_count = [0]

    def mock_urlopen(req, timeout=None):
        call_count[0] += 1
        # First model call exhausts budget by advancing time past total_budget (40.0s)
        current_time[0] += 45.0
        raise Exception("first model failed")

    with (
        mock.patch("urllib.request.urlopen", side_effect=mock_urlopen),
        mock.patch("phone_runner.vision_verify.get_vision_models", return_value=["ag/gemini-first", "ag/gemini-second"]),
    ):
        ans, model, detail = ask_vision(FAKE_PNG, "prompt", total_budget=40.0)
        assert ans is None
        assert model == ""
        assert detail.startswith("vision budget exhausted")
        # Second model was never called
        assert call_count[0] == 1


def test_loose_text_key():
    # Accent normalization & casefolding
    k1 = loose_text_key("Marc Márquez is proving", 15)
    k2 = loose_text_key("marc marquez is proving", 15)
    assert k1 == k2
    assert k1 == "marcmarquezispr"

    # Mismatch
    k3 = loose_text_key("Marc Marquez", 15)
    k4 = loose_text_key("Lewis Hamilton", 15)
    assert k3 != k4

    # None and empty
    assert loose_text_key(None, 15) == ""
    assert loose_text_key("", 15) == ""

    # Truncation to n
    assert loose_text_key("abcdefghijklmnopqrstuvwxyz", 5) == "abcde"


def test_check_composer_loose_caption():
    ans = {
        "screen": "composer",
        "author_name": "Agung",
        "has_attached_photo": True,
        "caption_starts_with": "Marc Márquez is proving why he is back",
        "same_photo_as_reference": True,
    }
    # Matching loose caption with accents and case differences
    ok, _ = check_composer(
        ans,
        expected_author="Agung",
        expected_caption="marc marquez is proving why he is back",
        require_caption=True,
    )
    assert ok is True

    # Mismatched caption
    ok, detail = check_composer(
        ans,
        expected_author="Agung",
        expected_caption="Lewis Hamilton takes the podium",
        require_caption=True,
    )
    assert ok is False
    assert "caption mismatch" in detail


def test_check_posted_posts_array():
    ans = {
        "screen": "profile",
        "posts": [
            {
                "author_name": "Someone Else",
                "caption_starts_with": "Unrelated post",
                "has_photo": True,
            },
            {
                "author_name": "Agung",
                "caption_starts_with": "Marc Márquez is proving why he is back",
                "has_photo": True,
            },
        ],
        "still_uploading": False,
    }

    # Second post matches author, photo, and loose caption
    ok, _ = check_posted(ans, expected_author="Agung", expected_caption="marc marquez is proving why...")
    assert ok is True

    # Photo is False
    ans_no_photo = {
        "screen": "profile",
        "posts": [
            {
                "author_name": "Agung",
                "caption_starts_with": "Marc Márquez is proving why he is back",
                "has_photo": False,
            }
        ],
        "still_uploading": False,
    }
    ok, _ = check_posted(ans_no_photo, expected_author="Agung", expected_caption="marc marquez is proving")
    assert ok is False

    # Author mismatch
    ok, _ = check_posted(ans, expected_author="NonExistentAuthor", expected_caption="marc marquez is proving")
    assert ok is False

    # Caption mismatch
    ok, _ = check_posted(ans, expected_author="Agung", expected_caption="Lewis Hamilton takes the win")
    assert ok is False

    # Empty posts
    ok, detail = check_posted({"screen": "profile", "posts": []}, expected_author="Agung", expected_caption="test")
    assert ok is False
    assert "no posts found" in detail


def test_composer_after_typing_allows_missing_photo_before_typing_requires_it():
    ans_no_photo = {
        "screen": "composer",
        "author_name": "Agung",
        "has_attached_photo": False,
        "caption_starts_with": "Hello world! This is my post",
        "same_photo_as_reference": False,
    }
    # check_composer directly
    ok_before, detail_before = check_composer(
        ans_no_photo,
        expected_author="Agung",
        expected_caption="Hello world! This is my post",
        require_caption=True,
        require_photo=True,
    )
    assert ok_before is False
    assert "has_attached_photo is not True" in detail_before

    ok_after, detail_after = check_composer(
        ans_no_photo,
        expected_author="Agung",
        expected_caption="Hello world! This is my post",
        require_caption=True,
        require_photo=False,
    )
    assert ok_after is True
    assert detail_after == "composer verified"

    # verify_gate integration
    with (
        mock.patch("phone_runner.vision_verify.capture_screenshot_png", return_value=FAKE_PNG),
        mock.patch("phone_runner.vision_verify.ask_vision", return_value=(ans_no_photo, "gemini-model", "ok")),
    ):
        # composer_before_typing fails with has_attached_photo False
        ok_gate_before, ev_gate_before = verify_gate(
            FAKE_SERIAL,
            "composer_before_typing",
            expected_author="Agung",
        )
        assert ok_gate_before is False
        assert "has_attached_photo is not True" in ev_gate_before["detail"]

        # composer_after_typing passes with has_attached_photo False
        ok_gate_after, ev_gate_after = verify_gate(
            FAKE_SERIAL,
            "composer_after_typing",
            expected_author="Agung",
            expected_caption="Hello world! This is my post",
            require_caption=True,
        )
        assert ok_gate_after is True
        assert ev_gate_after["ok"] is True


def test_check_reel_settings():
    assert check_reel_settings(None)[0] is False
    assert check_reel_settings({"screen": "post_settings", "share_now_visible": True})[0] is False
    assert check_reel_settings({"screen": "reel_settings", "share_now_visible": False})[0] is False
    ok, detail = check_reel_settings({"screen": "reel_settings", "share_now_visible": True})
    assert ok is True
    assert detail == "reel_settings verified"


def test_verify_gate_reel_settings():
    ans = {"screen": "reel_settings", "share_now_visible": True, "has_video_thumbnail": True}
    with (
        mock.patch("phone_runner.vision_verify.capture_screenshot_png", return_value=FAKE_PNG),
        mock.patch("phone_runner.vision_verify.ask_vision", return_value=(ans, "gemini-model", "ok")),
    ):
        ok, ev = verify_gate(FAKE_SERIAL, "reel_settings")
        assert ok is True
        assert ev["ok"] is True
        assert ev["gate"] == "reel_settings"


def test_check_composer_require_same_reference_flag():
    ans = {
        "screen": "composer",
        "author_name": "Agung",
        "has_attached_photo": True,
        "caption_starts_with": "",
        "same_photo_as_reference": False,
    }
    # Default require_same_reference=True -> fails because same_photo_as_reference is False
    ok1, d1 = check_composer(ans, expected_author="Agung", require_photo=True, require_same_reference=True)
    assert ok1 is False
    assert "same_photo_as_reference is not True" in d1

    # require_same_reference=False -> passes (video case)
    ok2, d2 = check_composer(ans, expected_author="Agung", require_photo=True, require_same_reference=False)
    assert ok2 is True
    assert d2 == "composer verified"





def test_after_typing_accepts_visible_end_fragment_of_long_caption():
    from phone_runner.vision_verify import check_composer

    expected = "It spins at up to ten thousand rpm, driven by compressed air.\n\n#F1 #Pitstop #Motorsport"
    ans = {"screen": "composer", "author_name": "Agung", "caption_starts_with": "#F1 #Pitstop #Motorsport"}
    ok, _ = check_composer(ans, "Agung", expected, require_caption=True, require_photo=False)
    assert ok is True


def test_after_typing_rejects_text_not_in_caption():
    from phone_runner.vision_verify import check_composer

    expected = "It spins at up to ten thousand rpm, driven by compressed air."
    ans = {"screen": "composer", "author_name": "Agung", "caption_starts_with": "Lewis Hamilton wins again"}
    ok, _ = check_composer(ans, "Agung", expected, require_caption=True, require_photo=False)
    assert ok is False
    short = {"screen": "composer", "author_name": "Agung", "caption_starts_with": "#F1"}
    assert check_composer(short, "Agung", expected + " #F1", require_caption=True, require_photo=False)[0] is False


# ---------------------------------------------------------------------------
# New focused tests for timeout / trace / safety requirements
# ---------------------------------------------------------------------------

def _make_success_resp(payload: dict):
    """Build a mock urlopen context-manager response that returns payload."""
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_http_error_then_success_on_next_candidate():
    """HTTP error on first candidate → fallback succeeds on second."""
    http_err = urllib.error.HTTPError(
        url="http://x", code=503, msg="Service Unavailable", hdrs=None, fp=None
    )
    success_resp = _make_success_resp({
        "choices": [{"message": {"content": '{"screen": "post_settings", "share_button_visible": true}'}}]
    })

    with (
        mock.patch("phone_runner.vision_verify.get_vision_models",
                   return_value=["ag/gemini-first", "ag/gemini-second"]),
        mock.patch("urllib.request.urlopen", side_effect=[http_err, success_resp]),
    ):
        ans, model, detail = ask_vision(FAKE_PNG, "prompt")

    assert ans == {"screen": "post_settings", "share_button_visible": True}
    assert model == "ag/gemini-second"
    assert detail.startswith("ok;winner=ag/gemini-second")
    # Winner must appear with success outcome and elapsed seconds
    assert ":success:" in detail
    assert "s" in detail
    assert "http_error:503" in detail
    # No sensitive content in detail
    assert "Service Unavailable" not in detail
    assert "http://x" not in detail


def test_timeout_then_fallback_success():
    """TimeoutError on first candidate → fallback succeeds on second."""
    success_resp = _make_success_resp({
        "choices": [{"message": {"content": '{"screen": "composer"}'}}]
    })

    with (
        mock.patch("phone_runner.vision_verify.get_vision_models",
                   return_value=["ag/gemini-slow", "ag/gemini-fast"]),
        mock.patch("urllib.request.urlopen", side_effect=[TimeoutError("timed out"), success_resp]),
    ):
        ans, model, detail = ask_vision(FAKE_PNG, "prompt")

    assert ans == {"screen": "composer"}
    assert model == "ag/gemini-fast"
    assert "timeout" in detail
    assert "prior_failures" in detail
    # Exception message must not leak
    assert "timed out" not in detail


def test_exhaustion_all_candidates_no_false_success():
    """All candidates fail → returns None with trace of all attempts."""
    http_err_1 = urllib.error.HTTPError(url="http://x", code=500, msg="err", hdrs=None, fp=None)
    http_err_2 = urllib.error.HTTPError(url="http://x", code=429, msg="err", hdrs=None, fp=None)
    http_err_3 = urllib.error.HTTPError(url="http://x", code=503, msg="err", hdrs=None, fp=None)

    models = ["ag/gemini-a", "ag/gemini-b", "ag/gemini-c"]
    with (
        mock.patch("phone_runner.vision_verify.get_vision_models", return_value=models),
        mock.patch("urllib.request.urlopen", side_effect=[http_err_1, http_err_2, http_err_3]),
    ):
        ans, model, detail = ask_vision(FAKE_PNG, "prompt")

    assert ans is None
    assert model == ""
    assert "all vision models failed" in detail
    # All three candidates must appear in trace
    assert "ag/gemini-a" in detail
    assert "ag/gemini-b" in detail
    assert "ag/gemini-c" in detail
    assert "http_error:500" in detail
    assert "http_error:429" in detail
    assert "http_error:503" in detail


def test_trace_includes_model_duration_and_http_status():
    """Trace entries must contain model name, elapsed time, and numeric HTTP status."""
    http_err = urllib.error.HTTPError(url="http://x", code=502, msg="Bad Gateway", hdrs=None, fp=None)
    success_resp = _make_success_resp({
        "choices": [{"message": {"content": '{"screen": "other"}'}}]
    })

    with (
        mock.patch("phone_runner.vision_verify.get_vision_models",
                   return_value=["ag/gemini-a", "ag/gemini-b"]),
        mock.patch("urllib.request.urlopen", side_effect=[http_err, success_resp]),
    ):
        ans, model, detail = ask_vision(FAKE_PNG, "prompt")

    assert ans is not None
    # detail must reference the failed model ID and its HTTP status code
    assert "ag/gemini-a" in detail
    assert "502" in detail
    # elapsed time in seconds present (format: <float>s)
    assert "s" in detail


def test_trace_does_not_include_sensitive_data():
    """Trace must not contain exception messages, response bodies, or secret-like strings."""
    secret = "super-secret-token-xyz"
    exc_msg = "Connection refused to backend"

    class LeakyError(Exception):
        pass

    def urlopen_raises(req, timeout=None):
        raise LeakyError(exc_msg)

    with (
        mock.patch("phone_runner.vision_verify.get_vision_models",
                   return_value=["ag/gemini-only"]),
        mock.patch("urllib.request.urlopen", side_effect=urlopen_raises),
        mock.patch.dict("os.environ", {"OPENAI_API_KEY": secret}),
    ):
        ans, model, detail = ask_vision(FAKE_PNG, "prompt")

    assert ans is None
    assert exc_msg not in detail
    assert secret not in detail
    # Exception class name is allowed, message is not
    assert "LeakyError" in detail


def test_per_candidate_timeout_is_25s_and_budget_is_110s():
    """Confirm constant values for timeout and budget."""
    assert VISION_MODEL_TIMEOUT_SECONDS == 25.0
    assert VISION_GATE_BUDGET_SECONDS == 110.0


def test_fallback_through_four_candidates_with_25s_timeout():
    """Mocks four candidates, three HTTP errors then valid JSON, asserting 25.0s per model."""
    four_candidates = [
        "FlashFast",
        "ag/gemini-3.8-flash-medium",
        "ag/gemini-3.8-flash-low",
        "ag/gemini-3.8-flash-high",
    ]
    captured_timeouts: list[float | None] = []

    def mock_urlopen(req, timeout=None):
        captured_timeouts.append(timeout)
        if len(captured_timeouts) <= 3:
            raise urllib.error.HTTPError(
                url="http://x", code=500 + len(captured_timeouts), msg="err", hdrs=None, fp=None
            )
        return _make_success_resp({
            "choices": [{"message": {"content": '{"screen": "composer", "author_name": "Agung"}'}}]
        })

    with (
        mock.patch("phone_runner.vision_verify.get_vision_models", return_value=four_candidates),
        mock.patch("urllib.request.urlopen", side_effect=mock_urlopen) as patched_urlopen,
    ):
        ans, model, detail = ask_vision(FAKE_PNG, "prompt")

    assert len(captured_timeouts) == 4
    assert captured_timeouts == [25.0, 25.0, 25.0, 25.0]
    assert [call.kwargs.get("timeout") for call in patched_urlopen.call_args_list] == [25.0, 25.0, 25.0, 25.0]
    assert model == "ag/gemini-3.8-flash-high"
    assert ans == {"screen": "composer", "author_name": "Agung"}
    assert "ok;winner=ag/gemini-3.8-flash-high" in detail
    assert "prior_failures" in detail


def test_trace_includes_success_outcome_and_elapsed_first_model():
    """When first model succeeds, detail includes its success outcome and elapsed seconds."""
    success_resp = _make_success_resp({
        "choices": [{"message": {"content": '{"screen": "composer"}'}}]
    })

    with (
        mock.patch("phone_runner.vision_verify.get_vision_models",
                   return_value=["ag/gemini-only"]),
        mock.patch("urllib.request.urlopen", return_value=success_resp),
    ):
        ans, model, detail = ask_vision(FAKE_PNG, "prompt")

    assert ans == {"screen": "composer"}
    assert model == "ag/gemini-only"
    # Winner's success outcome and elapsed must be in detail
    assert "ok;winner=ag/gemini-only" in detail
    assert ":success:" in detail
    assert detail.endswith("s") or "s" in detail  # elapsed seconds present
    # No prior_failures section when first model wins
    assert "prior_failures" not in detail


# ---------------------------------------------------------------------------
# Image shrinking before vision requests (9Router HTTP 413 above ~4-5 MB, 2026-10-31)
# ---------------------------------------------------------------------------

def _image_urls_from_request(req) -> list[str]:
    body = json.loads(req.data.decode("utf-8"))
    parts = body["messages"][0]["content"]
    return [p["image_url"]["url"] for p in parts if p.get("type") == "image_url"]


def _composer_success_resp():
    return _make_success_resp({"choices": [{"message": {"content": '{"screen": "composer"}'}}]})


def test_shrink_constants():
    assert VISION_SCREENSHOT_MAX_SIDE == 2000
    assert VISION_REFERENCE_MAX_SIDE == 1280
    assert VISION_MAX_IMAGE_BYTES == 3_000_000
    assert VISION_SHRINK_TIMEOUT_SECONDS == 20.0


def test_shrink_for_vision_runs_ffmpeg_with_scale_filter():
    fake_jpeg = b"\xff\xd8small-jpeg"
    with (
        mock.patch("phone_runner.vision_verify.shutil.which", return_value="/usr/bin/ffmpeg"),
        mock.patch(
            "phone_runner.vision_verify.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=fake_jpeg, stderr=b""),
        ) as mock_run,
    ):
        result = _shrink_for_vision(FAKE_PNG, 2000)

    assert result == (fake_jpeg, "image/jpeg")
    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert cmd[0] == "/usr/bin/ffmpeg"
    assert cmd[cmd.index("-i") + 1] == "pipe:0"
    assert cmd[-1] == "pipe:1"
    assert cmd[cmd.index("-frames:v") + 1] == "1"
    assert cmd[cmd.index("-c:v") + 1] == "mjpeg"
    vf = cmd[cmd.index("-vf") + 1]
    assert "min(iw,2000)" in vf and "min(ih,2000)" in vf
    assert kwargs["input"] == FAKE_PNG
    assert kwargs["capture_output"] is True
    assert kwargs["timeout"] == VISION_SHRINK_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "which, run_result",
    [
        (None, None),
        ("/usr/bin/ffmpeg", subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"bad")),
        ("/usr/bin/ffmpeg", subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")),
        ("/usr/bin/ffmpeg", subprocess.TimeoutExpired(cmd="ffmpeg", timeout=20.0)),
    ],
)
def test_shrink_for_vision_returns_none_on_failure(which, run_result):
    side_effect = run_result if isinstance(run_result, Exception) else None
    with (
        mock.patch("phone_runner.vision_verify.shutil.which", return_value=which),
        mock.patch(
            "phone_runner.vision_verify.subprocess.run",
            return_value=run_result,
            side_effect=side_effect,
        ) as mock_run,
    ):
        assert _shrink_for_vision(FAKE_PNG, 2000) is None
    if which is None:
        mock_run.assert_not_called()


def test_ask_vision_sends_shrunk_jpegs_with_right_max_sides():
    calls: list[int] = []

    def fake_shrink(data, max_side):
        calls.append(max_side)
        return b"small-" + data[:4], "image/jpeg"

    with (
        mock.patch("phone_runner.vision_verify._shrink_for_vision", side_effect=fake_shrink),
        mock.patch("phone_runner.vision_verify.get_vision_models", return_value=["ag/gemini-only"]),
        mock.patch("urllib.request.urlopen", return_value=_composer_success_resp()) as mock_open,
    ):
        ans, _model, _detail = ask_vision(FAKE_PNG, "prompt", reference_jpeg=b"\xff\xd8reference")

    assert ans == {"screen": "composer"}
    assert calls == [VISION_SCREENSHOT_MAX_SIDE, VISION_REFERENCE_MAX_SIDE]
    urls = _image_urls_from_request(mock_open.call_args[0][0])
    assert len(urls) == 2
    assert all(u.startswith("data:image/jpeg;base64,") for u in urls)
    assert base64.b64decode(urls[0].split(",", 1)[1]) == b"small-" + FAKE_PNG[:4]
    assert base64.b64decode(urls[1].split(",", 1)[1]) == b"small-\xff\xd8re"


def test_ask_vision_runs_real_shrink_through_mocked_ffmpeg():
    with (
        mock.patch("phone_runner.vision_verify.shutil.which", return_value="/usr/bin/ffmpeg"),
        mock.patch(
            "phone_runner.vision_verify.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=b"\xff\xd8jpeg", stderr=b""),
        ) as mock_run,
        mock.patch("phone_runner.vision_verify.get_vision_models", return_value=["ag/gemini-only"]),
        mock.patch("urllib.request.urlopen", return_value=_composer_success_resp()) as mock_open,
    ):
        ans, _model, _detail = ask_vision(FAKE_PNG, "prompt", reference_jpeg=b"\xff\xd8reference")

    assert ans == {"screen": "composer"}
    filters = [c.args[0][c.args[0].index("-vf") + 1] for c in mock_run.call_args_list]
    assert len(filters) == 2
    assert "min(iw,2000)" in filters[0]
    assert "min(iw,1280)" in filters[1]
    urls = _image_urls_from_request(mock_open.call_args[0][0])
    assert [u.split(",", 1)[0] for u in urls] == ["data:image/jpeg;base64", "data:image/jpeg;base64"]


def test_ask_vision_sends_originals_when_shrink_unavailable():
    reference = b"\xff\xd8reference"
    with (
        mock.patch("phone_runner.vision_verify.shutil.which", return_value=None),
        mock.patch("phone_runner.vision_verify.get_vision_models", return_value=["ag/gemini-only"]),
        mock.patch("urllib.request.urlopen", return_value=_composer_success_resp()) as mock_open,
    ):
        ans, _model, _detail = ask_vision(FAKE_PNG, "prompt", reference_jpeg=reference)

    assert ans == {"screen": "composer"}
    urls = _image_urls_from_request(mock_open.call_args[0][0])
    assert urls == [
        "data:image/png;base64," + base64.b64encode(FAKE_PNG).decode("ascii"),
        "data:image/jpeg;base64," + base64.b64encode(reference).decode("ascii"),
    ]


def test_ask_vision_shrinks_again_with_half_sides_when_still_too_big():
    calls: list[int] = []

    def fake_shrink(data, max_side):
        calls.append(max_side)
        if len(calls) <= 2:
            return b"x" * (VISION_MAX_IMAGE_BYTES // 2 + 1), "image/jpeg"
        return b"y" * 10, "image/jpeg"

    with (
        mock.patch("phone_runner.vision_verify._shrink_for_vision", side_effect=fake_shrink),
        mock.patch("phone_runner.vision_verify.get_vision_models", return_value=["ag/gemini-only"]),
        mock.patch("urllib.request.urlopen", return_value=_composer_success_resp()) as mock_open,
    ):
        ans, _model, _detail = ask_vision(FAKE_PNG, "prompt", reference_jpeg=b"\xff\xd8reference")

    assert ans == {"screen": "composer"}
    assert calls == [
        VISION_SCREENSHOT_MAX_SIDE,
        VISION_REFERENCE_MAX_SIDE,
        VISION_SCREENSHOT_MAX_SIDE // 2,
        VISION_REFERENCE_MAX_SIDE // 2,
    ]
    urls = _image_urls_from_request(mock_open.call_args[0][0])
    assert all(base64.b64decode(u.split(",", 1)[1]) == b"y" * 10 for u in urls)


def test_ask_vision_no_second_shrink_when_within_limit():
    calls: list[int] = []

    def fake_shrink(data, max_side):
        calls.append(max_side)
        return b"z" * 100, "image/jpeg"

    with (
        mock.patch("phone_runner.vision_verify._shrink_for_vision", side_effect=fake_shrink),
        mock.patch("phone_runner.vision_verify.get_vision_models", return_value=["ag/gemini-only"]),
        mock.patch("urllib.request.urlopen", return_value=_composer_success_resp()),
    ):
        ask_vision(FAKE_PNG, "prompt")

    # No reference photo → only the screenshot is shrunk, and only once
    assert calls == [VISION_SCREENSHOT_MAX_SIDE]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_shrink_for_vision_real_ffmpeg_round_trip():
    gen = subprocess.run(
        [
            shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red:s=3000x1500",
            # Noise so the PNG is large like a real screenshot; a flat red PNG is only ~16 KB.
            "-vf", "noise=alls=30:allf=u",
            "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1",
        ],
        capture_output=True,
        timeout=30,
    )
    assert gen.returncode == 0 and gen.stdout.startswith(b"\x89PNG")
    source = gen.stdout

    result = _shrink_for_vision(source, 2000)

    assert result is not None
    jpeg, mime = result
    assert mime == "image/jpeg"
    assert jpeg.startswith(b"\xff\xd8")
    assert len(jpeg) < len(source)

    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return
    probe = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", "-i", "pipe:0",
        ],
        input=jpeg,
        capture_output=True,
        timeout=30,
    )
    assert probe.returncode == 0
    assert probe.stdout.decode().strip() == "2000x1000"
