from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.design_templates import DesignTemplate
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ScrapedArticle
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.tasks.design_renderer import (
    NEWS_STALE_HOURS,
    render_design,
    render_pending_designs,
)
from app.services.pro_poster.art_direction import direct
from app.services.pro_poster.types import (
    ArtDirection,
    EventContext,
    PhotoCheck,
    PhotoSelection,
    PosterBrief,
    Subject,
)


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


class FakeRedis:
    def __init__(self):
        self.data = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    def delete(self, *keys: str):
        count = 0
        for k in keys:
            if self.data.pop(k, None) is not None:
                count += 1
        return count

    def get(self, key: str):
        return self.data.get(key)


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def db_engine():
    engine = create_engine("sqlite:///:memory:")
    PublishJob.__table__.create(bind=engine)
    TargetFanpage.__table__.create(bind=engine)
    ScrapedArticle.__table__.create(bind=engine)
    DesignTemplate.__table__.create(bind=engine)
    return engine


def test_render_pending_designs_stale_and_fresh_jobs(db_engine, fake_redis):
    """30 h old job skipped with stale error and not queued; 2 h old job queued as before."""
    Session = sessionmaker(bind=db_engine)
    init_db = Session()
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    fp = TargetFanpage(
        id=1,
        name="Auto Fanpage",
        mode2_publish_mode=PublishMode.auto,
    )
    job_stale = PublishJob(
        id=101,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.news_content,
        created_at=now_utc - timedelta(hours=30),
    )
    job_fresh = PublishJob(
        id=102,
        fanpage_id=1,
        status=PublishJobStatus.pending_design,
        content_type=ContentType.news_content,
        created_at=now_utc - timedelta(hours=2),
    )
    init_db.add_all([fp, job_stale, job_fresh])
    init_db.commit()
    init_db.close()

    with patch("app.tasks.design_renderer.SessionLocal", side_effect=Session), \
         patch("app.tasks.design_renderer._redis", return_value=fake_redis), \
         patch("app.services.engine_router.render_queue_for", return_value=None), \
         patch("app.tasks.design_renderer.render_design.delay") as mock_render_delay:

        render_pending_designs()

    verify_db = Session()
    refreshed_stale = verify_db.query(PublishJob).filter_by(id=101).first()
    refreshed_fresh = verify_db.query(PublishJob).filter_by(id=102).first()

    # 30 h job marked skipped
    assert refreshed_stale.status == PublishJobStatus.skipped
    assert refreshed_stale.last_error == "stale: news older than 24 h, not rendered"

    # Only 2 h job dispatched
    assert mock_render_delay.call_count == 1
    mock_render_delay.assert_called_once_with(102)
    verify_db.close()


@patch("app.services.pro_poster.pipeline.pro_poster_applies")
@patch("app.services.pro_poster.pipeline.compose_pro_poster")
@patch("app.tasks.design_renderer._maybe_compose_speaker_design")
@patch("app.tasks.design_renderer.select_image_for_job")
@patch("app.tasks.design_renderer.render_card_png")
@patch("app.services.design_images.resolve_template")
@patch("app.tasks.design_renderer.SessionLocal")
def test_render_design_pro_poster_spent_skips_speaker_design(
    mock_session_cls,
    mock_resolve_template,
    mock_render_card_png,
    mock_select_image,
    mock_speaker,
    mock_compose,
    mock_applies,
):
    """When pro_poster_spent is True, _maybe_compose_speaker_design is not called."""
    db = MagicMock()
    mock_session_cls.return_value = db

    job = MagicMock(
        id=501,
        fanpage_id=1,
        design_template_id=1,
        source_article_id=1,
        design_analysis_json={},
        last_image_marker=None,
    )
    fanpage = MagicMock(id=1, accent_color="#FF0000", news_visual_engine="chatgpt")
    article = MagicMock(id=1, scraped_title="Article Title")
    template = MagicMock(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1350,
        category="news",
    )

    def fake_query(model):
        m = MagicMock()
        name = getattr(model, "__name__", str(model))
        if "PublishJob" in name:
            m.filter_by.return_value.first.return_value = job
            m.filter.return_value.update.return_value = 1
        elif "TargetFanpage" in name:
            m.filter_by.return_value.first.return_value = fanpage
        elif "ScrapedArticle" in name:
            m.filter_by.return_value.first.return_value = article
        elif "DesignTemplate" in name:
            m.filter_by.return_value.first.return_value = template
        return m

    db.query.side_effect = fake_query
    mock_resolve_template.return_value = template
    mock_applies.return_value = True

    # pp_res attempts >= 1 and not image -> pro_poster_spent = True
    pp_res = MagicMock(
        attempts=1,
        image=None,
        qa=None,
        fallback_reason="qa rejected",
        brief=MagicMock(badge_label="F1"),
    )
    mock_compose.return_value = pp_res
    mock_select_image.return_value = ("data:image/png;base64,AAAA", None, "marker_1")
    mock_render_card_png.return_value = b"fakepng"

    with patch("app.services.design_images.single_photo_face_fits", return_value=True), \
         patch("app.tasks.design_renderer._release_queued_lock"), \
         patch("pathlib.Path.mkdir"), \
         patch("pathlib.Path.write_bytes"):
        render_design(501)

    mock_speaker.assert_not_called()
    assert mock_select_image.called


