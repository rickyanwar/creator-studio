"""Tests for pro_poster art direction service (v3: story-driven designer brief)."""
import pytest

from app.services.pro_poster.art_direction import (
    choose_layout,
    clean_story,
    direct,
    event_line,
    retry_prompt,
)
from app.services.pro_poster.types import (
    EventContext,
    PhotoCandidate,
    PhotoCheck,
    PhotoSelection,
    PosterBrief,
    PosterConcept,
    Subject,
)


def _make_candidate(role="hero", path="/images/test.jpg"):
    return PhotoCandidate(
        source="gallery",
        gallery_image_id=1,
        path_or_url=path,
        role=role,
    )


def _make_check(candidate=None, role="hero", path="/images/test.jpg"):
    cand = candidate or _make_candidate(role=role, path=path)
    return PhotoCheck(
        candidate=cand,
        person="yes",
        event_match="yes",
        obstructed=False,
        watermark=False,
        quality=4,
    )


def _make_brief(
    series="Formula 1",
    vehicle="Formula 1 car",
    circuit="Monaco",
    country=None,
    session="race",
    year=2026,
    story_type="result",
    mood="happy",
    must_avoid=(),
    subjects=(Subject(name="Max Verstappen", role="hero", team=None),),
    accent_colors=("#FF8000",),
    visual_ideas=("chequered flag",),
):
    return PosterBrief(
        job_id=123,
        subjects=subjects,
        event=EventContext(
            series=series,
            vehicle=vehicle,
            circuit=circuit,
            country=country,
            session=session,
            year=year,
        ),
        story_type=story_type,
        mood=mood,
        badge_label=f"{series.upper()} NEWS",
        accent_colors=accent_colors,
        visual_ideas=visual_ideas,
        must_avoid=must_avoid,
    )


def test_event_line_all_fields_and_only_series():
    """Verify event_line with all fields present vs only series."""
    # All fields
    brief_all = _make_brief(
        series="GT World Challenge Europe",
        vehicle="GT3 car",
        circuit="Portimao",
        country="Portugal",
        session="race",
        year=2026,
        subjects=(Subject(name="Max Verstappen", role="hero", team="Verstappen Racing"),),
    )
    assert (
        event_line(brief_all)
        == "GT World Challenge Europe, the GT3 car, Verstappen Racing, Portimao, Portugal, race, 2026"
    )

    # Only series
    brief_series_only = _make_brief(
        series="Formula 1",
        vehicle=None,
        circuit=None,
        country=None,
        session=None,
        year=None,
        subjects=(),
    )
    assert event_line(brief_series_only) == "Formula 1"

    # Vehicle already has "the"
    brief_the_veh = _make_brief(
        series="Formula 1",
        vehicle="the Formula 1 car",
        circuit=None,
        country=None,
        session=None,
        year=None,
        subjects=(),
    )
    assert event_line(brief_the_veh) == "Formula 1, the Formula 1 car"


def test_clean_story():
    """clean_story collapses whitespace, strips **, truncates at 1200 at a space, empty -> None."""
    # Collapse whitespace and strip **
    raw = "   **Bulega** seals   treble:\n\t**Fifth** in   all-time wins.  "
    assert clean_story(raw) == "Bulega seals treble: Fifth in all-time wins."

    # Empty inputs return None
    assert clean_story(None) is None
    assert clean_story("") is None
    assert clean_story("   \n\t  ") is None
    assert clean_story("   ****  \t  \n ") is None

    # Truncate at 1200 at a space
    words = [f"word{i}" for i in range(250)]
    long_story = " ".join(words)
    assert len(long_story) > 1200
    cleaned = clean_story(long_story)
    assert cleaned is not None
    assert len(cleaned) <= 1200
    assert not cleaned.endswith(" ")
    # Next char in original stream is a space boundary
    assert long_story[len(cleaned)] == " "

    # Unbroken string of > 1200 characters falls back to exact slice
    solid = "x" * 1300
    cleaned_solid = clean_story(solid)
    assert cleaned_solid == "x" * 1200


