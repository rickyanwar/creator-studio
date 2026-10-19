from types import SimpleNamespace

from app.services import ai_video_fill, research_desk
from app.services.video_templates import get_template, niche_liveries, render_clips


def _page(name, niches):
    return SimpleNamespace(id=7, name=name, mode2_gallery_niches=niches, ai_video_narrator=None, timezone="America/New_York")


def test_niche_detection_accepts_spaced_and_dashed_names():
    assert ai_video_fill.fanpage_niche(_page("GP Racing", ["MotoGP"])) == "motogp"
    assert ai_video_fill.fanpage_niche(_page("Moto GP Fans", [])) == "motogp"
    assert ai_video_fill.fanpage_niche(_page("X", ["moto-gp"])) == "motogp"
    assert ai_video_fill.fanpage_niche(_page("Formula Weekend", ["F1"])) == "f1"


def test_motogp_page_never_gets_a_car_and_gets_motogp_liveries(monkeypatch):
    t = get_template("motorsport_tech_explainer")
    out = ai_video_fill.apply_system_look(None, _page("GP Racing", ["MotoGP"]), t, {"vehicle_preset": "f1"})
    assert out["vehicle_preset"] == "motogp_livery"
    assert out["livery"] in niche_liveries(t, "motogp")


def test_f1_page_never_gets_a_bike():
    t = get_template("motorsport_tech_explainer")
    out = ai_video_fill.apply_system_look(None, _page("Formula Weekend", ["F1"]), t, {"vehicle_preset": "motogp_livery"})
    assert out["vehicle_preset"] == "f1"
    assert out["livery"] in niche_liveries(t, "f1")


def test_motogp_liveries_are_separate_from_f1():
    t = get_template("motorsport_tech_explainer")
    assert set(niche_liveries(t, "motogp")).isdisjoint(niche_liveries(t, "f1"))
    assert len(niche_liveries(t, "motogp")) >= 4


def test_motogp_clips_show_a_prototype_and_never_say_car():
    t = get_template("motorsport_tech_explainer")
    variables = dict(t.raw["example"]["variables"])
    variables.update(vehicle_preset="motogp_livery", livery=niche_liveries(t, "motogp")[0])
    clips = render_clips(t, variables, 3)
    assert "MotoGP prototype racing motorcycle" in clips[0]["prompt"]
    for clip in clips[1:]:
        assert "The car keeps driving" not in clip["prompt"]
        assert "The motorcycle keeps riding forward" in clip["prompt"]


def test_muse_brief_explains_motogp_bikes():
    assert any("MotoGP prototype racing bike" in rule for rule in research_desk.GLOBAL_RULES)