@patch("app.services.pro_poster.pipeline.pro_poster_applies")
@patch("app.services.pro_poster.pipeline.compose_pro_poster")
@patch("app.tasks.design_renderer._maybe_compose_speaker_design")
@patch("app.tasks.design_renderer.select_image_for_job")
@patch("app.tasks.design_renderer.render_card_png")
@patch("app.services.design_images.resolve_template")
@patch("app.tasks.design_renderer.SessionLocal")
def test_render_design_pro_poster_engine_unavailable_calls_speaker_design(
    mock_session_cls,
    mock_resolve_template,
    mock_render_card_png,
    mock_select_image,
    mock_speaker,
    mock_compose,
    mock_applies,
):
    """ChatGPT never ran (night or cooldown): the speaker design (Codex) still runs."""
    db = MagicMock()
    mock_session_cls.return_value = db

    job = MagicMock(
        id=501,
        fanpage_id=1,
        design_template_id=1,
        source_article_id=1,
        design_analysis_json={},
        last_image_marker=None,
    )
    fanpage = MagicMock(id=1, accent_color="#FF0000", news_visual_engine="chatgpt")
    article = MagicMock(id=1, scraped_title="Article Title")
    template = MagicMock(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1350,
        category="news",
    )

    def fake_query(model):
        m = MagicMock()
        name = getattr(model, "__name__", str(model))
        if "PublishJob" in name:
            m.filter_by.return_value.first.return_value = job
            m.filter.return_value.update.return_value = 1
        elif "TargetFanpage" in name:
            m.filter_by.return_value.first.return_value = fanpage
        elif "ScrapedArticle" in name:
            m.filter_by.return_value.first.return_value = article
        elif "DesignTemplate" in name:
            m.filter_by.return_value.first.return_value = template
        return m

    db.query.side_effect = fake_query
    mock_resolve_template.return_value = template
    mock_applies.return_value = True

    # attempts == 0 -> nothing was spent
    pp_res = MagicMock(
        attempts=0,
        image=None,
        qa=None,
        fallback_reason="engine: chatgpt not available",
        brief=MagicMock(badge_label="F1"),
    )
    mock_compose.return_value = pp_res
    mock_select_image.return_value = ("data:image/png;base64,AAAA", None, "marker_1")
    mock_render_card_png.return_value = b"fakepng"
    mock_speaker.return_value = None

    with patch("app.services.design_images.single_photo_face_fits", return_value=True), \
         patch("app.tasks.design_renderer._release_queued_lock"), \
         patch("pathlib.Path.mkdir"), \
         patch("pathlib.Path.write_bytes"):
        render_design(501)

    mock_speaker.assert_called_once()


