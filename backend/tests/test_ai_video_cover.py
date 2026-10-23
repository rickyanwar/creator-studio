import json
import sqlite3
import subprocess
import uuid
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.ai_video import GflowWorker, VideoIdea, VideoJob
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.settings import Settings
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.ai_video_cover import (
    MAX_COVER_LINES,
    build_cover_copy_prompt,
    build_cover_html,
    build_cover_image_prompt,
    fallback_cover_copy,
    parse_cover_copy,
)
from app.services.ai_video_cover_build import apply_cover, prepare_cover
from app.services import ai_video_dispatch
from app.tasks import ai_video_finalize

from sqlalchemy.pool import StaticPool

# SQLite compiles for Postgres types
sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)


@compiles(ARRAY, "sqlite")
def _compile_array(element, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(element, compiler, **kw):
    return "TEXT"


@pytest.fixture
def sqlite_db():
    clips_col = VideoJob.__table__.columns["clips_done"]
    orig_clips = clips_col.server_default
    clips_col.server_default = None

    worker_col = GflowWorker.__table__.columns["running_job_ids"]
    orig_worker = worker_col.server_default
    worker_col.server_default = None

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for t in [
        TargetFanpage.__table__,
        VideoIdea.__table__,
        VideoJob.__table__,
        PublishJob.__table__,
        Settings.__table__,
        GflowWorker.__table__,
    ]:
        t.create(bind=engine, checkfirst=True)

    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        clips_col.server_default = orig_clips
        worker_col.server_default = orig_worker


# 1x1 transparent PNG
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pure function tests
# ─────────────────────────────────────────────────────────────────────────────

def test_fallback_cover_copy():
    # <= MAX_COVER_LINES lines, uppercase, last highlighted
    res = fallback_cover_copy("Why 2026 F1 Wings Move So Fast On The Straights")
    assert len(res["lines"]) <= MAX_COVER_LINES
    assert res["lines"] == [l.upper() for l in res["lines"]]
    assert res["highlight"] == [len(res["lines"]) - 1]

    # Single short word
    res2 = fallback_cover_copy("Wings")
    assert res2["lines"] == ["WINGS"]
    assert res2["highlight"] == [0]

    # Empty string defaults to WATCH
    res3 = fallback_cover_copy("")
    assert res3["lines"] == ["WATCH"]
    assert res3["highlight"] == [0]


def test_parse_cover_copy():
    # Valid json with int highlight converted to list
    raw = '{"lines": ["THE WING", "MOVES NOW"], "highlight": 1, "scene": "f1 car in wind tunnel"}'
    res = parse_cover_copy(raw, "Why Wings Move", strict=True)
    assert res["lines"] == ["THE WING", "MOVES NOW"]
    assert res["highlight"] == [1]
    assert res["scene"] == "f1 car in wind tunnel"

    # Garbage input with strict=False returns fallback
    fallback_res = parse_cover_copy("not valid json at all", "Why Wings Move", strict=False)
    assert len(fallback_res["lines"]) <= MAX_COVER_LINES
    assert fallback_res["highlight"] == [len(fallback_res["lines"]) - 1]

    # Garbage input with strict=True raises ValueError
    with pytest.raises(ValueError, match="cover copy off spec"):
        parse_cover_copy("not valid json at all", "Why Wings Move", strict=True)

    # A full informative question in 3 lines is on spec
    question = '{"lines": ["WHY DOES", "YOUR KNEE KICK", "WHEN TAPPED?"], "highlight": [1]}'
    assert parse_cover_copy(question, "Title", strict=True)["lines"] == ["WHY DOES", "YOUR KNEE KICK", "WHEN TAPPED?"]

    # Off spec lines (> MAX_COVER_LINES lines) with strict=True raises ValueError
    off_spec = '{"lines": ["ONE", "TWO", "THREE", "FOUR", "FIVE"], "highlight": [0]}'
    with pytest.raises(ValueError):
        parse_cover_copy(off_spec, "Title", strict=True)


def test_build_cover_html():
    html = build_cover_html(
        width=1080,
        height=1920,
        background_src="data:image/jpeg;base64,fakebg",
        lines=["FAST", "CARS"],
        highlight=[1],
        highlight_hex="#123456",
        font_src="data:font/ttf;base64,fakefont",
        logo_src="data:image/png;base64,fakelogo",
    )
    assert "#123456" in html
    assert '<img class="logo"' in html
    assert "data:image/png;base64,fakelogo" in html
    assert "window.__coverReady" in html
    assert "FAST" in html
    assert "CARS" in html

    # logo_text branch
    html_text = build_cover_html(
        width=1080,
        height=1920,
        background_src="data:image/jpeg;base64,fakebg",
        lines=["FAST"],
        highlight=[0],
        highlight_hex="#FFE500",
        font_src="data:font/ttf;base64,fakefont",
        logo_text="BRAND_LOGO",
    )
    assert "BRAND_LOGO" in html_text
    assert '<div class="logo txt"' in html_text


def test_default_prompts_byte_for_byte_unchanged():
    title = "Why The Rear Wing Flexes"
    caption = "Aerodynamic downforce explainer"
    niche = "f1"
    recent = ["HOW WINGS WORK"]

    expected_copy = (
        'You write the cover (thumbnail) text for a short vertical explainer video on a f1 fan page.\n'
        'A viewer scrolling past sees the cover for under a second. The cover must tell them at once WHAT the video is about\n'
        'and make them NEED the answer.\n\n'
        'Video title: Why The Rear Wing Flexes\n'
        'Video caption: Aerodynamic downforce explainer\n\n'
        'Write the cover words in the SAME language as the video title.\n'
        'The cover is ONE short question that the video answers, written as a natural, grammatical sentence a curious person\n'
        'would really say out loud: it has its verb and its small words ("WHY DO YOU GET SICK READING IN A CAR?"), never\n'
        'broken shorthand ("WHY SICK READING, NOT DRIVING?"). Keep it as SHORT as a natural sentence allows.\n'
        '- Start with WHY, HOW or WHAT (or the same words in the title\'s language) and end with "?".\n'
        '- Name the subject plainly so it is understood instantly: the body part, the car part, the object. A viewer who\n'
        '  only reads the cover knows the topic. Never a vague teaser ("WHO DECIDED?", "HOW?", "THIS PART…").\n'
        '- Ask about the surprising or strange part, never give the answer.\n'
        '- Who the question is about: use "YOU"/"YOUR" only for everyday things that happen to every viewer\'s own body or\n'
        '  life ("WHY DOES YOUR KNEE KICK WHEN TAPPED?"). In motorsport the question is about the driver, the rider, the car\n'
        '  or the bike, never the viewer: "WHY IS AN F1 DRIVER\'S HEAD 40 KG?", never "WHY DOES YOUR HEAD HIT 40 KILOS?".\n'
        '- 4 to 8 words, everyday words, no jargon the caption does not use. 2 to 4 lines, each at most\n'
        '  14 characters; break lines at natural pauses.\n'
        '- SPECIFIC beats generic: use a number or a sharp contrast from the title or caption when there is one.\n'
        '- Do not reuse a hook from this page\'s recent covers: HOW WINGS WORK\n'
        '- Clickbait never means lying: no claim the video does not support, no invented facts or numbers, no fake\n'
        '  urgency. Use only numbers that appear in the title or caption; keep names, numbers and technical terms exactly as\n'
        '  written there. No worn-out words: "secret", "insane", "shocking", "crazy", "you won\'t believe".\n'
        '- Never end a line on a small word ("the", "a", "of", "le", "la", "de", "yang", ...); no colon, dash or comma at a\n'
        '  line end. No emojis, no hashtags, no quotation marks.\n'
        '- Highlight the ONE line that holds the surprising part (the subject or the strange result).\n\n'
        'Examples:\n'
        '"Why Your Knee Jerks When the Doctor Taps It" -> {"lines": ["WHY DOES", "YOUR KNEE KICK", "WHEN TAPPED?"], "highlight": [1]}\n'
        '"Why Your Stomach Growls: The Wave Inside You" -> {"lines": ["WHY DOES", "YOUR STOMACH", "GROWL?"], "highlight": [1]}\n'
        '"The lever that lifts a Formula 1 car" -> {"lines": ["HOW DOES", "ONE LEVER LIFT", "AN F1 CAR?"], "highlight": [1]}\n'
        '"Reading in the back seat makes you carsick" -> {"lines": ["WHY DO YOU", "GET SICK READING", "IN A CAR?"], "highlight": [1]}\n'
        '"The 40-kilo head: why an F1 driver\'s head gets heavier under braking" -> {"lines": ["WHY IS AN F1", "DRIVER\'S HEAD", "40 KG?"], "highlight": [2]}\n'
        '"The captive wheel nut: the single nut that never leaves the wheel" -> {"lines": ["WHY THIS", "WHEEL NUT", "NEVER COMES OFF?"], "highlight": [1]}\n'
        '"Le turbo : une turbine à 125 000 tours par minute" -> {"lines": ["COMMENT UN TURBO", "TOURNE À", "125 000 TR/MIN ?"], "highlight": [2]}\n\n'
        'Also describe ONE picture for the cover background, in English, for an image model:\n'
        '- a dramatic CLOSE-UP of the one object or moment the video is about, at its most intense instant (glowing, sparking,\n'
        '  straining, frozen just before something happens), from a striking low angle;\n'
        '- close, but the object stays INSTANTLY recognisable: show enough of it and its context (the whole jack under the car\n'
        '  nose, the whole knee with the hammer), never an abstract macro of a detail;\n'
        '- ONE focal point, nothing else competing: no crowds, no wide scenery, no busy cockpit or garage;\n'
        '- a touch of mystery is good (part of it in shadow, the key detail just catching the light);\n'
        '- do NOT describe the background, lighting, colours or style (the brand sets those);\n'
        '- a real-looking motorsport close-up; a car or bike part in a real team\'s colours is fine, but no real driver\'s or rider\'s face; no series logos.\n\n'
        'Reply with ONLY this JSON:\n'
        '{"lines": ["LINE 1", "LINE 2"], "highlight": [index of the highlighted line, 0-based], "scene": "one or two sentences"}'
    )

    actual_copy = build_cover_copy_prompt(title, caption, niche, recent)
    assert actual_copy == expected_copy
    assert build_cover_copy_prompt(title, caption, niche, recent, template_key=None) == expected_copy

    scene = "a rear wing flexing at speed"
    expected_image = (
        'Vertical 9:16 cover image (thumbnail) for a short explainer video titled "Why The Rear Wing Flexes".\n'
        'Scene: a rear wing flexing at speed\n'
        'Style: premium motorsport commercial photo, the subject brightly and clearly lit so every shape and colour reads at a glance on a phone, real team colours, clean darker-toned background, natural textures, no HDR look.\n'
        'Composition: a dramatic close-up of ONE hero subject that fills the LOWER part of the frame, from about 40% to 95% of\n'
        'the height, razor sharp and fully visible, well lit from the front with a rim light and one bright accent (a glow,\n'
        'spark, reflection or hot edge) as the focal point. A viewer must recognise the subject in half a second: no heavy\n'
        'shadows over it, not an abstract macro. Everything around it stays quiet and plain; minimal and uncluttered, no people\'s faces.\n'
        'The UPPER 40% of the frame is calm and simple: the SAME continuous background as the rest, out of focus and slightly\n'
        'darker toward the top, with no objects, so large title text placed there later stays readable. One seamless picture:\n'
        'no band, panel, border, horizontal line or split between the top and the rest.\n'
        'Bold contrast, rich colour, shallow depth of field, physically believable, like a premium commercial still.\n'
        'Do NOT add any text, letters, numbers, captions, watermarks or logos anywhere in the image. Liveries, signs and screens carry plain colour blocks only, never words or brand names. No series logos.'
    )

    actual_image = build_cover_image_prompt(title, scene, niche)
    assert actual_image == expected_image
    assert build_cover_image_prompt(title, scene, niche, template_key=None) == expected_image


def test_motorcycle_commercial_cover_prompts():
    title = "1994 Honda NSR500"
    caption = "Two-stroke GP legend"
    copy_p = build_cover_copy_prompt(title, caption, "motogp", template_key="motorcycle_commercial")

    assert "WHOLE motorcycle" in copy_p
    assert "no deep shadows" in copy_p
    assert "a dramatic CLOSE-UP" not in copy_p
    assert "a touch of mystery" not in copy_p
    assert "a real-looking photo of the whole motorcycle in its real livery" in copy_p

    img_p = build_cover_image_prompt(title, None, "motogp", template_key="motorcycle_commercial")
    assert "WHOLE motorcycle" in img_p or "whole motorcycle, fully visible" in img_p
    assert "no deep shadows" in img_p
    assert "a dramatic close-up" not in img_p.lower()
    assert "a touch of mystery" not in img_p.lower()
    assert "premium motorcycle brochure photo" in img_p


def test_f1_and_health_cover_prompts_unchanged():
    title = "Why Wings Flex"
    f1_copy = build_cover_copy_prompt(title, "caption", "f1", template_key="f1_template")
    assert "a dramatic CLOSE-UP" in f1_copy
    assert "a touch of mystery" in f1_copy
    assert "WHOLE motorcycle" not in f1_copy

    f1_img = build_cover_image_prompt(title, "scene", "f1", template_key="f1_template")
    assert "a dramatic close-up" in f1_img.lower()
    assert "WHOLE motorcycle" not in f1_img
    assert "brochure photo" not in f1_img

    health_copy = build_cover_copy_prompt("Knee Joint", "caption", "health")
    assert "3D anatomy " in health_copy and "everyday situation" in health_copy
    assert "a dramatic CLOSE-UP" in health_copy
    assert "WHOLE motorcycle" not in health_copy

    health_img = build_cover_image_prompt("Knee Joint", "scene", "health")
    assert "3D anatomy render" in health_img
    assert "WHOLE motorcycle" not in health_img


def test_prepare_cover_motorcycle_commercial(sqlite_db):
    fanpage = TargetFanpage(id=12, repliz_account_id="r12", name="Moto Page", ai_video_cover_enabled=True)
    sqlite_db.add(fanpage)
    sqlite_db.commit()

    job = VideoJob(
        id=102,
        fanpage_id=fanpage.id,
        template_key="motorcycle_commercial",
        title="Honda NSR500",
        caption="1994 Two Stroke",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    sqlite_db.add(job)
    sqlite_db.commit()

    fake_copy = {"lines": ["NSR500", "LEGEND"], "highlight": [0], "scene": "Whole bike on stand"}
    with patch("app.services.news_copywriter._generate_with_fallback", return_value=(fake_copy, "router")):
        prepare_cover(sqlite_db, fanpage, job)

    assert job.cover_lines == ["NSR500", "LEGEND"]
    assert job.cover_prompt is not None
    assert "whole motorcycle" in job.cover_prompt.lower()
    assert "brochure photo" in job.cover_prompt


# ─────────────────────────────────────────────────────────────────────────────
# 2. prepare_cover tests
# ─────────────────────────────────────────────────────────────────────────────

def test_prepare_cover_success(sqlite_db):
    fanpage = TargetFanpage(id=10, repliz_account_id="r10", name="FP 10", ai_video_cover_enabled=True)
    sqlite_db.add(fanpage)
    sqlite_db.commit()

    job = VideoJob(
        id=101,
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Why Wings Move Fast",
        caption="Aerodynamic breakdown",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    sqlite_db.add(job)
    sqlite_db.commit()

    fake_copy = {"lines": ["THE WING", "MOVES FAST"], "highlight": [1], "scene": "wind tunnel rear wing"}
    with patch("app.services.news_copywriter._generate_with_fallback", return_value=(fake_copy, "router")):
        prepare_cover(sqlite_db, fanpage, job)

    assert job.cover_lines == ["THE WING", "MOVES FAST"]
    assert job.cover_highlight == [1]
    assert job.cover_prompt is not None
    assert "wind tunnel rear wing" in job.cover_prompt


def test_prepare_cover_fallback_on_llm_error(sqlite_db):
    fanpage = TargetFanpage(id=11, repliz_account_id="r11", name="FP 11", ai_video_cover_enabled=True)
    sqlite_db.add(fanpage)
    sqlite_db.commit()

    job = VideoJob(
        id=102,
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Why Wings Move",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    sqlite_db.add(job)
    sqlite_db.commit()

    with patch("app.services.news_copywriter._generate_with_fallback", side_effect=RuntimeError("router unreachable")):
        prepare_cover(sqlite_db, fanpage, job)

    assert job.cover_lines is not None
    assert len(job.cover_lines) <= 3
    assert job.cover_highlight is not None
    assert job.cover_prompt is not None


def test_prepare_cover_disabled(sqlite_db):
    fanpage = TargetFanpage(id=12, repliz_account_id="r12", name="FP 12", ai_video_cover_enabled=False)
    sqlite_db.add(fanpage)
    sqlite_db.commit()

    job = VideoJob(
        id=103,
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Why Wings Move",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    sqlite_db.add(job)
    sqlite_db.commit()

    prepare_cover(sqlite_db, fanpage, job)
    assert job.cover_lines is None
    assert job.cover_highlight is None
    assert job.cover_prompt is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. apply_cover tests
# ─────────────────────────────────────────────────────────────────────────────

def _generate_test_video(dest: Path, duration: int = 1) -> None:
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=blue:s=180x320:d={duration}",
            "-f", "lavfi", "-i", "anullsrc",
            "-shortest",
            str(dest),
        ],
        check=True,
        timeout=30,
    )


def test_apply_cover_success(tmp_path):
    video = tmp_path / "input.mp4"
    _generate_test_video(video, duration=1)

    workdir = tmp_path / "work"
    job = Mock(
        id=1,
        cover_bg_path=None,
        cover_lines=["FAST", "CAR"],
        cover_highlight=[1],
        title="Fast Car",
    )
    fanpage = Mock(
        ai_video_logo_enabled=False,
        ai_video_caption_highlight="#FFE500",
        ai_video_logo_size_pct=14,
        ai_video_logo_position="top_right",
        ai_video_logo_margin_pct=3,
        watermark_text=None,
    )

    fake_response = Mock()
    fake_response.content = TINY_PNG
    fake_response.raise_for_status = Mock()

    with patch("httpx.post", return_value=fake_response):
        res = apply_cover(job, fanpage, video, video, workdir)

    assert res is not None
    out_video, out_cover = res
    assert out_video.is_file()
    assert out_cover.is_file()
    assert out_cover.name.endswith("_cover.jpg")

    # Check that video has valid duration ~ 1s
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out_video)],
        capture_output=True, text=True, check=True,
    )
    duration = float(probe.stdout.strip())
    assert 0.8 <= duration <= 1.5


