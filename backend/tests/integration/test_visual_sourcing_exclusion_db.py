"""D15: a photo one fanpage already used for a story must not be picked again
for another fanpage — exclusion keys are local paths / source URLs."""
from unittest.mock import patch

from app.models.gallery import GalleryImage
from app.services.ig_content_classifier import PostAnalysis
from app.services.visual_sourcing import plan_photos


def _gallery(db, n: int) -> GalleryImage:
    gi = GalleryImage(
        keyword="Max Verstappen",
        extra_keywords=[],
        source_image_url=f"https://example.test/max_{n}.jpg",
        local_path=f"/media/gallery/max/{n}.jpg",
        public_url=f"/media/gallery/max/{n}.jpg",
        width=800,
        height=1200,
        source_engine="bing",
        label="face",
    )
    db.add(gi)
    db.flush()
    return gi


def _analysis() -> PostAnalysis:
    return PostAnalysis(
        type="quote", text="We keep pushing.", speaker="Max Verstappen",
        main_subject="Max Verstappen", secondary_kind="none", secondary="",
        inset_context="", inset_query="", inset_contexts=(), moment_summary="Verstappen on his race", radio_lines=(), weather="dry",
        people=("Max Verstappen",), unique_moment=False,
    )


@patch("app.services.visual_sourcing._vision_chat", return_value="1")
@patch("app.services.visual_sourcing._vision_datauri", return_value="data:image/jpeg;base64,")
@patch("builtins.open")
def test_excluded_local_path_is_not_picked_again(_open, _uri, _chat, db_session):
    used = _gallery(db_session, 1)
    fresh = _gallery(db_session, 2)

    plan = plan_photos(db_session, _analysis(), "solo", "f1", None, {used.local_path})

    assert plan.hero is not None
    assert plan.hero.gallery_id == fresh.id


@patch("app.services.visual_sourcing._vision_chat", return_value="1")
@patch("app.services.visual_sourcing._vision_datauri", return_value="data:image/jpeg;base64,")
@patch("builtins.open")
def test_excluded_source_url_is_not_picked_again(_open, _uri, _chat, db_session):
    used = _gallery(db_session, 3)
    fresh = _gallery(db_session, 4)

    plan = plan_photos(db_session, _analysis(), "solo", "f1", None, {used.source_image_url})

    assert plan.hero is not None
    assert plan.hero.gallery_id == fresh.id
