from unittest.mock import MagicMock, patch

import pytest
import types

from app.models.publish_jobs import PublishJobStatus
from app.services.ai_caption import build_caption_prompt
from app.services.fanpage_caption import strip_credit_lines
from app.tasks.ai_generator import generate_caption_for_job


@pytest.mark.parametrize(
    "credit_line",
    [
        "Credit: @moto_indonesia_gp",
        "Via @tsering9393_",
        "via @x",
        "Source: RacingNews365 F1",
        "Sumber: Motosan",
        "📸: @someone",
        "cr: @someone",
        "Credits - @team",
    ],
)
def test_strip_credit_lines_removes_credit_lines(credit_line):
    # Arrange
    caption = f"Leading story text.\n\n{credit_line}\n\n#Tag1 #Tag2"

    # Act
    result = strip_credit_lines(caption)

    # Assert
    assert credit_line not in result
    assert result == "Leading story text.\n\n#Tag1 #Tag2"


@pytest.mark.parametrize(
    "kept_line",
    [
        "Via the new rule, teams must pit twice.",
        "Marc Marquez thanked @marcmarquez93 fans",
        "#MarcMarquez #MotoGP",
    ],
)
def test_strip_credit_lines_keeps_normal_content(kept_line):
    # Arrange
    caption = f"Header line.\n\n{kept_line}"

    # Act
    result = strip_credit_lines(caption)

    # Assert
    assert result == f"Header line.\n\n{kept_line}"


def test_strip_credit_lines_full_caption():
    # Arrange
    caption = "Hook line.\n\nBody text.\n\nCredit: @moto_indonesia_gp\n\n#MarcMarquez #MotoGP"

    # Act
    result = strip_credit_lines(caption)

    # Assert
    assert result == "Hook line.\n\nBody text.\n\n#MarcMarquez #MotoGP"


def test_strip_credit_lines_empty_input():
    # Arrange & Act & Assert
    assert strip_credit_lines("") == ""
    assert strip_credit_lines(None) is None


def test_build_caption_prompt_without_attribution():
    # Arrange
    fanpage = types.SimpleNamespace(
        name="GP Racing",
        use_attribution=False,
        caption_attribution_template="via @{source_username}",
    )

    # Act
    prompt = build_caption_prompt(fanpage, "motogp", "Original caption")

    # Assert
    assert "Add no credit line" in prompt
    assert "is only credited" not in prompt


def test_build_caption_prompt_with_attribution():
    # Arrange
    fanpage = types.SimpleNamespace(
        name="GP Racing",
        use_attribution=True,
        caption_attribution_template="via @{source_username}",
    )

    # Act
    prompt = build_caption_prompt(fanpage, "motogp", "Original caption")

    # Assert
    assert "is only credited" in prompt


def test_ai_generator_strips_credit_when_attribution_disabled():
    # Arrange
    job = types.SimpleNamespace(
        id=1,
        post_id=10,
        fanpage_id=5,
        status=PublishJobStatus.pending_caption,
        ai_generated_caption=None,
        ai_provider_used=None,
        last_error=None,
    )
    post = types.SimpleNamespace(
        id=10,
        ig_source_id=20,
        original_caption="Marc Marquez claims pole position.",
    )
    fanpage = types.SimpleNamespace(
        id=5,
        name="GP Racing",
        publish_mode=types.SimpleNamespace(value="manual"),
        use_attribution=False,
    )
    ig_source = types.SimpleNamespace(id=20, ig_username="motogp")

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
    raw_caption = "Marc Marquez claims pole.\n\nCredit: @motogp\n\n#MotoGP"

    # Act
    with patch("app.tasks.ai_generator.SessionLocal", return_value=db), \
         patch("app.services.ai_caption.generate_caption", return_value=(raw_caption, "groq")):
        generate_caption_for_job(job.id)

    # Assert
    assert job.ai_generated_caption == "Marc Marquez claims pole.\n\n#MotoGP"


def test_ai_generator_keeps_attribution_when_enabled():
    # Arrange
    job = types.SimpleNamespace(
        id=1,
        post_id=10,
        fanpage_id=5,
        status=PublishJobStatus.pending_caption,
        ai_generated_caption=None,
        ai_provider_used=None,
        last_error=None,
    )
    post = types.SimpleNamespace(
        id=10,
        ig_source_id=20,
        original_caption="Marc Marquez claims pole position.",
    )
    fanpage = types.SimpleNamespace(
        id=5,
        name="GP Racing",
        publish_mode=types.SimpleNamespace(value="manual"),
        use_attribution=True,
    )
    ig_source = types.SimpleNamespace(id=20, ig_username="motogp")

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
    raw_caption = "Marc Marquez claims pole.\n\nCredit: @motogp\n\n#MotoGP"

    # Act
    with patch("app.tasks.ai_generator.SessionLocal", return_value=db), \
         patch("app.services.ai_caption.generate_caption", return_value=(raw_caption, "groq")):
        generate_caption_for_job(job.id)

    # Assert
    assert job.ai_generated_caption == raw_caption


def test_facebook_photo_consume_one_strips_credit_lines():
    # Arrange
    fanpage = types.SimpleNamespace(
        id=5,
        name="GP Racing",
        publish_mode="manual",
        facebook_photo_publish_mode="manual",
        use_attribution=True,
    )
    idea = types.SimpleNamespace(
        id=101,
        fanpage_id=5,
        status="pending",
        design_title="Title",
        design_subtitle="Subtitle",
        design_caption="Caption",
        gallery_image_id=None,
        category="general",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = idea
    raw_caption = "Inspiring quote.\n\n📸: @someone\n\n#Motorsport"

    # Act
    with patch("app.services.ai_caption.generate_caption", return_value=(raw_caption, "router")), \
         patch("app.services.design_images.resolve_template", return_value=None):
        from app.tasks.facebook_photo import _consume_one

        success = _consume_one(db, fanpage)

    # Assert
    assert success is True
    assert db.add.called
    added_job = db.add.call_args[0][0]
    assert added_job.ai_generated_caption == "Inspiring quote.\n\n#Motorsport"

