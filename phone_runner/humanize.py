from __future__ import annotations

import math
import random
import time
from typing import Any, Sequence

from phone_runner.adb import run_adb


def jitter_point(
    bounds: Sequence[int],
    rng: Any = random,
) -> tuple[int, int]:
    x1, y1, x2, y2 = bounds[0], bounds[1], bounds[2], bounds[3]
    w = x2 - x1
    h = y2 - y1
    if w < 5 or h < 5:
        return (x1 + w // 2, y1 + h // 2)

    min_x = int(math.ceil(x1 + 0.2 * w))
    max_x = int(math.floor(x2 - 0.2 * w))
    min_y = int(math.ceil(y1 + 0.2 * h))
    max_y = int(math.floor(y2 - 0.2 * h))

    if min_x > max_x:
        min_x = max_x = x1 + w // 2
    if min_y > max_y:
        min_y = max_y = y1 + h // 2

    x = rng.randint(min_x, max_x)
    y = rng.randint(min_y, max_y)
    return (int(x), int(y))


def human_pause(
    lo: float = 1.0,
    hi: float = 3.0,
    rng: Any = random,
    sleep: Any = time.sleep,
) -> float:
    duration = rng.uniform(lo, hi)
    sleep(duration)
    return duration


def human_tap(
    serial: str,
    bounds: Sequence[int],
    rng: Any = random,
) -> None:
    x, y = jitter_point(bounds, rng=rng)
    run_adb(["shell", "input", "tap", str(x), str(y)], serial=serial)
