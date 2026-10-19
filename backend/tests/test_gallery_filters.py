import json
import sqlite3
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select, or_
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.elements import BinaryExpression

from app.api.gallery import ManualLabelsBody, update_image_labels
from app.models.gallery import GalleryImage
from app.services.design_images import _gallery_verified_candidates
from app.services.gallery_filters import not_keyword_mismatch
from app.services.gallery_labeler import _apply_hints
from app.services.pro_poster.photos import _query_person_rows, _query_context_rows
from app.services.speaker_design import _rows as speaker_rows

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


@compiles(BinaryExpression, "sqlite")
def _compile_binary(binary, compiler, **kw):
    if getattr(binary.operator, "opstring", None) == "&&":
        return "0"
    return compiler.visit_binary(binary, **kw)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_connect(dbapi_connection, connection_record):
        dbapi_connection.create_function(
            "array_to_string",
            2,
            lambda arr, sep: sep.join(
                json.loads(arr) if isinstance(arr, str) and arr.startswith("[") else ([str(arr)] if arr else [])
            ),
        )

    GalleryImage.__table__.create(engine, checkfirst=True)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_not_keyword_mismatch_sqlite_semantics(db_session):
    """Keep rows where attributes is None, {}, or keyword_mismatch is False.
    Only keyword_mismatch: True is excluded."""
    r_none = GalleryImage(
        id=1,
        keyword="driver",
        source_image_url="http://img1",
        local_path="/tmp/1.jpg",
        public_url="http://pub/1.jpg",
        width=100,
        height=100,
        source_engine="bing",
        attributes=None,
    )
    r_empty = GalleryImage(
        id=2,
        keyword="driver",
        source_image_url="http://img2",
        local_path="/tmp/2.jpg",
        public_url="http://pub/2.jpg",
        width=100,
        height=100,
        source_engine="bing",
        attributes={},
    )
    r_false = GalleryImage(
        id=3,
        keyword="driver",
        source_image_url="http://img3",
        local_path="/tmp/3.jpg",
        public_url="http://pub/3.jpg",
        width=100,
        height=100,
        source_engine="bing",
        attributes={"keyword_mismatch": False},
    )
    r_true = GalleryImage(
        id=4,
        keyword="driver",
        source_image_url="http://img4",
        local_path="/tmp/4.jpg",
        public_url="http://pub/4.jpg",
        width=100,
        height=100,
        source_engine="bing",
        attributes={"keyword_mismatch": True},
    )
    db_session.add_all([r_none, r_empty, r_false, r_true])
    db_session.commit()

    matched = db_session.query(GalleryImage).filter(not_keyword_mismatch()).all()
    matched_ids = {r.id for r in matched}
    assert matched_ids == {1, 2, 3}
    assert 4 not in matched_ids


