"""Caption classifier batching, normalization and fail-closed contracts."""

from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.fb_group_share import FbShareBrandSettings, FbShareItem
from app.services.fb_share.brand_settings import set_brand_share
from app.services.fb_share.classify import CLASSIFY_PROMPT, classify_pending_items


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://")
    FbShareItem.__table__.create(engine)
    FbShareBrandSettings.__table__.create(engine)
    with sessionmaker(bind=engine)() as db:
        yield db
    engine.dispose()


@pytest.fixture
def fanpage(db_session):
    set_brand_share(db_session, 1, True, {"niches": ["f1", "motogp"]})
    db_session.commit()
    return SimpleNamespace(id=1)


def item(db, **values):
    fields = dict(fanpage_id=1, niche="", source_kind="manual", source_ref=str(db.query(FbShareItem).count()),
                  media_kind="photo", media=["photo.jpg"], caption="Max wins", status="new",
                  created_at=datetime(2026, 11, 8))
    fields.update(values)
    row = FbShareItem(**fields)
    db.add(row)
    db.commit()
    return row


def result(row, **values):
    fields = dict(id=row.id, subjects=["Max"], sentiment={"Max": "positive"}, content_type="news", niche="f1",
                  group_caption="Max wins.", group_hashtags=["#F1", "#Max"])
    fields.update(values)
    return fields


@pytest.mark.parametrize("fenced", [False, True])
def test_batch_happy_path_and_prompt(db_session, fanpage, fenced):
    first = item(db_session, caption="Max wins\n" + "x" * 700)
    second = item(db_session, media_kind="video")
    omitted = item(db_session)
    generate = Mock(return_value=("```json\n" if fenced else "") + json.dumps({
        "items": [result(first), result(second, content_type="video")]
    }) + ("\n```" if fenced else ""))
    source_captions = {row.id: row.caption for row in (first, second, omitted)}
    assert classify_pending_items(db_session, fanpage, generate=generate) == 2
    generate.assert_called_once()
    prompt = generate.call_args.args[0]
    assert prompt == CLASSIFY_PROMPT.replace("{niches}", '["f1", "motogp"]').replace('{language}', "en").replace("{posts}", "\n".join(
        f"id={row.id} media={row.media_kind} caption={' '.join(source_captions[row.id][:600].splitlines())}"
        for row in (first, second, omitted)))
    assert first.subjects == ["Max"] and first.sentiment == {"Max": "positive"}
    assert first.niche == "f1" and first.content_type == "news"
    assert second.content_type == "video"
    assert omitted.sentiment is None and omitted.subjects is None


@pytest.mark.parametrize("text", ["garbage", "{}", "[]", '{"items":null}', '{"items":{}}', '```json\n{"items":'])
def test_parse_failures_leave_batch_untouched(db_session, fanpage, text, caplog):
    row = item(db_session)
    assert classify_pending_items(db_session, fanpage, generate=lambda prompt: text) == 0
    db_session.refresh(row)
    assert row.sentiment is None and row.subjects is None and row.status == "new"
    assert "classification failed" in caplog.text


@pytest.mark.parametrize("kind,fallback", [("photo", "photo"), ("album", "photo"), ("video", "video")])
def test_invalid_values_normalized(db_session, fanpage, kind, fallback):
    row = item(db_session, media_kind=kind)
    output = result(row, subjects=[" Max ", "", None, "Team", "Rider", "Other", "Fifth"],
                    sentiment={"Max": "bad", "Team": "negative", "Rider": [], "Other": "neutral"},
                    content_type=[], niche="f1")
    assert classify_pending_items(db_session, fanpage, generate=lambda prompt: json.dumps({"items": [output]})) == 1
    assert row.subjects == ["Max", "Team", "Rider", "Other"]
    assert row.sentiment == {"Max": "unsure", "Team": "negative", "Rider": "unsure", "Other": "neutral"}
    assert row.content_type == fallback


@pytest.mark.parametrize("existing,status", [("", "skipped"), ("f1", "new")])
def test_unknown_niche_preserves_existing_or_skips(db_session, fanpage, existing, status):
    row = item(db_session, niche=existing)
    assert classify_pending_items(db_session, fanpage, generate=lambda prompt: json.dumps({
        "items": [result(row, niche="football")]})) == 1
    assert row.niche == existing and row.status == status


def test_selection_oldest_limit_and_unknown_ids(db_session, fanpage):
    newer = item(db_session, created_at=datetime(2026, 11, 9))
    oldest = item(db_session, created_at=datetime(2026, 11, 8) - timedelta(days=1))
    other = item(db_session, fanpage_id=2)
    routed = item(db_session, status="routed")
    labeled = item(db_session, sentiment={"Max": "positive"})
    generate = Mock(return_value=json.dumps({"items": [result(oldest), result(newer), result(other),
        result(routed), result(labeled), result(oldest), {"id": "1"}, {"id": []}, None]}))
    assert classify_pending_items(db_session, fanpage, max_items=1, generate=generate) == 1
    assert f"id={oldest.id} " in generate.call_args.args[0]
    assert f"id={newer.id} " not in generate.call_args.args[0]
    assert newer.sentiment is None and other.sentiment is None and routed.sentiment is None
    assert oldest.sentiment == {"Max": "positive"}


def test_generation_error_and_empty_batch(db_session, fanpage, caplog):
    generate = Mock(side_effect=RuntimeError("unavailable"))
    assert classify_pending_items(db_session, fanpage, generate=generate) == 0
    generate.assert_not_called()
    row = item(db_session)
    assert classify_pending_items(db_session, fanpage, max_items=0, generate=generate) == 0
    generate.assert_not_called()
    assert classify_pending_items(db_session, fanpage, generate=generate) == 0
    assert row.sentiment is None
    assert "classification failed" in caplog.text


