import json
from types import SimpleNamespace
from unittest import mock

import pytest

from app.services.pro_poster.brief import (
    SERIES_BADGES,
    BriefError,
    brief_from_dict,
    brief_to_dict,
    build_brief,
    parse_brief,
    resolve_badge,
)
from app.services.pro_poster.types import EventContext, PosterBrief, Subject


def _make_mock_generate(canned_raw: str):
    def _mock(prompt, parse_fn, **kwargs):
        return parse_fn(canned_raw), "router"
    return _mock


def test_verstappen_gtwc_portimao(monkeypatch):
    canned = {
        "subjects": [{"name": "Max Verstappen", "role": "hero", "team": "Verstappen.com Racing"}],
        "series": "GT World Challenge Europe",
        "vehicle": "GT3 car",
        "circuit": "Portimao",
        "country": "Portugal",
        "session": "test",
        "year": 2026,
        "night": False,
        "rain": False,
        "story_type": "feature",
        "mood": "serious",
        "accent_colors": ["#FF8000", "#002B49"],
        "visual_ideas": ["Ferrari 296 GT3 car on track at Portimao", "Max Verstappen in race suit"],
        "must_avoid": ["Formula 1", "F1 cars", "Singapore night race"],
        "language": "en",
    }
    monkeypatch.setattr(
        "app.services.pro_poster.brief._generate_with_fallback",
        _make_mock_generate(json.dumps(canned)),
    )

    job = SimpleNamespace(id=12602, design_title="Verstappen GT3 Test", design_subtitle="Portimao run")
    article = SimpleNamespace(
        id=99,
        scraped_title="Max Verstappen tests GT3 Ferrari at Portimao",
        scraped_content="Max Verstappen took part in a closed test at Portimao...",
    )
    fanpage = SimpleNamespace(id=1, name="Redline News", mode2_gallery_niches=["GT World Challenge Europe"], mode2_badge_text=None)

    brief = build_brief(job, article, fanpage)

    assert brief.event.series == "GT World Challenge Europe"
    assert brief.event.vehicle == "GT3 car"
    assert brief.event.circuit == "Portimao"
    assert brief.badge_label == "GT WORLD CHALLENGE"
    assert "Formula 1" in brief.must_avoid
    assert len(brief.subjects) == 1
    assert brief.subjects[0].name == "Max Verstappen"
    assert brief.subjects[0].role == "hero"


def test_bulega_jerez_worldsbk(monkeypatch):
    canned = {
        "subjects": [{"name": "Nicolo Bulega", "role": "hero", "team": "Aruba.it Racing - Ducati"}],
        "series": "WorldSBK",
        "vehicle": "superbike",
        "circuit": "Jerez",
        "country": "Spain",
        "session": "race",
        "year": 2026,
        "night": False,
        "rain": False,
        "story_type": "result",
        "mood": "happy",
        "accent_colors": ["#CC0000", "#FFFFFF"],
        "visual_ideas": ["Ducati Panigale V4R celebrating"],
        "must_avoid": ["MotoGP", "MotoGP bikes"],
        "language": "en",
    }
    monkeypatch.setattr(
        "app.services.pro_poster.brief._generate_with_fallback",
        _make_mock_generate(json.dumps(canned)),
    )

    job = SimpleNamespace(id=12603, design_title="Bulega Dominates Jerez", design_subtitle="Race 1 victory")
    article = SimpleNamespace(id=100, scraped_title="Bulega wins Jerez WorldSBK opener", scraped_content="Nicolo Bulega took victory...")
    fanpage = SimpleNamespace(id=2, name="Superbike World", mode2_gallery_niches=["WorldSBK"], mode2_badge_text=None)

    brief = build_brief(job, article, fanpage)

    assert brief.badge_label == "WORLDSBK NEWS"
    assert brief.event.series == "WorldSBK"
    assert brief.event.circuit == "Jerez"


