import json
import os
import sqlite3
import pytest

if os.path.basename(os.getcwd()) != "backend" and os.path.isdir("backend"):
    os.chdir("backend")
from unittest.mock import MagicMock, patch
from dataclasses import dataclass
from typing import Tuple

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.gallery import GalleryKeyword, GalleryImage
from app.models.f1_drivers import F1Driver
from app.services.ig_content_classifier import PostAnalysis
from app.services.layout_chooser import choose_layout, LayoutChoice
from app.services.visual_sourcing import (
    wanted_labels,
    parse_inset_pick,
    _find_inset_pick,
    _gallery_niche_condition,
)

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)

@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"

@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def in_memory_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    GalleryKeyword.__table__.create(engine, checkfirst=True)
    GalleryImage.__table__.create(engine, checkfirst=True)
    F1Driver.__table__.create(engine, checkfirst=True)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_gallery_inset_query_niche_filter(in_memory_db):
    """Gallery inset query excludes a UFC-niche image for an F1 page (niche filter),
    allows NULL-niche and f1 images."""
    db = in_memory_db
    kw_ufc = GalleryKeyword(keyword="austin", niche="ufc", is_active=True)
    kw_f1 = GalleryKeyword(keyword="silverstone", niche="f1", is_active=True)
    kw_null = GalleryKeyword(keyword="monza", niche=None, is_active=True)
    db.add_all([kw_ufc, kw_f1, kw_null])
    db.commit()

    img_ufc = GalleryImage(
        id=1, keyword="austin", source_image_url="http://img/1", public_url="http://pub/1",
        width=1920, height=1080, source_engine="getty", label="action", local_path="/tmp/ufc.jpg", is_deleted=False
    )
    img_f1 = GalleryImage(
        id=2, keyword="silverstone", source_image_url="http://img/2", public_url="http://pub/2",
        width=1920, height=1080, source_engine="getty", label="action", local_path="/tmp/f1.jpg", is_deleted=False
    )
    img_null = GalleryImage(
        id=3, keyword="monza", source_image_url="http://img/3", public_url="http://pub/3",
        width=1920, height=1080, source_engine="getty", label="action", local_path="/tmp/monza.jpg", is_deleted=False
    )
    db.add_all([img_ufc, img_f1, img_null])
    db.commit()

    # Query with niche="f1" using the extracted niche condition
    niche_cond = _gallery_niche_condition("f1")
    assert niche_cond is not None

    kw_subq = db.query(GalleryKeyword.keyword).filter(niche_cond).subquery()
    query = db.query(GalleryImage).filter(GalleryImage.keyword.in_(kw_subq.select()))
    results = query.all()

    result_ids = {img.id for img in results}
    assert 1 not in result_ids  # UFC excluded
    assert 2 in result_ids      # F1 allowed
    assert 3 in result_ids      # NULL allowed

    # Now verify via _find_inset_pick directly:
    notes_pick = []
    with patch('app.services.visual_sourcing.fetch_subject_datauri', return_value=(None, None)):
        pick = _find_inset_pick(
            db=db,
            context_item={"description": "austin", "query": "austin"},
            hero_subj="Max Verstappen",
            label="action",
            analysis=PostAnalysis(
                type='news', text='', speaker='', main_subject='Verstappen',
                secondary_kind='event', secondary='austin',
                inset_context='austin', inset_query='austin',
                inset_contexts=({'description': 'austin', 'query': 'austin'},),
                moment_summary='Race at Austin',
                radio_lines=(), weather='none', people=('Verstappen',), unique_moment=False
            ),
            layout='inset',
            niche='f1',
            exclude_keys=set(),
            notes=notes_pick,
        )
    # UFC image id=1 must NOT be picked from gallery
    if pick and pick.pick:
        assert pick.pick.gallery_id != 1


