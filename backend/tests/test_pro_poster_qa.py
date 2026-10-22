import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.identity_check import IdentityResult
from app.services.pro_poster.qa import (
    check_safety,
    gear_identity,
)
from app.services.pro_poster.types import EventContext, PosterBrief, Subject


@pytest.fixture(autouse=True)
def no_sleep_on_retry(monkeypatch):
    monkeypatch.setattr("app.services.vision_retry.time.sleep", lambda s: None)


@pytest.fixture
def sample_brief() -> PosterBrief:
    return PosterBrief(
        job_id=101,
        subjects=(
            Subject(name="Max Verstappen", role="hero", team="Red Bull Racing"),
        ),
        event=EventContext(
            series="Formula 1",
            vehicle="Formula 1 car",
            circuit="Monza",
            session="race",
        ),
        story_type="result",
        mood="dramatic",
        badge_label="F1 NEWS",
        must_avoid=("MotoGP bikes", "night race"),
    )


def test_gear_identity_direct():
    """Direct unit test of gear_identity helper function."""
    with patch("app.services.design_images._vision_chat") as mock_vision, patch(
        "app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"
    ):
        # 1. Success case
        mock_vision.return_value = json.dumps(
            {"same_athlete": True, "confidence": 0.85, "notes": "helmet matches"}
        )
        passed, reason = gear_identity(b"ref", b"out", "Bulega")
        assert passed is True
        assert reason == "helmet matches"

        # 2. confidence < 0.7
        mock_vision.return_value = json.dumps(
            {"same_athlete": True, "confidence": 0.65, "notes": "unsure on visor"}
        )
        passed, reason = gear_identity(b"ref", b"out", "Bulega")
        assert passed is False
        assert "0.65" in reason

        # 3. same_athlete is False
        mock_vision.return_value = json.dumps(
            {"same_athlete": False, "confidence": 0.9, "notes": "wrong rider number 11 vs 55"}
        )
        passed, reason = gear_identity(b"ref", b"out", "Bulega")
        assert passed is False
        assert "wrong rider number 11 vs 55" in reason

        # 4. Bad JSON
        mock_vision.return_value = "not json"
        passed, reason = gear_identity(b"ref", b"out", "Bulega")
        assert passed is False
        assert reason.startswith("error:")


def test_check_safety_identity_pass_event_pass(sample_brief: PosterBrief):
    """Identity pass + event pass -> passed, scores empty, fix_hint empty."""
    with patch("app.services.identity_check.check_identity") as mock_id, patch(
        "app.services.design_images._vision_chat"
    ) as mock_vision, patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        mock_id.return_value = IdentityResult(passed=True, sface=0.88, gemini="yes", reason="match")
        mock_vision.return_value = json.dumps({"wrong_event": False, "reason": "matches F1 Monza"})

        res = check_safety(
            b"fake_image",
            sample_brief,
            hero_ref_bytes=b"hero_ref",
            headline="Verstappen Wins",
        )

        assert res.passed is True
        assert res.identity_passed is True
        assert res.scores == {}
        assert res.issues == ()
        assert res.fix_hint == ""
        mock_id.assert_called_once_with(
            b"hero_ref", b"fake_image", subject="Max Verstappen", context="pro_poster"
        )
        mock_vision.assert_called_once()


def test_check_safety_identity_fail_skips_event_call(sample_brief: PosterBrief):
    """Identity fail -> not passed, and the event call is skipped."""
    with patch("app.services.identity_check.check_identity") as mock_id, patch(
        "app.services.design_images._vision_chat"
    ) as mock_vision:
        mock_id.return_value = IdentityResult(
            passed=False, sface=0.1, gemini="no", reason="sface 0.10 < 0.25"
        )

        res = check_safety(
            b"fake_image",
            sample_brief,
            hero_ref_bytes=b"hero_ref",
            headline="Verstappen Wins",
        )

        assert res.passed is False
        assert res.identity_passed is False
        assert res.issues == ("identity: sface 0.10 < 0.25",)
        assert res.scores == {}
        assert res.fix_hint == ""
        mock_vision.assert_not_called()


def test_check_safety_helmet_case_identity_passes(sample_brief: PosterBrief):
    """Helmet case: check_identity fails with sface None, gear_identity mocked true 0.8 -> passes."""
    with patch("app.services.identity_check.check_identity") as mock_identity, patch(
        "app.services.pro_poster.qa.gear_identity", return_value=(True, "helmet and livery match")
    ) as mock_gear, patch(
        "app.services.design_images._vision_chat"
    ) as mock_vision, patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        mock_identity.return_value = IdentityResult(
            passed=False, sface=None, gemini="no", reason="face obscured by helmet"
        )
        mock_vision.return_value = json.dumps({"wrong_event": False, "reason": "ok"})

        res = check_safety(
            b"fake_image",
            sample_brief,
            hero_ref_bytes=b"hero_ref",
        )

        assert res.passed is True
        assert res.identity_passed is True
        mock_identity.assert_called_once()
        mock_gear.assert_called_once_with(b"hero_ref", b"fake_image", "Max Verstappen")
        mock_vision.assert_called_once()