def test_ufc_malott(monkeypatch):
    canned = {
        "subjects": [{"name": "Mike Malott", "role": "hero", "team": None}],
        "series": "UFC",
        "vehicle": None,
        "circuit": "Scotiabank Arena",
        "country": "Canada",
        "session": "fight night",
        "year": 2026,
        "night": True,
        "rain": False,
        "story_type": "preview",
        "mood": "tense",
        "accent_colors": ["#D20A0A", "#000000"],
        "visual_ideas": ["Malott staredown"],
        "must_avoid": ["Boxing ring", "cars", "bikes"],
        "language": "en",
    }
    monkeypatch.setattr(
        "app.services.pro_poster.brief._generate_with_fallback",
        _make_mock_generate(json.dumps(canned)),
    )

    job = SimpleNamespace(id=12604, design_title="Malott Returns", design_subtitle="UFC 297 preview")
    article = SimpleNamespace(id=101, scraped_title="Mike Malott ready for UFC return", scraped_content="Canadian welterweight Mike Malott...")
    fanpage = SimpleNamespace(id=3, name="Octagon Central", mode2_gallery_niches=["UFC"], mode2_badge_text=None)

    brief = build_brief(job, article, fanpage)

    assert brief.event.vehicle is None
    assert brief.badge_label == "UFC NEWS"
    assert brief.event.series == "UFC"
    assert len(brief.subjects) == 1
    assert brief.subjects[0].name == "Mike Malott"


def test_subject_portimao_dropped():
    canned = {
        "subjects": [
            {"name": "Max Verstappen", "role": "hero", "team": "Verstappen.com Racing"},
            {"name": "Portimao", "role": "secondary", "team": None},
        ],
        "series": "GT World Challenge Europe",
        "vehicle": "GT3 car",
        "circuit": "Portimao",
        "country": "Portugal",
        "session": "test",
        "year": 2026,
        "story_type": "feature",
        "mood": "serious",
    }
    brief = parse_brief(json.dumps(canned), job_id=12602)

    names = [s.name for s in brief.subjects]
    assert "Max Verstappen" in names
    assert "Portimao" not in names
    assert len(brief.subjects) == 1


def test_bad_json_raises_brief_error(monkeypatch):
    monkeypatch.setattr(
        "app.services.pro_poster.brief._generate_with_fallback",
        _make_mock_generate("{not valid json: at all"),
    )

    job = SimpleNamespace(id=12605, design_title="Title", design_subtitle="Sub")
    article = SimpleNamespace(id=102, scraped_title="Scraped", scraped_content="Content")
    fanpage = SimpleNamespace(id=4, name="Page", mode2_gallery_niches=[], mode2_badge_text=None)

    with pytest.raises(BriefError):
        build_brief(job, article, fanpage)

    # Missing series
    monkeypatch.setattr(
        "app.services.pro_poster.brief._generate_with_fallback",
        _make_mock_generate(json.dumps({"subjects": [], "series": ""})),
    )
    with pytest.raises(BriefError):
        build_brief(job, article, fanpage)


def test_round_trip_brief_to_dict():
    brief = PosterBrief(
        job_id=42,
        subjects=(
            Subject(name="Max Verstappen", role="hero", team="Red Bull Racing"),
            Subject(name="Lando Norris", role="secondary", team="McLaren"),
        ),
        event=EventContext(
            series="Formula 1",
            vehicle="Formula 1 car",
            circuit="Monza",
            country="Italy",
            session="race",
            year=2026,
            night=False,
            rain=True,
        ),
        story_type="result",
        mood="dramatic",
        badge_label="F1 NEWS",
        accent_colors=("#FF8000", "#002B49"),
        visual_ideas=("Podium celebration with trophy",),
        must_avoid=("MotoGP bikes", "WEC cars"),
        language="en",
    )

    d = brief_to_dict(brief)
    restored = brief_from_dict(d, job_id=brief.job_id)

    assert restored == brief


def test_subject_drop_and_role_normalization():
    canned = {
        "subjects": [
            {"name": "Ferrari", "role": "hero", "team": "Ferrari"},  # team name dropped
            {"name": "driver", "role": "hero"},  # single word lowercase dropped
            {"name": "Charles Leclerc", "role": "hero", "team": "Ferrari"},
            {"name": "Carlos Sainz", "role": "hero", "team": "Ferrari"},  # second hero -> secondary
            {"name": "Italy", "role": "mentioned"},  # country dropped
        ],
        "series": "Formula 1",
        "vehicle": "Formula 1 car",
        "circuit": "Monza",
        "country": "Italy",
    }
    brief = parse_brief(json.dumps(canned), job_id=10)

    assert len(brief.subjects) == 2
    assert brief.subjects[0].name == "Charles Leclerc"
    assert brief.subjects[0].role == "hero"
    assert brief.subjects[1].name == "Carlos Sainz"
    assert brief.subjects[1].role == "secondary"


