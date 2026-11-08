from dataclasses import FrozenInstanceError, replace
from datetime import datetime

import pytest

from app.services.fb_share.routing import normalize_subject, select_groups
from app.services.fb_share.types import GroupInfo, ShareItem, SentRecord


GROUP = GroupInfo(1, "F1", "https://facebook.com/groups/f1", "Fans", "general", None,
                  "page_allowed", None, None)
ITEM = ShareItem(1, "f1", "source:1", "0000000000000000", "photo")


def select(item=ITEM, groups=(GROUP,), joined=frozenset({1}), identity="page",
           history=(), weekday=0, limit=10, distance=10):
    return select_groups(item, groups, joined, identity, history, weekday, limit, distance)


@pytest.mark.parametrize("changes,joined,identity,expected", [
    ({}, {1}, "page", True),
    ({"is_active": False}, {1}, "page", False),
    ({"niche": "UFC"}, {1}, "page", False),
    ({}, set(), "page", False),
    ({"posting_identity": "profile_only"}, {1}, "page", False),
    ({"posting_identity": "profile_only"}, {1}, "profile", True),
    ({"allowed_days": (0, 2)}, {1}, "page", True),
    ({"allowed_days": (1,)}, {1}, "page", False),
    ({"allowed_days": ()}, {1}, "page", False),
    ({"banned_content": ("photo",)}, {1}, "page", False),
    ({"banned_content": ("video",)}, {1}, "page", True),
    ({"banned_content": ()}, {1}, "page", True),
])
def test_basic_filters(changes, joined, identity, expected):
    group = replace(GROUP, **changes)
    assert select(groups=[group], joined=joined, identity=identity) == ([group] if expected else [])


@pytest.mark.parametrize("kind", ["person_fan", "team_fan"])
@pytest.mark.parametrize("sentiment,expected", [
    ("positive", True), ("negative", False), ("neutral", False),
    ("unsure", False), (None, False),
])
def test_fan_sentiment(kind, sentiment, expected):
    item = replace(ITEM, sentiment={} if sentiment is None else {"  PÉREZ ": sentiment})
    group = replace(GROUP, kind=kind, subject="perez")
    assert select(item=item, groups=[group]) == ([group] if expected else [])


@pytest.mark.parametrize("subject", [None, "", "   "])
def test_fan_requires_subject(subject):
    assert select(groups=[replace(GROUP, kind="person_fan", subject=subject)]) == []


def test_subject_normalization_and_kimi():
    assert normalize_subject("  PÉREZ\t Fan  Club ") == "perez fan club"
    group = replace(GROUP, kind="person_fan", subject="Kimi Antonelli")
    assert select(item=replace(ITEM, sentiment={"kimi antonelli": "positive"}), groups=[group]) == [group]


@pytest.mark.parametrize("content_type,expected", [("meme", True), ("photo", False), (None, False)])
def test_meme_content(content_type, expected):
    group = replace(GROUP, kind="meme")
    assert select(item=replace(ITEM, content_type=content_type), groups=[group]) == ([group] if expected else [])


@pytest.mark.parametrize("group_id,source,phash,expected", [
    (1, "source:1", None, False),
    (1, "other", "00000000000003ff", False),
    (1, "other", "00000000000007ff", True),
    (2, "source:1", "0000000000000000", True),
    (1, "other", None, True),
])
def test_dedupe(group_id, source, phash, expected):
    history = [SentRecord(group_id, phash, source, datetime(2020, 1, 1))]
    assert select(history=history) == ([GROUP] if expected else [])


def test_missing_item_hash_and_custom_threshold():
    history = [SentRecord(1, "00000000000003ff", "other", datetime(2020, 1, 1))]
    assert select(item=replace(ITEM, phash=None), history=history) == [GROUP]
    assert select(history=history, distance=9) == [GROUP]


@pytest.mark.parametrize("limit,expected", [(10, [3, 2, 4, 1]), (2, [3, 2]), (0, []), (-1, [])])
def test_order_limit_and_no_mutation(limit, expected):
    groups = [GROUP, replace(GROUP, id=2, kind="team_fan", subject="team", name="beta"),
              replace(GROUP, id=3, kind="person_fan", subject="person", name="Alpha"),
              replace(GROUP, id=4, kind="meme", name="A")]
    item = replace(ITEM, content_type="meme", sentiment={"team": "positive", "person": "positive"})
    original = groups.copy()
    assert [g.id for g in select(item, groups, {1, 2, 3, 4}, limit=limit)] == expected
    assert groups == original
    assert item.sentiment == {"team": "positive", "person": "positive"}
    with pytest.raises(FrozenInstanceError):
        item.caption = "changed"