def test_choose_layout():
    """choose_layout handles all 5 layout variants including face_ref_scene."""
    brief = _make_brief()
    hero_check = _make_check(role="hero", path="/images/hero.jpg")
    ctx_check = _make_check(role="context", path="/images/ctx.jpg")
    face_check = _make_check(role="hero", path="/images/face_ref.jpg")

    # 1. No hero, no face_ref, no context -> typographic
    assert choose_layout(brief, PhotoSelection(hero=None, face_ref=None, context=())) == "typographic"

    # 2. Hero None + face_ref -> face_ref_scene (both with and without context)
    assert (
        choose_layout(brief, PhotoSelection(hero=None, face_ref=face_check, context=()))
        == "face_ref_scene"
    )
    assert (
        choose_layout(brief, PhotoSelection(hero=None, face_ref=face_check, context=(ctx_check,)))
        == "face_ref_scene"
    )

    # 3. Hero None + context -> action_full_bleed
    assert (
        choose_layout(brief, PhotoSelection(hero=None, face_ref=None, context=(ctx_check,)))
        == "action_full_bleed"
    )

    # 4. Hero + context -> hero_with_context
    assert (
        choose_layout(brief, PhotoSelection(hero=hero_check, face_ref=None, context=(ctx_check,)))
        == "hero_with_context"
    )

    # 5. Hero solo -> hero_solo
    assert (
        choose_layout(brief, PhotoSelection(hero=hero_check, face_ref=None, context=()))
        == "hero_solo"
    )


def test_hero_prompt_contents():
    """Hero prompt contains story text, clean headline, required key phrases, and hero ref line."""
    brief = _make_brief(
        series="WorldSBK",
        vehicle="superbike",
        circuit="Jerez",
        country="Spain",
        session="race",
        year=2026,
        subjects=(Subject(name="Nicolo Bulega", role="hero", team="Aruba.it Racing Ducati"),),
    )
    hero_check = _make_check(role="hero", path="/images/bulega.jpg")
    ctx_check = _make_check(role="context", path="/images/jerez.jpg")
    selection = PhotoSelection(hero=hero_check, context=(ctx_check,))

    headline = "Bulega seals Jerez treble: **Fifth in all-time wins**"
    story = "Nicolo Bulega claimed three race wins at Jerez. He is now fifth in all-time WorldSBK victories."

    direction = direct(brief, selection, headline=headline, story_text=story)

    assert direction.layout == "hero_with_context"
    assert direction.refs == ("/images/bulega.jpg", "/images/jerez.jpg")
    assert direction.allowed_text == ()

    p = direction.prompt
    assert 'Story headline: "Bulega seals Jerez treble: Fifth in all-time wins"' in p
    assert "**" not in p
    assert 'Story text (from the source article):\n"""Nicolo Bulega claimed three race wins at Jerez. He is now fifth in all-time WorldSBK victories."""' in p
    assert "ONE strongest visual idea" in p
    assert "the page's house style, the same as its Instagram cards" in p
    assert "- The bottom 35% of the image will be covered later by the fan page's headline text. Keep that area simple and calm (for example a plain background, the track, the floor or soft out-of-focus scenery), with no faces, text or important detail. It does not need to be dark. Everything above it is the full artwork." in p
    assert "bottom 42%" not in p
    assert "Keep the top 12% of the image free of insets" in p
    assert (
        "- Image 1: a real press photo of Nicolo Bulega (Aruba.it Racing Ducati). "
        "Keep his face, helmet design, race number, kit, vehicle livery and sponsor logos exactly as they are."
    ) in p
    assert "Nicolo Bulega must be recognisably himself: show his face clearly, turned toward the camera (front or three-quarter view)" in p


