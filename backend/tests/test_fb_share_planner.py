"""Planner and PIC dispatch regression checks with isolated SQLite storage."""

import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.models.fb_group_share import FbGroup, FbGroupMembership, FbShareBrandSettings, FbShareDelivery, FbShareItem, FbShareTask
from app.models.social_publishing import SocialDestination
from app.models.target_fanpages import TargetFanpage
from app.services.fb_share import planner
from app.services.fb_share.brand_settings import set_brand_share
from app.services.fb_share.pic_messages import ONBOARDING_TEXT
from backend.tests.test_fb_group_share_models import db_session

NOW = datetime(2026, 11, 8, 10)


def seed(db, groups=3, destination_id=1, **delivery_values):
    brand = db.get(TargetFanpage, 1)
    set_brand_share(db, brand.id, True, {})
    db.get(SocialDestination, destination_id).timezone = "UTC"
    delivery = FbShareDelivery(destination_id=destination_id, mode="pic_whatsapp", is_active=True,
                               pic_contact="+62 (812) 345", created_at=NOW, **delivery_values)
    db.add(delivery)
    for number in range(1, groups + 1):
        db.add(FbGroup(id=number, niche="f1", name=f"Group {number}", kind="general",
                       url=f"https://facebook.com/groups/{number}", daily_cap=10))
    db.flush()
    for number in range(1, groups + 1):
        db.add(FbGroupMembership(destination_id=destination_id, group_id=number, status="joined"))
    db.commit()
    return brand, delivery


def item(db, ref="first", **values):
    row = FbShareItem(fanpage_id=1, niche="f1", source_kind="manual", source_ref=ref,
                      media_kind="photo", media=["https://example.org/photo.jpg"], sentiment={},
                      caption="Caption", created_at=NOW, **values)
    db.add(row)
    db.commit()
    return row


def task(db, row, group_id=1, destination_id=1, **values):
    entry = FbShareTask(item_id=row.id, group_id=group_id, destination_id=destination_id,
                        delivery_mode="pic_whatsapp", scheduled_for=NOW, **values)
    db.add(entry)
    db.commit()
    return entry


@pytest.mark.parametrize("weeks,expected", [(0, 3), (1, 5), (2, 5)])
def test_caps_ramp_and_slots(db_session, weeks, expected):
    brand, delivery = seed(db_session, groups=8)
    delivery.created_at = NOW - timedelta(weeks=weeks)
    row = item(db_session)
    assert planner.plan_brand(db_session, brand, NOW.replace(tzinfo=timezone.utc), random.Random(1)) == expected
    tasks = db_session.query(FbShareTask).order_by(FbShareTask.id).all()
    assert row.status == "routed"
    assert all(b.scheduled_for - a.scheduled_for >= timedelta(minutes=30) for a, b in zip(tasks, tasks[1:]))
    assert planner.plan_brand(db_session, brand, NOW, random.Random(1)) == 0


def test_account_override_and_one_account(db_session):
    brand, _ = seed(db_session, groups=8, start_per_day=2, weekly_increase=0, max_per_day=2)
    db_session.add(FbShareDelivery(destination_id=2, mode="pic_telegram", is_active=True, created_at=NOW))
    db_session.add_all([FbGroupMembership(destination_id=2, group_id=n, status="joined") for n in range(1, 9)])
    item(db_session)
    assert planner.plan_brand(db_session, brand, NOW, random.Random(0)) == 2
    assert {t.destination_id for t in db_session.query(FbShareTask)} == {1}


def test_max_cap_and_same_tick_group_limit(db_session):
    brand, delivery = seed(db_session, groups=8)
    delivery.created_at = NOW - timedelta(weeks=20)
    delivery.max_per_day = 2
    item(db_session)
    second = item(db_session, "second")
    assert planner.plan_brand(db_session, brand, NOW, random.Random(0)) == 2
    assert second.status == "new"
    assert len({t.group_id for t in db_session.query(FbShareTask)}) == 2


def test_same_tick_group_limit(db_session):
    brand, _ = seed(db_session, groups=1)
    item(db_session)
    second = item(db_session, "second")
    assert planner.plan_brand(db_session, brand, NOW, random.Random(0)) == 1
    assert second.status == "new"