def test_apply_cover_renderer_error_preserves_input(tmp_path):
    video = tmp_path / "input.mp4"
    _generate_test_video(video, duration=1)
    original_bytes = video.read_bytes()

    workdir = tmp_path / "work"
    job = Mock(id=2, cover_bg_path=None, cover_lines=["TEST"], cover_highlight=[0], title="Test")
    fanpage = Mock(ai_video_logo_enabled=False, ai_video_caption_highlight="#FFE500")

    with patch("httpx.post", side_effect=httpx.HTTPError("renderer crash")):
        res = apply_cover(job, fanpage, video, video, workdir)

    assert res is None
    assert video.is_file()
    assert video.read_bytes() == original_bytes


# ─────────────────────────────────────────────────────────────────────────────
# 4. complete_job and complete endpoint tests
# ─────────────────────────────────────────────────────────────────────────────

def test_complete_job_with_and_without_cover_bg(sqlite_db, tmp_path, monkeypatch):
    from app.config import get_settings
    cfg = get_settings()
    monkeypatch.setattr(cfg, "storage_base_path", str(tmp_path))
    monkeypatch.setattr(cfg, "storage_base_url", "http://localhost/media")

    fanpage = TargetFanpage(id=20, repliz_account_id="r20", name="FP 20")
    sqlite_db.add(fanpage)
    sqlite_db.commit()

    # Job 1: with cover_bg_content
    job1 = VideoJob(
        id=201,
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job 1",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="generating",
        worker_id="w1",
        generation=1,
    )
    sqlite_db.add(job1)
    sqlite_db.commit()

    with patch("app.tasks.ai_video_finalize.finalize_ai_video.delay"):
        ai_video_dispatch.complete_job(
            sqlite_db,
            job1,
            "w1",
            1,
            b"video_bytes_1",
            b"thumb_bytes_1",
            {"duration_s": 4.0},
            cover_bg_content=b"cover_bg_image_data",
        )

    assert job1.status == "uploaded"
    assert job1.cover_bg_path is not None
    assert Path(job1.cover_bg_path).is_file()
    assert Path(job1.cover_bg_path).read_bytes() == b"cover_bg_image_data"

    # Job 2: without cover_bg_content
    job2 = VideoJob(
        id=202,
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job 2",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="generating",
        worker_id="w1",
        generation=1,
    )
    sqlite_db.add(job2)
    sqlite_db.commit()

    with patch("app.tasks.ai_video_finalize.finalize_ai_video.delay"):
        ai_video_dispatch.complete_job(
            sqlite_db,
            job2,
            "w1",
            1,
            b"video_bytes_2",
            b"thumb_bytes_2",
            {"duration_s": 4.0},
        )

    assert job2.status == "uploaded"
    assert job2.cover_bg_path is None


