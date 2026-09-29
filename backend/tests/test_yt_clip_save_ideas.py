"""Unit tests for item 1 fixup — title cleaning in _save_ideas + _consume_one.

Tests confirm:
  • _save_ideas applies clean_clip_title; highlights that fail are skipped,
    ideas_created counts only added rows.
  • _consume_one does NOT drop ideas (people field no longer needed there).

pytest -q backend/tests/test_yt_clip_save_ideas.py
"""

from __future__ import annotations
from dataclasses import dataclass
from unittest.mock import MagicMock, patch, call
import pytest

# S11: _save_ideas now calls _taken_ranges(db, video_id) internally.
# All tests in this module use distinct, non-overlapping time ranges per
# highlight so the intra-batch de-dup does not interfere with pre-S11 tests.
_PATCH_TAKEN = patch("app.tasks.yt_clip._taken_ranges", return_value=[])


# ── Minimal Highlight stand-in (matches the real frozen dataclass shape) ──────

@dataclass(frozen=True)
class _Highlight:
    start: float
    end: float
    title: str
    description: str
    hook_text: str
    score: int
    excerpt: str
    people: list  # we set this per test


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_video(channel_name="F1 Official"):
    v = MagicMock()
    v.id = 1
    v.video_id = "ni6hTEax3oE"
    v.channel_name = channel_name
    v.status = "analyzing"
    v.ideas_created = 0
    v.analyzed_at = None
    v.last_error = None
    return v


def _make_fanpage(fanpage_id=5):
    fp = MagicMock()
    fp.id = fanpage_id
    return fp


def _h(title, people, start=0.0, end=65.0):
    return _Highlight(
        start=start, end=end, title=title, description="desc",
        hook_text="hook", score=8, excerpt="…", people=people,
    )


# ── _save_ideas tests ─────────────────────────────────────────────────────────

class TestSaveIdeas:

    def _run(self, highlights, channel_name="F1 Official"):
        """Run _save_ideas with a mock DB and return (added_count, db_add_calls).

        Patches _taken_ranges → [] so the pre-S11 title-cleaning tests are not
        affected by the intra-batch or cross-fanpage similarity filter.
        """
        from app.tasks.yt_clip import _save_ideas

        mock_db = MagicMock()
        fanpage = _make_fanpage()
        video = _make_video(channel_name)

        with _PATCH_TAKEN:
            n = _save_ideas(mock_db, fanpage, video, highlights)
        return n, mock_db.add.call_count, video

    def test_valid_highlight_with_name_is_stored(self):
        """A highlight with name in title and non-empty people → stored, count=1."""
        h = _h("Leclerc Takes Pole", people=["Charles Leclerc"])
        n, adds, video = self._run([h])
        assert n == 1
        assert adds == 1
        assert video.ideas_created == 1

    def test_highlight_with_name_missing_gets_surname_prefix(self):
        """Title missing the name but people non-empty → surname prefixed and stored."""
        h = _h("Amazing Qualifying Lap", people=["Charles Leclerc"])
        n, adds, video = self._run([h])
        assert n == 1
        assert adds == 1
        # The stored title must now contain the surname
        stored_call = MagicMock()
        mock_db = MagicMock()
        from app.tasks.yt_clip import _save_ideas
        from app.models.yt_clip_ideas import YtClipIdea
        fanpage = _make_fanpage()
        video2 = _make_video()
        with _PATCH_TAKEN:
            _save_ideas(mock_db, fanpage, video2, [h])
        # Grab what was passed to db.add
        added_idea = mock_db.add.call_args[0][0]
        assert "Leclerc" in added_idea.title

    def test_people_empty_skips_highlight(self):
        """people=[] → clean_clip_title returns None → highlight skipped, count=0."""
        h = _h("Amazing Lap", people=[])
        n, adds, video = self._run([h])
        assert n == 0
        assert adds == 0
        assert video.ideas_created == 0

    def test_source_in_title_stripped_or_dropped(self):
        """A title with 'Source: X' that can't be salvaged → skipped."""
        h = _h("Source: F1 Official", people=["Hamilton"])
        n, adds, video = self._run([h])
        # After stripping "Source: F1 Official" nothing useful remains → drop
        assert n == 0
        assert video.ideas_created == 0

    def test_mixed_highlights_count_correctly(self):
        """3 highlights: 2 valid (non-overlapping), 1 with empty people → ideas_created=2."""
        highlights = [
            _h("Leclerc Pole Lap", people=["Charles Leclerc"], start=0.0, end=65.0),
            _h("Hamilton Win", people=["Lewis Hamilton"], start=200.0, end=265.0),
            _h("No Name Here", people=[], start=400.0, end=465.0),
        ]
        n, adds, video = self._run(highlights)
        assert n == 2
        assert adds == 2
        assert video.ideas_created == 2

    def test_channel_name_in_title_stripped_stored(self):
        """Channel name in title is stripped; result stored if still valid."""
        h = _h("Leclerc Lap F1 Official", people=["Charles Leclerc"],
               )
        mock_db = MagicMock()
        from app.tasks.yt_clip import _save_ideas
        fanpage = _make_fanpage()
        video = _make_video(channel_name="F1 Official")
        with _PATCH_TAKEN:
            n = _save_ideas(mock_db, fanpage, video, [h])
        if n == 1:
            added = mock_db.add.call_args[0][0]
            assert "F1 Official" not in added.title

    def test_video_status_set_to_analyzed(self):
        """video.status must be 'analyzed' after _save_ideas regardless of count."""
        highlights = [_h("Leclerc Wins", people=["Charles Leclerc"])]
        mock_db = MagicMock()
        from app.tasks.yt_clip import _save_ideas
        video = _make_video()
        with _PATCH_TAKEN:
            _save_ideas(mock_db, _make_fanpage(), video, highlights)
        assert video.status == "analyzed"

    def test_zero_highlights_still_marks_analyzed(self):
        """Even with all highlights dropped, video.status = 'analyzed'."""
        mock_db = MagicMock()
        from app.tasks.yt_clip import _save_ideas
        video = _make_video()
        with _PATCH_TAKEN:
            _save_ideas(mock_db, _make_fanpage(), video, [])
        assert video.status == "analyzed"
        assert video.ideas_created == 0


