"""Phone (phone_auto) group posting: selection, FB version gate, device lease, outcome mapping, calibration.

The runner is always mocked. Contract: .notes/CONTRACT-group-publish.md.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import text

from app.models.fb_group_share import (
    FbGroup, FbGroupMembership, FbShareDelivery, FbShareItem, FbShareTask, FbShareTaskCaption,
)
from app.models.social_publishing import DeviceLease, PhoneDevice, SocialDestination
from app.services.fb_share import phone_dispatch, planner
from app.services.fb_share.brand_settings import set_brand_share
from app.services.phone_runner_client import PhoneRunnerClient
from backend.tests.test_fb_group_share_models import db_session  # noqa: F401  (fixture)

NOW = datetime(2026, 11, 10, 10)
PHOTO = "https://example.org/photo.jpg"


class FakeCache:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def db(db_session):
    engine = db_session.get_bind()
    # Same columns as DeviceLease without the publish_deliveries FK (that table is not created here).
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE device_leases (device_id INTEGER PRIMARY KEY, delivery_id INTEGER UNIQUE, "
            "acquired_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, expires_at DATETIME NOT NULL)"))
    FbShareTaskCaption.__table__.create(engine)
    return db_session


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setenv("FB_SHARE_DISPATCH_ENABLED", "1")


@pytest.fixture(autouse=True)
def default_cache(monkeypatch):
    cache = FakeCache()
    monkeypatch.setattr(phone_dispatch, "_default_cache", lambda: cache)
    return cache


def runner(outcome="published", **values):
    result = {"ok": outcome == "published", "outcome": outcome, "post_url": None, "fb_version": "500.0",
              "detail": "", "stage": "done", **values}
    return Mock(group_publish=Mock(return_value=result), probe_fb_version=Mock(return_value=("500.0", "123")),
                notify_owner=Mock(return_value=(True, "")), group_calibrate=Mock())


def seed(db, destination_id=1, groups=1, calibrated=True, **delivery_values):
    set_brand_share(db, 1, True, {})
    device = db.get(PhoneDevice, 1)
    device.adb_serial, device.status = "phone-1", "available"
    destination = db.get(SocialDestination, destination_id)
    destination.timezone, destination.facebook_actor_id = "UTC", f"actor{destination_id}"
    values = dict(destination_id=destination_id, mode="phone_auto", phone_device_id=1, is_active=True,
                  task_hours_start="00:00", task_hours_end="00:00", created_at=NOW)
    if calibrated:
        values.update(calibration={"fb_version": "500.0", "phone_device_id": 1}, calibrated_at=NOW - timedelta(days=1))
    delivery = FbShareDelivery(**{**values, **delivery_values})
    db.add(delivery)
    for number in range(1, groups + 1):
        if db.get(FbGroup, number) is None:
            db.add(FbGroup(id=number, niche="f1", name=f"Group {number}", kind="general",
                           url=f"https://www.facebook.com/groups/g{number}"))
        db.flush()
        db.add(FbGroupMembership(destination_id=destination_id, group_id=number, status="joined"))
    db.commit()
    return delivery


def make_item(db, ref="first", **values):
    row = FbShareItem(**{"fanpage_id": 1, "niche": "f1", "source_kind": "manual", "source_ref": ref,
                         "media_kind": "photo", "media": [PHOTO], "sentiment": {}, "caption": "Short caption",
                         "hashtags": "#f1 #race", "status": "routed", "created_at": NOW, **values})
    db.add(row)
    db.commit()
    return row


def make_task(db, item, group_id=1, destination_id=1, **values):
    entry = FbShareTask(item_id=item.id, group_id=group_id, destination_id=destination_id,
                        delivery_mode="phone_auto", scheduled_for=NOW - timedelta(minutes=1), **{
                            "state": "planned", **values})
    db.add(entry)
    db.commit()
    return entry


def publish(db, task, client, cache=None, now=NOW):
    return phone_dispatch.publish_task(db, task.id, client, now, cache=cache if cache is not None else FakeCache())


# ── runner client mapping ─────────────────────────────────────────────────────────────────────────────────────────


def _client(monkeypatch, response=None, error=None):
    client = PhoneRunnerClient(base_url="http://runner", token="secret")

    def post(path, payload, timeout):
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(client, "_post_json", post)
    return client


def _response(status, body=None):
    return httpx.Response(status, json=body) if body is not None else httpx.Response(status, text="x")


@pytest.mark.parametrize("response,error,rehearse,expected", [
    (_response(200, {"ok": True, "outcome": "published", "post_url": "https://www.facebook.com/groups/g/posts/1",
                     "fb_version": "500.0", "detail": "done", "stage": "verify"}), None, False, "published"),
    (_response(409, {"status": "busy"}), None, False, "busy"),
    (_response(500), None, False, "uncertain"),
    (_response(500), None, True, "failed"),
    (_response(400), None, False, "failed"),
    (None, httpx.ConnectError("refused"), False, "failed"),
    (None, httpx.ReadTimeout("slow"), False, "uncertain"),
    (None, httpx.ReadTimeout("slow"), True, "failed"),
    (_response(200, {"ok": True, "outcome": "posted?"}), None, False, "uncertain"),
    (_response(200, ["bad"]), None, False, "uncertain"),
])
def test_group_publish_client_mapping(monkeypatch, response, error, rehearse, expected):
    result = _client(monkeypatch, response, error).group_publish({"rehearse": rehearse})
    assert result["outcome"] == expected
    assert set(result) == {"ok", "outcome", "post_url", "fb_version", "detail", "stage"}
    if expected == "published":
        assert result["ok"] is True and result["post_url"].endswith("/posts/1") and result["fb_version"] == "500.0"


def test_group_publish_missing_configuration_never_calls_runner():
    result = PhoneRunnerClient(base_url="http://runner", token="").group_publish({"rehearse": False})
    assert result["outcome"] == "failed" and result["detail"] == "missing_configuration"


def test_group_calibrate_and_probe_client(monkeypatch):
    body = {"ok": True, "fb_version": "500.0", "fb_version_code": "123", "android": "14", "screen": "1080x2400",
            "steps": [{"name": "open_group", "ok": True, "detail": "ok"}, "junk"], "detail": "rehearsed"}
    result = _client(monkeypatch, _response(200, body)).group_calibrate({})
    assert result["ok"] is True and result["fb_version"] == "500.0" and result["screen"] == "1080x2400"
    assert result["steps"] == [{"name": "open_group", "ok": True, "detail": "ok"}]
    assert _client(monkeypatch, _response(409, {})).group_calibrate({})["busy"] is True
    assert _client(monkeypatch, None, httpx.ReadTimeout("x")).group_calibrate({})["ok"] is False
    client = PhoneRunnerClient(base_url="http://runner", token="secret")
    monkeypatch.setattr(client, "probe_device", lambda device: {"success": True, "fb_version": "500.0",
                                                                "fb_version_code": 123})
    assert client.probe_fb_version(object()) == ("500.0", "123")
    monkeypatch.setattr(client, "probe_device", lambda device: {"success": False})
    assert client.probe_fb_version(object()) == ("", "")


# ── posting ───────────────────────────────────────────────────────────────────────────────────────────────────────


def test_published_payload_caption_variant_and_lease(db):
    seed(db)
    task = make_task(db, make_item(db))
    db.add(FbShareTaskCaption(task_id=task.id, caption="Variant caption", hashtags=["#one", "#two"]))
    db.commit()
    client = runner(post_url="https://www.facebook.com/groups/g1/posts/9")
    leases_during_call = []
    client.group_publish.side_effect = lambda payload: (
        leases_during_call.append(db.query(DeviceLease).count()) or client.group_publish.return_value)
    assert publish(db, task, client) == "published"
    payload = client.group_publish.call_args.args[0]
    assert payload == {
        "task_id": task.id, "serial": "phone-1",
        "account": {"kind": "facebook_profile", "name": "Profile", "actor_id": "actor1", "parent_profile_name": None},
        "group": {"url": "https://www.facebook.com/groups/g1", "name": "Group 1"},
        "media": [{"url": PHOTO, "kind": "photo"}], "caption": "Variant caption\n#one #two",
        "rehearse": False, "expected_fb_version": "500.0",
    }
    assert leases_during_call == [1] and db.query(DeviceLease).count() == 0
    assert task.state == "published" and task.post_url.endswith("/posts/9")


def test_page_account_and_base_caption(db):
    seed(db, destination_id=2)
    item = make_item(db, media_kind="album", media=[PHOTO, "https://example.org/2.jpg"])
    task = make_task(db, item, destination_id=2)
    client = runner()
    publish(db, task, client)
    payload = client.group_publish.call_args.args[0]
    assert payload["account"] == {"kind": "facebook_page", "name": "Page", "actor_id": "actor2",
                                  "parent_profile_name": "Profile"}
    assert payload["caption"] == "Short caption\n#f1 #race"
    assert [media["kind"] for media in payload["media"]] == ["photo", "photo"]


def test_version_mismatch_pauses_without_posting(db):
    delivery = seed(db)
    task = make_task(db, make_item(db))
    client = runner()
    client.probe_fb_version.return_value = ("501.0", "124")
    assert publish(db, task, client) == "version_changed"
    client.group_publish.assert_not_called()
    assert task.state == "planned" and delivery.calibrated_at is None
    assert delivery.calibration["paused_reason"] == "recalibrate: FB app changed 500.0 -> 501.0"
    assert "500.0 -> 501.0" in client.notify_owner.call_args.args[0]


def test_unknown_version_waits_and_cache_is_used(db):
    seed(db)
    task = make_task(db, make_item(db))
    client = runner()
    client.probe_fb_version.return_value = ("", "")
    assert publish(db, task, client).startswith("skipped")
    client.group_publish.assert_not_called()
    assert task.state == "planned"
    cache = FakeCache({"fb_share:fb_version:1": "500.0|123"})
    client.probe_fb_version.return_value = ("", "")
    assert publish(db, task, client, cache) == "published"
    client.probe_fb_version.assert_called_once()
    fresh = FakeCache()
    phone_dispatch.current_fb_version(db.get(PhoneDevice, 1), runner(), fresh)
    assert fresh.values == {"fb_share:fb_version:1": "500.0|123"}


def test_busy_lease_leaves_task_planned(db):
    seed(db)
    task = make_task(db, make_item(db))
    db.add(DeviceLease(device_id=1, delivery_id=None, acquired_at=NOW, expires_at=NOW + timedelta(minutes=5)))
    db.commit()
    client = runner()
    assert publish(db, task, client) == "skipped: busy"
    client.group_publish.assert_not_called()
    assert task.state == "planned" and task.attempts == 0
    assert db.query(DeviceLease).count() == 1


@pytest.mark.parametrize("outcome,state", [
    ("pending_admin", "pending_admin"), ("uncertain", "uncertain"), ("busy", "planned"),
])
def test_simple_outcomes(db, outcome, state):
    seed(db)
    task = make_task(db, make_item(db))
    assert publish(db, task, runner(outcome)) == state
    assert task.attempts == 0 and task.next_retry_at is None
    assert db.query(DeviceLease).count() == 0


def test_not_member_updates_membership(db):
    seed(db)
    task = make_task(db, make_item(db))
    publish(db, task, runner("not_member"))
    membership = db.query(FbGroupMembership).filter_by(destination_id=1, group_id=1).one()
    assert task.state == "not_member" and membership.status == "not_member" and membership.checked_at == NOW


def test_blocked_pauses_72h_and_notifies(db):
    delivery = seed(db)
    task = make_task(db, make_item(db))
    client = runner("blocked")
    publish(db, task, client)
    assert task.state == "blocked" and delivery.block_count == 1
    assert delivery.paused_until == NOW + timedelta(hours=72)
    client.notify_owner.assert_called_once()


@pytest.mark.parametrize("outcome,task_state,reason", [
    ("version_changed", "planned", "recalibrate: FB app changed 500.0 -> 501.0"),
    ("identity_failed", "failed", "identity check failed: wrong profile"),
])
def test_runner_pauses_delivery(db, outcome, task_state, reason):
    delivery = seed(db)
    task = make_task(db, make_item(db))
    client = runner(outcome, fb_version="501.0", detail="wrong profile")
    cache = FakeCache()
    publish(db, task, client, cache)
    assert task.state == task_state and delivery.calibrated_at is None
    assert delivery.calibration["paused_reason"] == reason
    client.notify_owner.assert_called_once()
    if outcome == "version_changed":
        assert cache.values["fb_share:fb_version:1"].startswith("501.0")


@pytest.mark.parametrize("outcome", ["failed", "rehearsed"])
def test_failed_retries_15_30_60_then_failed(db, outcome):
    seed(db)
    task = make_task(db, make_item(db))
    now = NOW
    for delay in (15, 30, 60):
        assert publish(db, task, runner(outcome), now=now) == "planned"
        assert task.next_retry_at == now + timedelta(minutes=delay)
        assert publish(db, task, runner(), now=now + timedelta(minutes=delay - 1)) == "not_due"
        now += timedelta(minutes=delay)
    assert publish(db, task, runner(outcome), now=now) == "failed"
    assert task.attempts == 4


def test_client_exception_after_claim_is_uncertain(db):
    seed(db)
    task = make_task(db, make_item(db))
    client = runner()
    client.group_publish.side_effect = RuntimeError("boom")
    assert publish(db, task, client) == "uncertain"
    assert db.query(DeviceLease).count() == 0


@pytest.mark.parametrize("change", ["disabled_env", "uncalibrated", "paused", "pic_mode", "not_planned", "future"])
def test_publish_task_guards(db, monkeypatch, change):
    delivery = seed(db)
    task = make_task(db, make_item(db))
    if change == "disabled_env":
        monkeypatch.delenv("FB_SHARE_DISPATCH_ENABLED")
    elif change == "uncalibrated":
        delivery.calibrated_at = None
    elif change == "paused":
        delivery.paused_until = NOW + timedelta(hours=1)
    elif change == "pic_mode":
        delivery.mode = "pic_whatsapp"
    elif change == "not_planned":
        task.state = "uncertain"
    else:
        task.scheduled_for = NOW + timedelta(minutes=5)
    db.commit()
    client = runner()
    publish(db, task, client)
    client.group_publish.assert_not_called()
    client.probe_fb_version.assert_not_called()


def test_expired_item_and_bad_caption(db):
    seed(db, groups=2)
    expired = make_task(db, make_item(db, expires_at=NOW - timedelta(minutes=1)))
    long_caption = make_task(db, make_item(db, "long", caption="x" * 2001), group_id=2)
    client = runner()
    assert publish(db, expired, client) == "expired"
    assert publish(db, long_caption, client) == "failed"
    client.group_publish.assert_not_called()


# ── tick selection ────────────────────────────────────────────────────────────────────────────────────────────────


def test_dispatch_selects_one_task_per_account_and_enqueues(db):
    seed(db, groups=2)
    seed(db, destination_id=2, groups=2)
    item = make_item(db)
    first = make_task(db, item)
    make_task(db, item, group_id=2)
    other = make_task(db, make_item(db, "page"), destination_id=2)
    enqueue = Mock()
    assert planner.dispatch_due(db, NOW, runner(), enqueue_phone=enqueue) == 2
    assert [call.args[0] for call in enqueue.call_args_list] == [first.id, other.id]


def test_dispatch_inline_posts_when_no_enqueue(db):
    seed(db)
    task = make_task(db, make_item(db))
    client = runner()
    assert phone_dispatch.dispatch_phone_due(db, NOW, client, cache=FakeCache()) == 1
    assert task.state == "published"


@pytest.mark.parametrize("change", ["publishing", "gap", "hours", "uncalibrated", "brand_off", "inactive", "expired"])
def test_dispatch_skips(db, change):
    delivery = seed(db, groups=2)
    task = make_task(db, make_item(db))
    if change == "publishing":
        make_task(db, make_item(db, "other"), group_id=2, state="publishing", updated_at=NOW)
    elif change == "gap":
        make_task(db, make_item(db, "other"), group_id=2, state="published", updated_at=NOW - timedelta(minutes=10))
    elif change == "hours":
        delivery.task_hours_start, delivery.task_hours_end = "20:00", "21:00"
    elif change == "uncalibrated":
        delivery.calibrated_at = None
    elif change == "brand_off":
        set_brand_share(db, 1, False, {})
    elif change == "inactive":
        delivery.is_active = False
    else:
        db.get(FbShareItem, task.item_id).expires_at = NOW - timedelta(minutes=1)
    db.commit()
    enqueue = Mock()
    assert phone_dispatch.dispatch_phone_due(db, NOW, runner(), enqueue=enqueue) == 0
    enqueue.assert_not_called()


def test_gap_counts_from_brand_min_gap(db):
    seed(db, groups=2)
    make_task(db, make_item(db, "other"), group_id=2, state="published", updated_at=NOW - timedelta(minutes=31))
    make_task(db, make_item(db))
    enqueue = Mock()
    assert phone_dispatch.dispatch_phone_due(db, NOW, runner(), enqueue=enqueue) == 1


def test_stale_publishing_claim_becomes_uncertain(db):
    seed(db)
    task = make_task(db, make_item(db), state="publishing", updated_at=NOW - timedelta(minutes=46))
    fresh = make_task(db, make_item(db, "fresh"), state="publishing", updated_at=NOW - timedelta(minutes=10))
    phone_dispatch.dispatch_phone_due(db, NOW, runner(), enqueue=Mock())
    assert task.state == "uncertain" and fresh.state == "publishing"


def test_dispatch_kill_switch_skips_phone(db, monkeypatch):
    seed(db)
    make_task(db, make_item(db))
    monkeypatch.delenv("FB_SHARE_DISPATCH_ENABLED")
    enqueue = Mock()
    assert planner.dispatch_due(db, NOW, runner(), enqueue_phone=enqueue) == 0
    enqueue.assert_not_called()


# ── calibration ───────────────────────────────────────────────────────────────────────────────────────────────────


def calibration_result(**values):
    return {"ok": True, "busy": False, "fb_version": "501.0", "fb_version_code": "124", "android": "14",
            "screen": "1080x2400", "steps": [{"name": "composer", "ok": True, "detail": ""}],
            "detail": "rehearsed", **values}


def test_calibration_success_stores_result(db):
    delivery = seed(db, calibrated=False)
    make_item(db)
    client = runner()
    client.group_calibrate.return_value = calibration_result()
    cache = FakeCache()
    assert phone_dispatch.calibrate_delivery(db, 1, client, NOW, cache=cache) == "calibrated"
    payload = client.group_calibrate.call_args.args[0]
    assert payload["media"] == [{"url": PHOTO, "kind": "photo"}] and payload["serial"] == "phone-1"
    assert payload["group"] == {"url": "https://www.facebook.com/groups/g1", "name": "Group 1"}
    assert "rehearse" not in payload and "caption" not in payload
    assert delivery.calibrated_at == NOW
    assert {key: delivery.calibration[key] for key in (
        "fb_version", "fb_version_code", "android", "screen", "calibrated_by", "group_url", "ok",
        "phone_device_id")} == {
        "fb_version": "501.0", "fb_version_code": "124", "android": "14", "screen": "1080x2400",
        "calibrated_by": "rehearsal", "group_url": "https://www.facebook.com/groups/g1", "ok": True,
        "phone_device_id": 1}
    assert cache.values["fb_share:fb_version:1"] == "501.0|124"
    assert db.query(DeviceLease).count() == 0


@pytest.mark.parametrize("result", [
    calibration_result(ok=False, detail="composer not found"),
    calibration_result(steps=[{"name": "composer", "ok": False, "detail": "missing"}]),
    calibration_result(fb_version=""),
    calibration_result(steps=[]),
])
def test_calibration_failure_clears_calibrated_at(db, result):
    delivery = seed(db)
    make_item(db)
    client = runner()
    client.group_calibrate.return_value = result
    assert phone_dispatch.calibrate_delivery(db, 1, client, NOW, cache=FakeCache()) == "failed"
    assert delivery.calibrated_at is None and delivery.calibration["ok"] is False
    assert delivery.calibration["steps"] == result["steps"]


@pytest.mark.parametrize("missing", ["group", "photo"])
def test_calibration_needs_joined_group_and_photo(db, missing):
    delivery = seed(db)
    if missing == "group":
        make_item(db)
        db.query(FbGroupMembership).update({"status": "pending_approval"})
        db.commit()
    client = runner()
    assert phone_dispatch.calibrate_delivery(db, 1, client, NOW, cache=FakeCache()) == "failed"
    client.group_calibrate.assert_not_called()
    assert delivery.calibrated_at is None and missing in delivery.calibration["detail"]


def test_calibration_busy_keeps_existing_calibration(db):
    delivery = seed(db)
    make_item(db)
    client = runner()
    client.group_calibrate.return_value = calibration_result(ok=False, busy=True)
    assert phone_dispatch.calibrate_delivery(db, 1, client, NOW, cache=FakeCache()) == "busy"
    assert delivery.calibrated_at is not None and delivery.calibration == {"fb_version": "500.0", "phone_device_id": 1}
    db.add(DeviceLease(device_id=1, delivery_id=None, acquired_at=NOW, expires_at=NOW + timedelta(minutes=5)))
    db.commit()
    assert phone_dispatch.calibrate_delivery(db, 1, client, NOW, cache=FakeCache()) == "busy"
    assert client.group_calibrate.call_count == 1
    phone_dispatch.record_calibration_busy(db, 1, NOW)
    assert delivery.calibrated_at is not None and delivery.calibration["fb_version"] == "500.0"
    assert delivery.calibration["last_attempt"]["ok"] is False


# ── Celery wrappers ───────────────────────────────────────────────────────────────────────────────────────────────


def test_celery_calibration_retries_busy_then_records(monkeypatch):
    from app.tasks import fb_share as tasks

    session = Mock()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: session)
    calibrate = Mock(return_value="busy")
    record = Mock()
    monkeypatch.setattr(phone_dispatch, "calibrate_delivery", calibrate)
    monkeypatch.setattr(phone_dispatch, "record_calibration_busy", record)
    tasks.calibrate_delivery.apply(args=[1])
    assert calibrate.call_count == tasks.CALIBRATION_BUSY_RETRIES + 1
    record.assert_called_once_with(session, 1)
    calibrate.reset_mock(return_value=True)
    calibrate.return_value = "calibrated"
    record.reset_mock()
    assert tasks.calibrate_delivery.apply(args=[1]).get() == "calibrated"
    record.assert_not_called()


def test_celery_publish_group_task_calls_service(monkeypatch):
    from app.tasks import fb_share as tasks

    session = Mock()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: session)
    publish_task = Mock(return_value="published")
    monkeypatch.setattr(phone_dispatch, "publish_task", publish_task)
    tasks.publish_group_task.apply(args=[7])
    assert publish_task.call_args.args[:2] == (session, 7)
    session.close.assert_called_once()


def test_queued_task_outside_task_hours_waits(db):
    seed(db, task_hours_start="20:00", task_hours_end="21:00")
    task = make_task(db, make_item(db))
    client = runner()
    assert publish(db, task, client) == "skipped: outside task hours"
    client.group_publish.assert_not_called()
    assert task.state == "planned"


# ── REVIEW-B fixes: calibration per phone, enqueue dedupe, preflight, probe outside the lock ─────────────────────────


def _second_phone(db):
    device = PhoneDevice(id=2, name="Phone 2", adb_serial="phone-2", status="available")
    db.add(device)
    db.commit()
    return device


def test_calibration_skipped_when_phone_changed_during_rehearsal(db):
    delivery = seed(db, calibrated=False)
    make_item(db)
    _second_phone(db)
    client = runner()

    def owner_switches_phone(payload):
        db.execute(text("UPDATE fb_share_deliveries SET phone_device_id = 2, calibration = NULL "
                        "WHERE destination_id = 1"))
        db.commit()
        return calibration_result()

    client.group_calibrate.side_effect = owner_switches_phone
    assert phone_dispatch.calibrate_delivery(db, 1, client, NOW, cache=FakeCache()) == "failed"
    assert client.group_calibrate.call_args.args[0]["serial"] == "phone-1"
    db.refresh(delivery)
    assert delivery.phone_device_id == 2 and delivery.calibrated_at is None
    assert "fb_version" not in delivery.calibration
    assert delivery.calibration["last_attempt"]["detail"].startswith("phone changed during calibration")
    assert db.query(DeviceLease).count() == 0


def test_calibration_skipped_when_mode_changed_during_rehearsal(db):
    delivery = seed(db, calibrated=False)
    make_item(db)
    client = runner()

    def owner_switches_mode(payload):
        db.execute(text("UPDATE fb_share_deliveries SET mode = 'pic_whatsapp' WHERE destination_id = 1"))
        db.commit()
        return calibration_result()

    client.group_calibrate.side_effect = owner_switches_mode
    assert phone_dispatch.calibrate_delivery(db, 1, client, NOW, cache=FakeCache()) == "failed"
    db.refresh(delivery)
    assert delivery.calibrated_at is None and delivery.calibration["last_attempt"]["ok"] is False


def test_calibration_for_another_phone_is_not_sendable(db):
    delivery = seed(db)
    _second_phone(db)
    task = make_task(db, make_item(db))
    delivery.phone_device_id = 2  # calibration JSON still says phone 1
    db.commit()
    enqueue = Mock()
    assert phone_dispatch.dispatch_phone_due(db, NOW, runner(), enqueue=enqueue) == 0
    enqueue.assert_not_called()
    client = runner()
    assert publish(db, task, client) == "skipped: not calibrated on this phone"
    client.probe_fb_version.assert_not_called()
    delivery.calibration = {"fb_version": "500.0"}  # older calibration without a phone id
    delivery.phone_device_id = 1
    db.commit()
    assert publish(db, task, client) == "skipped: not calibrated on this phone"
    client.group_publish.assert_not_called()


def test_enqueue_dedupe_until_task_finishes(db, default_cache):
    seed(db)
    task = make_task(db, make_item(db))
    enqueue = Mock()
    assert phone_dispatch.dispatch_phone_due(db, NOW, runner(), enqueue=enqueue) == 1
    assert phone_dispatch.dispatch_phone_due(db, NOW, runner(), enqueue=enqueue) == 0
    assert enqueue.call_count == 1
    assert default_cache.values == {f"fb_share:phone_enqueued:{task.id}": "1"}
    client = runner("busy")
    assert phone_dispatch.publish_task(db, task.id, client, NOW, cache=default_cache) == "planned"
    assert f"fb_share:phone_enqueued:{task.id}" not in default_cache.values
    assert phone_dispatch.dispatch_phone_due(db, NOW, runner(), enqueue=enqueue) == 1


def test_enqueue_failure_clears_mark_and_redis_error_still_enqueues(db, default_cache):
    seed(db)
    task = make_task(db, make_item(db))
    assert phone_dispatch.dispatch_phone_due(db, NOW, runner(), enqueue=Mock(side_effect=RuntimeError("broker"))) == 0
    assert default_cache.values == {}
    broken = Mock(set=Mock(side_effect=ConnectionError("redis down")))
    enqueue = Mock()
    assert phone_dispatch.dispatch_phone_due(db, NOW, runner(), enqueue=enqueue, cache=broken) == 1
    enqueue.assert_called_once_with(task.id)


def test_missing_actor_id_fails_without_retry(db):
    seed(db)
    db.get(SocialDestination, 1).facebook_actor_id = " "
    db.commit()
    task = make_task(db, make_item(db))
    client = runner()
    assert publish(db, task, client) == "failed"
    assert task.state == "failed" and task.next_retry_at is None and "actor id" in task.error
    client.probe_fb_version.assert_not_called()
    client.group_publish.assert_not_called()


@pytest.mark.parametrize("status", ["removed", "not_member", None])
def test_membership_no_longer_joined_expires_task(db, status):
    seed(db)
    task = make_task(db, make_item(db))
    membership = db.query(FbGroupMembership).filter_by(destination_id=1, group_id=1).one()
    if status is None:
        db.delete(membership)
    else:
        membership.status = status
    db.commit()
    client = runner()
    assert publish(db, task, client) == "expired"
    assert task.state == "expired" and "no longer a joined member" in task.error
    client.probe_fb_version.assert_not_called()
    client.group_publish.assert_not_called()


def test_task_changed_during_probe_is_not_posted(db):
    seed(db)
    task = make_task(db, make_item(db))
    client = runner()

    def probe_while_task_changes(device):
        db.execute(text("UPDATE fb_share_tasks SET state = 'uncertain' WHERE id = :id"), {"id": task.id})
        db.commit()
        return "500.0", "123"

    client.probe_fb_version.side_effect = probe_while_task_changes
    assert publish(db, task, client) == "not_planned"
    client.group_publish.assert_not_called()
    assert db.query(DeviceLease).count() == 0
