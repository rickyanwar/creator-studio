"""Owner 2026-11-01: motorcycle covers show the same bike as the video, with its real livery and sponsor logos."""
import json
import sqlite3
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.ai_video import VideoIdea, VideoJob
from app.models.target_fanpages import TargetFanpage
from app.services.ai_video_cover import (
    DEFAULT_TEXT_RULES,
    MOTORCYCLE_TEXT_RULES,
    build_cover_image_prompt,
    cover_bike_description,
    motorcycle_cover_copy,
)
from app.services.ai_video_cover_build import prepare_cover

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)


@compiles(ARRAY, "sqlite")
def _compile_array(element, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(element, compiler, **kw):
    return "TEXT"


ROTHMANS_BIKE = (
    "The factory 1990 Honda NSR500 in Rothmans Honda livery: a predominantly dark-blue fairing with white, red and "
    "gold accents and the Rothmans script across the fairing sides."
)
NSR_TITLE = "The 1990 Honda NSR500: the Rothmans V4 behind the greatest home win"


@pytest.fixture
def sqlite_db():
    clips_col = VideoJob.__table__.columns["clips_done"]
    orig_clips = clips_col.server_default
    clips_col.server_default = None
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in [TargetFanpage.__table__, VideoIdea.__table__, VideoJob.__table__]:
        table.create(bind=engine, checkfirst=True)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        clips_col.server_default = orig_clips


# ─────────────────────────────────────────────────────────────────────────────
# build_cover_image_prompt
# ─────────────────────────────────────────────────────────────────────────────

def test_motorcycle_cover_with_bike_uses_the_video_bike_and_real_livery():
    prompt = build_cover_image_prompt(NSR_TITLE, "a copywriter scene of some other bike", "motogp",
                                      template_key="motorcycle_commercial", bike=ROTHMANS_BIKE)
    assert ROTHMANS_BIKE in prompt
    assert "a copywriter scene of some other bike" not in prompt
    assert "paddock stands" in prompt
    assert "team garage" in prompt
    assert MOTORCYCLE_TEXT_RULES in prompt
    assert "plain colour blocks" not in prompt
    assert DEFAULT_TEXT_RULES not in prompt


def test_motorcycle_cover_bike_is_cleaned_and_capped():
    messy = "  The  1990 Honda\n NSR500   in Rothmans livery  "
    prompt = build_cover_image_prompt(NSR_TITLE, None, "motogp", template_key="motorcycle_commercial", bike=messy)
    assert "Scene: The 1990 Honda NSR500 in Rothmans livery. It stands alone on its paddock stands" in prompt

    long_bike = "x" * 900
    capped = build_cover_image_prompt(NSR_TITLE, None, "motogp", template_key="motorcycle_commercial", bike=long_bike)
    assert ("x" * 600 + ". It stands alone") in capped
    assert "x" * 601 not in capped


def test_motorcycle_cover_without_bike_keeps_copywriter_scene_and_livery_rules():
    prompt = build_cover_image_prompt(NSR_TITLE, "the bike on its stands", "f1", template_key="motorcycle_commercial")
    assert "Scene: the bike on its stands\n" in prompt
    assert MOTORCYCLE_TEXT_RULES in prompt
    assert "plain colour blocks" not in prompt

    blank_bike = build_cover_image_prompt(NSR_TITLE, "the bike on its stands", "f1",
                                          template_key="motorcycle_commercial", bike="   ")
    assert blank_bike == prompt


def test_motogp_niche_uses_motorcycle_style_for_any_template():
    prompt = build_cover_image_prompt("Why the GP bike wheelies", "scene", " MotoGP ",
                                      template_key="motorsport_tech_explainer", bike=ROTHMANS_BIKE)
    assert "real professional paddock photo of a racing motorcycle" in prompt
    assert ROTHMANS_BIKE in prompt
    assert MOTORCYCLE_TEXT_RULES in prompt


def test_default_and_health_covers_keep_the_plain_text_rules_and_ignore_bike():
    for niche in ("f1", "health"):
        prompt = build_cover_image_prompt("Why Wings Flex", "scene", niche, template_key=None, bike=ROTHMANS_BIKE)
        assert prompt.endswith("\n" + DEFAULT_TEXT_RULES)
        assert ROTHMANS_BIKE not in prompt
        assert MOTORCYCLE_TEXT_RULES not in prompt


# ─────────────────────────────────────────────────────────────────────────────
# cover_bike_description
# ─────────────────────────────────────────────────────────────────────────────

def test_cover_bike_description_cases():
    assert cover_bike_description({"bike_look": ROTHMANS_BIKE, "livery": "red"}) == ROTHMANS_BIKE
    assert cover_bike_description({"bike_look": "  ", "livery": "Repsol orange and white"}) == (
        "The motorcycle in this livery: Repsol orange and white")
    assert cover_bike_description({"livery": "Repsol orange"}) == "The motorcycle in this livery: Repsol orange"
    assert cover_bike_description({"bike_look": "", "livery": ""}) is None
    assert cover_bike_description({"bike_look": 42}) is None
    assert cover_bike_description({}) is None
    assert cover_bike_description(None) is None
    assert cover_bike_description(["bike_look"]) is None
    assert cover_bike_description("bike_look") is None


# ─────────────────────────────────────────────────────────────────────────────
# motorcycle_cover_copy
# ─────────────────────────────────────────────────────────────────────────────

def test_motorcycle_cover_copy_accepts_a_leading_the():
    assert motorcycle_cover_copy(NSR_TITLE) == {"lines": ["HONDA NSR500", "(1990)"], "highlight": [0]}
    assert motorcycle_cover_copy("The 2006 Ducati 999 F06: the factory twin that won it all")["lines"] == [
        "DUCATI 999 F06", "(2006)"]
    assert motorcycle_cover_copy("the 2006 Ducati 999 F06 - the factory twin")["lines"] == ["DUCATI 999 F06", "(2006)"]


def test_motorcycle_cover_copy_existing_cases_unchanged():
    assert motorcycle_cover_copy("2022 Ducati Desmosedici GP22: The Aero Masterpiece") == {
        "lines": ["DUCATI", "DESMOSEDICI GP22", "(2022)"], "highlight": [0, 1]}
    assert motorcycle_cover_copy("Honda RC166 (1966): the six-cylinder 250cc") == {
        "lines": ["HONDA RC166", "(1966)"], "highlight": [0]}
    assert motorcycle_cover_copy("1993 Honda NSR500: The Two-Stroke V4") == {
        "lines": ["HONDA NSR500", "(1993)"], "highlight": [0]}
    assert motorcycle_cover_copy("Why the 2027 rules change bikes") is None
    assert motorcycle_cover_copy("The bike that changed everything") is None


# ─────────────────────────────────────────────────────────────────────────────
# prepare_cover
# ─────────────────────────────────────────────────────────────────────────────

def _fanpage_and_job(db, *, idea_id=None, job_id=301):
    fanpage = db.get(TargetFanpage, 21) or TargetFanpage(id=21, repliz_account_id="r21", name="Moto Legends",
                                                         ai_video_cover_enabled=True)
    db.add(fanpage)
    db.commit()
    job = VideoJob(
        id=job_id,
        fanpage_id=fanpage.id,
        idea_id=idea_id,
        template_key="motorcycle_commercial",
        title=NSR_TITLE,
        caption="Two-stroke legend",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
    )
    db.add(job)
    db.commit()
    return fanpage, job


FAKE_COPY = {"lines": ["NSR500", "LEGEND"], "highlight": [0], "scene": "an invented yellow bike on a road"}


def test_prepare_cover_uses_bike_look_from_variables_arg(sqlite_db):
    fanpage, job = _fanpage_and_job(sqlite_db)
    with patch("app.services.news_copywriter._generate_with_fallback", return_value=(FAKE_COPY, "router")):
        prepare_cover(sqlite_db, fanpage, job, variables={"bike_look": ROTHMANS_BIKE})

    assert job.cover_lines == ["HONDA NSR500", "(1990)"]
    assert ROTHMANS_BIKE in job.cover_prompt
    assert "invented yellow bike" not in job.cover_prompt
    assert MOTORCYCLE_TEXT_RULES in job.cover_prompt
    assert "plain colour blocks" not in job.cover_prompt


def test_prepare_cover_loads_bike_look_from_the_idea(sqlite_db):
    sqlite_db.add(TargetFanpage(id=21, repliz_account_id="r21", name="Moto Legends", ai_video_cover_enabled=True))
    sqlite_db.commit()
    idea = VideoIdea(id=55, fanpage_id=21, template_key="motorcycle_commercial", topic="NSR500",
                     variables={"bike_look": ROTHMANS_BIKE})
    sqlite_db.add(idea)
    sqlite_db.commit()
    fanpage, job = _fanpage_and_job(sqlite_db, idea_id=idea.id)

    with patch("app.services.news_copywriter._generate_with_fallback", return_value=(FAKE_COPY, "router")):
        prepare_cover(sqlite_db, fanpage, job)

    assert ROTHMANS_BIKE in job.cover_prompt
    assert "invented yellow bike" not in job.cover_prompt


def test_prepare_cover_idea_lookup_error_falls_back_to_copywriter_scene(sqlite_db):
    fanpage, job = _fanpage_and_job(sqlite_db, idea_id=999)
    with patch("app.services.news_copywriter._generate_with_fallback", return_value=(FAKE_COPY, "router")), \
         patch.object(sqlite_db, "get", side_effect=RuntimeError("db down")):
        prepare_cover(sqlite_db, fanpage, job)

    assert "invented yellow bike" in job.cover_prompt
    assert MOTORCYCLE_TEXT_RULES in job.cover_prompt


def test_prepare_cover_without_idea_uses_copywriter_scene(sqlite_db):
    fanpage, job = _fanpage_and_job(sqlite_db)
    with patch("app.services.news_copywriter._generate_with_fallback", return_value=(FAKE_COPY, "router")):
        prepare_cover(sqlite_db, fanpage, job)

    assert "Scene: an invented yellow bike on a road\n" in job.cover_prompt