@patch("app.services.pro_poster.pipeline.pro_poster_applies")
@patch("app.services.pro_poster.pipeline.compose_pro_poster")
@patch("app.tasks.design_renderer._maybe_compose_speaker_design")
@patch("app.tasks.design_renderer.select_image_for_job")
@patch("app.tasks.design_renderer.render_card_png")
@patch("app.services.design_images.resolve_template")
@patch("app.tasks.design_renderer.SessionLocal")
def test_render_design_pro_poster_off_calls_speaker_design(
    mock_session_cls,
    mock_resolve_template,
    mock_render_card_png,
    mock_select_image,
    mock_speaker,
    mock_compose,
    mock_applies,
):
    """When pro poster is off, _maybe_compose_speaker_design is called as before."""
    db = MagicMock()
    mock_session_cls.return_value = db

    job = MagicMock(
        id=502,
        fanpage_id=1,
        design_template_id=1,
        source_article_id=1,
        design_analysis_json={},
        last_image_marker=None,
    )
    fanpage = MagicMock(id=1, accent_color="#FF0000", news_visual_engine="chatgpt")
    article = MagicMock(id=1, scraped_title="Article Title")
    template = MagicMock(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1350,
        category="news",
    )

    def fake_query(model):
        m = MagicMock()
        name = getattr(model, "__name__", str(model))
        if "PublishJob" in name:
            m.filter_by.return_value.first.return_value = job
            m.filter.return_value.update.return_value = 1
        elif "TargetFanpage" in name:
            m.filter_by.return_value.first.return_value = fanpage
        elif "ScrapedArticle" in name:
            m.filter_by.return_value.first.return_value = article
        elif "DesignTemplate" in name:
            m.filter_by.return_value.first.return_value = template
        return m

    db.query.side_effect = fake_query
    mock_resolve_template.return_value = template
    mock_applies.return_value = False
    mock_speaker.return_value = None
    mock_select_image.return_value = ("data:image/png;base64,AAAA", None, "marker_1")
    mock_render_card_png.return_value = b"fakepng"

    with patch("app.services.design_images.single_photo_face_fits", return_value=True), \
         patch("app.tasks.design_renderer._release_queued_lock"), \
         patch("pathlib.Path.mkdir"), \
         patch("pathlib.Path.write_bytes"):
        render_design(502)

    mock_speaker.assert_called_once_with(db, fanpage, job, article, mode="news")


def test_art_direction_prompt_avoid_block_last_and_template_clean():
    """Prompt's last block is avoid list with UFC/octagon wording and must_avoid items; template block has no logo line."""
    brief = PosterBrief(
        job_id=123,
        subjects=(Subject(name="Driver", role="hero"),),
        event=EventContext(series="Formula 1", vehicle="Formula 1 car", circuit="Monaco", year=2026),
        story_type="result",
        mood="tense",
        badge_label="F1 NEWS",
        must_avoid=("chequered finish flag", "UFC official wordmark"),
    )
    from app.services.pro_poster.types import PhotoCandidate
    cand = PhotoCandidate(source="gallery", gallery_image_id=1, path_or_url="/images/hero.jpg", role="hero")
    hero = PhotoCheck(
        candidate=cand,
        person="yes",
        event_match="yes",
        obstructed=False,
        watermark=False,
        quality=4,
    )
    selection = PhotoSelection(hero=hero, context=())

    direction = direct(brief, selection)
    prompt = direction.prompt
    blocks = prompt.split("\n\n")

    # Last block starts with "Do NOT include anything below"
    last_block = blocks[-1]
    assert last_block.startswith("Do NOT include anything below (very important):")
    assert "- No official series logo or wordmark anywhere in the image: not on banners, screens, walls, the track, the octagon mat or fence, or podium backdrops (for example F1, Formula 1, FIA, MotoGP, FIM, Dorna, UFC)." in last_block
    assert "- Any text or graphic words in the bottom 42% of the image." in last_block
    assert "- Do not show chequered finish flag." in last_block
    assert "- Do not show UFC official wordmark." in last_block
    assert "- Never mix in another series' cars, bikes or logos." in last_block

    # Template block does not contain series logo line, but contains bottom 42% text zone
    template_block = next(b for b in blocks if b.startswith("Space for the fan page template:"))
    assert "Never show an official series logo" not in template_block
    assert "No official series logo" not in template_block
    assert "Do not show" not in template_block
    assert "The bottom 42% of the image" in template_block


