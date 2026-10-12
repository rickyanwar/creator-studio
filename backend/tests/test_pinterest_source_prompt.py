import pytest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from app.services.pinterest_source import collect_new_candidates, build_idea_from_candidate


def test_pinterest_source_builds_search_prompt_from_pinterest_custom_prompt():
    fp = SimpleNamespace(
        id=42,
        name="Fight Fanpage",
        mode2_gallery_niches=["combat sports"],
        pinterest_custom_prompt="ONLY elite-level UFC/boxing",
        caption_custom_prompt="Global caption persona should not be here",
    )
    with patch("app.services.news_copywriter._generate_with_fallback", return_value=(("Jon Jones",), "gemini")) as mock_ai:
        from app.services.news_copywriter import generate_pinterest_search_keyword
        keyword = generate_pinterest_search_keyword(fp)
        assert keyword == "Jon Jones"
        called_prompt = mock_ai.call_args[0][0]
        assert "ONLY elite-level UFC/boxing" in called_prompt
        assert "Global caption persona should not be here" not in called_prompt


def test_pinterest_source_process_candidate_uses_pinterest_custom_prompt():
    fp = SimpleNamespace(
        id=42,
        name="Fight Fanpage",
        mode2_gallery_niches=["combat sports"],
        pinterest_custom_prompt="ONLY elite-level UFC/boxing",
        caption_custom_prompt="Global caption persona should not be here",
        pinterest_allow_low_quality=False,
    )
    candidate = SimpleNamespace(
        image_url="http://example.com/test.jpg",
        description="A great fight image",
    )
    item = SimpleNamespace(
        local_path="/tmp/fake_pin_image.jpg",
        source_url="http://example.com/test.jpg",
        filename="fake.jpg",
        width=800,
        height=800,
        label=None,
    )

    db = MagicMock()
    with patch("app.services.image_downloader._fetch_and_store", return_value=[item]), \
         patch("app.services.pinterest_source.Path.read_bytes", return_value=b"fakebytes"), \
         patch("app.services.design_images._is_low_quality_photo", return_value=False), \
         patch("app.services.design_images.vision_has_watermark", return_value=False), \
         patch("app.services.design_images.vision_check_photo_quality", return_value=True), \
         patch("app.services.design_images.vision_check_pin_description") as mock_check, \
         patch("app.models.gallery.GalleryImage"), \
         patch("app.models.pinterest_content_ideas.PinterestContentIdea"):

        mock_check.return_value = {"valid": True, "title": "UFC Title", "description": "Fight desc"}
        res = build_idea_from_candidate(db, fp, candidate, "curated")
        assert res is not None
        mock_check.assert_called_once()
        # custom_prompt arg is 4th argument (index 3)
        called_custom_prompt = mock_check.call_args[0][3]
        assert called_custom_prompt == "ONLY elite-level UFC/boxing"
        assert "Global caption persona should not be here" not in called_custom_prompt

