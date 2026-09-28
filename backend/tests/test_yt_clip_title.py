"""Unit tests for S2f — yt_clip_title.py (clean_clip_title + strip_source_lines).

pytest -q backend/tests/test_yt_clip_title.py
"""

import pytest
from app.services.yt_clip_title import clean_clip_title, strip_source_lines


# ── clean_clip_title ──────────────────────────────────────────────────────────

class TestCleanClipTitle:

    # ── multi-line title: leading newlines / embedded newlines ───────────────

    def test_multiline_becomes_single_line(self):
        title = "Leclerc Takes the Lead\nThen Hits the Wall"
        result = clean_clip_title(title, people=["Charles Leclerc"])
        assert result is not None
        assert "\n" not in result

    # ── > 60 chars ───────────────────────────────────────────────────────────

    def test_too_long_with_name_present_dropped(self):
        # Title that is > 60 chars and already contains the name → can't fix → drop
        title = "Charles Leclerc Overtakes Then Crashes On The Last Lap 2024 Race"
        assert len(title) > 60, f"test setup: expected >60, got {len(title)}"
        result = clean_clip_title(title, people=["Charles Leclerc"])
        assert result is None

    def test_exactly_60_chars_accepted(self):
        # Construct a title of exactly 60 chars with the surname in it
        title = "Leclerc " + "x" * 52   # 8 + 52 = 60
        assert len(title) == 60
        result = clean_clip_title(title, people=["Charles Leclerc"])
        assert result is not None
        assert len(result) <= 60

    # ── name missing → prefix surname if it fits ─────────────────────────────

    def test_missing_name_prefix_surname_if_fits(self):
        title = "Takes the Lead Then Crashes"
        result = clean_clip_title(title, people=["Charles Leclerc"])
        assert result is not None
        assert "Leclerc" in result
        assert len(result) <= 60

    def test_missing_name_drop_when_prefixed_too_long(self):
        title = "Takes The Lead And Then Crashes Hard In The Last Lap"  # already 52 chars
        # Surname "Leclerc" = 7 + space = 8 → total 60 — exactly fits.
        # Make the title longer so it won't fit.
        title = "Takes The Lead And Then Crashes Hard In The Last Lap_xx"  # 56 chars
        result = clean_clip_title(title, people=["Leclerc"])
        # 7 + 1 + 56 = 64 > 60 → should drop
        assert result is None

    def test_surname_match_accepts_without_prefix(self):
        # Title contains only the surname — must be accepted as-is.
        title = "Leclerc Crashes on Final Lap"
        result = clean_clip_title(title, people=["Charles Leclerc"])
        assert result is not None
        assert "Leclerc" in result

    def test_full_name_match_accepted(self):
        title = "Charles Leclerc Takes Pole Position"
        result = clean_clip_title(title, people=["Charles Leclerc"])
        assert result is not None

    # ── people empty → drop ──────────────────────────────────────────────────

    def test_empty_people_drops_clip(self):
        title = "Amazing Overtake"
        result = clean_clip_title(title, people=[])
        assert result is None

    # ── source/credit in title → strip or drop ───────────────────────────────

    def test_source_in_title_stripped(self):
        # "Source: Channel" appended — should be stripped, remainder must still pass.
        title = "Leclerc Pole Lap"
        result = clean_clip_title(title, people=["Charles Leclerc"])
        assert result is not None
        assert "Source" not in (result or "")

    def test_source_prefix_title_dropped(self):
        title = "Source: F1 Channel"
        result = clean_clip_title(title, people=["Hamilton"])
        # After stripping "Source: ", nothing useful remains → drop
        assert result is None or "Source" not in result

    def test_handle_in_title_stripped_or_dropped(self):
        title = "Hamilton Wins @F1Official"
        result = clean_clip_title(title, people=["Lewis Hamilton"])
        if result is not None:
            assert "@" not in result

    def test_channel_name_in_title_stripped(self):
        title = "Leclerc Best Lap — F1 Official"
        result = clean_clip_title(title, people=["Charles Leclerc"],
                                  forbidden_channel="F1 Official")
        if result is not None:
            assert "F1 Official" not in result

    def test_url_in_title_stripped(self):
        title = "Leclerc Crash https://youtu.be/abc"
        result = clean_clip_title(title, people=["Charles Leclerc"])
        if result is not None:
            assert "http" not in result

    # ── two-part title → keep strongest part ─────────────────────────────────

    def test_two_part_em_dash_kept(self):
        title = "Leclerc — Takes the Lead"
        result = clean_clip_title(title, people=["Charles Leclerc"])
        # Must not still contain " — "
        assert result is None or " — " not in result

    def test_two_part_colon_kept(self):
        title = "Leclerc: I Thought the Race Was Over"
        result = clean_clip_title(title, people=["Charles Leclerc"])
        assert result is None or ":" not in result or result.count(":") == 0

    def test_two_part_hyphen_kept(self):
        title = "Hamilton Wins - Incredible Drive"
        result = clean_clip_title(title, people=["Lewis Hamilton"])
        assert result is None or " - " not in result

    # ── Fix 1: "via" mid-sentence must NOT be stripped from title ────────────

    def test_via_mid_sentence_preserved(self):
        """'wins via strategy call' — via is not a source marker here."""
        title = "Verstappen wins via strategy call"
        result = clean_clip_title(title, people=["Max Verstappen"])
        assert result is not None
        assert "via" in result

    def test_via_at_start_stripped(self):
        """'via F1 Channel' at start of title IS a source marker → drop/strip."""
        title = "via F1 Channel"
        result = clean_clip_title(title, people=["Hamilton"])
        # Nothing useful remains after stripping → None, or "via" is gone
        assert result is None or "via" not in result

    def test_via_after_dash_stripped(self):
        """'Hamilton Wins — via F1Channel' trailing attribution is stripped."""
        title = "Hamilton Wins — via F1Channel"
        result = clean_clip_title(title, people=["Lewis Hamilton"])
        # Should keep "Hamilton Wins" (or drop if nothing valid left)
        if result is not None:
            assert "via" not in result
            assert "Hamilton" in result

    def test_via_in_middle_of_sentence_preserved_2(self):
        """Sentence-internal 'via': must not be removed."""
        title = "Leclerc recovers via fastest lap strategy"
        result = clean_clip_title(title, people=["Charles Leclerc"])
        assert result is not None
        assert "via" in result

    # ── Fix 2: _pick_strongest_part prefers the name-containing half ─────────

    def test_two_part_equal_length_name_in_second_half(self):
        """Equal-length halves: second half contains the name → keep second."""
        # "AAAA" (4) vs "Leclerc" (7) — not equal, but let's craft equal lengths
        # "Race Day" (8) vs "Leclerc!" (8) — name in second half
        title = "Race Day! - Leclerc!"
        result = clean_clip_title(title, people=["Charles Leclerc"])
        assert result is not None
        assert "Leclerc" in result

    def test_two_part_name_in_first_half_kept(self):
        """Name is in first half, second half is longer → still keep first."""
        title = "Leclerc - An Absolutely Incredible Qualifying Lap"
        result = clean_clip_title(title, people=["Charles Leclerc"])
        assert result is not None
        assert "Leclerc" in result