def test_group_daily_cap_across_accounts(db_session):
    brand, delivery = seed(db_session, groups=2)
    previous = item(db_session, "previous", status="routed")
    task(db_session, previous, destination_id=2)
    delivery.start_per_day = 1
    current = item(db_session)
    assert planner.plan_brand(db_session, brand, NOW, random.Random(0)) == 1
    assert db_session.query(FbShareTask).filter_by(item_id=current.id).one().group_id == 2


@pytest.mark.parametrize("kind,expected", [("facebook_page", 0), ("facebook_profile", 1)])
def test_page_and_profile_identity(db_session, kind, expected):
    brand, _ = seed(db_session, groups=1)
    db_session.get(SocialDestination, 1).kind = kind
    db_session.get(FbGroup, 1).posting_identity = "profile_only"
    current = item(db_session)
    assert planner.plan_brand(db_session, brand, NOW, random.Random(0)) == expected
    assert current.status == ("routed" if expected else "new")


@pytest.mark.parametrize("state,expected", [("published", 0), ("uncertain", 0), ("planned", 0),
                                                ("expired", 1), ("failed", 1), ("not_member", 1)])
@pytest.mark.parametrize("match", ["phash", "source_ref"])
def test_brand_history_dedupe(db_session, state, expected, match):
    brand, _ = seed(db_session, groups=1)
    old = item(db_session, "old", status="routed", phash="0000000000000000")
    previous = task(db_session, old, destination_id=2, state=state)
    previous.scheduled_for = NOW - timedelta(days=1)
    current = item(db_session, phash="0000000000000001" if match == "phash" else "ffffffffffffffff")
    if match == "source_ref":
        old.source_ref = current.source_ref
        old.source_kind = "muse"
    assert planner.plan_brand(db_session, brand, NOW, random.Random(0)) == expected


def test_other_brand_history_does_not_dedupe(db_session):
    brand, _ = seed(db_session, groups=1)
    old = item(db_session, "old", status="routed", phash="0000000000000000")
    old.fanpage_id = 2
    previous = task(db_session, old, destination_id=3, state="published")
    previous.scheduled_for = NOW - timedelta(days=1)
    item(db_session, phash="0000000000000000")
    assert planner.plan_brand(db_session, brand, NOW, random.Random(0)) == 1


@pytest.mark.parametrize("values", [{"is_active": False}, {"paused_until": NOW},
                                      {"paused_until": NOW + timedelta(hours=1)},
                                      {"mode": None}, {"mode": "phone_auto"}])
def test_ineligible_accounts(db_session, values):
    brand, delivery = seed(db_session, groups=1)
    for key, value in values.items():
        setattr(delivery, key, value)
    current = item(db_session)
    assert planner.plan_brand(db_session, brand, NOW, random.Random(0)) == 0
    assert current.status == "new"


def test_phone_ramp_uses_calibration_date(db_session):
    brand, delivery = seed(db_session, groups=8)
    delivery.mode = "phone_auto"
    delivery.created_at = NOW - timedelta(weeks=10)
    delivery.calibrated_at = NOW
    item(db_session)
    assert planner.plan_brand(db_session, brand, NOW, random.Random(0)) == 3


def test_expiry_unclassified_and_disabled_brand(db_session):
    brand, _ = seed(db_session, groups=1)
    expired = item(db_session, "expired", expires_at=NOW - timedelta(seconds=1))
    unclassified = item(db_session)
    unclassified.sentiment = None
    assert planner.plan_brand(db_session, brand, NOW, random.Random(0)) == 0
    assert expired.status == "expired" and unclassified.status == "new"
    unclassified.sentiment = {}
    set_brand_share(db_session, brand.id, False, {})
    assert planner.plan_brand(db_session, brand, NOW, random.Random(0)) == 0


def test_existing_destination_slots_and_daily_count(db_session):
    brand, delivery = seed(db_session, groups=8)
    old = item(db_session, "old", status="routed")
    previous = task(db_session, old, group_id=8, state="published")
    previous.scheduled_for = NOW + timedelta(hours=1)
    item(db_session)
    assert planner.plan_brand(db_session, brand, NOW, random.Random(0)) == 2
    assert all(t.scheduled_for >= previous.scheduled_for + timedelta(minutes=30)
               for t in db_session.query(FbShareTask).filter(FbShareTask.id != previous.id))
    assert previous.state == "published"


@pytest.fixture
def dispatch(monkeypatch):
    monkeypatch.setenv("FB_SHARE_DISPATCH_ENABLED", "1")
    return Mock(send_pic_messages=Mock(return_value=(True, "sent")))