def test_default_generator_reads_text_field(db_session, fanpage, monkeypatch):
    from app.services import gptcli_client

    row = item(db_session)
    config = object()
    get_config = Mock(return_value=config)
    generate = Mock(return_value=SimpleNamespace(text=json.dumps({"items": [result(row)]})))
    monkeypatch.setattr(gptcli_client, "get_gptcli_config", get_config)
    monkeypatch.setattr(gptcli_client, "generate_text", generate)
    assert classify_pending_items(db_session, fanpage) == 1
    get_config.assert_called_once_with(db_session)
    assert generate.call_args.args[0] is config


QUOTE_RULE = ("If you include a quote from the source, copy it word for word. Never shorten or paraphrase a quote. "
              "If the quote is too long for a short caption, leave it out.")


def test_prompt_has_quote_rule_and_brand_language(db_session):
    set_brand_share(db_session, 1, True, {"niches": ["f1"]})
    db_session.commit()
    row = item(db_session)
    generate = Mock(return_value=json.dumps({"items": [result(row)]}))
    assert classify_pending_items(db_session, SimpleNamespace(id=1, caption_language="id"), generate=generate) == 1
    prompt = generate.call_args.args[0]
    assert QUOTE_RULE in prompt
    assert 'language with code "id"' in prompt and "{language}" not in prompt
    assert '"group_caption"' in prompt and '"group_hashtags"' in prompt


def test_group_caption_and_hashtags_replace_page_caption(db_session, fanpage):
    row = item(db_session, caption="Max wins the race after a long fight. " * 10, hashtags="#A #B #C #D #E")
    output = result(row, group_caption="  Max wins again.  ",
                    group_hashtags=[" #F1 ", "#f1", "#MaxVerstappen", "no_hash", "#two words", 7, "#Win"])
    assert classify_pending_items(db_session, fanpage, generate=lambda prompt: json.dumps({"items": [output]})) == 1
    assert row.caption == "Max wins again."
    assert row.hashtags == "#F1 #MaxVerstappen #Win"
    assert row.sentiment == {"Max": "positive"}


@pytest.mark.parametrize("caption", [None, "", "   ", 12, ["Max"], "x" * 281])
def test_invalid_group_caption_keeps_original(db_session, fanpage, caption, monkeypatch):
    monkeypatch.setattr("app.services.fb_share.classify.redis_client", Mock())
    row = item(db_session, caption="Original caption", hashtags="#A #B #C #D")
    output = result(row, group_caption=caption, group_hashtags=["#F1"])
    if caption is None:
        output.pop("group_caption")
    client = Mock()
    client.incr.return_value = 1
    monkeypatch.setattr("app.services.fb_share.classify.redis_client", lambda: client)
    assert classify_pending_items(db_session, fanpage, generate=lambda prompt: json.dumps({"items": [output]})) == 0
    assert (row.caption, row.hashtags) == ("Original caption", "#A #B #C #D")
    assert row.sentiment is None and row.status == "new"


@pytest.mark.parametrize("stored,trimmed", [
    ("#A #B #C #D #E", "#A #B #C"), ("#A  #B", "#A #B"), ("", ""), (None, ""), ("#A,#B;#C #D", "#A #B #C"),
])
def test_invalid_quote_leaves_item_unclassified(db_session, fanpage, stored, trimmed, monkeypatch):
    client = Mock()
    client.incr.return_value = 1
    monkeypatch.setattr("app.services.fb_share.classify.redis_client", lambda: client)
    row = item(db_session, caption="Original caption", hashtags=stored)
    output = result(row, group_caption='Max: "never said this" today.', group_hashtags=["#F1"])
    assert classify_pending_items(db_session, fanpage, generate=lambda prompt: json.dumps({"items": [output]})) == 0
    assert (row.caption, row.hashtags) == ("Original caption", stored)
    assert row.sentiment is None


def test_unclassified_items_keep_their_hashtags(db_session, fanpage):
    row = item(db_session, hashtags="#A #B #C #D")
    assert classify_pending_items(db_session, fanpage, generate=lambda prompt: json.dumps({"items": []})) == 0
    assert row.hashtags == "#A #B #C #D"


def test_group_caption_at_limit(db_session, fanpage):
    row = item(db_session, hashtags="#A")
    output = result(row, group_caption="x" * 280, group_hashtags=["#F1", "#Racing"])
    assert classify_pending_items(db_session, fanpage, generate=lambda prompt: json.dumps({"items": [output]})) == 1
    assert (row.caption, row.hashtags) == ("x" * 280, "#F1 #Racing")


@pytest.mark.parametrize("group_caption,applied", [
    ('Max: "I gave everything today and the car was perfect." after the win.', True),
    ("Max: \u201cI gave everything   today and the car was perfect.\u201d after the win.", True),
    ('Max: "I gave everything" after the win.', False),
    ('Max: "I gave it all today" after the win.', False),
    ('Max: "I gave everything today, and more" after the win.', False),
    ("Max: \u201cI gave all\u201d after the win.", False),
])
def test_quotes_must_be_verbatim(db_session, fanpage, group_caption, applied, monkeypatch):
    client = Mock()
    client.incr.return_value = 1
    monkeypatch.setattr("app.services.fb_share.classify.redis_client", lambda: client)
    source = 'Max after the race: "I gave\neverything today and the car was perfect." #F1'
    row = item(db_session, caption=source, hashtags="#F1")
    output = result(row, group_caption=group_caption, group_hashtags=["#F1", "#Max"])
    assert classify_pending_items(db_session, fanpage, generate=lambda prompt: json.dumps({"items": [output]})) == int(applied)
    assert row.caption == (group_caption if applied else source)
    assert row.hashtags == ("#F1 #Max" if applied else "#F1")
