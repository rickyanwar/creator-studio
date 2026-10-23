"""Art direction service for Pro poster generation (v4: front-facing face reference and house style).

Translates a PosterBrief and PhotoSelection into a professional, non-AI-looking
poster prompt and ordered reference images for the visual engine.
"""
from __future__ import annotations

from app.services.prompt_rules import NO_SPLIT_LINE, TEMPLATE_TEXT_ZONE_PCT
from app.services.pro_poster.types import (
    ArtDirection,
    PhotoSelection,
    PosterBrief,
    PosterConcept,
    Subject,
)


def _hero_subject(brief: PosterBrief) -> Subject | None:
    """Find the hero subject from the brief."""
    for subject in brief.subjects:
        if subject.role == "hero":
            return subject
    return None


def event_line(brief: PosterBrief) -> str:
    """Format the event line joining known details with commas."""
    hero = _hero_subject(brief)
    ev = brief.event
    parts: list[str] = [ev.series.strip()]
    if ev.vehicle and ev.vehicle.strip():
        v = ev.vehicle.strip()
        parts.append(v if v.lower().startswith("the ") else f"the {v}")
    if hero and hero.team and hero.team.strip():
        parts.append(hero.team.strip())
    if ev.circuit and ev.circuit.strip():
        parts.append(ev.circuit.strip())
    if ev.country and ev.country.strip():
        parts.append(ev.country.strip())
    if ev.session and ev.session.strip():
        parts.append(ev.session.strip())
    if ev.year is not None:
        parts.append(str(ev.year))
    return ", ".join(parts)


def clean_story(story_text: str | None) -> str | None:
    """Collapse whitespace, strip markdown bold, truncate to 1200 chars at a space boundary."""
    if not story_text:
        return None
    cleaned = " ".join(story_text.replace("**", "").split())
    if not cleaned:
        return None
    if len(cleaned) > 1200:
        sub = cleaned[:1200]
        idx = sub.rfind(" ")
        cleaned = sub[:idx].rstrip() if idx != -1 else sub
    return cleaned or None


STRICT_REFERENCE_SERIES = ("motogp",)  # owner 2026-10-24: ChatGPT may only use the reference images we send, never search or imagine


def strict_references(brief: PosterBrief) -> bool:
    series = (brief.event.series or "").lower().replace(" ", "")
    return any(s in series for s in STRICT_REFERENCE_SERIES)


def choose_layout(brief: PosterBrief, selection: PhotoSelection) -> str:
    """Choose card layout based on available verified references."""
    if selection.hero is not None:
        return "hero_with_context" if selection.context else "hero_solo"
    if selection.face_ref is not None:
        return "face_ref_scene"
    if selection.context:
        return "action_full_bleed"
    return "typographic"


