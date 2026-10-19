import json
import sqlite3
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)

@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"

@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"

from app.models.f1_drivers import F1Driver
from app.models.gallery import GalleryImage
from app.services import speaker_design
from app.services.visual_engine import GenerationResult
from app.tasks import design_renderer


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    F1Driver.__table__.create(engine, checkfirst=True)
    GalleryImage.__table__.create(engine, checkfirst=True)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _dummy_image(size=(400, 500), color=(50, 100, 150)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Test 1 & 2: Headline speaker person detection (Fix A)
# ---------------------------------------------------------------------------

def test_headline_portimao_circuit_ignored_for_verstappen_primary(db_session):
    """'Portimao : Max Verstappen arrache...' must NOT take 'Portimao' as speaker.
    extract_two_subjects's primary ('Max Verstappen') must be used instead."""
    fanpage = SimpleNamespace(news_visual_engine="chatgpt", mode2_gallery_niches=["F1"], name="Motorsport")
    job = SimpleNamespace(design_subtitle="", design_title="Portimao : Max Verstappen arrache la pole position")
    article = SimpleNamespace(
        scraped_title="Portimao : Max Verstappen arrache la pole position",
        scraped_content="Grand Prix du Portugal...",
        article_published_at=None,
        scraped_at=None,
    )

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[object()]), \
         patch("app.services.speaker_design.compose_speaker_design", return_value=b"png-data") as compose, \
         patch("app.services.design_images.extract_two_subjects", return_value=("Max Verstappen", None)):
        res = design_renderer._maybe_compose_speaker_design(db_session, fanpage, job, article, mode="news")

    assert res == b"png-data"
    assert compose.call_args.kwargs["speaker"] == "Max Verstappen"
    # Portimao was rejected by _is_likely_speaker_person
    assert compose.call_args.kwargs["speaker"] != "Portimao"


def test_headline_two_capitalised_words_accepted_as_speaker(db_session):
    """Headline with 2+ capitalized words is accepted directly as speaker."""
    fanpage = SimpleNamespace(news_visual_engine="chatgpt", mode2_gallery_niches=["MotoGP"], name="GPOne")
    job = SimpleNamespace(design_subtitle="", design_title="")
    article = SimpleNamespace(
        scraped_title="Jorge Martin : We were fast today",
        scraped_content="",
        article_published_at=None,
        scraped_at=None,
    )

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[object()]), \
         patch("app.services.speaker_design.compose_speaker_design", return_value=b"png-data") as compose, \
         patch("app.services.design_images.extract_two_subjects", return_value=("Other Subject", None)):
        design_renderer._maybe_compose_speaker_design(db_session, fanpage, job, article, mode="news")

    assert compose.call_args.kwargs["speaker"] == "Jorge Martin"


def test_headline_single_word_driver_accepted_when_in_f1_drivers(db_session):
    """Single word matching f1_drivers surname is accepted as speaker."""
    driver = F1Driver(
        id=1,
        season=2026,
        surname="Verstappen",
        full_name="Max Verstappen",
        number=1,
        team_name="Red Bull",
        team_colour="#1E41FF",
    )
    db_session.add(driver)
    db_session.commit()

    fanpage = SimpleNamespace(news_visual_engine="chatgpt", mode2_gallery_niches=["F1"], name="Motorsport")
    job = SimpleNamespace(design_subtitle="", design_title="")
    article = SimpleNamespace(
        scraped_title="Verstappen : Great feeling in the car",
        scraped_content="",
        article_published_at=None,
        scraped_at=None,
    )

    with patch("app.services.speaker_design.pick_speaker_photos", return_value=[object()]), \
         patch("app.services.speaker_design.compose_speaker_design", return_value=b"png-data") as compose, \
         patch("app.services.design_images.extract_two_subjects", return_value=("Fallback Subject", None)):
        design_renderer._maybe_compose_speaker_design(db_session, fanpage, job, article, mode="news")

    assert compose.call_args.kwargs["speaker"] == "Verstappen"


def test_headline_single_word_gallery_person_accepted(db_session):
    """Single word matching GalleryImage face keyword or people attribute is accepted."""
    img = GalleryImage(
        id=1,
        keyword="Simoncelli",
        extra_keywords=[],
        source_image_url="https://example.com/simoncelli.jpg",
        local_path="/tmp/simoncelli.jpg",
        public_url="/media/simoncelli.jpg",
        width=800,
        height=1000,
        source_engine="bing",
        label="face",
        attributes={"people": [{"name": "Paolo Simoncelli", "confidence": 0.9}]},
        is_deleted=False,
    )
    db_session.add(img)
    db_session.commit()

    assert design_renderer._is_likely_speaker_person(db_session, "Simoncelli") is True
    assert design_renderer._is_likely_speaker_person(db_session, "Portimao") is False


