import io
import json
import subprocess
from unittest import mock
import pytest

from phone_runner.vision_verify import (
    _MODELS_CACHE,
    DEFAULT_VISION_MODELS,
    PROMPT_COMPOSER,
    PROMPT_POST_SETTINGS,
    PROMPT_POSTED,
    ask_vision,
    capture_screenshot_png,
    check_composer,
    check_post_settings,
    check_posted,
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
            {"id": "ag/gemini-3.8-flash"},
            {"id": "ag/gemini-3.7-flash-high"},
            {"id": "some/other-model"},
        ]
    }).encode("utf-8")
    fake_resp.__enter__.return_value = fake_resp

    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        models = get_vision_models()
        assert models == ["ag/gemini-3.8-flash", "ag/gemini-3.7-flash-high"]


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
            assert detail == "ok"


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
        assert detail == "vision budget exhausted"
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