def test_parse_inset_pick_matches_context():
    """parse_inset_pick handles matches_context true/false/missing."""
    # true
    raw_true = '{"index": 2, "shows_hero_person": false, "matches_context": true}'
    idx, shows_hero, matches_context = parse_inset_pick(raw_true, 3)
    assert idx == 1
    assert shows_hero is False
    assert matches_context is True

    # false
    raw_false = '{"index": 3, "shows_hero_person": false, "matches_context": false}'
    idx, shows_hero, matches_context = parse_inset_pick(raw_false, 3)
    assert idx == 2
    assert shows_hero is False
    assert matches_context is False

    # missing key -> defaults to False (reject)
    raw_missing = '{"index": 1, "shows_hero_person": false}'
    idx, shows_hero, matches_context = parse_inset_pick(raw_missing, 3)
    assert idx == 0
    assert shows_hero is False
    assert matches_context is False

    # garbage input
    idx, shows_hero, matches_context = parse_inset_pick("not valid json", 3)
    assert idx == 0
    assert shows_hero is True
    assert matches_context is False


def test_person_inset_not_imagined_circuit_can_be_imagined(in_memory_db):
    """A person inset with no verified photo returns None (not imagined);
    a circuit inset may be imagined."""
    db = in_memory_db
    notes = []

    # 1. Person inset with no verified photo
    person_analysis = PostAnalysis(
        type='quote', text='', speaker='Max Verstappen', main_subject='Max Verstappen',
        secondary_kind='person', secondary='Michael Schumacher',
        inset_context='Michael Schumacher', inset_query='Michael Schumacher',
        inset_contexts=({'description': 'Michael Schumacher', 'query': 'Michael Schumacher'},),
        moment_summary='Verstappen on Schumacher records',
        radio_lines=(), weather='none', people=('Max Verstappen', 'Michael Schumacher'), unique_moment=False
    )
    context_person = {"description": "Michael Schumacher", "query": "Michael Schumacher"}

    with patch('app.services.visual_sourcing.fetch_subject_datauri', return_value=(None, None)):
        plan_person = _find_inset_pick(
            db=db,
            context_item=context_person,
            hero_subj='Max Verstappen',
            label='face',
            analysis=person_analysis,
            layout='inset',
            niche='f1',
            exclude_keys=set(),
            notes=notes,
        )
    assert plan_person is None
    assert any("person without a verified real photo, dropped" in n for n in notes)

    # 2. Circuit inset (non-person) can be imagined
    notes_circuit = []
    circuit_analysis = PostAnalysis(
        type='news', text='', speaker='', main_subject='Max Verstappen',
        secondary_kind='event', secondary='Kyalami Circuit',
        inset_context='Kyalami Circuit Africa', inset_query='Kyalami Circuit',
        inset_contexts=({'description': 'Kyalami Circuit Africa', 'query': 'Kyalami Circuit'},),
        moment_summary='F1 returns to Africa at Kyalami',
        radio_lines=(), weather='none', people=('Max Verstappen',), unique_moment=False
    )
    context_circuit = {"description": "Kyalami Circuit Africa", "query": "Kyalami Circuit"}

    with patch('app.services.visual_sourcing.fetch_subject_datauri', return_value=(None, None)):
        plan_circuit = _find_inset_pick(
            db=db,
            context_item=context_circuit,
            hero_subj='Max Verstappen',
            label='action',
            analysis=circuit_analysis,
            layout='inset',
            niche='f1',
            exclude_keys=set(),
            notes=notes_circuit,
        )
    assert plan_circuit is not None
    assert plan_circuit.mode == 'imagined'
    assert plan_circuit.pick is None
    assert plan_circuit.context_desc == 'Kyalami Circuit Africa'


@dataclass(frozen=True)
class MockAnalysisForLayout:
    type: str = 'quote'
    secondary_kind: str = 'person'
    secondary: str = 'Carlos Sainz'
    inset_context: str = 'Carlos Sainz'
    inset_query: str = 'Carlos Sainz'
    radio_lines: Tuple[Tuple[str, str], ...] = ()
    unique_moment: bool = False
    scenario: str = 'none'
    target_team: str = ''
    inset_contexts: Tuple[dict, ...] = ()