# ---------------------------------------------------------------------------
# Test 3: _rows does not match location or getty_hints (Fix B)
# ---------------------------------------------------------------------------

def test_rows_does_not_match_location_or_getty_hints_in_attributes(db_session):
    """_rows must NOT match photos where search term only appears in location or getty_hints."""
    # Row 1: Paolo Simoncelli photo at Portimao Portugal
    simoncelli_row = GalleryImage(
        id=101,
        keyword="Paolo Simoncelli",
        extra_keywords=["Simoncelli"],
        source_image_url="https://example.com/p1.jpg",
        local_path="/tmp/p1.jpg",
        public_url="/media/p1.jpg",
        width=800,
        height=1000,
        source_engine="bing",
        label="face",
        attributes={
            "people": [{"name": "Paolo Simoncelli", "confidence": 0.95}],
            "location": "Portimao Portugal Paolo Simoncelli",
            "getty_hints": "Portimao Portugal circuit press conference",
            "caption": "Paolo Simoncelli in Portimao",
        },
        is_deleted=False,
    )
    # Row 2: Real Max Verstappen photo
    verstappen_row = GalleryImage(
        id=102,
        keyword="Max Verstappen",
        extra_keywords=["Verstappen"],
        source_image_url="https://example.com/p2.jpg",
        local_path="/tmp/p2.jpg",
        public_url="/media/p2.jpg",
        width=800,
        height=1000,
        source_engine="bing",
        label="face",
        attributes={
            "people": [{"name": "Max Verstappen", "confidence": 0.99}],
            "location": "Silverstone UK",
        },
        is_deleted=False,
    )
    db_session.add_all([simoncelli_row, verstappen_row])
    db_session.commit()

    # Querying "Portimao" must return EMPTY - neither keyword, extra_keywords, nor people matches
    portimao_matches = speaker_design._rows(db_session, ["Portimao"])
    assert portimao_matches == []

    # Querying "Paolo Simoncelli" matches row 101
    simoncelli_matches = speaker_design._rows(db_session, ["Paolo Simoncelli"])
    assert [r.id for r in simoncelli_matches] == [101]

    # Querying "Max Verstappen" matches row 102
    verstappen_matches = speaker_design._rows(db_session, ["Max Verstappen"])
    assert [r.id for r in verstappen_matches] == [102]


def test_rows_excludes_keyword_mismatch(db_session):
    """_rows must exclude photos where keyword_mismatch is True."""
    row_good = GalleryImage(
        id=201,
        keyword="Fernando Alonso",
        extra_keywords=[],
        source_image_url="https://example.com/fa_good.jpg",
        local_path="/tmp/fa_good.jpg",
        public_url="/media/fa_good.jpg",
        width=800,
        height=1000,
        source_engine="bing",
        label="face",
        attributes={"people": [{"name": "Fernando Alonso", "confidence": 0.95}], "keyword_mismatch": False},
        is_deleted=False,
    )
    row_bad = GalleryImage(
        id=202,
        keyword="Fernando Alonso",
        extra_keywords=[],
        source_image_url="https://example.com/fa_bad.jpg",
        local_path="/tmp/fa_bad.jpg",
        public_url="/media/fa_bad.jpg",
        width=800,
        height=1000,
        source_engine="bing",
        label="face",
        attributes={"people": [{"name": "Lance Stroll", "confidence": 0.95}], "keyword_mismatch": True},
        is_deleted=False,
    )
    db_session.add_all([row_good, row_bad])
    db_session.commit()

    matches = speaker_design._rows(db_session, ["Fernando Alonso"])
    assert [r.id for r in matches] == [201]


# ---------------------------------------------------------------------------
# Test 4: Reference check vision guard (Fix C)
# ---------------------------------------------------------------------------

def test_reference_check_yes_uses_candidate_photo(monkeypatch):
    """Vision answering 'yes' accepts the photo and continues composing."""
    img_data = _dummy_image()
    candidate = SimpleNamespace(id=501, local_path="speaker.jpg")

    monkeypatch.setattr(speaker_design, "pick_speaker_photos", lambda *a, **k: [candidate])
    monkeypatch.setattr(speaker_design, "plan_layout", lambda *a, **k: "inset")
    monkeypatch.setattr(speaker_design, "pick_moment_photos", lambda *a, **k: [SimpleNamespace(local_path="moment.jpg")])
    monkeypatch.setattr(speaker_design, "pick_bike_photos", lambda *a: [])
    monkeypatch.setattr(speaker_design, "_read_photo", lambda row: img_data)
    monkeypatch.setattr(speaker_design, "speaker_side", lambda _: "left")
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.9)
    monkeypatch.setattr(speaker_design.visual_engine, "generate_ex", lambda db, req, **k: GenerationResult(img_data, "9router", {}, None))

    with patch("app.services.design_images._vision_chat", return_value="Yes, this is Max Verstappen."):
        res = speaker_design.compose_speaker_design(
            MagicMock(), SimpleNamespace(id=1), speaker="Max Verstappen", subjects=[],
            article_text="Headline text", sport="F1", around=datetime.now(timezone.utc),
        )

    assert res is not None
    assert isinstance(res, bytes)


