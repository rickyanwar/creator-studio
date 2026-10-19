import uuid
from types import SimpleNamespace
from unittest.mock import patch
import json

import pytest

from app.database import Base, SessionLocal, engine
from app.models.ai_video import VideoIdea, VideoJob
from app.models.target_fanpages import TargetFanpage
from app.services import ai_video_fill, research_desk, video_templates
from app.schemas.fanpage import FanpageUpdate


@pytest.fixture(scope="module")
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db(setup_db):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _variables(preset="f1", **extra):
    values = {
        "vehicle_preset": preset,
        "part": "rear wing",
        "mechanism_details": "the actuator",
        "mechanism_action": "the flap moves visibly",
        "moving_part": "upper flap",
        "fixed_parts": "endplates and pillar",
        "effect_visual": "airflow lines glow around the assembly",
        "parts_list": "the flap, actuator, bolts, and supports",
        "final_state": "the complete assembly locks back into place",
        "narration_1": "This is a rear wing mechanism working inside a modern racing car during a fast lap on circuit.",
        "narration_2": "The parts separate clearly, then return together as the airflow settles around the car and the mechanism locks shut.",
    }
    values.update(extra)
    return values


def test_render_livery_with_channel_name():
    template = video_templates.get_template("motorsport_tech_explainer")
    rendered = video_templates.render_clips(
        template, _variables(livery="scarlet", channel_name="FORMULA ARCHIVES"), 2
    )

    assert "deep glossy red with white accents" in rendered[0]["prompt"]
    assert '"FORMULA ARCHIVES"' in rendered[0]["prompt"]
    assert "no other text" in rendered[0]["prompt"]
    assert 'glossy red open-wheel single-seater Grand Prix race car with the same small "FORMULA ARCHIVES" name' in rendered[1]["prompt"]
    assert all("{" not in clip["prompt"] and "}" not in clip["prompt"] for clip in rendered)
    assert all("no text" not in clip["prompt"].replace("no other text", "") for clip in rendered)
    assert all("no other text" in clip["prompt"] for clip in rendered)


def test_render_livery_without_channel_name_has_no_short_lettering():
    template = video_templates.get_template("motorsport_tech_explainer")
    rendered = video_templates.render_clips(template, _variables(livery="scarlet"), 2)

    assert ", no text" in rendered[0]["prompt"]
    assert "lettering" not in rendered[1]["prompt"]
    assert all("no text" in clip["prompt"] for clip in rendered)


def test_render_livery_validates_livery_and_channel_name():
    template = video_templates.get_template("motorsport_tech_explainer")
    with pytest.raises(ValueError, match="unknown livery"):
        video_templates.render_clips(template, _variables(livery="gold"), 2)
    with pytest.raises(ValueError):
        video_templates.render_clips(template, _variables(livery="scarlet", channel_name='BAD"NAME'), 2)
    with pytest.raises(ValueError):
        video_templates.render_clips(template, _variables(livery="scarlet", channel_name="lowercase"), 2)


def test_render_motogp_livery_uses_fairing_wording():
    template = video_templates.get_template("motorsport_tech_explainer")
    rendered = video_templates.render_clips(
        template,
        _variables("motogp_livery", livery="papaya", channel_name="MOTO ARCHIVES"),
        2,
    )

    assert "on the side of the fairing" in rendered[0]["prompt"]
    assert "on the side of the engine cover" not in rendered[0]["prompt"]


def test_legacy_f1_render_stays_unchanged():
    template = video_templates.get_template("motorsport_tech_explainer")
    rendered = video_templates.render_clips(
        template,
        _variables("f1_red", part="rear wing"),
        2,
    )

    assert "glossy racing red" in rendered[0]["prompt"]
    assert "no text" in rendered[0]["prompt"]
    assert "glossy red Formula 1 car" in rendered[1]["prompt"]
    assert "no text" in rendered[1]["prompt"]


@pytest.mark.parametrize("preset", ["EN-PADDOCK", "EN-AUSSIE-ENG", "EN-SCOT", "EN-JOURNO-F", "EN-US-ENG"])
def test_render_narrator_presets(preset):
    template = video_templates.get_template("motorsport_tech_explainer")
    preset_text = template.raw["blocks"]["narrator_presets"][preset]
    rendered = video_templates.render_clips(template, _variables(narrator_preset=preset, livery="scarlet"), 2)

    assert f"Voice-over: {preset_text}" in rendered[0]["prompt"]
    assert f"Voice-over: the same {preset_text[2:]}" in rendered[1]["prompt"]
    assert all("{" not in clip["prompt"] and "}" not in clip["prompt"] for clip in rendered)
    # Regression test: non-EN-DOC narrator preserves vehicle text
    assert "Grand Prix race car in a deep glossy red" in rendered[0]["prompt"]
    assert "glossy red open-wheel single-seater Grand Prix race car" in rendered[1]["prompt"]


