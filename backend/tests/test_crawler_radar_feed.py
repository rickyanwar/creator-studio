import pytest
from unittest.mock import MagicMock, patch

from app.tasks.crawler import _try_radar_ingest

def test_try_radar_ingest_rollback_on_error():
    db = MagicMock()
    source = MagicMock()
    source.ig_username = "test"
    medias = []

    db.query.return_value.filter.return_value.first.return_value = MagicMock()
    
    with patch("app.services.radar_ingest.ingest_medias", side_effect=Exception("Ingest failed")):
        _try_radar_ingest(db, source, medias)
        
    db.rollback.assert_called_once()