# ─────────────────────────────────────────────────────────────────────────────
# 5. finalize tests (cover success vs None)
# ─────────────────────────────────────────────────────────────────────────────

def test_finalize_ai_video_cover_success(sqlite_db, tmp_path, monkeypatch):
    from app.config import get_settings
    cfg = get_settings()
    monkeypatch.setattr(cfg, "storage_base_path", str(tmp_path))
    monkeypatch.setattr(cfg, "storage_base_url", "http://localhost/media")

    fanpage = TargetFanpage(
        id=30, repliz_account_id="r30", name="FP 30",
        ai_video_publish_mode=PublishMode.manual_review,
        ai_video_cover_enabled=True,
        ai_video_logo_enabled=False,
        ai_video_captions_enabled=False,
    )
    sqlite_db.add(fanpage)
    sqlite_db.commit()

    video = tmp_path / "raw_video.mp4"
    _generate_test_video(video, duration=1)
    thumb = tmp_path / "raw_thumb.jpg"
    thumb.write_bytes(b"thumb")

    job = VideoJob(
        id=301,
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Finalize Test",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="uploaded",
        video_path=str(video),
        video_url="http://localhost/media/raw_video.mp4",
        thumbnail_path=str(thumb),
        thumbnail_url="http://localhost/media/raw_thumb.jpg",
        duration_s=1.0,
    )
    sqlite_db.add(job)
    sqlite_db.commit()

    covered_video = tmp_path / "covered.mp4"
    covered_video.write_bytes(b"covered")
    cover_jpg = tmp_path / "cover.jpg"
    cover_jpg.write_bytes(b"cover_jpg_bytes")

    with patch("app.tasks.ai_video_finalize.SessionLocal", return_value=sqlite_db), \
         patch("app.services.ai_video_qc.technical_check", return_value={"passed": True, "reasons": []}), \
         patch("app.services.ai_video_cover_build.apply_cover", return_value=(covered_video, cover_jpg)):
        ai_video_finalize.finalize_ai_video(job.id)

    publish_job = sqlite_db.query(PublishJob).filter_by(content_type=ContentType.ai_video, fanpage_id=fanpage.id).first()
    assert publish_job is not None
    assert publish_job.video_thumbnail_path == str(cover_jpg)
    assert publish_job.video_path == str(covered_video)