def test_render_documentary_narrator_is_unchanged_without_or_with_doc_preset():
    template = video_templates.get_template("motorsport_tech_explainer")
    without_preset = video_templates.render_clips(template, _variables(livery="scarlet"), 2)
    with_doc = video_templates.render_clips(template, _variables(livery="scarlet", narrator_preset="EN-DOC"), 2)

    assert without_preset == with_doc


def test_render_unknown_narrator_preset_rejected():
    template = video_templates.get_template("motorsport_tech_explainer")
    with pytest.raises(ValueError, match="unknown narrator_preset"):
        video_templates.render_clips(template, _variables(livery="scarlet", narrator_preset="EN-UNKNOWN"), 2)


@pytest.mark.parametrize(
    ("timezone", "fanpage_id", "stored", "expected"),
    [
        ("Australia/Sydney", 1, None, "EN-AUSSIE-ENG"),
        ("America/New_York", 1, None, "EN-US-ENG"),
        ("Europe/London", 7, None, ["EN-PADDOCK", "EN-SCOT", "EN-JOURNO-F", "EN-DOC"][7 % 4]),
        ("Europe/London", 7, "EN-SCOT", "EN-SCOT"),
        ("Europe/London", 7, "EN-UNKNOWN", ["EN-PADDOCK", "EN-SCOT", "EN-JOURNO-F", "EN-DOC"][7 % 4]),
    ],
)
def test_resolve_narrator(timezone, fanpage_id, stored, expected):
    template = video_templates.get_template("motorsport_tech_explainer")
    fanpage = SimpleNamespace(id=fanpage_id, timezone=timezone, ai_video_narrator=stored)

    assert video_templates.resolve_narrator(fanpage, template) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Formula Archives", "FORMULA ARCHIVES"), ("intan", "INTAN"), ("A😀 \"B\"", "A B")],
)
def test_sanitize_channel_name(value, expected):
    assert video_templates.sanitize_channel_name(value) == expected


def test_sanitize_channel_name_empty_and_truncated():
    assert video_templates.sanitize_channel_name("") is None
    assert video_templates.sanitize_channel_name("!!!") is None
    result = video_templates.sanitize_channel_name("A VERY LONG CHANNEL NAME THAT DOES NOT FIT")
    assert len(result) <= 24
    assert result == result.rstrip()


def test_pick_livery_avoids_recent_values(monkeypatch):
    template = video_templates.get_template("motorsport_tech_explainer")

    class Query:
        def join(self, *args):
            return self

        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def limit(self, count):
            assert count == 50
            return self

        def all(self):
            return [({"livery": "scarlet"},), ({"livery": "papaya"},)]

    db = SimpleNamespace(query=lambda *args: Query())
    monkeypatch.setattr(ai_video_fill.random, "choice", lambda values: values[0])
    assert ai_video_fill.pick_livery(db, 1, template) not in {"scarlet", "papaya"}


def test_fill_idea_sets_and_overwrites_system_look(monkeypatch):
    template = video_templates.get_template("motorsport_tech_explainer")
    fanpage = SimpleNamespace(id=7, name="Formula Archives", timezone="Europe/London", ai_video_narrator=None, mode2_gallery_niches=["f1"], caption_language="en")
    idea = SimpleNamespace(
        id=1,
        template_key=template.key,
        topic="rear wing",
        angle=None,
        research_notes=None,
        variables=None,
    )
    generated = {"variables": _variables(livery="gold"), "title": "Title", "caption": "Caption"}

    def fake_generate(prompt, parse, **kwargs):
        return parse(json.dumps(generated)), "mock"

    monkeypatch.setattr(ai_video_fill.news_copywriter, "_generate_with_fallback", fake_generate)
    monkeypatch.setattr(ai_video_fill, "pick_livery", lambda db, fanpage_id, t, niche=None: "scarlet")
    variables, *_ = ai_video_fill.fill_idea(None, idea, fanpage, 2)

    assert variables["livery"] == "scarlet"
    assert variables["channel_name"] == "FORMULA ARCHIVES"
    assert variables["narrator_preset"] == "EN-DOC"


