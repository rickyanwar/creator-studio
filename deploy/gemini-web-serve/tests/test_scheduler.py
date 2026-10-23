"""Tests for scheduler.py — pure scheduling logic."""

from __future__ import annotations

import random
from datetime import date, timedelta

from scheduler import (
    DayPlan, SchedulerConfig, after_use, apply_error, is_available,
    next_ready_at, pick_account, plan_day, _local_day, _tz,
    _local_midnight_epoch,
)


def _epoch(day: date, hour: float = 0.0) -> float:
    """Helper: epoch for a given day+hour in Asia/Jakarta."""
    tz = _tz("Asia/Jakarta")
    mid = _local_midnight_epoch(day, tz)
    return mid + hour * 3600


_DAY = date(2026, 6, 15)
_CFG = SchedulerConfig()


class TestPlanDay4Accounts30Seeds:
    """4 accounts, 30 seeds: validate invariants."""

    NAMES = ["alpha", "bravo", "charlie", "delta"]

    def _run(self, seed: int):
        rng = random.Random(seed)
        plans = plan_day(self.NAMES, _DAY, _CFG, rng)
        assert set(plans.keys()) == set(self.NAMES)
        return plans

    def test_30_seeds_invariants(self):
        for seed in range(30):
            plans = self._run(seed)
            sleeps = sorted(
                (plans[n].sleep_start, plans[n].sleep_end) for n in self.NAMES
            )
            # Sleeps never overlap
            for i in range(len(sleeps) - 1):
                assert sleeps[i][1] <= sleeps[i + 1][0], (
                    f"seed={seed}: sleep overlap {sleeps[i]} vs {sleeps[i+1]}"
                )
            for n in self.NAMES:
                p = plans[n]
                dur_h = (p.sleep_end - p.sleep_start) / 3600
                assert 3.5 <= dur_h <= 4.5, f"seed={seed} {n}: sleep dur {dur_h}"
                assert 1 <= len(p.breaks) <= 2, f"seed={seed} {n}: {len(p.breaks)} breaks"
                for brk in p.breaks:
                    dur_m = (brk[1] - brk[0]) / 60
                    assert 20 <= dur_m <= 60, f"seed={seed} {n}: break dur {dur_m}"
                    # Break must not overlap own sleep
                    assert not (brk[0] < p.sleep_end and p.sleep_start < brk[1])
                assert 18 <= p.cap_today <= 28, f"seed={seed} {n}: cap {p.cap_today}"

            # Breaks don't overlap any sleep or each other
            all_sleeps = [(plans[n].sleep_start, plans[n].sleep_end) for n in self.NAMES]
            all_breaks = []
            for n in self.NAMES:
                for brk in plans[n].breaks:
                    # No overlap with any sleep
                    for s_start, s_end in all_sleeps:
                        assert not (brk[0] < s_end and s_start < brk[1]), (
                            f"seed={seed}: break overlaps sleep"
                        )
                    # No overlap with other breaks
                    for ob in all_breaks:
                        assert not (brk[0] < ob[1] and ob[0] < brk[1]), (
                            f"seed={seed}: break overlaps break"
                        )
                    all_breaks.append(brk)


class TestSingleAccount:
    """1 account: sleep window exists; pick_account returns None during sleep."""

    def test_single_account_sleep(self):
        rng = random.Random(42)
        plans = plan_day(["solo"], _DAY, _CFG, rng)
        p = plans["solo"]
        dur_h = (p.sleep_end - p.sleep_start) / 3600
        assert 3.5 <= dur_h <= 4.5

        # During sleep: pick_account returns None
        mid_sleep = (p.sleep_start + p.sleep_end) / 2
        st = {
            "solo": {
                "status": "ok",
                "sleep_start": p.sleep_start,
                "sleep_end": p.sleep_end,
                "breaks": p.breaks,
                "cap_today": p.cap_today,
                "used_today": 0,
            }
        }
        assert pick_account(st, mid_sleep, rng) is None

        # Outside sleep: returns "solo"
        outside = p.sleep_end + 60
        assert pick_account(st, outside, rng) == "solo"


class TestGap:
    def test_gap_after_use(self):
        rng = random.Random(7)
        now = _epoch(_DAY, 12.0)
        st = {
            "status": "ok", "used_today": 0, "cap_today": 28,
            "sleep_start": 0, "sleep_end": 0, "breaks": [],
            "cooldown_level": 0,
        }
        after_use(st, now, _CFG, rng)
        avail, reason = is_available(st, now + 1)
        assert not avail
        assert reason == "gap"
        # After next_ok_at: available
        avail2, _ = is_available(st, st["next_ok_at"] + 1)
        assert avail2


class TestDailyCap:
    def test_cap_reached_resets_next_day(self):
        now = _epoch(_DAY, 14.0)
        st = {
            "status": "ok", "used_today": 20, "cap_today": 20,
            "sleep_start": 0, "sleep_end": 0, "breaks": [],
            "day": _DAY.isoformat(),
        }
        avail, reason = is_available(st, now)
        assert not avail
        assert reason == "daily_cap"

        # next_ready_at should point to next midnight
        nra = next_ready_at({"a": st}, now)
        tz = _tz("Asia/Jakarta")
        next_mid = _local_midnight_epoch(_DAY + timedelta(days=1), tz)
        assert nra == next_mid