def test_dispatch_kill_switch(db_session, monkeypatch, dispatch):
    brand, _ = seed(db_session)
    current = item(db_session, expires_at=NOW - timedelta(seconds=1))
    entry = task(db_session, current)
    monkeypatch.delenv("FB_SHARE_DISPATCH_ENABLED")
    assert planner.dispatch_due(db_session, NOW, dispatch) == 0
    dispatch.send_pic_messages.assert_not_called()
    assert entry.state == "planned"


def test_bundle_numbering_onboarding_media_and_no_resend(db_session, dispatch, monkeypatch):
    _, delivery = seed(db_session)
    current = item(db_session)
    current.media = ["/storage/designs/photo.jpg"]
    monkeypatch.setattr(planner, "get_settings", lambda: SimpleNamespace(
        storage_base_path="/storage", storage_base_url="https://example.org/media"))
    first = task(db_session, current, group_id=3)
    second = task(db_session, current, group_id=1)
    second.scheduled_for = NOW + timedelta(hours=1)
    assert planner.dispatch_due(db_session, NOW, dispatch) == 1
    target, messages = dispatch.send_pic_messages.call_args.args
    assert target == "whatsapp:62812345"
    assert messages[0] == {"kind": "text", "text": ONBOARDING_TEXT}
    assert messages[2] == {"kind": "media", "media_url": "https://example.org/media/designs/photo.jpg"}
    assert messages[-1]["text"] == "1. https://facebook.com/groups/3\n2. https://facebook.com/groups/1"
    assert first.pic_message_ref == second.pic_message_ref == f"pic-{current.id}-1"
    assert first.state == second.state == "sent_to_pic"
    assert planner.dispatch_due(db_session, NOW, dispatch) == 0
    next_item = item(db_session, "next")
    task(db_session, next_item, group_id=2)
    assert planner.dispatch_due(db_session, NOW, dispatch) == 1
    assert dispatch.send_pic_messages.call_args.args[1][0]["text"] == "Profile"
    assert dispatch.send_pic_messages.call_count == 2


@pytest.mark.parametrize("zone,hour,expected", [("Asia/Jakarta", 1, 1), ("Asia/Jakarta", 14, 0),
                                                   ("UTC", 1, 0)])
def test_task_hours_timezone(db_session, dispatch, zone, hour, expected):
    seed(db_session, groups=1)
    db_session.get(SocialDestination, 1).timezone = zone
    current = item(db_session)
    entry = task(db_session, current)
    entry.scheduled_for = NOW.replace(hour=0)
    assert planner.dispatch_due(db_session, NOW.replace(hour=hour), dispatch) == expected


def test_failure_retry_telegram_and_future_bundle(db_session, dispatch):
    _, delivery = seed(db_session, groups=2)
    delivery.mode = "pic_telegram"
    delivery.pic_contact = "@operator"
    current = item(db_session)
    entry = task(db_session, current)
    entry.delivery_mode = "pic_telegram"
    dispatch.send_pic_messages.return_value = False, "x" * 600
    assert planner.dispatch_due(db_session, NOW, dispatch) == 0
    assert entry.state == "planned" and entry.error == "x" * 500
    assert dispatch.send_pic_messages.call_args.args[0] == "telegram:@operator"
    dispatch.send_pic_messages.return_value = True, "sent"
    assert planner.dispatch_due(db_session, NOW, dispatch) == 0
    assert planner.dispatch_due(db_session, NOW + timedelta(minutes=15), dispatch) == 1
    assert entry.error is None
    future = item(db_session, "future")
    future_task = task(db_session, future, group_id=2)
    future_task.scheduled_for = NOW + timedelta(minutes=1)
    assert planner.dispatch_due(db_session, NOW, dispatch) == 0


def test_bundles_separate_destinations_and_items(db_session, dispatch):
    seed(db_session, groups=3)
    db_session.add(FbShareDelivery(destination_id=2, mode="pic_whatsapp", is_active=True,
                                   pic_contact="123", created_at=NOW))
    db_session.get(SocialDestination, 2).timezone = "UTC"
    current = item(db_session)
    task(db_session, current, group_id=1)
    task(db_session, current, group_id=2, destination_id=2)
    other = item(db_session, "other")
    task(db_session, other, group_id=3)
    assert planner.dispatch_due(db_session, NOW, dispatch) == 3
    assert dispatch.send_pic_messages.call_count == 3
    for call in dispatch.send_pic_messages.call_args_list:
        assert [message["media_url"] for message in call.args[1] if message["kind"] == "media"] == [
            "https://example.org/photo.jpg"]


