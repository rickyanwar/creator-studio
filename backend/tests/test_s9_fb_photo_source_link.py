"""S9 — History: link to the source Facebook photo for Mode 6.

Tests (TDD, run before implementation passes):
  1. fbid_to_url() builds the correct FB photo URL.
  2. _consume_one sets source_gallery_image_id on the new PublishJob.
  3. backfill matching function: exact match, closest-used_at tiebreak,
     ambiguous (>1 equidistant), unmatched.

pytest -q backend/tests/test_s9_fb_photo_source_link.py
"""

import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone, timedelta


# ─── 1. fbid_to_url ──────────────────────────────────────────────────────────

from app.services.facebook_photo_source import fbid_to_url


class TestFbidToUrl:
    def test_known_fbid(self):
        assert fbid_to_url("1708825221248007") == "https://www.facebook.com/photo/?fbid=1708825221248007"

    def test_another_fbid(self):
        assert fbid_to_url("1708679397929256") == "https://www.facebook.com/photo/?fbid=1708679397929256"

    def test_empty_returns_none(self):
        assert fbid_to_url("") is None
        assert fbid_to_url(None) is None  # type: ignore[arg-type]


# ─── 2. _consume_one sets source_gallery_image_id ────────────────────────────

class TestConsumeOneSetsSourceGalleryImageId:
    """_consume_one must copy idea.gallery_image_id → job.source_gallery_image_id."""

    @patch("app.services.design_images.resolve_template", return_value=None)
    @patch("app.services.ai_caption.generate_caption", return_value=("A caption", "gemini"))
    @patch("app.services.ai_caption.build_caption_prompt", return_value="prompt")
    def test_source_gallery_image_id_is_set(
        self,
        mock_build_prompt,
        mock_gen_caption,
        mock_resolve,
    ):
        from app.tasks.facebook_photo import _consume_one
        from app.models.publish_jobs import PublishJob, ContentType

        # ── Build minimal stubs ──
        idea = MagicMock()
        idea.id = 42
        idea.category = "news"
        idea.design_title = "Test headline"
        idea.design_subtitle = None
        idea.design_caption = None
        idea.gallery_image_id = 99  # <-- the key field

        fanpage = MagicMock()
        fanpage.id = 5
        fanpage.name = "intan"
        # PublishMode.manual_review → no render_facebook_photo call
        from app.models.target_fanpages import PublishMode
        fanpage.facebook_photo_publish_mode = PublishMode.manual_review

        db = MagicMock()
        # db.query(FacebookPhotoIdea).filter(...).order_by(...).first() → idea
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = idea

        created_job = None

        def capture_add(obj):
            nonlocal created_job
            if isinstance(obj, PublishJob):
                created_job = obj

        db.add.side_effect = capture_add
        db.commit.return_value = None

        result = _consume_one(db, fanpage)

        assert result is True
        assert created_job is not None
        assert created_job.source_gallery_image_id == 99, (
            "_consume_one must set source_gallery_image_id = idea.gallery_image_id"
        )


# ─── 3. Backfill matching logic ───────────────────────────────────────────────

# Import the matching function that the backfill script exposes.
# We import from the script directly so tests drive the implementation.
import sys, os as _os
sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
from scripts.backfill_fb_photo_source import match_job_to_idea  # noqa: E402


_T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_idea(id_, used_at_offset_s=0):
    idea = MagicMock()
    idea.id = id_
    idea.gallery_image_id = 100 + id_
    idea.used_at = _T0 + timedelta(seconds=used_at_offset_s)
    return idea


def _make_job(created_at_offset_s=0):
    job = MagicMock()
    job.id = 1
    job.fanpage_id = 5
    job.design_title = "Same title"
    job.created_at = _T0 + timedelta(seconds=created_at_offset_s)
    job.source_gallery_image_id = None
    return job


class TestMatchJobToIdea:
    def test_single_match_returns_it(self):
        job = _make_job(created_at_offset_s=0)
        ideas = [_make_idea(1, used_at_offset_s=5)]
        result, ambiguous = match_job_to_idea(job, ideas)
        assert result is ideas[0]
        assert ambiguous is False

    def test_multiple_picks_closest_used_at(self):
        job = _make_job(created_at_offset_s=0)
        # idea 2 used_at=+1s is closer to job.created_at than idea 1 (+100s)
        ideas = [_make_idea(1, used_at_offset_s=100), _make_idea(2, used_at_offset_s=1)]
        result, ambiguous = match_job_to_idea(job, ideas)
        assert result is ideas[1]   # the closer one
        assert ambiguous is False

    def test_empty_ideas_returns_none(self):
        job = _make_job()
        result, ambiguous = match_job_to_idea(job, [])
        assert result is None
        assert ambiguous is False

    def test_equidistant_two_marks_ambiguous(self):
        job = _make_job(created_at_offset_s=0)
        # Both ideas exactly equidistant from job.created_at
        ideas = [
            _make_idea(1, used_at_offset_s=10),
            _make_idea(2, used_at_offset_s=-10),
        ]
        result, ambiguous = match_job_to_idea(job, ideas)
        # Result may be either candidate but ambiguous flag must be True
        assert ambiguous is True