def test_fill_idea_overwrites_invalid_llm_narrator(monkeypatch):
    template = video_templates.get_template("motorsport_tech_explainer")
    fanpage = SimpleNamespace(id=8, name="Formula Archives", timezone="America/New_York", ai_video_narrator=None, mode2_gallery_niches=["f1"], caption_language="en")
    idea = SimpleNamespace(id=1, template_key=template.key, topic="rear wing", angle=None, research_notes=None, variables=None)
    generated = {"variables": _variables(narrator_preset="EN-NOT-REAL"), "title": "Title", "caption": "Caption"}

    def fake_generate(prompt, parse, **kwargs):
        return parse(json.dumps(generated)), "mock"

    monkeypatch.setattr(ai_video_fill.news_copywriter, "_generate_with_fallback", fake_generate)
    monkeypatch.setattr(ai_video_fill, "pick_livery", lambda db, fanpage_id, t, niche=None: "scarlet")
    variables, *_ = ai_video_fill.fill_idea(None, idea, fanpage, 2)

    assert variables["narrator_preset"] == "EN-US-ENG"


def test_fanpage_narrator_schema():
    assert FanpageUpdate(ai_video_narrator=None).ai_video_narrator is None
    assert FanpageUpdate(ai_video_narrator="EN-SCOT").ai_video_narrator == "EN-SCOT"
    with pytest.raises(Exception):
        FanpageUpdate(ai_video_narrator="EN-NOT-REAL")


