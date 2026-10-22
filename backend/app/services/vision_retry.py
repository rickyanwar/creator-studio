from __future__ import annotations

import logging
import time
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

VISION_CHECK_ATTEMPTS = 3          # first try + 2 retries
VISION_RETRY_WAIT_SECONDS = 20

T = TypeVar("T")


def _default_sleep(s: float) -> None:
    time.sleep(s)


def run_vision_check(
    fn: Callable[[], T],
    *,
    attempts: int = VISION_CHECK_ATTEMPTS,
    wait_s: float = VISION_RETRY_WAIT_SECONDS,
    sleep: Callable[[float], Any] = _default_sleep,
    label: str = "",
) -> T:
    """Call fn() (no args). If it raises, log a warning and call it again after wait_s, up to `attempts` calls in
    total; re-raise the last exception when every call failed. A value returned by fn is returned at once."""
    tag = f" [{label}]" if label else ""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt < attempts:
                logger.warning(
                    "Vision check%s attempt %d/%d failed: %s; retrying in %ss",
                    tag,
                    attempt,
                    attempts,
                    exc,
                    wait_s,
                )
                sleep(wait_s)
            else:
                logger.warning(
                    "Vision check%s attempt %d/%d failed: %s; giving up",
                    tag,
                    attempt,
                    attempts,
                    exc,
                )
                raise
