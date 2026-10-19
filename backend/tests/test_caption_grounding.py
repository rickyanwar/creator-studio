"""Tests for caption grounding, vision checks, and hallucination guardrails."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.models.publish_jobs import PublishJobStatus
from app.models.target_fanpages import PublishMode
from app.services.ai_caption import build_caption_prompt
from app.services.caption_grounding import describe_image_for_caption, has_usable_caption
from app.tasks.ai_generator import generate_caption_for_job


# ─────────────────────────────────────────────────────────────────────────────
# 1. has_usable_caption
# ─────────────────────────────────────────────────────────────────────────────

def test_has_usable_caption_empty():
    assert has_usable_caption("") is False
    assert has_usable_caption(None) is False
    assert has_usable_caption("   \n\t  ") is False


def test_has_usable_caption_hashtags_only():
    assert has_usable_caption("#MotoGP #Marc") is False
    assert has_usable_caption("#F1 #Ferrari #Leclerc #Monaco") is False


def test_has_usable_caption_mentions_and_emojis():
    assert has_usable_caption("@user 🔥🔥") is False
    assert has_usable_caption("@motogp @marcmarquez93 🏁🏍️🏆") is False


def test_has_usable_caption_short_attribution():
    assert has_usable_caption("via @x") is False
    assert has_usable_caption("Credit: @source") is False


def test_has_usable_caption_real_sentence():
    assert has_usable_caption("Marc Márquez returns to victory at Aragon after 1043 days.") is True
    assert has_usable_caption("Incredible overtake on the final lap to claim the world championship!") is True
    # Real sentence with hashtags and emojis mixed in
    assert has_usable_caption("Marc Márquez takes P1 in MotoGP sprint race! 🔥 #MotoGP #MM93 @motogp") is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. describe_image_for_caption
# ─────────────────────────────────────────────────────────────────────────────

def test_describe_image_for_caption_good_json():
    dummy_payload = {
        "visible_text": "WHAT I THINK",
        "description": "Marc Marquez celebrating in bed",
        "people": [{"name": "Marc Marquez", "confidence": 0.98}],
        "is_meme": True,
    }
    with patch("app.services.caption_grounding._to_datauri", return_value="data:image/jpeg;base64,abc"), \
         patch("app.services.design_images._vision_chat", return_value=json.dumps(dummy_payload)) as mock_vchat:
        res = describe_image_for_caption(b"fake_bytes", niche_hint="MotoGP")
        assert res == dummy_payload
        assert mock_vchat.called
        assert mock_vchat.call_args[1]["context"] == "caption_grounding"
        assert mock_vchat.call_args[1]["max_tokens"] == 800


def test_describe_image_for_caption_markdown_wrapped_json():
    dummy_payload = {
        "visible_text": "TEXT",
        "description": "Description",
        "people": [],
        "is_meme": False,
    }
    raw = f"```json\n{json.dumps(dummy_payload)}\n```"
    with patch("app.services.caption_grounding._to_datauri", return_value="data:image/jpeg;base64,abc"), \
         patch("app.services.design_images._vision_chat", return_value=raw):
        res = describe_image_for_caption(b"fake_bytes")
        assert res == dummy_payload


def test_describe_image_for_caption_garbage_returns_none():
    with patch("app.services.caption_grounding._to_datauri", return_value="data:image/jpeg;base64,abc"), \
         patch("app.services.design_images._vision_chat", return_value="I am unable to analyze this image."):
        res = describe_image_for_caption(b"fake_bytes")
        assert res is None


def test_describe_image_for_caption_exception_returns_none():
    with patch("app.services.caption_grounding._to_datauri", return_value="data:image/jpeg;base64,abc"), \
         patch("app.services.design_images._vision_chat", side_effect=RuntimeError("connection dropped")):
        res = describe_image_for_caption(b"fake_bytes")
        assert res is None


def test_describe_image_for_caption_empty_input_returns_none():
    assert describe_image_for_caption(None) is None
    assert describe_image_for_caption("") is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. build_caption_prompt criteria and image_description block
# ─────────────────────────────────────────────────────────────────────────────

def test_build_caption_prompt_contains_three_rule_lines():
    fp = SimpleNamespace(
        name="Agung",
        caption_tone="engaging",
        caption_language="en",
        caption_max_length=500,
        caption_hashtag_count=5,
        caption_must_include=[],
        caption_must_avoid=[],
        caption_cta_text="",
        use_attribution=True,
        caption_attribution_template="via @{source_username}",
        attribution_position="caption_end",
        caption_custom_prompt="",
    )
    prompt = build_caption_prompt(fp, "tsering9393_", "A normal post caption about racing.")

    # 3 mandatory rules from step 1
    assert '- "Agung" is only the name of this Facebook page. It is NOT the topic. Never write about it as a place, person or thing.' in prompt
    assert "- Use ONLY facts stated in the ORIGINAL POST CONTEXT (and the IMAGE DESCRIPTION if one is given). Never invent people, places, numbers, dates, results or events. If something is not stated, leave it out." in prompt
    assert "- The source account @tsering9393_ is only credited. Do not treat its name as a person in the story." in prompt
    assert "IMAGE DESCRIPTION (from a vision check of the post image):" not in prompt


def test_build_caption_prompt_with_image_description_and_meme():
    fp = SimpleNamespace(
        name="Agung",
        caption_tone="engaging",
        caption_language="en",
        caption_max_length=500,
        caption_hashtag_count=5,
        caption_must_include=[],
        caption_must_avoid=[],
        caption_cta_text="",
        use_attribution=True,
        caption_attribution_template="via @{source_username}",
        attribution_position="caption_end",
        caption_custom_prompt="",
    )
    img_desc = {
        "visible_text": "WHAT PEOPLE THINK WHEN THEY SAY PRINCE",
        "description": "Three Disney princes on top, Marc Marquez sleeping in bed below",
        "people": [
            {"name": "Marc Marquez", "confidence": 0.95},
            {"name": "Prince Charming", "confidence": 0.60},
        ],
        "is_meme": True,
    }
    prompt = build_caption_prompt(fp, "tsering9393_", "", image_description=img_desc)

    assert "IMAGE DESCRIPTION (from a vision check of the post image):" in prompt
    assert '- Visible text: "WHAT PEOPLE THINK WHEN THEY SAY PRINCE"' in prompt
    assert "- What is visible: Three Disney princes on top, Marc Marquez sleeping in bed below" in prompt
    assert "- People: Marc Marquez" in prompt
    assert "Prince Charming" not in prompt  # confidence < 0.8
    assert "- This is a meme or joke post: write a short, light caption that reacts to the joke. Repeat the visible text if helpful. Do not turn it into a news story." in prompt


def test_build_caption_prompt_with_image_description_not_meme():
    fp = SimpleNamespace(
        name="Agung",
        caption_tone="engaging",
        caption_language="en",
        caption_max_length=500,
        caption_hashtag_count=5,
        caption_must_include=[],
        caption_must_avoid=[],
        caption_cta_text="",
        use_attribution=False,
        caption_attribution_template="",
        attribution_position="caption_end",
        caption_custom_prompt="",
    )
    img_desc = {
        "visible_text": "",
        "description": "Motorcycle on track during sunny practice session",
        "people": [],
        "is_meme": False,
    }
    prompt = build_caption_prompt(fp, "user", "original", image_description=img_desc)

    assert "IMAGE DESCRIPTION (from a vision check of the post image):" in prompt
    assert "- People: not identified" in prompt
    assert "This is a meme or joke post" not in prompt


# ─────────────────────────────────────────────────────────────────────────────
# 4. ai_generator task tests
# ─────────────────────────────────────────────────────────────────────────────

def _make_stubs(original_caption="", publish_mode=PublishMode.auto, niches=None):
    job = SimpleNamespace(
        id=12719,
        post_id=10,
        fanpage_id=5,
        status=PublishJobStatus.pending_caption,
        ai_generated_caption=None,
        ai_provider_used=None,
        last_error=None,
    )
    post = SimpleNamespace(
        id=10,
        ig_source_id=20,
        original_caption=original_caption,
        image_local_paths=["/tmp/meme.jpg"],
        image_public_urls=["https://example.com/meme.jpg"],
    )
    fanpage = SimpleNamespace(
        id=5,
        name="Agung",
        publish_mode=publish_mode,
        mode2_gallery_niches=niches or ["MotoGP"],
        use_attribution=True,
        caption_attribution_template="via @{source_username}",
        caption_tone="engaging",
        caption_language="en",
        caption_max_length=500,
        caption_hashtag_count=5,
        caption_must_include=[],
        caption_must_avoid=[],
        caption_cta_text="",
        attribution_position="caption_end",
        caption_custom_prompt="",
    )
    ig_source = SimpleNamespace(id=20, ig_username="tsering9393_")

    db = MagicMock()
    def fake_query(model):
        name = getattr(model, "__name__", str(model))
        q = MagicMock()
        if name == "PublishJob":
            q.filter_by.return_value.first.return_value = job
        elif name == "Post":
            q.filter_by.return_value.first.return_value = post
        elif name == "TargetFanpage":
            q.filter_by.return_value.first.return_value = fanpage
        elif name == "IGSource":
            q.filter_by.return_value.first.return_value = ig_source
        return q

    db.query.side_effect = fake_query
    return job, post, fanpage, ig_source, db


def test_ai_generator_empty_caption_vision_ok():
    job, post, fanpage, ig_source, db = _make_stubs(original_caption="")

    img_desc = {
        "visible_text": "WHAT I THINK",
        "description": "Marc Marquez in bed meme",
        "people": [{"name": "Marc Marquez", "confidence": 0.99}],
        "is_meme": True,
    }

    with patch("app.tasks.ai_generator.SessionLocal", return_value=db), \
         patch("os.path.isfile", return_value=True), \
         patch("app.services.caption_grounding.describe_image_for_caption", return_value=img_desc) as mock_desc, \
         patch("app.services.ai_caption.generate_caption", return_value=("Meme reaction caption", "gemini")) as mock_gen, \
         patch("app.tasks.ai_generator._gap_since_last_publish", return_value=0), \
         patch("app.tasks.publisher.publish_job.apply_async"):
        generate_caption_for_job(job.id)

    assert mock_desc.called
    assert mock_gen.called
    received_prompt = mock_gen.call_args[0][0]
    assert "IMAGE DESCRIPTION (from a vision check of the post image):" in received_prompt
    assert '- Visible text: "WHAT I THINK"' in received_prompt
    assert "- People: Marc Marquez" in received_prompt
    assert job.ai_generated_caption == "Meme reaction caption"
    assert job.status == PublishJobStatus.pending_publish


def test_ai_generator_empty_caption_vision_none_fails_closed():
    job, post, fanpage, ig_source, db = _make_stubs(original_caption="", publish_mode=PublishMode.auto, niches=["MotoGP"])

    with patch("app.tasks.ai_generator.SessionLocal", return_value=db), \
         patch("os.path.isfile", return_value=True), \
         patch("app.services.caption_grounding.describe_image_for_caption", return_value=None), \
         patch("app.services.ai_caption.generate_caption") as mock_gen:
        generate_caption_for_job(job.id)

    # AI copywriter must NOT be called
    assert not mock_gen.called
    # Status forced to pending_review even though publish_mode is auto
    assert job.status == PublishJobStatus.pending_review
    assert job.last_error == "caption needs review: source caption empty and image check failed"
    # Minimal safe caption
    assert "via @tsering9393_" in job.ai_generated_caption
    assert "#MotoGP" in job.ai_generated_caption


def test_ai_generator_normal_caption_regression_no_vision():
    job, post, fanpage, ig_source, db = _make_stubs(
        original_caption="Marc Marquez delivers a dominant masterclass to win the Aragon Grand Prix."
    )

    with patch("app.tasks.ai_generator.SessionLocal", return_value=db), \
         patch("app.services.caption_grounding.describe_image_for_caption") as mock_desc, \
         patch("app.services.ai_caption.generate_caption", return_value=("Standard caption", "gemini")) as mock_gen, \
         patch("app.tasks.ai_generator._gap_since_last_publish", return_value=0), \
         patch("app.tasks.publisher.publish_job.apply_async"):
        generate_caption_for_job(job.id)

    # Vision must NOT be invoked for a post with a usable caption
    assert not mock_desc.called
    assert mock_gen.called
    received_prompt = mock_gen.call_args[0][0]
    assert "IMAGE DESCRIPTION (from a vision check of the post image):" not in received_prompt
    assert job.ai_generated_caption == "Standard caption"
    assert job.status == PublishJobStatus.pending_publish