def test_finalize_ai_video_cover_none_keeps_old_behavior(sqlite_db, tmp_path, monkeypatch):
    from app.config import get_settings
    cfg = get_settings()
    monkeypatch.setattr(cfg, "storage_base_path", str(tmp_path))
    monkeypatch.setattr(cfg, "storage_base_url", "http://localhost/media")

    fanpage = TargetFanpage(
        id=31, repliz_account_id="r31", name="FP 31",
        ai_video_publish_mode=PublishMode.manual_review,
        ai_video_cover_enabled=True,
        ai_video_logo_enabled=False,
        ai_video_captions_enabled=False,
    )
    sqlite_db.add(fanpage)
    sqlite_db.commit()

    video = tmp_path / "raw_video_2.mp4"
    _generate_test_video(video, duration=1)
    thumb = tmp_path / "raw_thumb_2.jpg"
    thumb.write_bytes(b"thumb2")

    job = VideoJob(
        id=302,
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Finalize Fallback Test",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="uploaded",
        video_path=str(video),
        video_url="http://localhost/media/raw_video_2.mp4",
        thumbnail_path=str(thumb),
        thumbnail_url="http://localhost/media/raw_thumb_2.jpg",
        duration_s=1.0,
    )
    sqlite_db.add(job)
    sqlite_db.commit()

    with patch("app.tasks.ai_video_finalize.SessionLocal", return_value=sqlite_db), \
         patch("app.services.ai_video_qc.technical_check", return_value={"passed": True, "reasons": []}), \
         patch("app.services.ai_video_cover_build.apply_cover", return_value=None):
        ai_video_finalize.finalize_ai_video(job.id)

    publish_job = sqlite_db.query(PublishJob).filter_by(content_type=ContentType.ai_video, fanpage_id=fanpage.id).first()
    assert publish_job is not None
    # Falls back to old behaviour: raw video and raw thumbnail
    assert publish_job.video_path == str(video)
    assert publish_job.video_thumbnail_path == str(thumb)