def test_check_safety_gear_false_fails_with_both_reasons(sample_brief: PosterBrief):
    """Gear false -> fails, with both reasons in the issue."""
    with patch("app.services.identity_check.check_identity") as mock_identity, patch(
        "app.services.pro_poster.qa.gear_identity", return_value=(False, "different helmet design and colors")
    ) as mock_gear, patch(
        "app.services.design_images._vision_chat"
    ) as mock_vision:
        mock_identity.return_value = IdentityResult(
            passed=False, sface=None, gemini="no", reason="face obscured by visor"
        )

        res = check_safety(
            b"fake_image",
            sample_brief,
            hero_ref_bytes=b"hero_ref",
        )

        assert res.passed is False
        assert res.identity_passed is False
        assert len(res.issues) == 1
        assert "face obscured by visor" in res.issues[0]
        assert "different helmet design and colors" in res.issues[0]
        mock_vision.assert_not_called()


def test_check_safety_gear_low_confidence_fails_with_both_reasons(sample_brief: PosterBrief):
    """Gear confidence 0.6 (< 0.7) -> fails, with both reasons in the issue."""
    with patch("app.services.identity_check.check_identity") as mock_identity, patch(
        "app.services.design_images._vision_chat"
    ) as mock_vision, patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        mock_identity.return_value = IdentityResult(
            passed=False, sface=None, gemini="no", reason="sface is None"
        )
        mock_vision.return_value = json.dumps(
            {"same_athlete": True, "confidence": 0.6, "notes": "partially matched helmet"}
        )

        res = check_safety(
            b"fake_image",
            sample_brief,
            hero_ref_bytes=b"hero_ref",
        )

        assert res.passed is False
        assert res.identity_passed is False
        assert len(res.issues) == 1
        assert "sface is None" in res.issues[0]
        assert "0.6" in res.issues[0]


def test_check_safety_gear_bad_json_fails_with_both_reasons(sample_brief: PosterBrief):
    """Gear bad JSON -> fails, with both reasons in the issue."""
    with patch("app.services.identity_check.check_identity") as mock_identity, patch(
        "app.services.design_images._vision_chat"
    ) as mock_vision, patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        mock_identity.return_value = IdentityResult(
            passed=False, sface=None, gemini="no", reason="face covered"
        )
        mock_vision.return_value = "invalid json from vision"

        res = check_safety(
            b"fake_image",
            sample_brief,
            hero_ref_bytes=b"hero_ref",
        )

        assert res.passed is False
        assert res.identity_passed is False
        assert len(res.issues) == 1
        assert "face covered" in res.issues[0]
        assert "error:" in res.issues[0]


def test_check_safety_sface_clear_reject_gear_check_not_called(sample_brief: PosterBrief):
    """sface 0.1 (face found, clear reject) -> gear check NOT called."""
    with patch("app.services.identity_check.check_identity") as mock_identity, patch(
        "app.services.pro_poster.qa.gear_identity"
    ) as mock_gear, patch("app.services.design_images._vision_chat") as mock_vision:
        mock_identity.return_value = IdentityResult(
            passed=False, sface=0.1, gemini="skipped", reason="sface 0.10 < 0.25"
        )

        res = check_safety(
            b"fake_image",
            sample_brief,
            hero_ref_bytes=b"hero_ref",
        )

        assert res.passed is False
        assert res.identity_passed is False
        mock_gear.assert_not_called()
        mock_vision.assert_not_called()
        assert res.issues == ("identity: sface 0.10 < 0.25",)


def test_check_safety_wrong_event_true_not_passed(sample_brief: PosterBrief):
    """wrong_event true -> not passed, with event issue."""
    with patch("app.services.identity_check.check_identity") as mock_id, patch(
        "app.services.design_images._vision_chat"
    ) as mock_vision, patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        mock_id.return_value = IdentityResult(passed=True, sface=0.85, gemini="yes", reason="match")
        mock_vision.return_value = json.dumps(
            {"wrong_event": True, "reason": "shows MotoGP bike instead of F1 car"}
        )

        res = check_safety(
            b"fake_image",
            sample_brief,
            hero_ref_bytes=b"hero_ref",
            headline="Verstappen Wins",
        )

        assert res.passed is False
        assert res.identity_passed is True
        assert res.issues == ("event: shows MotoGP bike instead of F1 car",)
        assert res.scores == {}


def test_check_safety_bad_json_fails_closed(sample_brief: PosterBrief):
    """bad JSON from event vision call -> not passed with 'event: unreadable answer'."""
    with patch("app.services.identity_check.check_identity") as mock_id, patch(
        "app.services.design_images._vision_chat"
    ) as mock_vision, patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        mock_id.return_value = IdentityResult(passed=True, sface=0.85, gemini="yes", reason="match")
        mock_vision.return_value = "invalid json response"

        res = check_safety(
            b"fake_image",
            sample_brief,
            hero_ref_bytes=b"hero_ref",
            headline="Verstappen Wins",
        )

        assert res.passed is False
        assert res.identity_passed is True
        assert res.issues == ("event: unreadable answer",)
        assert res.scores == {}


