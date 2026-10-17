from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import speaker_design
from app.services.speaker_design import _as_naive_utc, pick_moment_photos, pick_speaker_photos


def test_as_naive_utc_conversions():
    # date -> midnight datetime
    d = date(2026, 10, 18)
    assert _as_naive_utc(d) == datetime(2026, 10, 18, 0, 0, 0)

    # aware datetime (WIB: UTC+7) -> naive UTC datetime
    wib = timezone(timedelta(hours=7))
    aware_wib = datetime(2026, 10, 18, 14, 30, tzinfo=wib)
    res_wib = _as_naive_utc(aware_wib)
    assert res_wib == datetime(2026, 10, 18, 7, 30, 0)
    assert res_wib.tzinfo is None

    # naive datetime unchanged
    naive = datetime(2026, 10, 18, 12, 0, 0)
    res_naive = _as_naive_utc(naive)
    assert res_naive == naive
    assert res_naive.tzinfo is None

    # None -> datetime.min
    assert _as_naive_utc(None) == datetime.min

    # Unknown type -> datetime.min
    assert _as_naive_utc("invalid") == datetime.min


def test_pick_moment_photos_handles_date_and_aware_around(monkeypatch):
    around_aware = datetime(2026, 10, 18, 12, 0, tzinfo=timezone(timedelta(hours=7)))

    old_row = SimpleNamespace(
        captured_at=date(2026, 10, 8),
        downloaded_at=datetime(2026, 10, 8, 10, 0),
        label="action",
        attributes={"shot_type": "action", "caption": "battle on track"},
        source_image_url="http://example.com/old.jpg",
    )
    recent_row = SimpleNamespace(
        captured_at=date(2026, 10, 17),
        downloaded_at=datetime(2026, 10, 17, 10, 0),
        label="action",
        attributes={"shot_type": "action", "caption": "battle on track"},
        source_image_url="http://example.com/recent.jpg",
    )

    monkeypatch.setattr(speaker_design, "_rows", lambda db, names: [old_row, recent_row])

    # Must NOT raise TypeError
    chosen = pick_moment_photos(
        None,
        ["Marc Marquez"],
        around_aware,
        article_text="Marc Marquez in a fierce battle on track",
    )

    assert chosen == [recent_row]


def test_pick_speaker_photos_sort_mixes_date_and_datetime(monkeypatch):
    row_date_only = SimpleNamespace(
        captured_at=date(2026, 10, 16),
        downloaded_at=None,
        label="face",
        attributes={"shot_type": "portrait"},
    )
    row_datetime_naive = SimpleNamespace(
        captured_at=None,
        downloaded_at=datetime(2026, 10, 17, 15, 30),
        label="face",
        attributes={"shot_type": "portrait"},
    )
    row_newest_date = SimpleNamespace(
        captured_at=date(2026, 10, 18),
        downloaded_at=None,
        label="face",
        attributes={"shot_type": "portrait"},
    )

    monkeypatch.setattr(
        speaker_design,
        "_rows",
        lambda db, names: [row_date_only, row_datetime_naive, row_newest_date],
    )
    monkeypatch.setattr(speaker_design, "_usable_face", lambda *args, **kwargs: True)

    # Must NOT raise TypeError when sorting rows with date vs naive datetime
    chosen = pick_speaker_photos(None, "Marc Marquez")

    assert chosen == [row_newest_date, row_datetime_naive]