def test_finalize_ai_video_branded_branch_cover_success(sqlite_db, tmp_path, monkeypatch):
    from app.config import get_settings
    cfg = get_settings()
    monkeypatch.setattr(cfg, "storage_base_path", str(tmp_path))
    monkeypatch.setattr(cfg, "storage_base_url", "http://localhost/media")

    fanpage = TargetFanpage(
        id=32, repliz_account_id="r32", name="FP 32",
        ai_video_publish_mode=PublishMode.manual_review,
        ai_video_cover_enabled=True,
        ai_video_logo_enabled=True,
        ai_video_captions_enabled=False,
    )
    sqlite_db.add(fanpage)
    sqlite_db.commit()

    video = tmp_path / "raw_branded_in.mp4"
    _generate_test_video(video, duration=1)
    thumb = tmp_path / "raw_thumb_branded.jpg"
    thumb.write_bytes(b"thumb3")

    job = VideoJob(
        id=303,
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Finalize Branded Test",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="uploaded",
        video_path=str(video),
        video_url="http://localhost/media/raw_branded_in.mp4",
        thumbnail_path=str(thumb),
        thumbnail_url="http://localhost/media/raw_thumb_branded.jpg",
        duration_s=1.0,
    )
    sqlite_db.add(job)
    sqlite_db.commit()

    def _fake_render_branded(src, out, fp, words, workdir):
        Path(out).write_bytes(b"branded_video_bytes")

    covered_video = tmp_path / "covered_branded.mp4"
    covered_video.write_bytes(b"covered_branded")
    cover_jpg = tmp_path / "cover_branded.jpg"
    cover_jpg.write_bytes(b"cover_jpg_bytes")

    with patch("app.tasks.ai_video_finalize.SessionLocal", return_value=sqlite_db), \
         patch("app.services.ai_video_qc.technical_check", return_value={"passed": True, "reasons": []}), \
         patch("app.services.speech_words.caption_words", return_value=[]), \
         patch("app.services.ai_video_overlay.speech_windows", return_value=[]), \
         patch("app.services.ai_video_overlay.estimate_words", return_value=[]), \
         patch("app.services.ai_video_overlay.render_branded", side_effect=_fake_render_branded), \
         patch("app.services.ai_video_cover_build.apply_cover", return_value=(covered_video, cover_jpg)):
        ai_video_finalize.finalize_ai_video(job.id)

    publish_job = sqlite_db.query(PublishJob).filter_by(content_type=ContentType.ai_video, fanpage_id=fanpage.id).first()
    assert publish_job is not None
    assert publish_job.video_thumbnail_path == str(cover_jpg)
    assert publish_job.video_path == str(covered_video)


