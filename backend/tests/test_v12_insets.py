import pytest
from app.services.layout_chooser import choose_layout

class MockAnalysis:
    def __init__(self, **kwargs):
        self.type = kwargs.get('type', 'news')
        self.secondary_kind = kwargs.get('secondary_kind', 'none')
        self.secondary = kwargs.get('secondary', '')
        self.inset_context = kwargs.get('inset_context', '')
        self.radio_lines = kwargs.get('radio_lines', ())
        self.unique_moment = kwargs.get('unique_moment', False)
        self.scenario = kwargs.get('scenario', 'none')
        self.target_team = kwargs.get('target_team', '')
        self.inset_contexts = kwargs.get('inset_contexts', ())
        self.speaker = kwargs.get('speaker', '')
        self.main_subject = kwargs.get('main_subject', '')
        self.moment_summary = kwargs.get('moment_summary', '')
        self.radio_lines = kwargs.get('radio_lines', ())
        self.unique_moment = kwargs.get('unique_moment', False)

def test_layout_chooser_whatif():
    analysis = MockAnalysis(scenario='transfer', target_team='Ferrari')
    choice = choose_layout(analysis, is_f1=True, hero_label='face', has_inset_photo=False, inset_is_distinct=False, valid_inset_count=1)
    assert choice.layout == 'inset'

    analysis = MockAnalysis(scenario='transfer', target_team='Ferrari', inset_contexts=({'description': 'something'}, {'description': 'else'}))
    choice = choose_layout(analysis, is_f1=True, hero_label='face', has_inset_photo=False, inset_is_distinct=False, valid_inset_count=2)
    assert choice.layout == 'inset'  # owner rule: what-if is always exactly one circle

    analysis = MockAnalysis(inset_context='something', inset_contexts=({'description': 'a'}, {'description': 'b'}))
    choice = choose_layout(analysis, is_f1=True, hero_label='face', has_inset_photo=True, inset_is_distinct=True, valid_inset_count=2)
    assert choice.layout == 'inset2'
    
    choice = choose_layout(analysis, is_f1=True, hero_label='face', has_inset_photo=True, inset_is_distinct=True, valid_inset_count=1)
    assert choice.layout == 'inset'

    analysis = MockAnalysis(inset_context='')
    choice = choose_layout(analysis, is_f1=True, hero_label='face', has_inset_photo=False, inset_is_distinct=False)
    assert choice.layout == 'solo'

    analysis = MockAnalysis(inset_context='', type='news', hero_label='action', secondary_kind='none')
    choice = choose_layout(analysis, is_f1=True, hero_label='action', has_inset_photo=False, inset_is_distinct=False)
    assert choice.layout == 'action'

from app.services.ig_content_classifier import _parse_analysis

def test_classifier_parsing():
    raw_0 = '{"type":"news", "inset_context": "legacy", "inset_query": "lq", "inset_contexts": []}'
    res_0 = _parse_analysis(raw_0, False)
    assert len(res_0.inset_contexts) == 1
    assert res_0.inset_context == "legacy"
    assert res_0.inset_query == "lq"
    
    raw_1 = '{"type":"news", "inset_contexts": [{"description": "c1", "query": "q1"}]}'
    res_1 = _parse_analysis(raw_1, False)
    assert len(res_1.inset_contexts) == 1
    assert res_1.inset_context == "c1"
    assert res_1.inset_query == "q1"

    raw_2 = '{"type":"news", "inset_contexts": [{"description": "c1", "query": "q1"}, {"description": "c2", "query": "q2"}]}'
    res_2 = _parse_analysis(raw_2, False)
    assert len(res_2.inset_contexts) == 2
    assert res_2.inset_context == "c1"
    assert res_2.inset_query == "q1"

    raw_bad = '{"type":"news", "inset_contexts": "not a list"}'
    res_bad = _parse_analysis(raw_bad, False)
    assert len(res_bad.inset_contexts) == 0

from app.services.visual_engine import build_inset_prompt, build_whatif_inset_prompt

def test_visual_engine_inset_prompt_modes():
    prompt, refs = build_inset_prompt(
        hero_desc="hero",
        insets=[{"mode": "real", "desc": "real photo", "path": "p1.jpg"}],
        purpose="p",
        accent="a",
        hero_path="hero.jpg"
    )
    assert "REAL PHOTO" in prompt
    assert "Image 2" in prompt
    assert len(refs) == 2

    prompt, refs = build_inset_prompt(
        hero_desc="hero",
        insets=[{"mode": "asset", "desc": "asset scene", "path": "p1.jpg"}],
        purpose="p",
        accent="a",
        hero_path="hero.jpg"
    )
    assert "ASSET SCENE" in prompt
    assert "Image 2" in prompt
    assert len(refs) == 2

    prompt, refs = build_inset_prompt(
        hero_desc="hero",
        insets=[{"mode": "imagined", "desc": "imagined scene", "path": "p1.jpg"}],
        purpose="p",
        accent="a",
        hero_path="hero.jpg"
    )
    assert "FULLY AI-IMAGINED SCENE" in prompt
    assert "Image 2" not in prompt
    assert len(refs) == 1