def test_check_safety_vision_exception_fails_closed(sample_brief: PosterBrief):
    """Exception during event vision call -> not passed with 'event: unreadable answer'."""
    with patch("app.services.identity_check.check_identity") as mock_id, patch(
        "app.services.design_images._vision_chat"
    ) as mock_vision, patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        mock_id.return_value = IdentityResult(passed=True, sface=0.85, gemini="yes", reason="match")
        mock_vision.side_effect = RuntimeError("Vision service unavailable")

        res = check_safety(
            b"fake_image",
            sample_brief,
            hero_ref_bytes=b"hero_ref",
        )

        assert res.passed is False
        assert res.issues == ("event: unreadable answer",)


def test_check_safety_no_hero_skips_identity_checks_event():
    """Brief without hero skips identity check and proceeds to event check."""
    brief_no_hero = PosterBrief(
        job_id=105,
        subjects=(),
        event=EventContext(series="MotoGP", vehicle="MotoGP bike", circuit="Mugello"),
        story_type="preview",
        mood="neutral",
        badge_label="MOTOGP NEWS",
    )
    with patch("app.services.identity_check.check_identity") as mock_id, patch(
        "app.services.design_images._vision_chat"
    ) as mock_vision, patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        mock_vision.return_value = json.dumps({"wrong_event": False, "reason": "ok"})

        res = check_safety(b"fake_image", brief_no_hero)

        mock_id.assert_not_called()
        assert res.passed is True
        assert res.identity_passed is None


def test_check_safety_prompt_formatting(sample_brief: PosterBrief):
    """Verify exact prompt format sent to vision chat in event check."""
    captured_prompt = None

    def fake_vision_chat(content, **kwargs):
        nonlocal captured_prompt
        captured_prompt = content[0]["text"]
        assert kwargs.get("context") == "pro_poster_event_check"
        assert kwargs.get("temperature") == 0
        assert kwargs.get("max_tokens") == 800
        return json.dumps({"wrong_event": False, "reason": "ok"})

    with patch("app.services.identity_check.check_identity", return_value=IdentityResult(passed=True, sface=0.9, gemini="yes", reason="ok")), \
         patch("app.services.design_images._vision_chat", side_effect=fake_vision_chat), \
         patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        res = check_safety(
            b"fake_image",
            sample_brief,
            hero_ref_bytes=b"hero",
            headline="Max Tops Friday",
        )

        assert res.passed is True
        expected_prompt = (
            'This is a designed sports news poster. Brief: series Formula 1, vehicle Formula 1 car, '
            'circuit/venue Monza, headline "Max Tops Friday". Must avoid: MotoGP bikes, night race. '
            'Does the image show a DIFFERENT series or a different type of vehicle than the brief, '
            'an official series/league logo or wordmark on banners, screens, walls, the track, the octagon mat or fence, podium backdrops or as an added graphic (e.g. the F1, Formula 1, FIA, MotoGP, FIM, Dorna or UFC logo), '
            'or anything from the must-avoid list? '
            "A small series logo that is part of an athlete's real gear (gloves, fight shorts, race suit, helmet) is fine. "
            'Team, manufacturer and sponsor logos (e.g. Ferrari, Red Bull, Ducati, Mercedes, Monster) are fine and never make it wrong. '
            'Reply with ONLY strict JSON {"wrong_event": true|false, "reason": "<short>"}'
        )
        assert captured_prompt == expected_prompt


def test_qa_event_check_allows_gear_logos_and_forbids_octagon_mat(sample_brief: PosterBrief):
    """Event check prompt allows series logo on gear and forbids octagon mat or fence."""
    captured_prompt = None

    def fake_vision_chat(content, **kwargs):
        nonlocal captured_prompt
        captured_prompt = content[0]["text"]
        return json.dumps({"wrong_event": False, "reason": "ok"})

    with patch("app.services.identity_check.check_identity", return_value=IdentityResult(passed=True, sface=0.9, gemini="yes", reason="ok")), \
         patch("app.services.design_images._vision_chat", side_effect=fake_vision_chat), \
         patch("app.services.design_images._vision_datauri", return_value="data:image/jpeg;base64,abc"):
        res = check_safety(
            b"fake_image",
            sample_brief,
            hero_ref_bytes=b"hero",
            headline="Max Tops Friday",
        )
        assert res.passed is True
        assert captured_prompt is not None
        assert "A small series logo that is part of an athlete's real gear (gloves, fight shorts, race suit, helmet) is fine." in captured_prompt
        assert "octagon mat or fence" in captured_prompt
        question_clause = captured_prompt.split("Does the image show")[1].split("?")[0]
        assert "gloves" not in question_clause
