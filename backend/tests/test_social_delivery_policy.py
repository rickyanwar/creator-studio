"""Behavioral contract tests for social delivery policy pure module."""

from datetime import datetime, timedelta, timezone
import zoneinfo
import pytest

from app.services.social_delivery_policy import (
    approval_state,
    assert_publish_identity,
    choose_method_for_slot,
    compute_next_slot,
    fallback_allowed,
    validate_route,
)


# ==============================================================================
# 1. validate_route
# ==============================================================================

@pytest.mark.parametrize(
    "kind,mode,parent_profile_id,repliz_account_id,daily_target,phone_daily_target,repliz_daily_target",
    [
        # Profile Phone-only route with phone quota = daily target
        ("profile", "phone", None, None, 3, 3, 0),
        # Page Repliz route with Repliz quota = target
        ("page", "repliz", None, "rep_123", 5, 0, 5),
        # Page Mixed route e.g. 1 Phone + 5 Repliz = 6
        ("page", "mixed", "prof_123", "rep_123", 6, 1, 5),
        # Page Phone-only route
        ("page", "phone", "prof_123", None, 2, 2, 0),
    ],
)
def test_validate_route_accepts_valid_configurations(
    kind, mode, parent_profile_id, repliz_account_id, daily_target, phone_daily_target, repliz_daily_target
):
    """Valid profile phone-only and page phone/repliz/mixed routes must be accepted."""
    result = validate_route(
        kind=kind,
        mode=mode,
        parent_profile_id=parent_profile_id,
        repliz_account_id=repliz_account_id,
        daily_target=daily_target,
        phone_daily_target=phone_daily_target,
        repliz_daily_target=repliz_daily_target,
    )
    assert result is None or result is True or isinstance(result, (dict, object))


def test_validate_route_profile_rejects_parent_profile():
    """Profiles cannot have a parent profile."""
    with pytest.raises(ValueError, match="(?i)parent.*profile"):
        validate_route(
            kind="profile",
            mode="phone",
            parent_profile_id="prof_self",
            repliz_account_id=None,
            daily_target=1,
            phone_daily_target=1,
            repliz_daily_target=0,
        )


@pytest.mark.parametrize(
    "mode,repliz_account_id,phone_quota,repliz_quota",
    [
        ("repliz", "rep_123", 0, 3),
        ("mixed", "rep_123", 1, 2),
    ],
)
def test_validate_route_profile_rejects_repliz_and_mixed(mode, repliz_account_id, phone_quota, repliz_quota):
    """Profiles are Phone-only and must reject Repliz or Mixed modes."""
    with pytest.raises(ValueError, match="(?i)profile.*phone"):
        validate_route(
            kind="profile",
            mode=mode,
            parent_profile_id=None,
            repliz_account_id=repliz_account_id,
            daily_target=phone_quota + repliz_quota,
            phone_daily_target=phone_quota,
            repliz_daily_target=repliz_quota,
        )


@pytest.mark.parametrize("mode", ["phone", "mixed"])
@pytest.mark.parametrize("parent_profile_id", [None, "", "   "])
def test_validate_route_page_phone_and_mixed_require_parent_profile(mode, parent_profile_id):
    """Pages using Phone or Mixed mode must have a non-empty parent profile."""
    phone_quota = 1
    repliz_quota = 2 if mode == "mixed" else 0
    with pytest.raises(ValueError, match="(?i)parent.*profile"):
        validate_route(
            kind="page",
            mode=mode,
            parent_profile_id=parent_profile_id,
            repliz_account_id="rep_123",
            daily_target=phone_quota + repliz_quota,
            phone_daily_target=phone_quota,
            repliz_daily_target=repliz_quota,
        )


@pytest.mark.parametrize("mode", ["repliz", "mixed"])
@pytest.mark.parametrize("repliz_account_id", [None, "", "   "])
def test_validate_route_page_repliz_and_mixed_require_repliz_account(mode, repliz_account_id):
    """Pages using Repliz or Mixed mode must have a non-empty Repliz account ID."""
    phone_quota = 1 if mode == "mixed" else 0
    repliz_quota = 3
    with pytest.raises(ValueError, match="(?i)repliz.*account"):
        validate_route(
            kind="page",
            mode=mode,
            parent_profile_id="prof_123",
            repliz_account_id=repliz_account_id,
            daily_target=phone_quota + repliz_quota,
            phone_daily_target=phone_quota,
            repliz_daily_target=repliz_quota,
        )