def test_apply_system_look_maps_f1_red(db):
    template = video_templates.get_template("motorsport_tech_explainer")
    fp = TargetFanpage(
        name="Formula Page",
        username=f"f1_{uuid.uuid4().hex[:8]}",
        repliz_account_id=f"rep_{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    db.add(fp)
    db.flush()

    vars_in = _variables("f1_red")
    res = ai_video_fill.apply_system_look(db, fp, template, vars_in)
    assert res["vehicle_preset"] == "f1"
    assert res["livery"] in video_templates.liveries(template)


def test_pick_livery_rotation_lru(db):
    template = video_templates.get_template("motorsport_tech_explainer")
    all_liveries = video_templates.liveries(template)
    assert len(all_liveries) == 9

    fp = TargetFanpage(
        name="Rotation Page",
        username=f"rot_{uuid.uuid4().hex[:8]}",
        repliz_account_id=f"rep_{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    db.add(fp)
    db.flush()

    used = all_liveries[:-1]
    unused = all_liveries[-1]

    livery_sequence = list(used) + [used[0]]
    for i, liv in enumerate(livery_sequence, start=1):
        idea = VideoIdea(
            fanpage_id=fp.id,
            template_key=template.key,
            topic=f"topic {i}",
            variables={"livery": liv, "vehicle_preset": "f1"},
        )
        db.add(idea)
        db.flush()
        job = VideoJob(
            fanpage_id=fp.id,
            idea_id=idea.id,
            template_key=template.key,
            title=f"title {i}",
            caption=f"caption {i}",
            model="flow",
            aspect="9:16",
            clip_seconds=4,
            clips=[],
            credits_per_clip=7,
            credits_estimate=14,
            status="done",
        )
        db.add(job)
        db.flush()

    # With all except 1 used, pick_livery must return the unused one
    assert ai_video_fill.pick_livery(db, fp.id, template) == unused

    # When all were used, returns the least recently used one
    idea_last = VideoIdea(
        fanpage_id=fp.id,
        template_key=template.key,
        topic=f"topic {len(livery_sequence) + 1}",
        variables={"livery": unused, "vehicle_preset": "f1"},
    )
    db.add(idea_last)
    db.flush()
    job_last = VideoJob(
        fanpage_id=fp.id,
        idea_id=idea_last.id,
        template_key=template.key,
        title=f"title {len(livery_sequence) + 1}",
        caption=f"caption {len(livery_sequence) + 1}",
        model="flow",
        aspect="9:16",
        clip_seconds=4,
        clips=[],
        credits_per_clip=7,
        credits_estimate=14,
        status="done",
    )
    db.add(job_last)
    db.flush()

    assert ai_video_fill.pick_livery(db, fp.id, template) == used[1]


def test_brief_niche_presets_and_check_variant_accepts_f1_red(db):
    template = video_templates.get_template("motorsport_tech_explainer")
    presets = research_desk.niche_presets(template, "f1")
    assert "f1_red" not in presets
    assert "f1" in presets

    fp = TargetFanpage(
        name="Tech F1",
        username=f"tech_{uuid.uuid4().hex[:8]}",
        repliz_account_id=f"rep_{uuid.uuid4().hex[:8]}",
        mode2_gallery_niches=["f1"],
        is_active=True,
        ai_video_enabled=True,
    )
    db.add(fp)
    db.flush()

    variant = {
        "channel_id": fp.id,
        "angle": "Explaining the mechanism",
        "variables": _variables("f1_red"),
        "title": "A Great Video Title",
        "caption": "A concise hook sentence that tells the reader what happens in this clip. #F1 #Tech",
    }
    pack = {
        "client_ref": str(uuid.uuid4()),
        "niche": "f1",
        "template_key": template.key,
        "clips": 2,
        "topic": "rear wing mechanism",
        "research_notes": "1. Note [S1].",
        "sources": [],
        "references": [],
        "score": 85,
        "rationale": "Strong topic.",
        "qc": {"passed": True, "motion_checked": True, "motion_notes": "Verified all motions."},
        "variants": [variant],
    }
    errors, accepted_fp = research_desk._check_variant(
        db, variant, pack, template, None, need_left={fp.id: 1}, accepted=[], channel_topic_counts={}
    )
    assert not errors, f"Unexpected errors: {errors}"
    assert accepted_fp.id == fp.id


def test_team_livery_accepted_and_applied(db):
    template = video_templates.get_template("motorsport_tech_explainer")
    fp = TargetFanpage(
        name="Scuderia Fanpage",
        username=f"scud_{uuid.uuid4().hex[:8]}",
        repliz_account_id=f"rep_{uuid.uuid4().hex[:8]}",
        mode2_gallery_niches=["f1"],
        is_active=True,
        ai_video_enabled=True,
    )
    db.add(fp)
    db.flush()

    # 1. Variant with team_livery="scarlet" is accepted
    variant = {
        "channel_id": fp.id,
        "angle": "High downforce rear wing angle",
        "variables": _variables("f1", team_livery="scarlet"),
        "title": "High Downforce Wing Tech",
        "caption": "How the team shaped its high downforce wing for maximum speed. #F1 #Speed",
    }
    pack = {
        "client_ref": str(uuid.uuid4()),
        "niche": "f1",
        "template_key": template.key,
        "clips": 2,
        "topic": "high downforce wing mechanism",
        "research_notes": "1. Wing detail [S1].",
        "sources": [],
        "references": [],
        "score": 85,
        "rationale": "Team specific innovation.",
        "qc": {"passed": True, "motion_checked": True, "motion_notes": "Verified motion."},
        "variants": [variant],
    }
    errors, accepted_fp = research_desk._check_variant(
        db, variant, pack, template, None, need_left={fp.id: 1}, accepted=[], channel_topic_counts={}
    )
    assert not errors, f"Unexpected errors: {errors}"

    # 2. Produced variables get livery == "scarlet" after apply_system_look
    res = ai_video_fill.apply_system_look(db, fp, template, variant["variables"])
    assert res["livery"] == "scarlet"

    # 3. Invalid team_livery key -> rejected
    bad_variant = dict(variant, variables=_variables("f1", team_livery="neon_yellow"))
    bad_errors, _ = research_desk._check_variant(
        db, bad_variant, pack, template, None, need_left={fp.id: 1}, accepted=[], channel_topic_counts={}
    )
    assert any("team_livery must be one of" in e for e in bad_errors)

    # 4. Non-livery preset with team_livery -> rejected
    bad_preset_variant = dict(variant, variables=_variables("unsupported_preset", team_livery="scarlet"))
    bad_preset_errors, _ = research_desk._check_variant(
        db, bad_preset_variant, pack, template, None, need_left={fp.id: 1}, accepted=[], channel_topic_counts={}
    )
    assert any("team_livery only works with a livery preset" in e for e in bad_preset_errors)

    # 5. Without team_livery -> rotation as before
    no_team_var = _variables("f1")
    res_rot = ai_video_fill.apply_system_look(db, fp, template, no_team_var)
    assert res_rot["livery"] in video_templates.liveries(template)


def test_team_livery_with_f1_red_preset(db):
    template = video_templates.get_template("motorsport_tech_explainer")
    fp = TargetFanpage(
        name="Scuderia Red",
        username=f"scud_red_{uuid.uuid4().hex[:8]}",
        repliz_account_id=f"rep_{uuid.uuid4().hex[:8]}",
        mode2_gallery_niches=["f1"],
        is_active=True,
        ai_video_enabled=True,
    )
    db.add(fp)
    db.flush()

    res = ai_video_fill.apply_system_look(db, fp, template, _variables("f1_red", team_livery="scarlet"))
    assert res["vehicle_preset"] == "f1"
    assert res["livery"] == "scarlet"