@patch("app.services.pro_poster.pipeline.pro_poster_applies")
@patch("app.services.pro_poster.pipeline.compose_pro_poster")
@patch("app.tasks.design_renderer._maybe_compose_speaker_design")
@patch("app.tasks.design_renderer.select_image_for_job")
@patch("app.tasks.design_renderer.render_card_png")
@patch("app.services.design_images.resolve_template")
@patch("app.tasks.design_renderer.SessionLocal")
def test_render_design_pro_poster_codex_engine_recorded_as_codex(
    mock_session_cls,
    mock_resolve_template,
    mock_render_card_png,
    mock_select_image,
    mock_speaker,
    mock_compose,
    mock_applies,
):
    """When compose_pro_poster returns engine='9router' and an image, job ends with design_engine=='codex'."""
    db = MagicMock()
    mock_session_cls.return_value = db

    job = MagicMock(
        id=503,
        fanpage_id=1,
        design_template_id=1,
        source_article_id=1,
        design_analysis_json={},
        last_image_marker=None,
        design_engine=None,
    )
    fanpage = MagicMock(id=1, accent_color="#FF0000", news_visual_engine="chatgpt")
    article = MagicMock(id=1, scraped_title="Article Title")
    template = MagicMock(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1350,
        category="news",
    )

    def fake_query(model):
        m = MagicMock()
        name = getattr(model, "__name__", str(model))
        if "PublishJob" in name:
            m.filter_by.return_value.first.return_value = job
            m.filter.return_value.update.return_value = 1
        elif "TargetFanpage" in name:
            m.filter_by.return_value.first.return_value = fanpage
        elif "ScrapedArticle" in name:
            m.filter_by.return_value.first.return_value = article
        elif "DesignTemplate" in name:
            m.filter_by.return_value.first.return_value = template
        return m

    db.query.side_effect = fake_query
    mock_resolve_template.return_value = template
    mock_applies.return_value = True

    pp_res = MagicMock(
        attempts=1,
        image=b"valid_raw_image_bytes",
        engine="9router",
        qa=MagicMock(scores={"event": 5, "ai_look": 5}),
        fallback_reason=None,
        brief=MagicMock(badge_label="F1"),
    )
    mock_compose.return_value = pp_res
    mock_render_card_png.return_value = b"fakepng"

    with patch("app.services.design_images.single_photo_face_fits", return_value=True), \
         patch("app.tasks.design_renderer._release_queued_lock"), \
         patch("pathlib.Path.mkdir"), \
         patch("pathlib.Path.write_bytes"):
        render_design(503)

    assert job.design_engine == "codex"
    assert job.last_image_marker == "pro_poster"


@patch("app.services.pro_poster.pipeline.pro_poster_applies")
@patch("app.services.pro_poster.pipeline.compose_pro_poster")
@patch("app.tasks.design_renderer._maybe_compose_speaker_design")
@patch("app.tasks.design_renderer.select_image_for_job")
@patch("app.tasks.design_renderer.render_card_png")
@patch("app.services.design_images.resolve_template")
@patch("app.tasks.design_renderer.SessionLocal")
def test_render_design_pro_poster_gptcli_engine_recorded_as_chatgpt(
    mock_session_cls,
    mock_resolve_template,
    mock_render_card_png,
    mock_select_image,
    mock_speaker,
    mock_compose,
    mock_applies,
):
    """When compose_pro_poster returns engine='gptcli' and an image, job ends with design_engine=='chatgpt'."""
    db = MagicMock()
    mock_session_cls.return_value = db

    job = MagicMock(
        id=504,
        fanpage_id=1,
        design_template_id=1,
        source_article_id=1,
        design_analysis_json={},
        last_image_marker=None,
        design_engine=None,
    )
    fanpage = MagicMock(id=1, accent_color="#FF0000", news_visual_engine="chatgpt")
    article = MagicMock(id=1, scraped_title="Article Title")
    template = MagicMock(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1350,
        category="news",
    )

    def fake_query(model):
        m = MagicMock()
        name = getattr(model, "__name__", str(model))
        if "PublishJob" in name:
            m.filter_by.return_value.first.return_value = job
            m.filter.return_value.update.return_value = 1
        elif "TargetFanpage" in name:
            m.filter_by.return_value.first.return_value = fanpage
        elif "ScrapedArticle" in name:
            m.filter_by.return_value.first.return_value = article
        elif "DesignTemplate" in name:
            m.filter_by.return_value.first.return_value = template
        return m

    db.query.side_effect = fake_query
    mock_resolve_template.return_value = template
    mock_applies.return_value = True

    pp_res = MagicMock(
        attempts=1,
        image=b"valid_raw_image_bytes",
        engine="gptcli",
        qa=MagicMock(scores={"event": 5, "ai_look": 5}),
        fallback_reason=None,
        brief=MagicMock(badge_label="F1"),
    )
    mock_compose.return_value = pp_res
    mock_render_card_png.return_value = b"fakepng"

    with patch("app.services.design_images.single_photo_face_fits", return_value=True), \
         patch("app.tasks.design_renderer._release_queued_lock"), \
         patch("pathlib.Path.mkdir"), \
         patch("pathlib.Path.write_bytes"):
        render_design(504)

    assert job.design_engine == "chatgpt"
    assert job.last_image_marker == "pro_poster"
