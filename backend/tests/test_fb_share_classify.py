"""Caption classifier batching, normalization and fail-closed contracts."""

from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.fb_group_share import FbShareItem
from app.services.fb_share.classify import CLASSIFY_PROMPT, classify_pending_items


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://")
    FbShareItem.__table__.create(engine)
    with sessionmaker(bind=engine)() as db:
        yield db
    engine.dispose()


@pytest.fixture
def fanpage():
    return SimpleNamespace(id=1, fb_share_settings={"niches": ["f1", "motogp"]})


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
    fields = dict(id=row.id, subjects=["Max"], sentiment={"Max": "positive"}, content_type="news", niche="f1")
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
    assert classify_pending_items(db_session, fanpage, generate=generate) == 2
    generate.assert_called_once()
    prompt = generate.call_args.args[0]
    assert prompt == CLASSIFY_PROMPT.replace("{niches}", '["f1", "motogp"]').replace("{posts}", "\n".join(
        f"id={row.id} media={row.media_kind} caption={' '.join(row.caption[:600].splitlines())}"
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
