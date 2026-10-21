import pytest
from unittest.mock import MagicMock, patch, mock_open
from sqlalchemy import or_, func

from app.models.gallery import GalleryImage, GalleryKeyword
from app.models.f1_drivers import F1Driver
from app.services.ig_content_classifier import PostAnalysis
from app.services.visual_sourcing import (
    plan_photos,
    _is_person_subject,
    _names_are_same_person,
    is_same_person,
)


# --- (a) subject "Aston Martin", people ["Fernando Alonso"] ---

def test_is_person_subject_team_vs_driver():
    analysis = PostAnalysis(
        type='news', text='', speaker='', main_subject='Aston Martin',
        secondary_kind='none', secondary='', inset_context='', inset_query='',
        inset_contexts=(), moment_summary='Aston Martin updates package',
        radio_lines=(), weather='none', people=('Fernando Alonso',), unique_moment=False
    )
    db = MagicMock()
    # Team match query finds Aston Martin
    mock_driver_row = MagicMock(spec=F1Driver, team_name="Aston Martin Aramco F1 Team")
    mock_q = MagicMock()
    db.query.return_value = mock_q
    mock_q.filter.return_value = mock_q
    mock_q.first.return_value = mock_driver_row

    # "Aston Martin" is a team → False
    assert _is_person_subject(db, analysis, "Aston Martin") is False
    # "Fernando Alonso" is in people → True
    # When querying team for Fernando Alonso, first() returns None
    mock_q.first.return_value = None
    assert _is_person_subject(db, analysis, "Fernando Alonso") is True


def test_plan_photos_team_subject_no_surname_search_no_jorge_martin():
    """
    subject 'Aston Martin', people ['Fernando Alonso'] → surname search is never run.
    A gallery candidate with keyword 'Jorge Martin' is NOT returned.
    Hero label is requested as 'action' instead of 'face'.
    """
    db = MagicMock()
    mock_q = MagicMock()
    db.query.return_value = mock_q
    mock_q.filter.return_value = mock_q
    mock_q.order_by.return_value = mock_q
    mock_q.limit.return_value = mock_q

    # Team match query finds Aston Martin team row
    mock_team = MagicMock(spec=F1Driver, team_name="Aston Martin")
    mock_q.first.return_value = mock_team

    # Exact match for "Aston Martin" returns empty; surname match must NOT run
    mock_q.all.return_value = []

    analysis = PostAnalysis(
        type='news', text='', speaker='', main_subject='Aston Martin',
        secondary_kind='none', secondary='', inset_context='', inset_query='',
        inset_contexts=(), moment_summary='Aston Martin brings new upgrades',
        radio_lines=(), weather='none', people=('Fernando Alonso',), unique_moment=False
    )

    with patch('app.services.visual_sourcing.fetch_subject_datauri') as mock_fetch:
        mock_fetch.return_value = (None, None)
        plan = plan_photos(db, analysis, 'quote', 'F1', None, set())

    # Verify that no query filter used %martin%
    for call in mock_q.filter.call_args_list:
        args, _ = call
        for arg in args:
            arg_str = str(arg)
            assert "%martin%" not in arg_str.lower()

    # Hero was requested as action since Aston Martin is a team
    assert mock_fetch.called
    _, fetch_kwargs = mock_fetch.call_args
    # image_type should be 'action'
    assert fetch_kwargs.get('image_type') == 'action'


# --- (b) subject "Charles Leclerc" in people → person; niche filter ---

def test_is_person_subject_in_people():
    analysis = PostAnalysis(
        type='news', text='', speaker='', main_subject='Charles Leclerc',
        secondary_kind='none', secondary='', inset_context='', inset_query='',
        inset_contexts=(), moment_summary='Leclerc qualifies on pole',
        radio_lines=(), weather='none', people=('Charles Leclerc',), unique_moment=False
    )
    db = MagicMock()
    mock_q = MagicMock()
    db.query.return_value = mock_q
    mock_q.filter.return_value = mock_q
    mock_q.first.return_value = None

    assert _is_person_subject(db, analysis, "Charles Leclerc") is True


def test_plan_photos_niche_filter_applied():
    """
    Niche filter subquery on GalleryKeyword is applied to base_query when niche is known.
    """
    db = MagicMock()
    mock_q = MagicMock()
    db.query.return_value = mock_q
    mock_q.filter.return_value = mock_q
    mock_q.order_by.return_value = mock_q
    mock_q.limit.return_value = mock_q
    mock_q.all.return_value = []
    mock_q.first.return_value = None

    analysis = PostAnalysis(
        type='news', text='', speaker='Charles Leclerc', main_subject='Charles Leclerc',
        secondary_kind='none', secondary='', inset_context='', inset_query='',
        inset_contexts=(), moment_summary='Leclerc wins Monaco GP',
        radio_lines=(), weather='none', people=('Charles Leclerc',), unique_moment=False
    )

    with patch('app.services.visual_sourcing.fetch_subject_datauri', return_value=(None, None)):
        plan = plan_photos(db, analysis, 'quote', 'F1', None, set())

    # db.query(GalleryKeyword.keyword) must have been called for niche filtering
    keyword_queries = [
        call for call in db.query.call_args_list
        if call[0] and call[0][0] is GalleryKeyword.keyword
    ]
    assert len(keyword_queries) > 0


# --- (c) person face pick that fails vision_verify_subject is not returned; next verified candidate is ---

