"""Mode 8 health template (health_3d_explainer): rendering, niche, fill prompt, QC and the research desk."""
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.ai_video import VideoIdea
from app.services import ai_video_fill, ai_video_qc, research_desk
from app.services.video_templates import (
    MAX_PROMPT_CHARS,
    fanpage_niche,
    get_template,
    narrator_presets,
    render_clips,
    required_variables,
    resolve_narrator,
    uses_vehicle,
)
from tests.test_research_desk import clean, db, make_fanpage, setup_db, token  # noqa: F401

KEY = "health_3d_explainer"


def _page(name="Ayok sehat", niches=(), templates=(KEY,), tz="Europe/London", **kw):
    return SimpleNamespace(id=12, name=name, mode2_gallery_niches=list(niches), ai_video_templates=list(templates),
                           ai_video_template=None, ai_video_narrator=None, timezone=tz, **kw)


def _example():
    return dict(get_template(KEY).raw["example"]["variables"])


# ── Template and rendering ───────────────────────────────────────────────────

def test_template_has_no_vehicle_slots():
    t = get_template(KEY)
    assert not uses_vehicle(t)
    assert "vehicle_preset" not in required_variables(t, 4)
    assert required_variables(t, 4)[0] == "visual_subject"
    assert len([n for n in required_variables(t, 5) if n.startswith("shot_")]) == 10
    assert "shot_1c" not in required_variables(t, 5)
    assert narrator_presets(t) == ["EN-DOC"]


def test_example_renders_four_cut_style_t2v_clips():
    t = get_template(KEY)
    clips = render_clips(t, _example(), 4, lang_code="en")
    assert [c["mode"] for c in clips] == ["t2v"] * 4
    narrator = t.raw["blocks"]["narrator_en"]
    for clip in clips:
        prompt = clip["prompt"]
        assert len(prompt) <= MAX_PROMPT_CHARS
        assert "{" not in prompt and "}" not in prompt
        assert prompt.startswith(t.raw["blocks"]["style"])
        assert _example()["visual_subject"] in prompt
        assert narrator in prompt  # one identical narrator sentence in every clip
        assert "Two shots with one clean cut" in prompt
        assert "Cut to shot 2" in prompt
        assert "Cut to shot 3" not in prompt


def test_narration_outside_budget_is_rejected():
    variables = _example()
    variables["narration_2"] = "Too short."
    with pytest.raises(ValueError, match="narration_2 must contain"):
        render_clips(get_template(KEY), variables, 4, lang_code="en")


def test_prompt_over_flow_limit_is_rejected(monkeypatch):
    monkeypatch.setattr("app.services.video_templates.MAX_PROMPT_CHARS", 2000)
    variables = _example()
    for slot in ("visual_subject", "shot_1a", "shot_1b"):
        variables[slot] = "a calm soft pink cross-section of healthy tissue in gentle light " * 7
    with pytest.raises(ValueError, match="Flow refuses prompts"):
        render_clips(get_template(KEY), variables, 4, lang_code="en")


def test_health_explainer_renders_long_fill_under_2300_chars():
    t = get_template(KEY)
    shot_text = "A glossy pink anatomical cross-section showing tiny active cells gliding through healthy soft tissue under warm studio lighting with shallow depth of field and a gentle slow camera move forward."
    assert len(shot_text) <= 200
    narr_22 = " ".join(["word"] * 22)
    variables = {
        "visual_subject": "a stylised human body in soft pink translucent skin with cream bones",
        "shot_1a": shot_text,
        "shot_1b": shot_text,
        "narration_1": narr_22,
        "shot_2a": shot_text,
        "shot_2b": shot_text,
        "narration_2": narr_22,
        "shot_3a": shot_text,
        "shot_3b": shot_text,
        "narration_3": narr_22,
        "shot_4a": shot_text,
        "shot_4b": shot_text,
        "narration_4": narr_22,
    }
    clips = render_clips(t, variables, 4, lang_code="en")
    lengths = [len(c["prompt"]) for c in clips]
    for i, length in enumerate(lengths):
        assert length <= 2300, f"clip {i + 1} length {length} exceeds 2300"


def test_health_explainer_ignores_extra_shot_slots():
    t = get_template(KEY)
    variables = dict(_example())
    variables["shot_1c"] = "extra third shot from old pack"
    variables["shot_2c"] = "extra third shot from old pack"
    variables["shot_3c"] = "extra third shot from old pack"
    variables["shot_4c"] = "extra third shot from old pack"
    clips = render_clips(t, variables, 4, lang_code="en")
    assert len(clips) == 4