class TestApplyError:
    def test_cooldown_ladder(self):
        st = {"cooldown_level": 0, "status": "ok"}
        now = 1000000.0

        apply_error(st, "usage_limit", now)
        assert st["cooldown_level"] == 1
        assert st["cooldown_until"] == now + 30 * 60
        assert st["status"] == "cooldown"

        apply_error(st, "blocked", now + 1)
        assert st["cooldown_level"] == 2
        assert st["cooldown_until"] == now + 1 + 2 * 3600

        apply_error(st, "usage_limit", now + 2)
        assert st["cooldown_level"] == 3
        assert st["cooldown_until"] == now + 2 + 12 * 3600

    def test_auth_needs_login(self):
        st = {"cooldown_level": 0, "status": "ok"}
        apply_error(st, "auth", 1000.0)
        assert st["status"] == "needs_login"

    def test_abuse_24h(self):
        st = {"cooldown_level": 0, "status": "ok"}
        now = 5000.0
        apply_error(st, "abuse", now)
        assert st["status"] == "abuse_flag"
        assert st["cooldown_until"] == now + 24 * 3600

    def test_success_resets_cooldown_level(self):
        rng = random.Random(1)
        st = {"cooldown_level": 2, "status": "cooldown", "used_today": 0, "cap_today": 28}
        after_use(st, 99999.0, _CFG, rng)
        assert st["cooldown_level"] == 0
        assert st["status"] == "ok"

    def test_timeout_no_status_change(self):
        st = {"status": "ok", "cooldown_level": 0}
        now = 1000.0
        apply_error(st, "timeout", now)
        assert st["status"] == "ok"
        assert st["next_ok_at"] == now + 600

    def test_cooldown_expiry_resets_status_to_ok(self):
        st = {"status": "cooldown", "cooldown_until": 1000.0}
        avail, reason = is_available(st, 999.0)
        assert not avail
        assert reason == "cooldown"

        avail2, reason2 = is_available(st, 1000.0)
        assert avail2
        assert reason2 == "ok"
        assert st["status"] == "ok"
        assert st["cooldown_until"] is None


class TestNeedsLoginExcluded:
    def test_needs_login_excluded_from_plan(self):
        rng = random.Random(10)
        names = ["a", "b", "c"]
        plans = plan_day(names, _DAY, _CFG, rng)
        assert "a" in plans and "b" in plans and "c" in plans

        # If "b" is needs_login, re-plan with N-1
        active = ["a", "c"]
        plans2 = plan_day(active, _DAY, _CFG, rng)
        assert "b" not in plans2
        assert len(plans2) == 2
        # Sleeps should not overlap
        sleeps = sorted((plans2[n].sleep_start, plans2[n].sleep_end) for n in active)
        if len(sleeps) > 1:
            assert sleeps[0][1] <= sleeps[1][0]


class TestPickAccountPreference:
    def test_prefers_least_recently_used(self):
        rng = random.Random(0)
        now = _epoch(_DAY, 12.0)
        states = {
            "a": {
                "status": "ok", "last_used_at": now - 100,
                "sleep_start": 0, "sleep_end": 0, "breaks": [],
                "used_today": 1, "cap_today": 28,
            },
            "b": {
                "status": "ok", "last_used_at": now - 500,
                "sleep_start": 0, "sleep_end": 0, "breaks": [],
                "used_today": 1, "cap_today": 28,
            },
            "c": {
                "status": "ok", "last_used_at": now - 50,
                "sleep_start": 0, "sleep_end": 0, "breaks": [],
                "used_today": 1, "cap_today": 28,
            },
        }
        # Over many picks, should only choose from the two LRU (b and a)
        picks = set()
        for s in range(50):
            r = random.Random(s)
            p = pick_account(states, now, r)
            picks.add(p)
        # b (oldest) and a (second oldest) should be the candidates
        assert "b" in picks
        assert "a" in picks
        # c (most recent) should never be picked
        assert "c" not in picks


class TestNeedsTosScheduler:
    def test_apply_error_tos_and_is_available_6h(self):
        state = {"status": "ok"}
        now = 1000.0
        apply_error(state, "tos", now)
        assert state["status"] == "needs_tos"
        assert state["last_error_type"] == "tos"
        assert state["cooldown_until"] == now + 6 * 3600

        # Before 6h
        avail, reason = is_available(state, now + 100)
        assert avail is False
        assert reason == "needs_tos"

        # At/after 6h
        avail, reason = is_available(state, now + 6 * 3600)
        assert avail is True
        assert reason == "ok"
        assert state["status"] == "ok"
        assert state["cooldown_until"] is None

    def test_plan_day_excludes_needs_tos_dict(self):
        states = {
            "a": {"status": "ok"},
            "b": {"status": "needs_tos"},
            "c": {"status": "needs_login"},
        }
        plans = plan_day(states, _DAY, _CFG, random.Random(1))
        assert "a" in plans
        assert "b" not in plans
        assert "c" not in plans

    def test_next_ready_at_includes_needs_tos(self):
        now = 1000.0
        states = {
            "a": {"status": "needs_tos", "cooldown_until": now + 7200},
        }
        ra = next_ready_at(states, now)
        assert ra == now + 7200