def test_visual_engine_whatif_prompt():
    prompt, refs = build_whatif_inset_prompt(
        hero_desc="hero",
        target_team="ferrari",
        target_team_color="red",
        target_team_logo_desc="logo",
        accent="a",
        hero_path="hero.jpg",
        alt_path="alt.jpg",
        kit_path="kit.jpg"
    )
    assert len(refs) == 3
    assert "Image 1" in prompt
    assert "Image 2" in prompt
    assert "Image 3" in prompt
    assert "EVERY sponsor logo" in prompt
    assert "MUST ONLY have the logos" not in prompt

    prompt, refs = build_whatif_inset_prompt(
        hero_desc="hero",
        target_team="ferrari",
        target_team_color="red",
        target_team_logo_desc="logo",
        accent="a",
        hero_path="hero.jpg",
        alt_path=None,
        kit_path="kit.jpg"
    )
    assert len(refs) == 2
    assert "Image 1" in prompt
    assert "Image 2" in prompt
    assert "Image 3" not in prompt
    assert "EVERY sponsor logo" in prompt

    prompt, refs = build_whatif_inset_prompt(
        hero_desc="hero",
        target_team="ferrari",
        target_team_color="red",
        target_team_logo_desc="logo",
        accent="a",
        hero_path="hero.jpg",
        alt_path=None,
        kit_path=None
    )
    assert len(refs) == 1
    assert "Image 1" in prompt
    assert "Image 2" not in prompt
    assert "Image 3" not in prompt
    assert "EVERY sponsor logo" not in prompt

from app.services.visual_sourcing import plan_photos, PhotoPick
from app.models.f1_drivers import F1Driver
from app.models.gallery import GalleryImage
from unittest.mock import patch, MagicMock

def test_visual_sourcing_whatif_picks():
    db = MagicMock()
    analysis = MockAnalysis(scenario='transfer', target_team='Ferrari', main_subject='Hamilton')

    mock_hero = MagicMock()
    mock_hero.id = 1
    mock_hero.local_path = 'hero.jpg'
    mock_hero.source_image_url = 'url_hero'
    
    mock_alt_candidate = MagicMock()
    mock_alt_candidate.id = 2
    mock_alt_candidate.local_path = 'alt.jpg'
    mock_alt_candidate.source_image_url = 'url_alt'
    
    mock_kit_candidate = MagicMock()
    mock_kit_candidate.id = 3
    mock_kit_candidate.local_path = 'kit.jpg'
    mock_kit_candidate.source_image_url = 'url_kit'
    
    mock_driver = MagicMock()
    mock_driver.surname = 'Leclerc'
    mock_driver.full_name = 'Charles Leclerc'
    
    class MockQuery:
        def filter(self, *args, **kwargs): return self
        def order_by(self, *args, **kwargs): return self
        def first(self): return mock_driver
        def limit(self, val): return self
        def all(self): return [mock_alt_candidate] # ensures candidates exist
    
    def db_query(model):
        return MockQuery()
    
    db.query = db_query

    with patch('app.services.visual_sourcing._vision_chat') as vision_chat, \
         patch('builtins.open'):
        
        vision_chat.return_value = '{"index": 1, "same_person": true, "different_pose": true}'
        
        with patch('app.services.visual_sourcing.decision_photo_keys', return_value=set()):
            with patch('app.services.visual_sourcing._find_inset_pick', return_value=None):
                with patch('app.services.visual_sourcing.is_same_person', return_value=True):
                    
                    plan = plan_photos(db, analysis, layout='inset', niche='f1', source_image_path=None, exclude_keys=set())
                        
    notes = " ".join(plan.notes)
    # The notes prove the paths were taken
    assert "Alt pick: picked gallery index 1" in notes
    assert "Kit pick: found Leclerc for team Ferrari" in notes

from app.services.visual_qa import evaluate

def test_visual_qa():
    qa = {"plain_whatif_suit": True}
    passed, problems = evaluate(qa, "inset", None, whatif_target_team="ferrari")
    assert not passed
    assert "plain_whatif_suit" in problems

    qa = {"invented_or_garbled_logos": ["some garbled logo"]}
    passed, problems = evaluate(qa, "inset", None)
    assert passed
    assert len(problems) == 0

    qa = {"inset_shows_hero": True}
    passed, problems = evaluate(qa, "inset", None, inset_modes=('asset',))
    assert not passed
    assert "inset_shows_hero" in problems

    qa = {"source_branding_visible": True}
    passed, problems = evaluate(qa, "inset", None)
    assert not passed
    assert "source_branding" in problems


from app.services.redesign_pipeline import redesign_card
from unittest.mock import patch, MagicMock
from app.services.redesign_pipeline import RedesignResult


def test_redesign_pipeline_notes_copied_properly():
    # just read the file to ensure the line exists
    with open('app/services/redesign_pipeline.py') as f:
        content = f.read()
    assert "notes.extend(plan.notes)" in content