def test_ref_order_and_portrait_line():
    """Portrait photo is placed immediately after hero photo and numbered correctly."""
    brief = _make_brief(
        series="Formula 1",
        vehicle="Formula 1 car",
        circuit="Silverstone",
        subjects=(Subject(name="Lando Norris", role="hero", team="McLaren"),),
    )
    hero_check = _make_check(role="hero", path="/images/norris_action.jpg")
    portrait_check = _make_check(role="hero", path="/images/norris_face.jpg")
    ctx_check = _make_check(role="context", path="/images/silverstone.jpg")

    selection = PhotoSelection(hero=hero_check, portrait=portrait_check, context=(ctx_check,))
    direction = direct(brief, selection)

    assert direction.refs == (
        "/images/norris_action.jpg",
        "/images/norris_face.jpg",
        "/images/silverstone.jpg",
    )
    p = direction.prompt
    assert "- Image 1: a real press photo of Lando Norris (McLaren)." in p
    assert "- Image 2: a real front-facing photo of Lando Norris. Use it for his exact face and likeness." in p
    assert "- Image 3: a real Formula 1 photo of the Formula 1 car at Silverstone." in p


def test_face_ref_scene_prompt():
    """face_ref_scene prompt contains 'Use it ONLY for his face' and frontal face reference."""
    brief = _make_brief(
        series="GT World Challenge Europe",
        vehicle="GT3 car",
        circuit="Portimao",
        country="Portugal",
        session="race",
        year=2026,
        subjects=(Subject(name="Max Verstappen", role="hero", team="Verstappen Racing"),),
    )
    face_check = _make_check(role="hero", path="/images/verstappen_face.jpg")
    ctx_check = _make_check(role="context", path="/images/gt3_portimao.jpg")
    selection = PhotoSelection(hero=None, face_ref=face_check, context=(ctx_check,))

    direction = direct(brief, selection)

    assert direction.layout == "face_ref_scene"
    assert direction.refs == ("/images/verstappen_face.jpg", "/images/gt3_portimao.jpg")

    p = direction.prompt
    assert "Use it for his exact face and likeness." in p
    assert "Never give him a plain, unbranded or logo-free kit." in p
    assert "Dress him in his real team kit for this event with its real team and sponsor logos" in p
    assert (
        "search the web for real photos of Max Verstappen at GT World Challenge Europe Portimao 2026 and copy that kit."
    ) in p
    assert "Show his face from the front like this photo." in p
    assert "Max Verstappen must be recognisably himself: show his face clearly, turned toward the camera (front or three-quarter view)" in p


def test_frontal_face_line_only_with_hero_or_faceref():
    """Frontal face line is included only with hero or face_ref; no-face line when neither."""
    brief = _make_brief(
        subjects=(Subject(name="Charles Leclerc", role="hero"),),
    )
    ctx = _make_check(role="context", path="/images/track.jpg")

    # Case A: with hero
    sel_hero = PhotoSelection(hero=_make_check(path="/images/hero.jpg"), context=(ctx,))
    p_hero = direct(brief, sel_hero).prompt
    assert "Charles Leclerc must be recognisably himself: show his face clearly, turned toward the camera" in p_hero
    assert "Do not show any identifiable person's face" not in p_hero

    # Case B: with face_ref
    sel_faceref = PhotoSelection(hero=None, face_ref=_make_check(path="/images/face.jpg"), context=(ctx,))
    p_faceref = direct(brief, sel_faceref).prompt
    assert "Charles Leclerc must be recognisably himself: show his face clearly, turned toward the camera" in p_faceref
    assert "Do not show any identifiable person's face" not in p_faceref

    # Case C: with neither (even though brief has hero subject)
    sel_neither = PhotoSelection(hero=None, face_ref=None, context=(ctx,))
    p_neither = direct(brief, sel_neither).prompt
    assert "Charles Leclerc must be recognisably himself" not in p_neither
    assert "- Do not show any identifiable person's face. Tell the story with the vehicles, the venue, the trophy and graphics." in p_neither


