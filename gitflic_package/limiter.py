"""Request pacing and retry delay helpers."""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable


class RateLimiter:
    def __init__(
        self,
        min_interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        max_retries: int = 5,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be non-negative")
        self.min_interval_seconds = float(min_interval_seconds)
        self.clock = clock
        self.sleeper = sleeper
        self.max_retries = max_retries
        self._last_request_at: float | None = None

    def wait(self) -> None:
        now = self.clock()
        if self._last_request_at is not None:
            remaining = self.min_interval_seconds - (now - self._last_request_at)
            if remaining > 0:
                self.sleeper(remaining)
        self._last_request_at = self.clock()


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value.strip())
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    except (AttributeError, ValueError):
        pass

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at is None:
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    delay = (retry_at - current).total_seconds()
    return max(0.0, delay) if math.isfinite(delay) else None


def backoff_seconds(
    attempt: int, retry_after: str | None, now: datetime | None = None
) -> float:
    parsed = parse_retry_after(retry_after, now)
    if parsed is not None:
        return parsed
    exponent = min(6, max(0, attempt - 1))
    return min(60.0, 2.0**exponent)