def test_postgresql_compile_check():
    """Verify postgresql dialect compilation does not raise."""
    compiled = str(select(GalleryImage).where(not_keyword_mismatch()).compile(dialect=postgresql.dialect()))
    assert "coalesce" in compiled.lower()
    compiled_literal = str(
        select(GalleryImage).where(not_keyword_mismatch()).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "keyword_mismatch" in compiled_literal


def test_pro_poster_query_person_rows_excludes_mismatch(db_session):
    """pro_poster._query_person_rows must exclude photos where keyword_mismatch is True."""
    normal_img = GalleryImage(
        id=10,
        keyword="carlos sainz",
        source_image_url="http://normal",
        local_path="/tmp/normal.jpg",
        public_url="http://pub/normal.jpg",
        width=100,
        height=100,
        source_engine="bing",
        attributes={"people": [{"name": "Carlos Sainz"}]},
    )
    mismatch_img = GalleryImage(
        id=11,
        keyword="carlos sainz",
        source_image_url="http://mismatch",
        local_path="/tmp/mismatch.jpg",
        public_url="http://pub/mismatch.jpg",
        width=100,
        height=100,
        source_engine="bing",
        attributes={"people": [{"name": "Jack Doohan"}], "keyword_mismatch": True},
    )
    db_session.add_all([normal_img, mismatch_img])
    db_session.commit()

    rows = _query_person_rows(db_session, ["Carlos Sainz"])
    row_ids = [r.id for r in rows]
    assert 10 in row_ids
    assert 11 not in row_ids


def test_pro_poster_query_context_rows_excludes_mismatch(db_session):
    """pro_poster._query_context_rows must exclude photos where keyword_mismatch is True."""
    normal_img = GalleryImage(
        id=20,
        keyword="f1 car",
        source_image_url="http://normal_f1",
        local_path="/tmp/normal_f1.jpg",
        public_url="http://pub/normal_f1.jpg",
        width=100,
        height=100,
        source_engine="bing",
        attributes={"event": {"series": "Formula 1"}},
    )
    mismatch_img = GalleryImage(
        id=21,
        keyword="f1 car",
        source_image_url="http://mismatch_f1",
        local_path="/tmp/mismatch_f1.jpg",
        public_url="http://pub/mismatch_f1.jpg",
        width=100,
        height=100,
        source_engine="bing",
        attributes={"event": {"series": "Formula 1"}, "keyword_mismatch": True},
    )
    db_session.add_all([normal_img, mismatch_img])
    db_session.commit()

    rows = _query_context_rows(db_session, "Formula 1")
    row_ids = [r.id for r in rows]
    assert 20 in row_ids
    assert 21 not in row_ids


def test_design_renderer_matched_query_excludes_mismatch(db_session):
    """design_renderer matched_query excludes mismatch photo with right keyword."""
    normal_img = GalleryImage(
        id=30,
        keyword="sergio perez",
        source_image_url="http://perez_normal",
        local_path="/tmp/perez_normal.jpg",
        public_url="http://pub/perez_normal.jpg",
        width=100,
        height=100,
        source_engine="bing",
        attributes={},
    )
    mismatch_img = GalleryImage(
        id=31,
        keyword="sergio perez",
        source_image_url="http://perez_mismatch",
        local_path="/tmp/perez_mismatch.jpg",
        public_url="http://pub/perez_mismatch.jpg",
        width=100,
        height=100,
        source_engine="bing",
        attributes={"keyword_mismatch": True},
    )
    db_session.add_all([normal_img, mismatch_img])
    db_session.commit()

    matched = ["sergio perez"]
    matched_query = db_session.query(GalleryImage).filter(
        or_(GalleryImage.keyword.in_(matched), GalleryImage.extra_keywords.overlap(matched)),
        GalleryImage.is_deleted == False,
        not_keyword_mismatch(),
    )
    rows = matched_query.all()
    row_ids = [r.id for r in rows]
    assert 30 in row_ids
    assert 31 not in row_ids


def test_speaker_design_rows_excludes_mismatch(db_session):
    """speaker_design._rows excludes mismatch photo with right keyword."""
    normal = GalleryImage(
        id=50,
        keyword="marc marquez",
        source_image_url="http://normal_mm",
        local_path="/tmp/normal_mm.jpg",
        public_url="http://pub/normal_mm.jpg",
        width=100,
        height=100,
        source_engine="bing",
        attributes={"people": [{"name": "Marc Marquez"}]},
    )
    mismatch = GalleryImage(
        id=51,
        keyword="marc marquez",
        source_image_url="http://mismatch_mm",
        local_path="/tmp/mismatch_mm.jpg",
        public_url="http://pub/mismatch_mm.jpg",
        width=100,
        height=100,
        source_engine="bing",
        attributes={"people": [{"name": "Alex Marquez"}], "keyword_mismatch": True},
    )
    db_session.add_all([normal, mismatch])
    db_session.commit()

    rows = speaker_rows(db_session, ["Marc Marquez"])
    ids = [r.id for r in rows]
    assert 50 in ids
    assert 51 not in ids


def test_gallery_manual_edit_clears_keyword_mismatch(db_session):
    """Updating people by hand in update_image_labels sets keyword_mismatch to False."""
    img = GalleryImage(
        id=40,
        keyword="sergio perez",
        source_image_url="http://edit_test",
        local_path="/tmp/edit_test.jpg",
        public_url="http://pub/edit_test.jpg",
        width=100,
        height=100,
        source_engine="bing",
        attributes={"keyword_mismatch": True, "keyword_person_visible": "no"},
    )
    db_session.add(img)
    db_session.commit()

    body = ManualLabelsBody(people=["Sergio Perez"])
    res = update_image_labels(image_id=40, body=body, db=db_session, _=MagicMock())

    updated = db_session.query(GalleryImage).filter_by(id=40).first()
    assert updated.attributes["keyword_mismatch"] is False
    assert updated.attributes["people"] == [{"name": "Sergio Perez", "confidence": 1.0, "source": "manual"}]
    assert res["attributes"]["keyword_mismatch"] is False


def test_gallery_labeler_apply_hints_with_people():
    """_apply_hints sets keyword_mismatch=False and keyword_person_visible='yes' when hints have people."""
    result = {
        "label": "face",
        "keyword_mismatch": True,
        "keyword_person_visible": "no",
        "people": [{"name": "Unknown", "confidence": 0.5}],
    }
    hints = {"people": ["Max Verstappen"]}
    updated = _apply_hints(result, hints)

    assert updated["keyword_mismatch"] is False
    assert updated["keyword_person_visible"] == "yes"
    assert updated["people"] == [{"name": "Max Verstappen", "confidence": 1.0, "source": "manual"}]


def test_gallery_labeler_apply_hints_without_people():
    """_apply_hints preserves keyword_mismatch when hints have no people."""
    result = {
        "label": "action",
        "keyword_mismatch": True,
        "keyword_person_visible": "no",
    }
    hints = {"event_name": "Monaco GP"}
    updated = _apply_hints(result, hints)

    assert updated["keyword_mismatch"] is True
    assert updated["keyword_person_visible"] == "no"


def test_design_images_gallery_verified_candidates_excludes_mismatch(db_session, tmp_path):
    """_gallery_verified_candidates in design_images excludes photos with keyword_mismatch: True."""
    f_good = tmp_path / "hamilton_good.jpg"
    f_good.write_bytes(b"good")
    f_bad = tmp_path / "hamilton_bad.jpg"
    f_bad.write_bytes(b"bad")

    img_good = GalleryImage(
        id=60,
        keyword="lewis hamilton",
        extra_keywords=[],
        source_image_url="http://hamilton_good",
        local_path=str(f_good),
        public_url="http://pub/h_good.jpg",
        width=100,
        height=100,
        source_engine="bing",
        label="face",
        is_deleted=False,
        attributes={"keyword_mismatch": False},
    )
    img_bad = GalleryImage(
        id=61,
        keyword="lewis hamilton",
        extra_keywords=[],
        source_image_url="http://hamilton_bad",
        local_path=str(f_bad),
        public_url="http://pub/h_bad.jpg",
        width=100,
        height=100,
        source_engine="bing",
        label="face",
        is_deleted=False,
        attributes={"keyword_mismatch": True},
    )
    db_session.add_all([img_good, img_bad])
    db_session.commit()

    candidates = _gallery_verified_candidates(
        db_session,
        subject="Lewis Hamilton",
        niche="F1",
        image_type="face",
        use_vision=False,
    )
    cand_ids = [gi.id for uri, gi in candidates]
    assert 60 in cand_ids
    assert 61 not in cand_ids


def test_visual_sourcing_base_query_excludes_mismatch(db_session):
    """GalleryImage candidate queries in visual_sourcing exclude keyword_mismatch rows."""
    img_normal = GalleryImage(
        id=70,
        keyword="charles leclerc",
        extra_keywords=[],
        source_image_url="http://leclerc_normal",
        local_path="/tmp/leclerc_normal.jpg",
        public_url="http://pub/leclerc_normal.jpg",
        width=100,
        height=100,
        source_engine="bing",
        label="face",
        is_deleted=False,
        attributes={},
    )
    img_mismatch = GalleryImage(
        id=71,
        keyword="charles leclerc",
        extra_keywords=[],
        source_image_url="http://leclerc_mismatch",
        local_path="/tmp/leclerc_mismatch.jpg",
        public_url="http://pub/leclerc_mismatch.jpg",
        width=100,
        height=100,
        source_engine="bing",
        label="face",
        is_deleted=False,
        attributes={"keyword_mismatch": True},
    )
    db_session.add_all([img_normal, img_mismatch])
    db_session.commit()

    query = db_session.query(GalleryImage).filter(
        GalleryImage.is_deleted == False,
        GalleryImage.label == "face",
        not_keyword_mismatch(),
    )
    res = query.all()
    res_ids = [r.id for r in res]
    assert 70 in res_ids
    assert 71 not in res_ids