def test_house_style_inset_line_always_present():
    """House style inset line is always present, adapting subject name."""
    ctx = _make_check(role="context", path="/images/track.jpg")

    # With hero subject
    brief_hero = _make_brief(subjects=(Subject(name="Max Verstappen", role="hero"),))
    p_hero = direct(brief_hero, PhotoSelection(hero=None, context=(ctx,))).prompt
    assert (
        "- Layout: Max Verstappen large in the frame, plus ONE inset inside a large circle (about 40-45% of the card width) "
        "with a clean solid white ring and a soft drop shadow, upper-left, its top edge no higher than 12% of the image height "
        "(the page logo sits in the top corners); the hero overlaps the edge of the circle, in front of it, with a soft contact shadow."
    ) in p_hero
    assert "Put the inset in the upper 40%" not in p_hero

    # Without hero subject
    brief_no_hero = _make_brief(subjects=())
    p_no_hero = direct(brief_no_hero, PhotoSelection(hero=None, context=(ctx,))).prompt
    assert (
        "- Layout: the main subject large in the frame, plus ONE inset inside a large circle (about 40-45% of the card width) "
        "with a clean solid white ring and a soft drop shadow, upper-left, its top edge no higher than 12% of the image height "
        "(the page logo sits in the top corners); the hero overlaps the edge of the circle, in front of it, with a soft contact shadow."
    ) in p_no_hero
    assert "Put the inset in the upper 40%" not in p_no_hero


def test_realism_and_template_space_sections_present():
    """Realism section and template space section are present with exact wording."""
    brief = _make_brief(
        must_avoid=("Formula 1 cars", "do not show Singapore night race.", "Do not show rain"),
    )
    hero = _make_check(role="hero", path="/images/hero.jpg")
    selection = PhotoSelection(hero=hero, context=())

    direction = direct(brief, selection)
    p = direction.prompt

    # Realism section
    assert "Photographic realism (very important):" in p
    assert "- It must look like a real photograph by a professional sports photographer, then designed: realistic skin with pores and natural imperfections, real lens depth of field and motion, natural light and colour." in p
    assert "Sharp and detailed" not in p
    assert "- But keep it natural" not in p
    assert "- No airbrushed, waxy or plastic skin, no painterly or illustrated textures, no over-saturated glow, no HDR, no AI sheen." in p
    assert "- Logos, sponsor names and numbers on kits, caps, helmets and vehicles must be copied from the reference photos, crisp and correct as in real photos. Never blank out, blur, cover or remove a real team or sponsor logo." in p

    # Template space section
    assert "Space for the fan page template:" in p
    assert "- The bottom 35% of the image will be covered later by the fan page's headline text. Keep that area simple and calm (for example a plain background, the track, the floor or soft out-of-focus scenery), with no faces, text or important detail. It does not need to be dark. Everything above it is the full artwork." in p
    assert "bottom 42%" not in p
    assert "- Keep the top 12% of the image free of insets and faces: the page logo and name sit in the top corners." in p

    # Avoid section
    assert "Do NOT include anything below (very important):" in p
    assert "No official series logo or wordmark anywhere in the image" in p and "Team, manufacturer and sponsor logos" in p
    assert "- Any text or graphic words in the bottom 35% of the image." in p
    assert "- Do not show Formula 1 cars." in p
    assert "- Do not show Singapore night race." in p
    assert "- Do not show rain." in p
    assert "- Never mix in another series' cars, bikes or logos." in p