@patch('app.services.visual_sourcing.vision_verify_subject')
@patch('app.services.visual_sourcing._vision_chat')
@patch('app.services.visual_sourcing._vision_datauri')
def test_identity_check_rejects_unverified_candidate_and_picks_next(mock_datauri, mock_chat, mock_verify):
    mock_datauri.return_value = "data:image/jpeg;base64,mock"
    # Vision pick picks candidate 1 (index 0) initially
    mock_chat.return_value = "1"

    cand1 = MagicMock(spec=GalleryImage, id=101, local_path="fake1.jpg", source_image_url="url1")
    cand2 = MagicMock(spec=GalleryImage, id=102, local_path="fake2.jpg", source_image_url="url2")
    cand3 = MagicMock(spec=GalleryImage, id=103, local_path="fake3.jpg", source_image_url="url3")

    # cand1 fails verification; cand2 succeeds
    def fake_verify(img_bytes, subject, niche):
        if img_bytes == b'cand1_bytes':
            return {"match": False, "confidence": 0.1}
        elif img_bytes == b'cand2_bytes':
            return {"match": True, "confidence": 0.95}
        return {"match": False, "confidence": 0.0}

    mock_verify.side_effect = fake_verify

    db = MagicMock()
    mock_q = MagicMock()
    db.query.return_value = mock_q
    mock_q.filter.return_value = mock_q
    mock_q.order_by.return_value = mock_q
    mock_q.limit.return_value = mock_q
    mock_q.all.return_value = [cand1, cand2, cand3]
    mock_q.first.return_value = None

    analysis = PostAnalysis(
        type='news', text='', speaker='Charles Leclerc', main_subject='Charles Leclerc',
        secondary_kind='none', secondary='', inset_context='', inset_query='',
        inset_contexts=(), moment_summary='Leclerc wins Monaco GP',
        radio_lines=(), weather='none', people=('Charles Leclerc',), unique_moment=False
    )

    def fake_open(path, mode="r"):
        if "fake1.jpg" in str(path):
            return mock_open(read_data=b'cand1_bytes')()
        elif "fake2.jpg" in str(path):
            return mock_open(read_data=b'cand2_bytes')()
        return mock_open(read_data=b'cand3_bytes')()

    with patch('builtins.open', side_effect=fake_open):
        plan = plan_photos(db, analysis, 'quote', 'F1', None, set())

    assert plan.hero is not None
    # cand1 failed identity check, so cand2 was picked!
    assert plan.hero.gallery_id == 102
    assert plan.hero.path == "fake2.jpg"


@patch('app.services.visual_sourcing.vision_verify_subject')
@patch('app.services.visual_sourcing._vision_chat')
@patch('app.services.visual_sourcing._vision_datauri')
def test_identity_check_all_fail_falls_to_jina(mock_datauri, mock_chat, mock_verify):
    mock_datauri.return_value = "data:image/jpeg;base64,mock"
    mock_chat.return_value = "1"

    cand1 = MagicMock(spec=GalleryImage, id=101, local_path="fake1.jpg", source_image_url="url1")
    cand2 = MagicMock(spec=GalleryImage, id=102, local_path="fake2.jpg", source_image_url="url2")

    # All candidates fail verification
    mock_verify.return_value = {"match": False, "confidence": 0.2}

    db = MagicMock()
    mock_q = MagicMock()
    db.query.return_value = mock_q
    mock_q.filter.return_value = mock_q
    mock_q.order_by.return_value = mock_q
    mock_q.limit.return_value = mock_q
    mock_q.all.return_value = [cand1, cand2]
    mock_q.first.return_value = None

    analysis = PostAnalysis(
        type='news', text='', speaker='Charles Leclerc', main_subject='Charles Leclerc',
        secondary_kind='none', secondary='', inset_context='', inset_query='',
        inset_contexts=(), moment_summary='Leclerc wins Monaco GP',
        radio_lines=(), weather='none', people=('Charles Leclerc',), unique_moment=False
    )

    jina_gi = MagicMock(spec=GalleryImage, id=999, local_path="jina.jpg", source_image_url="jina_url")

    with patch('builtins.open', mock_open(read_data=b'fake_bytes')):
        with patch('app.services.visual_sourcing.fetch_subject_datauri', return_value=("uri", jina_gi)) as mock_jina:
            plan = plan_photos(db, analysis, 'quote', 'F1', None, set())

    # Gallery candidates were rejected, so Jina fallback was used
    assert mock_jina.called
    assert plan.hero is not None
    assert plan.hero.source == 'jina'
    assert plan.hero.gallery_id == 999


# --- (d) _names_are_same_person assertions ---

def test_names_are_same_person_rules():
    # 2-word names: full match or same first initial + surname
    assert _names_are_same_person("Aston Martin", "Jorge Martin") is False
    assert _names_are_same_person("Jorge Martin", "Aston Martin") is False
    assert _names_are_same_person("Max Verstappen", "M. Verstappen") is True
    assert _names_are_same_person("M. Verstappen", "Max Verstappen") is True
    assert _names_are_same_person("Damon Hill", "Graham Hill") is False

    # 1-word names: old surname rule (length >= 3)
    assert _names_are_same_person("Verstappen", "Max Verstappen") is True
    assert _names_are_same_person("Max Verstappen", "Verstappen") is True
    assert _names_are_same_person("Martin", "Jorge Martin") is True
    assert _names_are_same_person("Alonso", "Fernando Alonso") is True
    assert _names_are_same_person("Jo", "Jo Ramirez") is False

    # Exact matches
    assert _names_are_same_person("Max Verstappen", "Max Verstappen") is True
    assert _names_are_same_person("max verstappen", "MAX VERSTAPPEN") is True
    assert _names_are_same_person("Aston Martin", "Aston Martin") is True

    # Empty
    assert _names_are_same_person("", "Max") is False
    assert _names_are_same_person("Max", "") is False