def test_layout_chooser_one_vs_two_insets():
    """layout chooser picks 1 inset when the two contexts have the same query,
    2 when they differ."""
    # Same query -> 1 inset
    analysis_same = MockAnalysisForLayout(
        inset_contexts=(
            {"description": "Monza podium", "query": "Monza Circuit"},
            {"description": "Monza track", "query": "monza circuit"},
        )
    )
    choice_same = choose_layout(
        analysis_same,
        is_f1=True,
        hero_label='face',
        has_inset_photo=True,
        inset_is_distinct=True,
        valid_inset_count=2,
    )
    assert choice_same.layout == 'inset'

    # Different queries -> 2 insets (inset2)
    analysis_diff = MockAnalysisForLayout(
        inset_contexts=(
            {"description": "Monza podium", "query": "Monza Circuit"},
            {"description": "Spa rain", "query": "Spa Francorchamps"},
        )
    )
    choice_diff = choose_layout(
        analysis_diff,
        is_f1=True,
        hero_label='face',
        has_inset_photo=True,
        inset_is_distinct=True,
        valid_inset_count=2,
    )
    assert choice_diff.layout == 'inset2'


def test_wanted_labels_non_grid_driver_forced_face(in_memory_db):
    """wanted_labels forces 'face' for a non-grid F1 driver on an action layout;
    current-grid driver keeps 'action'."""
    db = in_memory_db
    grid_driver = F1Driver(
        season=2024,
        surname="Verstappen",
        full_name="Max Verstappen",
        number=1,
        team_name="Red Bull Racing",
        team_colour="#0600EF",
        verified=True,
    )
    db.add(grid_driver)
    db.commit()

    # Current grid driver on action layout -> keeps 'action'
    analysis_grid = PostAnalysis(
        type='news', text='', speaker='', main_subject='Max Verstappen',
        secondary_kind='none', secondary='', inset_context='', inset_query='',
        inset_contexts=(), moment_summary='Verstappen on track',
        radio_lines=(), weather='none', people=('Max Verstappen',), unique_moment=False
    )
    labels_grid = wanted_labels(analysis_grid, 'action', db=db, niche='f1')
    assert labels_grid['hero'] == 'action'

    # Non-grid driver (retired / historic) on action layout -> forced 'face'
    analysis_non_grid = PostAnalysis(
        type='news', text='', speaker='', main_subject='Michael Schumacher',
        secondary_kind='none', secondary='', inset_context='', inset_query='',
        inset_contexts=(), moment_summary='Schumacher anniversary',
        radio_lines=(), weather='none', people=('Michael Schumacher',), unique_moment=False
    )
    labels_non_grid = wanted_labels(analysis_non_grid, 'action', db=db, niche='f1')
    assert labels_non_grid['hero'] == 'face'


def test_motogp_never_gets_an_imagined_inset(monkeypatch):
    """Owner 2026-10-24: MotoGP insets come only from real photos."""
    import app.services.visual_sourcing as vs
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    db = MagicMock()
    db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    db.query.return_value.all.return_value = []
    monkeypatch.setattr(vs, "is_in_event_window", lambda *a, **k: False)
    monkeypatch.setattr(vs, "_is_person_subject", lambda *a, **k: False)
    analysis = SimpleNamespace(secondary="", secondary_kind="place", moment_summary="Marquez wins in Sepang")
    notes = []
    plan = vs._find_inset_pick(db=db, context_item={"description": "the Sepang circuit", "query": "Sepang circuit aerial"},
                               hero_subj="Marc Marquez", label="action", analysis=analysis, layout="inset",
                               niche="motogp", exclude_keys=set(), notes=notes)
    assert plan is None
    assert any("MotoGP never uses an imagined inset" in n for n in notes)