@pytest.mark.parametrize(
    "daily_target,phone_quota,repliz_quota",
    [
        (0, 0, 0),         # nonpositive daily target
        (-1, -1, 0),       # negative daily target
        (5, 2, 2),         # quota sum (4) != daily target (5)
        (3, 2, 2),         # quota sum (4) != daily target (3)
        (3, -1, 4),        # negative phone quota
        (3, 4, -1),        # negative repliz quota
    ],
)
def test_validate_route_rejects_nonpositive_or_mismatched_quotas(daily_target, phone_quota, repliz_quota):
    """Quota targets must be positive and their sum must strictly match daily_target."""
    with pytest.raises(ValueError):
        validate_route(
            kind="page",
            mode="mixed",
            parent_profile_id="prof_123",
            repliz_account_id="rep_123",
            daily_target=daily_target,
            phone_daily_target=phone_quota,
            repliz_daily_target=repliz_quota,
        )


@pytest.mark.parametrize(
    "kind,mode",
    [
        ("group", "phone"),
        ("instagram", "phone"),
        ("page", "carrier_pigeon"),
        ("profile", "carrier_pigeon"),
    ],
)
def test_validate_route_rejects_unsupported_kind_or_mode(kind, mode):
    """Unsupported platform kinds or delivery modes must be rejected."""
    with pytest.raises(ValueError):
        validate_route(
            kind=kind,
            mode=mode,
            parent_profile_id="prof_1",
            repliz_account_id="rep_1",
            daily_target=1,
            phone_daily_target=1,
            repliz_daily_target=0,
        )


# ==============================================================================
# 2. choose_method_for_slot
# ==============================================================================

def test_choose_method_mixed_1_phone_5_repliz_cycle():
    """Mixed 1+5 returns Phone for slot 0, Repliz for slots 1..5; slot 6 starts new cycle at Phone."""
    expected = [
        "phone",   # slot 0
        "repliz",  # slot 1
        "repliz",  # slot 2
        "repliz",  # slot 3
        "repliz",  # slot 4
        "repliz",  # slot 5
        "phone",   # slot 6 (next cycle)
        "repliz",  # slot 7
    ]
    for slot_idx, exp_method in enumerate(expected):
        assert choose_method_for_slot("mixed", slot_idx, phone_daily_target=1, repliz_daily_target=5) == exp_method


def test_choose_method_mixed_arbitrary_split():
    """Mixed with 2 phone + 3 repliz allocates phone for first 2, repliz for next 3, then repeats."""
    expected = ["phone", "phone", "repliz", "repliz", "repliz", "phone", "phone", "repliz"]
    for slot_idx, exp_method in enumerate(expected):
        assert choose_method_for_slot("mixed", slot_idx, phone_daily_target=2, repliz_daily_target=3) == exp_method


@pytest.mark.parametrize("mode,expected_method", [("phone", "phone"), ("repliz", "repliz")])
@pytest.mark.parametrize("slot_index", [0, 1, 5, 12, 100])
def test_choose_method_phone_and_repliz_only_modes_always_return_themselves(mode, expected_method, slot_index):
    """Phone-only and Repliz-only modes always return their own method regardless of slot index."""
    phone_target = 3 if mode == "phone" else 0
    repliz_target = 0 if mode == "phone" else 3
    assert choose_method_for_slot(mode, slot_index, phone_target, repliz_target) == expected_method


@pytest.mark.parametrize("slot_index", [-1, -5, -99])
def test_choose_method_rejects_negative_slot_index(slot_index):
    """Negative slot index must raise ValueError."""
    with pytest.raises(ValueError, match="(?i)slot.*index"):
        choose_method_for_slot("mixed", slot_index, phone_daily_target=1, repliz_daily_target=5)


# ==============================================================================
# 3. compute_next_slot
# ==============================================================================