def test_unknown_series_and_badge_resolution():
    fanpage_with_badge = SimpleNamespace(mode2_badge_text="MOTORSPORT NEWS")
    assert resolve_badge("Super Formula") == "SUPER FORMULA NEWS"
    assert resolve_badge("", fanpage_with_badge) == "MOTORSPORT NEWS"
    assert resolve_badge(None, None) == ""

    canned = {
        "subjects": [{"name": "Liam Lawson", "role": "hero"}],
        "series": "Super Formula",
        "vehicle": "SF23",
    }
    brief = parse_brief(json.dumps(canned), job_id=11)
    assert brief.badge_label == "SUPER FORMULA NEWS"


def test_validation_fallbacks():
    canned = {
        "subjects": [{"name": "Lewis Hamilton", "role": "hero"}],
        "series": "Formula 1",
        "story_type": "invalid_story_type",
        "mood": "invalid_mood",
        "accent_colors": ["red", "#FFF", "#00A19B", "invalid"],
        "year": 1820,
    }
    brief = parse_brief(json.dumps(canned), job_id=12)

    assert brief.story_type == "other"
    assert brief.mood == "neutral"
    assert brief.accent_colors == ("#00A19B",)
    assert brief.event.year is None


@pytest.mark.parametrize(
    "raw_series,expected_series,expected_badge",
    [
        ("F1", "Formula 1", "F1 NEWS"),
        ("Formula One", "Formula 1", "F1 NEWS"),
        ("Formula 1 World Championship", "Formula 1", "F1 NEWS"),
        ("World Superbike", "WorldSBK", "WORLDSBK NEWS"),
        ("WSBK", "WorldSBK", "WORLDSBK NEWS"),
        ("Superbike World Championship", "WorldSBK", "WORLDSBK NEWS"),
        ("World Supersport", "WorldSSP", "WORLDSSP NEWS"),
        ("WSSP", "WorldSSP", "WORLDSSP NEWS"),
        ("WorldWCR", "WorldWCR", "WORLDWCR NEWS"),
        ("Women's Circuit Racing", "WorldWCR", "WORLDWCR NEWS"),
        ("GTWC", "GT World Challenge Europe", "GT WORLD CHALLENGE"),
        ("GT World Challenge", "GT World Challenge Europe", "GT WORLD CHALLENGE"),
        ("GTWC Europe", "GT World Challenge Europe", "GT WORLD CHALLENGE"),
        ("Fanatec GT World Challenge", "GT World Challenge Europe", "GT WORLD CHALLENGE"),
        ("World Endurance", "WEC", "WEC NEWS"),
        ("FIA WEC", "WEC", "WEC NEWS"),
        ("Le Mans", "WEC", "WEC NEWS"),
        ("Formula E", "Formula E", "FORMULA E NEWS"),
        ("ABB FIA Formula E", "Formula E", "FORMULA E NEWS"),
        ("IndyCar Series", "IndyCar", "INDYCAR NEWS"),
        ("UFC Fight Night", "UFC", "UFC NEWS"),
        ("UFC 300", "UFC", "UFC NEWS"),
        ("UFC 297", "UFC", "UFC NEWS"),
    ],
)
def test_series_aliases(raw_series, expected_series, expected_badge):
    canned = {
        "subjects": [{"name": "Hero Athlete", "role": "hero"}],
        "series": raw_series,
    }
    brief = parse_brief(json.dumps(canned), job_id=100)
    assert brief.event.series == expected_series
    assert brief.badge_label == expected_badge


def test_other_series_parsing():
    canned = {
        "subjects": [{"name": "Liam Lawson", "role": "hero"}],
        "series": "Other: Super Formula",
    }
    brief = parse_brief(json.dumps(canned), job_id=101)
    assert brief.event.series == "Super Formula"
    assert brief.badge_label == "SUPER FORMULA NEWS"

    canned_long = {
        "subjects": [{"name": "Johan Kristoffersson", "role": "hero"}],
        "series": "Other: World Rallycross",
    }
    brief_long = parse_brief(json.dumps(canned_long), job_id=102)
    assert brief_long.event.series == "World Rallycross"
    assert brief_long.badge_label == "WORLD RALLYCROSS NEWS"

    # Other: with an alias
    canned_alias = {
        "subjects": [{"name": "Hero Athlete", "role": "hero"}],
        "series": "Other: F1",
    }
    brief_alias = parse_brief(json.dumps(canned_alias), job_id=103)
    assert brief_alias.event.series == "Formula 1"
    assert brief_alias.badge_label == "F1 NEWS"

    # Empty Other: fails closed
    with pytest.raises(BriefError):
        parse_brief(json.dumps({"subjects": [], "series": "Other:"}))
    with pytest.raises(BriefError):
        parse_brief(json.dumps({"subjects": [], "series": "Other:   "}))