def test_dispatch_expiry_and_phone_left_planned(db_session, dispatch):
    seed(db_session, groups=3)
    expired = item(db_session, "expired", expires_at=NOW - timedelta(seconds=1))
    planned = task(db_session, expired)
    sent = task(db_session, expired, group_id=2, state="sent_to_pic", pic_message_ref="old")
    published = task(db_session, expired, group_id=3, state="published")
    phone = item(db_session)
    phone_task = task(db_session, phone)
    phone_task.delivery_mode = "phone_auto"
    assert planner.dispatch_due(db_session, NOW, dispatch) == 0
    assert planned.state == sent.state == "expired"
    assert published.state == "published" and phone_task.state == "planned"
    dispatch.send_pic_messages.assert_not_called()


@pytest.mark.parametrize("setting", ["disabled_brand", "missing_settings", "inactive", "paused"])
def test_dispatch_requires_enabled_active_unpaused_brand(db_session, dispatch, setting):
    brand, delivery = seed(db_session, groups=1)
    entry = task(db_session, item(db_session))
    if setting == "disabled_brand":
        set_brand_share(db_session, brand.id, False, {})
    elif setting == "missing_settings":
        db_session.delete(db_session.get(FbShareBrandSettings, brand.id))
    elif setting == "inactive":
        delivery.is_active = False
    else:
        delivery.paused_until = NOW + timedelta(minutes=1)
    db_session.commit()
    assert planner.dispatch_due(db_session, NOW, dispatch) == 0
    assert entry.pic_message_ref is None
    dispatch.send_pic_messages.assert_not_called()


def test_bundle_claim_is_committed_before_send(db_session, dispatch):
    seed(db_session, groups=1)
    entry = task(db_session, item(db_session))

    def send(*args):
        db_session.expire_all()
        assert entry.state == "planned"
        assert entry.pic_message_ref == f"pic-{entry.item_id}-1"
        assert not db_session.dirty
        assert planner.dispatch_due(db_session, NOW, dispatch) == 0
        return True, "sent"

    dispatch.send_pic_messages.side_effect = send
    assert planner.dispatch_due(db_session, NOW, dispatch) == 1
    dispatch.send_pic_messages.assert_called_once()


@pytest.mark.parametrize("age,expected", [(29, "planned"), (31, "uncertain")])
def test_abandoned_claim_is_never_resent(db_session, dispatch, age, expected):
    seed(db_session, groups=1)
    current = item(db_session)
    entry = task(db_session, current, pic_message_ref=f"pic-{current.id}-1",
                 updated_at=NOW - timedelta(minutes=age))
    assert planner.dispatch_due(db_session, NOW, dispatch) == 0
    assert entry.state == expected
    dispatch.send_pic_messages.assert_not_called()


def test_retry_backoff_and_terminal_failure(db_session, dispatch):
    seed(db_session, groups=2)
    current = item(db_session)
    entries = [task(db_session, current, group_id=n) for n in (1, 2)]
    dispatch.send_pic_messages.return_value = False, "offline"
    now = NOW
    for attempt, delay in enumerate((15, 30, 60, 120), 1):
        assert planner.dispatch_due(db_session, now, dispatch) == 0
        for entry in entries:
            assert entry.attempts == attempt
            assert entry.next_retry_at == now + timedelta(minutes=delay)
            assert entry.pic_message_ref is None
            assert entry.state == ("failed" if attempt == 4 else "planned")
        assert dispatch.send_pic_messages.call_args.args[1][0]["text"] == ONBOARDING_TEXT
        assert planner.dispatch_due(db_session, now + timedelta(minutes=delay - 1), dispatch) == 0
        assert dispatch.send_pic_messages.call_count == attempt
        now += timedelta(minutes=delay)
    assert planner.dispatch_due(db_session, now, dispatch) == 0
    assert dispatch.send_pic_messages.call_count == 4


@pytest.mark.parametrize("state", ["planned", "failed", "uncertain"])
def test_unsuccessful_history_does_not_suppress_onboarding(db_session, dispatch, state):
    seed(db_session, groups=2)
    task(db_session, item(db_session, "old"), state=state, pic_message_ref="old")
    task(db_session, item(db_session, "new"), group_id=2)
    assert planner.dispatch_due(db_session, NOW, dispatch) == 1
    assert dispatch.send_pic_messages.call_args.args[1][0]["text"] == ONBOARDING_TEXT