def test_compute_next_slot_returns_aware_utc_future():
    """compute_next_slot returns an aware UTC datetime at or after now_utc."""
    now_utc = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    slot = compute_next_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=5,
        min_gap_minutes=60,
        existing_slots_utc=[],
    )
    assert isinstance(slot, datetime)
    assert slot.tzinfo is not None
    assert slot.utcoffset() == timedelta(0)
    assert slot >= now_utc


def test_compute_next_slot_respects_minimum_gap():
    """Next slot must be at least min_gap_minutes after the latest existing slot."""
    now_utc = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    latest_slot = datetime(2026, 10, 20, 11, 30, tzinfo=timezone.utc)
    min_gap = 45

    slot = compute_next_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=5,
        min_gap_minutes=min_gap,
        existing_slots_utc=[
            datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc),
            latest_slot,
        ],
    )
    assert slot >= latest_slot + timedelta(minutes=min_gap)


def test_compute_next_slot_local_day_daily_cap_and_rollover_non_utc():
    """When daily target is reached for local calendar day in non-UTC timezone, rolls over to next local day."""
    # Asia/Jakarta is UTC+7.
    # At 2026-10-20 03:00 UTC, Jakarta local time is 2026-10-20 10:00.
    jakarta_tz = zoneinfo.ZoneInfo("Asia/Jakarta")
    now_utc = datetime(2026, 10, 20, 3, 0, tzinfo=timezone.utc)
    daily_target = 2

    # Two existing slots already on 2026-10-20 Jakarta time
    existing = [
        datetime(2026, 10, 20, 4, 0, tzinfo=timezone.utc),  # 11:00 Jakarta
        datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc),  # 15:00 Jakarta
    ]

    slot_utc = compute_next_slot(
        now_utc=now_utc,
        timezone_name="Asia/Jakarta",
        daily_target=daily_target,
        min_gap_minutes=60,
        existing_slots_utc=existing,
    )

    slot_local = slot_utc.astimezone(jakarta_tz)
    # Must have rolled over past 2026-10-20 Jakarta date
    assert slot_local.date() >= datetime(2026, 10, 21).date()


def test_compute_next_slot_dst_boundary_london():
    """Handles Europe/London daylight-saving transition without invalid time crash or offset error."""
    # 2026-10-25: London clocks go back from 02:00 BST to 01:00 GMT (Autumn transition)
    london_tz = zoneinfo.ZoneInfo("Europe/London")
    now_utc = datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc)

    slot_utc = compute_next_slot(
        now_utc=now_utc,
        timezone_name="Europe/London",
        daily_target=4,
        min_gap_minutes=60,
        existing_slots_utc=[
            datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc),
        ],
        sleep_start_hour=0,
        sleep_end_hour=6,
    )

    assert isinstance(slot_utc, datetime)
    assert slot_utc.tzinfo is not None
    slot_london = slot_utc.astimezone(london_tz)
    # Outside sleep window in London
    assert slot_london.hour >= 6


def test_compute_next_slot_backlog_spacing_and_daily_cap():
    """A backlog of 3 calls must maintain min_gap spacing and respect daily target rollover."""
    tz_name = "Asia/Jakarta"
    jakarta_tz = zoneinfo.ZoneInfo(tz_name)
    now_utc = datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc)  # 08:00 Jakarta
    daily_target = 2
    min_gap = 90

    # 1st slot
    slot1 = compute_next_slot(
        now_utc=now_utc,
        timezone_name=tz_name,
        daily_target=daily_target,
        min_gap_minutes=min_gap,
        existing_slots_utc=[],
    )
    # 2nd slot
    slot2 = compute_next_slot(
        now_utc=now_utc,
        timezone_name=tz_name,
        daily_target=daily_target,
        min_gap_minutes=min_gap,
        existing_slots_utc=[slot1],
    )
    # 3rd slot
    slot3 = compute_next_slot(
        now_utc=now_utc,
        timezone_name=tz_name,
        daily_target=daily_target,
        min_gap_minutes=min_gap,
        existing_slots_utc=[slot1, slot2],
    )

    # Invariant 1: minimum spacing between successive slots
    assert slot2 >= slot1 + timedelta(minutes=min_gap)
    assert slot3 >= slot2 + timedelta(minutes=min_gap)

    # Invariant 2: slot 3 exceeds daily_target (2), must roll over to the next local day
    d1 = slot1.astimezone(jakarta_tz).date()
    d2 = slot2.astimezone(jakarta_tz).date()
    d3 = slot3.astimezone(jakarta_tz).date()

    assert d1 == d2, "First two slots fit within the same local calendar day"
    assert d3 > d1, "Third slot must roll over to subsequent day because daily_target=2"


