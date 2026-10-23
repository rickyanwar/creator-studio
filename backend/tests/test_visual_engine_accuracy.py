from app.services.visual_engine import (
    build_action_prompt,
    build_inset_prompt,
    build_solo_prompt,
    build_whatif_inset_prompt,
    hero_framing_clause,
)


def _inset(mode, desc="the Kyalami circuit in South Africa", path="/tmp/inset.jpg"):
    return {"mode": mode, "desc": desc, "path": path}


def test_every_builder_carries_the_accuracy_block_for_its_sport():
    inset, _ = build_inset_prompt("Lewis Hamilton", [_inset("real")], "news", "#E10600", "/tmp/h.jpg", sport="Formula 1")
    solo = build_solo_prompt("Lewis Hamilton", "news", "#E10600", sport="MotoGP")
    action = build_action_prompt("a red car", "news", "#E10600", sport="Formula 1")
    whatif, _ = build_whatif_inset_prompt("Lewis Hamilton", "Ferrari", "red", "prancing horse", "#E10600", "/tmp/h.jpg", None, None)
    for prompt in (inset, action, whatif):
        assert "ACCURACY (very important)" in prompt and "must belong to Formula 1" in prompt
    assert "must belong to MotoGP" in solo


def test_hero_framing_handles_head_and_upper_body_photos():
    text = hero_framing_clause("center-right")
    assert "placed center-right" in text
    assert "chin no lower than 50% of the card height" in text
    assert "keep the whole upper body visible" in text and "never slice it at the neck, chest or elbows" in text


def test_hero_keeps_his_real_age():
    prompt, _ = build_inset_prompt("Kimi Raikkonen", [_inset("real")], "news", "#E10600", "/tmp/h.jpg")
    assert "exactly the age he has in image 1" in prompt


def test_generated_inset_never_shows_people_or_another_sport():
    prompt, refs = build_inset_prompt("Lewis Hamilton", [_inset("imagined", path=None)], "news", "#E10600", "/tmp/h.jpg")
    assert "GENERATED PLACE OR OBJECT" in prompt and "NO people at all" in prompt and "Nothing from another sport" in prompt
    assert refs == ["/tmp/h.jpg"]


def test_real_inset_is_used_as_photographed():
    prompt, refs = build_inset_prompt("Lewis Hamilton", [_inset("real")], "news", "#E10600", "/tmp/h.jpg")
    assert "Use Image 2 exactly as photographed" in prompt and "never change its sport, venue or era" in prompt
    assert refs == ["/tmp/h.jpg", "/tmp/inset.jpg"]


def test_brand_inset_shows_only_the_reference_logo():
    prompt, _ = build_inset_prompt("Lewis Hamilton", [_inset("asset", desc="Ferrari", path="/tmp/logo.png")], "news", "#E10600", "/tmp/h.jpg")
    assert "BRAND SCENE for Ferrari" in prompt and "No people, faces, helmets or drivers" in prompt
