"""Unit tests for Facebook photo source logic (S8 quote truncation detection).

pytest -q backend/tests/test_facebook_photo_source.py
"""

import pytest
from app.services.facebook_photo_source import is_incomplete_quote

class TestIsIncompleteQuote:
    def test_example_vasseur(self):
        # job 9595: ends with a connector ("what") and no punctuation
        assert is_incomplete_quote("…everybody knows, everywhere, but what") is True

    def test_example_leclerc(self):
        # job 9582: ends with a connector ("but") and no punctuation
        assert is_incomplete_quote("…something I wasn't supposed to say, but") is True
        
    def test_normal_full_sentence(self):
        # Full sentence ending with punctuation
        assert is_incomplete_quote("This is a complete and good sentence.") is False
        assert is_incomplete_quote("What a great race!") is False
        assert is_incomplete_quote("Did he win?") is False

    def test_ellipsis_endings(self):
        # "…" ellipsis endings produced by _fit_quote are complete (not ending on connector)
        assert is_incomplete_quote("This is a long sentence that got truncated…") is False
        
    def test_ellipsis_on_connector(self):
        # If the ellipsis follows a connector, it should be considered incomplete
        assert is_incomplete_quote("He was going to win but…") is True
        
    def test_missing_terminal_punctuation(self):
        # Missing terminal punctuation alone must NOT make it incomplete
        assert is_incomplete_quote("Different race weekend, same s***") is False
        assert is_incomplete_quote("We just have to keep pushing") is False

    def test_trailing_punctuation(self):
        # Ends with a trailing comma/dash/colon/semicolon -> incomplete
        assert is_incomplete_quote("He was pushing,") is True
        assert is_incomplete_quote("They won the race -") is True
        assert is_incomplete_quote("It is because:") is True
        assert is_incomplete_quote("It is because;") is True
        
    def test_quotes(self):
        # Ends with a quote mark
        assert is_incomplete_quote('He said "hello"') is False 
        assert is_incomplete_quote('He said "hello."') is False

    def test_indonesian_connectors(self):
        assert is_incomplete_quote("Balapan yang sangat seru tapi") is True
        assert is_incomplete_quote("Dia menang karena") is True
from unittest.mock import MagicMock, patch
from app.services.facebook_photo_source import build_idea_from_candidate, FacebookPhotoCandidate, _DownloadedPhoto

class TestBuildIdeaSkips:
    @patch("app.services.facebook_photo_source._download_photo")
    @patch("app.services.facebook_content_classifier.classify_facebook_photo")
    @patch("app.services.facebook_photo_source._record_seen")
    @patch("app.services.facebook_photo_source._seen_fbids", return_value=set())
    def test_skips_when_raw_quote_incomplete(self, mock_seen, mock_record, mock_classify, mock_download):
        mock_db = MagicMock()
        mock_fanpage = MagicMock()
        
        # Candidate with alt_text that seems complete
        candidate = FacebookPhotoCandidate(
            fbid="123", image_url="http://x.jpg", alt_text="May be an image of 1 person and text that says 'This is complete'"
        )
        
        mock_item = MagicMock()
        mock_item.local_path = "/tmp/fake.jpg"
        mock_download.return_value = mock_item
        
        mock_record.return_value = MagicMock() # gi
        
        with patch("pathlib.Path.read_bytes", return_value=b"fake"):
            with patch("pathlib.Path.unlink"):
                mock_classify.return_value = {
                    "type": "quote",
                    "quote": "He was going to win but",  # Incomplete!
                    "speaker": "Driver",
                    "on_topic": True
                }
                
                idea = build_idea_from_candidate(mock_db, mock_fanpage, candidate)
                
                # Should skip unconditionally when raw quote is incomplete
                assert idea is None
                mock_record.assert_called_once()