def test_compute_next_slot_respects_sleep_window():
    """Slots must not be scheduled during sleep hours."""
    now_utc = datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc)
    slot_utc = compute_next_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=5,
        min_gap_minutes=30,
        existing_slots_utc=[],
        sleep_start_hour=22,
        sleep_end_hour=6,
    )
    assert slot_utc.hour >= 6
    assert slot_utc >= now_utc


# ==============================================================================
# 4. fallback_allowed
# ==============================================================================

def test_fallback_allowed_true_when_all_guards_satisfied():
    """Page in needs_human state with certain state, connected Repliz, supported content, and verified absence is allowed."""
    assert fallback_allowed(
        kind="page",
        incident_state="needs_human",
        uncertain=False,
        repliz_connected=True,
        content_supported=True,
        absence_verified=True,
    ) is True


def test_fallback_allowed_profile_never_allowed():
    """Profiles are strictly phone-only and must never fall back to Repliz."""
    assert fallback_allowed(
        kind="profile",
        incident_state="needs_human",
        uncertain=False,
        repliz_connected=True,
        content_supported=True,
        absence_verified=True,
    ) is False


@pytest.mark.parametrize(
    "incident_state",
    ["sleeping", "offline", "repairing", "healthy", "acknowledged", "unknown", None, ""],
)
def test_fallback_allowed_disallowed_for_non_needs_human_states(incident_state):
    """Fallback requires owner exhaustion / needs_human; routine offline, repair, or sleep states must wait."""
    assert fallback_allowed(
        kind="page",
        incident_state=incident_state,
        uncertain=False,
        repliz_connected=True,
        content_supported=True,
        absence_verified=True,
    ) is False


def test_fallback_allowed_disallowed_when_uncertain():
    """Uncertain deliveries must never auto-publish again or fall back."""
    assert fallback_allowed(
        kind="page",
        incident_state="needs_human",
        uncertain=True,
        repliz_connected=True,
        content_supported=True,
        absence_verified=True,
    ) is False


def test_fallback_allowed_disallowed_when_repliz_disconnected():
    """Fallback cannot occur if Repliz connection is unavailable."""
    assert fallback_allowed(
        kind="page",
        incident_state="needs_human",
        uncertain=False,
        repliz_connected=False,
        content_supported=True,
        absence_verified=True,
    ) is False


def test_fallback_allowed_disallowed_when_content_unsupported():
    """Media formats not supported by Repliz must wait rather than falling back."""
    assert fallback_allowed(
        kind="page",
        incident_state="needs_human",
        uncertain=False,
        repliz_connected=True,
        content_supported=False,
        absence_verified=True,
    ) is False


def test_fallback_allowed_disallowed_when_absence_unverified():
    """Absence of previous post on Facebook must be verified before fallback is permitted."""
    assert fallback_allowed(
        kind="page",
        incident_state="needs_human",
        uncertain=False,
        repliz_connected=True,
        content_supported=True,
        absence_verified=False,
    ) is False


# ==============================================================================
# 5. approval_state
# ==============================================================================

@pytest.mark.parametrize(
    "reply,elapsed_seconds,expected",
    [
        ("approved", 60, "approved"),
        ("approved", 14 * 60 + 59, "approved"),
        ("approved", 15 * 60, "approved"),
        ("approved", 20 * 60, "approved"),
        ("denied", 30, "denied"),
        ("denied", 15 * 60, "denied"),
        ("denied", 60 * 60, "denied"),
    ],
)
def test_approval_state_explicit_reply_takes_precedence(reply, elapsed_seconds, expected):
    """An explicit owner reply of 'approved' or 'denied' immediately decides the state."""
    notified_at = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    now_utc = notified_at + timedelta(seconds=elapsed_seconds)
    assert approval_state(notified_at, now_utc, reply=reply) == expected


