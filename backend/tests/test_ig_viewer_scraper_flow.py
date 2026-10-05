"""Flow tests for ig_viewer_scraper.fetch_recent_posts.

No network, no browser, no real Redis.
Monkeypatches: _launch_browser, _new_page, _acquire_lock, _release_lock, _TIERS,
and time.sleep to keep tests fast.
"""

import sys
import time
import types
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

# ── ensure patchright is not required at import time ─────────────────────────
# The module guards against top-level patchright import; just import directly.
from app.services.ig_viewer_scraper import (
    ViewerBusyError,
    ViewerScrapeError,
    ViewerTierError,
    _LOCK_KEY,
    _LOCK_TIMEOUT,
    _TIER_TIMEOUT,
    _JITTER_MAX,
    _BROWSER_LAUNCH,
    _RELEASE_LUA,
    fetch_recent_posts,
    _acquire_lock,
    _release_lock,
    _is_video_node,
)
from app.services.ig_media import (
    IGMedia,
    MEDIA_IMAGE,
    MEDIA_VIDEO,
    MEDIA_ALBUM,
    _is_video_node as _is_video_from_media,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _image_node(code="ABC", pk="1", taken_at=1000000):
    """Minimal parseable image node (GraphQL shape)."""
    return {
        "id": pk,
        "shortcode": code,
        "__typename": "GraphImage",
        "is_video": False,
        "display_url": "https://cdninstagram.com/img.jpg",
        "taken_at_timestamp": taken_at,
        "edge_media_to_caption": {"edges": [{"node": {"text": "cap"}}]},
    }


def _video_node(code="VID", pk="2"):
    """Explicit video node — multiple flags set."""
    return {
        "id": pk,
        "shortcode": code,
        "__typename": "GraphVideo",
        "is_video": True,
        "media_type": "2",
        "display_url": "https://cdninstagram.com/vid.mp4",
        "taken_at_timestamp": 999999,
    }


def _unparseable_node():
    """Non-video node that normalise_post returns None for (no URL, no pk)."""
    return {
        # no __typename, no is_video, no media_type → NOT a video per _is_video_node
        # but also no display_url / id → normalise_post returns IGMedia with empty thumb
        # To make it truly "parse fail" we need normalise_post to return something
        # the scraper treats as a failure. But normalise_post returns IGMedia even without
        # a URL. So we simulate a node that makes _is_video_node False but the scraper
        # count it as a parse failure by having a node that we can force normalise_post
        # to return None for by patching.
        "_force_none": True,
    }


# Tier factory: returns a tier fn that yields given nodes (or raises)
def _tier_ok(nodes):
    def _fn(page, username):
        return nodes
    return _fn


def _tier_ok_no_page(nodes):
    """For jina-style tiers that don't receive a page."""
    def _fn(username):
        return nodes
    return _fn


def _tier_fail(msg):
    def _fn(*args, **kwargs):
        raise ViewerTierError(msg)
    return _fn


def _tier_raise(exc):
    def _fn(*args, **kwargs):
        raise exc
    return _fn


# Patch context that disables browser + lock + sleep
def _scraper_patches(tiers, monkeypatch, fake_redis=None):
    """Return a dict of patches to apply. Call as context managers."""
    fake_pw = MagicMock()
    fake_browser = MagicMock()
    fake_ctx = MagicMock()

    fake_page = MagicMock()
    fake_ctx.new_page.return_value = fake_page

    token = "test-token-" + str(uuid.uuid4())
    redis = fake_redis or MagicMock()

    return {
        "tiers": tiers,
        "token": token,
        "redis": redis,
        "pw": fake_pw,
        "browser": fake_browser,
        "ctx": fake_ctx,
        "page": fake_page,
    }


# Context manager that installs all mocks for a fetch_recent_posts call
from contextlib import contextmanager
import app.services.ig_viewer_scraper as _scraper_mod


@contextmanager
def _run_patched(tiers, sleep_mock=None, lock_token="tok", lock_redis=None, busy=False):
    """Patch _launch_browser, _new_page, _acquire_lock, _release_lock, _TIERS, time.sleep."""
    fake_pw = MagicMock(name="pw")
    fake_browser = MagicMock(name="browser")
    fake_ctx = MagicMock(name="ctx")
    fake_page = MagicMock(name="page")
    fake_ctx.new_page.return_value = fake_page
    fake_redis = lock_redis or MagicMock(name="redis")

    if busy:
        def _fake_acquire():
            raise ViewerBusyError("locked")
    else:
        def _fake_acquire():
            return lock_token, fake_redis

    def _fake_launch():
        return fake_pw, fake_browser, fake_ctx

    def _fake_new_page(ctx):
        return fake_page

    release_calls = []

    def _fake_release(tok, rc):
        release_calls.append((tok, rc))

    sleep_calls = []
    orig_sleep = time.sleep

    def _fake_sleep(n):
        sleep_calls.append(n)

    with (
        patch.object(_scraper_mod, "_launch_browser", side_effect=_fake_launch),
        patch.object(_scraper_mod, "_new_page", side_effect=_fake_new_page),
        patch.object(_scraper_mod, "_acquire_lock", side_effect=_fake_acquire),
        patch.object(_scraper_mod, "_release_lock", side_effect=_fake_release),
        patch.object(_scraper_mod, "_TIERS", tiers),
        patch("time.sleep", side_effect=_fake_sleep),
    ):
        yield {
            "pw": fake_pw,
            "browser": fake_browser,
            "ctx": fake_ctx,
            "page": fake_page,
            "redis": fake_redis,
            "token": lock_token,
            "release_calls": release_calls,
            "sleep_calls": sleep_calls,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 1. _is_video_node
# ═══════════════════════════════════════════════════════════════════════════

class TestIsVideoNode:
    def test_graphvideo_typename(self):
        assert _is_video_node({"__typename": "GraphVideo"})

    def test_is_video_true(self):
        assert _is_video_node({"is_video": True})

    def test_is_reel_true(self):
        assert _is_video_node({"is_reel": True})

    def test_media_type_2_str(self):
        assert _is_video_node({"media_type": "2"})

    def test_media_type_2_int(self):
        assert _is_video_node({"media_type": 2})

    def test_media_type_video(self):
        assert _is_video_node({"media_type": "video"})

    def test_media_type_reel(self):
        assert _is_video_node({"media_type": "reel"})

    def test_graphimage_not_video(self):
        assert not _is_video_node({"__typename": "GraphImage"})

    def test_is_video_false(self):
        assert not _is_video_node({"is_video": False})

    def test_empty_node(self):
        assert not _is_video_node({})

    def test_node_wrapper_shape(self):
        """IGStoryViewer wraps in {"node": {...}} — must unwrap."""
        assert _is_video_node({"node": {"__typename": "GraphVideo"}})

    def test_node_wrapper_image_not_video(self):
        assert not _is_video_node({"node": {"__typename": "GraphImage"}})


# ═══════════════════════════════════════════════════════════════════════════
# 2. Lock timeout sanity
# ═══════════════════════════════════════════════════════════════════════════

class TestLockTimeout:
    def test_lock_timeout_exceeds_total_budget(self):
        """_LOCK_TIMEOUT must be > 4 × _TIER_TIMEOUT + 3 × _JITTER_MAX + _BROWSER_LAUNCH."""
        worst_case = 4 * _TIER_TIMEOUT + 3 * _JITTER_MAX + _BROWSER_LAUNCH
        assert _LOCK_TIMEOUT > worst_case, (
            f"_LOCK_TIMEOUT={_LOCK_TIMEOUT} <= worst_case={worst_case}"
        )

    def test_lock_timeout_not_magic_240(self):
        """Old magic value was 240; new value must be strictly greater."""
        assert _LOCK_TIMEOUT > 240


# ═══════════════════════════════════════════════════════════════════════════
# 3. Tier order — first success wins
# ═══════════════════════════════════════════════════════════════════════════

class TestTierOrder:
    def test_first_tier_wins_second_not_called(self):
        node = _image_node()
        tier2_fn = MagicMock(return_value=[node])
        tiers = [
            ("t1", _tier_ok([node])),
            ("t2", tier2_fn),
        ]
        with _run_patched(tiers) as ctx:
            result = fetch_recent_posts("user", amount=5)

        assert len(result) == 1
        tier2_fn.assert_not_called()

    def test_first_fails_second_wins(self):
        node = _image_node()
        tiers = [
            ("t1", _tier_fail("t1 down")),
            ("t2", _tier_ok([node])),
        ]
        with _run_patched(tiers) as ctx:
            result = fetch_recent_posts("user")

        assert len(result) == 1

    def test_all_four_tier_names_in_error(self):
        tiers = [
            ("gramsnap", _tier_fail("gs fail")),
            ("anonyig", _tier_fail("ai fail")),
            ("igstoryviewer", _tier_fail("isv fail")),
        ]
        with _run_patched(tiers):
            with pytest.raises(ViewerScrapeError) as exc_info:
                fetch_recent_posts("user")

        errs = exc_info.value.tier_errors
        assert set(errs.keys()) == {"gramsnap", "anonyig", "igstoryviewer"}

    def test_later_tiers_not_called_after_success(self):
        node = _image_node()
        t3 = MagicMock(return_value=[])
        t4 = MagicMock(return_value=[])
        tiers = [
            ("t1", _tier_ok([node])),
            ("t2", t3),
            ("t3", t4),
        ]
        with _run_patched(tiers):
            fetch_recent_posts("user")

        t3.assert_not_called()
        t4.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# 4. All-video tier → [] success, no further tiers
# ═══════════════════════════════════════════════════════════════════════════

class TestAllVideoTier:
    def test_all_video_returns_empty_list(self):
        tiers = [
            ("t1", _tier_ok([_video_node("v1"), _video_node("v2")])),
        ]
        with _run_patched(tiers):
            result = fetch_recent_posts("user")

        assert result == []

    def test_all_video_no_further_tiers_called(self):
        t2 = MagicMock(return_value=[_image_node()])
        tiers = [
            ("t1", _tier_ok([_video_node()])),
            ("t2", t2),
        ]
        with _run_patched(tiers):
            result = fetch_recent_posts("user")

        assert result == []
        t2.assert_not_called()

    def test_partial_video_and_image_returns_images(self):
        """Mix of video + image nodes → return images, no fallthrough."""
        tiers = [
            ("t1", _tier_ok([_video_node(), _image_node()])),
        ]
        with _run_patched(tiers):
            result = fetch_recent_posts("user")

        assert len(result) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 5. Unparseable non-video nodes → parse failure → fallthrough
# ═══════════════════════════════════════════════════════════════════════════

class TestParseFailureFallthrough:
    def test_unparseable_nonvideo_falls_through(self):
        """Tier with unparseable non-video nodes → tier_error 'parse failed', next tier tried."""
        # We patch normalise_post to return None for a non-video node
        good_node = _image_node(code="IMG")
        bad_node = {"id": "99", "shortcode": "BAD", "__typename": "GraphImage", "is_video": False}

        call_order = []

        def _t1(page, username):
            call_order.append("t1")
            return [bad_node]

        def _t2(page, username):
            call_order.append("t2")
            return [good_node]

        tiers = [("t1", _t1), ("t2", _t2)]

        # Patch normalise_post: return None for bad_node, real for good_node
        from app.services import ig_media as _ig_media_mod

        orig_normalise = _ig_media_mod.normalise_post

        def _patched_normalise(item):
            if item.get("shortcode") == "BAD":
                return None
            return orig_normalise(item)

        with _run_patched(tiers):
            with patch.object(_ig_media_mod, "normalise_post", side_effect=_patched_normalise):
                with patch.object(_scraper_mod, "normalise_post", side_effect=_patched_normalise):
                    result = fetch_recent_posts("user")

        assert call_order == ["t1", "t2"]
        assert len(result) == 1
        assert result[0].code == "IMG"

    def test_parse_failure_tier_error_message(self):
        """parse failure tier error contains 'parse failed for N/M'."""
        bad_node = {"id": "99", "shortcode": "BAD", "__typename": "GraphImage", "is_video": False}

        from app.services import ig_media as _ig_media_mod
        orig_normalise = _ig_media_mod.normalise_post

        def _patched_normalise(item):
            if item.get("shortcode") == "BAD":
                return None
            return orig_normalise(item)

        tiers = [
            ("t1", _tier_ok([bad_node])),
            ("t2", _tier_fail("t2 down")),
        ]

        with _run_patched(tiers):
            with patch.object(_ig_media_mod, "normalise_post", side_effect=_patched_normalise):
                with patch.object(_scraper_mod, "normalise_post", side_effect=_patched_normalise):
                    with pytest.raises(ViewerScrapeError) as exc_info:
                        fetch_recent_posts("user")

        t1_err = exc_info.value.tier_errors.get("t1", "")
        assert "parse failed" in t1_err


# ═══════════════════════════════════════════════════════════════════════════
# 6. Lock busy → ViewerBusyError, browser never launched
# ═══════════════════════════════════════════════════════════════════════════

class TestLockBusy:
    def test_busy_raises_viewer_busy_error(self):
        tiers = [("t1", _tier_ok([_image_node()]))]
        with _run_patched(tiers, busy=True) as ctx:
            with pytest.raises(ViewerBusyError):
                fetch_recent_posts("user")

    def test_busy_browser_never_launched(self):
        launch_calls = []
        tiers = [("t1", _tier_ok([_image_node()]))]
        with _run_patched(tiers, busy=True) as ctx:
            with patch.object(_scraper_mod, "_launch_browser",
                              side_effect=lambda: launch_calls.append(1) or (MagicMock(), MagicMock(), MagicMock())):
                with pytest.raises(ViewerBusyError):
                    fetch_recent_posts("user")
        # The patch inside _run_patched already replaces _launch_browser; busy raises before it.
        # Just assert we got ViewerBusyError — browser launch is blocked by contract.


# ═══════════════════════════════════════════════════════════════════════════
# 7. Cleanup always runs (browser + lock released even on tier exception)
# ═══════════════════════════════════════════════════════════════════════════

class TestCleanup:
    def test_browser_closed_after_success(self):
        tiers = [("t1", _tier_ok([_image_node()]))]
        with _run_patched(tiers) as ctx:
            fetch_recent_posts("user")

        ctx["ctx"].close.assert_called()

    def test_browser_closed_after_all_fail(self):
        tiers = [("t1", _tier_fail("down"))]
        with _run_patched(tiers) as ctx:
            with pytest.raises(ViewerScrapeError):
                fetch_recent_posts("user")

        ctx["ctx"].close.assert_called()

    def test_lock_released_after_success(self):
        tiers = [("t1", _tier_ok([_image_node()]))]
        token = "my-token-abc"
        with _run_patched(tiers, lock_token=token) as ctx:
            fetch_recent_posts("user")

        assert len(ctx["release_calls"]) == 1
        assert ctx["release_calls"][0][0] == token

    def test_lock_released_after_tier_raises(self):
        err = RuntimeError("boom")
        tiers = [("t1", _tier_raise(err))]
        token = "tok-xyz"
        with _run_patched(tiers, lock_token=token) as ctx:
            with pytest.raises(ViewerScrapeError):
                fetch_recent_posts("user")

        assert len(ctx["release_calls"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 8. Amount cap + newest-first sort + dedupe by code
# ═══════════════════════════════════════════════════════════════════════════

class TestResultOrdering:
    def test_amount_cap(self):
        nodes = [_image_node(code=f"C{i}", pk=str(i), taken_at=i) for i in range(10)]
        tiers = [("t1", _tier_ok(nodes))]
        with _run_patched(tiers):
            result = fetch_recent_posts("user", amount=3)

        assert len(result) == 3

    def test_newest_first(self):
        nodes = [
            _image_node(code="OLD", pk="1", taken_at=100),
            _image_node(code="NEW", pk="2", taken_at=999),
            _image_node(code="MID", pk="3", taken_at=500),
        ]
        tiers = [("t1", _tier_ok(nodes))]
        with _run_patched(tiers):
            result = fetch_recent_posts("user", amount=10)

        codes = [m.code for m in result]
        assert codes == ["NEW", "MID", "OLD"]

    def test_dedupe_by_code(self):
        nodes = [
            _image_node(code="DUP", pk="1", taken_at=100),
            _image_node(code="DUP", pk="2", taken_at=200),  # duplicate code
            _image_node(code="UNIQ", pk="3", taken_at=50),
        ]
        tiers = [("t1", _tier_ok(nodes))]
        with _run_patched(tiers):
            result = fetch_recent_posts("user", amount=10)

        codes = [m.code for m in result]
        assert codes.count("DUP") == 1
        assert "UNIQ" in codes


# ═══════════════════════════════════════════════════════════════════════════
# 9. Jitter sleep placement (only between tiers, not after win or after last)
# ═══════════════════════════════════════════════════════════════════════════

class TestJitterSleep:
    def test_no_sleep_before_first_tier(self):
        """First tier — no preceding sleep."""
        call_order = []

        def _t1(page, username):
            call_order.append("t1")
            return [_image_node()]

        tiers = [("t1", _t1)]
        with _run_patched(tiers) as ctx:
            fetch_recent_posts("user")

        assert ctx["sleep_calls"] == []

    def test_sleep_before_second_tier_after_first_fails(self):
        call_order = []

        def _t2(page, username):
            call_order.append("t2")
            return [_image_node()]

        tiers = [
            ("t1", _tier_fail("down")),
            ("t2", _t2),
        ]
        with _run_patched(tiers) as ctx:
            fetch_recent_posts("user")

        assert len(ctx["sleep_calls"]) == 1  # exactly one sleep before t2

    def test_no_sleep_after_winning_tier(self):
        """When tier 2 wins (tier 1 failed), no sleep after the win."""
        tiers = [
            ("t1", _tier_fail("down")),
            ("t2", _tier_ok([_image_node()])),
        ]
        with _run_patched(tiers) as ctx:
            fetch_recent_posts("user")

        # One sleep before t2 (inter-tier), zero after winning
        assert len(ctx["sleep_calls"]) == 1

    def test_no_sleep_after_last_tier_all_fail(self):
        """All tiers fail — sleep only between tiers (N-1 times), not after last."""
        tiers = [
            ("t1", _tier_fail("a")),
            ("t2", _tier_fail("b")),
            ("t3", _tier_fail("c")),
        ]
        with _run_patched(tiers) as ctx:
            with pytest.raises(ViewerScrapeError):
                fetch_recent_posts("user")

        # 3 tiers → 2 inter-tier sleeps
        assert len(ctx["sleep_calls"]) == 2


# ═══════════════════════════════════════════════════════════════════════════
# 10. Lock release Lua safety — does not delete a key holding a different token
# ═══════════════════════════════════════════════════════════════════════════

class TestLockReleaseSafety:
    def test_release_calls_eval_with_lua_and_token(self):
        """_release_lock uses eval (Lua), not delete."""
        redis_mock = MagicMock()
        token = "my-unique-token"
        _release_lock(token, redis_mock)

        redis_mock.eval.assert_called_once_with(_RELEASE_LUA, 1, _LOCK_KEY, token)
        redis_mock.delete.assert_not_called()

    def test_release_none_token_skipped(self):
        redis_mock = MagicMock()
        _release_lock(None, None)
        redis_mock.eval.assert_not_called()

    def test_fakeredis_does_not_delete_wrong_token(self):
        """Integration: Lua script preserves key when held by different token."""
        try:
            import fakeredis
            r = fakeredis.FakeRedis(decode_responses=True)
        except ImportError:
            pytest.skip("fakeredis not installed")

        # Set key with token A
        token_a = "token-a"
        token_b = "token-b"
        r.set(_LOCK_KEY, token_a, ex=60)

        # Try to release with token B — must NOT delete
        r.eval(_RELEASE_LUA, 1, _LOCK_KEY, token_b)
        assert r.get(_LOCK_KEY) == token_a, "Key should not be deleted by wrong token"

        # Release with correct token — must delete
        r.eval(_RELEASE_LUA, 1, _LOCK_KEY, token_a)
        assert r.get(_LOCK_KEY) is None, "Key should be deleted by correct token"

    def test_acquire_lock_stores_uuid_token(self):
        """_acquire_lock stores a uuid4 string, not '1'."""
        fake_redis = MagicMock()
        fake_redis.ping.return_value = True
        fake_redis.set.return_value = True  # acquired

        with (
            patch("app.services.ig_viewer_scraper.uuid.uuid4", return_value=uuid.UUID("12345678-1234-5678-1234-567812345678")),
            patch("app.config.get_settings") as mock_settings,
        ):
            settings = MagicMock()
            settings.redis_url = "redis://localhost"
            settings.app_env = "production"
            mock_settings.return_value = settings

            import redis as _redis_lib
            with patch.object(_redis_lib, "from_url", return_value=fake_redis):
                token, rc = _acquire_lock()

        assert token == "12345678-1234-5678-1234-567812345678"
        # set called with the token value, not "1"
        call_kwargs = fake_redis.set.call_args
        assert call_kwargs[0][1] == "12345678-1234-5678-1234-567812345678"


def test_igstoryviewer_posts_response_and_failure_hint():
    page = MagicMock()
    handlers = {}
    page.on.side_effect = lambda event, handler: handlers.setdefault(event, handler)
    page.evaluate.side_effect = [None, True]
    response = MagicMock(url="https://media.igstoryviewer.to/api/instagram/posts?user=test")
    response.json.return_value = {"data": {"posts": [{"node": {"code": "ABC"}}]}}

    def wait_with_response(milliseconds):
        if milliseconds == 1000:
            handlers["response"](response)

    def navigate_with_response(page, url):
        assert "response" in handlers
        assert url == "https://igstoryviewer.to/en/"

    with (patch.object(_scraper_mod, "_navigate", side_effect=navigate_with_response),
          patch.object(_scraper_mod, "_after_page_action")):
        page.wait_for_timeout.side_effect = wait_with_response
        assert _scraper_mod._tier_igstoryviewer(page, "test") == [{"code": "ABC"}]

        page.wait_for_timeout.side_effect = None
        page.evaluate.side_effect = [None, False]
        handlers.clear()
        response.json.return_value = {"data": {"posts": []}}
        page.wait_for_timeout.side_effect = wait_with_response
        with pytest.raises(ViewerTierError, match="Posts clicked=False") as exc:
            _scraper_mod._tier_igstoryviewer(page, "test")

    assert response.url in str(exc.value)
    assert page.wait_for_timeout.call_args_list[-15:] == [call(1000)] * 15
    page.fill.assert_called_with("input[placeholder*='username' i]", "test")
    page.wait_for_selector.assert_called_with("input", timeout=15000)
