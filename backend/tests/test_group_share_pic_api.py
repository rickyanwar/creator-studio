from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.schema import DefaultClause

from app.api.deps import get_db
from app.main import app
from app.models.api_tokens import ApiToken
from app.models.fb_group_share import FbGroup, FbGroupMembership, FbShareDelivery, FbShareItem, FbShareTask
from app.services import api_tokens
from app.services.fb_share.pic_messages import OFF_TOPIC_TEXT, confirmation_text
from app.services.phone_runner_client import PhoneRunnerClient
from backend.tests.test_fb_group_share_models import db_session


@pytest.fixture
def setup(db_session):
    column = ApiToken.__table__.c.created_at
    original = column.server_default
    column.server_default = DefaultClause(text("CURRENT_TIMESTAMP"))
    try:
        ApiToken.__table__.create(db_session.bind)
    finally:
        column.server_default = original
    token, plaintext = api_tokens.create_token(db_session, "pic-test", ["group_share:pic"])
    db_session.add(FbShareDelivery(destination_id=1, mode="pic_whatsapp", pic_contact="+62 812-3456-7890"))
    db_session.add(FbShareItem(id=1, fanpage_id=1, niche="f1", source_kind="manual", source_ref="pic", media_kind="photo", media=[]))
    db_session.flush()
    for i in (3, 1, 2):
        db_session.add(FbGroup(id=i, niche="f1", url=f"https://facebook.com/groups/g{i}", name=f"Group {i}", kind="general"))
    db_session.flush()
    for i in (3, 1, 2):
        db_session.add(FbShareTask(id=i * 10, item_id=1, group_id=i, destination_id=1, delivery_mode="pic_whatsapp",
                                   state="sent_to_pic", scheduled_for=datetime.now(), pic_message_ref="pic-1-1"))
    db_session.commit()
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as client:
        yield client, {"Authorization": f"Bearer {plaintext}"}, db_session
    app.dependency_overrides.clear()


def reply(setup, text, **values):
    client, headers, _ = setup
    response = client.post("/group-share/pic-reply", headers=headers, json={
        "channel": "whatsapp", "sender": "6281234567890@s.whatsapp.net", "text": text, **values,
    })
    assert response.status_code == 200, response.text
    return response.json()


def tasks(db):
    db.expire_all()
    return db.query(FbShareTask).order_by(FbShareTask.id).all()


def test_unknown_and_jid_numbering(setup):
    assert reply(setup, "1 sudah", sender="99999999") == {"handled": False, "reply": None}
    assert reply(setup, "1 sudah")["handled"] is True
    assert [t.state for t in tasks(setup[2])] == ["published", "sent_to_pic", "sent_to_pic"]


@pytest.mark.parametrize("text,index,status", [("2 bukan anggota", 1, "not_member"), ("3 dikeluarkan", 2, "removed")])
def test_membership_upsert(setup, text, index, status):
    reply(setup, text)
    db = setup[2]
    assert tasks(db)[index].state == "not_member"
    membership = db.query(FbGroupMembership).one()
    assert (membership.destination_id, membership.group_id, membership.status) == (1, index + 1, status)
    tasks(db)[index].state = "sent_to_pic"
    db.commit()
    reply(setup, text)
    assert db.query(FbGroupMembership).count() == 1


@pytest.mark.parametrize("notify_raises", [False, True])
def test_block_all_delivery_bundles(setup, monkeypatch, notify_raises):
    db = setup[2]
    rows = tasks(db)
    rows[2].pic_message_ref = "older"
    rows[2].updated_at = datetime.now() - timedelta(days=1)
    rows[0].state = "published"
    db.commit()
    calls = []
    def notify(self, message):
        calls.append(message)
        if notify_raises:
            raise RuntimeError("unavailable")
        return True, "sent"
    monkeypatch.setattr(PhoneRunnerClient, "notify_owner", notify)
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    reply(setup, "diblokir")
    assert [t.state for t in tasks(db)] == ["published", "blocked", "blocked"]
    delivery = db.query(FbShareDelivery).one()
    assert delivery.block_count == 1
    assert before + timedelta(hours=72) <= delivery.paused_until <= datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=72)
    assert calls == ["Group Share: Profile reported blocked by PIC; paused 72 h"]