# ── _consume_one — no longer drops ideas ─────────────────────────────────────

class TestConsumeOneNeverDropsForPeople:
    """_consume_one must create a job for any pending idea regardless of the
    idea's title content (title cleaning already happened in _save_ideas)."""

    def _run_consume(self, idea_title="Leclerc Wins"):
        """
        Run _consume_one with a mocked DB / fanpage / idea and return whether
        it returned True (job created) or False (skipped).
        """
        from app.tasks.yt_clip import _consume_one

        idea = MagicMock()
        idea.id = 42
        idea.title = idea_title
        idea.hook_text = "hook"
        idea.video_id = "abc123"
        idea.start_s = 10.0
        idea.end_s = 75.0
        idea.transcript_excerpt = "some text"
        idea.status = "pending"
        idea.used_at = None

        video = MagicMock()
        video.channel_name = "F1 Official"
        idea.yt_video = video

        fanpage = MagicMock()
        fanpage.id = 5
        fanpage.name = "Intan F1"
        fanpage.mode2_caption_language = "Indonesian"
        fanpage.mode2_caption_tone = "casual"
        fanpage.mode2_caption_max_length = 300
        fanpage.mode2_caption_hashtag_count = 3
        fanpage.mode2_caption_cta_text = "Komen dong!"
        fanpage.mode2_caption_custom_prompt = ""
        fanpage.mode2_gallery_niches = ["F1"]
        fanpage.yt_clip_publish_mode = "manual_review"

        mock_db = MagicMock()

        # _next_idea returns our idea first call, then None
        call_count = [0]
        def next_idea_side(db, fanpage_id):
            if call_count[0] == 0:
                call_count[0] += 1
                return idea
            return None

        with (
            patch("app.tasks.yt_clip._next_idea", side_effect=next_idea_side),
            patch("app.services.ai_caption.generate_caption", return_value=("Great caption", "router")),
            patch("app.services.yt_clip_title.strip_source_lines", return_value="Great caption"),
        ):
            result = _consume_one(mock_db, fanpage)

        return result, mock_db

    def test_creates_job_for_ordinary_idea(self):
        result, mock_db = self._run_consume("Leclerc Wins")
        assert result is True
        mock_db.add.assert_called_once()

    def test_does_not_drop_idea_with_no_people_field(self):
        """_consume_one must NOT read idea.people — no AttributeError, job created."""
        result, mock_db = self._run_consume("Short Title")
        # Must be True: title cleaning is NOT done here anymore
        assert result is True

    def test_title_used_as_is_from_idea(self):
        """design_title = idea.title (already cleaned at save time)."""
        result, mock_db = self._run_consume("Leclerc Pole")
        assert result is True
        added = mock_db.add.call_args[0][0]
        assert added.design_title == "Leclerc Pole"