@pytest.mark.parametrize("invalid", ["timezone", "start", "end"])
def test_bad_bundle_configuration_does_not_stop_next(db_session, dispatch, invalid, caplog):
    _, delivery = seed(db_session, groups=2)
    if invalid == "timezone":
        db_session.get(SocialDestination, 1).timezone = "Invalid/Timezone"
    else:
        setattr(delivery, f"task_hours_{invalid}", "25:99")
    db_session.add(FbShareDelivery(destination_id=2, mode="pic_whatsapp", is_active=True,
                                 pic_contact="123", created_at=NOW))
    db_session.get(SocialDestination, 2).timezone = "UTC"
    current = item(db_session)
    task(db_session, current)
    task(db_session, current, group_id=2, destination_id=2)
    assert planner.dispatch_due(db_session, NOW, dispatch) == 1
    assert dispatch.send_pic_messages.call_args.args[0] == "whatsapp:123"
    assert caplog.records and caplog.records[-1].levelname == "WARNING"


@pytest.mark.parametrize("zone,now,old_slot,expected", [
    ("Asia/Jakarta", datetime(2026, 11, 8, 18), datetime(2026, 11, 8, 16), 1),
    ("America/New_York", datetime(2026, 11, 8, 3), datetime(2026, 11, 7, 23), 0),
    ("Invalid/Timezone", NOW, NOW - timedelta(days=1), 1),
])
def test_caps_use_destination_local_day(db_session, zone, now, old_slot, expected):
    brand, delivery = seed(db_session, groups=1, start_per_day=1, weekly_increase=0, max_per_day=1)
    db_session.get(SocialDestination, 1).timezone = zone
    previous = task(db_session, item(db_session, "old"), state="published")
    previous.scheduled_for = old_slot
    item(db_session, "new")
    assert planner.plan_brand(db_session, brand, now, random.Random(0)) == expected


@pytest.mark.parametrize("zone,now,expected", [
    ("Asia/Jakarta", datetime(2026, 11, 8, 15), datetime(2026, 11, 9, 1)),
    ("Asia/Jakarta", datetime(2026, 11, 8, 0), datetime(2026, 11, 8, 1)),
    ("America/New_York", datetime(2026, 11, 8, 3), datetime(2026, 11, 8, 13)),
])
def test_slots_move_to_next_local_task_window(db_session, zone, now, expected):
    brand, _ = seed(db_session, groups=1)
    db_session.get(SocialDestination, 1).timezone = zone
    task_item = item(db_session)
    assert planner.plan_brand(db_session, brand, now, random.Random(0)) == 1
    assert db_session.query(FbShareTask).filter_by(item_id=task_item.id).one().scheduled_for == expected


@pytest.mark.parametrize("old_slot,expected", [
    (datetime(2026, 11, 8, 16), 1), (datetime(2026, 11, 8, 17, 30), 0),
])
def test_group_cap_across_accounts_uses_receiving_destination_day(db_session, old_slot, expected):
    brand, _ = seed(db_session, groups=1)
    db_session.get(SocialDestination, 1).timezone = "Asia/Jakarta"
    previous = task(db_session, item(db_session, "old"), destination_id=2, state="published")
    previous.scheduled_for = old_slot
    item(db_session, "new")
    assert planner.plan_brand(db_session, brand, datetime(2026, 11, 8, 18), random.Random(0)) == expected


def test_one_deferred_task_defers_entire_bundle(db_session, dispatch):
    seed(db_session, groups=2)
    current = item(db_session)
    task(db_session, current)
    task(db_session, current, group_id=2, next_retry_at=NOW + timedelta(minutes=15))
    assert planner.dispatch_due(db_session, NOW, dispatch) == 0
    dispatch.send_pic_messages.assert_not_called()
    assert planner.dispatch_due(db_session, NOW + timedelta(minutes=15), dispatch) == 1


@pytest.mark.parametrize("hour,expected", [(23, datetime(2026, 11, 8, 23)),
                                          (7, datetime(2026, 11, 8, 22))])
def test_overnight_task_window(db_session, hour, expected):
    brand, _ = seed(db_session, groups=1, task_hours_start="22:00", task_hours_end="06:00")
    item(db_session)
    assert planner.plan_brand(db_session, brand, NOW.replace(hour=hour), random.Random(0)) == 1
    assert db_session.query(FbShareTask).one().scheduled_for == expected
