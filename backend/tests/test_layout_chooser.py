import pytest
from dataclasses import dataclass
from typing import Tuple

from app.services.layout_chooser import choose_layout, LayoutChoice, AnalysisLike

@dataclass(frozen=True)
class MockAnalysis:
    type: str = 'news'
    secondary_kind: str = 'none'
    secondary: str = ''
    inset_context: str = ''
    inset_query: str = ''
    radio_lines: Tuple[Tuple[str, str], ...] = ()
    unique_moment: bool = False

def test_choose_layout_team_radio():
    analysis = MockAnalysis(
        type='team_radio',
        radio_lines=(('driver', 'hello'),)
    )
    assert choose_layout(analysis, is_f1=True, hero_label=None, has_inset_photo=False) == LayoutChoice(
        layout='team_radio', reason='F1 team radio with lines'
    )
    
    assert choose_layout(analysis, is_f1=False, hero_label=None, has_inset_photo=False).layout == 'solo'
    
    analysis_no_lines = MockAnalysis(
        type='team_radio',
        radio_lines=()
    )
    assert choose_layout(analysis_no_lines, is_f1=True, hero_label=None, has_inset_photo=False).layout == 'solo'

def test_choose_layout_inset():
    analysis = MockAnalysis(
        type='quote',
        secondary_kind='person',
        secondary='Toto Wolff', inset_context='Toto Wolff'
    )
    assert choose_layout(analysis, is_f1=True, hero_label=None, has_inset_photo=True, inset_is_distinct=True).layout == 'inset'
    
    assert choose_layout(analysis, is_f1=True, hero_label=None, has_inset_photo=False).layout == 'solo'
    
    analysis_empty = MockAnalysis(
        type='quote',
        secondary_kind='person',
        secondary='   '
    )
    assert choose_layout(analysis_empty, is_f1=True, hero_label=None, has_inset_photo=True).layout == 'solo'

def test_choose_layout_action():
    analysis = MockAnalysis(
        type='news',
        secondary_kind='none',
        secondary=''
    )
    assert choose_layout(analysis, is_f1=True, hero_label='action', has_inset_photo=False).layout == 'action'
    
    analysis_not_news = MockAnalysis(
        type='quote',
        secondary_kind='none'
    )
    assert choose_layout(analysis_not_news, is_f1=True, hero_label='action', has_inset_photo=False).layout == 'solo'

    analysis_secondary = MockAnalysis(
        type='news',
        secondary_kind='person',
        secondary='Driver'
    )
    assert choose_layout(analysis_secondary, is_f1=True, hero_label='action', has_inset_photo=False, inset_is_distinct=True).layout == 'solo'

def test_choose_layout_quote_with_event():
    analysis = MockAnalysis(
        type='quote',
        secondary_kind='event',
        secondary='British GP', inset_context='British GP'
    )
    assert choose_layout(analysis, is_f1=True, hero_label=None, has_inset_photo=True, inset_is_distinct=True).layout == 'inset'

def test_choose_layout_order():
    analysis = MockAnalysis(
        type='team_radio',
        radio_lines=(('engineer', 'box box'),),
        secondary_kind='person',
        secondary='Bono', inset_context='Bono'
    )
    assert choose_layout(analysis, is_f1=True, hero_label=None, has_inset_photo=True).layout == 'team_radio'

    assert choose_layout(analysis, is_f1=False, hero_label=None, has_inset_photo=True, inset_is_distinct=True).layout == 'inset'

# --- V5 owner-rule tests ---

def test_single_person_news_no_inset_is_solo():
    """Domenicali news: single person, secondary_kind=event, inset_is_distinct=False → solo."""
    analysis = MockAnalysis(
        type='news',
        secondary_kind='event',
        secondary='FIA announcement',
    )
    result = choose_layout(
        analysis, is_f1=True, hero_label='face',
        has_inset_photo=False, inset_is_distinct=False
    )
    assert result.layout == 'solo', f"Expected solo, got {result.layout}"

def test_event_secondary_inset_shows_hero_is_solo():
    """Event secondary but inset would show hero → inset_is_distinct=False → solo (not action, face label)."""
    analysis = MockAnalysis(
        type='news',
        secondary_kind='event',
        secondary='Bahrain GP',
    )
    result = choose_layout(
        analysis, is_f1=True, hero_label='face',
        has_inset_photo=False, inset_is_distinct=False
    )
    assert result.layout == 'solo'

def test_win_action_news_single_person_is_action():
    """Verstappen 11 seasons win: news, secondary_kind=none, hero_label=action → action."""
    analysis = MockAnalysis(
        type='news',
        secondary_kind='none',
        secondary='',
    )
    result = choose_layout(
        analysis, is_f1=True, hero_label='action',
        has_inset_photo=False, inset_is_distinct=True
    )
    assert result.layout == 'action'

def test_win_news_event_secondary_no_distinct_inset_is_action():
    """News win with event secondary, inset_is_distinct=False, hero_label=action → action (not solo)."""
    analysis = MockAnalysis(
        type='news',
        secondary_kind='event',
        secondary='Rain at Spa',
    )
    result = choose_layout(
        analysis, is_f1=True, hero_label='action',
        has_inset_photo=False, inset_is_distinct=False
    )
    assert result.layout == 'action'

def test_quote_different_second_person_is_inset():
    """Verstappen-responds-to-Russell: different second person → inset kept."""
    analysis = MockAnalysis(
        type='quote',
        secondary_kind='person',
        secondary='George Russell', inset_context='George Russell',
    )
    result = choose_layout(
        analysis, is_f1=True, hero_label='face',
        has_inset_photo=True, inset_is_distinct=True
    )
    assert result.layout == 'inset'

def test_inset_not_distinct_overrides_has_inset_photo():
    """has_inset_photo=True but inset_is_distinct=False → no inset layout."""
    analysis = MockAnalysis(
        type='news',
        secondary_kind='event',
        secondary='Monaco GP',
    )
    result = choose_layout(
        analysis, is_f1=True, hero_label='face',
        has_inset_photo=True, inset_is_distinct=False
    )
    assert result.layout != 'inset', f"Should not be inset when not distinct, got {result.layout}"