def test_non_english_page_gets_the_narrator_in_its_language():
    variables = _example()
    for i in range(1, 5):
        variables[f"narration_{i}"] = " ".join(["kata"] * 18)
    clips = render_clips(get_template(KEY), variables, 4, lang_code="id")
    assert all("says in Indonesian:" in c["prompt"] and "says in English:" not in c["prompt"] for c in clips)


def test_motorsport_narrators_fall_back_to_the_health_voice():
    t = get_template(KEY)
    assert resolve_narrator(_page(tz="America/New_York"), t) == "EN-DOC"
    assert resolve_narrator(_page(tz="Australia/Sydney"), t) == "EN-DOC"
    assert resolve_narrator(_page(), t) == "EN-DOC"


# ── Niche ────────────────────────────────────────────────────────────────────

def test_niche_comes_from_the_page_templates():
    assert fanpage_niche(_page()) == "health"
    assert fanpage_niche(_page(name="Wellness Today", templates=(), niches=("health",))) == "health"
    assert fanpage_niche(_page(name="Formula Weekend", niches=("F1",), templates=("motorsport_tech_explainer",))) == "f1"
    assert fanpage_niche(_page(name="GP Racing", niches=("MotoGP",), templates=("motorsport_tech_explainer",))) == "motogp"
    assert fanpage_niche(_page(name="Formula Weekend", niches=("F1",), templates=())) == "f1"


# ── Fill prompt and the studio look ──────────────────────────────────────────

def _fill_page():
    return SimpleNamespace(**vars(_page()), caption_language="en", ai_video_title_max_chars=None,
                           ai_video_description_max_chars=None)


def test_health_fill_prompt_has_health_rules_and_no_vehicle_rules():
    idea = SimpleNamespace(topic="How an asthma inhaler opens the airways", angle=None, research_notes=None, variables=None)
    prompt = ai_video_fill.build_fill_prompt(get_template(KEY), idea, _fill_page(), 4)
    assert "Health accuracy requirements" in prompt
    assert "Never name real brands, companies or products." in prompt
    assert "without specific numbers or study results" in prompt
    for motorsport in ("Vehicle preset", "Mechanical accuracy", "livery", "MGU-K", "season claims"):
        assert motorsport not in prompt


def test_motorsport_fill_prompt_keeps_its_rules():
    idea = SimpleNamespace(topic="How DRS works", angle=None, research_notes=None, variables=None)
    page = SimpleNamespace(**{**vars(_fill_page()), "name": "Formula Weekend", "mode2_gallery_niches": ["F1"],
                              "ai_video_templates": ["motorsport_tech_explainer"]})
    prompt = ai_video_fill.build_fill_prompt(get_template("motorsport_tech_explainer"), idea, page, 2)
    assert "Vehicle preset choices for this page:" in prompt
    assert "Mechanical accuracy requirements" in prompt
    assert "Never name real teams, sponsors, or brands." in prompt
    assert "Health accuracy" not in prompt


def test_system_look_drops_vehicle_slots():
    variables = {**_example(), "vehicle_preset": "f1", "team_livery": "scarlet", "livery": "papaya", "channel_name": "X"}
    out = ai_video_fill.apply_system_look(None, _page(), get_template(KEY), variables)
    assert out["narrator_preset"] == "EN-DOC"
    assert not {"vehicle_preset", "team_livery", "livery", "channel_name"} & set(out)


def test_research_fill_adds_no_motion_lock_to_health_ideas():
    idea = SimpleNamespace(template_key=KEY, variables=_example(), title="Why fever helps", caption="A fever is a tool.")
    variables, title, caption, rendered, provider = ai_video_fill.fill_from_research(None, idea, _page(), 4)
    assert "moving_part" not in variables and "fixed_parts" not in variables
    assert [c["mode"] for c in rendered] == ["t2v"] * 4
    assert provider == "research_desk" and title == "Why fever helps"


# ── Vision QC ────────────────────────────────────────────────────────────────

def test_vision_qc_prompt_for_health_checks_subject_text_and_gore():
    seen = {}

    def fake_chat(messages, **kwargs):
        seen["prompt"] = messages[0]["text"]
        return '{"pass": true, "issues": []}'

    with patch("app.services.design_images._vision_chat", fake_chat), \
            patch("app.services.design_images._vision_datauri", lambda *a, **k: "data:image/jpeg;base64,x"):
        result = ai_video_qc.vision_check(b"jpeg", "Why fever helps", {"visual_subject": "a stylised human body"})
    assert result["passed"]
    assert "Main subject in every clip: a stylised human body" in seen["prompt"]
    assert "hard cuts between short shots are intended" in seen["prompt"]
    assert "text, letters, numbers, labels or logos appear" in seen["prompt"]
    assert "needle piercing skin" in seen["prompt"]
    assert "rigid parts (wings" not in seen["prompt"]


