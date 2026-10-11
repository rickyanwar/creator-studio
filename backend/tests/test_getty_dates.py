from datetime import date

from app.services.getty_dates import build_hints, parse_asset_id, estimate_date_from_anchors


def test_parse_asset_id_and_slug_hints():
    url = "https://media.gettyimages.com/id/123456789/photo/losail-qatar-max-verstappen-during-race.jpg"
    assert parse_asset_id(url) == 123456789
    hints = build_hints(url)
    assert hints["slug"] == "losail-qatar-max-verstappen-during-race"
    assert hints["location"] == "Losail Qatar Max Verstappen"
    assert hints["session_hint"] == "race"


def test_estimate_date_interpolates_and_marks_confidence():
    anchors = [(100, date(2026, 1, 1)), (200, date(2026, 1, 3))]
    assert estimate_date_from_anchors(150, anchors) == (date(2026, 1, 2), "day")
    assert estimate_date_from_anchors(50, anchors) == (date(2026, 1, 1), "estimated")


def test_estimate_date_drops_a_non_monotonic_outlier():
    anchors = [(100, date(2026, 1, 1)), (150, date(2025, 12, 1)), (200, date(2026, 1, 3))]
    assert estimate_date_from_anchors(150, anchors) == (date(2026, 1, 2), "day")
