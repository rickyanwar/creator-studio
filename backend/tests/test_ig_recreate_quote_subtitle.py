from types import SimpleNamespace

from app.tasks.ig_recreate import _quote_subtitle


def _analysis(speaker="Lewis Hamilton", secondary="F1 race in Africa", topic=""):
    return SimpleNamespace(speaker=speaker, secondary=secondary, topic=topic)


def test_plain_ig_recreate_quote_shows_the_speaker_full_name():
    assert _quote_subtitle(_analysis(), from_radar=False) == "LEWIS HAMILTON"


def test_radar_quote_keeps_the_surname_on_topic_line():
    assert _quote_subtitle(_analysis(topic="Leclerc"), from_radar=True) == "HAMILTON ON LECLERC"


def test_no_speaker_gives_no_line():
    assert _quote_subtitle(_analysis(speaker=""), from_radar=False) == ""