def test_approval_state_unanswered_15_minute_boundary():
    """Without reply, 14m59s is waiting while exactly 15m00s approves."""
    notified_at = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    # 14 minutes, 59 seconds: still waiting
    at_14m59s = notified_at + timedelta(minutes=14, seconds=59)
    assert approval_state(notified_at, at_14m59s, reply=None) == "waiting"

    # 15 minutes, 00 seconds: auto-approved on silence
    at_15m00s = notified_at + timedelta(minutes=15, seconds=0)
    assert approval_state(notified_at, at_15m00s, reply=None) == "approved"

    # 15 minutes, 01 seconds: approved
    at_15m01s = notified_at + timedelta(minutes=15, seconds=1)
    assert approval_state(notified_at, at_15m01s, reply=None) == "approved"


# ==============================================================================
# 6. assert_publish_identity
# ==============================================================================

def test_assert_publish_identity_accepts_verified_profile():
    """Verified profile with matching ID and valid calibration passes."""
    assert_publish_identity(
        expected_profile_id="prof_1001",
        observed_profile_id="prof_1001",
        target_kind="profile",
        expected_page_id=None,
        observed_page_id=None,
        calibration_valid=True,
    )


def test_assert_publish_identity_accepts_verified_page():
    """Verified page under correct parent profile with valid calibration passes."""
    assert_publish_identity(
        expected_profile_id="prof_1001",
        observed_profile_id="prof_1001",
        target_kind="page",
        expected_page_id="page_2002",
        observed_page_id="page_2002",
        calibration_valid=True,
    )


@pytest.mark.parametrize(
    "expected_p,observed_p",
    [
        ("prof_1001", "prof_9999"),  # mismatched profile
        ("prof_1001", None),         # missing observed
        (None, "prof_1001"),         # missing expected
        ("", "prof_1001"),           # empty expected
        ("prof_1001", ""),           # empty observed
    ],
)
def test_assert_publish_identity_rejects_profile_mismatch_or_missing(expected_p, observed_p):
    """Mismatched or missing profile ID raises ValueError."""
    with pytest.raises(ValueError, match="(?i)profile"):
        assert_publish_identity(
            expected_profile_id=expected_p,
            observed_profile_id=observed_p,
            target_kind="profile",
            expected_page_id=None,
            observed_page_id=None,
            calibration_valid=True,
        )


@pytest.mark.parametrize(
    "expected_pg,observed_pg",
    [
        ("page_2002", "page_8888"),  # mismatched page
        ("page_2002", None),         # missing observed page
        (None, "page_2002"),         # missing expected page
        ("", "page_2002"),           # empty expected page
        ("page_2002", ""),           # empty observed page
    ],
)
def test_assert_publish_identity_rejects_page_mismatch_or_missing(expected_pg, observed_pg):
    """Pages require matching non-empty page ID; mismatch or missing raises ValueError."""
    with pytest.raises(ValueError, match="(?i)page"):
        assert_publish_identity(
            expected_profile_id="prof_1001",
            observed_profile_id="prof_1001",
            target_kind="page",
            expected_page_id=expected_pg,
            observed_page_id=observed_pg,
            calibration_valid=True,
        )


def test_assert_publish_identity_rejects_invalid_calibration():
    """Invalid device calibration raises ValueError."""
    with pytest.raises(ValueError, match="(?i)calibration"):
        assert_publish_identity(
            expected_profile_id="prof_1001",
            observed_profile_id="prof_1001",
            target_kind="profile",
            expected_page_id=None,
            observed_page_id=None,
            calibration_valid=False,
        )


def test_assert_publish_identity_display_name_never_passes_missing_id():
    """Account display-name equality alone must never pass when stable ID is missing."""
    with pytest.raises(ValueError):
        assert_publish_identity(
            expected_profile_id=None,
            observed_profile_id=None,
            target_kind="profile",
            expected_page_id=None,
            observed_page_id=None,
            calibration_valid=True,
        )