def direct(
    brief: PosterBrief,
    selection: PhotoSelection,
    *,
    headline: str | None = None,
    story_text: str | None = None,
    concept: PosterConcept | None = None,
) -> ArtDirection:
    """Generate story-driven designer brief prompt and ordered reference images."""
    layout = choose_layout(brief, selection)
    strict = strict_references(brief)
    if layout == "typographic":
        return ArtDirection(layout="typographic", prompt="", refs=(), allowed_text=())

    refs_list: list[str] = []
    if selection.hero is not None:
        refs_list.append(selection.hero.candidate.path_or_url)
        if selection.portrait is not None:
            refs_list.append(selection.portrait.candidate.path_or_url)
    elif selection.face_ref is not None:
        refs_list.append(selection.face_ref.candidate.path_or_url)
    for ctx in selection.context:
        refs_list.append(ctx.candidate.path_or_url)
    refs = tuple(refs_list)

    hero_subj = _hero_subject(brief)
    hero = hero_subj.name if hero_subj is not None else "the athletes in the story"

    opening = (
        f"You are a senior sports poster designer for a {brief.event.series} fan page. "
        "Read the story, choose the ONE strongest visual idea that tells this specific story at a glance, "
        "then design the poster artwork (vertical 4:5)."
    )

    story_lines: list[str] = []
    if headline and headline.strip():
        cleaned_hl = " ".join(headline.replace("**", "").split())
        if cleaned_hl:
            story_lines.append(f'Story headline: "{cleaned_hl}"')
    cleaned_st = clean_story(story_text)
    if cleaned_st:
        story_lines.append(f'Story text (from the source article):\n"""{cleaned_st}"""')

    event_block = f"Event: {event_line(brief)}"

    ref_lines: list[str] = ["Reference photos (real, from a photo agency):"]
    img_num = 1
    if selection.hero is not None:
        team_str = (
            f" ({hero_subj.team.strip()})"
            if hero_subj and hero_subj.team and hero_subj.team.strip()
            else ""
        )
        ref_lines.append(
            f"- Image {img_num}: a real press photo of {hero}{team_str}. "
            "Keep his face, helmet design, race number, kit, vehicle livery and sponsor logos exactly as they are."
        )
        img_num += 1
        if selection.portrait is not None:
            ref_lines.append(
                f"- Image {img_num}: a real front-facing photo of {hero}. Use it for his exact face and likeness."
            )
            img_num += 1
    elif selection.face_ref is not None:
        loc_parts = [brief.event.series]
        if brief.event.circuit and brief.event.circuit.strip():
            loc_parts.append(brief.event.circuit.strip())
        if brief.event.year is not None:
            loc_parts.append(str(brief.event.year))
        at_event = " ".join(loc_parts)
        if strict:
            ref_lines.append(
                f"- Image {img_num}: a real photo of {hero} from a different event. "
                "Use it for his exact face and likeness, and keep his kit, helmet and bike exactly as this photo shows them; "
                "do not change them to another season's design you are not shown. Show his face from the front like this photo."
            )
        else:
            ref_lines.append(
                f"- Image {img_num}: a real photo of {hero} from a different event. "
                "Use it ONLY for his face and likeness; ignore his clothing, vehicle and background. "
                f"Search the web for real photos of {hero} at {at_event} so his kit, helmet and vehicle are accurate for this event. "
                "Show his face from the front like this photo."
            )
        img_num += 1

    for ctx in selection.context:
        veh_clause = ""
        if brief.event.vehicle and brief.event.vehicle.strip():
            v = brief.event.vehicle.strip()
            veh_str = v if v.lower().startswith("the ") else f"the {v}"
            veh_clause = f" of {veh_str}"
        circ_clause = ""
        if brief.event.circuit and brief.event.circuit.strip():
            circ_clause = f" at {brief.event.circuit.strip()}"
        ref_lines.append(
            f"- Image {img_num}: a real {brief.event.series} photo{veh_clause}{circ_clause}. "
            "Use it as event context (the setting, the vehicles, the atmosphere)."
        )
        img_num += 1

    if strict:
        ref_lines.append(
            "Use ONLY these reference images. Do not search for, copy or invent any other photo, person, bike, kit or venue. "
            "If something is not shown in a reference image, leave it out or keep it plain."
        )
    else:
        ref_lines.append(
            "If you are not sure how the venue, the kit or the vehicle look at this event, search the web for recent real photos of them first. "
            "Never invent a person, a face or a vehicle that is not in the reference images."
        )

    must_do_lines = [
        "What the poster must do:",
        "- Tell THIS story, not just show the athlete on a new background. Show the moment, the result or what is at stake as the story describes it (for example the decisive move, the win, the trophy or podium, the penalty, the comeback, the record), in the real setting of this event.",
        "- Stay true to the story: do not invent results, places, people or objects that contradict it. It must be physically logical and believable as a real photo.",
    ]
    if hero_subj is not None and (selection.hero is not None or selection.face_ref is not None):
        must_do_lines.append(
            f"- {hero} must be recognisably himself: show his face clearly, turned toward the camera (front or three-quarter view), with his real face and likeness from the reference photos; never show him only from behind or with his face hidden. Keep his face high in the image: eyes at about 25-35% of the image height and the chin above the middle of the image, so the headline never covers it."
        )
    if selection.hero is None and selection.face_ref is None:
        must_do_lines.append(
            "- Do not show any identifiable person's face. Tell the story with the vehicles, the venue, the trophy and graphics."
        )
    if hero_subj is not None and (selection.hero is not None or selection.face_ref is not None):
        must_do_lines.append(
            f"- Show {hero} at exactly the age he has in the reference photos: never older, younger, slimmer or restyled, and never a different person who looks similar. "
            "If he is framed from the waist up, keep the whole upper body visible (head, both shoulders, any raised arm, hand or trophy) and never slice him at the neck, chest or elbows."
        )
    main_subject_str = hero_subj.name if hero_subj is not None else "the main subject"
    context_count = len(selection.context)
    if strict:
        if context_count:
            must_do_lines.append(
                f"- House style, when it helps the story: {main_subject_str} large in the frame, plus at most ONE circular inset with a thin white ring in the upper part of the image, its top edge no higher than 12% of the image height (the page logo sits in the top corners). "
                "The inset must be cropped from one of the event reference images above (say which in your design), showing a different moment or detail than the main image. "
                "Never generate an inset that is not in a reference image, and never repeat the same person or pose."
            )
        else:
            must_do_lines.append(
                f"- No inset: {main_subject_str} large in the frame on the real setting of the reference photo."
            )
    else:
        must_do_lines.append(
            f"- House style, when it helps the story: {main_subject_str} large in the frame, plus ONE circular inset with a thin white ring in the upper part of the image, its top edge no higher than 12% of the image height (the page logo sits in the top corners) showing a DIFFERENT moment or detail that adds context and matches the story (for example the trophy, the car on track, the venue, the rival). "
            "One inset is enough; never add a second. An inset showing a person must come from a reference image; without one, show only a place or object named in the story. Never repeat the same person or pose in the inset."
        )
    must_do_lines.append(
        f"- Everything in the poster belongs to {brief.event.series}: no athletes, venues or equipment from another sport (no octagon, ring, fighter, football pitch or court)."
    )
    if concept is not None:
        if concept.graphic_words:
            words_joined = ", ".join(f'"{w}"' for w in concept.graphic_words)
            must_do_lines.append(
                f'- Graphic words: use exactly these and nothing else, each at most once, spelled exactly letter by letter: {words_joined}. '
                f'Place them in the upper half of the image, well above the bottom {TEMPLATE_TEXT_ZONE_PCT}% (the page badge sits just above that area).'
            )
        else:
            must_do_lines.append("- Do not add any words or numbers.")
    else:
        must_do_lines.append(
            f"- You may add at most three short graphic words or numbers that come from the story (for example a finishing position, a race count or a record), spelled exactly letter by letter, placed in the upper half of the image, well above the bottom {TEMPLATE_TEXT_ZONE_PCT}%. No other text."
        )

    realism_lines = [
        "Photographic realism (very important):",
        "- It must look like a real photograph by a professional sports photographer, then designed: realistic skin with pores and natural imperfections, real lens depth of field and motion, natural light and colour.",
        "- No airbrushed, waxy or plastic skin, no painterly or illustrated textures, no over-saturated glow, no HDR, no AI sheen.",
        "- Logos, numbers and text on kits and vehicles must be crisp and correct as in real photos; if you cannot render one correctly, leave it plain instead of inventing letters.",
    ]

    template_lines = [
        "Space for the fan page template:",
        f"- The bottom {TEMPLATE_TEXT_ZONE_PCT}% of the image will be covered later by the fan page's headline text. Keep that area simple and calm (for example a plain background, the track, the floor or soft out-of-focus scenery), with no faces, text or important detail. It does not need to be dark. Everything above it is the full artwork.",
        "- Keep the top 12% of the image free of insets, faces and graphic words: the page logo and name sit in the top corners.",
    ]

    avoid_lines = [
        "Do NOT include anything below (very important):",
        "- No official series logo or wordmark anywhere in the image: not on banners, screens, walls, the track, the octagon mat or fence, or podium backdrops (for example F1, Formula 1, FIA, MotoGP, FIM, Dorna, UFC). Use plain unbranded surfaces there. A small series logo that is part of an athlete's real gear (gloves, fight shorts, race suit, helmet) is fine. Team, manufacturer and sponsor logos (Ferrari, Red Bull, Ducati, Mercedes, Monster and so on) are fine and must look real. No watermark.",
        NO_SPLIT_LINE,
    ]
    avoid_lines.append(f"- Any text or graphic words in the bottom {TEMPLATE_TEXT_ZONE_PCT}% of the image.")
    for item in brief.must_avoid:
        cleaned = item.strip().rstrip(".")
        if cleaned.lower().startswith("do not show"):
            cleaned = cleaned[len("do not show"):].strip()
        if cleaned:
            avoid_lines.append(f"- Do not show {cleaned}.")
    avoid_lines.append("- Never mix in another series' cars, bikes or logos.")

    blocks = [opening]
    if story_lines:
        blocks.append("\n".join(story_lines))
    blocks.append(event_block)
    if refs:
        blocks.append("\n".join(ref_lines))
    if concept is not None:
        concept_block_lines = [
            "Visual concept (decided by the art director — follow it):",
            f"- Key moment: {concept.key_moment.strip()}",
            f"- Main image: {concept.scene.strip()}",
        ]
        if concept.inset and concept.inset.strip().lower() != "none":
            concept_block_lines.append(f"- Inset: {concept.inset.strip()}")
        if concept.palette and concept.palette.strip():
            concept_block_lines.append(f"- Colour palette: {concept.palette.strip()}")
        if concept.mood and concept.mood.strip():
            concept_block_lines.append(f"- Mood: {concept.mood.strip()}")
        blocks.append("\n".join(concept_block_lines))
    blocks.append("\n".join(must_do_lines))
    blocks.append("\n".join(realism_lines))
    blocks.append("\n".join(template_lines))
    blocks.append("\n".join(avoid_lines))

    prompt = "\n\n".join(blocks)
    return ArtDirection(layout=layout, prompt=prompt, refs=refs, allowed_text=())


def retry_prompt(direction: ArtDirection, fix_hint: str) -> ArtDirection:
    """Append QA review correction hint to an existing art direction."""
    return ArtDirection(
        layout=direction.layout,
        prompt=f"{direction.prompt}\nCorrection from review: {fix_hint}",
        refs=direction.refs,
        size=direction.size,
        allowed_text=direction.allowed_text,
    )