# ── Research desk (needs the database) ───────────────────────────────────────

CAPTION = "Your body turns up its own heat to fight germs. Here is how a fever works. #health"


def health_pack(channel_ids, **overrides):
    p = {
        "client_ref": str(uuid.uuid4()), "niche": "health", "template_key": KEY, "clips": 4,
        "topic": f"How a fever helps the body {uuid.uuid4().hex[:6]}",
        "research_notes": "1. The hypothalamus raises the body's temperature set point during infection. [S1][S2]",
        "sources": [
            {"id": "S1", "url": "https://www.nhs.uk/conditions/fever-in-adults/", "publisher": "NHS", "title": "Fever", "published_at": None},
            {"id": "S2", "url": "https://my.clevelandclinic.org/health/symptoms/10880-fever", "publisher": "Cleveland Clinic",
             "title": "Fever", "published_at": None},
        ],
        "references": [{"url": "https://www.instagram.com/reel/abc/", "platform": "instagram", "kind": "video_style"}],
        "score": 82, "rationale": "Fever explainers beat the reference accounts' median views by 3x.",
        "qc": {"passed": True, "motion_checked": True,
               "motion_notes": "Every shot matches its clause; the same body in all clips; no text or gore."},
        "variants": [{"channel_id": cid, "angle": f"angle {i}", "variables": _example(),
                      "title": f"Why a fever helps you heal {i}", "caption": CAPTION} for i, cid in enumerate(channel_ids)],
    }
    p.update(overrides)
    return p


def _health_channel(db, clean):
    # Health pages run 40-50 s videos (like Inside You in production); the clip-count default of make_fanpage is for motorsport.
    return make_fanpage(db, clean, name="Leaky Health Page", niches=(), ai_video_templates=[KEY],
                        ai_video_seconds_min=40, ai_video_seconds_max=50)


def test_brief_lists_the_health_niche_with_its_guide(db, clean):
    fp = _health_channel(db, clean)
    brief = TestClient(app).get("/research/brief", headers=token(db)).json()
    entry = next(n for n in brief["niches"] if n["niche"] == "health")
    assert entry["guide"] == research_desk.NICHE_GUIDES["health"]
    channel = entry["channels"][0]
    assert channel["channel_id"] == fp.id
    assert channel["templates"][KEY]["vehicle_presets"] == []
    item = next(t for t in brief["templates"] if t["key"] == KEY)
    assert item["uses_vehicle"] is False and item["vehicle_presets"] == {}
    assert "vehicle / vehicle_short" not in item["studio_filled_placeholders"]
    assert "Leaky Health Page" not in str(brief)


def test_health_pack_is_stored_without_vehicle_slots(db, clean):
    fp = _health_channel(db, clean)
    result = TestClient(app).post("/research/packs", headers=token(db),
                                  json={"brief_id": "x", "packs": [health_pack([fp.id])]}).json()["results"][0]
    assert result["status"] == "created", result
    idea = db.query(VideoIdea).filter(VideoIdea.id == result["variants"][0]["idea_id"]).one()
    assert idea.template_key == KEY and idea.clips == 4 and "vehicle_preset" not in idea.variables


def test_health_variant_with_a_vehicle_preset_is_rejected(db, clean):
    fp = _health_channel(db, clean)
    p = health_pack([fp.id])
    p["variants"][0]["variables"]["vehicle_preset"] = "f1"
    result = TestClient(app).post("/research/packs", headers=token(db),
                                  json={"brief_id": "x", "packs": [p]}).json()["results"][0]
    assert "has no vehicle" in " ".join(result["variants"][0]["errors"])


def test_health_pack_cannot_go_to_an_f1_channel(db, clean):
    f1 = make_fanpage(db, clean, name="Leaky F1 Page", niches=("f1",))
    result = TestClient(app).post("/research/packs", headers=token(db),
                                  json={"brief_id": "x", "packs": [health_pack([f1.id])]}).json()["results"][0]
    assert "is a f1 channel, not health" in " ".join(result["variants"][0]["errors"])


def test_schema_needs_vehicle_preset_only_for_vehicle_templates():
    conditions = research_desk.pack_schema()["$defs"]["pack"]["allOf"]

    def rule(key):
        cond = next(c for c in conditions if c["if"]["properties"]["template_key"] == {"const": key}
                    and "clips" not in c["if"]["properties"])
        return cond["then"]["properties"]["variants"]["items"]["properties"]["variables"]

    assert rule("motorsport_tech_explainer")["required"] == ["vehicle_preset"]
    assert "required" not in rule(KEY) and "not" in rule(KEY)
