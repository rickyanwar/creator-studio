import pytest

from app.database import Base, SessionLocal, engine
from datetime import datetime, timezone
from uuid import uuid4

from app.models.gallery import GalleryImage
from app.services.muse_labeler import fill_people_from_keyword, merge_label_attributes


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.query(GalleryImage).filter(GalleryImage.source_image_url.like("http://manual-fields-test-%")).delete(synchronize_session=False)
        session.commit()
        session.close()


def test_merge_label_attributes_preserves_manual_fields():
    existing = {
        "people": [{"name": "Manual Person", "confidence": 1.0, "source": "manual"}],
        "shot_type": "portrait",
        "event": {"session": "race", "year": 2024},
        "manual_fields": ["people", "shot_type", "event.session"],
    }
    labelled = {
        "people": [{"name": "AI Person", "confidence": 0.5, "source": "vision"}],
        "shot_type": "action",
        "event": {"session": "practice", "year": 2025},
    }
    merged = merge_label_attributes(existing, labelled)
    assert merged["people"] == existing["people"]
    assert merged["shot_type"] == "portrait"
    assert merged["event"]["session"] == "race"
    assert merged["event"]["year"] == 2025


def test_merge_label_attributes_combines_people_rules_with_manual_protection():
    existing_people = [{"name": "Keyword Person", "confidence": 0.8, "source": "keyword"}]
    assert merge_label_attributes(
        {"people": existing_people},
        {"people": []},
    )["people"] == existing_people
    assert merge_label_attributes(
        {"people": existing_people},
        {"people": [{"name": "Max Verstappen", "confidence": 0.9, "source": "vision"}], "keyword_mismatch": True},
    )["people"] == [{"name": "Max Verstappen", "confidence": 0.9, "source": "vision"}]
    assert merge_label_attributes(
        {"people": existing_people, "manual_fields": ["people"]},
        {"people": [{"name": "Max Verstappen", "confidence": 0.9, "source": "vision"}], "keyword_mismatch": True},
    )["people"] == existing_people


def test_fill_people_skips_manual_people(db_session):
    source_url = f"http://manual-fields-test-{uuid4().hex}"
    image = GalleryImage(
        keyword="marc marquez", source_image_url=source_url,
        local_path="/tmp/x", public_url="http://x", width=1, height=1, source_engine="manual",
        labeled_at=datetime.now(timezone.utc).replace(tzinfo=None), attributes={"manual_fields": ["people"]},
    )
    db_session.add(image)
    db_session.commit()
    assert fill_people_from_keyword(db_session) == 0
