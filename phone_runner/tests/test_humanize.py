import random
from unittest import mock

from phone_runner.humanize import human_pause, human_tap, jitter_point


def test_jitter_point_inside_inner_box_200_seeds():
    bounds = (100, 200, 300, 500)
    w = 300 - 100
    h = 500 - 200
    # Inner 60% box: margins are 20% on each side
    min_x = 100 + 0.2 * w
    max_x = 300 - 0.2 * w
    min_y = 200 + 0.2 * h
    max_y = 500 - 0.2 * h

    for seed in range(200):
        rng = random.Random(seed)
        x, y = jitter_point(bounds, rng=rng)
        assert min_x <= x <= max_x, f"x={x} outside inner box [{min_x}, {max_x}] for seed {seed}"
        assert min_y <= y <= max_y, f"y={y} outside inner box [{min_y}, {max_y}] for seed {seed}"


def test_jitter_point_tiny_bounds_returns_center():
    # Width < 5
    bounds_tiny_w = (10, 20, 14, 40)
    assert jitter_point(bounds_tiny_w) == (12, 30)

    # Height < 5
    bounds_tiny_h = (10, 20, 50, 23)
    assert jitter_point(bounds_tiny_h) == (30, 21)


def test_human_pause_uses_injected_sleep():
    mock_sleep = mock.Mock()
    rng = random.Random(42)
    dur = human_pause(lo=1.5, hi=2.5, rng=rng, sleep=mock_sleep)
    assert 1.5 <= dur <= 2.5
    mock_sleep.assert_called_once_with(dur)


def test_human_tap_calls_run_adb():
    bounds = (100, 100, 200, 200)
    with mock.patch("phone_runner.humanize.run_adb") as mock_adb:
        human_tap("test_serial", bounds)
        mock_adb.assert_called_once()
        args, kwargs = mock_adb.call_args
        cmd = args[0]
        assert cmd[:3] == ["shell", "input", "tap"]
        assert kwargs.get("serial") == "test_serial"
        tx, ty = int(cmd[3]), int(cmd[4])
        assert 120 <= tx <= 180
        assert 120 <= ty <= 180
