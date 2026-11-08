"""Authenticated group-share API contracts, using isolated SQLite storage."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.fb_group_share import FbGroup, FbShareDelivery, FbShareItem, FbShareTask
from backend.tests.test_fb_group_share_models import db_session


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: "admin_user"
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def group(client, **values):
    return client.post("/fb-groups", json={"niche": "f1", "name": "F1", "kind": "general",
                                         "url": "https://facebook.com/groups/f1", **values})


@pytest.mark.parametrize("url", [" https://m.facebook.com/groups/f1/ ", "https://fb.com/groups/f1?ref=share", "facebook.com/groups/f1/",
                                "https://web.facebook.com/groups/F1", "https://mbasic.facebook.com/groups/F1/",
                                "https://www.facebook.com/groups/F1", "https://www.fb.com/groups/F1"])
def test_group_crud_and_normalization(client, url):
    response = group(client, url=url)
    assert response.status_code == 200
    row = response.json()
    assert row["url"] == "https://www.facebook.com/groups/f1"
    assert group(client).status_code == 409
    assert client.get("/fb-groups?niche=f1").json() == [row]
    assert client.get("/fb-groups/niches").json() == ["f1"]
    assert client.patch(f"/fb-groups/{row['id']}", json={"name": "New"}).json()["name"] == "New"
    assert client.delete(f"/fb-groups/{row['id']}").json()["is_active"] is False
    assert client.get("/fb-groups").json() == []
    assert len(client.get("/fb-groups?include_inactive=true").json()) == 1
    assert client.get("/fb-groups/niches").json() == []


@pytest.mark.parametrize("values", [
    {"url": "https://facebook.com/person"}, {"url": "https://evilfacebook.com/groups/f1"},
    {"kind": "invalid"}, {"allowed_days": [7]}, {"banned_content": ["invalid"]}, {"daily_cap": -1},
])
def test_invalid_groups(client, values):
    assert group(client, **values).status_code == 422


@pytest.mark.parametrize("slug", ["feed", "discover", "joins", "create", "notifications", "search", "your_groups", "yourgroups"])
def test_reserved_group_slugs_rejected_on_create_and_patch(client, slug):
    url = f"https://facebook.com/groups/{slug.upper()}"
    assert group(client, url=url).status_code == 422
    row = group(client).json()
    assert client.patch(f"/fb-groups/{row['id']}", json={"url": url}).status_code == 422


def test_settings_defaults_and_merge(client):
    path = "/fanpages/1/group-share"
    defaults = client.get(path).json()
    assert defaults["enabled"] is False
    assert defaults["settings"]["caps"]["min_gap_minutes"] == 30
    result = client.put(path, json={"enabled": True, "settings": {"sources": {"ig": {"source": "both"}}}})
    assert result.status_code == 200
    assert result.json()["settings"]["sources"]["ig"] == {"source": "both", "groups_only_per_day": 0}
    assert result.json()["settings"]["caps"] == defaults["settings"]["caps"]
    assert client.get(path).json() == result.json()
    assert client.get("/fanpages/999/group-share").status_code == 404


def test_patch_null_and_duplicate_urls(client):
    first = group(client).json()
    second = group(client, url="https://facebook.com/groups/other").json()
    assert client.patch(f"/fb-groups/{first['id']}", json={"url": None}).status_code == 422
    assert client.patch(f"/fb-groups/{first['id']}", json={"name": None}).status_code == 422
    assert client.patch(f"/fb-groups/{second['id']}", json={"url": first["url"]}).status_code == 409
    assert client.delete("/fb-groups/999").status_code == 404


@pytest.mark.parametrize("settings", [
    {"sources": {"bad_mode": {}}}, {"sources": {"ig": {"source": "bad"}}},
    {"sources": {"ig": {"groups_only_per_day": 51}}}, {"caps": {"min_gap_minutes": 10}},
    {"caps": {"max_per_day": 51}}, {"caps": {"start_per_day": -1}},
    {"caps": {"start_per_day": 51}}, {"caps": {"weekly_increase": 51}},
    {"caps": {"per_group_per_day": 51}}, {"caps": {"min_gap_minutes": 721}},
    {"caps": {"start_per_day": 4, "max_per_day": 3}},
])
def test_invalid_settings(client, settings):
    assert client.put("/fanpages/1/group-share", json={"enabled": True, "settings": settings}).status_code == 422


def test_accounts_and_calibration(client, db_session):
    path = "/fanpages/1/group-share/accounts"
    rows = client.get(path).json()
    assert [row["kind"] for row in rows] == ["facebook_profile", "facebook_page"]
    assert rows[1]["parent_profile_name"] == "Profile"
    assert rows[0]["delivery"] is None
    body = {"mode": "phone_auto", "phone_device_id": 1, "is_active": True}
    result = client.put(path + "/1", json=body)
    assert result.status_code == 422 and "Calibrate first" in result.text
    body["is_active"] = False
    assert client.put(path + "/1", json=body).status_code == 200
    delivery = db_session.query(FbShareDelivery).one()
    delivery.calibrated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db_session.commit()
    body["is_active"] = True
    assert client.put(path + "/1", json=body).json()["delivery"]["is_active"] is True
    assert client.put(path + "/3", json=body).status_code == 404
    assert client.put(path + "/2", json={"mode": "pic_whatsapp", "pic_name": "Editor", "pic_contact": "+62 812-3456-7890"}).status_code == 200
    assert client.put(path + "/2", json={"mode": "pic_telegram", "pic_name": "Editor", "pic_contact": "@editor"}).status_code == 200


@pytest.mark.parametrize("body", [
    {"mode": "phone_auto"}, {"mode": "pic_whatsapp", "pic_contact": "abc12345678"},
    {"mode": "pic_whatsapp", "pic_contact": "123"}, {"mode": "pic_telegram", "pic_contact": "editor"},
    {"mode": "pic_telegram"}, {"task_hours_start": "25:00"}, {"task_hours_end": "8:00"},
    {"calibrated_at": "2026-11-08T00:00:00"}, {"calibration": {}},
    {"start_per_day": 51}, {"weekly_increase": 51}, {"max_per_day": 51},
    {"start_per_day": 4, "max_per_day": 3},
])
def test_invalid_delivery(client, body):
    assert client.put("/fanpages/1/group-share/accounts/1", json=body).status_code == 422


def test_activation_requires_delivery_mode(client):
    result = client.put("/fanpages/1/group-share/accounts/1", json={"is_active": True, "mode": None})
    assert result.status_code == 422
    assert "Choose who posts before activating" in result.text


@pytest.mark.parametrize("mode,contact", [("pic_whatsapp", "+62 812-3456-7890"), ("pic_telegram", "@editor")])
@pytest.mark.parametrize("name", [None, "", "   "])
def test_pic_requires_name(client, mode, contact, name):
    result = client.put("/fanpages/1/group-share/accounts/1", json={"mode": mode, "pic_contact": contact, "pic_name": name})
    assert result.status_code == 422
    assert "pic_name" in result.text


@pytest.mark.parametrize("values", [
    {"mode": "phone_auto", "phone_device_id": 2},
    {"mode": "pic_telegram", "pic_name": "Editor", "pic_contact": "@editor"},
])
def test_changing_delivery_clears_calibration(client, db_session, values):
    from app.models.social_publishing import PhoneDevice

    db_session.add(PhoneDevice(id=2, name="Second phone", adb_serial="second"))
    db_session.add(FbShareDelivery(destination_id=1, mode="phone_auto", phone_device_id=1,
                                   calibration={"share": {"ok": True}}, calibrated_at=datetime(2026, 11, 8)))
    db_session.commit()
    result = client.put("/fanpages/1/group-share/accounts/1", json=values)
    assert result.status_code == 200
    assert result.json()["delivery"]["calibration"] is None
    assert result.json()["delivery"]["calibrated_at"] is None


def test_switching_phone_cannot_reuse_calibration_to_activate(client, db_session):
    db_session.add(FbShareDelivery(destination_id=1, mode="phone_auto", phone_device_id=1,
                                   calibration={"share": {"ok": True}}, calibrated_at=datetime(2026, 11, 8)))
    db_session.commit()
    result = client.put("/fanpages/1/group-share/accounts/1", json={"mode": "phone_auto", "phone_device_id": 2, "is_active": True})
    assert result.status_code == 422
    assert "Calibrate first" in result.text


@pytest.mark.parametrize("limits", [{"start_per_day": 50, "weekly_increase": 50, "max_per_day": 50},
                                    {"start_per_day": None}, {"max_per_day": None}])
def test_valid_delivery_cap_boundaries(client, limits):
    assert client.put("/fanpages/1/group-share/accounts/1", json=limits).status_code == 200


@pytest.mark.parametrize("gap", [15, 720])
def test_valid_brand_cap_boundaries(client, gap):
    caps = {"start_per_day": 50, "weekly_increase": 50, "max_per_day": 50, "per_group_per_day": 50, "min_gap_minutes": gap}
    assert client.put("/fanpages/1/group-share", json={"enabled": True, "settings": {"caps": caps}}).status_code == 200


def test_memberships_upsert_and_scope(client):
    gid = group(client).json()["id"]
    path = "/fanpages/1/group-share/memberships"
    item = {"destination_id": 1, "group_id": gid, "status": "joined"}
    assert client.put(path, json={"items": [item]}).json()[0]["status"] == "joined"
    item["status"] = "removed"
    assert len(client.put(path, json={"items": [item]}).json()) == 1
    assert client.get(path).json()[0]["status"] == "removed"
    for values in ({"destination_id": 3}, {"group_id": 999}):
        assert client.put(path, json={"items": [{**item, **values}]}).status_code == 404
    assert client.get(path).json()[0]["status"] == "removed"


def test_memberships_duplicate_batch_and_atomic_validation(client):
    gid = group(client).json()["id"]
    path = "/fanpages/1/group-share/memberships"
    item = {"destination_id": 1, "group_id": gid, "status": "joined"}
    assert client.put(path, json={"items": [item, {**item, "status": "removed"}]}).json()[0]["status"] == "removed"
    assert client.put(path, json={"items": [item, {**item, "destination_id": 3}]}).status_code == 404
    assert client.get(path).json()[0]["status"] == "removed"


def test_summary_utc_boundaries_and_brand_scope(client, db_session):
    from app.api import group_share
    from unittest.mock import patch

    now = datetime(2026, 11, 8, 0, 1)
    item = FbShareItem(fanpage_id=1, niche="f1", source_kind="manual", source_ref="utc", media_kind="photo", media=[])
    db_session.add(item)
    db_session.flush()
    for index, (destination_id, scheduled) in enumerate(((1, datetime(2026, 11, 7, 23, 59)), (1, datetime(2026, 11, 8)), (1, datetime(2026, 11, 9)), (3, now))):
        row = FbGroup(niche="f1", name=str(index), url=f"https://www.facebook.com/groups/utc{index}", kind="general")
        db_session.add(row)
        db_session.flush()
        db_session.add(FbShareTask(item_id=item.id, group_id=row.id, destination_id=destination_id, delivery_mode="pic_telegram", scheduled_for=scheduled, state="published"))
    db_session.commit()
    with patch.object(group_share, "datetime", wraps=datetime) as clock:
        clock.now.return_value = now.replace(tzinfo=timezone.utc)
        assert client.get("/fanpages/1/group-share/summary").json()["today_published"] == 1
    assert len(client.get("/fanpages/1/group-share/tasks").json()) == 3


def test_tasks_and_summary(client, db_session):
    gid = group(client).json()["id"]
    now = datetime.now(timezone.utc).replace(tzinfo=None, hour=12)
    item = FbShareItem(fanpage_id=1, niche="f1", source_kind="manual", source_ref="1", media_kind="photo", media=["photo.jpg"], caption="Caption")
    db_session.add(item)
    db_session.flush()
    for index, state in enumerate(("published", "planned", "pending_admin")):
        other = FbGroup(niche="f1", name=str(index), url=f"https://www.facebook.com/groups/{index}", kind="general")
        db_session.add(other)
        db_session.flush()
        db_session.add(FbShareTask(item_id=item.id, group_id=other.id, destination_id=1, delivery_mode="pic_telegram", scheduled_for=now - timedelta(minutes=index), state=state))
    db_session.add(FbShareDelivery(destination_id=1, paused_until=now + timedelta(days=1)))
    db_session.commit()
    path = "/fanpages/1/group-share"
    membership = {"destination_id": 1, "group_id": gid, "status": "joined"}
    client.put(path + "/memberships", json={"items": [membership, {**membership, "destination_id": 2}]})
    tasks = client.get(path + "/tasks").json()
    assert len(tasks) == 3 and tasks[0]["state"] == "published"
    assert tasks[0]["caption"] == "Caption" and tasks[0]["media_first"] == "photo.jpg"
    assert tasks[0]["destination_name"] == "Profile"
    assert len(client.get(path + "/tasks?state=planned&limit=1").json()) == 1
    assert client.get(path + "/tasks?limit=201").status_code == 422
    assert client.get(path + "/summary").json() == {"enabled": False, "today_published": 1, "today_planned": 1, "pending_admin": 1, "paused_accounts": 1, "groups_joined": 1}
    assert client.get("/fanpages/2/group-share/tasks").json() == []