def test_complete_endpoint_cover_bg_too_large_ignored(sqlite_db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api.deps import get_db, require_hermes_scope
    from app.api.gflow_worker import MAX_THUMB_BYTES
    from app.config import get_settings

    cfg = get_settings()
    monkeypatch.setattr(cfg, "storage_base_path", str(tmp_path))
    monkeypatch.setattr(cfg, "storage_base_url", "http://localhost/media")

    fanpage = TargetFanpage(id=40, repliz_account_id="r40", name="FP 40")
    sqlite_db.add(fanpage)
    sqlite_db.commit()

    job = VideoJob(
        id=401,
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Job 401",
        caption="Cap",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="generating",
        worker_id="w1",
        generation=1,
    )
    sqlite_db.add(job)
    sqlite_db.commit()

    app.dependency_overrides[get_db] = lambda: sqlite_db
    client = TestClient(app)

    try:
        # cover_bg too large (> MAX_THUMB_BYTES * 2) should NOT cause 413; it is ignored with warning
        too_big_cover = b"x" * (MAX_THUMB_BYTES * 2 + 10)
        meta_json = json.dumps({"duration_s": 4.0})

        with patch("app.services.api_tokens.verify", return_value=Mock(scopes=["gflow:worker"], last_used_at=None)), \
             patch("app.tasks.ai_video_finalize.finalize_ai_video.delay"):
            res = client.post(
                f"/worker/gflow/jobs/{job.id}/complete",
                headers={"Authorization": "Bearer test_token"},
                data={"worker_id": "w1", "generation": "1", "meta": meta_json},
                files={
                    "video": ("video.mp4", b"valid_video", "video/mp4"),
                    "thumbnail": ("thumb.jpg", b"valid_thumb", "image/jpeg"),
                    "cover_bg": ("cover.jpg", too_big_cover, "image/jpeg"),
                },
            )
        assert res.status_code == 200
        assert res.json()["status"] == "uploaded"
        sqlite_db.refresh(job)
        # cover_bg was ignored because it was too large
        assert job.cover_bg_path is None
    finally:
        app.dependency_overrides.pop(get_db, None)



# Owner 2026-10-24: natural, short questions; never "YOUR" for motorsport; bikes show their name and year.
from app.services.ai_video_cover import build_cover_copy_prompt, motorcycle_cover_copy


def test_cover_prompt_asks_for_natural_short_questions_about_the_driver():
    prompt = build_cover_copy_prompt("The 40-kilo head", "", "f1")
    assert "natural, grammatical sentence" in prompt and "as SHORT as a natural sentence allows" in prompt
    assert "never the viewer" in prompt and "WHY IS AN F1 DRIVER'S HEAD 40 KG?" in prompt


def test_motorcycle_cover_is_the_bike_name_and_year():
    assert motorcycle_cover_copy("Honda RC166 (1966): the six-cylinder 250cc") == {"lines": ["HONDA RC166", "(1966)"], "highlight": [0]}
    assert motorcycle_cover_copy("1993 Honda NSR500: The Two-Stroke V4") == {"lines": ["HONDA NSR500", "(1993)"], "highlight": [0]}
    assert motorcycle_cover_copy("Ducati Desmosedici GP7 (2007) - Stoner")["lines"] == ["DUCATI", "DESMOSEDICI GP7", "(2007)"]
    assert motorcycle_cover_copy("Why the 2027 rules change bikes") is None