def test_art_direction_allows_gear_logos_and_forbids_octagon_mat():
    """Art direction avoid block allows series logo on gear and forbids octagon mat or fence."""
    brief = _make_brief(
        series="UFC",
        vehicle="",
        circuit="T-Mobile Arena",
        subjects=(Subject(name="Jon Jones", role="hero"),),
    )
    hero = _make_check(role="hero", path="/images/jones.jpg")
    selection = PhotoSelection(hero=hero, context=())
    direction = direct(brief, selection)
    p = direction.prompt

    assert "A small series logo that is part of an athlete's real gear (gloves, fight shorts, race suit, helmet) is fine." in p
    assert "octagon mat or fence" in p
    avoid_section = p.split("Do NOT include anything below")[1]
    series_line = [line for line in avoid_section.splitlines() if "No official series logo" in line][0]
    assert "gloves" not in series_line.split("(for example")[0]


def test_context_lines_numbered_after_hero():
    """Context photos are numbered after the hero photo, and refs follow that order."""
    brief = _make_brief(
        series="Formula 1",
        vehicle="Formula 1 car",
        circuit="Monaco",
        subjects=(Subject(name="Charles Leclerc", role="hero"),),
    )
    hero = _make_check(role="hero", path="/images/leclerc.jpg")
    ctx1 = _make_check(role="context", path="/images/monaco1.jpg")
    ctx2 = _make_check(role="context", path="/images/monaco2.jpg")
    selection = PhotoSelection(hero=hero, context=(ctx1, ctx2))

    direction = direct(brief, selection)

    assert direction.refs == ("/images/leclerc.jpg", "/images/monaco1.jpg", "/images/monaco2.jpg")
    p = direction.prompt
    assert "- Image 1: a real press photo of Charles Leclerc." in p
    assert "- Image 2: a real Formula 1 photo of the Formula 1 car at Monaco." in p
    assert "- Image 3: a real Formula 1 photo of the Formula 1 car at Monaco." in p


def test_must_avoid_lines():
    """must_avoid items are cleanly formatted with 'Do not show' prefix."""
    brief = _make_brief(
        must_avoid=("Formula 1 cars", "do not show Singapore night race.", "Do not show rain"),
    )
    hero = _make_check(role="hero", path="/images/hero.jpg")
    selection = PhotoSelection(hero=hero, context=())

    direction = direct(brief, selection)

    p = direction.prompt
    assert "- Do not show Formula 1 cars." in p
    assert "- Do not show Singapore night race." in p
    assert "- Do not show rain." in p
    assert "- Never mix in another series' cars, bikes or logos." in p


def test_no_hero_brief_omits_likeness_line():
    """When there is no hero subject in the brief, the likeness bullet is omitted."""
    brief = _make_brief(subjects=())
    ctx = _make_check(role="context", path="/images/track.jpg")
    selection = PhotoSelection(hero=None, context=(ctx,))

    direction = direct(brief, selection)

    assert direction.layout == "action_full_bleed"
    p = direction.prompt
    assert "must be recognisably himself" not in p
    assert "Tell THIS story" in p


def test_typographic_empty_prompt_and_refs():
    """Typographic layout must produce empty prompt, empty refs, and empty allowed_text."""
    brief = _make_brief()
    selection = PhotoSelection(hero=None, face_ref=None, context=())

    direction = direct(brief, selection)

    assert direction.layout == "typographic"
    assert direction.prompt == ""
    assert direction.refs == ()
    assert direction.allowed_text == ()


def test_retry_prompt_keeps_refs_and_appends_correction():
    """retry_prompt preserves layout, refs, size, allowed_text, and appends the correction."""
    hero = _make_check(role="hero", path="/images/hero.jpg")
    brief = _make_brief()
    direction = direct(brief, PhotoSelection(hero=hero, context=()))

    fix_hint = "Fix helmet visor reflection and remove background blur"
    retried = retry_prompt(direction, fix_hint)

    assert retried.layout == direction.layout
    assert retried.refs == direction.refs
    assert retried.size == direction.size
    assert retried.allowed_text == direction.allowed_text
    assert retried.prompt == direction.prompt + f"\nCorrection from review: {fix_hint}"