def test_reference_check_no_skips_photo_and_falls_back_to_next(monkeypatch):
    """Vision answering 'no' skips that photo and proceeds to the next candidate photo."""
    img_data = _dummy_image()
    bad_candidate = SimpleNamespace(id=101, local_path="wrong_person.jpg")
    good_candidate = SimpleNamespace(id=102, local_path="right_person.jpg")

    monkeypatch.setattr(speaker_design, "pick_speaker_photos", lambda *a, **k: [bad_candidate, good_candidate])
    monkeypatch.setattr(speaker_design, "plan_layout", lambda *a, **k: "inset")
    monkeypatch.setattr(speaker_design, "pick_moment_photos", lambda *a, **k: [SimpleNamespace(local_path="moment.jpg")])
    monkeypatch.setattr(speaker_design, "pick_bike_photos", lambda *a: [])
    monkeypatch.setattr(speaker_design, "_read_photo", lambda row: img_data)
    monkeypatch.setattr(speaker_design, "speaker_side", lambda _: "left")
    monkeypatch.setattr(speaker_design, "identity_score", lambda *_: 0.9)

    called_requests = []
    monkeypatch.setattr(
        speaker_design.visual_engine, "generate_ex",
        lambda db, req, **k: called_requests.append(req) or GenerationResult(img_data, "9router", {}, None)
    )

    # First call ('wrong_person') returns 'no', second call ('right_person') returns 'yes'
    vision_responses = ["no, this is someone else", "yes, main person is Max Verstappen"]
    with patch("app.services.design_images._vision_chat", side_effect=lambda *a, **k: vision_responses.pop(0)):
        res = speaker_design.compose_speaker_design(
            MagicMock(), SimpleNamespace(id=1), speaker="Max Verstappen", subjects=[],
            article_text="Headline text", sport="F1", around=datetime.now(timezone.utc),
        )

    assert res is not None
    assert len(called_requests) == 1
    # The primary reference photo in refs must be good_candidate (102), not bad_candidate (101)
    assert called_requests[0].refs[0] == "right_person.jpg"


def test_reference_check_all_no_returns_none(monkeypatch):
    """When all candidates answer 'no', compose_speaker_design returns None (fails closed)."""
    img_data = _dummy_image()
    bad_cand1 = SimpleNamespace(id=201, local_path="bad1.jpg")
    bad_cand2 = SimpleNamespace(id=202, local_path="bad2.jpg")

    monkeypatch.setattr(speaker_design, "pick_speaker_photos", lambda *a, **k: [bad_cand1, bad_cand2])
    monkeypatch.setattr(speaker_design, "_read_photo", lambda row: img_data)

    with patch("app.services.design_images._vision_chat", return_value="no"):
        res = speaker_design.compose_speaker_design(
            MagicMock(), SimpleNamespace(id=1), speaker="Max Verstappen", subjects=[],
            article_text="Headline text", sport="F1", around=datetime.now(timezone.utc),
        )

    assert res is None


def test_reference_check_error_fails_closed(monkeypatch):
    """When vision call errors or raises an exception, the candidate is skipped (fail closed)."""
    img_data = _dummy_image()
    cand = SimpleNamespace(id=301, local_path="cand.jpg")

    monkeypatch.setattr(speaker_design, "pick_speaker_photos", lambda *a, **k: [cand])
    monkeypatch.setattr(speaker_design, "_read_photo", lambda row: img_data)

    with patch("app.services.design_images._vision_chat", side_effect=RuntimeError("9Router connection timeout")):
        res = speaker_design.compose_speaker_design(
            MagicMock(), SimpleNamespace(id=1), speaker="Max Verstappen", subjects=[],
            article_text="Headline text", sport="F1", around=datetime.now(timezone.utc),
        )

    assert res is None


def test_reference_check_unsure_fails_closed(monkeypatch):
    """When vision call answers 'unsure', the candidate is skipped (fail closed)."""
    img_data = _dummy_image()
    cand = SimpleNamespace(id=401, local_path="cand.jpg")

    monkeypatch.setattr(speaker_design, "pick_speaker_photos", lambda *a, **k: [cand])
    monkeypatch.setattr(speaker_design, "_read_photo", lambda row: img_data)

    with patch("app.services.design_images._vision_chat", return_value="unsure"):
        res = speaker_design.compose_speaker_design(
            MagicMock(), SimpleNamespace(id=1), speaker="Max Verstappen", subjects=[],
            article_text="Headline text", sport="F1", around=datetime.now(timezone.utc),
        )

    assert res is None