def test_post_url_and_published_immutable(setup):
    db = setup[2]
    url = "https://facebook.com/groups/g2/posts/12345"
    reply(setup, url)
    assert tasks(db)[1].post_url == url
    reply(setup, "2 dikeluarkan")
    assert tasks(db)[1].state == "published"
    assert db.query(FbGroupMembership).count() == 0


def test_off_topic_pending_and_no_open_bundle(setup):
    assert reply(setup, "hello")["reply"] == OFF_TOPIC_TEXT
    assert reply(setup, "2 pending")["reply"] == OFF_TOPIC_TEXT
    assert all(t.state == "sent_to_pic" for t in tasks(setup[2]))
    reply(setup, "semua sudah")
    assert reply(setup, "")["reply"] == OFF_TOPIC_TEXT
    assert reply(setup, "hello")["reply"] == OFF_TOPIC_TEXT


@pytest.mark.parametrize("has_history", [True, False])
def test_block_without_open_bundle(setup, monkeypatch, has_history):
    db = setup[2]
    for task in tasks(db):
        task.state = "published"
        if not has_history:
            task.pic_message_ref = None
    delivery = db.query(FbShareDelivery).one()
    delivery.block_count = 2
    db.commit()
    calls = []
    monkeypatch.setattr(PhoneRunnerClient, "notify_owner", lambda self, message: calls.append(message))
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    assert reply(setup, "diblokir") == {
        "handled": True, "reply": confirmation_text(set(), set(), set(), blocked=True, remaining=0),
    }
    db.refresh(delivery)
    assert delivery.block_count == 3
    assert before + timedelta(hours=72) <= delivery.paused_until <= datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=72)
    assert calls == ["Group Share: Profile reported blocked by PIC; paused 72 h"]
    assert all(task.state == "published" for task in tasks(db))


def test_latest_open_bundle_and_telegram_case(setup):
    db = setup[2]
    delivery = db.query(FbShareDelivery).one()
    delivery.mode, delivery.pic_contact = "pic_telegram", "@Editor"
    db.flush()
    rows = tasks(db)
    rows[0].pic_message_ref = "older"
    rows[0].updated_at = datetime.now() - timedelta(days=1)
    db.commit()
    reply(setup, "1 sudah", channel="telegram", sender="editor")
    assert [t.state for t in tasks(db)] == ["sent_to_pic", "published", "sent_to_pic"]


def test_auth_and_body_limits(setup):
    client, _, db = setup
    body = {"channel": "whatsapp", "sender": "6281234567890", "text": "hello"}
    assert client.post("/group-share/pic-reply", json=body).status_code in (401, 403)
    assert client.post("/group-share/pic-reply", json=body, headers={"Authorization": "Bearer invalid"}).status_code == 401
    _, plaintext = api_tokens.create_token(db, "wrong-scope", ["read"])
    assert client.post("/group-share/pic-reply", json=body, headers={"Authorization": f"Bearer {plaintext}"}).status_code == 403
    assert client.post("/group-share/pic-reply", json={**body, "text": "x" * 2001}, headers=setup[1]).status_code == 422
    assert client.post("/group-share/pic-reply", json={**body, "username": "x" * 65}, headers=setup[1]).status_code == 422


@pytest.mark.parametrize("contact,username", [
    ("@RinaPic", "rinapic"), ("@RinaPic", "@RINAPIC"),
    ("123456789", None), ("123456789", "unrelated"),
])
def test_telegram_matches_username_or_sender_id(setup, contact, username):
    db = setup[2]
    delivery = db.query(FbShareDelivery).one()
    delivery.mode, delivery.pic_contact = "pic_telegram", contact
    db.commit()
    assert reply(setup, "1 sudah", channel="telegram", sender="123456789", username=username)["handled"] is True
    assert [t.state for t in tasks(db)] == ["published", "sent_to_pic", "sent_to_pic"]


def test_whatsapp_ignores_username(setup):
    assert reply(setup, "1 sudah", sender="99999999", username="6281234567890")["handled"] is False