def test_concept_section_present_when_given_and_absent_without():
    """Concept section is inserted after reference photos when given, absent when None."""
    hero = _make_check(role="hero", path="/images/hero.jpg")
    brief = _make_brief()
    selection = PhotoSelection(hero=hero, context=())

    # 1. Absent without concept
    dir_no_concept = direct(brief, selection, concept=None)
    assert "Visual concept (decided by the art director — follow it):" not in dir_no_concept.prompt
    assert "Key moment:" not in dir_no_concept.prompt
    assert "Main image:" not in dir_no_concept.prompt

    # 2. Present with concept
    concept = PosterConcept(
        key_moment="Max wins championship",
        scene="Max standing on car with #1 sign matching Image 1",
        inset="Trophy on pedestal",
        graphic_words=("P1", "CHAMP"),
        palette="Navy blue, red, gold",
        mood="celebratory and intense",
    )
    dir_with_concept = direct(brief, selection, concept=concept)
    p = dir_with_concept.prompt

    assert "Visual concept (decided by the art director — follow it):" in p
    assert "- Key moment: Max wins championship" in p
    # Owner 2026-10-24: hero cards use the Instagram house style, so the concept never stages a new main scene.
    assert "- Main image:" not in p
    assert "- Inset: Trophy on pedestal" in p
    assert "- Colour palette: Navy blue, red, gold" in p
    assert "- Mood: celebratory and intense" in p

    # Verify positioning: after Reference photos, before What the poster must do
    pos_refs = p.index("Reference photos")
    pos_concept = p.index("Visual concept (decided by the art director — follow it):")
    pos_must_do = p.index("What the poster must do")
    assert pos_refs < pos_concept < pos_must_do


def test_concept_inset_omitted_for_none():
    """Inset line is omitted when inset is 'none' (case-insensitive)."""
    hero = _make_check(role="hero", path="/images/hero.jpg")
    brief = _make_brief()
    selection = PhotoSelection(hero=hero, context=())

    # inset is 'none'
    concept_none = PosterConcept(
        key_moment="Bulega takes pole",
        scene="Bulega leaning deep on Panigale",
        inset="none",
        graphic_words=(),
    )
    p_none = direct(brief, selection, concept=concept_none).prompt
    assert "- Inset:" not in p_none

    # inset is provided
    concept_with_inset = PosterConcept(
        key_moment="Bulega takes pole",
        scene="Bulega leaning deep on Panigale",
        inset="Podium celebration with champagne",
        graphic_words=(),
    )
    p_inset = direct(brief, selection, concept=concept_with_inset).prompt
    assert "- Inset: Podium celebration with champagne" in p_inset


def test_concept_graphic_words_line_cases():
    """Graphic words line replaces generic line with exact words or 'Do not add any words or numbers.'."""
    hero = _make_check(role="hero", path="/images/hero.jpg")
    brief = _make_brief()
    selection = PhotoSelection(hero=hero, context=())

    generic_line = "- You may add at most three short graphic words or numbers"

    # 1. With graphic words
    concept_words = PosterConcept(
        key_moment="Great race",
        scene="Hero on track",
        inset="none",
        graphic_words=("P9", "2X"),
    )
    p_words = direct(brief, selection, concept=concept_words).prompt
    # Owner 2026-10-24: the artwork never carries words, even when the concept suggests some.
    assert "- Graphic words:" not in p_words and '"P9"' not in p_words
    assert "- No added words, letters or numbers in the artwork" in p_words
    assert generic_line not in p_words

    # 2. Without graphic words (empty tuple)
    concept_no_words = PosterConcept(
        key_moment="Great race",
        scene="Hero on track",
        inset="none",
        graphic_words=(),
    )
    p_no_words = direct(brief, selection, concept=concept_no_words).prompt
    assert "- No added words, letters or numbers in the artwork" in p_no_words
    assert generic_line not in p_no_words

    # 3. No concept given -> generic line kept
    p_generic = direct(brief, selection, concept=None).prompt
    assert generic_line not in p_generic
    assert "- No added words, letters or numbers in the artwork" in p_generic
    assert "- Graphic words: use exactly these" not in p_generic
    assert "- Do not add any words or numbers." not in p_generic


