"""Owner 2026-10-24: news posters follow the IG-recreate accuracy rules; MotoGP posters use only the reference images."""
from app.services.pro_poster.art_direction import direct, strict_references
from app.services.pro_poster.concept import _build_concept_prompt as build_concept_prompt
from app.services.pro_poster.types import PhotoSelection, Subject
from tests.test_pro_poster_art_direction import _make_brief, _make_check


def _motogp_brief():
    return _make_brief(series="MotoGP", vehicle="MotoGP bike", circuit="Sepang",
                       subjects=(Subject(name="Marc Marquez", role="hero", team="Ducati Lenovo Team"),))


def test_only_motogp_is_strict():
    assert strict_references(_motogp_brief())
    assert not strict_references(_make_brief())


def test_motogp_face_ref_poster_never_searches_the_web():
    selection = PhotoSelection(hero=None, face_ref=_make_check(role="face"), context=(_make_check(role="context", path="/c.jpg"),))
    prompt = direct(_motogp_brief(), selection).prompt
    assert "search the web" not in prompt.lower()
    assert "Use ONLY these reference images" in prompt
    assert "cropped from one of the event reference images above" in prompt


def test_motogp_without_context_photo_has_no_inset():
    prompt = direct(_motogp_brief(), PhotoSelection(hero=_make_check(), context=())).prompt
    assert "- No inset:" in prompt and "circular inset" not in prompt


def test_formula1_keeps_web_search_but_never_invents_people():
    selection = PhotoSelection(hero=_make_check(), context=(_make_check(role="context", path="/c.jpg"),))
    prompt = direct(_make_brief(), selection).prompt
    assert "search the web for recent real photos" in prompt
    assert "Never invent a person, a face or a vehicle" in prompt
    assert "One inset is enough; never add a second." in prompt


def test_every_poster_keeps_age_framing_and_sport():
    prompt = direct(_make_brief(), PhotoSelection(hero=_make_check(), context=())).prompt
    assert "exactly the age he has in the reference photos" in prompt
    assert "keep the whole upper body visible" in prompt
    assert "Everything in the poster belongs to Formula 1" in prompt


def test_concept_prompt_is_strict_for_motogp_only():
    sel = PhotoSelection(hero=_make_check(), context=())
    moto = build_concept_prompt(_motogp_brief(), sel, headline="Marquez wins", story_text="Marquez won in Sepang.")
    f1 = build_concept_prompt(_make_brief(), sel, headline="Verstappen wins", story_text="Verstappen won.")
    assert "STRICT for this series" in moto and "STRICT for this series" not in f1
    assert "Use one inset at most." in f1 and "nothing from another sport" in f1


def test_hero_news_card_uses_the_instagram_house_style_without_words():
    """Owner 2026-10-24: a news card with a person looks like the IG recreate card - big close hero, one inset, no words."""
    from app.services.pro_poster.types import PosterConcept
    concept = PosterConcept(key_moment="Antonelli leads", scene="Antonelli on a podium in Texas", inset="the Mercedes on track",
                            graphic_words=("102", "TEXAS"))
    prompt = direct(_make_brief(), PhotoSelection(hero=_make_check(), context=(_make_check(role="context", path="/c.jpg"),)),
                    concept=concept).prompt
    assert "the same as its Instagram cards" in prompt and "close and BIG" in prompt
    assert "softly out of focus" in prompt and "Do not stage a new scene" in prompt
    assert "- Main image:" not in prompt and '"102"' not in prompt and "- Graphic words:" not in prompt
    assert "- No added words, letters or numbers in the artwork" in prompt
    assert "large circle (about 40-45% of the card width)" in prompt