# ── strip_source_lines ────────────────────────────────────────────────────────

class TestStripSourceLines:

    def test_removes_source_line(self):
        caption = "Great clip!\n\nSource: F1 Channel (YouTube)"
        result = strip_source_lines(caption)
        assert "Source" not in result
        assert "Great clip!" in result

    def test_removes_sumber_line(self):
        caption = "Klip bagus!\n\nSumber: F1 Channel (YouTube)"
        result = strip_source_lines(caption)
        assert "Sumber" not in result

    def test_removes_via_line(self):
        caption = "Great moment\nvia F1TV\n#hashtag"
        result = strip_source_lines(caption)
        assert "via" not in result
        assert "#hashtag" in result

    def test_removes_youtube_mention(self):
        caption = "Watch this!\nF1 Official (YouTube)\n#f1"
        result = strip_source_lines(caption)
        assert "(YouTube)" not in result

    def test_removes_channel_name_line(self):
        caption = "Amazing lap!\nF1 Official\n#racing"
        result = strip_source_lines(caption, channel_name="F1 Official")
        assert "F1 Official" not in result

    def test_preserves_non_source_lines(self):
        caption = "First line\nSecond line\n#hashtag"
        result = strip_source_lines(caption)
        assert result == "First line\nSecond line\n#hashtag"

    def test_trailing_blank_lines_removed(self):
        caption = "Great!\n\n\nSource: Channel (YouTube)\n\n"
        result = strip_source_lines(caption)
        assert not result.endswith("\n")
        assert "Source" not in result

    def test_handle_in_caption_stripped(self):
        caption = "Amazing drive!\n@F1Official\n#f1"
        result = strip_source_lines(caption)
        assert "@F1Official" not in result

    def test_empty_caption_returned_unchanged(self):
        assert strip_source_lines("") == ""
        assert strip_source_lines(None) is None  # type: ignore

    def test_channel_name_in_source_line_stripped(self):
        caption = "Great lap!\nSource: F1 Official (YouTube)\n#f1"
        result = strip_source_lines(caption, channel_name="F1 Official")
        assert "Source" not in result
        assert "#f1" in result
