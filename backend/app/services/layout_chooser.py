import typing
from dataclasses import dataclass
from typing import Literal, Tuple

LayoutType = Literal['team_radio', 'inset', 'inset2', 'solo', 'action']

@dataclass(frozen=True)
class LayoutChoice:
    layout: LayoutType
    reason: str

class AnalysisLike(typing.Protocol):
    type: str  # 'news' | 'quote' | 'team_radio' | 'other'
    secondary_kind: str  # 'person' | 'event' | 'none'
    secondary: str
    inset_context: str
    radio_lines: Tuple[Tuple[str, str], ...]
    unique_moment: bool
    scenario: str
    target_team: str
    inset_contexts: Tuple[dict, ...]

def choose_layout(
    analysis: AnalysisLike,
    *,
    is_f1: bool,
    hero_label: str | None,
    has_inset_photo: bool,
    inset_is_distinct: bool = False,
    valid_inset_count: int = 1,
) -> LayoutChoice:
    if analysis.type == 'team_radio' and is_f1 and analysis.radio_lines:
        return LayoutChoice(layout='team_radio', reason='F1 team radio with lines')

    scenario = getattr(analysis, 'scenario', 'none')
    target = getattr(analysis, 'target_team', '').strip()
    
    # Owner rule: a what-if/transfer card always gets exactly ONE circle.
    if scenario != 'none' and target:
        return LayoutChoice(layout='inset', reason='Transfer scenario what-if inset')

    if bool(analysis.inset_context.strip()) and inset_is_distinct:
        if getattr(analysis, 'inset_contexts', ()) and len(analysis.inset_contexts) >= 2 and valid_inset_count >= 2:
            return LayoutChoice(layout='inset2', reason='Two distinct contextual insets found')
        return LayoutChoice(layout='inset', reason='Distinct contextual inset photo found')

    if analysis.type == 'news' and hero_label == 'action' and analysis.secondary_kind == 'none':
        return LayoutChoice(layout='action', reason='News action hero with no secondary')

    # news with secondary but no distinct inset → action if hero is action-labelled
    if analysis.type == 'news' and hero_label == 'action' and not inset_is_distinct:
        return LayoutChoice(layout='action', reason='News action hero, inset not distinct')

    return LayoutChoice(layout='solo', reason='Fallback to simplest layout')