def test_bottom_third_template_line_and_no_placement_rules():
    """Verify bottom 35% template line is present, and placement rules are absent."""
    hero = _make_check(role="hero", path="/images/hero.jpg")
    brief = _make_brief()
    selection = PhotoSelection(hero=hero, context=())

    concept_words = PosterConcept(
        key_moment="Great race",
        scene="Hero on track",
        inset="none",
        graphic_words=("WIN",),
    )
    p = direct(brief, selection, concept=concept_words).prompt

    # Restored line is present
    assert "- The bottom 35% of the image will be covered later by the fan page's headline text. Keep that area simple and calm (for example a plain background, the track, the floor or soft out-of-focus scenery), with no faces, text or important detail. It does not need to be dark. Everything above it is the full artwork." in p
    # Placement rules and bottom 42% are absent
    assert "bottom 42%" not in p
    assert "upper 55%" not in p
    assert "Put the inset in the upper 40%" not in p

    # Final block does not contain bottom-zone avoid line
    blocks = p.split("\n\n")
    last_block = blocks[-1]
    assert last_block.startswith("Do NOT include anything below (very important):")
    assert "- Any text or graphic words in the bottom 35% of the image." in last_block


def test_realism_lines_have_no_sharpness_push():
    """Verify realism section has no sharpness push lines and keeps baseline realism."""
    hero = _make_check(role="hero", path="/images/hero.jpg")
    brief = _make_brief()
    selection = PhotoSelection(hero=hero, context=())

    p = direct(brief, selection).prompt
    assert "Sharp and detailed" not in p
    assert "- But keep it natural" not in p
    # Existing realism lines still present
    assert "- It must look like a real photograph by a professional sports photographer, then designed: realistic skin with pores and natural imperfections, real lens depth of field and motion, natural light and colour." in p
    assert "- No airbrushed, waxy or plastic skin, no painterly or illustrated textures, no over-saturated glow, no HDR, no AI sheen." in p
    assert "- Logos, sponsor names and numbers on kits, caps, helmets and vehicles must be copied from the reference photos, crisp and correct as in real photos. Never blank out, blur, cover or remove a real team or sponsor logo." in p


def test_prompt_never_asks_to_strip_logos():
    forbidden_phrases = (
        "leave it plain",
        "keep it plain",
        "ignore his clothing",
        "No words, letters or numbers anywhere",
    )
    must_have = "Never blank out, blur, cover or remove a real team or sponsor logo."

    ctx = _make_check(role="context", path="/images/ctx.jpg")
    hero_check = _make_check(role="hero", path="/images/hero.jpg")
    face_check = _make_check(role="hero", path="/images/face.jpg")

    # (a) hero-from-event case
    brief_a = _make_brief(series="Formula 1")
    sel_a = PhotoSelection(hero=hero_check, context=(ctx,))
    prompt_a = direct(brief_a, sel_a).prompt

    # (b) strict face_ref case
    brief_b = _make_brief(series="MotoGP")
    sel_b = PhotoSelection(hero=None, face_ref=face_check, context=(ctx,))
    prompt_b = direct(brief_b, sel_b).prompt

    # (c) non-strict face_ref case
    brief_c = _make_brief(series="Formula 1")
    sel_c = PhotoSelection(hero=None, face_ref=face_check, context=(ctx,))
    prompt_c = direct(brief_c, sel_c).prompt

    for p in (prompt_a, prompt_b, prompt_c):
        for phrase in forbidden_phrases:
            assert phrase not in p
        assert must_have in p
