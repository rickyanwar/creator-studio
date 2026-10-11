import pytest
from app.models.gallery import GalleryImage
from app.services.design_images import source_news_main

def test_source_news_main_norris_stray_punctuation(db_session, monkeypatch):
    # Setup the DB with shapes like the incident
    g1 = GalleryImage(keyword="lando norris", local_path="/tmp/lando1.jpg", source_image_url="http://test1", public_url="http://public1", width=100, height=100, source_engine="getty", is_deleted=False)
    g2 = GalleryImage(keyword="`norris", local_path="/tmp/norris2.jpg", source_image_url="http://test2", public_url="http://public2", width=100, height=100, source_engine="getty", is_deleted=False)
    db_session.add(g1)
    db_session.add(g2)
    db_session.commit()

    monkeypatch.setattr("app.services.design_images.vision_verify_subject", lambda *a, **kw: {"match": True, "confidence": 0.99})
    monkeypatch.setattr("app.services.design_images.vision_pick_best", lambda *a, **kw: 0)
    import base64
    b64 = base64.b64encode(b"fake_image_data").decode()
    monkeypatch.setattr("app.services.design_images.file_to_datauri", lambda p: f"data:image/jpeg;base64,{b64}")
    import os
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr("app.services.design_images.fetch_subject_datauri", lambda *a, **kw: (None, None))
    monkeypatch.setattr("app.services.design_images.pick_split_image_type", lambda *a, **kw: None)

    # 1. When known_primary is "Lando Norris", it should find the "lando norris" image
    uri, path = source_news_main(db_session, "Lando Norris quote", "Formula 1", use_vision=True, known_primary="Lando Norris")
    assert uri is not None
    assert "lando1" in path

    # 2. When known_primary is "Norris", it should also find it
    uri, path = source_news_main(db_session, "Lando Norris quote", "Formula 1", use_vision=True, known_primary="Norris")
    assert uri is not None

    # 3. What if known_primary is "`Norris" (simulating a bad input or DB keyword)
    uri, path = source_news_main(db_session, "Lando Norris quote", "Formula 1", use_vision=True, known_primary="`Norris")
    assert uri is not None

