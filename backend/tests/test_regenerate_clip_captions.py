"""Tests for regenerate_clip_captions.py — per-job function (S12 fix).

Verifies:
  • The script uses db.get(YtClipIdea, ...) to resolve the idea — NOT job.yt_video
    (which would raise AttributeError on a real PublishJob).
  • When idea + video resolve, generate_caption IS called (AI path).
  • The resulting caption has no newline (one-line rule enforced).
  • Falls back to re-cleaning the old caption when generate_caption raises.

pytest -q backend/tests/test_regenerate_clip_captions.py
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — build minimal PublishJob-like objects with NO .yt_video attribute
# ---------------------------------------------------------------------------

def _make_job(job_id=1, fanpage_id=5, yt_clip_idea_id=42, yt_video_id="abc123",
              status="pending_review"):
    """Return a SimpleNamespace that mimics a real PublishJob.

    Crucially, it does NOT have a .yt_video attribute — accessing one would
    raise AttributeError, exactly like the production crash this script fixes.
    """
    job = SimpleNamespace(
        id=job_id,
        fanpage_id=fanpage_id,
        yt_clip_idea_id=yt_clip_idea_id,
        yt_video_id=yt_video_id,
        status=status,
        ai_generated_caption="Old caption line 1\n\nOld caption line 2\n\n#F1 #Verstappen",
        ai_provider_used=None,
    )
    # Explicitly ensure .yt_video does NOT exist
    assert not hasattr(job, "yt_video"), "test setup: job must not have .yt_video"
    return job


def _make_idea(idea_id=42, video_id="abc123"):
    idea = MagicMock()
    idea.id = idea_id
    idea.video_id = video_id
    idea.title = "Verstappen Defends the Lead"
    idea.description = "Intense overtake battle"
    idea.hook_text = "Would you have done the same?"
    idea.transcript_excerpt = "Max pushes hard into turn 1"
    video = MagicMock()
    video.channel_name = "F1 Official"
    video.title = "Azerbaijan GP Highlights"
    idea.yt_video = video
    return idea


def _make_fanpage(fanpage_id=5):
    fp = MagicMock()
    fp.id = fanpage_id
    fp.name = "Intan F1"
    fp.mode2_caption_language = "Indonesian"
    fp.mode2_caption_tone = "casual"
    fp.mode2_caption_hashtag_count = 3
    fp.mode2_caption_cta_text = "Komen dong!"
    fp.mode2_caption_custom_prompt = ""
    fp.mode2_gallery_niches = ["F1"]
    return fp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRegenerateClipCaptionsPerJob:

    def _call_regenerate(self, job, fanpage, mock_db, ai_return=None, ai_raises=None):
        """Call _regenerate_caption_for_job with apply=True and mocked AI."""
        from scripts.regenerate_clip_captions import _regenerate_caption_for_job

        idea = _make_idea()

        def fake_db_get(model, pk):
            from app.models.yt_clip_ideas import YtClipIdea
            if model is YtClipIdea and pk == job.yt_clip_idea_id:
                return idea
            return None

        mock_db.get.side_effect = fake_db_get

        if ai_raises is not None:
            ai_side = ai_raises
            ai_patch = patch(
                "app.services.ai_caption.generate_caption",
                side_effect=ai_side,
            )
        else:
            caption_text = ai_return or "Verstappen holds off the challenge — would you have done the same? #F1 #Verstappen #AzerbaijanGP"
            ai_patch = patch(
                "app.services.ai_caption.generate_caption",
                return_value=(caption_text, "router"),
            )

        with ai_patch:
            return _regenerate_caption_for_job(mock_db, job, fanpage, apply=True)

    def test_no_yt_video_attribute_on_job(self):
        """Accessing job.yt_video must raise AttributeError — confirms test setup is correct."""
        job = _make_job()
        with pytest.raises(AttributeError):
            _ = job.yt_video

    def test_ai_path_used_when_idea_and_video_resolve(self):
        """When db.get resolves the idea (and idea.yt_video returns the video),
        generate_caption MUST be called (AI path, not fallback re-clean)."""
        job = _make_job()
        fanpage = _make_fanpage()
        mock_db = MagicMock()

        new_caption, provider, used_ai, fallback_reason, idea, video = self._call_regenerate(
            job, fanpage, mock_db
        )

        assert used_ai is True, "expected AI path, got fallback"
        assert fallback_reason is None
        assert idea is not None
        assert video is not None

    def test_result_has_no_newline(self):
        """The caption returned in apply mode must have no newline (one_line_caption applied)."""
        job = _make_job()
        fanpage = _make_fanpage()
        mock_db = MagicMock()

        # AI returns a multi-line caption — script must collapse it
        multi_line = (
            "Verstappen holds off the pressure!\n\n"
            "An incredible defensive drive in Baku — would you have done the same?\n\n"
            "#F1 #Verstappen #AzerbaijanGP"
        )

        new_caption, provider, used_ai, fallback_reason, idea, video = self._call_regenerate(
            job, fanpage, mock_db, ai_return=multi_line
        )

        assert used_ai is True
        assert new_caption is not None
        assert "\n" not in new_caption, f"newline found in caption: {new_caption!r}"

    def test_fallback_when_generate_caption_raises(self):
        """When generate_caption raises, the script falls back to re-cleaning the old caption
        and sets used_ai=False + a non-None fallback_reason."""
        job = _make_job()
        fanpage = _make_fanpage()
        mock_db = MagicMock()

        new_caption, provider, used_ai, fallback_reason, idea, video = self._call_regenerate(
            job, fanpage, mock_db, ai_raises=RuntimeError("All AI providers failed")
        )

        assert used_ai is False
        assert fallback_reason is not None
        assert "generate_caption raised" in fallback_reason
        # Fallback re-cleans old caption — result must still be newline-free
        assert new_caption is not None
        assert "\n" not in new_caption, f"fallback caption has newline: {new_caption!r}"

    def test_fallback_when_idea_missing(self):
        """When db.get returns None for the idea (idea deleted), the script falls back
        to re-cleaning the old caption and notes 'idea missing' in fallback_reason."""
        job = _make_job(yt_clip_idea_id=999)  # no such idea in mock
        fanpage = _make_fanpage()
        mock_db = MagicMock()
        mock_db.get.return_value = None  # idea not found, no video fallback either
        mock_db.query.return_value.filter_by.return_value.first.return_value = None

        from scripts.regenerate_clip_captions import _regenerate_caption_for_job
        new_caption, provider, used_ai, fallback_reason, idea, video = _regenerate_caption_for_job(
            mock_db, job, fanpage, apply=True
        )

        assert used_ai is False
        assert idea is None
        assert fallback_reason is not None
        assert "idea missing" in fallback_reason
        assert new_caption is not None
        assert "\n" not in new_caption

    def test_dry_run_does_not_call_ai(self):
        """In dry-run mode (apply=False) generate_caption must NEVER be called."""
        job = _make_job()
        fanpage = _make_fanpage()
        mock_db = MagicMock()
        idea = _make_idea()
        mock_db.get.return_value = idea

        from scripts.regenerate_clip_captions import _regenerate_caption_for_job

        with patch("app.services.ai_caption.generate_caption") as mock_gen:
            new_caption, provider, used_ai, fallback_reason, idea_out, video_out = _regenerate_caption_for_job(
                mock_db, job, fanpage, apply=False
            )
            mock_gen.assert_not_called()

        assert new_caption is None
        assert used_ai is False
