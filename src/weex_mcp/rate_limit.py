from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Bucket:
    count: int
    reset_at: float


class FixedWindowRateLimiter:
    def __init__(self, *, limit: int, window_seconds: int, time_func: Callable[[], float] | None = None):
        if limit <= 0:
            raise ValueError("limit must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._limit = limit
        self._window_seconds = window_seconds
        self._time = time_func or time.time
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str) -> bool:
        now = self._time()
        bucket = self._buckets.get(key)
        if bucket is None or now >= bucket.reset_at:
            self._buckets[key] = _Bucket(count=1, reset_at=now + self._window_seconds)
            return True
        if bucket.count >= self._limit:
            return False
        bucket.count += 1
        return True
